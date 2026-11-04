import datetime as dt

from sge import feedgen, feeds

CHANNEL = {"title": "Test & Co", "link": "https://x.test/",
           "description": "A <feed>"}


def test_feed_is_valid_xml_our_own_parser_reads():
    xml = feedgen.build_feed(CHANNEL, [{
        "title": "A & B <raw>", "link": "https://x.test/a",
        "description": "body", "date": "2026-11-04 10:00:00",
    }])

    entries = feeds.parse_bytes(xml.encode())

    assert entries[0]["title"] == "A & B <raw>"
    assert entries[0]["link"] == "https://x.test/a"


def test_special_characters_are_escaped_not_dropped():
    xml = feedgen.build_feed(CHANNEL, [])

    assert "Test &amp; Co" in xml
    assert "&lt;feed&gt;" in xml
    assert "<feed>" not in xml


def test_guid_marks_whether_it_is_a_permalink():
    xml = feedgen.build_feed(CHANNEL, [
        {"title": "a", "link": "https://x.test/a", "description": "d"},
        {"title": "b", "link": "https://x.test/b", "description": "d",
         "guid": "slug-b"},
    ])

    assert '<guid isPermaLink="true">https://x.test/a</guid>' in xml
    assert '<guid isPermaLink="false">slug-b</guid>' in xml


def test_an_empty_feed_is_still_valid():
    xml = feedgen.build_feed(CHANNEL, [])

    assert feeds.parse_bytes(xml.encode()) == []


def test_dates_are_formatted_as_rfc822():
    assert feedgen.rfc822("2026-11-04 10:00:00") == \
        "Wed, 04 Nov 2026 10:00:00 +0000"
    assert feedgen.rfc822("2026-11-04T10:00:00Z") == \
        "Wed, 04 Nov 2026 10:00:00 +0000"
    assert feedgen.rfc822("2026-11-04") == \
        "Wed, 04 Nov 2026 00:00:00 +0000"


def test_an_unparsable_date_falls_back_to_now_not_a_crash():
    value = feedgen.rfc822("not a date")

    assert value.endswith("+0000")


def test_aware_datetimes_are_converted_to_utc():
    moment = dt.datetime(2026, 11, 4, 12, 0,
                         tzinfo=dt.timezone(dt.timedelta(hours=3)))

    assert feedgen.rfc822(moment) == "Wed, 04 Nov 2026 09:00:00 +0000"
