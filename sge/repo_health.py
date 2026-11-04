"""Checks over a repository's public face.

What a newcomer sees in the first minute: a README that says what the
tool is and how to run it, a recent release, a licence, somewhere to
report a bug. Each check states what is missing, never invents activity.
"""

import re

SEVERITIES = ("critical", "high", "medium", "low")

RELEASE_STALE_DAYS = 120
README_MIN_WORDS = 150

# An entry point can be called several things: a Go project says
# "Build", a released binary says "Download".
QUICK_START_PATTERNS = (
    r"(?im)^#{1,4}\s*(quick\s*start|getting\s+started|installation|"
    r"install|download|build|setup)",
    r"(?im)^#{1,4}\s*(usage|how\s+to\s+use|running|run\b)",
)

CODE_BLOCK = re.compile(r"```")
FENCED_BLOCK = re.compile(r"```.*?```", re.DOTALL)
LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")


def without_code(text):
    """Text with fenced blocks removed.

    Shell examples contain comment lines that look exactly like Markdown
    headings ("# RANK CVE EPSS"), so heading checks must not see them.
    """
    return FENCED_BLOCK.sub("\n", text)


def _finding(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def check_metadata(repo):
    """Findings about the repository's own metadata."""
    findings = []

    if not (repo.get("description") or "").strip():
        findings.append(_finding(
            "high", "no_description",
            "Repository has no description: it is the first line a "
            "visitor reads in search results"
        ))

    if not repo.get("license"):
        findings.append(_finding(
            "high", "no_license",
            "No licence detected: contributors cannot tell what they may do"
        ))

    topics = repo.get("topics") or []

    if len(topics) < 3:
        findings.append(_finding(
            "medium", "few_topics",
            f"Only {len(topics)} topic(s): topics are how GitHub "
            "surfaces a project in its own search"
        ))

    if not (repo.get("homepage") or "").strip():
        findings.append(_finding(
            "medium", "no_homepage",
            "No homepage set, so the site is not linked from the sidebar"
        ))

    if not repo.get("has_issues"):
        findings.append(_finding(
            "critical", "issues_disabled",
            "Issues are disabled: users have nowhere to report a bug"
        ))

    return findings


def check_readme(text):
    """Findings about README content."""
    findings = []

    if not text.strip():
        return [_finding("critical", "readme_missing", "README is empty")]

    prose = without_code(text)
    words = len(re.findall(r"\b[\w'-]+\b", prose))

    if words < README_MIN_WORDS:
        findings.append(_finding(
            "high", "readme_thin",
            f"README is {words} words, under {README_MIN_WORDS}"
        ))

    if not any(re.search(pattern, prose)
               for pattern in QUICK_START_PATTERNS):
        findings.append(_finding(
            "high", "no_quick_start",
            "No install, quick start or usage heading in the README"
        ))

    if len(CODE_BLOCK.findall(text)) < 2:
        findings.append(_finding(
            "medium", "no_examples",
            "Fewer than one fenced code block: a scanner README needs a "
            "runnable example"
        ))

    if not re.search(r"(?i)(licen[cs]e)", text):
        findings.append(_finding(
            "low", "readme_no_license_mention",
            "README does not mention the licence"
        ))

    if not re.search(r"(?i)(authori[sz]ed|permission|legal|only\s+scan)",
                     text):
        findings.append(_finding(
            "high", "no_authorisation_notice",
            "README does not tell users to scan only systems they are "
            "authorised to test"
        ))

    return findings


def readme_links(text):
    """http(s) links in the README."""
    found = []

    for match in LINK.finditer(text):
        url = match.group(1)
        if url not in found:
            found.append(url)

    return found


def check_community_files(paths_present):
    """Findings about contribution and support files."""
    findings = []
    lowered = {name.lower() for name in paths_present}

    expectations = (
        ("contributing.md", "high", "no_contributing",
         "No CONTRIBUTING file: contributors have no stated process"),
        ("security.md", "high", "no_security_policy",
         "No SECURITY policy: a security tool needs a disclosure route"),
        ("code_of_conduct.md", "low", "no_code_of_conduct",
         "No code of conduct"),
        ("changelog.md", "medium", "no_changelog",
         "No CHANGELOG: release notes are the only history"),
    )

    for filename, severity, code, message in expectations:
        if filename not in lowered:
            findings.append(_finding(severity, code, message))

    if not any(name.startswith(".github") for name in lowered):
        findings.append(_finding(
            "medium", "no_github_templates",
            "No .github directory: no issue or pull request templates"
        ))

    return findings


def check_releases(releases, now, stale_days=RELEASE_STALE_DAYS):
    """Findings about release hygiene."""
    findings = []

    if not releases:
        return [_finding(
            "high", "no_releases",
            "No releases: users have nothing to download and no changelog"
        )]

    latest = releases[0]
    published = latest.get("published_at") or ""

    if published:
        import datetime as dt

        try:
            when = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
            age = (now - when).days

            if age > stale_days:
                findings.append(_finding(
                    "medium", "release_stale",
                    f"Latest release {latest.get('tag_name')} is {age} "
                    f"days old"
                ))
        except ValueError:
            pass

    without_notes = [
        release.get("tag_name", "?")
        for release in releases
        if len((release.get("body") or "").strip()) < 40
    ]

    if without_notes:
        findings.append(_finding(
            "medium", "releases_without_notes",
            f"Releases with little or no notes: "
            f"{', '.join(without_notes[:5])}"
        ))

    return findings


def check_issue_response(issues, now):
    """Findings about unanswered issues."""
    findings = []

    if not issues:
        return findings

    import datetime as dt

    stale = []

    for issue in issues:
        if issue.get("comments"):
            continue

        created = issue.get("created_at") or ""

        try:
            when = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue

        if (now - when).days > 7:
            stale.append(f"#{issue.get('number')}")

    if stale:
        findings.append(_finding(
            "high", "issues_unanswered",
            f"Open issues with no reply after a week: "
            f"{', '.join(stale[:8])}"
        ))

    return findings


def sort_findings(findings):
    order = {severity: index for index, severity in enumerate(SEVERITIES)}

    return sorted(findings, key=lambda f: order.get(f["severity"], 99))
