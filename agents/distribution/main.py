"""Distribution Agent.

Publishes to channels the project owns — the site and its RSS feed — and
nowhere else. External communities stay draft-only: for those the agent
reports what is waiting and leaves posting to a human.

Publishing here means moving an approved draft into content/published
and adding it to the feed. The daily cap from policies.yml applies.

Usage:
    python agents/distribution/main.py queue
    python agents/distribution/main.py approve --slug SLUG
    python agents/distribution/main.py publish --slug SLUG [--dry-run]
    python agents/distribution/main.py rss
"""

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import config, db, feedgen, paths, quality  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "distribution"
OWNED_CHANNELS = ("website", "rss", "github_releases", "github")

FEED_ITEMS = 20
EXCERPT_WORDS = 60

log = get_logger("sge.distribution")


def _front_matter_stripped(text):
    """Body of a draft without its YAML front matter."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()

    return text.strip()


def _excerpt(text, words=EXCERPT_WORDS):
    body = _front_matter_stripped(text)
    body = re.sub(r"^#.*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
    # List markers and emphasis go; hyphens inside words stay, or dates
    # come out as "2026 11 04".
    body = re.sub(r"^\s*[-*>]\s+", " ", body, flags=re.MULTILINE)
    body = re.sub(r"[`*_>#]", " ", body)
    body = re.sub(r"\s+", " ", body).strip()

    parts = body.split()

    if len(parts) <= words:
        return body

    return " ".join(parts[:words]) + "…"


def _published_today(connection):
    today = dt.date.today().isoformat()

    return connection.execute(
        """
        SELECT COUNT(*) AS n FROM publications
        WHERE published_at IS NOT NULL AND substr(published_at, 1, 10) = ?
        """,
        (today,),
    ).fetchone()["n"]


def command_queue(_args):
    with db.session() as connection:
        db.migrate(connection)
        rows = connection.execute(
            """
            SELECT c.slug, c.kind, c.channel, c.status, c.quality_passed,
                   p.published_at
            FROM content_items AS c
            LEFT JOIN publications AS p
              ON p.slug = c.slug AND p.channel = c.channel
            ORDER BY c.id DESC
            """
        ).fetchall()
        today = _published_today(connection)

    limit = config.load_policies()["automation"]["max_posts_per_day"]

    if not rows:
        print("Nothing in the queue. Build a draft first:")
        print("  python agents/content/main.py draft --topic advisories")
        return 0

    print(f"Published today: {today} of {limit} allowed\n")

    for row in rows:
        owned = row["channel"] in OWNED_CHANNELS
        where = "owned" if owned else "draft-only, human posts"
        state = (f"published {row['published_at'][:10]}"
                 if row["published_at"] else row["status"])
        gate = "gate ok" if row["quality_passed"] else "GATE FAILED"

        print(f"{row['slug']}")
        print(f"  {row['kind']} → {row['channel']} ({where}) | "
              f"{state} | {gate}")

    return 0


def command_approve(args):
    """Mark a reviewed draft as ready. A human runs this, not a schedule."""
    with db.session() as connection:
        db.migrate(connection)
        row = connection.execute(
            "SELECT status, quality_passed FROM content_items "
            "WHERE slug = ?", (args.slug,)
        ).fetchone()

        if not row:
            log.error("Unknown draft: %s", args.slug)
            return 1

        if not row["quality_passed"]:
            log.error("Draft did not pass the quality gate; fix it first")
            return 1

        connection.execute(
            "UPDATE content_items SET status = 'approved', "
            "updated_at = CURRENT_TIMESTAMP WHERE slug = ?",
            (args.slug,),
        )
        db.log_event(connection, "content_approved", AGENT, args.slug, {})
        connection.commit()

    log.info("Approved: %s", args.slug)
    return 0


def command_publish(args):
    brand = config.load_brand()["project"]
    policies = config.load_policies()
    limit = policies["automation"]["max_posts_per_day"]

    with db.session() as connection:
        db.migrate(connection)
        row = connection.execute(
            "SELECT * FROM content_items WHERE slug = ?", (args.slug,)
        ).fetchone()

        if not row:
            log.error("Unknown draft: %s", args.slug)
            return 1

        if row["channel"] not in OWNED_CHANNELS:
            log.error(
                "Channel %s is not ours to publish to. External "
                "communities are draft-only: a human posts them.",
                row["channel"],
            )
            return 1

        if row["status"] != "approved":
            log.error(
                "Draft is '%s', not 'approved'. Review it, then: "
                "distribution approve --slug %s",
                row["status"], args.slug,
            )
            return 1

        if not row["quality_passed"]:
            log.error("Draft did not pass the quality gate")
            return 1

        source = paths.ROOT / row["path"]

        if not source.is_file():
            log.error("Draft file is missing: %s", source)
            return 1

        text = source.read_text(encoding="utf-8")

        # The gate runs again at the last moment: the file may have been
        # edited by hand since it was generated.
        report = quality.check(text, sources=json.loads(row["sources"]),
                               cta_url=brand["github"])

        if not report["passed"]:
            for problem in report["problems"]:
                log.error("Quality gate: %s", problem)
            return 1

        already = _published_today(connection)

        if already >= limit:
            log.error("Daily limit reached: %d of %d published today",
                      already, limit)
            return 1

        if args.dry_run:
            log.info("Would publish %s to %s (%d words)", args.slug,
                     row["channel"], report["stats"]["words"])
            return 0

        paths.PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
        target = paths.PUBLISHED_DIR / source.name
        shutil.copy2(source, target)

        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        connection.execute(
            """
            UPDATE content_items
            SET status = 'published', path = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE slug = ?
            """,
            (str(target.relative_to(paths.ROOT)), args.slug),
        )
        connection.execute(
            """
            INSERT INTO publications (slug, channel, mode, url,
                                      published_at)
            VALUES (?, ?, 'automatic', ?, ?)
            ON CONFLICT(slug, channel) DO UPDATE SET
                published_at = excluded.published_at,
                url = excluded.url
            """,
            (args.slug, row["channel"],
             f"{brand['website'].rstrip('/')}/{args.slug}", now),
        )
        db.log_event(connection, "content_published", AGENT, args.slug,
                     {"channel": row["channel"]})
        connection.commit()

    log.info("Published to %s: %s", row["channel"], target)
    return 0


def command_rss(args):
    brand = config.load_brand()["project"]

    with db.session() as connection:
        db.migrate(connection)
        rows = connection.execute(
            """
            SELECT c.slug, c.title, c.path, p.published_at, p.url
            FROM content_items AS c
            JOIN publications AS p
              ON p.slug = c.slug AND p.channel = c.channel
            WHERE c.status = 'published' AND p.published_at IS NOT NULL
            ORDER BY p.published_at DESC
            LIMIT ?
            """,
            (FEED_ITEMS,),
        ).fetchall()

    items = []

    for row in rows:
        source = paths.ROOT / row["path"]
        text = source.read_text(encoding="utf-8") if source.is_file() else ""

        items.append({
            "title": row["title"],
            "link": row["url"] or brand["website"],
            "description": _excerpt(text),
            "date": row["published_at"],
            "guid": row["slug"],
        })

    feed = feedgen.build_feed(
        {
            "title": f"{brand['name']} — security digests",
            "link": brand["website"],
            "description": (
                f"Public advisories and tool releases worth verifying, "
                f"collected and written up for {brand['name']} users."
            ),
        },
        items,
    )

    target = Path(args.output) if args.output else (
        paths.PUBLISHED_DIR / "feed.xml"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(feed, encoding="utf-8")

    log.info("Feed written with %d item(s): %s", len(items), target)
    print(target)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine Distribution Agent"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("queue", help="show what is waiting")

    approve = commands.add_parser("approve",
                                  help="mark a reviewed draft as ready")
    approve.add_argument("--slug", required=True)

    publish = commands.add_parser("publish",
                                  help="publish to an owned channel")
    publish.add_argument("--slug", required=True)
    publish.add_argument("--dry-run", action="store_true")

    rss = commands.add_parser("rss", help="rebuild the RSS feed")
    rss.add_argument("--output", default=None)

    return parser


HANDLERS = {
    "queue": command_queue,
    "approve": command_approve,
    "publish": command_publish,
    "rss": command_rss,
}


def main(argv=None):
    args = build_parser().parse_args(argv)

    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
