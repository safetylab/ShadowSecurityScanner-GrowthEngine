"""Posting to the channels the project does not own.

A publisher turns one approved draft into one request through sge.net.
Nothing here decides *whether* to post: the distribution agent owns the
approval check, the daily limit and the audit log, and this module fails
loudly rather than guessing when a credential or a target is missing.

Credentials are read from the environment only — never a config file,
never the database — and every name carries TOKEN, SECRET or PASSWORD so
that the log redactor masks the value if one ever reaches a log line.

Hacker News has no publisher and will not get one. Its API is read-only,
submissions go through a web form, and automated submissions are against
its rules: the channel stays draft_only and a person posts it.
"""

import base64
import json
from urllib.parse import urlencode

from . import net


class PublishError(Exception):
    """Posting failed; the message says what the platform reported."""


class MissingCredentials(PublishError):
    """The environment does not carry what this platform needs."""


class PlatformRefused(PublishError):
    """The platform answered, and the answer was no."""


class Unsupported(PublishError):
    """This channel has no automated path, by nature of the platform."""


def _json(response, where):
    try:
        return json.loads(response.body.decode("utf-8", "replace") or "{}")
    except ValueError as error:
        raise PlatformRefused(
            f"{where}: HTTP {response.status} with an unreadable body"
        ) from error


class RedditPublisher:
    """Submits a self post to one subreddit through the script grant.

    The script grant (username and password of the account that owns the
    app) is the only Reddit flow that works unattended; the others need a
    browser. Reddit requires the app to be registered as type "script"
    and the account to be a developer of it.
    """

    name = "reddit"
    needs_target = True
    AUTH_URL = "https://www.reddit.com/api/v1/access_token"
    SUBMIT_URL = "https://oauth.reddit.com/api/submit"

    def __init__(self, environ, token_url=None, submit_url=None):
        self.environ = environ
        self.token_url = token_url or self.AUTH_URL
        self.submit_url = submit_url or self.SUBMIT_URL

    def missing(self):
        return [name for name in (
            "SGE_REDDIT_CLIENT_ID", "SGE_REDDIT_CLIENT_SECRET",
            "SGE_REDDIT_USERNAME", "SGE_REDDIT_PASSWORD",
        ) if not self.environ.get(name)]

    def _access_token(self):
        missing = self.missing()

        if missing:
            raise MissingCredentials(
                f"reddit: {', '.join(missing)} not set"
            )

        pair = (f"{self.environ['SGE_REDDIT_CLIENT_ID']}:"
                f"{self.environ['SGE_REDDIT_CLIENT_SECRET']}")
        basic = base64.b64encode(pair.encode("utf-8")).decode("ascii")

        body = urlencode({
            "grant_type": "password",
            "username": self.environ["SGE_REDDIT_USERNAME"],
            "password": self.environ["SGE_REDDIT_PASSWORD"],
        })

        response = net.post(
            self.token_url, body,
            "application/x-www-form-urlencoded",
            headers={"Authorization": f"Basic {basic}"},
        )

        payload = _json(response, "reddit token")
        token = payload.get("access_token")

        if not token:
            raise PlatformRefused(
                "reddit token: HTTP {} ({})".format(
                    response.status,
                    payload.get("error", "no access_token in the answer"),
                )
            )

        return token

    def submit(self, target, title, body):
        if not target:
            raise PublishError("reddit: no subreddit given")

        token = self._access_token()

        form = urlencode({
            "sr": target,
            "kind": "self",
            "title": title,
            "text": body,
            "api_type": "json",
            # Reddit sends its own notification; one more email per post
            # is noise, and the audit log is the record that matters.
            "sendreplies": "false",
        })

        response = net.post(
            self.submit_url, form,
            "application/x-www-form-urlencoded",
            headers={"Authorization": f"Bearer {token}"},
        )

        payload = _json(response, "reddit submit")

        # Reddit answers 200 and puts the refusal inside the document:
        # a rate limit, a subreddit rule, a shadowban. Only the body
        # tells them apart.
        errors = payload.get("json", {}).get("errors") or []

        if errors:
            raise PlatformRefused(f"reddit: {errors}")

        data = payload.get("json", {}).get("data") or {}
        url = data.get("url")

        if not url:
            raise PlatformRefused(
                f"reddit: HTTP {response.status} without a post URL"
            )

        return {"platform": self.name, "url": url, "id": data.get("id", "")}


class XPublisher:
    """Posts one tweet through API v2.

    The token must be a user-context OAuth 2.0 access token: app-only
    tokens cannot write. Writing also needs a paid API tier, so a 403
    here usually means the plan, not the text.
    """

    name = "x"
    needs_target = False
    POST_URL = "https://api.x.com/2/tweets"
    LIMIT = 280

    def __init__(self, environ, post_url=None):
        self.environ = environ
        self.post_url = post_url or self.POST_URL

    def missing(self):
        return [] if self.environ.get("SGE_X_TOKEN") else ["SGE_X_TOKEN"]

    def submit(self, target, title, body):
        missing = self.missing()

        if missing:
            raise MissingCredentials(f"x: {', '.join(missing)} not set")

        text = body.strip() or title.strip()

        if len(text) > self.LIMIT:
            raise PublishError(
                f"x: {len(text)} characters, the limit is {self.LIMIT}"
            )

        response = net.post(
            self.post_url, json.dumps({"text": text}), "application/json",
            headers={
                "Authorization": f"Bearer {self.environ['SGE_X_TOKEN']}",
            },
        )

        payload = _json(response, "x")

        if response.status >= 300 or "data" not in payload:
            raise PlatformRefused(
                "x: HTTP {} ({})".format(
                    response.status,
                    payload.get("detail") or payload.get("title")
                    or "no post id in the answer",
                )
            )

        post_id = payload["data"].get("id", "")

        return {
            "platform": self.name,
            "url": f"https://x.com/i/web/status/{post_id}",
            "id": post_id,
        }


class LinkedInPublisher:
    """Posts to a person or organisation page through the Posts API.

    Needs an access token from an app approved for the Community
    Management API; until that approval lands every call answers 403.
    The author URN decides where the post lands and has to be given:
    urn:li:organization:<id> or urn:li:person:<id>.
    """

    name = "linkedin"
    needs_target = False
    POST_URL = "https://api.linkedin.com/rest/posts"
    VERSION = "202411"

    def __init__(self, environ, post_url=None):
        self.environ = environ
        self.post_url = post_url or self.POST_URL

    def missing(self):
        return [name for name in ("SGE_LINKEDIN_TOKEN",
                                  "SGE_LINKEDIN_AUTHOR")
                if not self.environ.get(name)]

    def submit(self, target, title, body):
        missing = self.missing()

        if missing:
            raise MissingCredentials(f"linkedin: {', '.join(missing)} not set")

        document = {
            "author": self.environ["SGE_LINKEDIN_AUTHOR"],
            "commentary": body.strip() or title.strip(),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        response = net.post(
            self.post_url, json.dumps(document), "application/json",
            headers={
                "Authorization":
                    f"Bearer {self.environ['SGE_LINKEDIN_TOKEN']}",
                "LinkedIn-Version": self.VERSION,
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )

        if response.status >= 300:
            payload = _json(response, "linkedin")
            raise PlatformRefused(
                "linkedin: HTTP {} ({})".format(
                    response.status,
                    payload.get("message", "no message"),
                )
            )

        # The post URN comes back in a header, not in the body.
        urn = (response.headers.get("x-restli-id")
               or response.headers.get("X-RestLi-Id") or "")

        if not urn:
            raise PlatformRefused(
                f"linkedin: HTTP {response.status} without a post URN"
            )

        return {
            "platform": self.name,
            "url": f"https://www.linkedin.com/feed/update/{urn}",
            "id": urn,
        }


PUBLISHERS = {
    "reddit": RedditPublisher,
    "x": XPublisher,
    "linkedin": LinkedInPublisher,
}

# Platforms with no automated path, and why. Kept apart from the
# publishers so a configuration error reads as a fact about the
# platform rather than as a missing feature.
NO_AUTOMATION = {
    "hackernews": ("the API is read-only, submissions go through a web "
                   "form, and automated submissions are against the "
                   "site's rules"),
    "telegram": ("no bot token path is implemented; the channel is a "
                 "person's to post to"),
}


def has_publisher(channel):
    return channel in PUBLISHERS


def needs_target(channel):
    """True where a post has to name a place on the platform."""
    return bool(getattr(PUBLISHERS.get(channel), "needs_target", False))


def publisher_for(channel, environ):
    """A publisher for a channel, or an explanation why there is none."""
    if channel in NO_AUTOMATION:
        raise Unsupported(f"{channel}: {NO_AUTOMATION[channel]}")

    if channel not in PUBLISHERS:
        raise Unsupported(f"{channel}: no publisher exists for this channel")

    return PUBLISHERS[channel](environ)
