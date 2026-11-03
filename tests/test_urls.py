import pytest

from sge import urls


@pytest.mark.parametrize("raw, expected", [
    ("https://Example.COM/path/", "https://example.com/path"),
    ("https://example.com:443/a", "https://example.com/a"),
    ("http://example.com:80/a", "http://example.com/a"),
    ("https://example.com/a#section", "https://example.com/a"),
    ("https://example.com/a?utm_source=x&utm_medium=y",
     "https://example.com/a"),
    ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
    ("https://example.com", "https://example.com/"),
    ("  https://example.com/a  ", "https://example.com/a"),
])
def test_canonical_url_normalises(raw, expected):
    assert urls.canonical_url(raw) == expected


def test_tracking_only_query_is_dropped_so_variants_collapse():
    first = urls.canonical_url("https://example.com/post?utm_campaign=rss")
    second = urls.canonical_url("https://example.com/post?ref=twitter")

    assert first == second == "https://example.com/post"


@pytest.mark.parametrize("raw", [
    "javascript:alert(1)",
    "data:text/html,<script>",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "/relative/path",
    "example.com/no-scheme",
    "",
    None,
])
def test_non_http_urls_are_rejected(raw):
    with pytest.raises(urls.InvalidUrl):
        urls.canonical_url(raw)

    assert urls.is_valid_url(raw) is False
