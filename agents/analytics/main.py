"""Analytics Agent.

Records what can actually be measured — GitHub counters and the engine's
own tables — and writes daily, weekly and monthly reports from the
recorded history.

No hidden telemetry: nothing is added to ShadowSecurityScanner, and no
visitor is tracked. Website traffic is absent from these reports because
there is no privacy-friendly analytics connected yet; an unmeasurable
number is left out rather than estimated.

Usage:
    python agents/analytics/main.py collect [--no-github]
    python agents/analytics/main.py report [--period daily|weekly|monthly]
    python agents/analytics/main.py series --metric github_stars
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import config, db, github, metrics, paths  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "analytics"
PERIODS = {"daily": 1, "weekly": 7, "monthly": 30}

log = get_logger("sge.analytics")


def command_collect(args):
    with db.session() as connection:
        db.migrate(connection)

        engine = metrics.collect_engine_metrics(connection)
        collected = dict(engine)

        if not args.no_github:
            repo_name = github.owner_repo_from_url(
                config.load_brand()["project"]["github"]
            )

            try:
                repo = github.repository(repo_name)
                # Ask for a full page: counting a one-item request would
                # record "1 release" forever.
                releases = github.releases(repo_name, 100)
            except github.GitHubError as error:
                log.warning("GitHub metrics unavailable: %s", error)
            else:
                snapshot = {
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "watchers": repo.get("subscribers_count", 0),
                    "open_issues": repo.get("open_issues_count", 0),
                    "releases": len(releases),
                }
                collected.update(
                    metrics.collect_github_metrics(connection, snapshot)
                )

        db.log_event(connection, "metrics_collected", AGENT, None,
                     {"metrics": len(collected)})
        connection.commit()

    for name, value in sorted(collected.items()):
        print(f"{name:28} {value}")

    log.info("Recorded %d metric(s) for %s", len(collected), metrics.today())
    return 0


def _trend_line(connection, metric, days):
    latest, delta, covered = metrics.change(connection, metric, days)

    if latest is None:
        return f"| {metric} | no data | — |"

    if delta is None:
        return f"| {metric} | {latest:g} | first reading |"

    direction = f"{delta:+g} over {covered} day(s)"

    return f"| {metric} | {latest:g} | {direction} |"


def command_report(args):
    days = PERIODS[args.period]
    today = dt.date.today().isoformat()

    with db.session() as connection:
        db.migrate(connection)

        recorded = [
            row["metric"] for row in connection.execute(
                "SELECT DISTINCT metric FROM metrics_daily ORDER BY metric"
            )
        ]

        if not recorded:
            log.error("No metrics recorded yet. Run: analytics collect")
            return 1

        lines = [
            f"# {args.period.capitalize()} report {today}",
            "",
            f"Window: {days} day(s). Only measured values appear here.",
            "",
            "| Metric | Latest | Change |",
            "| --- | --- | --- |",
        ]

        for metric in recorded:
            lines.append(_trend_line(connection, metric, days))

        runs = connection.execute(
            """
            SELECT status, COUNT(*) AS n FROM source_runs
            WHERE started_at >= date('now', ?)
            GROUP BY status
            """,
            (f"-{days} days",),
        ).fetchall()

        lines += ["", "## Collection health", ""]

        if runs:
            for row in runs:
                lines.append(f"- {row['status']}: {row['n']} source run(s)")
        else:
            lines.append("- no collection runs in this window")

        drafts = connection.execute(
            """
            SELECT status, COUNT(*) AS n FROM content_items
            GROUP BY status ORDER BY status
            """
        ).fetchall()

        lines += ["", "## Content pipeline", ""]

        if drafts:
            for row in drafts:
                lines.append(f"- {row['status']}: {row['n']}")
        else:
            lines.append("- nothing in the pipeline")

        signals = connection.execute(
            """
            SELECT advice, COUNT(*) AS n FROM community_signals
            WHERE reviewed = 0 GROUP BY advice
            """
        ).fetchall()

        lines += ["", "## Community radar, awaiting review", ""]

        if signals:
            for row in signals:
                lines.append(f"- {row['advice']}: {row['n']}")
        else:
            lines.append("- nothing awaiting review")

        lines += [
            "",
            "## Not measured",
            "",
            "- website visitors, referrers and documentation clicks: no "
            "privacy-friendly analytics is connected",
            "- downloads and installs: not observable without telemetry, "
            "which the project does not ship",
            "- enterprise enquiries: recorded by hand, not here",
        ]

    report = "\n".join(lines) + "\n"

    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.REPORTS_DIR / f"{args.period}-{today}.md"
    target.write_text(report, encoding="utf-8")

    print(report)
    log.info("Report written: %s", target)
    return 0


def command_series(args):
    with db.session() as connection:
        db.migrate(connection)
        points = metrics.series(connection, args.metric, args.days)

    if not points:
        print(f"No data for {args.metric}.")
        return 0

    width = max(len(day) for day, _ in points)

    for day, value in points:
        print(f"{day:{width}}  {value:g}")

    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine Analytics Agent"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="record today's metrics")
    collect.add_argument("--no-github", action="store_true",
                         help="skip the GitHub API, record local metrics only")

    report = commands.add_parser("report", help="write a markdown report")
    report.add_argument("--period", choices=tuple(PERIODS), default="daily")

    series = commands.add_parser("series", help="print one metric over time")
    series.add_argument("--metric", required=True)
    series.add_argument("--days", type=int, default=30)

    return parser


HANDLERS = {
    "collect": command_collect,
    "report": command_report,
    "series": command_series,
}


def main(argv=None):
    args = build_parser().parse_args(argv)

    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
