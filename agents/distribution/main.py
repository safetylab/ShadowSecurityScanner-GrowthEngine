"""Distribution Agent.

Publishes an approved draft to a channel. For a channel the project owns
— the site and its RSS feed — publishing means moving the draft into
content/published and adding it to the feed. For an external channel set
to 'automatic' in channels.yml it means one request to that platform
through sge.publishers.

Two rules hold whatever the configuration says. A draft is posted only
after it is approved and passes the quality gate, because the engine
cannot check a claim about a CVE and a person can. And one call posts to
one place: cross-posting the same text to several communities at once is
the shape of spam, so extra targets are reported, not posted.

The daily cap from policies.yml counts every channel together.

Usage:
    python agents/distribution/main.py queue
    python agents/distribution/main.py approve --slug SLUG
    python agents/distribution/main.py publish --slug SLUG [--dry-run]
                                               [--target SUBREDDIT]
    python agents/distribution/main.py rss
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import (config, db, feedgen, paths,  # noqa: E402
                 publishers, quality)
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "distribution"
OWNED_CHANNELS = ("website", "rss", "github_releases", "github")

FEED_ITEMS = 20
EXCERPT_WORDS = 60

# X counts characters, not words; LinkedIn's limit is 3000 and the last
# hundred are left for the link.
X_LIMIT = 280
LINKEDIN_LIMIT = 2900

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


def _external_text(channel, title, text, link):
    """What goes to a platform that is not ours.

    Reddit takes the draft as written. X has 280 characters, so it gets
    the title and the link only: half a sentence about a vulnerability
    is worse than no sentence. LinkedIn takes the body, trimmed at a
    word boundary.
    """
    body = _front_matter_stripped(text)

    if channel == "x":
        room = X_LIMIT - len(link) - 1

        if len(title) > room:
            title = title[:max(room - 1, 0)].rstrip() + "…"

        return f"{title} {link}".strip()

    if channel == "linkedin" and len(body) > LINKEDIN_LIMIT:
        cut = body[:LINKEDIN_LIMIT].rsplit(" ", 1)[0]
        body = cut.rstrip() + "…"

    return f"{body}\n\n{link}".strip()


def _post_externally(channel, channels, row, text, link, target):
    """One post to one place. Returns the publication URL."""
    settings = channels["channels"].get(channel, {})
    configured = settings.get("targets") or []
    publisher = publishers.publisher_for(channel, os.environ)

    if publisher.needs_target:
        chosen = target or (configured[0] if configured else None)

        if not chosen:
            raise publishers.PublishError(
                f"{channel}: no target configured and none given"
            )

        if len(configured) > 1:
            log.info(
                "Posting to %s only; %s are configured and are not "
                "cross-posted in one run",
                chosen, ", ".join(configured),
            )
    else:
        chosen = None

    body = _external_text(channel, row["title"], text, link)
    result = publisher.submit(chosen, row["title"], body)

    log.info("Posted to %s: %s", channel, result["url"])
    return result["url"]


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
    channels = config.load_channels()

    if not rows:
        print("Nothing in the queue. Build a draft first:")
        print("  python agents/content/main.py draft --topic advisories")
        return 0

    print(f"Published today: {today} of {limit} allowed\n")

    for row in rows:
        channel = row["channel"]
        mode = config.channel_mode(channel, channels)

        if channel in OWNED_CHANNELS:
            where = "owned"
        elif mode == "automatic":
            where = "external, posted from here"
        else:
            where = f"external, {mode}: a person posts"
        state = (f"published {row['published_at'][:10]}"
                 if row["published_at"] else row["status"])
        gate = "gate ok" if row["quality_passed"] else "GATE FAILED"

        print(f"{row['slug']}")
        print(f"  {row['kind']} → {channel} ({where}) | "
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

        channel = row["channel"]
        channels = config.load_channels()
        mode = config.channel_mode(channel, channels)
        owned = channel in OWNED_CHANNELS

        if not owned and mode != "automatic":
            log.error(
                "Channel %s is '%s': it is posted by a person. Switch it "
                "to 'automatic' in channels.yml to post it from here.",
                channel, mode,
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

        source = paths.inside_root(row["path"])

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

        link = f"{brand['website'].rstrip('/')}/{args.slug}"

        if args.dry_run:
            log.info("Would publish %s to %s (%d words)", args.slug,
                     channel, report["stats"]["words"])

            if not owned:
                publisher = publishers.publisher_for(channel, os.environ)
                missing = publisher.missing()
                log.info(
                    "Credentials: %s",
                    "missing " + ", ".join(missing) if missing else "present",
                )
                log.info("Would post: %s",
                         _external_text(channel, row["title"], text, link))

            return 0

        target = None

        if owned:
            paths.PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
            target = paths.PUBLISHED_DIR / source.name
            shutil.copy2(source, target)
            url = link
        else:
            # A platform refusal is not a crash: the draft keeps its
            # approved state and the run reports why nothing was posted.
            try:
                url = _post_externally(channel, channels, row, text, link,
                                       args.target)
            except publishers.PublishError as error:
                log.error("Not posted: %s", error)
                return 1

        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        connection.execute(
            """
            UPDATE content_items
            SET status = 'published', path = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE slug = ?
            """,
            (str(target.relative_to(paths.ROOT)) if target else row["path"],
             args.slug),
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
            (args.slug, channel, url, now),
        )
        db.log_event(connection, "content_published", AGENT, args.slug,
                     {"channel": channel, "url": url})
        connection.commit()

    log.info("Published to %s: %s", channel, target or url)
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
        source = paths.inside_root(row["path"])
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

    publish = commands.add_parser(
        "publish", help="publish an approved draft to its channel")
    publish.add_argument("--slug", required=True)
    publish.add_argument("--dry-run", action="store_true")
    publish.add_argument(
        "--target",
        help="where to post on platforms that have places of their own "
             "(a subreddit); defaults to the first in channels.yml",
    )

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
