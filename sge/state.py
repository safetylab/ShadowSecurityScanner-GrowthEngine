"""Portable state for runners that keep nothing.

A GitHub Actions runner is temporary, so growth.db cannot carry
deduplication keys from one run to the next. State is exported as NDJSON
instead: text that git stores as a readable delta, holding the keys that
make a re-run idempotent but not the article bodies, which are only
needed inside the run that collected them.

Rows are written sorted by canonical link, and the file carries no
timestamp of its own, so that two exports of unchanged data produce
byte-identical output: the workflow can then skip the commit instead of
adding an empty one to the state branch every run.
"""

import datetime as dt
import json

FORMAT_VERSION = 1
DEFAULT_KEEP_DAYS = 365

# Everything needed to recognise an item again and to report on it.
# 'summary' is deliberately absent: it is ~85% of the bytes.
ITEM_FIELDS = (
    "source", "title", "link", "canonical_link", "published",
    "topic", "relevance", "excluded", "fetched_at",
)


class StateError(Exception):
    """The state file is unusable."""


def _cutoff(keep_days):
    if not keep_days:
        return None

    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")


def export_state(connection, keep_days=DEFAULT_KEEP_DAYS):
    """Serialise deduplication state as NDJSON text."""
    query = f"""
        SELECT {', '.join(ITEM_FIELDS)}
        FROM research_items
    """
    params = []
    cutoff = _cutoff(keep_days)

    if cutoff:
        query += " WHERE fetched_at >= ?"
        params.append(cutoff)

    query += " ORDER BY canonical_link"

    records = []

    for row in connection.execute(query, params):
        record = {"type": "research_item"}
        record.update({field: row[field] for field in ITEM_FIELDS})
        records.append(json.dumps(record, ensure_ascii=False, sort_keys=True))

    # The header deliberately holds no export time: a timestamp would
    # change on every run and defeat the unchanged-state check.
    header = json.dumps({
        "v": FORMAT_VERSION,
        "type": "meta",
        "items": len(records),
    }, ensure_ascii=False)

    return "\n".join([header] + records) + "\n", len(records)


def import_state(connection, text):
    """Load NDJSON state; returns (imported, skipped_existing).

    Unknown record types are ignored so a newer exporter does not break
    an older importer.
    """
    imported = 0
    skipped = 0

    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()

        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise StateError(f"Line {number}: invalid JSON: {error}") from error

        if not isinstance(record, dict):
            raise StateError(f"Line {number}: expected an object")

        kind = record.get("type")

        if kind == "meta":
            version = record.get("v")

            if version != FORMAT_VERSION:
                raise StateError(
                    f"Unsupported state format version: {version!r}"
                )
            continue

        if kind != "research_item":
            continue

        missing = [
            field for field in ("source", "title", "link", "canonical_link")
            if not record.get(field)
        ]

        if missing:
            raise StateError(
                f"Line {number}: missing {', '.join(missing)}"
            )

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO research_items
                (source, title, link, canonical_link, published, topic,
                 relevance, excluded, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (record["source"], record["title"], record["link"],
             record["canonical_link"], record.get("published"),
             record.get("topic"), int(record.get("relevance") or 0),
             int(record.get("excluded") or 0),
             record.get("fetched_at")
             or dt.datetime.now(dt.timezone.utc).strftime(
                 "%Y-%m-%d %H:%M:%S")),
        )

        if cursor.rowcount:
            imported += 1
        else:
            skipped += 1

    return imported, skipped
