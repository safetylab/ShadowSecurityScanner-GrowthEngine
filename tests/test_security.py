"""Security properties of the engine, asserted rather than assumed.

These are the checks a reviewer would make by hand, kept as tests so a
later change cannot quietly undo them.
"""

import re
from pathlib import Path

import pytest
import yaml

from sge import db, paths

WORKFLOWS = sorted(Path(".github/workflows").glob("*.yml"))
PYTHON_FILES = sorted(Path("sge").glob("*.py")) + \
    sorted(Path("agents").glob("*/main.py"))


def test_paths_refuse_to_escape_the_repository():
    for attempt in ("../../etc/passwd", "/etc/passwd",
                    "content/../../../../etc/passwd"):
        with pytest.raises(paths.UnsafePath):
            paths.inside_root(attempt)


def test_paths_inside_the_repository_resolve():
    assert paths.inside_root("content/drafts/x.md").name == "x.md"


def test_the_database_is_owner_only(temp_db):
    with db.session() as connection:
        connection.execute("SELECT 1")

    assert temp_db.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda p: str(p))
def test_no_dangerous_constructs(path):
    """No eval, exec, pickle, shell execution or unsafe YAML."""
    source = path.read_text(encoding="utf-8")

    for pattern in (r"\beval\(", r"\bexec\(", r"\bpickle\b",
                    r"\bsubprocess\b", r"os\.system", r"shell\s*=\s*True",
                    r"yaml\.load\s*\(", r"__import__\("):
        assert not re.search(pattern, source), f"{pattern} in {path}"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_a_commit_sha(workflow):
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))

    for job in document["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses")

            if not uses:
                continue

            assert "@" in uses, uses
            ref = uses.split("@", 1)[1]
            assert re.fullmatch(r"[0-9a-f]{40}", ref), \
                f"{uses} is not pinned to a full commit SHA"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_untrusted_interpolation_in_shell_steps(workflow):
    """A workflow input interpolated into a script runs commands.

    '--topic ${{ inputs.topic }}' with a quote in the value executes
    whatever follows it on the runner.
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    trusted = ("github.run_id", "github.repository", "secrets.")

    for job in document["jobs"].values():
        for step in job["steps"]:
            script = step.get("run", "")

            for expression in re.findall(r"\$\{\{\s*([^}]+?)\s*\}\}",
                                         script):
                assert expression.startswith(trusted), (
                    f"{workflow.name}: {expression} is interpolated into a "
                    "script; pass it through env instead"
                )


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_permissions_are_declared_and_minimal(workflow):
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))

    assert document.get("permissions") == {"contents": "read"}

    for job in document["jobs"].values():
        permissions = job.get("permissions")

        if permissions is None:
            continue

        # Only write access to contents is ever needed, for the state
        # branch; nothing may grant packages, id-token or actions write.
        assert set(permissions) <= {"contents"}, permissions


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_pull_request_target(workflow):
    """It runs untrusted pull request code with access to secrets."""
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    triggers = document.get(True) or document.get("on")

    assert "pull_request_target" not in triggers


def test_state_push_targets_only_the_state_branch():
    for workflow in WORKFLOWS:
        source = workflow.read_text(encoding="utf-8")

        for line in source.splitlines():
            stripped = line.strip()

            if not stripped.startswith("git push"):
                continue

            assert ("HEAD:state" in stripped
                    or "HEAD:gh-pages" in stripped), stripped


def test_the_community_agent_cannot_post():
    """Structural guarantee: no outbound write path exists in it."""
    source = Path("agents/community/main.py").read_text(encoding="utf-8")

    for forbidden in ("urlopen", "net.fetch", "net.iter_chunks",
                      "requests.", "POST", "oauth"):
        assert forbidden not in source, forbidden


def test_every_outbound_request_goes_through_sge_net():
    """One place enforces timeouts, size caps and the SSRF guard."""
    for path in PYTHON_FILES:
        if path.name == "net.py":
            continue

        source = path.read_text(encoding="utf-8")

        for forbidden in ("urllib.request.urlopen", "http.client",
                          "requests.get", "socket.create_connection"):
            assert forbidden not in source, f"{forbidden} in {path}"


def test_secrets_are_never_written_to_the_database():
    """No agent stores an environment value that looks like a credential."""
    for path in PYTHON_FILES:
        source = path.read_text(encoding="utf-8")

        assert "GITHUB_TOKEN" not in source or path.name == "github.py", \
            f"{path} references a token directly"


def test_gitignore_covers_state_and_credentials():
    ignored = Path(".gitignore").read_text(encoding="utf-8")

    for pattern in (".env", "*.db", "logs/", "*.pem", "*.key"):
        assert pattern in ignored, pattern
