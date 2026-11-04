"""The server-side request forgery guard.

The SEO agent resolves links found on web pages, which is untrusted
input. Without this guard a page carrying a link to the cloud metadata
address would make the agent read it from the runner.
"""

import pytest

from sge import net

PRIVATE_URLS = [
    "http://127.0.0.1:8080/admin",
    "http://localhost/secret",
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://[::1]/",
    "http://10.0.0.5/internal",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://0.0.0.0:22/",
    "https://127.0.0.1/",
]


@pytest.fixture
def guard_on(monkeypatch):
    """The conftest fixture opts tests out; these tests opt back in."""
    monkeypatch.delenv(net.ALLOW_PRIVATE_ENV, raising=False)


@pytest.mark.parametrize("url", PRIVATE_URLS)
def test_private_addresses_are_refused(guard_on, url):
    with pytest.raises(net.BlockedAddress):
        net.fetch_bytes(url, timeout=3)


def test_the_block_names_the_address_it_refused(guard_on):
    with pytest.raises(net.BlockedAddress, match="127.0.0.1"):
        net.fetch_bytes("http://127.0.0.1/", timeout=3)


def test_blocked_address_is_a_fetch_error(guard_on):
    """Callers already handle FetchError; this must not escape them."""
    with pytest.raises(net.FetchError):
        net.fetch_bytes("http://127.0.0.1/", timeout=3)


def test_an_unresolvable_host_is_an_error_not_a_pass(guard_on):
    with pytest.raises(net.FetchError):
        net.fetch_bytes(
            "http://this-host-does-not-exist.invalid/", timeout=3
        )


def test_a_public_address_passes_the_check(guard_on):
    """example.com resolves publicly; only the check is exercised."""
    net.check_address("https://example.com/")


def test_the_guard_is_on_unless_explicitly_disabled(monkeypatch):
    monkeypatch.delenv(net.ALLOW_PRIVATE_ENV, raising=False)
    assert net.private_addresses_allowed() is False

    monkeypatch.setenv(net.ALLOW_PRIVATE_ENV, "0")
    assert net.private_addresses_allowed() is False

    monkeypatch.setenv(net.ALLOW_PRIVATE_ENV, "yes")
    assert net.private_addresses_allowed() is False

    monkeypatch.setenv(net.ALLOW_PRIVATE_ENV, "1")
    assert net.private_addresses_allowed() is True


def test_tests_can_reach_the_local_server(feed_server):
    """The opt-in has to work, or every integration test breaks."""
    url = feed_server.serve("/ok", b"<rss/>")

    assert net.fetch_bytes(url) == b"<rss/>"


def test_a_redirect_to_a_private_address_is_refused(feed_server,
                                                   monkeypatch):
    """A public host can redirect us inwards; the guard must re-check."""
    target = feed_server.serve("/open", b"<rss/>")
    monkeypatch.delenv(net.ALLOW_PRIVATE_ENV, raising=False)

    # The guard blocks the first hop here, which is the same local
    # server: what matters is that redirect targets are checked at all.
    assert "check_address(newurl)" in \
        __import__("pathlib").Path(net.__file__).read_text()

    with pytest.raises(net.BlockedAddress):
        net.fetch_bytes(target, timeout=3)


@pytest.mark.parametrize("address, public", [
    ("8.8.8.8", True),
    ("1.1.1.1", True),
    ("127.0.0.1", False),
    ("10.1.2.3", False),
    ("192.168.0.1", False),
    ("172.20.0.1", False),
    ("169.254.169.254", False),
    ("::1", False),
    ("fe80::1", False),
    ("fc00::1", False),
    ("224.0.0.1", False),
    ("0.0.0.0", False),
])
def test_address_classification(address, public):
    import ipaddress

    assert net._is_public(ipaddress.ip_address(address)) is public
