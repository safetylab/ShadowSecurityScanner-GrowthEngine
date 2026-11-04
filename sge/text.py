"""Turning feed markup into plain text.

Feed summaries arrive as HTML: the ZAP release notes are a full table,
which left words like "tbody" and "href" in the database and fed them
to relevance scoring as if they were subject matter.
"""

import html
import re

TAG = re.compile(r"<[^>]+>")
# A summary truncated to a length limit can end mid-tag, leaving
# '<a href="https://…' with no closing bracket for TAG to match.
DANGLING_TAG = re.compile(r"<[^>]*$")
WHITESPACE = re.compile(r"\s+")


def plain(text, limit=None):
    """Plain text from a fragment of HTML.

    Entities are decoded before tags are stripped: a doubly escaped
    "&amp;lt;script&amp;gt;" would otherwise turn back into a tag after
    the tags were already gone.
    """
    if not text:
        return ""

    decoded = html.unescape(str(text))
    stripped = DANGLING_TAG.sub(" ", TAG.sub(" ", decoded))
    collapsed = WHITESPACE.sub(" ", stripped.replace(" ", " ")).strip()

    if limit and len(collapsed) > limit:
        return collapsed[:limit].rsplit(" ", 1)[0] + "…"

    return collapsed


def as_int(value, default=0):
    """Integer from untrusted data, falling back instead of raising.

    Public APIs do change shape. int("many") raises, which would end a
    whole scan over a comment count, so a value that will not convert
    becomes the default. This is only for metadata where a wrong number
    is cosmetic — never for anything a decision is made on.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
