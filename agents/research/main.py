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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import config, db, feeds, net, paths, relevance, urls  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "research"

log = get_logger("sge.research")


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


def _prepare(entries, source_name, keywords):
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
        verdict = relevance.evaluate(entry, keywords)

        prepared.append({
            "source": source_name,
            "title": entry["title"],
            "link": entry["link"],
            "canonical_link": canonical,
            "published": entry["published"] or None,
            "summary": entry["summary"] or None,
            "topic": verdict["topic"],
            "relevance": verdict["relevance"],
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
                 topic, relevance)
            VALUES (:source, :title, :link, :canonical_link, :published,
                    :summary, :topic, :relevance)
            """,
            item,
        )
        new += cursor.rowcount

    return new


def _record_run(connection, source, status, seen, new, started, error=None):
    connection.execute(
        """
        INSERT INTO source_runs
            (source, status, items_seen, items_new, duration_ms, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source, status, seen, new,
         int((time.monotonic() - started) * 1000),
         error[:500] if error else None),
    )


def command_collect(args):
    sources = config.load_sources()["sources"]
    limits = _limits()
    keywords = config.load_keywords()

    if args.source:
        sources = [s for s in sources if s["name"] == args.source]

        if not sources:
            log.error("Unknown source: %s", args.source)
            return 1

    total_new = 0
    failures = 0

    with db.session() as connection:
        db.migrate(connection)

        for source in sources:
            name = source["name"]
            started = time.monotonic()

            if not source.get("enabled"):
                log.info("%s: disabled, skipping", name)
                _record_run(connection, name, "skipped", 0, 0, started)
                connection.commit()
                continue

            try:
                entries = fetch_source(source, limits)
            except (net.FetchError, feeds.FeedError) as error:
                failures += 1
                log.warning("%s: failed, %s", name, error)
                _record_run(connection, name, "failed", 0, 0, started,
                            str(error))
                db.log_event(connection, "source_failed", AGENT, None,
                             {"source": name, "error": str(error)[:200]})
                connection.commit()
                continue
            except Exception as error:  # defensive: one source cannot
                failures += 1       # be allowed to abort the whole run
                log.exception("%s: unexpected failure", name)
                _record_run(connection, name, "failed", 0, 0, started,
                            repr(error))
                connection.commit()
                continue

            items = _prepare(entries, name, keywords)

            if args.dry_run:
                log.info("%s: %d entries parsed (dry run, nothing saved)",
                         name, len(items))
                _record_run(connection, name, "ok", len(items), 0, started)
                connection.commit()
                continue

            new = _store(connection, items)
            total_new += new
            _record_run(connection, name, "ok", len(items), new, started)
            db.log_event(connection, "source_collected", AGENT, None,
                         {"source": name, "seen": len(items), "new": new})
            connection.commit()

            log.info("%s: %d entries, %d new", name, len(items), new)

    log.info("Collection finished: %d new items, %d source failures",
             total_new, failures)

    # A partial run is still a successful run; only a total failure is an
    # error, so scheduled jobs do not go red because one feed moved.
    return 1 if failures and failures == len(sources) else 0


def command_list(args):
    with db.session() as connection:
        db.migrate(connection)
        rows = connection.execute(
            """
            SELECT source, title, link, published, topic, relevance
            FROM research_items
            WHERE relevance >= ?
            ORDER BY relevance DESC, id DESC
            LIMIT ?
            """,
            (args.min_relevance, args.limit),
        ).fetchall()

    if not rows:
        print("No research items found.")
        return 0

    for row in rows:
        print(f"\n[{row['source']}] {row['title']}")
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

        items = connection.execute(
            """
            SELECT source, title, link, topic, relevance
            FROM research_items
            WHERE fetched_at >= ?
            ORDER BY relevance DESC, id DESC
            """,
            (since,),
        ).fetchall()

        runs = connection.execute(
            """
            SELECT source, status, items_seen, items_new, error
            FROM source_runs
            WHERE started_at >= ?
            ORDER BY started_at DESC
            """,
            (since,),
        ).fetchall()

    today = dt.date.today().isoformat()
    lines = [
        f"# Research report {today}",
        "",
        f"Window: last {args.days} day(s). New items: {len(items)}.",
        "",
        "## Source runs",
        "",
    ]

    for run in runs:
        detail = f" — {run['error']}" if run["error"] else ""
        lines.append(
            f"- {run['source']}: {run['status']}, seen {run['items_seen']}, "
            f"new {run['items_new']}{detail}"
        )

    if not runs:
        lines.append("- no runs in this window")

    lines += ["", "## New items by relevance", ""]

    for item in items:
        lines.append(
            f"- **{item['title']}** "
            f"({item['topic']}, relevance {item['relevance']}) — "
            f"<{item['link']}> — source: {item['source']}"
        )

    if not items:
        lines.append("- no new items")

    report = "\n".join(lines) + "\n"

    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.REPORTS_DIR / f"research-{today}.md"
    target.write_text(report, encoding="utf-8")

    print(report)
    log.info("Report written: %s", target)
    return 0


def command_sources(_args):
    for source in config.load_sources()["sources"]:
        state = "enabled" if source.get("enabled") else "disabled"
        print(f"{state:9} {source['name']}  {source['url']}")

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

    report = commands.add_parser("report", help="write a markdown report")
    report.add_argument("--days", type=int, default=1)

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
