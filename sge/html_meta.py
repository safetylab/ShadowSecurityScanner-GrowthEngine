"""Minimal HTML metadata extraction.

Uses html.parser from the standard library: the pages we audit are our
own, we need a handful of head elements and link targets, and a parser
dependency would buy nothing here.
"""

from html.parser import HTMLParser

MAX_LINKS = 500


class PageParser(HTMLParser):
    """Collects the head metadata and links an SEO audit needs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta = {}
        self.canonical = ""
        self.links = []
        self.headings = []
        self.images_without_alt = 0
        self.images = 0
        self._in_title = False
        self._in_heading = None

    def handle_starttag(self, tag, attrs):
        attributes = {key.lower(): (value or "") for key, value in attrs}

        if tag == "title":
            self._in_title = True

        elif tag == "meta":
            name = (attributes.get("name")
                    or attributes.get("property")
                    or "").lower()
            if name:
                self.meta[name] = attributes.get("content", "").strip()

        elif tag == "link":
            rels = (attributes.get("rel") or "").lower().split()
            if "canonical" in rels:
                self.canonical = attributes.get("href", "").strip()

        elif tag == "a":
            href = attributes.get("href", "").strip()
            if href and len(self.links) < MAX_LINKS:
                self.links.append(href)

        elif tag == "img":
            self.images += 1
            if not attributes.get("alt", "").strip():
                self.images_without_alt += 1

        elif tag in ("h1", "h2", "h3"):
            self._in_heading = tag

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == self._in_heading:
            self._in_heading = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()
        elif self._in_heading:
            text = data.strip()
            if text:
                self.headings.append((self._in_heading, text))


def parse_page(html):
    """Return the metadata of one HTML document."""
    parser = PageParser()
    parser.feed(html)
    parser.close()

    return {
        "title": parser.title.strip(),
        "meta": parser.meta,
        "canonical": parser.canonical,
        "links": parser.links,
        "headings": parser.headings,
        "images": parser.images,
        "images_without_alt": parser.images_without_alt,
    }
