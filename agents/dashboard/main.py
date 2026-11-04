"""Dashboard Agent.

Renders one static HTML file from the engine's own tables: last run,
what was collected, what is waiting for a human, and the open findings
of each audit.

No secrets, no personal data, no raw logs. Every number here comes from
a table the other agents filled, and the page is plain HTML with inline
CSS so it can be opened from disk or served from anywhere.

Usage:
    python agents/dashboard/main.py build [--output path]
"""

import argparse
import datetime as dt
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import config, db, metrics, paths  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "dashboard"

log = get_logger("sge.dashboard")

STYLE = """
:root {
  --bg: #f6f7f9; --card: #fff; --ink: #14161c; --soft: #5c6170;
  --line: #e0e3ea; --accent: #2f4ad0;
  --ok: #17764a; --warn: #8a5a00; --bad: #a8301c;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161c; --card: #1d2029; --ink: #eceef4; --soft: #9ba2b4;
    --line: #2e323e; --accent: #93a4f4;
    --ok: #46c489; --warn: #dca847; --bad: #f08874;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 32px 18px 64px; }
h1 { font-size: 25px; margin: 0 0 4px; }
.sub { color: var(--soft); margin: 0 0 26px; font-size: 14px; }
h2 {
  font-size: 17px; margin: 32px 0 12px; padding-bottom: 7px;
  border-bottom: 1px solid var(--line);
}
.tiles {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(146px, 1fr));
}
.tile {
  background: var(--card); border: 1px solid var(--line);
  border-radius: 9px; padding: 13px 15px;
}
.tile b {
  display: block; font-size: 23px; font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.tile span { color: var(--soft); font-size: 13px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
.card {
  background: var(--card); border: 1px solid var(--line);
  border-radius: 9px; overflow-x: auto;
}
th, td {
  text-align: left; padding: 8px 13px; border-bottom: 1px solid var(--line);
  vertical-align: top;
}
th {
  font-size: 11.5px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--soft);
}
tbody tr:last-child td { border-bottom: none; }
td.n { font-variant-numeric: tabular-nums; white-space: nowrap; }
.pill {
  display: inline-block; font-size: 11.5px; padding: 1px 7px;
  border-radius: 99px; border: 1px solid currentColor; white-space: nowrap;
}
.ok { color: var(--ok); } .warn { color: var(--warn); } .bad { color: var(--bad); }
.empty { color: var(--soft); padding: 13px; }
a { color: var(--accent); }
footer {
  margin-top: 36px; padding-top: 14px; border-top: 1px solid var(--line);
  color: var(--soft); font-size: 13px;
}
"""


def esc(value):
    return html.escape(str(value if value is not None else ""))


def _tile(value, label):
    return f'<div class="tile"><b>{esc(value)}</b><span>{esc(label)}</span></div>'


def _table(headers, rows, empty="nothing yet"):
    if not rows:
        return f'<div class="card"><p class="empty">{esc(empty)}</p></div>'

    head = "".join(f"<th>{esc(name)}</th>" for name in headers)
    body = "".join(
        "<tr>" + "".join(cells) + "</tr>" for cells in rows
    )

    return (f'<div class="card"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def _severity_pill(severity):
    css = {"critical": "bad", "high": "bad", "medium": "warn",
           "low": "ok"}.get(severity, "")

    return f'<span class="pill {css}">{esc(severity)}</span>'


def collect_state(connection):
    """Everything the page shows, read in one place."""
    def one(query, params=()):
        return connection.execute(query, params).fetchone()

    def many(query, params=()):
        return connection.execute(query, params).fetchall()

    last_run = one(
        "SELECT run_id, MIN(started_at) AS started FROM source_runs "
        "WHERE run_id = (SELECT run_id FROM source_runs "
        "WHERE run_id IS NOT NULL ORDER BY id DESC LIMIT 1)"
    )

    return {
        "last_run": last_run,
        "run_sources": many(
            """
            SELECT source, status, items_seen, items_new, duration_ms, error
            FROM source_runs
            WHERE run_id = (SELECT run_id FROM source_runs
                            WHERE run_id IS NOT NULL ORDER BY id DESC LIMIT 1)
            ORDER BY id
            """
        ),
        "research_total": one(
            "SELECT COUNT(*) AS n FROM research_items"
        )["n"],
        "research_week": one(
            "SELECT COUNT(*) AS n FROM research_items "
            "WHERE fetched_at >= date('now', '-7 days')"
        )["n"],
        "topics": many(
            "SELECT topic, COUNT(*) AS n FROM research_items "
            "WHERE excluded = 0 GROUP BY topic ORDER BY n DESC LIMIT 8"
        ),
        "content": many(
            "SELECT status, COUNT(*) AS n FROM content_items "
            "GROUP BY status ORDER BY status"
        ),
        "drafts": many(
            """
            SELECT slug, kind, channel, status, quality_passed
            FROM content_items
            WHERE status != 'published' ORDER BY id DESC LIMIT 8
            """
        ),
        "signals": many(
            """
            SELECT title, url, advice, relevance, platform
            FROM community_signals
            WHERE reviewed = 0 AND advice != 'skip'
            ORDER BY relevance DESC LIMIT 8
            """
        ),
        "seo": many(
            """
            SELECT severity, code, COUNT(*) AS n FROM seo_findings
            WHERE run_id = (SELECT run_id FROM seo_pages
                            ORDER BY id DESC LIMIT 1)
            GROUP BY severity, code
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high'
                     THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, n DESC
            """
        ),
        "github": one(
            "SELECT * FROM github_snapshots ORDER BY id DESC LIMIT 1"
        ),
        "github_findings": many(
            """
            SELECT severity, code, message FROM github_findings
            WHERE run_id = (SELECT run_id FROM github_snapshots
                            ORDER BY id DESC LIMIT 1)
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high'
                     THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
            """
        ),
        "failures": many(
            """
            SELECT source, error, started_at FROM source_runs
            WHERE status = 'failed' AND started_at >= date('now', '-7 days')
            ORDER BY id DESC LIMIT 6
            """
        ),
        "recommendations": many(
            """
            SELECT area, priority, title FROM recommendations
            WHERE run_id = (SELECT run_id FROM recommendations
                            ORDER BY id DESC LIMIT 1)
            ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium'
                     THEN 1 ELSE 2 END
            """
        ),
    }


def render(state, project, stars_series):
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    github = state["github"]
    last = state["last_run"]

    tiles = [
        _tile(state["research_total"], "research items"),
        _tile(state["research_week"], "collected this week"),
        _tile(github["stars"] if github else "—", "GitHub stars"),
        _tile(len(state["seo"]), "SEO finding types"),
        _tile(len(state["signals"]), "threads to review"),
    ]

    source_rows = [
        [
            f"<td>{esc(row['source'])}</td>",
            f"<td>{_severity_pill('low') if row['status'] == 'ok' else _severity_pill('medium') if row['status'] == 'skipped' else _severity_pill('high')}"
            f" {esc(row['status'])}</td>",
            f"<td class=\"n\">{esc(row['items_seen'])}</td>",
            f"<td class=\"n\">{esc(row['items_new'])}</td>",
            f"<td class=\"n\">{esc(row['duration_ms'])} ms</td>",
            f"<td>{esc(row['error'] or '')}</td>",
        ]
        for row in state["run_sources"]
    ]

    topic_rows = [
        [f"<td>{esc(row['topic'])}</td>",
         f"<td class=\"n\">{esc(row['n'])}</td>"]
        for row in state["topics"]
    ]

    draft_rows = [
        [f"<td>{esc(row['slug'])}</td>",
         f"<td>{esc(row['kind'])} → {esc(row['channel'])}</td>",
         f"<td>{esc(row['status'])}</td>",
         f"<td>{_severity_pill('low') if row['quality_passed'] else _severity_pill('critical')}"
         f" {'gate ok' if row['quality_passed'] else 'gate failed'}</td>"]
        for row in state["drafts"]
    ]

    signal_rows = [
        [f'<td><a href="{esc(row["url"])}">{esc(row["title"][:70])}</a></td>',
         f"<td>{esc(row['platform'])}</td>",
         f"<td>{_severity_pill('high') if row['advice'] == 'reply' else _severity_pill('medium')} {esc(row['advice'])}</td>",
         f"<td class=\"n\">{esc(row['relevance'])}</td>"]
        for row in state["signals"]
    ]

    seo_rows = [
        [f"<td>{_severity_pill(row['severity'])}</td>",
         f"<td>{esc(row['code'])}</td>",
         f"<td class=\"n\">{esc(row['n'])}</td>"]
        for row in state["seo"]
    ]

    gh_rows = [
        [f"<td>{_severity_pill(row['severity'])}</td>",
         f"<td>{esc(row['code'])}</td>",
         f"<td>{esc(row['message'])}</td>"]
        for row in state["github_findings"]
    ]

    failure_rows = [
        [f"<td>{esc(row['started_at'][:16])}</td>",
         f"<td>{esc(row['source'])}</td>",
         f"<td>{esc((row['error'] or '')[:120])}</td>"]
        for row in state["failures"]
    ]

    advice_rows = [
        [f"<td>{_severity_pill('high') if row['priority'] == 'high' else _severity_pill('medium')} {esc(row['priority'])}</td>",
         f"<td>{esc(row['area'])}</td>",
         f"<td>{esc(row['title'])}</td>"]
        for row in state["recommendations"]
    ]

    stars_note = ""
    if len(stars_series) >= 2:
        delta = stars_series[-1][1] - stars_series[0][1]
        stars_note = (f" Stars {delta:+g} since {stars_series[0][0]}."
                      if delta else " Stars unchanged in this window.")

    run_line = (
        f"Last collection run <code>{esc(last['run_id'])}</code> at "
        f"{esc(last['started'])}." if last and last["run_id"]
        else "No collection run recorded yet."
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(project['name'])} Growth Engine</title>
<style>{STYLE}</style>
</head><body><div class="wrap">

<h1>{esc(project['name'])} Growth Engine</h1>
<p class="sub">Generated {esc(now)}. {run_line}{esc(stars_note)}</p>

<div class="tiles">{''.join(tiles)}</div>

<h2>Last run, by source</h2>
{_table(['Source', 'Status', 'Seen', 'New', 'Time', 'Error'], source_rows,
        'no collection run recorded yet')}

<h2>Waiting for a human</h2>
{_table(['Draft', 'Kind', 'Status', 'Quality gate'], draft_rows,
        'no drafts waiting')}

<h2>Community threads to read</h2>
{_table(['Thread', 'Platform', 'Advice', 'Relevance'], signal_rows,
        'nothing on the radar')}

<h2>Recommendations</h2>
{_table(['Priority', 'Area', 'What to do'], advice_rows,
        'no analysis yet')}

<h2>Site findings</h2>
{_table(['Severity', 'Finding', 'Pages'], seo_rows, 'no SEO audit yet')}

<h2>Repository findings</h2>
{_table(['Severity', 'Finding', 'Detail'], gh_rows,
        'no repository audit yet')}

<h2>Topics collected</h2>
{_table(['Topic', 'Items'], topic_rows, 'nothing collected yet')}

<h2>Source failures, last 7 days</h2>
{_table(['When', 'Source', 'Error'], failure_rows, 'no failures')}

<footer>
Read-only view of the engine's own tables. No secrets, personal data or
raw logs appear here. Posting to external communities is always manual.
</footer>

</div></body></html>
"""


def command_build(args):
    project = config.load_brand()["project"]

    with db.session() as connection:
        db.migrate(connection)
        state = collect_state(connection)
        stars = metrics.series(connection, "github_stars", 30)

    page = render(state, project, stars)

    target = Path(args.output) if args.output else (
        paths.ROOT / "analytics" / "dashboard.html"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")

    log.info("Dashboard written: %s (%.1f KiB)", target,
             len(page.encode()) / 1024)
    print(target)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine Dashboard"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="render the static dashboard")
    build.add_argument("--output", default=None)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    return {"build": command_build}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
