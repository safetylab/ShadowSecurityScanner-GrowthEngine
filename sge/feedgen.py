"""RSS 2.0 generation for our own channel.

Built with xml.etree so escaping is the library's problem, not ours: a
title containing & or < must not be able to break the feed.
"""

import datetime as dt
import xml.etree.ElementTree as ET

RFC822 = "%a, %d %b %Y %H:%M:%S +0000"


def rfc822(value):
    """Format a date for RSS. Accepts a datetime or an ISO-ish string."""
    if isinstance(value, dt.datetime):
        moment = value
    else:
        text = str(value or "").strip()

        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d"):
            try:
                moment = dt.datetime.strptime(text.replace("Z", ""), pattern)
                break
            except ValueError:
                continue
        else:
            moment = dt.datetime.now(dt.timezone.utc)

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)

    return moment.astimezone(dt.timezone.utc).strftime(RFC822)


def build_feed(channel, items):
    """Return RSS 2.0 XML text.

    ``channel`` needs title, link and description. Each item needs
    title, link and description; date and guid are optional.
    """
    rss = ET.Element("rss", {"version": "2.0"})
    node = ET.SubElement(rss, "channel")

    ET.SubElement(node, "title").text = channel["title"]
    ET.SubElement(node, "link").text = channel["link"]
    ET.SubElement(node, "description").text = channel["description"]
    ET.SubElement(node, "language").text = channel.get("language", "en")
    ET.SubElement(node, "lastBuildDate").text = rfc822(
        channel.get("built_at") or dt.datetime.now(dt.timezone.utc)
    )
    ET.SubElement(node, "generator").text = channel.get(
        "generator", "Shadow Growth Engine"
    )

    for item in items:
        entry = ET.SubElement(node, "item")
        ET.SubElement(entry, "title").text = item["title"]
        ET.SubElement(entry, "link").text = item["link"]
        ET.SubElement(entry, "description").text = item["description"]
        ET.SubElement(entry, "pubDate").text = rfc822(item.get("date"))

        guid = ET.SubElement(entry, "guid")
        guid.text = item.get("guid") or item["link"]
        guid.set("isPermaLink", "true" if guid.text == item["link"]
                 else "false")

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(rss, encoding="unicode"))
