"""URL canonicalisation used for deduplication."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = ("http", "https")

DEFAULT_PORTS = {"http": "80", "https": "443"}

# Campaign parameters change between channels but not the destination.
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "at_")
TRACKING_PARAMS = {
    "gclid", "fbclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "ref", "ref_src", "referrer", "source", "spm", "yclid",
}


class InvalidUrl(ValueError):
    """The URL cannot be used as a stable identifier."""


def canonical_url(url):
    """Return a stable form of ``url`` for use as a dedup key.

    Raises InvalidUrl for anything that is not an absolute http(s) URL,
    which also keeps javascript:, data: and file: out of the database.
    """
    if not url or not isinstance(url, str):
        raise InvalidUrl("Empty URL")

    parts = urlsplit(url.strip())

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidUrl(f"Unsupported scheme: {parts.scheme or 'none'!r}")

    if not parts.hostname:
        raise InvalidUrl("URL has no host")

    scheme = parts.scheme.lower()
    host = parts.hostname.lower()

    netloc = host
    if parts.port and str(parts.port) != DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{parts.port}"

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
        and not key.lower().startswith(TRACKING_PREFIXES)
    ]

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, urlencode(sorted(query)), ""))


def is_valid_url(url):
    """True when canonical_url would accept the URL."""
    try:
        canonical_url(url)
    except InvalidUrl:
        return False

    return True
