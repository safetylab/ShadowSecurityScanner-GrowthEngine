"""GitHub REST API access.

Anonymous by default — 60 requests an hour is plenty for the counters we
read. A token in GITHUB_TOKEN raises that to 5000 and is the normal case
on CI; nothing here needs write scope.
"""

import json
import os

from . import net

API = "https://api.github.com"


class GitHubError(Exception):
    """The API call failed or returned something unusable."""


def token():
    for name in ("SGE_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value

    return None


def _headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    secret = token()
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    return headers


def get(path, max_bytes=4 * 1024 * 1024):
    """GET an API path and decode its JSON."""
    url = path if path.startswith("https://") else f"{API}{path}"

    try:
        data = net.fetch_bytes(url, headers=_headers(), max_bytes=max_bytes)
    except net.FetchError as error:
        raise GitHubError(str(error)) from error

    try:
        return json.loads(data)
    except json.JSONDecodeError as error:
        raise GitHubError(f"Invalid JSON from {url}: {error}") from error


def repository(owner_repo):
    """Repository metadata: stars, forks, issues, topics, license."""
    return get(f"/repos/{owner_repo}")


def releases(owner_repo, limit=10):
    data = get(f"/repos/{owner_repo}/releases?per_page={min(limit, 100)}")

    if not isinstance(data, list):
        raise GitHubError("Unexpected releases payload")

    return data


def contents(owner_repo, path=""):
    """Top-level (or given path) listing of a repository."""
    data = get(f"/repos/{owner_repo}/contents/{path}")

    if not isinstance(data, list):
        raise GitHubError(f"Unexpected contents payload for {path!r}")

    return data


def readme(owner_repo):
    """Raw README text."""
    meta = get(f"/repos/{owner_repo}/readme")
    download = meta.get("download_url")

    if not download:
        raise GitHubError("README has no download URL")

    try:
        return net.fetch_bytes(download).decode("utf-8", errors="replace")
    except net.FetchError as error:
        raise GitHubError(f"Cannot read README: {error}") from error


def issues(owner_repo, state="open", limit=50):
    data = get(
        f"/repos/{owner_repo}/issues?state={state}"
        f"&per_page={min(limit, 100)}"
    )

    if not isinstance(data, list):
        raise GitHubError("Unexpected issues payload")

    # The issues endpoint also returns pull requests; they are not issues.
    return [item for item in data if "pull_request" not in item]


def pull_requests(owner_repo, state="open", limit=50):
    data = get(
        f"/repos/{owner_repo}/pulls?state={state}"
        f"&per_page={min(limit, 100)}"
    )

    if not isinstance(data, list):
        raise GitHubError("Unexpected pulls payload")

    return data


def search_issues(query, limit=30):
    """Search issues and discussions-as-issues across GitHub."""
    from urllib.parse import quote

    data = get(
        f"/search/issues?q={quote(query)}&per_page={min(limit, 100)}"
        "&sort=updated"
    )

    return data.get("items", []) if isinstance(data, dict) else []


def owner_repo_from_url(url):
    """'https://github.com/owner/repo' -> 'owner/repo'."""
    parts = [part for part in url.split("github.com/")[-1].split("/") if part]

    if len(parts) < 2:
        raise GitHubError(f"Not a repository URL: {url}")

    return f"{parts[0]}/{parts[1]}"
