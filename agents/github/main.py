"""GitHub Growth Agent.

Audits the public face of the main repository: metadata, README, quick
start, community files, releases and unanswered issues. Prepares draft
community files and release notes locally, for a human to review and
commit.

Read-only against GitHub. It never opens issues or pull requests, never
messages anyone, and never touches stars, forks or any other counter.

Usage:
    python agents/github/main.py audit
    python agents/github/main.py report [--run-id ID]
    python agents/github/main.py templates
    python agents/github/main.py snapshot
"""

import argparse
import datetime as dt
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import config, db, github, paths, repo_health  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "github"

log = get_logger("sge.github")


def target_repository():
    project = config.load_brand()["project"]

    return github.owner_repo_from_url(project["github"])


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _gather(repo_name):
    """Everything the audit needs, with per-call failure isolation."""
    data = {"errors": []}

    def attempt(key, call, default):
        try:
            data[key] = call()
        except github.GitHubError as error:
            log.warning("%s unavailable: %s", key, error)
            data["errors"].append(f"{key}: {error}")
            data[key] = default

    attempt("repo", lambda: github.repository(repo_name), {})
    attempt("readme", lambda: github.readme(repo_name), "")
    attempt("releases", lambda: github.releases(repo_name, 10), [])
    attempt("contents", lambda: github.contents(repo_name), [])
    attempt("issues", lambda: github.issues(repo_name, "open", 50), [])
    attempt("pulls", lambda: github.pull_requests(repo_name, "open", 50), [])

    return data


def command_audit(args):
    repo_name = args.repository or target_repository()
    run_id = uuid.uuid4().hex[:12]
    now = _now()

    log.info("Audit %s of %s (%s)", run_id, repo_name,
             "authenticated" if github.token() else "anonymous")

    data = _gather(repo_name)

    if not data["repo"]:
        log.error("Repository metadata unavailable; nothing to audit")
        return 1

    findings = []
    findings += repo_health.check_metadata(data["repo"])
    findings += repo_health.check_readme(data["readme"])
    findings += repo_health.check_community_files(
        [item["name"] for item in data["contents"]]
    )
    findings += repo_health.check_releases(data["releases"], now)
    findings += repo_health.check_issue_response(data["issues"], now)

    with db.session() as connection:
        db.migrate(connection)

        for finding in findings:
            connection.execute(
                """
                INSERT INTO github_findings
                    (run_id, repository, severity, code, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, repo_name, finding["severity"], finding["code"],
                 finding["message"]),
            )

        latest = data["releases"][0] if data["releases"] else {}

        connection.execute(
            """
            INSERT INTO github_snapshots
                (run_id, repository, stars, forks, watchers, open_issues,
                 open_pull_requests, releases, latest_release,
                 latest_release_at, pushed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, repo_name,
             data["repo"].get("stargazers_count", 0),
             data["repo"].get("forks_count", 0),
             data["repo"].get("subscribers_count", 0),
             len(data["issues"]), len(data["pulls"]),
             len(data["releases"]), latest.get("tag_name"),
             latest.get("published_at"), data["repo"].get("pushed_at")),
        )
        db.log_event(connection, "github_audit", AGENT, None,
                     {"run_id": run_id, "findings": len(findings)})
        connection.commit()

    counts = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1

    log.info("Audit %s done: %s", run_id,
             ", ".join(f"{count} {name}"
                       for name, count in sorted(counts.items()))
             or "no findings")
    print(run_id)
    return 0


def _latest_run(connection):
    row = connection.execute(
        "SELECT run_id FROM github_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()

    return row["run_id"] if row else None


def command_report(args):
    with db.session() as connection:
        db.migrate(connection)
        run_id = args.run_id or _latest_run(connection)

        if not run_id:
            log.error("No audit found. Run: github audit")
            return 1

        snapshot = connection.execute(
            "SELECT * FROM github_snapshots WHERE run_id = ?", (run_id,)
        ).fetchone()
        findings = connection.execute(
            "SELECT severity, code, message FROM github_findings "
            "WHERE run_id = ?", (run_id,)
        ).fetchall()
        history = connection.execute(
            """
            SELECT stars, forks, taken_at FROM github_snapshots
            WHERE repository = ? ORDER BY id DESC LIMIT 2
            """,
            (snapshot["repository"],),
        ).fetchall()

    ordered = repo_health.sort_findings([dict(row) for row in findings])
    today = dt.date.today().isoformat()

    change = ""
    if len(history) == 2:
        delta = history[0]["stars"] - history[1]["stars"]
        if delta:
            change = f" ({delta:+d} since {history[1]['taken_at'][:10]})"

    lines = [
        f"# GitHub report {today}",
        "",
        f"Repository: `{snapshot['repository']}`, audit `{run_id}`.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Stars | {snapshot['stars']}{change} |",
        f"| Forks | {snapshot['forks']} |",
        f"| Watchers | {snapshot['watchers']} |",
        f"| Open issues | {snapshot['open_issues']} |",
        f"| Open pull requests | {snapshot['open_pull_requests']} |",
        f"| Releases | {snapshot['releases']} |",
        f"| Latest release | {snapshot['latest_release'] or 'none'} "
        f"({(snapshot['latest_release_at'] or '')[:10] or 'n/a'}) |",
        f"| Last push | {(snapshot['pushed_at'] or '')[:10] or 'n/a'} |",
        "",
        "## Findings, worst first",
        "",
    ]

    for finding in ordered:
        lines.append(
            f"- **{finding['severity']}** `{finding['code']}` — "
            f"{finding['message']}"
        )

    if not ordered:
        lines.append("- nothing to fix")

    lines += [
        "",
        "Recommendations only. This agent does not open issues or pull "
        "requests, and does not touch any counter.",
    ]

    report = "\n".join(lines) + "\n"

    paths.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.REPORTS_DIR / f"github-{today}.md"
    target.write_text(report, encoding="utf-8")

    print(report)
    log.info("Report written: %s", target)
    return 0


def command_snapshot(args):
    """Record the counters without running the full audit."""
    repo_name = args.repository or target_repository()

    try:
        repo = github.repository(repo_name)
        releases = github.releases(repo_name, 1)
    except github.GitHubError as error:
        log.error("Cannot read repository: %s", error)
        return 1

    latest = releases[0] if releases else {}

    with db.session() as connection:
        db.migrate(connection)
        connection.execute(
            """
            INSERT INTO github_snapshots
                (run_id, repository, stars, forks, watchers, open_issues,
                 releases, latest_release, latest_release_at, pushed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex[:12], repo_name,
             repo.get("stargazers_count", 0), repo.get("forks_count", 0),
             repo.get("subscribers_count", 0),
             repo.get("open_issues_count", 0), len(releases),
             latest.get("tag_name"), latest.get("published_at"),
             repo.get("pushed_at")),
        )
        connection.commit()

    log.info("%s: %d stars, %d forks", repo_name,
             repo.get("stargazers_count", 0), repo.get("forks_count", 0))
    return 0


TEMPLATES = {
    "ISSUE_TEMPLATE/bug_report.md": '''---
name: Bug report
about: Something does not work as documented
labels: bug
---

**What happened**

**What you expected**

**How to reproduce**

1.
2.

**Environment**

- {project} version:
- Operating system:
- Installed from: release binary / built from source

**Scan target** (only describe systems you are authorised to test)

**Logs or output** (remove any hostnames, addresses or credentials you
do not want public)

```
```
''',
    "ISSUE_TEMPLATE/feature_request.md": '''---
name: Feature request
about: Suggest a check, output format or workflow
labels: enhancement
---

**What problem does this solve**

**How you work around it today**

**What the output or behaviour should look like**

**Would you be willing to contribute it?**
''',
    "PULL_REQUEST_TEMPLATE.md": '''## What this changes

## Why

## How it was verified

- [ ] tests pass locally
- [ ] new behaviour is covered by a test
- [ ] documentation updated where behaviour changed
- [ ] no change to scanning behaviour without an explicit note below

## Notes for the reviewer
''',
    "CONTRIBUTING.md": '''# Contributing to {project}

Thank you for considering a contribution. {project} is a security tool,
so a few rules exist that an ordinary project would not need.

## Ground rules

- Only ever test against systems you own or are explicitly authorised to
  test. Do not attach scan output from third-party infrastructure.
- Report a security problem in {project} itself privately, through the
  SECURITY policy, not as a public issue.
- Do not add a check that is destructive by default, or one whose only
  purpose is evasion.

## Getting started

1. Fork the repository and create a branch from the default branch.
2. Build and run the test suite before you change anything, so you know
   the baseline passes.
3. Make your change with a test that fails without it.
4. Open a pull request describing what you verified and how.

## What makes a change easy to accept

- A clear statement of the problem it solves.
- A test that demonstrates the behaviour.
- No unrelated reformatting in the same commit.
- Documentation updated in the same change where behaviour changed.

## Reporting a bug

Use the bug report template. The most useful bug report says what you
ran, what you expected and what happened, with the version and platform.

## Questions

Open a discussion rather than an issue if you are not sure whether
something is a bug.
''',
    "SECURITY.md": '''# Security policy

## Reporting a vulnerability in {project}

Report privately through GitHub's "Report a vulnerability" button on the
Security tab of {repository}, which opens a private advisory visible only
to the maintainers.

Please do not open a public issue for a security problem in {project}
itself.

Include:

- what the problem is and what an attacker could do with it,
- the version you tested and how you built or installed it,
- the steps to reproduce, against a system you are authorised to test.

## What to expect

You will get an acknowledgement that the report was received, a decision
on whether it is in scope, and a note when a fix ships. If a report is
out of scope you will be told why.

## Scope

In scope: {project} itself — the scanner, its parsing of responses, its
reporting output, and its handling of credentials or scan data.

Out of scope: vulnerabilities in the systems you scan, and findings
produced by running {project} against targets you do not control.

## Using this tool

{project} is for systems you own or are authorised to test. Scanning
infrastructure without the owner's permission is unlawful in most
jurisdictions and is not supported here.
''',
}


def command_templates(args):
    """Write community file drafts for review. Nothing is committed."""
    project = config.load_brand()["project"]
    repository = args.repository or target_repository()

    target_dir = paths.DRAFTS_DIR / "github"
    target_dir.mkdir(parents=True, exist_ok=True)

    written = []

    for relative, template in TEMPLATES.items():
        path = target_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists() and not args.force:
            log.info("Exists, keeping: %s", path)
            continue

        path.write_text(
            template.format(project=project["name"], repository=repository),
            encoding="utf-8",
        )
        written.append(path)

    with db.session() as connection:
        db.migrate(connection)
        db.log_event(connection, "github_templates_drafted", AGENT, None,
                     {"files": len(written)})
        connection.commit()

    for path in written:
        print(path)

    log.info("Wrote %d draft(s) to %s. Review before copying into the "
             "main repository.", len(written), target_dir)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine GitHub Growth Agent (read-only)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit", help="audit the repository")
    audit.add_argument("--repository", default=None, help="owner/repo")

    report = commands.add_parser("report", help="write a markdown report")
    report.add_argument("--run-id", default=None)

    snapshot = commands.add_parser("snapshot",
                                   help="record counters only")
    snapshot.add_argument("--repository", default=None)

    templates = commands.add_parser(
        "templates", help="draft issue templates and community files"
    )
    templates.add_argument("--repository", default=None)
    templates.add_argument("--force", action="store_true",
                           help="overwrite existing drafts")

    return parser


HANDLERS = {
    "audit": command_audit,
    "report": command_report,
    "snapshot": command_snapshot,
    "templates": command_templates,
}


def main(argv=None):
    args = build_parser().parse_args(argv)

    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
