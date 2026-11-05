"""Publishers, exercised against a local server rather than a platform.

Every test here asserts one of two things: what we send to a platform,
or what we do with what it sends back. A refusal has to be reported as a
refusal — an API that answers 200 with an error object inside is the
normal case on Reddit, and treating that as success would log a post
that does not exist.
"""

import json

import pytest

from sge import net, publishers

REDDIT_ENV = {
    "SGE_REDDIT_CLIENT_ID": "id",
    "SGE_REDDIT_CLIENT_SECRET": "secret",
    "SGE_REDDIT_USERNAME": "safetylab",
    "SGE_REDDIT_PASSWORD": "pw",
}


def reddit(server, token_body=None, submit_body=None, submit_status=200):
    token_url = server.serve(
        "/token",
        token_body if token_body is not None
        else json.dumps({"access_token": "t0ken"}).encode(),
    )
    submit_url = server.serve(
        "/submit",
        submit_body if submit_body is not None
        else json.dumps({"json": {"errors": [], "data": {
            "url": "https://reddit.test/r/netsec/comments/1/x/",
            "id": "1",
        }}}).encode(),
        status=submit_status,
    )

    return publishers.RedditPublisher(REDDIT_ENV, token_url=token_url,
                                      submit_url=submit_url)


def test_reddit_posts_the_draft_and_returns_its_url(feed_server):
    result = reddit(feed_server).submit("netsec", "Title", "Body text")

    assert result["url"].endswith("/r/netsec/comments/1/x/")
    assert result["platform"] == "reddit"

    token_request, submit_request = feed_server.requests

    assert token_request["body"].startswith("grant_type=password")
    assert "sr=netsec" in submit_request["body"]
    assert "kind=self" in submit_request["body"]
    assert submit_request["headers"]["Authorization"] == "Bearer t0ken"


def test_reddit_reports_an_error_object_in_a_200_as_a_refusal(feed_server):
    body = json.dumps({"json": {"errors": [["RATELIMIT", "try later", None]],
                                "data": {}}}).encode()

    with pytest.raises(publishers.PlatformRefused, match="RATELIMIT"):
        reddit(feed_server, submit_body=body).submit("netsec", "T", "B")


def test_reddit_without_a_token_in_the_answer_refuses(feed_server):
    body = json.dumps({"error": "invalid_grant"}).encode()

    with pytest.raises(publishers.PlatformRefused, match="invalid_grant"):
        reddit(feed_server, token_body=body).submit("netsec", "T", "B")


def test_reddit_needs_a_subreddit(feed_server):
    with pytest.raises(publishers.PublishError, match="no subreddit"):
        reddit(feed_server).submit("", "T", "B")


def test_missing_credentials_are_named_and_nothing_is_sent(feed_server):
    publisher = publishers.RedditPublisher(
        {"SGE_REDDIT_CLIENT_ID": "id"},
        token_url=feed_server.serve("/token", b"{}"),
    )

    with pytest.raises(publishers.MissingCredentials,
                       match="SGE_REDDIT_CLIENT_SECRET"):
        publisher.submit("netsec", "T", "B")

    assert feed_server.requests == []


def test_x_posts_json_and_builds_the_status_url(feed_server):
    url = feed_server.serve(
        "/tweets", json.dumps({"data": {"id": "42"}}).encode()
    )
    publisher = publishers.XPublisher({"SGE_X_TOKEN": "t"}, post_url=url)

    result = publisher.submit(None, "Title", "Short text")

    assert result["url"] == "https://x.com/i/web/status/42"
    assert json.loads(feed_server.requests[0]["body"]) == {
        "text": "Short text"
    }


def test_x_refuses_text_over_the_limit_before_sending(feed_server):
    url = feed_server.serve("/tweets", b"{}")
    publisher = publishers.XPublisher({"SGE_X_TOKEN": "t"}, post_url=url)

    with pytest.raises(publishers.PublishError, match="the limit is 280"):
        publisher.submit(None, "Title", "x" * 281)

    assert feed_server.requests == []


def test_x_reports_the_platforms_own_explanation(feed_server):
    url = feed_server.serve(
        "/tweets",
        json.dumps({"detail": "not permitted on your plan"}).encode(),
        status=403,
    )
    publisher = publishers.XPublisher({"SGE_X_TOKEN": "t"}, post_url=url)

    with pytest.raises(publishers.PlatformRefused, match="your plan"):
        publisher.submit(None, "Title", "Short text")


def test_linkedin_reads_the_post_urn_from_the_header(feed_server):
    url = feed_server.serve(
        "/posts", b"", status=201,
        headers={"x-restli-id": "urn:li:share:7"},
    )
    publisher = publishers.LinkedInPublisher(
        {"SGE_LINKEDIN_TOKEN": "t",
         "SGE_LINKEDIN_AUTHOR": "urn:li:organization:9"},
        post_url=url,
    )

    result = publisher.submit(None, "Title", "Body")

    assert result["url"].endswith("urn:li:share:7")

    sent = json.loads(feed_server.requests[0]["body"])

    assert sent["author"] == "urn:li:organization:9"
    assert sent["commentary"] == "Body"
    assert sent["lifecycleState"] == "PUBLISHED"


def test_linkedin_without_a_urn_is_a_refusal(feed_server):
    url = feed_server.serve("/posts", b"", status=201)
    publisher = publishers.LinkedInPublisher(
        {"SGE_LINKEDIN_TOKEN": "t", "SGE_LINKEDIN_AUTHOR": "urn:li:person:1"},
        post_url=url,
    )

    with pytest.raises(publishers.PlatformRefused, match="without a post"):
        publisher.submit(None, "Title", "Body")


def test_hacker_news_has_no_publisher_and_says_why():
    with pytest.raises(publishers.Unsupported, match="read-only"):
        publishers.publisher_for("hackernews", {})

    assert not publishers.has_publisher("hackernews")


def test_an_unknown_channel_has_no_publisher():
    with pytest.raises(publishers.Unsupported, match="no publisher"):
        publishers.publisher_for("mastodon", {})


def test_a_write_never_follows_a_redirect(feed_server):
    """A redirected POST becomes a GET without the body."""
    url = feed_server.serve("/posts", b"", redirect_to="http://example.test/")

    with pytest.raises(net.FetchError, match="redirect"):
        net.post(url, b"{}", "application/json")


def test_a_write_cannot_reach_a_private_address(monkeypatch):
    monkeypatch.delenv(net.ALLOW_PRIVATE_ENV, raising=False)

    with pytest.raises(net.BlockedAddress):
        net.post("http://169.254.169.254/latest/meta-data/", b"",
                 "application/json")


def test_an_oversized_answer_to_a_write_is_refused(feed_server):
    url = feed_server.serve("/posts", b"x" * 5000)

    with pytest.raises(net.ResponseTooLarge):
        net.post(url, b"{}", "application/json", max_bytes=1000)
