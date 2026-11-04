"""Metric recording and trends."""

import datetime as dt

from sge import db, metrics


def test_recording_a_metric_twice_replaces_it(temp_db):
    with db.session() as connection:
        metrics.record(connection, "github_stars", 10, "2026-11-04")
        metrics.record(connection, "github_stars", 14, "2026-11-04")
        connection.commit()

        rows = connection.execute(
            "SELECT value, source FROM metrics_daily"
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["value"] == 14
    assert rows[0]["source"] == "github api"


def test_engine_metrics_count_what_is_in_the_tables(temp_db):
    today = metrics.today()

    with db.session() as connection:
        connection.execute(
            """
            INSERT INTO research_items
                (source, title, link, canonical_link, topic, fetched_at)
            VALUES ('s', 't', 'https://a.test/x', 'https://a.test/x',
                    'advisories', ?)
            """,
            (f"{today} 10:00:00",),
        )
        connection.execute(
            """
            INSERT INTO content_items
                (slug, kind, channel, title, path, status)
            VALUES ('a', 'digest', 'website', 'A', 'p.md', 'published')
            """
        )
        connection.commit()

        collected = metrics.collect_engine_metrics(connection)
        connection.commit()

    assert collected["research_items_total"] == 1
    assert collected["research_items_new"] == 1
    assert collected["content_published"] == 1
    assert collected["content_drafts"] == 0


def test_github_metrics_come_from_a_snapshot(temp_db):
    with db.session() as connection:
        values = metrics.collect_github_metrics(connection, {
            "stars": 14, "forks": 2, "watchers": 0, "open_issues": 1,
            "releases": 14,
        })
        connection.commit()

    assert values["github_stars"] == 14
    assert values["github_releases"] == 14


def test_no_snapshot_records_nothing(temp_db):
    with db.session() as connection:
        assert metrics.collect_github_metrics(connection, None) == {}


def test_series_is_ordered_oldest_first(temp_db):
    with db.session() as connection:
        for day, value in (("2026-11-03", 12), ("2026-11-01", 10),
                           ("2026-11-04", 14)):
            metrics.record(connection, "github_stars", value, day)
        connection.commit()

        points = metrics.series(connection, "github_stars", days=30)

    assert [day for day, _ in points] == ["2026-11-01", "2026-11-03",
                                          "2026-11-04"]


def test_series_respects_the_window(temp_db):
    old_day = (dt.date.today() - dt.timedelta(days=60)).isoformat()

    with db.session() as connection:
        metrics.record(connection, "github_stars", 1, old_day)
        metrics.record(connection, "github_stars", 14, metrics.today())
        connection.commit()

        assert len(metrics.series(connection, "github_stars", days=7)) == 1


def test_change_reports_the_delta_and_the_days_covered(temp_db):
    with db.session() as connection:
        metrics.record(connection, "github_stars", 10, "2026-11-01")
        metrics.record(connection, "github_stars", 14, "2026-11-04")
        connection.commit()

        latest, delta, days = metrics.change(connection, "github_stars",
                                             days=3650)

    assert (latest, delta) == (14, 4)
    assert days == 3


def test_a_single_reading_has_no_delta(temp_db):
    with db.session() as connection:
        metrics.record(connection, "github_stars", 14)
        connection.commit()

        latest, delta, days = metrics.change(connection, "github_stars")

    assert (latest, delta, days) == (14, None, 1)


def test_an_unknown_metric_has_no_data(temp_db):
    with db.session() as connection:
        assert metrics.change(connection, "nothing") == (None, None, 0)
        assert metrics.series(connection, "nothing") == []


def test_every_recorded_metric_declares_its_source(temp_db):
    with db.session() as connection:
        metrics.collect_engine_metrics(connection)
        connection.commit()

        unknown = connection.execute(
            "SELECT metric FROM metrics_daily WHERE source = 'unknown'"
        ).fetchall()

    assert unknown == []
