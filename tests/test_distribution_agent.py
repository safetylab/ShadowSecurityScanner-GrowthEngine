"""Distribution agent: owned channels only, gate re-checked at publish."""

import importlib
import json

import pytest

from sge import db, paths

dist = importlib.import_module("agents.distribution.main")

SOURCE = "https://example.test/advisory"
PROJECT = "https://github.com/safetylab/ShadowSecurityScanner"


def run(argv):
    return dist.main(argv)


@pytest.fixture
def draft(temp_db, monkeypatch, tmp_path):
    """One approved-ready digest draft on disk and in the database."""
    monkeypatch.setattr(dist.paths, "ROOT", tmp_path)
    monkeypatch.setattr(dist.paths, "PUBLISHED_DIR", tmp_path / "published")
    drafts = tmp_path / "drafts"
    drafts.mkdir()

    body = (
        "---\ntitle: \"Digest\"\n---\n\n# Digest\n\n"
        + "Operators verify advisories against their own hosts. " * 25
        + f"\n\n- [Advisory]({SOURCE})\n\nRun it only where authorised: "
        + f"{PROJECT}\n"
    )
    path = drafts / "digest.md"
    path.write_text(body, encoding="utf-8")

    def register(channel="website", status="draft", gate=1):
        with db.session() as connection:
            connection.execute(
                """
                INSERT INTO content_items
                    (slug, kind, channel, title, path, status,
                     quality_passed, sources)
                VALUES ('digest', 'digest', ?, 'Digest', 'drafts/digest.md',
                        ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    channel = excluded.channel, status = excluded.status,
                    quality_passed = excluded.quality_passed
                """,
                (channel, status, gate, json.dumps([SOURCE])),
            )
            connection.commit()

    register.path = path
    register.root = tmp_path
    return register


def published_rows():
    with db.session() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT slug, channel, published_at FROM publications"
        )]


def test_publishing_requires_approval(draft, capsys):
    draft(status="draft")

    assert run(["publish", "--slug", "digest"]) == 1
    assert published_rows() == []


def test_external_channels_are_refused(draft, capsys):
    draft(channel="reddit", status="approved")

    assert run(["publish", "--slug", "digest"]) == 1
    assert published_rows() == []


def test_a_failed_gate_is_refused_even_when_approved(draft):
    draft(status="approved", gate=0)

    assert run(["publish", "--slug", "digest"]) == 1


def test_approve_refuses_a_draft_that_failed_the_gate(draft):
    draft(gate=0)

    assert run(["approve", "--slug", "digest"]) == 1


def test_the_gate_runs_again_at_publish_time(draft):
    """The file may have been edited by hand after it was generated."""
    draft(status="approved")
    draft.path.write_text(
        "---\ntitle: x\n---\n\nThis tool offers 100% detection. "
        + f"[Advisory]({SOURCE}) {PROJECT}\n" + "word " * 200,
        encoding="utf-8",
    )

    assert run(["publish", "--slug", "digest"]) == 1
    assert published_rows() == []


def test_a_good_draft_publishes_to_an_owned_channel(draft, capsys):
    draft(status="approved")

    assert run(["publish", "--slug", "digest"]) == 0

    rows = published_rows()
    assert rows[0]["channel"] == "website"
    assert rows[0]["published_at"]
    assert (draft.root / "published" / "digest.md").is_file()

    with db.session() as connection:
        status = connection.execute(
            "SELECT status FROM content_items WHERE slug = 'digest'"
        ).fetchone()["status"]

    assert status == "published"


def test_dry_run_publishes_nothing(draft):
    draft(status="approved")

    assert run(["publish", "--slug", "digest", "--dry-run"]) == 0
    assert published_rows() == []


def test_the_daily_limit_is_enforced(draft, monkeypatch):
    from sge import config

    policies = config.load_policies()
    policies["automation"] = {**policies["automation"],
                              "max_posts_per_day": 1}
    monkeypatch.setattr(config, "load_policies", lambda: policies)

    draft(status="approved")
    assert run(["publish", "--slug", "digest"]) == 0

    # A second item on the same day must not go out.
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO content_items
                (slug, kind, channel, title, path, status, quality_passed,
                 sources)
            VALUES ('second', 'digest', 'website', 'Second',
                    'drafts/digest.md', 'approved', 1, ?)
            """,
            (json.dumps([SOURCE]),),
        )
        connection.commit()

    assert run(["publish", "--slug", "second"]) == 1


def test_a_missing_file_is_reported_not_published(draft):
    draft(status="approved")
    draft.path.unlink()

    assert run(["publish", "--slug", "digest"]) == 1


def test_unknown_slug_is_an_error(temp_db):
    assert run(["publish", "--slug", "nope"]) == 1
    assert run(["approve", "--slug", "nope"]) == 1


def test_rss_contains_published_items_only(draft, tmp_path, capsys):
    draft(status="approved")
    run(["publish", "--slug", "digest"])
    capsys.readouterr()

    target = tmp_path / "feed.xml"
    assert run(["rss", "--output", str(target)]) == 0

    from sge import feeds

    entries = feeds.parse_bytes(target.read_bytes())
    assert len(entries) == 1
    assert entries[0]["title"] == "Digest"


def test_rss_of_an_empty_site_is_still_valid(temp_db, tmp_path, capsys):
    target = tmp_path / "feed.xml"

    assert run(["rss", "--output", str(target)]) == 0

    from sge import feeds

    assert feeds.parse_bytes(target.read_bytes()) == []


def test_excerpts_keep_hyphenated_dates(draft):
    """A blunt Markdown strip turned 2026-11-04 into "2026 11 04"."""
    text = "---\nt: x\n---\n\n# H\n\n- A review dated 2026-11-04 here.\n"

    assert "2026-11-04" in dist._excerpt(text)
    assert "#" not in dist._excerpt(text)


def test_queue_reports_the_daily_allowance(draft, capsys):
    draft()

    assert run(["queue"]) == 0
    output = capsys.readouterr().out

    assert "Published today: 0 of" in output
    assert "owned" in output


def test_queue_marks_external_channels_as_human_posted(draft, capsys):
    draft(channel="reddit", status="review")
    run(["queue"])

    assert "human posts" in capsys.readouterr().out
