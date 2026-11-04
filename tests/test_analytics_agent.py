"""Analytics agent: records only measurable values, reports honestly."""

import importlib

import pytest

from sge import db, github, metrics

analytics = importlib.import_module("agents.analytics.main")


def run(argv):
    return analytics.main(argv)


def test_collect_without_github_records_local_metrics_only(temp_db, capsys):
    assert run(["collect", "--no-github"]) == 0
    capsys.readouterr()

    with db.session() as connection:
        recorded = {row["metric"] for row in connection.execute(
            "SELECT metric FROM metrics_daily"
        )}

    assert "research_items_total" in recorded
    assert not any(name.startswith("github_") for name in recorded)


def test_github_failure_does_not_lose_local_metrics(temp_db, monkeypatch,
                                                    capsys):
    def failing(name):
        raise github.GitHubError("rate limited")

    monkeypatch.setattr(github, "repository", failing)

    assert run(["collect"]) == 0
    capsys.readouterr()

    with db.session() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS n FROM metrics_daily"
        ).fetchone()["n"] > 0


def test_report_needs_metrics_first(temp_db):
    assert run(["report"]) == 1


@pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
def test_reports_state_what_is_not_measured(temp_db, monkeypatch, tmp_path,
                                            capsys, period):
    monkeypatch.setattr(analytics.paths, "REPORTS_DIR", tmp_path / "r")
    run(["collect", "--no-github"])
    capsys.readouterr()

    assert run(["report", "--period", period]) == 0

    text = next((tmp_path / "r").glob(f"{period}-*.md")).read_text("utf-8")

    assert "Not measured" in text
    assert "no privacy-friendly analytics" in text
    assert "telemetry" in text


def test_report_shows_a_trend_once_there_are_two_readings(
    temp_db, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(analytics.paths, "REPORTS_DIR", tmp_path / "r")

    with db.session() as connection:
        metrics.record(connection, "github_stars", 10, "2026-11-01")
        metrics.record(connection, "github_stars", 14, "2026-11-04")
        connection.commit()

    assert run(["report", "--period", "monthly"]) == 0
    capsys.readouterr()

    text = next((tmp_path / "r").glob("monthly-*.md")).read_text("utf-8")

    assert "+4" in text


def test_series_prints_points_in_order(temp_db, capsys):
    with db.session() as connection:
        metrics.record(connection, "github_stars", 10, "2026-11-01")
        metrics.record(connection, "github_stars", 14, "2026-11-04")
        connection.commit()

    assert run(["series", "--metric", "github_stars"]) == 0
    output = capsys.readouterr().out

    assert output.index("2026-11-01") < output.index("2026-11-04")


def test_series_of_an_unknown_metric_says_so(temp_db, capsys):
    assert run(["series", "--metric", "nothing"]) == 0
    assert "No data" in capsys.readouterr().out
