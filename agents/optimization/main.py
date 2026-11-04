"""Optimization Agent.

Reads the recorded metrics and the other agents' findings, then states
what to do next and on what evidence. Every recommendation names the
numbers it came from, so a wrong one can be argued with.

It changes nothing: not the configuration, not the publishing policy,
not the budget, not a security setting, not a strategic goal. It writes
rows into `recommendations` and a report. Acting on them is a human
decision.

Usage:
    python agents/optimization/main.py analyse
    python agents/optimization/main.py report [--run-id ID]
"""

import argparse
import datetime as dt
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import db, metrics, paths  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "optimization"

# Thresholds that turn an observation into advice. Named so a reader can
# disagree with the number rather than guess at it.
STALE_RESEARCH_DAYS = 3
MIN_PUBLISHABLE_SHARE = 0.4
MANY_SEO_FINDINGS = 10
WAITING_SIGNALS = 3
DRAFT_BACKLOG = 5

log = get_logger("sge.optimization")


def _recommend(area, priority, title, detail, evidence):
    return {
        "area": area, "priority": priority, "title": title,
        "detail": detail, "evidence": evidence,
    }


def analyse_research(connection):
    """Advice about the collection pipeline."""
    found = []

    failed = connection.execute(
        """
        SELECT source, COUNT(*) AS failures FROM source_runs
        WHERE status = 'failed' AND started_at >= date('now', '-14 days')
        GROUP BY source ORDER BY failures DESC
        """
    ).fetchall()

    for row in failed:
        found.append(_recommend(
            "research", "high",
            f"Source {row['source']} keeps failing",
            "Either fix the feed URL or mark the source as unavailable in "
            "this environment, so a known block stops showing up as an "
            "error every run.",
            {"failures_14d": row["failures"], "source": row["source"]},
        ))

    last = connection.execute(
        "SELECT MAX(started_at) AS latest FROM source_runs"
    ).fetchone()["latest"]

    if last:
        age = (dt.date.today()
               - dt.date.fromisoformat(last[:10])).days

        if age > STALE_RESEARCH_DAYS:
            found.append(_recommend(
                "research", "high",
                f"No collection run for {age} days",
                "The schedule may be disabled or failing. Check the "
                "workflow's recent runs before trusting any report.",
                {"days_since_last_run": age, "last_run": last},
            ))

    total = connection.execute(
        "SELECT COUNT(*) AS n FROM research_items"
    ).fetchone()["n"]

    if total:
        general = connection.execute(
            "SELECT COUNT(*) AS n FROM research_items WHERE topic = 'general'"
        ).fetchone()["n"]
        share = general / total

        if share > 1 - MIN_PUBLISHABLE_SHARE:
            found.append(_recommend(
                "research", "medium",
                f"{general} of {total} items have no topic",
                "Topic keywords are missing the vocabulary these sources "
                "actually use. Read a sample of the unclassified items, "
                "add the terms they share to config/keywords.yml, then run "
                "`research rescore`.",
                {"general": general, "total": total,
                 "share": round(share, 2)},
            ))

    return found


def analyse_content(connection):
    """Advice about drafts and publishing."""
    found = []

    rows = {
        row["status"]: row["n"]
        for row in connection.execute(
            "SELECT status, COUNT(*) AS n FROM content_items GROUP BY status"
        )
    }

    waiting = rows.get("draft", 0) + rows.get("review", 0)

    if waiting >= DRAFT_BACKLOG:
        found.append(_recommend(
            "content", "medium",
            f"{waiting} drafts are waiting for review",
            "Drafts age badly: an advisory digest is worth little a month "
            "after the advisories. Review and publish, or delete the ones "
            "that no longer matter.",
            {"waiting": waiting},
        ))

    if rows.get("approved", 0) and not rows.get("published", 0):
        found.append(_recommend(
            "content", "high",
            "Approved drafts have never been published",
            "Run `distribution publish --slug <slug>` for the approved "
            "items, then rebuild the feed with `distribution rss`.",
            {"approved": rows["approved"]},
        ))

    failed_gate = connection.execute(
        "SELECT COUNT(*) AS n FROM content_items WHERE quality_passed = 0"
    ).fetchone()["n"]

    if failed_gate:
        found.append(_recommend(
            "content", "high",
            f"{failed_gate} draft(s) did not pass the quality gate",
            "Check them with `content check --slug <slug>`: a gate failure "
            "is usually a missing source link or an unverifiable claim.",
            {"failed_gate": failed_gate},
        ))

    return found


def analyse_seo(connection):
    """Advice from the latest SEO audit."""
    found = []

    latest = connection.execute(
        "SELECT run_id FROM seo_pages ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if not latest:
        return [_recommend(
            "seo", "medium", "The site has never been audited",
            "Run `seo audit --check-links` to get a baseline before "
            "changing any page.", {},
        )]

    by_code = connection.execute(
        """
        SELECT code, severity, COUNT(*) AS n FROM seo_findings
        WHERE run_id = ? GROUP BY code ORDER BY n DESC
        """,
        (latest["run_id"],),
    ).fetchall()

    critical = [row for row in by_code
                if row["severity"] in ("critical", "high")]

    for row in critical:
        found.append(_recommend(
            "seo", "high",
            f"{row['n']} page(s): {row['code']}",
            "A critical or high SEO finding keeps pages out of search "
            "results or sends the wrong page to it. See the SEO report "
            "for the affected URLs.",
            {"code": row["code"], "pages": row["n"]},
        ))

    repeated = [row for row in by_code
                if row["n"] >= 3 and row["severity"] not in
                ("critical", "high")]

    for row in repeated:
        found.append(_recommend(
            "seo", "medium",
            f"{row['code']} repeats on {row['n']} pages",
            "A finding on most pages is a template problem, not a page "
            "problem: fix it where the layout is generated.",
            {"code": row["code"], "pages": row["n"]},
        ))

    return found


def analyse_github(connection):
    """Advice from the latest repository audit."""
    found = []

    latest = connection.execute(
        "SELECT run_id FROM github_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if not latest:
        return [_recommend(
            "github", "medium", "The repository has never been audited",
            "Run `github audit` for a baseline of what a newcomer sees.",
            {},
        )]

    serious = connection.execute(
        """
        SELECT code, message FROM github_findings
        WHERE run_id = ? AND severity IN ('critical', 'high')
        """,
        (latest["run_id"],),
    ).fetchall()

    for row in serious:
        found.append(_recommend(
            "github", "high", row["code"].replace("_", " ").capitalize(),
            row["message"] + " Drafts for the missing community files are "
            "available: `github templates`.",
            {"code": row["code"]},
        ))

    stars, delta, days = metrics.change(connection, "github_stars", 30)

    if stars is not None and delta is not None and delta == 0 and days >= 14:
        found.append(_recommend(
            "github", "medium",
            f"Stars unchanged at {stars:g} for {days} days",
            "Nothing in the engine can or should change this number. What "
            "moves it is a release people want, documentation they can "
            "follow, and answers in threads where the tool genuinely "
            "fits — see the community radar.",
            {"stars": stars, "days": days},
        ))

    return found


def analyse_community(connection):
    """Advice about threads awaiting a human."""
    found = []

    waiting = connection.execute(
        """
        SELECT advice, COUNT(*) AS n FROM community_signals
        WHERE reviewed = 0 AND advice != 'skip' GROUP BY advice
        """
    ).fetchall()

    total = sum(row["n"] for row in waiting)

    if total >= WAITING_SIGNALS:
        found.append(_recommend(
            "community", "medium",
            f"{total} thread(s) are waiting for a reply",
            "These age out: the radar skips anything older than the "
            "configured window. Read them, draft with `community draft "
            "--id N`, post by hand or mark reviewed.",
            {"waiting": total},
        ))

    return found


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def command_analyse(_args):
    run_id = uuid.uuid4().hex[:12]

    with db.session() as connection:
        db.migrate(connection)

        found = (analyse_research(connection)
                 + analyse_content(connection)
                 + analyse_seo(connection)
                 + analyse_github(connection)
                 + analyse_community(connection))

        found.sort(key=lambda item: PRIORITY_ORDER[item["priority"]])

        for item in found:
            connection.execute(
                """
                INSERT INTO recommendations
                    (run_id, area, priority, title, detail, evidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, item["area"], item["priority"], item["title"],
                 item["detail"],
                 json.dumps(item["evidence"], ensure_ascii=False)),
            )

        db.log_event(connection, "optimization_analysis", AGENT, None,
                     {"run_id": run_id, "recommendations": len(found)})
        connection.commit()

    log.info("Analysis %s: %d recommendation(s)", run_id, len(found))

    for item in found:
        print(f"{item['priority']:6} {item['area']:10} {item['title']}")

    print(f"\n{run_id}")
    return 0


def command_report(args):
    with db.session() as connection:
        db.migrate(connection)

        run_id = args.run_id or (connection.execute(
            "SELECT run_id FROM recommendations ORDER BY id DESC LIMIT 1"
        ).fetchone() or {"run_id": None})["run_id"]

        if not run_id:
            log.error("No analysis yet. Run: optimization analyse")
            return 1

        rows = connection.execute(
            """
            SELECT area, priority, title, detail, evidence
            FROM recommendations WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()

    items = sorted((dict(row) for row in rows),
                   key=lambda item: PRIORITY_ORDER[item["priority"]])
    today = dt.date.today().isoformat()

    lines = [
        f"# Optimisation report {today}",
        "",
        f"Analysis `{run_id}`: {len(items)} recommendation(s), "
        "highest priority first.",
        "",
        "Advice only. Nothing here was applied: the engine does not "
        "change its own policy, budget or publishing rules.",
        "",
    ]

    current_area = None

    for item in items:
        if item["area"] != current_area:
            current_area = item["area"]
            lines += ["", f"## {current_area}", ""]

        evidence = json.loads(item["evidence"])
        measured = ", ".join(f"{key}={value}"
                             for key, value in sorted(evidence.items()))

        lines.append(f"### {item['title']} ({item['priority']})")
        lines.append("")
        lines.append(item["detail"])

        if measured:
            lines.append("")
            lines.append(f"Evidence: {measured}")

        lines.append("")

    if not items:
        lines.append("Nothing to recommend: no agent reported a problem.")

    report = "\n".join(lines) + "\n"

    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.REPORTS_DIR / f"optimisation-{today}.md"
    target.write_text(report, encoding="utf-8")

    print(report)
    log.info("Report written: %s", target)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine Optimization Agent (advisory)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("analyse", help="derive recommendations")

    report = commands.add_parser("report", help="write a markdown report")
    report.add_argument("--run-id", default=None)

    return parser


HANDLERS = {
    "analyse": command_analyse,
    "report": command_report,
}


def main(argv=None):
    args = build_parser().parse_args(argv)

    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
