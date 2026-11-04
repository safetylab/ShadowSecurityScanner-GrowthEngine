"""SEO checks over a page's metadata.

Every check returns findings with a severity, so a report can be read
worst-first. The checks only describe what is there: nothing here edits
the site.
"""

from urllib.parse import urljoin, urlsplit

from . import urls

# Google truncates around these lengths; they are guidance, not rules.
TITLE_MIN = 25
TITLE_MAX = 65
DESCRIPTION_MIN = 70
DESCRIPTION_MAX = 165

SEVERITIES = ("critical", "high", "medium", "low")

OPEN_GRAPH_REQUIRED = ("og:title", "og:description", "og:type", "og:url")


def _finding(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def check_page(url, status, page):
    """Findings for one fetched page. ``page`` comes from parse_page."""
    findings = []

    if status != 200:
        findings.append(_finding(
            "critical", "bad_status", f"HTTP {status}"
        ))
        return findings

    title = page["title"]

    if not title:
        findings.append(_finding("critical", "title_missing",
                                 "No <title>"))
    elif len(title) > TITLE_MAX:
        findings.append(_finding(
            "medium", "title_long",
            f"Title is {len(title)} characters, over {TITLE_MAX}"
        ))
    elif len(title) < TITLE_MIN:
        findings.append(_finding(
            "medium", "title_short",
            f"Title is {len(title)} characters, under {TITLE_MIN}"
        ))

    description = page["meta"].get("description", "")

    if not description:
        findings.append(_finding("high", "description_missing",
                                 "No meta description"))
    elif len(description) > DESCRIPTION_MAX:
        findings.append(_finding(
            "low", "description_long",
            f"Description is {len(description)} characters, "
            f"over {DESCRIPTION_MAX}"
        ))
    elif len(description) < DESCRIPTION_MIN:
        findings.append(_finding(
            "low", "description_short",
            f"Description is {len(description)} characters, "
            f"under {DESCRIPTION_MIN}"
        ))

    canonical = page["canonical"]

    if not canonical:
        findings.append(_finding("high", "canonical_missing",
                                 "No canonical link"))
    else:
        try:
            if urls.canonical_url(urljoin(url, canonical)) != \
                    urls.canonical_url(url):
                findings.append(_finding(
                    "medium", "canonical_mismatch",
                    f"Canonical points elsewhere: {canonical}"
                ))
        except urls.InvalidUrl:
            findings.append(_finding(
                "high", "canonical_invalid",
                f"Canonical is not a usable URL: {canonical}"
            ))

    missing_og = [
        key for key in OPEN_GRAPH_REQUIRED if not page["meta"].get(key)
    ]

    if missing_og:
        findings.append(_finding(
            "medium", "open_graph_incomplete",
            f"Missing Open Graph tags: {', '.join(missing_og)}"
        ))

    h1s = [text for level, text in page["headings"] if level == "h1"]

    if not h1s:
        findings.append(_finding("high", "h1_missing", "No <h1>"))
    elif len(h1s) > 1:
        findings.append(_finding(
            "low", "h1_multiple", f"{len(h1s)} <h1> elements"
        ))

    if page["images_without_alt"]:
        findings.append(_finding(
            "low", "images_without_alt",
            f"{page['images_without_alt']} of {page['images']} images "
            "have no alt text"
        ))

    return findings


SKIP_PREFIXES = ("mailto:", "tel:", "javascript:", "#", "data:")


def _absolute_links(url, links):
    """Yield (canonical, absolute) for each usable link on a page.

    Both forms are needed. The canonical form deduplicates, so one page
    is not checked twice because of a tracking parameter. The absolute
    form is what gets requested, because canonicalisation strips a
    trailing slash and "/guides/child" is not the same request as
    "/guides/child/": a server that does not redirect between them
    answers 404, which an audit would report as a broken link on a site
    that is fine.
    """
    seen = set()

    for href in links:
        if href.startswith(SKIP_PREFIXES):
            continue

        absolute = urljoin(url, href)

        if not urls.is_valid_url(absolute):
            continue

        canonical = urls.canonical_url(absolute)

        if canonical in seen:
            continue

        seen.add(canonical)
        yield canonical, absolute


def internal_links(url, links, host):
    """Same-host links from a page, as (canonical, absolute) pairs."""
    return [
        pair for pair in _absolute_links(url, links)
        if urlsplit(pair[1]).hostname == host
    ]


def external_links(url, links):
    """Off-site links from a page, as (canonical, absolute) pairs."""
    host = urlsplit(url).hostname

    return [
        pair for pair in _absolute_links(url, links)
        if urlsplit(pair[1]).hostname != host
    ]


def sort_findings(findings):
    """Worst first, stable within a severity."""
    order = {severity: index for index, severity in enumerate(SEVERITIES)}

    return sorted(findings, key=lambda f: order.get(f["severity"], 99))
