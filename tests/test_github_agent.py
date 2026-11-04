"""GitHub Growth agent: audit, snapshots, drafted community files."""

import importlib
import json

import pytest

from sge import db, github

gh_agent = importlib.import_module("agents.github.main")


def run(argv):
    return gh_agent.main(argv)


REPO = {
    "full_name": "o/r", "description": "A scanner",
    "license": {"spdx_id": "MIT"}, "topics": ["a", "b", "c"],
    "homepage": "https://example.test", "has_issues": True,
    "stargazers_count": 14, "forks_count": 2, "subscribers_count": 3,
    "open_issues_count": 0, "pushed_at": "2026-11-03T00:00:00Z",
}

README = (
    "# Tool\n\n## Install\n\n```\nbrew install tool\n```\n\n"
    "## Usage\n\n```\ntool scan host\n```\n\n"
    "Scan only systems you are authorised to test. Licence: MIT.\n\n"
    + "word " * 200
)


@pytest.fixture
def api(feed_server, monkeypatch, temp_db):
    """Serve a complete, healthy repository from the local server."""
    monkeypatch.setattr(github, "API", feed_server.url)

    def serve(path, payload, **options):
        feed_server.serve(path, json.dumps(payload).encode(),
                          content_type="application/json", **options)

    readme_url = feed_server.serve("/readme.md", README.encode(),
                                   content_type="text/plain")

    serve("/repos/o/r", REPO)
    serve("/repos/o/r/readme", {"download_url": readme_url})
    serve("/repos/o/r/releases?per_page=10", [{
        "tag_name": "v1.5.8", "published_at": "2026-10-27T00:00:00Z",
        "body": "A real changelog entry describing what changed here.",
    }])
    serve("/repos/o/r/contents/", [
        {"name": "CONTRIBUTING.md"}, {"name": "SECURITY.md"},
        {"name": "CODE_OF_CONDUCT.md"}, {"name": "CHANGELOG.md"},
        {"name": ".github"},
    ])
    serve("/repos/o/r/issues?state=open&per_page=50", [])
    serve("/repos/o/r/pulls?state=open&per_page=50", [])

    serve.raw = serve
    return serve


def audit_findings():
    with db.session() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT severity, code, message FROM github_findings"
        )]


def test_healthy_repository_has_no_findings(api, capsys):
    assert run(["audit", "--repository", "o/r"]) == 0
    capsys.readouterr()

    assert audit_findings() == []


def test_snapshot_records_the_counters(api, capsys):
    run(["audit", "--repository", "o/r"])
    capsys.readouterr()

    with db.session() as connection:
        row = connection.execute(
            "SELECT stars, forks, releases, latest_release "
            "FROM github_snapshots"
        ).fetchone()

    assert row["stars"] == 14
    assert row["forks"] == 2
    assert row["latest_release"] == "v1.5.8"


def test_problems_are_recorded_as_findings(api, capsys):
    api("/repos/o/r", {**REPO, "has_issues": False, "description": ""})
    api("/repos/o/r/contents/", [{"name": "README.md"}])

    run(["audit", "--repository", "o/r"])
    capsys.readouterr()
    codes = {row["code"] for row in audit_findings()}

    assert "issues_disabled" in codes
    assert "no_description" in codes
    assert "no_contributing" in codes


def test_one_failing_api_call_does_not_abort_the_audit(api, capsys):
    """A missing README must not cost us the rest of the audit."""
    api("/repos/o/r/readme", {"message": "Not Found"}, status=404)

    assert run(["audit", "--repository", "o/r"]) == 0
    capsys.readouterr()

    assert any(row["code"] == "readme_missing" for row in audit_findings())

    with db.session() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS n FROM github_snapshots"
        ).fetchone()["n"] == 1


def test_unavailable_repository_is_an_error(api, capsys):
    api("/repos/o/r", {"message": "Not Found"}, status=404)

    assert run(["audit", "--repository", "o/r"]) == 1
    capsys.readouterr()


def test_report_shows_the_star_delta(api, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(gh_agent.paths, "REPORTS_DIR", tmp_path / "r")
    run(["audit", "--repository", "o/r"])
    api("/repos/o/r", {**REPO, "stargazers_count": 20})
    run(["audit", "--repository", "o/r"])
    capsys.readouterr()

    assert run(["report"]) == 0

    text = next((tmp_path / "r").glob("github-*.md")).read_text("utf-8")

    assert "+6" in text
    assert "does not open issues" in text


def test_report_needs_an_audit_first(temp_db):
    assert run(["report"]) == 1


def test_snapshot_command_records_without_auditing(api, capsys):
    api("/repos/o/r/releases?per_page=1", [{
        "tag_name": "v1.5.8", "published_at": "2026-10-27T00:00:00Z",
    }])

    assert run(["snapshot", "--repository", "o/r"]) == 0
    capsys.readouterr()

    with db.session() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS n FROM github_findings"
        ).fetchone()["n"] == 0
        assert connection.execute(
            "SELECT stars FROM github_snapshots"
        ).fetchone()["stars"] == 14


def test_templates_are_drafted_locally(temp_db, monkeypatch, tmp_path,
                                       capsys):
    monkeypatch.setattr(gh_agent.paths, "DRAFTS_DIR", tmp_path / "drafts")

    assert run(["templates", "--repository", "o/r"]) == 0
    capsys.readouterr()

    directory = tmp_path / "drafts" / "github"
    written = {path.name for path in directory.rglob("*.md")}

    assert {"bug_report.md", "feature_request.md",
            "PULL_REQUEST_TEMPLATE.md", "CONTRIBUTING.md",
            "SECURITY.md"} <= written


def test_drafted_templates_carry_the_project_name_and_rules(
    temp_db, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(gh_agent.paths, "DRAFTS_DIR", tmp_path / "drafts")
    run(["templates", "--repository", "o/r"])
    capsys.readouterr()

    contributing = (tmp_path / "drafts" / "github"
                    / "CONTRIBUTING.md").read_text("utf-8")
    security = (tmp_path / "drafts" / "github"
                / "SECURITY.md").read_text("utf-8")

    assert "ShadowSecurityScanner" in contributing
    assert "authorised" in contributing
    assert "o/r" in security
    assert "{project}" not in contributing


def test_existing_drafts_are_kept_unless_forced(temp_db, monkeypatch,
                                                tmp_path, capsys):
    monkeypatch.setattr(gh_agent.paths, "DRAFTS_DIR", tmp_path / "drafts")
    run(["templates", "--repository", "o/r"])
    target = tmp_path / "drafts" / "github" / "CONTRIBUTING.md"
    target.write_text("edited by a human", encoding="utf-8")

    run(["templates", "--repository", "o/r"])
    assert target.read_text("utf-8") == "edited by a human"

    run(["templates", "--repository", "o/r", "--force"])
    capsys.readouterr()
    assert "edited by a human" not in target.read_text("utf-8")
