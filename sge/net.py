"""HTTP access with hard limits.

Every outbound request goes through this module so timeouts, response
size caps, redirect policy, the User-Agent and the refusal to reach
private addresses are enforced in one place.
"""

import ipaddress
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import __version__, urls

USER_AGENT = (
    f"ShadowGrowthEngine/{__version__} "
    "(+https://github.com/safetylab/ShadowSecurityScanner-GrowthEngine)"
)

DEFAULT_TIMEOUT = 15
DEFAULT_MAX_BYTES = 12 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 3
CHUNK_SIZE = 64 * 1024


class FetchError(Exception):
    """Any outbound request failure, normalised for callers."""


class ResponseTooLarge(FetchError):
    """The response exceeded the configured size cap."""


class BlockedAddress(FetchError):
    """The target resolves to an address we refuse to reach.

    This is the server-side request forgery guard. The SEO agent follows
    links found on web pages, which is untrusted input: a page carrying
    <a href="http://169.254.169.254/latest/meta-data/"> would otherwise
    make the agent read the runner's cloud metadata service.
    """


# Tests serve from 127.0.0.1, so they opt in explicitly through this
# variable. Nothing else sets it.
ALLOW_PRIVATE_ENV = "SGE_ALLOW_PRIVATE_NETWORKS"


def private_addresses_allowed():
    return os.environ.get(ALLOW_PRIVATE_ENV) == "1"


def _is_public(address):
    """True when an IP address is a routable public address."""
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def resolved_addresses(host, port):
    """Every address ``host`` resolves to."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise FetchError(f"Cannot resolve {host}: {error}") from error

    found = []

    for info in infos:
        try:
            found.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue

    if not found:
        raise FetchError(f"Cannot resolve {host}")

    return found


def check_address(url):
    """Refuse a URL whose host resolves to a non-public address.

    Note the residual risk: the name is resolved again by the socket
    layer when the request is made, so a DNS entry that changes between
    the two lookups is not covered. Closing that needs connecting by IP
    with an explicit Host header, which is more machinery than this
    threat model justifies.
    """
    if private_addresses_allowed():
        return

    parts = urlsplit(url)
    host = parts.hostname

    if not host:
        raise FetchError(f"URL has no host: {url!r}")

    port = parts.port or (443 if parts.scheme == "https" else 80)
    blocked = [
        address for address in resolved_addresses(host, port)
        if not _is_public(address)
    ]

    if blocked:
        raise BlockedAddress(
            f"Refusing to reach {host}: resolves to {blocked[0]}, which is "
            "not a public address"
        )


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but only to absolute http(s) URLs."""

    max_redirections = DEFAULT_MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not urls.is_valid_url(newurl):
            raise FetchError(f"Refusing redirect to unsupported URL: {code}")

        # A redirect is as untrusted as the first URL: a public host can
        # send us to 127.0.0.1 or to a metadata address.
        check_address(newurl)

        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_StrictRedirectHandler())


def open_response(url, timeout=DEFAULT_TIMEOUT, headers=None):
    """Open a validated URL and return the raw response object."""
    if not urls.is_valid_url(url):
        raise FetchError(f"Unsupported URL: {url!r}")

    check_address(url)

    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )

    try:
        return _opener().open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        raise FetchError(f"HTTP {error.code} for {url}") from error
    except urllib.error.URLError as error:
        raise FetchError(f"Request failed: {error.reason}") from error
    except OSError as error:
        raise FetchError(f"Request failed: {error}") from error


def iter_chunks(url, timeout=DEFAULT_TIMEOUT, max_bytes=DEFAULT_MAX_BYTES,
                headers=None):
    """Yield response chunks, raising ResponseTooLarge past ``max_bytes``.

    Streaming matters: some advisory feeds are several megabytes, and a
    truncated read produces invalid XML rather than an honest error.
    """
    response = open_response(url, timeout=timeout, headers=headers)
    received = 0

    try:
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ResponseTooLarge(
                f"Content-Length {declared} exceeds limit {max_bytes}"
            )

        while True:
            try:
                chunk = response.read(CHUNK_SIZE)
            except OSError as error:
                raise FetchError(f"Read failed: {error}") from error

            if not chunk:
                break

            received += len(chunk)
            if received > max_bytes:
                raise ResponseTooLarge(
                    f"Response exceeded limit of {max_bytes} bytes"
                )

            yield chunk
    finally:
        response.close()


def fetch_bytes(url, timeout=DEFAULT_TIMEOUT, max_bytes=DEFAULT_MAX_BYTES,
                headers=None):
    """Read a whole response into memory, respecting the size cap."""
    return b"".join(iter_chunks(url, timeout, max_bytes, headers))


def fetch(url, timeout=DEFAULT_TIMEOUT, max_bytes=DEFAULT_MAX_BYTES,
          headers=None):
    """Read a response and report the URL it actually came from.

    The final URL matters for resolving relative links: a request for
    "/guides" is redirected to "/guides/", and urljoin against the
    pre-redirect URL drops the last segment, turning "page/" into
    "/page/" at the site root. That produced phantom 404s in SEO audits.
    """
    response = open_response(url, timeout=timeout, headers=headers)
    received = bytearray()

    try:
        final_url = response.geturl() or url
        declared = response.headers.get("Content-Length")

        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ResponseTooLarge(
                f"Content-Length {declared} exceeds limit {max_bytes}"
            )

        while True:
            try:
                chunk = response.read(CHUNK_SIZE)
            except OSError as error:
                raise FetchError(f"Read failed: {error}") from error

            if not chunk:
                break

            received.extend(chunk)

            if len(received) > max_bytes:
                raise ResponseTooLarge(
                    f"Response exceeded limit of {max_bytes} bytes"
                )
    finally:
        response.close()

    return bytes(received), final_url
