"""Shared Arctic Shift client for the Reddit-archive sources.

Reddit's unauthenticated ``.json`` endpoints 403 since 2026-05; Arctic Shift
(https://arctic-shift.photon-reddit.com) archives every post and comment and
re-fetches scores ~1.5 days later. Its search API returns Reddit's own JSON
shapes, so posts convert like the ``reddit`` source's. It throttles hard
("Timeout. Maybe slow down a bit"), so requests are paced and retried.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from vibe_quant.research.schema import RawItem
from vibe_quant.research.sources.reddit import (
    BASE_URL,
    TOP_COMMENT_COUNT,
    _extract_image_urls,
    _normalize_author,
)

logger = logging.getLogger(__name__)

API_URL = "https://arctic-shift.photon-reddit.com/api"
MIN_REQUEST_INTERVAL_S = 1.0
RETRY_BACKOFF_S = (5.0, 15.0, 45.0)
HTTP_TIMEOUT_S = 60.0
PAGE_LIMIT = 100
COMMENT_FETCH_LIMIT = 100
REMOVED_MARKERS = frozenset({"[removed]", "[deleted]", "[ Removed by moderator ]"})


class ArcticShiftClient:
    """Paced GET client for the Arctic Shift API."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=HTTP_TIMEOUT_S,
            headers={"User-Agent": "vibe-quant-research/0.1", "Accept": "application/json"},
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self._client.close()

    def get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """Return the ``data`` list of one API call (empty on persistent failure)."""
        for attempt, backoff in enumerate((*RETRY_BACKOFF_S, None)):
            wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            error: str | None = None
            body: Any = {}
            try:
                response = self._client.get(f"{API_URL}/{path}", params=params)
                self._last_request_at = time.monotonic()
                body = response.json() if response.content else {}
            except (httpx.HTTPError, ValueError) as e:
                error = f"{type(e).__name__}: {e}"
            else:
                if isinstance(body, dict) and body.get("error"):
                    error = str(body["error"])
                elif response.status_code >= 400:
                    error = f"HTTP {response.status_code}"
            if not error and isinstance(body, dict):
                return list(body.get("data") or [])
            if backoff is None:
                logger.warning("arctic shift %s failed after %d attempts: %s", path, attempt + 1, error)
                return []
            logger.info("arctic shift %s: %s; retrying in %.0fs", path, error, backoff)
            time.sleep(backoff)
        return []

    def posts(
        self,
        subreddit: str,
        *,
        after: datetime,
        before: datetime,
        flair: str | None = None,
    ) -> list[dict[str, Any]]:
        """All posts in ``[after, before)``, newest first (pages backwards through the window)."""
        out: list[dict[str, Any]] = []
        cursor = before
        while True:
            params = {
                "subreddit": subreddit,
                "after": after.strftime("%Y-%m-%dT%H:%M:%S"),
                "before": cursor.strftime("%Y-%m-%dT%H:%M:%S"),
                "limit": str(PAGE_LIMIT),
                "sort": "desc",
            }
            if flair:
                params["link_flair_text"] = flair
            page = self.get("posts/search", params)
            out.extend(page)
            if len(page) < PAGE_LIMIT:
                return out
            oldest = min(int(p.get("created_utc") or 0) for p in page)
            next_cursor = datetime.fromtimestamp(oldest, tz=UTC)
            if next_cursor >= cursor:
                return out
            cursor = next_cursor

    def posts_by_id(self, ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(0, len(ids), 100):
            out.extend(self.get("posts/ids", {"ids": ",".join(ids[i : i + 100])}))
        return out

    def top_comments(self, post_id: str) -> list[dict[str, Any]]:
        """The post's highest-scored comments (Arctic Shift can't sort by score: sort locally)."""
        raw = self.get("comments/search", {"link_id": post_id, "limit": str(COMMENT_FETCH_LIMIT)})
        comments: list[dict[str, Any]] = [
            {
                "author": _normalize_author(c.get("author")),
                "body": str(c.get("body") or ""),
                "score": int(c.get("score") or 0),
            }
            for c in raw
            if str(c.get("body") or "").strip() not in REMOVED_MARKERS
        ]
        comments.sort(key=lambda c: int(c["score"]), reverse=True)
        return comments[:TOP_COMMENT_COUNT]


def is_removed(post: dict[str, Any]) -> bool:
    meta = post.get("_meta") or {}
    return (
        str(post.get("title") or "").strip() in REMOVED_MARKERS
        or str(post.get("selftext") or "").strip() in REMOVED_MARKERS
        or bool(meta.get("removal_type"))
    )


def post_to_raw_item(
    post: dict[str, Any], *, source: str, comments: list[dict[str, Any]]
) -> RawItem:
    """Same RawItem shape as the ``reddit`` source (permalink, flair, comments, images)."""
    permalink = str(post.get("permalink") or "")
    extras: dict[str, Any] = {
        "subreddit": post.get("subreddit"),
        "flair": post.get("link_flair_text"),
        "num_comments": int(post.get("num_comments") or 0),
        "comments": comments,
        "via": "arctic_shift",
    }
    image_urls = _extract_image_urls(post)
    if image_urls:
        extras["image_urls"] = image_urls
    return RawItem(
        source=source,
        external_id=str(post.get("id") or ""),
        url=f"{BASE_URL}{permalink}" if permalink else str(post.get("url") or ""),
        title=str(post.get("title") or ""),
        body=str(post.get("selftext") or ""),
        author=_normalize_author(post.get("author")),
        posted_at=datetime.fromtimestamp(float(post.get("created_utc") or 0.0), tz=UTC),
        score=int(post.get("score") or 0),
        extras=extras,
    )
