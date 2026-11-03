"""Controller: idempotent queue, audit trail, migrations."""

import importlib
import json

import pytest

from sge import db

controller = importlib.import_module("agents.controller.main")


def run(argv):
    return controller.main(argv)


def test_init_applies_all_migrations(tmp_path, monkeypatch):
    monkeypatch.setenv("SGE_DB_PATH", str(tmp_path / "fresh.db"))

    assert run(["init"]) == 0

    with db.session() as connection:
        applied = [
            row["filename"]
            for row in connection.execute(
                "SELECT filename FROM schema_migrations ORDER BY filename"
            )
        ]

    assert applied == ["001_core.sql", "002_research.sql", "003_content.sql"]


def test_init_is_repeatable(temp_db):
    assert run(["init"]) == 0
    assert run(["init"]) == 0


def test_add_creates_a_task(temp_db):
    assert run(["add", "--key", "k1", "--agent", "research",
                "--type", "collect"]) == 0

    with db.session() as connection:
        row = connection.execute(
            "SELECT agent, task_type, status FROM tasks WHERE task_key = 'k1'"
        ).fetchone()

    assert row["agent"] == "research"
    assert row["status"] == "pending"


def test_adding_the_same_key_twice_does_not_duplicate(temp_db):
    run(["add", "--key", "dup", "--agent", "research", "--type", "collect"])
    assert run(["add", "--key", "dup", "--agent", "research",
                "--type", "collect"]) == 0

    with db.session() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE task_key = 'dup'"
        ).fetchone()["n"]
        events = connection.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE task_key = 'dup'"
        ).fetchone()["n"]

    assert count == 1
    # The duplicate attempt must not pollute the audit trail either.
    assert events == 1


@pytest.mark.parametrize("payload", ["{broken", "[]", '"text"', "42"])
def test_invalid_payload_is_rejected(temp_db, payload):
    assert run(["add", "--key", "bad", "--agent", "a", "--type", "t",
                "--payload", payload]) == 2

    with db.session() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM tasks"
        ).fetchone()["n"]

    assert count == 0


def test_payload_round_trips(temp_db):
    run(["add", "--key", "p", "--agent", "a", "--type", "t",
         "--payload", '{"source": "CISA", "limit": 5}'])

    with db.session() as connection:
        stored = connection.execute(
            "SELECT payload FROM tasks WHERE task_key = 'p'"
        ).fetchone()["payload"]

    assert json.loads(stored) == {"source": "CISA", "limit": 5}


def test_set_status_updates_and_counts_failures(temp_db):
    run(["add", "--key", "s", "--agent", "a", "--type", "t"])

    assert run(["set-status", "--key", "s", "--status", "failed",
                "--error", "source timed out"]) == 0

    with db.session() as connection:
        row = connection.execute(
            "SELECT status, attempts, last_error FROM tasks "
            "WHERE task_key = 's'"
        ).fetchone()

    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert row["last_error"] == "source timed out"


def test_set_status_on_unknown_task_fails(temp_db):
    assert run(["set-status", "--key", "nope", "--status",
                "completed"]) == 1


def test_invalid_status_is_rejected_by_the_parser(temp_db):
    with pytest.raises(SystemExit):
        run(["set-status", "--key", "s", "--status", "weird"])


def test_list_filters_by_status(temp_db, capsys):
    run(["add", "--key", "a1", "--agent", "a", "--type", "t"])
    run(["add", "--key", "a2", "--agent", "a", "--type", "t"])
    run(["set-status", "--key", "a2", "--status", "completed"])
    capsys.readouterr()

    run(["list", "--status", "pending"])
    output = capsys.readouterr().out

    assert "a1" in output
    assert "a2" not in output


def test_config_check_passes_on_the_repository_configuration(temp_db):
    assert run(["config-check"]) == 0


def test_stats_reports_every_table(temp_db, capsys):
    run(["add", "--key", "x", "--agent", "a", "--type", "t"])
    capsys.readouterr()

    run(["stats"])
    output = capsys.readouterr().out

    assert "Research items: 0" in output
    assert "Content items:  0" in output
