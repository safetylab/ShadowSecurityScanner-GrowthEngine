"""Relevance scoring and topic classification for research items.

Deliberately a transparent keyword heuristic: the score explains itself,
runs offline and costs nothing, which matters because every item we act
on later must be traceable back to why it was picked.

Two things are kept apart on purpose. The *score* says how close an item
is to what the project is about. The *excluded* flag says it is not
material for a digest — a hiring post or a conference announcement. A
real advisory that happens to mention a webinar keeps its score and is
merely excluded from publication, instead of being scored down to zero.
"""

import re
from functools import lru_cache

from . import config

PRIMARY_WEIGHT = 25
SECONDARY_WEIGHT = 8
TITLE_BONUS = 10
MAX_SCORE = 100

DEFAULT_THRESHOLD = 30


@lru_cache(maxsize=512)
def _matcher(keyword):
    """Word-boundary matcher for a keyword.

    Substring matching produced false positives that mattered:
    "Statistics" matched "ics" and "Dispatch" matched "patch". Phrases
    work the same way, since \b anchors at each end of the phrase.
    """
    return re.compile(rf"\b{re.escape(keyword.lower())}\b")


def _hits(keyword, text):
    return bool(_matcher(keyword).search(text))


def _haystacks(item):
    title = (item.get("title") or "").lower()
    body = " ".join(
        str(item.get(field) or "") for field in ("summary", "link")
    ).lower()

    return title, f"{title} {body}"


def score_item(item, keywords=None, floor=0):
    """Return (score 0..100, matched keywords) for a research item.

    ``floor`` is the baseline a source vouches for: an advisory feed is
    on topic by construction, even when an entry names neither the
    product category nor a CVE — "Siemens SIMATIC S7-1500 CPU" is a real
    advisory whose text matches none of our keywords.
    """
    data = keywords or config.load_keywords()
    title, full = _haystacks(item)

    matched = []
    score = 0

    for keyword in data["primary"]:
        if _hits(keyword, full):
            matched.append(keyword)
            score += PRIMARY_WEIGHT
            if _hits(keyword, title):
                score += TITLE_BONUS

    for keyword in data["secondary"]:
        if _hits(keyword, full):
            matched.append(keyword)
            score += SECONDARY_WEIGHT

    return max(floor, min(MAX_SCORE, score)), matched


def exclusions(item, keywords=None):
    """Negative keywords found in the item: reasons not to publish it."""
    data = keywords or config.load_keywords()
    _title, full = _haystacks(item)

    return [
        keyword for keyword in data["negative"]
        if _hits(keyword, full)
    ]


def classify_item(item, keywords=None):
    """Return the topic with the most keyword hits, or 'general'."""
    data = keywords or config.load_keywords()
    _title, full = _haystacks(item)

    best_topic = "general"
    best_hits = 0

    for topic, terms in data["topics"].items():
        hits = sum(1 for term in terms if _hits(str(term), full))

        if hits > best_hits:
            best_topic = topic
            best_hits = hits

    return best_topic


def evaluate(item, keywords=None, floor=0):
    """Score, classify and decide publishability in one pass."""
    data = keywords or config.load_keywords()
    score, matched = score_item(item, data, floor)
    excluded = exclusions(item, data)

    # An item we scored down to zero is not worth a topic label: keeping
    # one would put off-topic noise in topic reports.
    topic = classify_item(item, data) if score else "general"

    return {
        "relevance": score,
        "topic": topic,
        "matched": matched,
        "excluded": bool(excluded),
        "exclusion_reasons": excluded,
    }
