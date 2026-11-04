"""Content Factory Agent.

Turns collected research into drafts that always carry links to their
primary sources, and refuses to save anything that fails the quality
gate. Drafts are written to content/drafts only: this agent never
publishes anywhere.

Usage:
    python agents/content/main.py draft --topic advisories [--limit 5]
    python agents/content/main.py social --channel reddit --slug SLUG
    python agents/content/main.py list
    python agents/content/main.py check --slug SLUG
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import (config, db, paths, quality, render,  # noqa: E402
                 text as text_util)
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "content"
MIN_RELEVANCE = 30

log = get_logger("sge.content")


def _slugify(text, limit=60):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].strip("-") or "draft"


def _today():
    return dt.date.today().isoformat()


def _pick_items(connection, topic, limit, min_relevance):
    query = """
        SELECT source, title, link, summary, topic, relevance, published
        FROM research_items
        WHERE relevance >= ? AND excluded = 0
          AND summary IS NOT NULL AND TRIM(summary) != ''
    """
    params = [min_relevance]

    if topic:
        query += " AND topic = ?"
        params.append(topic)

    # One item per title keeps near-identical advisory entries from
    # filling a digest with the same headline.
    query += """
        GROUP BY title
        ORDER BY relevance DESC, id DESC
        LIMIT ?
    """
    params.append(limit)

    return connection.execute(query, params).fetchall()


def _clean(value, limit=320):
    """Plain-text excerpt of a feed summary."""
    return text_util.plain(value, limit)


def _render_items(items):
    blocks = []

    for item in items:
        excerpt = _clean(item["summary"])
        detail = f"\n  {excerpt}" if excerpt else ""
        blocks.append(
            f"- [{item['title']}]({item['link']}) — {item['source']}"
            f"{detail}"
        )

    return "\n".join(blocks)


def _analysis(items, topic, project):
    """Factual framing only: counts and sources, no invented findings."""
    sources = sorted({item["source"] for item in items})
    origin = (
        f"{len(sources)} independent public sources"
        if len(sources) > 1 else "one public source"
    )

    subject = ("This item comes from" if len(items) == 1
               else f"These {len(items)} items come from")

    return (
        f"{subject} {origin} "
        f"({', '.join(sources)}). They are relevant to "
        f"anyone running {project['name']} because the topic — {topic} — "
        "maps directly to checks an operator can run against their own "
        "infrastructure. Read each advisory at its source before acting: "
        "the authoritative details, affected versions and mitigations are "
        "maintained there, not here."
    )


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or f"{singular}s")


def _intro(topic, count):
    noun = _plural(count, "item")

    return (
        f"A review of {count} public security {noun} collected on "
        f"{_today()} in the area of {topic}. Each entry links to its "
        "primary source; nothing here replaces reading that source. "
        "This digest exists to help operators decide what to verify in "
        "their own environment this week."
    )


def _cta(project):
    return (
        f"{project['name']} is an open-source security scanner you can "
        f"run against systems you are authorised to test: "
        f"{project['github']}\n\n"
        "Never scan infrastructure without written permission from its "
        f"owner. Documentation and quick start: {project['website']}"
    )


def command_draft(args):
    brand = config.load_brand()
    project = brand["project"]
    rules = config.load_publishing_rules()

    with db.session() as connection:
        db.migrate(connection)
        items = _pick_items(connection, args.topic, args.limit,
                            args.min_relevance)

        if not items:
            log.error(
                "No research items with relevance >= %d%s and a stored "
                "summary. Collected state carries deduplication keys "
                "without article text, so a digest can only use items "
                "fetched in a run: collect first, then draft.",
                args.min_relevance,
                f" for topic '{args.topic}'" if args.topic else "",
            )
            return 1

        topic = args.topic or "security operations"
        title = args.title or (
            f"Security digest {_today()}: {topic.replace('_', ' ')}"
        )
        slug = _slugify(f"{_today()}-{title}")

        template = (paths.TEMPLATES_DIR / "research-digest.md").read_text(
            encoding="utf-8"
        )

        text = render.render(template, {
            "title": title,
            "topic": topic,
            "generated_at": _today(),
            "intro": _intro(topic, len(items)),
            "items": _render_items(items),
            "analysis": _analysis(items, topic, project),
            "cta": _cta(project),
            "project_name": project["name"],
        })

        sources = [item["link"] for item in items]
        report = quality.check(text, sources=sources, rules=rules,
                               cta_url=project["github"])

        for problem in report["problems"]:
            log.error("Quality gate: %s", problem)

        if not report["passed"]:
            db.log_event(connection, "draft_rejected", AGENT, slug,
                         {"problems": report["problems"]})
            connection.commit()
            return 1

        paths.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        target = paths.DRAFTS_DIR / f"{slug}.md"
        target.write_text(text, encoding="utf-8")

        connection.execute(
            """
            INSERT INTO content_items
                (slug, kind, channel, title, path, status, quality_passed,
                 quality_report, sources)
            VALUES (?, 'digest', 'website', ?, ?, 'draft', 1, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                title = excluded.title,
                path = excluded.path,
                quality_report = excluded.quality_report,
                sources = excluded.sources,
                updated_at = CURRENT_TIMESTAMP
            """,
            (slug, title, str(target.relative_to(paths.ROOT)),
             json.dumps(report, ensure_ascii=False),
             json.dumps(sources, ensure_ascii=False)),
        )
        db.log_event(connection, "draft_created", AGENT, slug,
                     {"items": len(items), "words": report["stats"]["words"]})
        connection.commit()

    log.info("Draft saved: %s (%d words, %d sources)", target,
             report["stats"]["words"], len(sources))
    print(f"\n{target}")
    return 0


def command_social(args):
    brand = config.load_brand()
    project = brand["project"]
    channels = config.load_channels()
    mode = config.channel_mode(args.channel, channels)

    if mode is None:
        log.error("Channel is disabled or unknown: %s", args.channel)
        return 1

    if mode != "draft_only":
        log.error(
            "Channel %s is configured as '%s'; this agent only produces "
            "drafts for review.", args.channel, mode
        )
        return 1

    with db.session() as connection:
        db.migrate(connection)
        row = connection.execute(
            "SELECT slug, title, sources FROM content_items WHERE slug = ?",
            (args.slug,),
        ).fetchone()

        if not row:
            log.error("Unknown draft: %s", args.slug)
            return 1

        sources = json.loads(row["sources"])
        slug = f"{row['slug']}-{args.channel}"

        template = (paths.TEMPLATES_DIR / "social-post.md").read_text(
            encoding="utf-8"
        )

        body = (
            f"{row['title']}\n\n"
            "I collect public advisories and tool releases and check which "
            "of them are worth verifying in your own environment. The "
            "write-up links every primary source, so you can read the "
            "originals instead of taking my word for it.\n\n"
            f"The checks in it are the ones {project['name']}, an "
            "open-source scanner, automates — only ever against systems "
            "you are authorised to test."
        )
        link_line = (
            f"Sources: {sources[0]}\n"
            f"Project: {project['github']}"
        )

        text = render.render(template, {
            "title": row["title"],
            "channel": args.channel,
            "community": args.community or "choose a community that allows "
                                          "technical self-promotion",
            "generated_at": _today(),
            "body": body,
            "link_line": link_line,
        })

        # Social drafts are short by design: the gate still has to see a
        # source link and the project link, so only the word floor moves.
        rules = config.load_publishing_rules()
        rules["quality_gate"] = {**rules["quality_gate"], "min_words": 60}

        report = quality.check(text, sources=[sources[0]], rules=rules,
                               cta_url=project["github"])

        for problem in report["problems"]:
            log.error("Quality gate: %s", problem)

        if not report["passed"]:
            return 1

        social_dir = paths.CONTENT_DIR / "social"
        social_dir.mkdir(parents=True, exist_ok=True)
        target = social_dir / f"{slug}.md"
        target.write_text(text, encoding="utf-8")

        connection.execute(
            """
            INSERT INTO content_items
                (slug, kind, channel, title, path, status, quality_passed,
                 quality_report, sources)
            VALUES (?, 'social', ?, ?, ?, 'review', 1, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                path = excluded.path,
                quality_report = excluded.quality_report,
                updated_at = CURRENT_TIMESTAMP
            """,
            (slug, args.channel, row["title"],
             str(target.relative_to(paths.ROOT)),
             json.dumps(report, ensure_ascii=False),
             json.dumps([sources[0]], ensure_ascii=False)),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO publications (slug, channel, mode)
            VALUES (?, ?, 'draft_only')
            """,
            (slug, args.channel),
        )
        db.log_event(connection, "social_draft_created", AGENT, slug,
                     {"channel": args.channel, "mode": mode})
        connection.commit()

    log.info("Social draft saved for manual review: %s", target)
    print(f"\n{target}")
    return 0


def command_list(_args):
    with db.session() as connection:
        db.migrate(connection)
        rows = connection.execute(
            """
            SELECT slug, kind, channel, status, quality_passed, path
            FROM content_items
            ORDER BY id DESC
            """
        ).fetchall()

    if not rows:
        print("No content items yet.")
        return 0

    for row in rows:
        gate = "passed" if row["quality_passed"] else "FAILED"
        print(f"{row['slug']} | {row['kind']} | {row['channel']} | "
              f"{row['status']} | gate={gate} | {row['path']}")

    return 0


def command_check(args):
    with db.session() as connection:
        db.migrate(connection)
        row = connection.execute(
            "SELECT path, sources FROM content_items WHERE slug = ?",
            (args.slug,),
        ).fetchone()

    if not row:
        log.error("Unknown draft: %s", args.slug)
        return 1

    path = paths.inside_root(row["path"])

    if not path.is_file():
        log.error("Draft file is missing: %s", path)
        return 1

    project = config.load_brand()["project"]
    report = quality.check(
        path.read_text(encoding="utf-8"),
        sources=json.loads(row["sources"]),
        cta_url=project["github"],
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine Content Factory Agent"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    draft = commands.add_parser("draft", help="build a research digest draft")
    draft.add_argument("--topic", default=None)
    draft.add_argument("--title", default=None)
    draft.add_argument("--limit", type=int, default=5)
    draft.add_argument("--min-relevance", type=int, default=MIN_RELEVANCE)

    social = commands.add_parser(
        "social", help="build a draft-only post for an external channel"
    )
    social.add_argument("--channel", required=True)
    social.add_argument("--slug", required=True)
    social.add_argument("--community", default=None)

    commands.add_parser("list", help="list drafts and their gate status")

    check = commands.add_parser("check", help="re-run the quality gate")
    check.add_argument("--slug", required=True)

    return parser


HANDLERS = {
    "draft": command_draft,
    "social": command_social,
    "list": command_list,
    "check": command_check,
}


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "draft":
        args.limit = max(1, min(args.limit, 20))

    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
