"""Market Research Agent.

Collects public RSS/Atom sources, deduplicates by canonical URL, scores
relevance and records what every source run did. A failing source is
logged and skipped; it never stops the others.

Usage:
    python agents/research/main.py collect [--dry-run] [--source NAME]
    python agents/research/main.py list [--limit 10] [--min-relevance 30]
    python agents/research/main.py report [--days 1]
    python agents/research/main.py sources
"""

import argparse
import datetime as dt
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import config, db, feeds, net, paths, relevance, urls  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "research"

log = get_logger("sge.research")


def environment():
    """Where we are running: sources may be reachable from one and not
    the other. CISA answers HTTP 403 to Actions runners."""
    if os.environ.get("SGE_ENV"):
        return os.environ["SGE_ENV"]

    return "ci" if os.environ.get("GITHUB_ACTIONS") == "true" else "local"


def _limits():
    data = config.load_sources()
    limits = data.get("limits") or {}

    return {
        "timeout": int(limits.get("timeout_seconds", net.DEFAULT_TIMEOUT)),
        "max_bytes": int(
            limits.get("max_response_bytes", net.DEFAULT_MAX_BYTES)
        ),
        "max_items": int(limits.get("max_items_per_feed", feeds.MAX_ITEMS)),
    }


def fetch_source(source, limits):
    """Return parsed entries for one source. Raises on failure."""
    chunks = net.iter_chunks(
        source["url"],
        timeout=limits["timeout"],
        max_bytes=limits["max_bytes"],
    )

    return feeds.parse_stream(chunks, max_items=limits["max_items"])


def _prepare(entries, source_name, keywords, floor=0):
    """Normalise entries and drop those we cannot identify or use."""
    prepared = []
    seen = set()

    for entry in entries:
        try:
            canonical = urls.canonical_url(entry["link"])
        except urls.InvalidUrl as error:
            log.warning("%s: skipping entry, %s", source_name, error)
            continue

        # Feeds do repeat entries within a single document.
        if canonical in seen:
            continue

        seen.add(canonical)
        verdict = relevance.evaluate(entry, keywords, floor)

        prepared.append({
            "source": source_name,
            "title": entry["title"],
            "link": entry["link"],
            "canonical_link": canonical,
            "published": entry["published"] or None,
            "summary": entry["summary"] or None,
            "topic": verdict["topic"],
            "relevance": verdict["relevance"],
            "excluded": int(verdict["excluded"]),
        })

    return prepared


def _store(connection, items):
    """Insert items, ignoring ones already known by canonical URL."""
    new = 0

    for item in items:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO research_items
                (source, title, link, canonical_link, published, summary,
                 topic, relevance, excluded)
            VALUES (:source, :title, :link, :canonical_link, :published,
                    :summary, :topic, :relevance, :excluded)
            """,
            item,
        )
        new += cursor.rowcount

    return new


def _record_run(connection, run_id, source, status, seen, new, started,
                error=None):
    connection.execute(
        """
        INSERT INTO source_runs
            (run_id, source, status, items_seen, items_new, duration_ms,
             error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, source, status, seen, new,
         int((time.monotonic() - started) * 1000),
         error[:500] if error else None),
    )


def command_collect(args):
    sources = config.load_sources()["sources"]
    limits = _limits()
    keywords = config.load_keywords()
    env = environment()
    run_id = uuid.uuid4().hex[:12]

    if args.source:
        sources = [s for s in sources if s["name"] == args.source]

        if not sources:
            log.error("Unknown source: %s", args.source)
            return 1

    total_new = 0
    failures = 0
    attempted = 0

    log.info("Run %s in environment '%s'", run_id, env)

    with db.session() as connection:
        db.migrate(connection)

        for source in sources:
            name = source["name"]
            started = time.monotonic()
            allowed = source.get("environments")

            if not source.get("enabled"):
                log.info("%s: disabled, skipping", name)
                _record_run(connection, run_id, name, "skipped", 0, 0,
                            started, "disabled")
                connection.commit()
                continue

            # A source that is known to be unreachable from here is
            # skipped, not failed: a 403 we already expect is not news.
            if allowed and env not in allowed:
                log.info("%s: not available in environment '%s', skipping",
                         name, env)
                _record_run(connection, run_id, name, "skipped", 0, 0,
                            started, f"not available in '{env}'")
                connection.commit()
                continue

            attempted += 1

            try:
                entries = fetch_source(source, limits)
            except (net.FetchError, feeds.FeedError) as error:
                failures += 1
                log.warning("%s: failed, %s", name, error)
                _record_run(connection, run_id, name, "failed", 0, 0,
                            started, str(error))
                db.log_event(connection, "source_failed", AGENT, None,
                             {"source": name, "error": str(error)[:200]})
                connection.commit()
                continue
            except Exception as error:  # defensive: one source cannot
                failures += 1       # be allowed to abort the whole run
                log.exception("%s: unexpected failure", name)
                _record_run(connection, run_id, name, "failed", 0, 0,
                            started, repr(error))
                connection.commit()
                continue

            items = _prepare(entries, name, keywords,
                             int(source.get("relevance_floor") or 0))

            if args.dry_run:
                log.info("%s: %d entries parsed (dry run, nothing saved)",
                         name, len(items))
                _record_run(connection, run_id, name, "ok", len(items), 0,
                            started)
                connection.commit()
                continue

            new = _store(connection, items)
            total_new += new
            _record_run(connection, run_id, name, "ok", len(items), new,
                        started)
            db.log_event(connection, "source_collected", AGENT, None,
                         {"source": name, "seen": len(items), "new": new})
            connection.commit()

            log.info("%s: %d entries, %d new", name, len(items), new)

    log.info("Run %s finished: %d new items, %d of %d sources failed",
             run_id, total_new, failures, attempted)

    # A partial run is still a successful run; only a total failure is an
    # error, so scheduled jobs do not go red because one feed moved.
    return 1 if attempted and failures == attempted else 0


def command_list(args):
    # Advisory feeds reuse headlines ("CISA Adds Two Known Exploited
    # Vulnerabilities to Catalog") across different URLs, so the default
    # view collapses them and --all shows every row.
    grouping = "" if args.all else " GROUP BY title"
    visibility = "" if args.include_excluded else " AND excluded = 0"

    with db.session() as connection:
        db.migrate(connection)
        rows = connection.execute(
            f"""
            SELECT source, title, link, published, topic, relevance,
                   excluded,
                   -- A correlated subquery, because a bare COUNT(*)
                   -- without GROUP BY collapses the whole table into
                   -- one row when --all is used.
                   (SELECT COUNT(*) FROM research_items AS same
                    WHERE same.title = research_items.title) AS variants
            FROM research_items
            WHERE relevance >= ?{visibility}{grouping}
            ORDER BY relevance DESC, id DESC
            LIMIT ?
            """,
            (args.min_relevance, args.limit),
        ).fetchall()

    if not rows:
        print("No research items found.")
        return 0

    for row in rows:
        flags = []
        if row["excluded"]:
            flags.append("excluded")
        if not args.all and row["variants"] > 1:
            flags.append(f"+{row['variants'] - 1} same-title")

        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"\n[{row['source']}] {row['title']}{suffix}")
        print(f"  topic={row['topic']} relevance={row['relevance']} "
              f"published={row['published'] or 'unknown'}")
        print(f"  {row['link']}")

    return 0


def command_report(args):
    since = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    ).strftime("%Y-%m-%d %H:%M:%S")

    with db.session() as connection:
        db.migrate(connection)

        # The report is about one run. Earlier versions counted every
        # row fetched inside a time window, which reported "100 new"
        # after a run that added nothing.
        latest = connection.execute(
            "SELECT run_id FROM source_runs WHERE run_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        run_id = args.run_id or (latest["run_id"] if latest else None)

        runs = connection.execute(
            """
            SELECT source, status, items_seen, items_new, duration_ms, error
            FROM source_runs
            WHERE run_id IS ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()

        added = sum(run["items_new"] for run in runs)

        items = connection.execute(
            """
            SELECT source, title, link, topic, relevance, excluded
            FROM research_items
            WHERE fetched_at >= ?
            GROUP BY title
            ORDER BY relevance DESC, id DESC
            """,
            (since,),
        ).fetchall()

        total = connection.execute(
            "SELECT COUNT(*) AS n FROM research_items"
        ).fetchone()["n"]

    today = dt.date.today().isoformat()
    publishable = [item for item in items if not item["excluded"]]

    lines = [
        f"# Research report {today}",
        "",
        f"Run `{run_id or 'none'}` added **{added}** items.",
        f"In the database: {total} items, "
        f"{len(items)} fetched in the last {args.days} day(s) "
        f"({len(publishable)} of them publishable).",
        "",
        "## This run, by source",
        "",
    ]

    for run in runs:
        detail = f" — {run['error']}" if run["error"] else ""
        lines.append(
            f"- {run['source']}: {run['status']}, seen "
            f"{run['items_seen']}, new {run['items_new']}, "
            f"{run['duration_ms']} ms{detail}"
        )

    if not runs:
        lines.append("- no collection run recorded yet")

    lines += ["", f"## Fetched in the last {args.days} day(s), by relevance",
              ""]

    for item in items:
        mark = " _(excluded from digests)_" if item["excluded"] else ""
        lines.append(
            f"- **{item['title']}** "
            f"({item['topic']}, relevance {item['relevance']}) — "
            f"<{item['link']}> — source: {item['source']}{mark}"
        )

    if not items:
        lines.append("- nothing fetched in this window")

    report = "\n".join(lines) + "\n"

    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.REPORTS_DIR / f"research-{today}.md"
    target.write_text(report, encoding="utf-8")

    print(report)
    log.info("Report written: %s", target)
    return 0


def command_sources(_args):
    env = environment()
    print(f"Current environment: {env}\n")

    for source in config.load_sources()["sources"]:
        allowed = source.get("environments")

        if not source.get("enabled"):
            state = "disabled"
        elif allowed and env not in allowed:
            state = "unavailable"
        else:
            state = "enabled"

        scope = f" (only {', '.join(allowed)})" if allowed else ""
        floor = source.get("relevance_floor")
        baseline = f" floor={floor}" if floor else ""
        print(f"{state:12} {source['name']}{scope}{baseline}")
        print(f"{'':12} {source['url']}")

    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine Market Research Agent"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="fetch all enabled sources")
    collect.add_argument("--dry-run", action="store_true",
                         help="parse and score without writing to the database")
    collect.add_argument("--source", default=None, help="limit to one source")

    listing = commands.add_parser("list", help="show stored items")
    listing.add_argument("--limit", type=int, default=10)
    listing.add_argument("--min-relevance", type=int, default=0)
    listing.add_argument("--all", action="store_true",
                         help="do not collapse items sharing a title")
    listing.add_argument("--include-excluded", action="store_true",
                         help="also show items excluded from digests")

    report = commands.add_parser("report", help="write a markdown report")
    report.add_argument("--days", type=int, default=1)
    report.add_argument("--run-id", default=None,
                        help="report on a specific run (default: latest)")

    commands.add_parser("sources", help="list configured sources")

    return parser


HANDLERS = {
    "collect": command_collect,
    "list": command_list,
    "report": command_report,
    "sources": command_sources,
}


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "list":
        args.limit = max(1, min(args.limit, 200))

    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
