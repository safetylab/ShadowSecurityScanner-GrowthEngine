"""GitHub API wrapper: auth handling and payload shapes.

Served from the local HTTP server by pointing sge.github.API at it, so
no test reaches github.com.
"""

import json

import pytest

from sge import github


@pytest.fixture
def api(feed_server, monkeypatch):
    monkeypatch.setattr(github, "API", feed_server.url)

    def serve(path, payload, **options):
        body = json.dumps(payload).encode()
        return feed_server.serve(path, body,
                                 content_type="application/json", **options)

    serve.server = feed_server
    return serve


def test_repository_is_decoded(api):
    api("/repos/o/r", {"full_name": "o/r", "stargazers_count": 14})

    assert github.repository("o/r")["stargazers_count"] == 14


def test_pull_requests_are_excluded_from_issues(api):
    api("/repos/o/r/issues?state=open&per_page=50", [
        {"number": 1},
        {"number": 2, "pull_request": {"url": "..."}},
    ])

    numbers = [item["number"] for item in github.issues("o/r")]

    assert numbers == [1]


def test_http_error_becomes_github_error(api):
    api("/repos/o/r", {"message": "Not Found"}, status=404)

    with pytest.raises(github.GitHubError):
        github.repository("o/r")


def test_invalid_json_becomes_github_error(api):
    api.server.serve("/repos/o/bad", b"not json",
                     content_type="application/json")

    with pytest.raises(github.GitHubError, match="Invalid JSON"):
        github.repository("o/bad")


def test_unexpected_payload_shape_is_rejected(api):
    api("/repos/o/r/releases?per_page=5", {"message": "nope"})

    with pytest.raises(github.GitHubError, match="Unexpected"):
        github.releases("o/r", 5)


def test_token_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("SGE_GITHUB_TOKEN", raising=False)

    assert github.token() is None

    monkeypatch.setenv("GITHUB_TOKEN", "t1")
    assert github.token() == "t1"

    monkeypatch.setenv("SGE_GITHUB_TOKEN", "t2")
    assert github.token() == "t2"


def test_search_returns_items_or_empty(api):
    api("/search/issues?q=scanner&per_page=30&sort=updated",
        {"items": [{"number": 5}]})

    assert github.search_issues("scanner") == [{"number": 5}]


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/owner/repo", "owner/repo"),
    ("https://github.com/owner/repo/", "owner/repo"),
    ("https://github.com/owner/repo/tree/main", "owner/repo"),
])
def test_owner_repo_is_parsed_from_a_url(url, expected):
    assert github.owner_repo_from_url(url) == expected


def test_a_url_without_a_repository_is_an_error():
    with pytest.raises(github.GitHubError):
        github.owner_repo_from_url("https://github.com/owner")
