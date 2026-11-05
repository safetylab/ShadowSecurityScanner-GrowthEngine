"""Content agent: attribution is mandatory, publishing stays manual."""

import importlib
import json

import pytest

from sge import config, db, paths

content = importlib.import_module("agents.content.main")


def run(argv):
    return content.main(argv)


@pytest.fixture
def drafts_dir(monkeypatch, tmp_path):
    """Keep generated files out of the repository."""
    target = tmp_path / "drafts"
    social = tmp_path / "content"
    monkeypatch.setattr(paths, "DRAFTS_DIR", target)
    monkeypatch.setattr(content.paths, "DRAFTS_DIR", target)
    monkeypatch.setattr(content.paths, "CONTENT_DIR", social)
    monkeypatch.setattr(content.paths, "ROOT", tmp_path)
    return target


@pytest.fixture
def research_rows(temp_db):
    """Three relevant research items to build a digest from."""
    with db.session() as connection:
        for index in range(3):
            connection.execute(
                """
                INSERT INTO research_items
                    (source, title, link, canonical_link, published,
                     summary, topic, relevance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"Source {index}",
                 f"Advisory number {index} about a vulnerability scanner",
                 f"https://example.test/item/{index}",
                 f"https://example.test/item/{index}",
                 "2026-11-01",
                 "A CVE in a web application requires patching.",
                 "advisories", 70),
            )
        connection.commit()

    return temp_db


def test_draft_is_written_and_registered(research_rows, drafts_dir):
    assert run(["draft", "--topic", "advisories", "--limit", "3"]) == 0

    files = list(drafts_dir.glob("*.md"))
    assert len(files) == 1

    with db.session() as connection:
        row = connection.execute(
            "SELECT kind, status, quality_passed, sources FROM content_items"
        ).fetchone()

    assert row["kind"] == "digest"
    assert row["status"] == "draft"
    assert row["quality_passed"] == 1
    assert len(json.loads(row["sources"])) == 3


def test_every_source_is_linked_in_the_draft(research_rows, drafts_dir):
    run(["draft", "--topic", "advisories", "--limit", "3"])

    text = next(drafts_dir.glob("*.md")).read_text(encoding="utf-8")

    for index in range(3):
        assert f"https://example.test/item/{index}" in text


def test_draft_links_the_project_and_warns_about_authorisation(
    research_rows, drafts_dir
):
    run(["draft", "--topic", "advisories"])

    text = next(drafts_dir.glob("*.md")).read_text(encoding="utf-8")
    project = config.load_brand()["project"]

    assert project["github"] in text
    assert "without written permission" in text
    assert "requires_human_review: true" in text


def test_draft_without_research_is_refused(temp_db, drafts_dir):
    assert run(["draft", "--topic", "advisories"]) == 1
    assert list(drafts_dir.glob("*.md")) == []


def test_low_relevance_items_are_not_used(temp_db, drafts_dir):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO research_items
                (source, title, link, canonical_link, topic, relevance)
            VALUES ('s', 'Webinar announcement', 'https://example.test/w',
                    'https://example.test/w', 'general', 0)
            """
        )
        connection.commit()

    assert run(["draft", "--min-relevance", "30"]) == 1
    assert list(drafts_dir.glob("*.md")) == []


def test_a_failing_quality_gate_blocks_the_file_and_is_audited(
    research_rows, drafts_dir, monkeypatch
):
    """The gate is the last line: no file, no content row, one audit event."""
    rules = config.load_publishing_rules()
    rules["quality_gate"] = {**rules["quality_gate"], "min_words": 100_000}
    monkeypatch.setattr(config, "load_publishing_rules", lambda: rules)

    assert run(["draft", "--topic", "advisories"]) == 1
    assert list(drafts_dir.glob("*.md")) == []

    with db.session() as connection:
        items = connection.execute(
            "SELECT COUNT(*) AS n FROM content_items"
        ).fetchone()["n"]
        rejected = connection.execute(
            "SELECT COUNT(*) AS n FROM audit_log "
            "WHERE event = 'draft_rejected'"
        ).fetchone()["n"]

    assert items == 0
    assert rejected == 1


def test_social_draft_always_waits_for_a_review(
    research_rows, drafts_dir
):
    """Reddit is 'automatic', and the draft still lands in review."""
    run(["draft", "--topic", "advisories"])

    with db.session() as connection:
        slug = connection.execute(
            "SELECT slug FROM content_items"
        ).fetchone()["slug"]

    assert run(["social", "--channel", "reddit", "--slug", slug]) == 0

    with db.session() as connection:
        row = connection.execute(
            "SELECT mode FROM publications WHERE channel = 'reddit'"
        ).fetchone()
        item = connection.execute(
            "SELECT status FROM content_items WHERE kind = 'social'"
        ).fetchone()

    assert row["mode"] == "automatic"
    assert item["status"] == "review"

    text = (drafts_dir.parent / "content" / "social").glob("*.md")
    body = next(text).read_text(encoding="utf-8")
    assert "Not posted yet" in body
    assert "mode: automatic" in body


def test_social_refuses_channels_the_project_owns(
    research_rows, drafts_dir
):
    run(["draft", "--topic", "advisories"])

    with db.session() as connection:
        slug = connection.execute(
            "SELECT slug FROM content_items"
        ).fetchone()["slug"]

    # The site publishes itself; a social draft aimed at it is a
    # mistake even though it is an 'automatic' channel.
    assert run(["social", "--channel", "website", "--slug", slug]) == 1


def test_social_refuses_disabled_channels(research_rows, drafts_dir):
    run(["draft", "--topic", "advisories"])

    with db.session() as connection:
        slug = connection.execute(
            "SELECT slug FROM content_items"
        ).fetchone()["slug"]

    assert run(["social", "--channel", "telegram", "--slug", slug]) == 1


def test_social_refuses_an_unknown_draft(research_rows, drafts_dir):
    assert run(["social", "--channel", "reddit", "--slug", "nope"]) == 1


def test_check_reruns_the_gate_on_a_saved_draft(research_rows, drafts_dir,
                                                capsys):
    run(["draft", "--topic", "advisories"])
    capsys.readouterr()

    with db.session() as connection:
        slug = connection.execute(
            "SELECT slug FROM content_items"
        ).fetchone()["slug"]

    assert run(["check", "--slug", slug]) == 0
    assert '"passed": true' in capsys.readouterr().out


def test_check_reports_a_missing_file(research_rows, drafts_dir):
    run(["draft", "--topic", "advisories"])

    with db.session() as connection:
        slug = connection.execute(
            "SELECT slug FROM content_items"
        ).fetchone()["slug"]

    next(drafts_dir.glob("*.md")).unlink()

    assert run(["check", "--slug", slug]) == 1


def test_html_in_feed_summaries_does_not_reach_the_draft(temp_db, drafts_dir):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO research_items
                (source, title, link, canonical_link, summary, topic,
                 relevance)
            VALUES ('s', 'CVE in a vulnerability scanner',
                    'https://example.test/x', 'https://example.test/x',
                    '<script>alert(1)</script>&nbsp;patch now',
                    'advisories', 70)
            """
        )
        connection.commit()

    run(["draft", "--topic", "advisories"])
    text = next(drafts_dir.glob("*.md")).read_text(encoding="utf-8")

    assert "<script>" not in text
    assert "&nbsp;" not in text
    assert "patch now" in text


def test_doubly_escaped_markup_cannot_come_back_as_a_tag(temp_db,
                                                         drafts_dir):
    """Unescaping after stripping tags would re-create <script>."""
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO research_items
                (source, title, link, canonical_link, summary, topic,
                 relevance)
            VALUES ('s', 'CVE in a vulnerability scanner',
                    'https://example.test/x', 'https://example.test/x',
                    '&amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt; patch',
                    'advisories', 70)
            """
        )
        connection.commit()

    run(["draft", "--topic", "advisories"])
    text = next(drafts_dir.glob("*.md")).read_text(encoding="utf-8")

    assert "<script>" not in text
    assert "patch" in text


def test_items_without_a_summary_are_not_used(temp_db, drafts_dir):
    """State carries keys without article text: a digest needs the text."""
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO research_items
                (source, title, link, canonical_link, summary, topic,
                 relevance)
            VALUES ('s', 'A CVE in a vulnerability scanner',
                    'https://example.test/x', 'https://example.test/x',
                    NULL, 'advisories', 70)
            """
        )
        connection.execute(
            """
            INSERT INTO research_items
                (source, title, link, canonical_link, summary, topic,
                 relevance)
            VALUES ('s', 'Another CVE in a scanner',
                    'https://example.test/y', 'https://example.test/y',
                    '   ', 'advisories', 70)
            """
        )
        connection.commit()

    assert run(["draft", "--topic", "advisories"]) == 1
    assert list(drafts_dir.glob("*.md")) == []


def test_a_single_item_digest_reads_as_singular(temp_db, drafts_dir):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO research_items
                (source, title, link, canonical_link, summary, topic,
                 relevance)
            VALUES ('s', 'HTTP header injection made critical',
                    'https://example.test/x', 'https://example.test/x',
                    'A CRLF desync in a web application.', 'web_research',
                    70)
            """
        )
        connection.commit()

    assert run(["draft", "--topic", "web_research"]) == 0

    text = next(drafts_dir.glob("*.md")).read_text(encoding="utf-8")

    assert "1 public security item collected" in text
    assert "1 items" not in text
    assert "This item comes from" in text
