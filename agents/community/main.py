"""Community Radar Agent.

Finds public threads where the project is genuinely useful and drafts a
reply for a human to read, edit and post.

There is no posting code in this agent, by design. It does not comment,
vote, follow, or send anyone a message, and no configuration setting can
turn that on.

Usage:
    python agents/community/main.py scan [--platform hackernews]
    python agents/community/main.py list [--advice reply]
    python agents/community/main.py draft --id N
    python agents/community/main.py review --id N
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import (community, config, db, github, hackernews,  # noqa: E402
                 paths, text as text_util, urls)
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "community"
PLATFORMS = ("hackernews", "github")

log = get_logger("sge.community")


def _store(connection, thread, verdict, query):
    try:
        canonical = urls.canonical_url(thread["url"])
    except urls.InvalidUrl:
        return 0

    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO community_signals
            (platform, external_id, url, canonical_url, title, author,
             excerpt, created_at_source, points, comments, relevance,
             intent, advice, reasons, query)
        VALUES (:platform, :external_id, :url, :canonical_url, :title,
                :author, :excerpt, :created_at_source, :points, :comments,
                :relevance, :intent, :advice, :reasons, :query)
        """,
        {
            **thread,
            "canonical_url": canonical,
            "relevance": verdict["relevance"],
            "intent": verdict["intent"],
            "advice": verdict["advice"],
            "reasons": json.dumps(verdict["reasons"], ensure_ascii=False),
            "query": query,
        },
    )

    return cursor.rowcount


def _scan_hackernews(connection, settings, queries, dry_run):
    found = 0
    new = 0

    max_age = settings["rules"].get("max_age_days", 120)

    for query in queries:
        try:
            hits = hackernews.search(query, limit=20, since_days=max_age)
        except hackernews.HackerNewsError as error:
            log.warning("Hacker News query %r failed: %s", query, error)
            continue

        for hit in hits:
            verdict = community.evaluate_thread(hit, settings)
            found += 1

            if verdict["advice"] == "skip":
                continue

            if not dry_run:
                new += _store(connection, hit, verdict, query)

        log.info("Hacker News %r: %d hit(s)", query, len(hits))

    return found, new


def _github_thread(item):
    return {
        "platform": "github",
        "external_id": str(item.get("id", "")),
        "url": item.get("html_url", ""),
        "title": (item.get("title") or "(no title)")[:500],
        "author": (item.get("user") or {}).get("login"),
        "excerpt": (item.get("body") or "")[:1000],
        "created_at_source": item.get("created_at"),
        "points": text_util.as_int(
            (item.get("reactions") or {}).get("total_count")),
        "comments": text_util.as_int(item.get("comments")),
    }


def _scan_github(connection, settings, queries, dry_run):
    found = 0
    new = 0

    for query in queries:
        try:
            items = github.search_issues(query, limit=20)
        except github.GitHubError as error:
            log.warning("GitHub query %r failed: %s", query, error)
            continue

        for item in items:
            thread = _github_thread(item)

            if not thread["url"]:
                continue

            verdict = community.evaluate_thread(thread, settings)
            found += 1

            if verdict["advice"] == "skip":
                continue

            if not dry_run:
                new += _store(connection, thread, verdict, query)

        log.info("GitHub %r: %d result(s)", query, len(items))

    return found, new


def command_scan(args):
    settings = community.load_rules()
    platforms = ([args.platform] if args.platform else list(PLATFORMS))

    total_found = 0
    total_new = 0

    with db.session() as connection:
        db.migrate(connection)

        for platform in platforms:
            queries = settings["queries"].get(platform) or []

            if not queries:
                log.info("%s: no queries configured", platform)
                continue

            if platform == "hackernews":
                found, new = _scan_hackernews(connection, settings, queries,
                                              args.dry_run)
            else:
                found, new = _scan_github(connection, settings, queries,
                                          args.dry_run)

            total_found += found
            total_new += new
            connection.commit()

        if not args.dry_run:
            db.log_event(connection, "community_scan", AGENT, None,
                         {"examined": total_found, "new": total_new})
            connection.commit()

    log.info("Scan finished: %d thread(s) examined, %d worth a look%s",
             total_found, total_new,
             " (dry run, nothing saved)" if args.dry_run else "")
    return 0


def command_list(args):
    query = """
        SELECT id, platform, title, url, relevance, intent, advice,
               reasons, comments, created_at_source, reviewed
        FROM community_signals
    """
    params = []
    conditions = []

    if args.advice:
        conditions.append("advice = ?")
        params.append(args.advice)

    if not args.include_reviewed:
        conditions.append("reviewed = 0")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY relevance DESC, id DESC LIMIT ?"
    params.append(args.limit)

    with db.session() as connection:
        db.migrate(connection)
        rows = connection.execute(query, params).fetchall()

    if not rows:
        print("Nothing on the radar. Run: community scan")
        return 0

    for row in rows:
        reasons = ", ".join(json.loads(row["reasons"]))
        age = community.age_days(row["created_at_source"])
        print(f"\n[{row['id']}] {row['advice'].upper()} "
              f"relevance={row['relevance']} {row['platform']}")
        print(f"  {row['title']}")
        print(f"  {row['url']}")
        print(f"  intent={row['intent']} comments={row['comments']} "
              f"age={age if age is not None else '?'}d — {reasons}")

    return 0


def command_draft(args):
    brand = config.load_brand()["project"]

    with db.session() as connection:
        db.migrate(connection)
        row = connection.execute(
            "SELECT * FROM community_signals WHERE id = ?", (args.id,)
        ).fetchone()

        if not row:
            log.error("Unknown signal: %s", args.id)
            return 1

        if row["advice"] == "skip":
            log.error(
                "This thread was judged not worth replying to: %s",
                ", ".join(json.loads(row["reasons"])),
            )
            return 1

        reasons = json.loads(row["reasons"])
        age = community.age_days(row["created_at_source"])

        body = f"""---
platform: {row['platform']}
thread: {row['url']}
title: "{row['title'].replace('"', "'")}"
intent: {row['intent']}
relevance: {row['relevance']}
mode: draft_only
requires_human_review: true
generated_at: {dt.date.today().isoformat()}
---

# Draft reply — not posted

**Thread:** [{row['title']}]({row['url']})
**Platform:** {row['platform']} · **Age:** {age if age is not None else '?'} days · **Comments:** {row['comments']}
**Why it surfaced:** {', '.join(reasons)}

## Read first

Open the thread and read it before posting anything. This draft was
written from a title and an excerpt, not from the conversation. If
someone has already given the same answer, say nothing.

## Suggested reply

> I maintain {brand['name']}, an open-source scanner, so take this as a
> biased source.
>
> For what you are describing it covers: service and OS fingerprinting,
> prioritisation using EPSS and the CISA KEV catalogue, scan diffing,
> and SARIF or PDF output. It runs as a desktop application, with no
> cloud component.
>
> It is free and the source is at {brand['github']} — docs at
> {brand['website']}. Whether it fits depends on what you need that
> other scanners do not give you; happy to answer specifics.

## Before you post

- [ ] the community's rules allow a link like this
- [ ] the affiliation disclosure above is still in the text
- [ ] the reply answers the actual question, not a nearby one
- [ ] no claim here goes beyond what the tool does today
- [ ] no comparison with another tool without evidence
- [ ] nobody else already said this

Posting is manual. This agent has no code that can post.
"""

        target_dir = paths.DRAFTS_DIR / "community"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{row['platform']}-{row['external_id']}.md"
        target.write_text(body, encoding="utf-8")

        db.log_event(connection, "community_reply_drafted", AGENT, None,
                     {"signal": row["id"], "platform": row["platform"]})
        connection.commit()

    log.info("Draft written for review: %s", target)
    print(target)
    return 0


def command_review(args):
    with db.session() as connection:
        db.migrate(connection)
        cursor = connection.execute(
            "UPDATE community_signals SET reviewed = 1 WHERE id = ?",
            (args.id,),
        )

        if cursor.rowcount == 0:
            log.error("Unknown signal: %s", args.id)
            return 1

        connection.commit()

    log.info("Marked as reviewed: %s", args.id)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine Community Radar (never posts)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="search public threads")
    scan.add_argument("--platform", choices=PLATFORMS, default=None)
    scan.add_argument("--dry-run", action="store_true")

    listing = commands.add_parser("list", help="show what was found")
    listing.add_argument("--advice", choices=("reply", "consider", "skip"),
                         default=None)
    listing.add_argument("--limit", type=int, default=15)
    listing.add_argument("--include-reviewed", action="store_true")

    draft = commands.add_parser("draft", help="draft a reply for review")
    draft.add_argument("--id", type=int, required=True)

    review = commands.add_parser("review", help="mark a signal as handled")
    review.add_argument("--id", type=int, required=True)

    return parser


HANDLERS = {
    "scan": command_scan,
    "list": command_list,
    "draft": command_draft,
    "review": command_review,
}


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "list":
        args.limit = max(1, min(args.limit, 100))

    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
