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
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import quote, urlencode

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


def _quote(value):
    """Percent-encoding as OAuth 1.0a defines it (RFC 3986 unreserved)."""
    return quote(str(value), safe="~")


def oauth1_header(method, url, credentials, nonce=None, timestamp=None):
    """Signed Authorization header for a request with a JSON body.

    Only the oauth_* parameters are signed: a JSON body is not
    form-encoded, so it does not enter the signature base string. The
    signing key is the consumer secret and the token secret, joined by
    an ampersand, both percent-encoded.
    """
    params = {
        "oauth_consumer_key": credentials["consumer_key"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp or int(time.time())),
        "oauth_token": credentials["token"],
        "oauth_version": "1.0",
    }

    collected = "&".join(
        f"{_quote(key)}={_quote(params[key])}" for key in sorted(params)
    )
    base = "&".join([method.upper(), _quote(url), _quote(collected)])
    key = (f"{_quote(credentials['consumer_secret'])}&"
           f"{_quote(credentials['token_secret'])}")

    signature = base64.b64encode(
        hmac.new(key.encode("utf-8"), base.encode("utf-8"),
                 hashlib.sha1).digest()
    ).decode("ascii")

    signed = {**params, "oauth_signature": signature}

    return "OAuth " + ", ".join(
        f'{_quote(key)}="{_quote(signed[key])}"' for key in sorted(signed)
    )


class XPublisher:
    """Posts one tweet through API v2.

    Two credential shapes work, and the choice matters for unattended
    runs. OAuth 1.0a (four values straight from the developer portal)
    does not expire and is the default here. An OAuth 2.0 user token
    lasts two hours and its refresh token is single-use, which a CI job
    reading a fixed secret cannot survive — it is supported only for a
    manual run.

    Writing also needs a paid API tier, so a 403 usually means the plan
    rather than the text.
    """

    name = "x"
    needs_target = False
    POST_URL = "https://api.x.com/2/tweets"
    LIMIT = 280
    OAUTH1_VARS = ("SGE_X_API_KEY", "SGE_X_API_SECRET",
                   "SGE_X_ACCESS_TOKEN", "SGE_X_ACCESS_SECRET")

    def __init__(self, environ, post_url=None):
        self.environ = environ
        self.post_url = post_url or self.POST_URL

    def _oauth1_credentials(self):
        if any(self.environ.get(name) for name in self.OAUTH1_VARS):
            return {
                "consumer_key": self.environ.get("SGE_X_API_KEY", ""),
                "consumer_secret": self.environ.get("SGE_X_API_SECRET", ""),
                "token": self.environ.get("SGE_X_ACCESS_TOKEN", ""),
                "token_secret": self.environ.get("SGE_X_ACCESS_SECRET", ""),
            }

        return None

    def missing(self):
        if any(self.environ.get(name) for name in self.OAUTH1_VARS):
            return [name for name in self.OAUTH1_VARS
                    if not self.environ.get(name)]

        if self.environ.get("SGE_X_TOKEN"):
            return []

        return list(self.OAUTH1_VARS)

    def submit(self, target, title, body):
        missing = self.missing()

        if missing:
            raise MissingCredentials(f"x: {', '.join(missing)} not set")

        text = body.strip() or title.strip()

        if len(text) > self.LIMIT:
            raise PublishError(
                f"x: {len(text)} characters, the limit is {self.LIMIT}"
            )

        credentials = self._oauth1_credentials()

        if credentials:
            authorization = oauth1_header("POST", self.post_url, credentials)
        else:
            authorization = f"Bearer {self.environ['SGE_X_TOKEN']}"

        response = net.post(
            self.post_url, json.dumps({"text": text}), "application/json",
            headers={"Authorization": authorization},
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

    Posting as a person needs only the self-service "Share on
    LinkedIn" product, and then the author must be that same person's
    URN: a token with w_member_social cannot post as an organisation,
    which is the usual cause of a 403 here. Posting as a company page
    needs w_organization_social, which comes with the Community
    Management API and LinkedIn's review of the app.

    The author URN decides where the post lands and has to be given:
    urn:li:person:<id> or urn:li:organization:<id>.
    """

    name = "linkedin"
    needs_target = False
    POST_URL = "https://api.linkedin.com/rest/posts"
    # LinkedIn dates its API versions and retires one after about a
    # year; a stale value answers 426.
    VERSION = "202610"

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
