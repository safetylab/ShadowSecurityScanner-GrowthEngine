"""SEO Agent.

Audits the project's own site: HTTP status, title and description,
canonical links, Open Graph tags, headings, image alt text, sitemap and
robots.txt, and whether the links on those pages still resolve.

Read-only by design. It produces findings and a report; changing the
site is a human decision, and this agent has no way to do it.

Usage:
    python agents/seo/main.py audit [--limit 20] [--check-links]
    python agents/seo/main.py report [--run-id ID]
    python agents/seo/main.py links [--run-id ID]
"""

import argparse
import datetime as dt
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import config, db, html_meta, net, paths, seo, urls  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "seo"

# Our own site, visited politely: a small delay between requests and a
# hard page cap, so an audit can never look like a crawl.
REQUEST_DELAY = 0.5
MAX_PAGES = 50
MAX_PAGE_BYTES = 4 * 1024 * 1024
LINK_TIMEOUT = 10

log = get_logger("sge.seo")


def site_url():
    return config.load_brand()["project"]["website"]


def fetch_text(url, max_bytes=MAX_PAGE_BYTES):
    """Fetch a page, returning (status, text, byte length)."""
    data = net.fetch_bytes(url, timeout=net.DEFAULT_TIMEOUT,
                           max_bytes=max_bytes)

    return 200, data.decode("utf-8", errors="replace"), len(data)


def sitemap_urls(base):
    """Page URLs from sitemap.xml, or just the base URL if there is none."""
    target = urls.canonical_url(base.rstrip("/") + "/sitemap.xml")

    try:
        _status, text, _size = fetch_text(target)
    except net.FetchError as error:
        log.warning("No usable sitemap at %s: %s", target, error)
        return [urls.canonical_url(base)], False

    found = []

    # A sitemap is XML, but a tolerant scan beats a parser here: some
    # generators emit a sitemap index, others wrap entries in namespaces.
    for part in text.split("<loc>")[1:]:
        raw = part.split("</loc>")[0].strip()

        try:
            canonical = urls.canonical_url(raw)
        except urls.InvalidUrl:
            continue

        if canonical not in found:
            found.append(canonical)

    if not found:
        return [urls.canonical_url(base)], False

    return found, True


def check_robots(base):
    """Findings about robots.txt."""
    target = urls.canonical_url(base.rstrip("/") + "/robots.txt")

    try:
        _status, text, _size = fetch_text(target, max_bytes=512 * 1024)
    except net.FetchError as error:
        return [{
            "severity": "high", "code": "robots_missing",
            "message": f"robots.txt unavailable: {error}",
        }], ""

    findings = []
    lowered = text.lower()

    if "sitemap:" not in lowered:
        findings.append({
            "severity": "medium", "code": "robots_no_sitemap",
            "message": "robots.txt does not point to a sitemap",
        })

    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped in ("disallow: /", "disallow:/"):
            findings.append({
                "severity": "critical", "code": "robots_blocks_site",
                "message": "robots.txt disallows the whole site",
            })
            break

    return findings, text


def command_audit(args):
    base = site_url()
    run_id = uuid.uuid4().hex[:12]
    pages, has_sitemap = sitemap_urls(base)
    pages = pages[:min(args.limit, MAX_PAGES)]

    log.info("Audit %s of %s: %d page(s)%s", run_id, base, len(pages),
             "" if has_sitemap else " (no sitemap, auditing the base URL)")

    all_findings = []
    link_targets = {}

    with db.session() as connection:
        db.migrate(connection)

        robots_findings, _robots = check_robots(base)

        if not has_sitemap:
            robots_findings.append({
                "severity": "high", "code": "sitemap_missing",
                "message": "sitemap.xml is missing or has no entries",
            })

        for finding in robots_findings:
            all_findings.append({"url": base, **finding})

        host = urlsplit(base).hostname

        for index, url in enumerate(pages):
            if index:
                time.sleep(REQUEST_DELAY)

            started = time.monotonic()

            try:
                status, text, size = fetch_text(url)
            except net.FetchError as error:
                log.warning("%s: %s", url, error)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO seo_pages
                        (run_id, url, status, duration_ms)
                    VALUES (?, ?, 0, ?)
                    """,
                    (run_id, url, int((time.monotonic() - started) * 1000)),
                )
                all_findings.append({
                    "url": url, "severity": "critical",
                    "code": "unreachable",
                    "message": f"Page could not be fetched: {error}",
                })
                continue

            page = html_meta.parse_page(text)
            internal = seo.internal_links(url, page["links"], host)
            external = seo.external_links(url, page["links"])

            for target in internal:
                link_targets.setdefault(target, (url, "internal"))
            for target in external:
                link_targets.setdefault(target, (url, "external"))

            connection.execute(
                """
                INSERT OR REPLACE INTO seo_pages
                    (run_id, url, status, title, description, canonical,
                     internal_links, external_links, bytes, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, url, status, page["title"],
                 page["meta"].get("description", ""), page["canonical"],
                 len(internal), len(external), size,
                 int((time.monotonic() - started) * 1000)),
            )

            page_findings = seo.check_page(url, status, page)

            for finding in page_findings:
                all_findings.append({"url": url, **finding})

            log.info("%s: HTTP %d, %d finding(s)", url, status,
                     len(page_findings))

        for finding in all_findings:
            connection.execute(
                """
                INSERT INTO seo_findings (run_id, url, severity, code,
                                          message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, finding["url"], finding["severity"],
                 finding["code"], finding["message"]),
            )

        if args.check_links:
            _check_links(connection, run_id, link_targets, args.limit * 10)

        db.log_event(connection, "seo_audit", AGENT, None,
                     {"run_id": run_id, "pages": len(pages),
                      "findings": len(all_findings)})
        connection.commit()

    by_severity = {}
    for finding in all_findings:
        by_severity[finding["severity"]] = \
            by_severity.get(finding["severity"], 0) + 1

    log.info("Audit %s done: %s", run_id,
             ", ".join(f"{count} {name}"
                       for name, count in sorted(by_severity.items()))
             or "no findings")
    print(run_id)
    return 0


REFUSAL_CODES = ("HTTP 403", "HTTP 429")


def _refused_us(error):
    """True when the target refused the checker rather than being dead."""
    return any(code in error for code in REFUSAL_CODES)


def _check_links(connection, run_id, targets, limit):
    """Resolve collected links; records status or error per link."""
    log.info("Checking %d link target(s)", min(len(targets), limit))
    broken = 0
    blocked = 0

    for index, (url, (found_on, kind)) in enumerate(targets.items()):
        if index >= limit:
            break

        if index:
            time.sleep(REQUEST_DELAY)

        status = None
        error = None

        try:
            # A small read is enough: we only want the status.
            net.fetch_bytes(url, timeout=LINK_TIMEOUT, max_bytes=65536)
            status = 200
        except net.ResponseTooLarge:
            status = 200          # it answered, it is simply a big page
        except net.FetchError as failure:
            error = str(failure)[:300]

            # 403 and 429 say the target refused *us*, not that the link
            # is dead: cisa.gov answers 200 to a browser and 403 to this
            # agent. Reporting that as broken would send someone to fix
            # a working link.
            if _refused_us(error):
                status = None
                blocked += 1
            else:
                broken += 1

        connection.execute(
            """
            INSERT OR REPLACE INTO seo_links
                (run_id, url, found_on, kind, status, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, url, found_on, kind, status, error),
        )

    if broken:
        connection.execute(
            """
            INSERT INTO seo_findings (run_id, url, severity, code, message)
            VALUES (?, ?, 'high', 'broken_links', ?)
            """,
            (run_id, "site", f"{broken} link(s) returned 404 or failed"),
        )

    if blocked:
        connection.execute(
            """
            INSERT INTO seo_findings (run_id, url, severity, code, message)
            VALUES (?, ?, 'low', 'links_unverifiable', ?)
            """,
            (run_id, "site",
             f"{blocked} link(s) refused this checker (403/429); they may "
             "still work in a browser"),
        )

    log.info("Link check done: %d broken, %d unverifiable", broken, blocked)


def _latest_run(connection):
    row = connection.execute(
        "SELECT run_id FROM seo_pages ORDER BY id DESC LIMIT 1"
    ).fetchone()

    return row["run_id"] if row else None


def command_report(args):
    with db.session() as connection:
        db.migrate(connection)
        run_id = args.run_id or _latest_run(connection)

        if not run_id:
            log.error("No audit found. Run: seo audit")
            return 1

        pages = connection.execute(
            "SELECT * FROM seo_pages WHERE run_id = ? ORDER BY url",
            (run_id,),
        ).fetchall()
        findings = connection.execute(
            "SELECT url, severity, code, message FROM seo_findings "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        broken = connection.execute(
            """
            SELECT url, found_on, error FROM seo_links
            WHERE run_id = ? AND error IS NOT NULL AND status IS NULL
              AND error NOT LIKE '%HTTP 403%' AND error NOT LIKE '%HTTP 429%'
            """,
            (run_id,),
        ).fetchall()
        unverifiable = connection.execute(
            """
            SELECT url, found_on, error FROM seo_links
            WHERE run_id = ?
              AND (error LIKE '%HTTP 403%' OR error LIKE '%HTTP 429%')
            """,
            (run_id,),
        ).fetchall()

    ordered = seo.sort_findings([dict(row) for row in findings])
    today = dt.date.today().isoformat()

    counts = {}
    for finding in ordered:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1

    lines = [
        f"# SEO report {today}",
        "",
        f"Audit `{run_id}`: {len(pages)} page(s), {len(ordered)} finding(s)"
        + (f" ({', '.join(f'{v} {k}' for k, v in sorted(counts.items()))})"
           if counts else ""),
        "",
        "Recommendations only. This agent does not change the site.",
        "",
        "## Findings, worst first",
        "",
    ]

    for finding in ordered:
        lines.append(
            f"- **{finding['severity']}** `{finding['code']}` — "
            f"{finding['message']}  \n  {finding['url']}"
        )

    if not ordered:
        lines.append("- nothing to fix")

    lines += ["", "## Pages", "",
              "| Page | HTTP | Title length | Description length | "
              "Links in / out |",
              "| --- | --- | --- | --- | --- |"]

    for page in pages:
        path = urlsplit(page["url"]).path or "/"
        lines.append(
            f"| `{path}` | {page['status']} | "
            f"{len(page['title'] or '')} | "
            f"{len(page['description'] or '')} | "
            f"{page['internal_links']} / {page['external_links']} |"
        )

    if broken:
        lines += ["", "## Broken links", ""]
        for link in broken:
            lines.append(
                f"- {link['url']} — {link['error']}  \n"
                f"  found on {link['found_on']}"
            )

    if unverifiable:
        lines += ["", "## Links this checker could not verify", "",
                  "These refused the audit (403 or 429). They often work "
                  "in a browser: check by hand before changing anything.",
                  ""]
        for link in unverifiable:
            lines.append(
                f"- {link['url']} — {link['error']}  \n"
                f"  found on {link['found_on']}"
            )

    report = "\n".join(lines) + "\n"

    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.REPORTS_DIR / f"seo-{today}.md"
    target.write_text(report, encoding="utf-8")

    print(report)
    log.info("Report written: %s", target)
    return 0


def command_links(args):
    with db.session() as connection:
        db.migrate(connection)
        run_id = args.run_id or _latest_run(connection)

        if not run_id:
            log.error("No audit found. Run: seo audit --check-links")
            return 1

        rows = connection.execute(
            """
            SELECT url, kind, status, error, found_on
            FROM seo_links WHERE run_id = ?
            ORDER BY (error IS NULL), url
            """,
            (run_id,),
        ).fetchall()

    if not rows:
        print("No links checked in this run. Use: audit --check-links")
        return 0

    for row in rows:
        state = row["error"] or f"HTTP {row['status']}"
        print(f"{row['kind']:8} {state:28} {row['url']}")

    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine SEO Agent (read-only)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit", help="audit the project site")
    audit.add_argument("--limit", type=int, default=20,
                       help=f"maximum pages to visit (cap {MAX_PAGES})")
    audit.add_argument("--check-links", action="store_true",
                       help="also resolve every link found")

    report = commands.add_parser("report", help="write a markdown report")
    report.add_argument("--run-id", default=None)

    links = commands.add_parser("links", help="show checked links")
    links.add_argument("--run-id", default=None)

    return parser


HANDLERS = {
    "audit": command_audit,
    "report": command_report,
    "links": command_links,
}


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "audit":
        args.limit = max(1, min(args.limit, MAX_PAGES))

    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
