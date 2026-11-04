"""SQLite access and migrations.

The engine is designed for a single sequential writer (see the state
handling notes in the implementation plan): WAL plus a long busy timeout
protects us from the occasional overlap, nothing more.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import paths

BUSY_TIMEOUT_MS = 30_000

OWNER_ONLY = 0o600


def _restrict(path):
    """Keep the database readable by its owner only.

    It holds no secrets by policy, but it does hold everything the
    engine collected, and the default 0644 shares that with every local
    account for no reason.
    """
    try:
        if path.exists() and (path.stat().st_mode & 0o777) != OWNER_ONLY:
            path.chmod(OWNER_ONLY)
    except OSError:
        pass


MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def connect(db_file=None):
    """Open a connection with the engine's standard pragmas."""
    target = Path(db_file) if db_file else paths.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(target, timeout=BUSY_TIMEOUT_MS / 1000)
    _restrict(target)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


@contextmanager
def session(db_file=None):
    """Connection context manager that actually closes the connection."""
    connection = connect(db_file)

    try:
        yield connection
    finally:
        connection.close()


def migrate(connection, migrations_dir=None):
    """Apply pending migrations in filename order; return applied names."""
    directory = Path(migrations_dir) if migrations_dir else paths.MIGRATIONS_DIR

    if not directory.is_dir():
        raise FileNotFoundError(f"Migrations directory missing: {directory}")

    connection.execute(MIGRATIONS_TABLE)
    connection.commit()

    done = {
        row["filename"]
        for row in connection.execute("SELECT filename FROM schema_migrations")
    }

    applied = []

    for migration in sorted(directory.glob("*.sql")):
        if migration.name in done:
            continue

        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations (filename) VALUES (?)",
            (migration.name,),
        )
        connection.commit()
        applied.append(migration.name)

    return applied


def initialize(db_file=None, migrations_dir=None):
    """Create or upgrade the database; return (path, applied migrations)."""
    target = Path(db_file) if db_file else paths.db_path()

    with session(target) as connection:
        applied = migrate(connection, migrations_dir)

    return target, applied


def log_event(connection, event, agent=None, task_key=None, details=None):
    """Append an audit record. Never pass secrets in details."""
    import json

    connection.execute(
        """
        INSERT INTO audit_log (event, agent, task_key, details)
        VALUES (?, ?, ?, ?)
        """,
        (event, agent, task_key, json.dumps(details or {}, ensure_ascii=False)),
    )
