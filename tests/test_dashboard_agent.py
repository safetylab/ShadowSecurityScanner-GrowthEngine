"""Dashboard: renders from tables, escapes content, leaks nothing."""

import importlib
import json
from html.parser import HTMLParser

from sge import db, metrics

dashboard = importlib.import_module("agents.dashboard.main")


def run(argv):
    return dashboard.main(argv)


class Balance(HTMLParser):
    """Checks that every non-void element is closed in order."""

    VOID = {"meta", "link", "br", "img", "input", "hr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.mismatched = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            self.mismatched.append(tag)


def build(tmp_path):
    target = tmp_path / "dashboard.html"
    assert run(["build", "--output", str(target)]) == 0

    return target.read_text(encoding="utf-8")


def test_an_empty_engine_still_renders(temp_db, tmp_path, capsys):
    page = build(tmp_path)
    capsys.readouterr()

    assert page.startswith("<!DOCTYPE html>")
    assert "No collection run recorded yet" in page
    assert "no drafts waiting" in page


def test_html_is_balanced(temp_db, tmp_path, capsys):
    page = build(tmp_path)
    capsys.readouterr()

    checker = Balance()
    checker.feed(page)

    assert checker.stack == []
    assert checker.mismatched == []


def test_content_is_escaped_not_injected(temp_db, tmp_path, capsys):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO community_signals
                (platform, external_id, url, canonical_url, title, advice,
                 relevance, reasons)
            VALUES ('hackernews', '1', 'https://a.test/1',
                    'https://a.test/1',
                    '<script>alert(1)</script> & more', 'reply', 50, ?)
            """,
            (json.dumps(["asking"]),),
        )
        connection.commit()

    page = build(tmp_path)
    capsys.readouterr()

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "&amp; more" in page


def test_tables_are_filled_from_the_database(temp_db, tmp_path, capsys):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO source_runs
                (run_id, source, status, items_seen, items_new, duration_ms)
            VALUES ('r1', 'CISA', 'skipped', 0, 0, 0)
            """
        )
        connection.execute(
            """
            INSERT INTO research_items
                (source, title, link, canonical_link, topic, relevance)
            VALUES ('CISA', 'An advisory', 'https://a.test/x',
                    'https://a.test/x', 'advisories', 50)
            """
        )
        connection.execute(
            """
            INSERT INTO content_items
                (slug, kind, channel, title, path, status, quality_passed)
            VALUES ('d1', 'digest', 'website', 'D', 'p.md', 'draft', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO recommendations
                (run_id, area, priority, title, detail)
            VALUES ('o1', 'seo', 'high', 'Fix the titles', 'detail')
            """
        )
        connection.commit()

    page = build(tmp_path)
    capsys.readouterr()

    assert "CISA" in page
    assert "advisories" in page
    assert "d1" in page
    assert "Fix the titles" in page


def test_a_failed_quality_gate_is_visible_as_state_not_just_text(
    temp_db, tmp_path, capsys
):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO content_items
                (slug, kind, channel, title, path, status, quality_passed)
            VALUES ('bad', 'digest', 'website', 'B', 'p.md', 'draft', 0)
            """
        )
        connection.commit()

    page = build(tmp_path)
    capsys.readouterr()

    assert "gate failed" in page
    assert 'class="pill bad"' in page


def test_the_star_trend_is_stated_when_there_are_two_readings(
    temp_db, tmp_path, capsys
):
    import datetime as dt

    today = dt.date.today()

    with db.session() as connection:
        metrics.record(connection, "github_stars", 10,
                       (today - dt.timedelta(days=10)).isoformat())
        metrics.record(connection, "github_stars", 14, today.isoformat())
        connection.commit()

    page = build(tmp_path)
    capsys.readouterr()

    assert "Stars +4" in page


def test_no_secrets_or_local_paths_reach_the_page(temp_db, tmp_path,
                                                  monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secretvalue1234567890abcd")

    page = build(tmp_path)
    capsys.readouterr()

    assert "ghp_" not in page
    assert "Bearer" not in page
    assert str(tmp_path) not in page


def test_the_page_loads_nothing_from_outside(temp_db, tmp_path, capsys):
    """A dashboard that phones home would be telemetry by the back door."""
    page = build(tmp_path)
    capsys.readouterr()

    assert "<script" not in page
    assert "http://" not in page.replace('xmlns="http://', "")
    for remote in ("cdn.", "fonts.googleapis", "googletagmanager"):
        assert remote not in page
