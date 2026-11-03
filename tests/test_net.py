"""Network limits, exercised against a local server."""

import pytest

from sge import net


def test_fetch_reads_a_small_response(feed_server, rss_feed):
    url = feed_server.serve("/feed.xml", rss_feed)

    assert net.fetch_bytes(url) == rss_feed


def test_response_over_the_cap_is_refused(feed_server):
    body = b"<rss><channel>" + b"x" * 200_000 + b"</channel></rss>"
    url = feed_server.serve("/big.xml", body, send_length=False)

    with pytest.raises(net.ResponseTooLarge):
        net.fetch_bytes(url, max_bytes=50_000)


def test_declared_content_length_over_the_cap_fails_before_reading(
    feed_server
):
    url = feed_server.serve("/big.xml", b"y" * 100_000)

    with pytest.raises(net.ResponseTooLarge):
        net.fetch_bytes(url, max_bytes=1_000)


def test_slow_source_times_out(feed_server, rss_feed):
    url = feed_server.serve("/slow.xml", rss_feed, delay=2)

    with pytest.raises(net.FetchError):
        net.fetch_bytes(url, timeout=0.3)


def test_unreachable_source_raises_fetch_error():
    # Port 1 on localhost: refused immediately, no network needed.
    with pytest.raises(net.FetchError):
        net.fetch_bytes("http://127.0.0.1:1/feed.xml", timeout=2)


def test_http_error_status_raises_fetch_error(feed_server):
    url = feed_server.serve("/missing.xml", b"gone", status=500)

    with pytest.raises(net.FetchError):
        net.fetch_bytes(url, timeout=5)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.test/feed.xml",
    "javascript:alert(1)",
    "/not-absolute",
])
def test_unsupported_schemes_never_leave_the_process(url):
    with pytest.raises(net.FetchError):
        net.fetch_bytes(url)


def test_user_agent_identifies_the_project():
    assert "ShadowGrowthEngine" in net.USER_AGENT
    assert "github.com/safetylab" in net.USER_AGENT
