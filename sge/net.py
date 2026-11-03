"""HTTP access with hard limits.

Every outbound request goes through this module so timeouts, response
size caps, redirect policy and the User-Agent are enforced in one place.
"""

import urllib.error
import urllib.request

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


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but only to absolute http(s) URLs."""

    max_redirections = DEFAULT_MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not urls.is_valid_url(newurl):
            raise FetchError(f"Refusing redirect to unsupported URL: {code}")

        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_StrictRedirectHandler())


def open_response(url, timeout=DEFAULT_TIMEOUT, headers=None):
    """Open a validated URL and return the raw response object."""
    if not urls.is_valid_url(url):
        raise FetchError(f"Unsupported URL: {url!r}")

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
