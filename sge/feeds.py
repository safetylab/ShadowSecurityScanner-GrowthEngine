"""Incremental RSS/Atom parsing.

Feeds are parsed as they arrive and parsing stops once enough entries
have been seen, so a multi-megabyte advisory feed costs us only its
first entries instead of the whole document.
"""

import html
import re
import xml.etree.ElementTree as ET

from . import text as text_util

MAX_ITEMS = 30
MAX_TITLE = 500
MAX_LINK = 2000
MAX_SUMMARY = 2000
MAX_PUBLISHED = 100

ENTRY_TAGS = ("item", "entry")
# RSS 2.0, Atom and RSS 1.0 respectively. Anything else is not a feed —
# typically an HTML error page served with status 200.
ROOT_TAGS = ("rss", "feed", "rdf")
TITLE_TAGS = {"title"}
PUBLISHED_TAGS = ("pubdate", "published", "updated", "date")
SUMMARY_TAGS = ("description", "summary", "content", "encoded")

# A DTD in a syndication feed has no legitimate use here and is the entry
# point for entity-expansion attacks, so documents declaring one are rejected.
DOCTYPE_PATTERN = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)
DOCTYPE_SCAN_BYTES = 8192


class FeedError(Exception):
    """The document could not be parsed as a feed."""


class UnsafeFeed(FeedError):
    """The document contains constructs we refuse to parse."""


class NotAFeed(FeedError):
    """The document parsed, but its root element is not a feed."""


def local_name(tag):
    """Tag name without its XML namespace, lowercased."""
    return str(tag).rsplit("}", 1)[-1].lower()


def normalise(text):
    """Decode entities and collapse whitespace.

    Feeds routinely escape their own markup, so a title arrives as
    "Lock:&nbsp;Bypasses" — the XML parser hands back the literal
    "&nbsp;" and only unescaping turns it into a space.
    """
    decoded = html.unescape(text).replace("\u00a0", " ")

    return re.sub(r"\s+", " ", decoded).strip()


def _text_of(element, names):
    for child in element:
        if local_name(child.tag) in names:
            return normalise("".join(child.itertext()))

    return ""


def _link_of(element):
    """Entry link: RSS puts it in text, Atom in a href attribute."""
    best = ""

    for child in element:
        if local_name(child.tag) != "link":
            continue

        href = (child.attrib.get("href") or "").strip()
        text = "".join(child.itertext()).strip()
        rel = (child.attrib.get("rel") or "alternate").lower()
        candidate = href or text

        if not candidate:
            continue

        if rel == "alternate":
            return candidate

        best = best or candidate

    if best:
        return best

    return _text_of(element, {"guid", "id"})


def build_entry(element):
    """Extract one entry, or None when required fields are missing."""
    title = _text_of(element, TITLE_TAGS)
    link = _link_of(element)

    if not title or not link:
        return None

    # Markup comes out before the length limit is applied: truncating
    # first leaves a half-written tag that nothing can strip later.
    summary = text_util.plain(_text_of(element, set(SUMMARY_TAGS)))

    return {
        "title": title[:MAX_TITLE],
        "link": link[:MAX_LINK],
        "published": _text_of(element, set(PUBLISHED_TAGS))[:MAX_PUBLISHED],
        "summary": summary[:MAX_SUMMARY],
    }


def parse_stream(chunks, max_items=MAX_ITEMS):
    """Parse entries from an iterable of byte chunks.

    A parse error raised while entries are still unknown is fatal; once
    entries have been collected a truncated tail only ends collection,
    because an early stop is the normal outcome of hitting max_items.
    """
    parser = ET.XMLPullParser(["start", "end"])
    entries = []
    head = b""
    checked_doctype = False
    root_checked = False

    for chunk in chunks:
        if not checked_doctype:
            head += chunk
            if DOCTYPE_PATTERN.search(head[:DOCTYPE_SCAN_BYTES]):
                raise UnsafeFeed("Feed declares a DTD or entity")
            checked_doctype = len(head) >= DOCTYPE_SCAN_BYTES

        try:
            parser.feed(chunk)
        except ET.ParseError as error:
            if entries:
                return entries
            raise FeedError(f"Invalid XML: {error}") from error

        for event, element in parser.read_events():
            if event == "start":
                if not root_checked:
                    root_checked = True
                    if local_name(element.tag) not in ROOT_TAGS:
                        raise NotAFeed(
                            f"Root element is <{local_name(element.tag)}>, "
                            "not a syndication feed"
                        )
                continue

            if local_name(element.tag) not in ENTRY_TAGS:
                continue

            entry = build_entry(element)
            element.clear()

            if entry:
                entries.append(entry)

            if len(entries) >= max_items:
                return entries

    try:
        parser.close()
    except ET.ParseError as error:
        if not entries:
            raise FeedError(f"Invalid XML: {error}") from error

    return entries


def parse_bytes(data, max_items=MAX_ITEMS):
    """Convenience wrapper for an in-memory document."""
    return parse_stream([data], max_items=max_items)
