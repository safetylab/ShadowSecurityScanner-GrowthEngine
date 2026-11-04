import datetime as dt

from sge import repo_health

NOW = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)

HEALTHY_REPO = {
    "description": "A scanner",
    "license": {"spdx_id": "MIT"},
    "topics": ["security", "scanner", "cve"],
    "homepage": "https://example.test",
    "has_issues": True,
}

GOOD_README = (
    "# Tool\n\n## Installation\n\n```\nbrew install tool\n```\n\n"
    "## Usage\n\n```\ntool scan host\n```\n\n"
    "Only scan systems you are authorised to test.\n\n"
    "Licence: MIT.\n\n" + "word " * 200
)


def codes(findings):
    return {finding["code"] for finding in findings}


def test_healthy_metadata_has_no_findings():
    assert repo_health.check_metadata(HEALTHY_REPO) == []


def test_disabled_issues_is_critical():
    findings = repo_health.check_metadata({**HEALTHY_REPO,
                                           "has_issues": False})

    assert any(f["code"] == "issues_disabled" and f["severity"] == "critical"
               for f in findings)


def test_missing_description_and_license_are_reported():
    findings = repo_health.check_metadata(
        {**HEALTHY_REPO, "description": "", "license": None}
    )

    assert {"no_description", "no_license"} <= codes(findings)


def test_few_topics_counts_them():
    findings = repo_health.check_metadata({**HEALTHY_REPO,
                                           "topics": ["security"]})
    finding = next(f for f in findings if f["code"] == "few_topics")

    assert "1 topic" in finding["message"]


def test_good_readme_has_no_findings():
    assert repo_health.check_readme(GOOD_README) == []


def test_empty_readme_is_critical():
    findings = repo_health.check_readme("")

    assert [f["code"] for f in findings] == ["readme_missing"]


def test_thin_readme_is_reported():
    findings = repo_health.check_readme("# Tool\n\n## Install\n\nShort.")

    assert "readme_thin" in codes(findings)


def test_missing_authorisation_notice_is_high():
    without = GOOD_README.replace(
        "Only scan systems you are authorised to test.", "")
    findings = repo_health.check_readme(without)

    assert any(f["code"] == "no_authorisation_notice"
               and f["severity"] == "high" for f in findings)


def test_download_or_build_counts_as_an_entry_point():
    """A Go project says Build, a released binary says Download."""
    for heading in ("## Download", "## Build the libraries", "## Running"):
        readme = (f"# Tool\n\n{heading}\n\n```\nmake\n```\n\n```\nrun\n```\n\n"
                  "Authorised systems only. Licence: MIT.\n" + "word " * 200)

        assert "no_quick_start" not in codes(
            repo_health.check_readme(readme)
        ), heading


def test_a_heading_inside_a_code_block_does_not_count():
    """Shell examples contain lines that look like Markdown headings."""
    readme = ("# Tool\n\n```\n# Install the thing\n# RANK CVE EPSS\n```\n\n"
              "```\nmore\n```\n\nAuthorised only. Licence: MIT.\n"
              + "word " * 200)

    assert "no_quick_start" in codes(repo_health.check_readme(readme))


def test_code_blocks_do_not_pad_the_word_count():
    readme = ("# Tool\n\n## Install\n\n```\n" + "filler " * 300
              + "\n```\n\n```\nx\n```\n\nAuthorised. Licence: MIT.")

    assert "readme_thin" in codes(repo_health.check_readme(readme))


def test_readme_links_are_extracted_once():
    text = ("[a](https://example.test/a) [again](https://example.test/a) "
            "[b](https://example.test/b) [rel](/not-absolute)")

    assert repo_health.readme_links(text) == [
        "https://example.test/a", "https://example.test/b"
    ]


def test_community_files_present_means_no_findings():
    present = ["CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md",
               "CHANGELOG.md", ".github"]

    assert repo_health.check_community_files(present) == []


def test_missing_community_files_are_listed():
    findings = repo_health.check_community_files(["README.md"])

    assert {"no_contributing", "no_security_policy", "no_changelog",
            "no_code_of_conduct", "no_github_templates"} == codes(findings)


def test_community_file_check_is_case_insensitive():
    present = ["contributing.md", "security.md", "code_of_conduct.md",
               "changelog.md", ".github"]

    assert repo_health.check_community_files(present) == []


def test_no_releases_is_high():
    findings = repo_health.check_releases([], NOW)

    assert [f["code"] for f in findings] == ["no_releases"]


def test_recent_release_with_notes_is_clean():
    releases = [{
        "tag_name": "v1.0.0",
        "published_at": "2026-11-01T00:00:00Z",
        "body": "A real changelog entry describing what changed here.",
    }]

    assert repo_health.check_releases(releases, NOW) == []


def test_stale_release_reports_its_age():
    releases = [{
        "tag_name": "v1.0.0",
        "published_at": "2026-01-01T00:00:00Z",
        "body": "A real changelog entry describing what changed here.",
    }]
    finding = next(f for f in repo_health.check_releases(releases, NOW)
                   if f["code"] == "release_stale")

    assert "307 days" in finding["message"]


def test_releases_without_notes_are_named():
    releases = [
        {"tag_name": "v1.1", "published_at": "2026-11-01T00:00:00Z",
         "body": "A real changelog entry describing what changed here."},
        {"tag_name": "v1.0", "published_at": "2026-10-01T00:00:00Z",
         "body": ""},
    ]
    finding = next(f for f in repo_health.check_releases(releases, NOW)
                   if f["code"] == "releases_without_notes")

    assert "v1.0" in finding["message"]


def test_unanswered_old_issues_are_reported():
    issues = [
        {"number": 7, "comments": 0, "created_at": "2026-10-01T00:00:00Z"},
        {"number": 8, "comments": 3, "created_at": "2026-10-01T00:00:00Z"},
        {"number": 9, "comments": 0, "created_at": "2026-11-03T00:00:00Z"},
    ]
    findings = repo_health.check_issue_response(issues, NOW)

    assert len(findings) == 1
    assert "#7" in findings[0]["message"]
    assert "#8" not in findings[0]["message"]
    assert "#9" not in findings[0]["message"]


def test_no_issues_is_not_a_finding():
    assert repo_health.check_issue_response([], NOW) == []
