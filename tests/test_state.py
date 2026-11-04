"""Portable state: the file a temporary runner carries between runs."""

import json

import pytest

from sge import db, state


def insert_item(connection, index, **overrides):
    row = {
        "source": "Test source",
        "title": f"Item {index}",
        "link": f"https://example.test/{index}?utm_source=rss",
        "canonical_link": f"https://example.test/{index}",
        "published": "2026-11-01",
        "summary": "A long body that state deliberately does not carry. " * 20,
        "topic": "advisories",
        "relevance": 70,
        "excluded": 0,
        "fetched_at": "2026-11-01 10:00:00",
    }
    row.update(overrides)

    connection.execute(
        """
        INSERT INTO research_items
            (source, title, link, canonical_link, published, summary,
             topic, relevance, excluded, fetched_at)
        VALUES (:source, :title, :link, :canonical_link, :published,
                :summary, :topic, :relevance, :excluded, :fetched_at)
        """,
        row,
    )


def test_export_then_import_restores_the_keys(temp_db):
    with db.session() as connection:
        for index in range(3):
            insert_item(connection, index)
        connection.commit()

        text, count = state.export_state(connection)

    assert count == 3

    with db.session(temp_db.parent / "fresh.db") as other:
        db.migrate(other)
        imported, skipped = state.import_state(other, text)
        rows = other.execute(
            "SELECT canonical_link, relevance, topic FROM research_items "
            "ORDER BY canonical_link"
        ).fetchall()

    assert (imported, skipped) == (3, 0)
    assert [row["canonical_link"] for row in rows] == [
        f"https://example.test/{index}" for index in range(3)
    ]
    assert rows[0]["relevance"] == 70
    assert rows[0]["topic"] == "advisories"


def test_import_is_idempotent(temp_db):
    with db.session() as connection:
        insert_item(connection, 1)
        connection.commit()
        text, _ = state.export_state(connection)

        first = state.import_state(connection, text)
        second = state.import_state(connection, text)
        connection.commit()

        total = connection.execute(
            "SELECT COUNT(*) AS n FROM research_items"
        ).fetchone()["n"]

    assert first == (0, 1)
    assert second == (0, 1)
    assert total == 1


def test_imported_state_prevents_recollection(temp_db):
    """The whole point: a fresh runner must not call old items new."""
    with db.session() as connection:
        insert_item(connection, 1)
        connection.commit()
        text, _ = state.export_state(connection)

    with db.session(temp_db.parent / "runner.db") as runner:
        db.migrate(runner)
        state.import_state(runner, text)

        # The collector would insert exactly this row again.
        cursor = runner.execute(
            """
            INSERT OR IGNORE INTO research_items
                (source, title, link, canonical_link)
            VALUES ('Test source', 'Item 1',
                    'https://example.test/1?ref=newsletter',
                    'https://example.test/1')
            """
        )

    assert cursor.rowcount == 0


def test_old_summaries_are_not_exported(temp_db):
    """Only recent text travels: old bodies are dead weight."""
    with db.session() as connection:
        insert_item(connection, 1, fetched_at="2020-01-01 00:00:00")
        connection.commit()
        text, _ = state.export_state(connection, keep_days=0)

    assert "state deliberately does not carry" not in text
    assert "summary" not in text


def test_rows_are_sorted_so_the_diff_stays_small(temp_db):
    with db.session() as connection:
        for index in (5, 1, 3):
            insert_item(connection, index)
        connection.commit()
        text, _ = state.export_state(connection)

    links = [
        json.loads(line)["canonical_link"]
        for line in text.splitlines()
        if '"research_item"' in line
    ]

    assert links == sorted(links)


def test_old_items_are_dropped_by_keep_days(temp_db):
    with db.session() as connection:
        insert_item(connection, 1, fetched_at="2020-01-01 00:00:00")
        insert_item(connection, 2, fetched_at="2026-11-01 00:00:00")
        connection.commit()

        _text, kept = state.export_state(connection, keep_days=365)
        _all_text, everything = state.export_state(connection, keep_days=0)

    assert kept == 1
    assert everything == 2


def test_excluded_flag_survives_a_round_trip(temp_db):
    with db.session() as connection:
        insert_item(connection, 1, excluded=1)
        connection.commit()
        text, _ = state.export_state(connection)

    with db.session(temp_db.parent / "x.db") as other:
        db.migrate(other)
        state.import_state(other, text)
        excluded = other.execute(
            "SELECT excluded FROM research_items"
        ).fetchone()["excluded"]

    assert excluded == 1


def test_malformed_line_is_rejected_with_its_number(temp_db):
    with db.session() as connection:
        with pytest.raises(state.StateError, match="Line 2"):
            state.import_state(
                connection,
                '{"v": 1, "type": "meta"}\nnot json at all\n',
            )


def test_future_format_version_is_refused(temp_db):
    with db.session() as connection:
        with pytest.raises(state.StateError, match="version"):
            state.import_state(connection, '{"v": 99, "type": "meta"}\n')


def test_records_missing_keys_are_refused(temp_db):
    with db.session() as connection:
        with pytest.raises(state.StateError, match="canonical_link"):
            state.import_state(
                connection,
                '{"v": 1, "type": "meta"}\n'
                '{"type": "research_item", "source": "s", "title": "t",'
                ' "link": "https://a.test/x"}\n',
            )


def test_unknown_record_types_are_ignored(temp_db):
    """Forward compatibility: a newer exporter must not break us."""
    with db.session() as connection:
        imported, _ = state.import_state(
            connection,
            '{"v": 1, "type": "meta"}\n'
            '{"type": "something_from_the_future", "data": 1}\n',
        )

    assert imported == 0


def test_empty_state_is_valid(temp_db):
    with db.session() as connection:
        assert state.import_state(connection, "") == (0, 0)
        assert state.import_state(connection, "\n\n") == (0, 0)


def test_two_exports_of_unchanged_data_are_identical(temp_db):
    """Otherwise the workflow commits an empty state change every run."""
    with db.session() as connection:
        insert_item(connection, 1)
        connection.commit()

        first, _ = state.export_state(connection)
        second, _ = state.export_state(connection)

    assert first == second


def test_header_carries_no_timestamp(temp_db):
    with db.session() as connection:
        insert_item(connection, 1)
        connection.commit()
        text, _ = state.export_state(connection)

    header = json.loads(text.splitlines()[0])

    assert header == {"v": state.FORMAT_VERSION, "type": "meta", "items": 1}


def test_recent_text_travels_but_old_text_does_not(temp_db):
    """A runner needs text to build a digest; old text is dead weight."""
    import datetime as dt

    today = dt.datetime.now(dt.timezone.utc)
    recent = today.strftime("%Y-%m-%d %H:%M:%S")
    old = (today - dt.timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")

    with db.session() as connection:
        insert_item(connection, 1, fetched_at=recent,
                    summary="Fresh article text.")
        insert_item(connection, 2, fetched_at=old,
                    summary="Stale article text.")
        connection.commit()

        text, _ = state.export_state(connection, keep_days=0, text_days=30)

    assert "Fresh article text." in text
    assert "Stale article text." not in text


def test_text_days_zero_carries_every_summary(temp_db):
    with db.session() as connection:
        insert_item(connection, 1, fetched_at="2020-01-01 00:00:00",
                    summary="Ancient text.")
        connection.commit()

        text, _ = state.export_state(connection, keep_days=0, text_days=0)

    assert "Ancient text." in text


def test_summaries_round_trip_so_a_runner_can_draft(temp_db):
    with db.session() as connection:
        insert_item(connection, 1, summary="A CRLF desync in a scanner.")
        connection.commit()
        text, _ = state.export_state(connection)

    with db.session(temp_db.parent / "runner.db") as runner:
        db.migrate(runner)
        state.import_state(runner, text)
        summary = runner.execute(
            "SELECT summary FROM research_items"
        ).fetchone()["summary"]

    assert summary == "A CRLF desync in a scanner."


def test_stored_summaries_are_capped(temp_db):
    with db.session() as connection:
        insert_item(connection, 1, summary="x" * 5000)
        connection.commit()
        text, _ = state.export_state(connection)

    record = json.loads(text.splitlines()[1])

    assert len(record["summary"]) == state.MAX_STORED_SUMMARY


def test_version_one_state_is_still_readable(temp_db):
    """Older state has no summary field; accepting it loses nothing."""
    with db.session() as connection:
        imported, _ = state.import_state(
            connection,
            '{"v": 1, "type": "meta"}\n'
            '{"type": "research_item", "source": "s", "title": "t",'
            ' "link": "https://a.test/x",'
            ' "canonical_link": "https://a.test/x"}\n',
        )
        connection.commit()

        summary = connection.execute(
            "SELECT summary FROM research_items"
        ).fetchone()["summary"]

    assert imported == 1
    assert summary is None


def test_the_header_declares_the_current_version(temp_db):
    with db.session() as connection:
        text, _ = state.export_state(connection)

    assert json.loads(text.splitlines()[0])["v"] == state.FORMAT_VERSION
