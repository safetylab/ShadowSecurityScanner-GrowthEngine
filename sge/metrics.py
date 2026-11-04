"""Collecting and reading the engine's own metrics.

Only numbers we can actually observe. Nothing here estimates traffic,
infers installs or invents a funnel: a metric we cannot measure honestly
is absent rather than guessed.
"""

import datetime as dt

# Metric name -> how it is obtained. Kept explicit so a report can say
# where a number came from.
SOURCES = {
    "github_stars": "github api",
    "github_forks": "github api",
    "github_watchers": "github api",
    "github_open_issues": "github api",
    "github_releases": "github api",
    "research_items_total": "engine database",
    "research_items_new": "engine database",
    "research_sources_ok": "engine database",
    "research_sources_failed": "engine database",
    "content_drafts": "engine database",
    "content_published": "engine database",
    "community_signals_open": "engine database",
    "seo_findings_open": "engine database",
}


def today():
    return dt.date.today().isoformat()


def record(connection, metric, value, day=None, source=None):
    """Store one metric for one day; re-recording replaces the value."""
    connection.execute(
        """
        INSERT INTO metrics_daily (day, metric, value, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(day, metric) DO UPDATE SET
            value = excluded.value,
            source = excluded.source,
            recorded_at = CURRENT_TIMESTAMP
        """,
        (day or today(), metric, float(value),
         source or SOURCES.get(metric, "unknown")),
    )


def collect_engine_metrics(connection, day=None):
    """Metrics the engine knows about itself, without any network call."""
    target = day or today()

    counts = {
        "research_items_total": connection.execute(
            "SELECT COUNT(*) AS n FROM research_items"
        ).fetchone()["n"],
        "research_items_new": connection.execute(
            "SELECT COUNT(*) AS n FROM research_items "
            "WHERE substr(fetched_at, 1, 10) = ?", (target,)
        ).fetchone()["n"],
        "research_sources_ok": connection.execute(
            "SELECT COUNT(*) AS n FROM source_runs "
            "WHERE status = 'ok' AND substr(started_at, 1, 10) = ?",
            (target,)
        ).fetchone()["n"],
        "research_sources_failed": connection.execute(
            "SELECT COUNT(*) AS n FROM source_runs "
            "WHERE status = 'failed' AND substr(started_at, 1, 10) = ?",
            (target,)
        ).fetchone()["n"],
        "content_drafts": connection.execute(
            "SELECT COUNT(*) AS n FROM content_items WHERE status IN "
            "('draft', 'review', 'approved')"
        ).fetchone()["n"],
        "content_published": connection.execute(
            "SELECT COUNT(*) AS n FROM content_items "
            "WHERE status = 'published'"
        ).fetchone()["n"],
        "community_signals_open": connection.execute(
            "SELECT COUNT(*) AS n FROM community_signals "
            "WHERE reviewed = 0 AND advice != 'skip'"
        ).fetchone()["n"],
    }

    latest_seo = connection.execute(
        "SELECT run_id FROM seo_pages ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if latest_seo:
        counts["seo_findings_open"] = connection.execute(
            "SELECT COUNT(*) AS n FROM seo_findings WHERE run_id = ?",
            (latest_seo["run_id"],),
        ).fetchone()["n"]

    for metric, value in counts.items():
        record(connection, metric, value, target)

    return counts


def collect_github_metrics(connection, snapshot, day=None):
    """Metrics from the most recent GitHub snapshot."""
    if not snapshot:
        return {}

    values = {
        "github_stars": snapshot["stars"],
        "github_forks": snapshot["forks"],
        "github_watchers": snapshot["watchers"],
        "github_open_issues": snapshot["open_issues"],
        "github_releases": snapshot["releases"],
    }

    for metric, value in values.items():
        record(connection, metric, value, day)

    return values


def series(connection, metric, days=30):
    """Daily values for one metric, oldest first."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()

    return [
        (row["day"], row["value"])
        for row in connection.execute(
            "SELECT day, value FROM metrics_daily "
            "WHERE metric = ? AND day >= ? ORDER BY day",
            (metric, since),
        )
    ]


def change(connection, metric, days=7):
    """(latest value, change over the window, days actually covered)."""
    points = series(connection, metric, days)

    if not points:
        return None, None, 0

    latest = points[-1][1]

    if len(points) == 1:
        return latest, None, 1

    first_day = dt.date.fromisoformat(points[0][0])
    last_day = dt.date.fromisoformat(points[-1][0])

    return latest, latest - points[0][1], (last_day - first_day).days or 1
