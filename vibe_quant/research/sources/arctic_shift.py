"""Reddit through the Arctic Shift archive: WallStreetBets plays and other subs.

Unlike the ``reddit`` source (live ``.json`` listings, 403 since 2026-05), this
reads Arctic Shift's archive, newest first, skipping the last ``settle_days``
(scores are re-fetched ~1.5 days after posting, so younger posts all read 1).
Posts below ``min_score``, removed posts and (for subs with a flair filter)
off-flair posts are skipped. Subreddits come from ``VQ_ARCTIC_SUBREDDITS``
(default ``wallstreetbets``) or the saved research settings.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from vibe_quant.research.sources import register_source
from vibe_quant.research.sources._arctic_shift import (
    ArcticShiftClient,
    is_removed,
    post_to_raw_item,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from vibe_quant.research.schema import RawItem

logger = logging.getLogger(__name__)

ENV_SUBREDDITS = "VQ_ARCTIC_SUBREDDITS"
DEFAULT_SUBREDDITS = ("wallstreetbets",)
# Where WallStreetBets posts its plays: due diligence, positions, and closed trades.
DEFAULT_FLAIRS: dict[str, tuple[str, ...]] = {
    "wallstreetbets": ("DD", "YOLO", "Gain", "Loss", "Technical Analysis"),
}
DEFAULT_MIN_SCORE = 50
DEFAULT_SETTLE_DAYS = 3
DEFAULT_WINDOW_DAYS = 30


def subreddits_from_env() -> list[str]:
    raw = os.getenv(ENV_SUBREDDITS, "").strip()
    if not raw:
        return list(DEFAULT_SUBREDDITS)
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


@register_source("arctic_shift")
class ArcticShiftSource:
    """Archived Reddit posts (with top comments) via Arctic Shift."""

    name: ClassVar[str] = "arctic_shift"

    def __init__(
        self,
        subreddits: list[str] | None = None,
        flairs: dict[str, tuple[str, ...]] | None = None,
        min_score: int = DEFAULT_MIN_SCORE,
        settle_days: int = DEFAULT_SETTLE_DAYS,
        window_days: int = DEFAULT_WINDOW_DAYS,
        client: ArcticShiftClient | None = None,
    ) -> None:
        self._subreddits = subreddits if subreddits is not None else subreddits_from_env()
        self._flairs = DEFAULT_FLAIRS if flairs is None else flairs
        self._min_score = min_score
        self._settle_days = settle_days
        self._window_days = window_days
        self._client = client or ArcticShiftClient()

    def close(self) -> None:
        self._client.close()

    def fetch(self, since: datetime | None, limit: int) -> Iterable[RawItem]:
        """Yield up to ``limit`` qualifying posts per subreddit, newest first."""
        before = datetime.now(tz=UTC) - timedelta(days=self._settle_days)
        after = before - timedelta(days=self._window_days)
        if since is not None:
            after = max(after, since)
        for subreddit in self._subreddits:
            yielded = 0
            for post in self._candidates(subreddit, after, before):
                if yielded >= limit:
                    break
                comments = (
                    self._client.top_comments(str(post["id"]))
                    if int(post.get("num_comments") or 0) > 0
                    else []
                )
                yielded += 1
                yield post_to_raw_item(post, source=self.name, comments=comments)
            logger.info("arctic_shift r/%s: %d posts", subreddit, yielded)

    def _candidates(self, subreddit: str, after: datetime, before: datetime) -> list[dict[str, Any]]:
        flairs = self._flairs.get(subreddit.lower())
        raw = (
            [p for f in flairs for p in self._client.posts(subreddit, after=after, before=before, flair=f)]
            if flairs
            else self._client.posts(subreddit, after=after, before=before)
        )
        kept = [
            p for p in raw
            if not is_removed(p) and int(p.get("score") or 0) >= self._min_score
        ]
        # Best-received first: the plays the crowd rated most are the likeliest signal.
        kept.sort(key=lambda p: int(p.get("score") or 0), reverse=True)
        return kept
