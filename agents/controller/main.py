"""Shadow Growth Engine Controller.

Owns the task queue, the audit trail and the database schema. Other
agents ask the controller for work and report back; nothing else writes
to the tasks table.

Usage:
    python agents/controller/main.py init
    python agents/controller/main.py add --key k --agent research --type collect
    python agents/controller/main.py list [--status pending]
    python agents/controller/main.py set-status --key k --status completed
    python agents/controller/main.py config-check
    python agents/controller/main.py stats
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sge import config, db  # noqa: E402
from sge.logging_setup import get_logger  # noqa: E402

AGENT = "controller"
STATUSES = ("pending", "running", "completed", "failed", "blocked")

log = get_logger("sge.controller")


def command_init(_args):
    path, applied = db.initialize()

    if applied:
        log.info("Applied migrations: %s", ", ".join(applied))
    else:
        log.info("Database already up to date")

    log.info("Database ready: %s", path)
    return 0


def command_add(args):
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as error:
        log.error("Invalid JSON payload: %s", error)
        return 2

    if not isinstance(payload, dict):
        log.error("Payload must be a JSON object")
        return 2

    with db.session() as connection:
        db.migrate(connection)

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO tasks (task_key, agent, task_type, payload)
            VALUES (?, ?, ?, ?)
            """,
            (args.key, args.agent, args.type,
             json.dumps(payload, ensure_ascii=False)),
        )

        created = cursor.rowcount == 1

        if created:
            db.log_event(connection, "task_created", args.agent, args.key,
                         {"task_type": args.type})

        connection.commit()

    if created:
        log.info("Task created: %s", args.key)
        return 0

    log.info("Task already exists, nothing to do: %s", args.key)
    return 0


def command_list(args):
    query = """
        SELECT task_key, agent, task_type, status, attempts, created_at
        FROM tasks
    """
    params = []

    if args.status:
        query += " WHERE status = ?"
        params.append(args.status)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(args.limit)

    with db.session() as connection:
        db.migrate(connection)
        rows = connection.execute(query, params).fetchall()

    if not rows:
        print("No tasks found.")
        return 0

    for row in rows:
        print(
            f"{row['task_key']} | {row['agent']} | {row['task_type']} | "
            f"{row['status']} | attempts={row['attempts']} | "
            f"{row['created_at']}"
        )

    return 0


def command_set_status(args):
    with db.session() as connection:
        db.migrate(connection)

        cursor = connection.execute(
            """
            UPDATE tasks
            SET status = ?,
                last_error = ?,
                attempts = attempts + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_key = ?
            """,
            (args.status, args.error, 1 if args.status == "failed" else 0,
             args.key),
        )

        if cursor.rowcount == 0:
            connection.commit()
            log.error("Unknown task: %s", args.key)
            return 1

        db.log_event(connection, "task_status_changed", AGENT, args.key,
                     {"status": args.status})
        connection.commit()

    log.info("Task %s is now %s", args.key, args.status)
    return 0


def command_log(args):
    with db.session() as connection:
        db.migrate(connection)
        db.log_event(connection, args.event, args.agent, args.key, {})
        connection.commit()

    log.info("Event recorded: %s", args.event)
    return 0


def command_stats(_args):
    with db.session() as connection:
        db.migrate(connection)

        tasks = connection.execute(
            "SELECT status, COUNT(*) AS total FROM tasks GROUP BY status"
        ).fetchall()
        research = connection.execute(
            "SELECT COUNT(*) AS total FROM research_items"
        ).fetchone()
        drafts = connection.execute(
            "SELECT COUNT(*) AS total FROM content_items"
        ).fetchone()
        events = connection.execute(
            "SELECT COUNT(*) AS total FROM audit_log"
        ).fetchone()

    print("Tasks:")
    if tasks:
        for row in tasks:
            print(f"  {row['status']}: {row['total']}")
    else:
        print("  none")

    print(f"Research items: {research['total']}")
    print(f"Content items:  {drafts['total']}")
    print(f"Audit events:   {events['total']}")
    return 0


def command_config_check(_args):
    results = config.validate_all()
    failed = 0

    for name, error in sorted(results.items()):
        if error is None:
            print(f"ok    {name}")
        else:
            failed += 1
            print(f"FAIL  {name}: {error}")

    if failed:
        log.error("Configuration problems: %d", failed)
        return 1

    log.info("All configuration files are valid")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Shadow Growth Engine Controller"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create or upgrade the database")

    add = commands.add_parser("add", help="queue a task (idempotent)")
    add.add_argument("--key", required=True, help="idempotency key")
    add.add_argument("--agent", required=True)
    add.add_argument("--type", required=True)
    add.add_argument("--payload", default="{}", help="JSON object")

    listing = commands.add_parser("list", help="show queued tasks")
    listing.add_argument("--status", choices=STATUSES)
    listing.add_argument("--limit", type=int, default=50)

    status = commands.add_parser("set-status", help="update a task status")
    status.add_argument("--key", required=True)
    status.add_argument("--status", required=True, choices=STATUSES)
    status.add_argument("--error", default=None)

    event = commands.add_parser("log", help="append an audit event")
    event.add_argument("--event", required=True)
    event.add_argument("--agent", default=None)
    event.add_argument("--key", default=None)

    commands.add_parser("stats", help="counters across all tables")
    commands.add_parser("config-check", help="validate configuration files")

    return parser


HANDLERS = {
    "init": command_init,
    "add": command_add,
    "list": command_list,
    "set-status": command_set_status,
    "log": command_log,
    "stats": command_stats,
    "config-check": command_config_check,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    return HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
