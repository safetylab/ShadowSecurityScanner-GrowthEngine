import pytest

from sge import feeds


def test_rss_entries_are_extracted(rss_feed):
    entries = feeds.parse_bytes(rss_feed)

    assert len(entries) == 2
    assert entries[0]["title"].startswith("First advisory")
    assert entries[0]["link"] == "https://example.test/a?utm_source=rss"
    assert "CVE" in entries[0]["summary"]


def test_atom_entries_use_href_links(atom_feed):
    entries = feeds.parse_bytes(atom_feed)

    assert len(entries) == 1
    assert entries[0]["link"] == "https://example.test/releases/v1.2.0"
    assert entries[0]["published"] == "2026-11-01T08:00:00Z"


def test_empty_feed_returns_no_entries():
    document = b"<rss version='2.0'><channel><title>x</title></channel></rss>"

    assert feeds.parse_bytes(document) == []


def test_entries_missing_required_fields_are_skipped():
    document = (
        b"<rss><channel>"
        b"<item><title>No link here</title></item>"
        b"<item><link>https://example.test/no-title</link></item>"
        b"<item><title>Complete</title>"
        b"<link>https://example.test/ok</link></item>"
        b"</channel></rss>"
    )

    entries = feeds.parse_bytes(document)

    assert [entry["title"] for entry in entries] == ["Complete"]


def test_malformed_xml_raises_feed_error():
    with pytest.raises(feeds.FeedError):
        feeds.parse_bytes(b"<rss><channel><item><title>truncated")


def test_not_xml_at_all_raises_feed_error():
    with pytest.raises(feeds.FeedError):
        feeds.parse_bytes(b"<html><body>404 Not Found</body></html>")


def test_doctype_is_refused_before_parsing():
    billion_laughs = (
        b"<?xml version='1.0'?>"
        b"<!DOCTYPE lolz [<!ENTITY lol 'lol'>"
        b"<!ENTITY lol2 '&lol;&lol;&lol;&lol;'>]>"
        b"<rss><channel><item><title>&lol2;</title>"
        b"<link>https://example.test/x</link></item></channel></rss>"
    )

    with pytest.raises(feeds.UnsafeFeed):
        feeds.parse_bytes(billion_laughs)


def test_external_entity_reference_does_not_read_local_files(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")

    document = (
        f"<?xml version='1.0'?>"
        f"<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file://{secret}'>]>"
        f"<rss><channel><item><title>&xxe;</title>"
        f"<link>https://example.test/x</link></item></channel></rss>"
    ).encode()

    with pytest.raises(feeds.FeedError):
        feeds.parse_bytes(document)


def test_max_items_stops_parsing_early():
    items = b"".join(
        f"<item><title>Item {index}</title>"
        f"<link>https://example.test/{index}</link></item>".encode()
        for index in range(50)
    )
    document = b"<rss><channel>" + items + b"</channel></rss>"

    assert len(feeds.parse_bytes(document, max_items=5)) == 5


def test_truncated_tail_keeps_entries_already_parsed():
    """A size cap cuts the document mid-stream; earlier entries stand."""
    document = (
        b"<rss><channel>"
        b"<item><title>Kept</title>"
        b"<link>https://example.test/kept</link></item>"
        b"<item><title>Cut off here"
    )

    entries = feeds.parse_bytes(document)

    assert [entry["title"] for entry in entries] == ["Kept"]


def test_long_fields_are_truncated():
    long_title = "A" * 5000
    document = (
        f"<rss><channel><item><title>{long_title}</title>"
        f"<link>https://example.test/x</link></item></channel></rss>"
    ).encode()

    entry = feeds.parse_bytes(document)[0]

    assert len(entry["title"]) == feeds.MAX_TITLE


def test_escaped_entities_in_titles_are_decoded():
    """Feeds escape their own markup; a title must not keep &nbsp;."""
    document = (
        b"<rss><channel><item>"
        b"<title>The Fragile Lock:&amp;nbsp;Novel Bypasses &amp;amp; More"
        b"</title>"
        b"<link>https://example.test/x</link></item></channel></rss>"
    )

    entry = feeds.parse_bytes(document)[0]

    assert entry["title"] == "The Fragile Lock: Novel Bypasses & More"


def test_whitespace_in_titles_is_collapsed():
    document = (
        b"<rss><channel><item><title>  Spread\n   over   lines  </title>"
        b"<link>https://example.test/x</link></item></channel></rss>"
    )

    assert feeds.parse_bytes(document)[0]["title"] == "Spread over lines"


def test_non_breaking_spaces_become_ordinary_spaces():
    document = (
        "<rss><channel><item><title>A B</title>"
        "<link>https://example.test/x</link></item></channel></rss>"
    ).encode()

    assert feeds.parse_bytes(document)[0]["title"] == "A B"
