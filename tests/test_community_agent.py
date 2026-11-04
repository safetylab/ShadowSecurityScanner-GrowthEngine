"""Community radar agent: scanning, storing, drafting. Never posting."""

import importlib
import json

import pytest

from sge import community, db, hackernews

radar = importlib.import_module("agents.community.main")


def run(argv):
    return radar.main(argv)


RELEVANT = {
    "platform": "hackernews", "external_id": "1",
    "url": "https://news.ycombinator.com/item?id=1",
    "title": "Which open source vulnerability scanner do you recommend?",
    "author": "someone", "excerpt": "We need network scanning and CVE data",
    "created_at_source": "2026-11-03T00:00:00Z",
    "points": 10, "comments": 4,
}

OFF_TOPIC = {
    **RELEVANT, "external_id": "2",
    "url": "https://news.ycombinator.com/item?id=2",
    "title": "Pando - OpenAI's alternative to Git",
    "excerpt": "",
}


@pytest.fixture
def hn(monkeypatch, temp_db):
    """Replace the Hacker News search with a fixed result set."""
    served = {"hits": [RELEVANT, OFF_TOPIC]}

    def fake_search(query, tags="story", limit=20, since_days=None):
        fake_search.calls.append((query, since_days))
        return list(served["hits"])

    fake_search.calls = []
    monkeypatch.setattr(hackernews, "search", fake_search)
    monkeypatch.setattr(
        community, "load_rules",
        lambda: {
            "queries": {"hackernews": ['"scanner"'], "github": []},
            "intent_signals": {"asking": ["recommend"],
                               "discussing": ["comparison"]},
            "avoid": ["hiring"],
            "rules": {"max_age_days": 120, "min_relevance": 25,
                      "min_topic_relevance": 25, "mode": "draft_only"},
        },
    )

    served["search"] = fake_search
    return served


def signals():
    with db.session() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT id, title, advice, relevance, intent, reasons "
            "FROM community_signals"
        )]


def test_only_worthwhile_threads_are_stored(hn):
    assert run(["scan", "--platform", "hackernews"]) == 0

    stored = signals()
    assert len(stored) == 1
    assert stored[0]["advice"] == "reply"
    assert "vulnerability scanner" in stored[0]["title"]


def test_the_date_filter_is_passed_to_the_search(hn):
    run(["scan", "--platform", "hackernews"])

    assert hn["search"].calls == [('"scanner"', 120)]


def test_rescanning_does_not_duplicate(hn):
    run(["scan", "--platform", "hackernews"])
    run(["scan", "--platform", "hackernews"])

    assert len(signals()) == 1


def test_dry_run_stores_nothing(hn):
    assert run(["scan", "--platform", "hackernews", "--dry-run"]) == 0
    assert signals() == []


def test_a_failing_search_does_not_abort_the_scan(hn, monkeypatch):
    def failing(query, tags="story", limit=20, since_days=None):
        raise hackernews.HackerNewsError("rate limited")

    monkeypatch.setattr(hackernews, "search", failing)

    assert run(["scan", "--platform", "hackernews"]) == 0


def test_list_shows_stored_signals(hn, capsys):
    run(["scan", "--platform", "hackernews"])
    capsys.readouterr()

    run(["list"])
    output = capsys.readouterr().out

    assert "REPLY" in output
    assert "recommend" in output


def test_list_filters_by_advice(hn, capsys):
    run(["scan", "--platform", "hackernews"])
    capsys.readouterr()

    run(["list", "--advice", "consider"])
    assert "Nothing on the radar" in capsys.readouterr().out


def test_reviewed_signals_are_hidden_by_default(hn, capsys):
    run(["scan", "--platform", "hackernews"])
    signal_id = signals()[0]["id"]
    capsys.readouterr()

    assert run(["review", "--id", str(signal_id)]) == 0

    run(["list"])
    assert "Nothing on the radar" in capsys.readouterr().out

    run(["list", "--include-reviewed"])
    assert "REPLY" in capsys.readouterr().out


def test_draft_carries_disclosure_and_a_checklist(hn, monkeypatch, tmp_path,
                                                  capsys):
    monkeypatch.setattr(radar.paths, "DRAFTS_DIR", tmp_path / "drafts")
    run(["scan", "--platform", "hackernews"])
    signal_id = signals()[0]["id"]
    capsys.readouterr()

    assert run(["draft", "--id", str(signal_id)]) == 0

    text = next((tmp_path / "drafts" / "community").glob("*.md")).read_text(
        "utf-8"
    )

    assert "mode: draft_only" in text
    assert "biased source" in text
    assert "has no code that can post" in text
    assert "ShadowSecurityScanner" in text


def test_draft_refuses_a_thread_judged_not_worth_it(hn, temp_db):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO community_signals
                (platform, external_id, url, canonical_url, title, advice,
                 reasons)
            VALUES ('hackernews', '9', 'https://news.ycombinator.com/item?id=9',
                    'https://news.ycombinator.com/item?id=9', 'Off topic',
                    'skip', ?)
            """,
            (json.dumps(["not about our subject"]),),
        )
        connection.commit()
        signal_id = connection.execute(
            "SELECT id FROM community_signals"
        ).fetchone()["id"]

    assert run(["draft", "--id", str(signal_id)]) == 1


def test_unknown_ids_are_errors(temp_db):
    assert run(["draft", "--id", "999"]) == 1
    assert run(["review", "--id", "999"]) == 1


def test_the_agent_has_no_posting_code():
    """A structural guarantee, not a behavioural one."""
    source = (radar.Path(radar.__file__).read_text(encoding="utf-8")
              if hasattr(radar, "Path") else "")

    from pathlib import Path

    source = Path(radar.__file__).read_text(encoding="utf-8")

    for forbidden in ("urlopen", "requests.post", "net.fetch",
                      "POST", "api.reddit", "oauth"):
        assert forbidden not in source, forbidden
