"""Hacker News search through the public Algolia API.

No key, no account, documented for public use. We only read.
"""

import datetime as dt
import json
from urllib.parse import quote

from . import net

API = "https://hn.algolia.com/api/v1"
ITEM_URL = "https://news.ycombinator.com/item?id="


class HackerNewsError(Exception):
    """The search failed or returned something unusable."""


def search(query, tags="story", limit=20, since_days=None):
    """Search stories (or comments with tags='comment').

    ``since_days`` matters more than it looks: the default ranking is by
    relevance, not date, so an unfiltered search returns threads up to
    fifteen years old, which are closed conversations.
    """
    # advancedSyntax honours quoted phrases. Without it the search is a
    # fuzzy OR: "openvas alternative" returned threads about OpenAI,
    # because "openvas" matched nothing while "alternative" matched lots.
    url = (f"{API}/search?query={quote(query)}&tags={tags}"
           f"&hitsPerPage={min(limit, 100)}&advancedSyntax=true")

    if since_days:
        cutoff = int(
            (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=since_days)).timestamp()
        )
        url += f"&numericFilters=created_at_i>{cutoff}"

    try:
        data = json.loads(net.fetch_bytes(url, max_bytes=2 * 1024 * 1024))
    except net.FetchError as error:
        raise HackerNewsError(str(error)) from error
    except json.JSONDecodeError as error:
        raise HackerNewsError(f"Invalid JSON: {error}") from error

    if not isinstance(data, dict) or "hits" not in data:
        raise HackerNewsError("Unexpected search payload")

    return [normalise(hit) for hit in data["hits"]]


def normalise(hit):
    """One search hit as a thread record."""
    object_id = str(hit.get("objectID", ""))
    title = (hit.get("title") or hit.get("story_title") or "").strip()
    excerpt = (hit.get("story_text") or hit.get("comment_text") or "").strip()

    return {
        "platform": "hackernews",
        "external_id": object_id,
        "url": f"{ITEM_URL}{object_id}",
        "title": title or "(no title)",
        "author": hit.get("author"),
        "excerpt": excerpt[:1000],
        "created_at_source": hit.get("created_at"),
        "points": int(hit.get("points") or 0),
        "comments": int(hit.get("num_comments") or 0),
    }
