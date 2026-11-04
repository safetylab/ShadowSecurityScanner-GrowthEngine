import pytest

from sge import html_meta, seo

GOOD = """<html><head>
<title>A reasonable page title for a scanner guide</title>
<meta name="description" content="%s">
<meta property="og:title" content="t"><meta property="og:description" content="d">
<meta property="og:type" content="website"><meta property="og:url" content="u">
<link rel="canonical" href="https://example.test/page">
</head><body><h1>One heading</h1></body></html>""" % ("d" * 100)


def check(html, url="https://example.test/page", status=200):
    return seo.check_page(url, status, html_meta.parse_page(html))


def test_a_complete_page_has_no_findings():
    assert check(GOOD) == []


def test_non_200_short_circuits():
    findings = check(GOOD, status=404)

    assert [f["code"] for f in findings] == ["bad_status"]
    assert findings[0]["severity"] == "critical"


def test_missing_title_is_critical():
    findings = check(GOOD.replace(
        "<title>A reasonable page title for a scanner guide</title>", ""))

    assert any(f["code"] == "title_missing" and f["severity"] == "critical"
               for f in findings)


def test_long_title_is_reported_with_its_length():
    findings = check(GOOD.replace(
        "A reasonable page title for a scanner guide", "T" * 120))
    finding = next(f for f in findings if f["code"] == "title_long")

    assert "120" in finding["message"]


def test_missing_description_is_high():
    findings = check(GOOD.replace('<meta name="description" content="%s">'
                                  % ("d" * 100), ""))

    assert any(f["code"] == "description_missing" and f["severity"] == "high"
               for f in findings)


def test_canonical_pointing_elsewhere_is_reported():
    findings = check(GOOD.replace("https://example.test/page",
                                  "https://example.test/other"))

    assert any(f["code"] == "canonical_mismatch" for f in findings)


def test_canonical_differing_only_by_tracking_is_accepted():
    """Our own canonicalisation must not invent a mismatch."""
    findings = check(GOOD.replace('href="https://example.test/page"',
                                  'href="https://example.test/page/"'))

    assert not any(f["code"] == "canonical_mismatch" for f in findings)


def test_invalid_canonical_is_reported():
    findings = check(GOOD.replace('href="https://example.test/page"',
                                  'href="javascript:alert(1)"'))

    assert any(f["code"] == "canonical_invalid" for f in findings)


def test_incomplete_open_graph_lists_what_is_missing():
    findings = check(GOOD.replace('<meta property="og:type" content="website">',
                                  ""))
    finding = next(f for f in findings if f["code"] == "open_graph_incomplete")

    assert "og:type" in finding["message"]


def test_missing_and_multiple_h1_are_both_reported():
    assert any(f["code"] == "h1_missing"
               for f in check(GOOD.replace("<h1>One heading</h1>", "")))
    assert any(f["code"] == "h1_multiple"
               for f in check(GOOD.replace("<h1>One heading</h1>",
                                           "<h1>A</h1><h1>B</h1>")))


def test_internal_links_are_absolute_same_host_and_unique():
    links = ["/a", "/a/", "a", "https://example.test/b",
             "https://other.test/c", "mailto:x@y.test", "#frag",
             "javascript:void(0)"]

    found = seo.internal_links("https://example.test/page", links,
                               "example.test")
    canonical = [pair[0] for pair in found]

    assert "https://example.test/a" in canonical
    assert "https://example.test/b" in canonical
    assert all("other.test" not in url for url in canonical)
    assert len(canonical) == len(set(canonical))


def test_links_keep_the_address_to_request_as_well_as_the_dedup_key():
    """Canonicalisation strips a trailing slash; the request must not.

    "/guides/child" is a different request from "/guides/child/", and a
    server that does not redirect between them answers 404.
    """
    found = seo.internal_links("https://example.test/guides/",
                               ["child/"], "example.test")

    assert found == [("https://example.test/guides/child",
                      "https://example.test/guides/child/")]


def test_external_links_exclude_our_own_host():
    links = ["/a", "https://other.test/c", "https://other.test/c?utm_source=x"]

    found = seo.external_links("https://example.test/page", links)

    assert [pair[0] for pair in found] == ["https://other.test/c"]


@pytest.mark.parametrize("severity", seo.SEVERITIES)
def test_sorting_puts_the_worst_first(severity):
    findings = [{"severity": s, "code": s, "message": ""}
                for s in reversed(seo.SEVERITIES)]

    assert seo.sort_findings(findings)[0]["severity"] == "critical"
