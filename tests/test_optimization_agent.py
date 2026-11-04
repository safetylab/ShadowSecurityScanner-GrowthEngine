"""Optimization agent: advice with evidence, and no action taken."""

import importlib
import json

from sge import db, metrics

optimisation = importlib.import_module("agents.optimization.main")


def run(argv):
    return optimisation.main(argv)


def recommendations():
    with db.session() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT area, priority, title, detail, evidence "
            "FROM recommendations"
        )]


def test_a_repeated_seo_finding_is_called_a_template_problem(temp_db,
                                                             capsys):
    with db.session() as connection:
        connection.execute(
            "INSERT INTO seo_pages (run_id, url, status) "
            "VALUES ('r1', 'https://a.test/', 200)"
        )
        for index in range(4):
            connection.execute(
                """
                INSERT INTO seo_findings (run_id, url, severity, code,
                                          message)
                VALUES ('r1', ?, 'low', 'title_long', 'too long')
                """,
                (f"https://a.test/{index}",),
            )
        connection.commit()

    assert run(["analyse"]) == 0
    capsys.readouterr()

    finding = next(item for item in recommendations()
                   if item["area"] == "seo")

    assert "template problem" in finding["detail"]
    assert json.loads(finding["evidence"])["pages"] == 4


def test_failing_sources_are_high_priority(temp_db, capsys):
    with db.session() as connection:
        for _ in range(3):
            connection.execute(
                """
                INSERT INTO source_runs (source, status, started_at)
                VALUES ('CISA', 'failed', datetime('now'))
                """
            )
        connection.commit()

    run(["analyse"])
    capsys.readouterr()

    item = next(row for row in recommendations()
                if row["area"] == "research" and "CISA" in row["title"])

    assert item["priority"] == "high"
    assert json.loads(item["evidence"])["failures_14d"] == 3


def test_a_stale_pipeline_is_reported_with_its_age(temp_db, capsys):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO source_runs (source, status, started_at)
            VALUES ('s', 'ok', datetime('now', '-10 days'))
            """
        )
        connection.commit()

    run(["analyse"])
    capsys.readouterr()

    item = next(row for row in recommendations()
                if "No collection run" in row["title"])

    assert json.loads(item["evidence"])["days_since_last_run"] == 10


def test_unclassified_items_lead_to_a_keyword_recommendation(temp_db,
                                                             capsys):
    with db.session() as connection:
        for index in range(10):
            connection.execute(
                """
                INSERT INTO research_items
                    (source, title, link, canonical_link, topic)
                VALUES ('s', 't', ?, ?, 'general')
                """,
                (f"https://a.test/{index}", f"https://a.test/{index}"),
            )
        connection.commit()

    run(["analyse"])
    capsys.readouterr()

    item = next(row for row in recommendations()
                if "no topic" in row["title"])

    assert "rescore" in item["detail"]
    assert json.loads(item["evidence"])["general"] == 10


def test_approved_but_unpublished_content_is_high_priority(temp_db, capsys):
    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO content_items
                (slug, kind, channel, title, path, status, quality_passed)
            VALUES ('a', 'digest', 'website', 'A', 'p.md', 'approved', 1)
            """
        )
        connection.commit()

    run(["analyse"])
    capsys.readouterr()

    item = next(row for row in recommendations()
                if "never been published" in row["title"])

    assert item["priority"] == "high"


def test_flat_stars_are_reported_without_promising_a_fix(temp_db, capsys):
    with db.session() as connection:
        connection.execute(
            "INSERT INTO github_snapshots (run_id, repository) "
            "VALUES ('g1', 'o/r')"
        )
        # Both readings must fall inside the 30-day window the agent
        # looks at, and be at least 14 days apart.
        import datetime as dt

        today = dt.date.today()
        metrics.record(connection, "github_stars", 14,
                       (today - dt.timedelta(days=20)).isoformat())
        metrics.record(connection, "github_stars", 14, today.isoformat())
        connection.commit()

    run(["analyse"])
    capsys.readouterr()

    item = next(row for row in recommendations()
                if "Stars unchanged" in row["title"])

    assert "Nothing in the engine can or should change this number" in \
        item["detail"]


def test_a_clean_engine_produces_only_baseline_advice(temp_db, capsys):
    assert run(["analyse"]) == 0
    capsys.readouterr()

    titles = {row["title"] for row in recommendations()}

    assert titles == {"The site has never been audited",
                      "The repository has never been audited"}


def test_report_lists_advice_and_states_it_changed_nothing(
    temp_db, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(optimisation.paths, "REPORTS_DIR", tmp_path / "r")
    run(["analyse"])
    capsys.readouterr()

    assert run(["report"]) == 0

    text = next((tmp_path / "r").glob("optimisation-*.md")).read_text("utf-8")

    assert "Nothing here was applied" in text
    assert "does not change its own policy" in text


def test_report_needs_an_analysis_first(temp_db):
    assert run(["report"]) == 1


def test_high_priority_comes_first_in_the_report(temp_db, monkeypatch,
                                                 tmp_path, capsys):
    monkeypatch.setattr(optimisation.paths, "REPORTS_DIR", tmp_path / "r")

    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO content_items
                (slug, kind, channel, title, path, status, quality_passed)
            VALUES ('a', 'digest', 'website', 'A', 'p.md', 'approved', 1)
            """
        )
        connection.commit()

    run(["analyse"])
    run(["report"])
    capsys.readouterr()

    text = next((tmp_path / "r").glob("optimisation-*.md")).read_text("utf-8")

    assert text.index("## High priority") < text.index("## Medium priority")


def test_each_priority_heading_appears_once(temp_db, monkeypatch, tmp_path,
                                            capsys):
    """Grouping by area duplicated "## seo" when sorted by priority."""
    monkeypatch.setattr(optimisation.paths, "REPORTS_DIR", tmp_path / "r")

    with db.session() as connection:
        connection.execute(
            "INSERT INTO seo_pages (run_id, url, status) "
            "VALUES ('r1', 'https://a.test/', 200)"
        )
        connection.execute(
            """
            INSERT INTO seo_findings (run_id, url, severity, code, message)
            VALUES ('r1', 'https://a.test/', 'high', 'broken_links', 'x')
            """
        )
        for index in range(3):
            connection.execute(
                """
                INSERT INTO seo_findings (run_id, url, severity, code,
                                          message)
                VALUES ('r1', ?, 'low', 'title_long', 'y')
                """,
                (f"https://a.test/{index}",),
            )
        connection.commit()

    run(["analyse"])
    run(["report"])
    capsys.readouterr()

    text = next((tmp_path / "r").glob("optimisation-*.md")).read_text("utf-8")

    assert text.count("## High priority") == 1
    assert text.count("## Medium priority") == 1


def test_the_agent_writes_no_configuration(temp_db, capsys):
    """Structural: it must not be able to change policy or config."""
    from pathlib import Path

    source = Path(optimisation.__file__).read_text(encoding="utf-8")

    for forbidden in ("write_text", "open(", "yaml.dump", "config.CONFIG",
                      "UPDATE content_items", "UPDATE research_items"):
        if forbidden == "write_text":
            # It writes exactly one thing: its own report.
            assert source.count("write_text") == 1
            continue

        assert forbidden not in source, forbidden
