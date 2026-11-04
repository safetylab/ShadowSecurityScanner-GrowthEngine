"""Database layer: migrations are idempotent and ordered."""

import pytest

from sge import db, paths


def test_migrate_applies_once(tmp_path):
    target = tmp_path / "a.db"

    with db.session(target) as connection:
        first = db.migrate(connection, paths.MIGRATIONS_DIR)
        second = db.migrate(connection, paths.MIGRATIONS_DIR)

    assert first == sorted(
        path.name for path in paths.MIGRATIONS_DIR.glob("*.sql")
    )
    assert second == []


def test_migrate_applies_in_filename_order(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "002_second.sql").write_text(
        "CREATE TABLE child (parent INTEGER REFERENCES parent(id));"
    )
    (migrations / "001_first.sql").write_text(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
    )

    with db.session(tmp_path / "b.db") as connection:
        assert db.migrate(connection, migrations) == \
            ["001_first.sql", "002_second.sql"]


def test_missing_migrations_directory_is_an_error(tmp_path):
    with db.session(tmp_path / "c.db") as connection:
        with pytest.raises(FileNotFoundError):
            db.migrate(connection, tmp_path / "absent")


def test_initialize_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "deeper" / "growth.db"

    path, applied = db.initialize(target, paths.MIGRATIONS_DIR)

    assert path.is_file()
    assert applied


def test_foreign_keys_are_enforced(temp_db):
    with db.session() as connection:
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO publications (slug, channel, mode) "
                "VALUES ('no-such-draft', 'reddit', 'draft_only')"
            )
            connection.commit()


def test_task_status_is_constrained(temp_db):
    with db.session() as connection:
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO tasks (task_key, agent, task_type, status) "
                "VALUES ('k', 'a', 't', 'nonsense')"
            )


def test_canonical_link_is_unique(temp_db):
    with db.session() as connection:
        connection.execute(
            "INSERT INTO research_items (source, title, link, canonical_link)"
            " VALUES ('s', 't', 'https://a.test/x', 'https://a.test/x')"
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO research_items "
            "(source, title, link, canonical_link) "
            "VALUES ('other', 't2', 'https://a.test/x?utm_source=z',"
            " 'https://a.test/x')"
        )
        connection.commit()

    assert cursor.rowcount == 0


def test_log_event_stores_json_details(temp_db):
    import json

    with db.session() as connection:
        db.log_event(connection, "test_event", "agent", "key",
                     {"count": 3})
        connection.commit()
        row = connection.execute(
            "SELECT event, details FROM audit_log"
        ).fetchone()

    assert row["event"] == "test_event"
    assert json.loads(row["details"]) == {"count": 3}
