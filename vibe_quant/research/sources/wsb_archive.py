"""Backfill WallStreetBets plays from the local post-title archive.

The archive (``VQ_WSB_ARCHIVE_DB``, default ``~/trading/data/cache/lab/wsb.db``)
holds every r/wallstreetbets post title with flair and score since 2024-05,
plus per-day ticker mentions. This source picks the best-received posts of the
play flairs from it and hydrates their bodies and top comments through Arctic
Shift, so the research pipeline can mine the whole history, not just the last
month.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from vibe_quant.research.sources import register_source
from vibe_quant.research.sources._arctic_shift import (
    ArcticShiftClient,
    is_removed,
    post_to_raw_item,
)
from vibe_quant.research.sources.arctic_shift import DEFAULT_FLAIRS

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from vibe_quant.research.schema import RawItem

logger = logging.getLogger(__name__)

ENV_ARCHIVE_DB = "VQ_WSB_ARCHIVE_DB"
DEFAULT_ARCHIVE_DB = Path.home() / "trading" / "data" / "cache" / "lab" / "wsb.db"
DEFAULT_MIN_SCORE = 200


def archive_path() -> Path:
    return Path(os.getenv(ENV_ARCHIVE_DB) or DEFAULT_ARCHIVE_DB)


@register_source("wsb_archive")
class WsbArchiveSource:
    """Best-received archived WallStreetBets plays, hydrated via Arctic Shift."""

    name: ClassVar[str] = "wsb_archive"

    def __init__(
        self,
        db_path: Path | None = None,
        flairs: tuple[str, ...] | None = None,
        min_score: int = DEFAULT_MIN_SCORE,
        client: ArcticShiftClient | None = None,
        exclude_ids: set[str] | frozenset[str] | None = None,
    ) -> None:
        self._db_path = db_path or archive_path()
        self._exclude_ids = frozenset(exclude_ids or ())
        if not self._db_path.exists():
            raise FileNotFoundError(f"WallStreetBets archive not found: {self._db_path}")
        self._flairs = flairs or DEFAULT_FLAIRS["wallstreetbets"]
        self._min_score = min_score
        self._client = client or ArcticShiftClient()

    def close(self) -> None:
        self._client.close()

    def _post_ids(self, since: datetime | None, limit: int) -> list[str]:
        marks = ",".join("?" for _ in self._flairs)
        query = (
            f"SELECT id FROM posts WHERE flair IN ({marks}) AND score >= ?"  # noqa: S608 (placeholders only)
            + (" AND created_utc >= ?" if since is not None else "")
            + " ORDER BY score DESC"
        )
        args: list[object] = [*self._flairs, self._min_score]
        if since is not None:
            args.append(int(since.timestamp()))
        db = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True, timeout=30)
        try:
            ids: list[str] = []
            for (post_id,) in db.execute(query, args):
                if str(post_id) not in self._exclude_ids:
                    ids.append(str(post_id))
                    if len(ids) >= limit:
                        break
            return ids
        finally:
            db.close()

    def fetch(self, since: datetime | None, limit: int) -> Iterable[RawItem]:
        """Yield up to ``limit`` posts, highest score first, skipping ``exclude_ids`` (already stored)."""
        ids = self._post_ids(since, limit)
        posts = {str(p.get("id")): p for p in self._client.posts_by_id(ids)}
        yielded = 0
        for post_id in ids:
            post = posts.get(post_id)
            if post is None or is_removed(post):
                continue
            comments = self._client.top_comments(post_id) if int(post.get("num_comments") or 0) > 0 else []
            yielded += 1
            yield post_to_raw_item(post, source=self.name, comments=comments)
        logger.info("wsb_archive: %d of %d archived posts hydrated", yielded, len(ids))


def mention_counts(days: int = 30, db_path: Path | None = None) -> dict[str, int]:
    """Token -> titles naming it over the archive's last ``days`` covered days.

    Cashtags (``$TSLA``) always count; bare upper-case words only from three
    letters up (``A``/``DD``-style tokens are mostly words). Everyday words
    (``YOLO``, ``CEO``) still rank, so callers keep only symbols they have data for.
    """
    path = db_path or archive_path()
    if not path.exists():
        return {}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        rows = db.execute(
            "SELECT token, SUM(cashtag + CASE WHEN length(token) >= 3 THEN upper ELSE 0 END) FROM tokens "
            "WHERE day >= (SELECT date(MAX(day), ?) FROM coverage) GROUP BY token",
            (f"-{days} days",),
        ).fetchall()
    finally:
        db.close()
    return {str(t): int(n) for t, n in rows}
