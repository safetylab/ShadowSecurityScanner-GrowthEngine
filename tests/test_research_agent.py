"""Research agent against a local server: dedup, isolation, reporting."""

import importlib

import pytest

from sge import config, db

research = importlib.import_module("agents.research.main")


@pytest.fixture
def sources(monkeypatch, feed_server):
    """Replace configured sources with routes on the local server."""
    registered = []

    def configure(*entries):
        registered.clear()
        registered.extend(entries)

        monkeypatch.setattr(
            config, "load_sources",
            lambda: {"sources": list(registered),
                     "limits": {"timeout_seconds": 3,
                                "max_response_bytes": 2_000_000,
                                "max_items_per_feed": 30}},
        )

    configure.server = feed_server
    return configure


def run(argv):
    return research.main(argv)


def count_items():
    with db.session() as connection:
        return connection.execute(
            "SELECT COUNT(*) AS n FROM research_items"
        ).fetchone()["n"]


def runs_by_status():
    with db.session() as connection:
        return {
            row["source"]: row["status"]
            for row in connection.execute(
                "SELECT source, status FROM source_runs"
            )
        }


def test_collect_stores_items(temp_db, sources, rss_feed):
    url = sources.server.serve("/feed.xml", rss_feed)
    sources({"name": "local", "url": url, "enabled": True})

    assert run(["collect"]) == 0
    assert count_items() == 2


def test_second_collect_creates_no_duplicates(temp_db, sources, rss_feed):
    url = sources.server.serve("/feed.xml", rss_feed)
    sources({"name": "local", "url": url, "enabled": True})

    run(["collect"])
    run(["collect"])

    assert count_items() == 2

    with db.session() as connection:
        runs = connection.execute(
            "SELECT items_new FROM source_runs ORDER BY id"
        ).fetchall()

    assert [row["items_new"] for row in runs] == [2, 0]


def test_the_same_url_with_tracking_parameters_is_one_item(
    temp_db, sources
):
    first = (
        b"<rss><channel><item><title>Post</title>"
        b"<link>https://example.test/post?utm_source=rss</link>"
        b"</item></channel></rss>"
    )
    second = (
        b"<rss><channel><item><title>Post</title>"
        b"<link>https://example.test/post?ref=newsletter</link>"
        b"</item></channel></rss>"
    )
    url_a = sources.server.serve("/a.xml", first)
    url_b = sources.server.serve("/b.xml", second)
    sources({"name": "a", "url": url_a, "enabled": True},
            {"name": "b", "url": url_b, "enabled": True})

    run(["collect"])

    assert count_items() == 1


def test_one_failing_source_does_not_stop_the_others(
    temp_db, sources, rss_feed
):
    good = sources.server.serve("/good.xml", rss_feed)
    broken = sources.server.serve("/broken.xml", b"<rss><channel><item>")
    missing = f"{sources.server.url}/does-not-exist.xml"
    sources({"name": "broken", "url": broken, "enabled": True},
            {"name": "missing", "url": missing, "enabled": True},
            {"name": "good", "url": good, "enabled": True})

    assert run(["collect"]) == 0
    assert count_items() == 2
    assert runs_by_status() == {"broken": "failed", "missing": "failed",
                               "good": "ok"}


def test_a_source_serving_html_is_a_failure_not_an_empty_success(
    temp_db, sources
):
    url = sources.server.serve(
        "/html", b"<html><body>Not found</body></html>",
        content_type="text/html",
    )
    sources({"name": "html", "url": url, "enabled": True})

    assert run(["collect"]) == 1
    assert runs_by_status() == {"html": "failed"}


def test_all_sources_failing_is_an_error_exit(temp_db, sources):
    url = sources.server.serve("/broken.xml", b"<rss><channel><item>")
    sources({"name": "broken", "url": url, "enabled": True})

    assert run(["collect"]) == 1


def test_oversized_response_is_refused(temp_db, sources):
    body = (b"<rss><channel>"
            + b"<item><title>x</title><link>https://a.test/x</link></item>"
            + b"<!--" + b"p" * 3_000_000 + b"-->"
            + b"</channel></rss>")
    url = sources.server.serve("/big.xml", body)
    sources({"name": "big", "url": url, "enabled": True})

    run(["collect"])

    assert runs_by_status() == {"big": "failed"}
    with db.session() as connection:
        error = connection.execute(
            "SELECT error FROM source_runs"
        ).fetchone()["error"]

    assert "exceeds limit" in error or "exceeded limit" in error


def test_slow_source_times_out_and_is_recorded(temp_db, sources, rss_feed):
    url = sources.server.serve("/slow.xml", rss_feed, delay=5)
    sources({"name": "slow", "url": url, "enabled": True})

    run(["collect"])

    assert runs_by_status() == {"slow": "failed"}


def test_disabled_sources_are_skipped(temp_db, sources, rss_feed):
    url = sources.server.serve("/feed.xml", rss_feed)
    sources({"name": "off", "url": url, "enabled": False})

    assert run(["collect"]) == 0
    assert count_items() == 0
    assert runs_by_status() == {"off": "skipped"}


def test_dry_run_writes_nothing(temp_db, sources, rss_feed):
    url = sources.server.serve("/feed.xml", rss_feed)
    sources({"name": "local", "url": url, "enabled": True})

    assert run(["collect", "--dry-run"]) == 0
    assert count_items() == 0


def test_items_without_a_usable_link_are_skipped(temp_db, sources):
    body = (
        b"<rss><channel>"
        b"<item><title>Bad link</title>"
        b"<link>javascript:alert(1)</link></item>"
        b"<item><title>Good</title>"
        b"<link>https://example.test/ok</link></item>"
        b"</channel></rss>"
    )
    url = sources.server.serve("/mixed.xml", body)
    sources({"name": "mixed", "url": url, "enabled": True})

    run(["collect"])

    assert count_items() == 1


def test_relevance_and_topic_are_stored(temp_db, sources, rss_feed):
    url = sources.server.serve("/feed.xml", rss_feed)
    sources({"name": "local", "url": url, "enabled": True})

    run(["collect"])

    with db.session() as connection:
        row = connection.execute(
            "SELECT topic, relevance FROM research_items "
            "ORDER BY relevance DESC LIMIT 1"
        ).fetchone()

    assert row["relevance"] > 0
    assert row["topic"] != ""


def test_single_source_collection(temp_db, sources, rss_feed, atom_feed):
    url_a = sources.server.serve("/a.xml", rss_feed)
    url_b = sources.server.serve("/b.xml", atom_feed)
    sources({"name": "a", "url": url_a, "enabled": True},
            {"name": "b", "url": url_b, "enabled": True})

    assert run(["collect", "--source", "b"]) == 0
    assert count_items() == 1


def test_unknown_source_name_is_an_error(temp_db, sources, rss_feed):
    url = sources.server.serve("/a.xml", rss_feed)
    sources({"name": "a", "url": url, "enabled": True})

    assert run(["collect", "--source", "nope"]) == 1


def test_report_is_written_and_lists_sources(temp_db, sources, rss_feed,
                                             monkeypatch, tmp_path):
    monkeypatch.setattr(research.paths, "REPORTS_DIR", tmp_path / "reports")
    url = sources.server.serve("/feed.xml", rss_feed)
    sources({"name": "local", "url": url, "enabled": True})
    run(["collect"])

    assert run(["report", "--days", "1"]) == 0

    reports = list((tmp_path / "reports").glob("research-*.md"))
    assert len(reports) == 1

    text = reports[0].read_text(encoding="utf-8")
    assert "local" in text
    assert "https://example.test/a" in text


def test_report_handles_an_empty_window(temp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(research.paths, "REPORTS_DIR", tmp_path / "reports")

    assert run(["report", "--days", "1"]) == 0
