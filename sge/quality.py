"""Quality gate for generated content.

Nothing leaves the drafts directory without passing these checks. They
are intentionally mechanical: they catch missing attribution, leftover
placeholders and the kind of unverifiable marketing claim the project
has committed not to publish.
"""

import re

from . import config, urls

LINK_PATTERN = re.compile(r"https?://[^\s\)\]<>\"']+")
WORD_PATTERN = re.compile(r"\b[\w'-]+\b", re.UNICODE)


class QualityGateError(Exception):
    """Raised when a caller tries to publish content that did not pass."""


def _words(text):
    return WORD_PATTERN.findall(text)


def check(text, sources=None, rules=None, cta_url=None):
    """Evaluate ``text`` against the quality gate.

    Returns a report dict: {"passed": bool, "problems": [...],
    "stats": {...}}. Callers must refuse to publish when passed is False.
    """
    gate = (rules or config.load_publishing_rules())["quality_gate"]
    sources = list(sources or [])
    problems = []

    words = _words(text)
    links = LINK_PATTERN.findall(text)

    if len(words) < gate["min_words"]:
        problems.append(
            f"too short: {len(words)} words, minimum {gate['min_words']}"
        )

    max_words = gate.get("max_words")
    if isinstance(max_words, int) and len(words) > max_words:
        problems.append(
            f"too long: {len(words)} words, maximum {max_words}"
        )

    if len(sources) < gate["min_sources"]:
        problems.append(
            f"not enough sources: {len(sources)}, "
            f"minimum {gate['min_sources']}"
        )

    invalid = [source for source in sources if not urls.is_valid_url(source)]
    if invalid:
        problems.append(f"invalid source URL: {invalid[0]}")

    if gate.get("require_source_links", True):
        missing = [
            source for source in sources
            if source not in text
        ]
        if missing:
            problems.append(
                f"source not linked in the text: {missing[0]}"
            )

    if gate.get("require_cta") and cta_url and cta_url not in text:
        problems.append("missing call to action link to the project")

    lowered = text.lower()

    for phrase in gate["banned_phrases"]:
        if str(phrase).lower() in lowered:
            problems.append(f"unverifiable claim: {phrase!r}")

    for marker in gate["placeholder_markers"]:
        if str(marker).lower() in lowered:
            problems.append(f"unfinished placeholder: {marker!r}")

    if not links:
        problems.append("no links at all: content must cite its sources")

    return {
        "passed": not problems,
        "problems": problems,
        "stats": {
            "words": len(words),
            "links": len(links),
            "sources": len(sources),
        },
    }


def enforce(text, sources=None, rules=None, cta_url=None):
    """Return the report, raising QualityGateError when it did not pass."""
    report = check(text, sources=sources, rules=rules, cta_url=cta_url)

    if not report["passed"]:
        raise QualityGateError("; ".join(report["problems"]))

    return report
