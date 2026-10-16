"""Persist `RawItem` objects into the `research_items` table.

Thin wrapper over `StateManager` that catches the unique-constraint violation
on `(source, external_id)` so duplicates are silently skipped.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from vibe_quant.db.state_manager import DuplicateResearchItem, StateManager
from vibe_quant.research.schema import RawItem

if TYPE_CHECKING:
    from pathlib import Path


# Sources that store Reddit posts under the Reddit post id: one research item per post.
REDDIT_SOURCES: frozenset[str] = frozenset({"reddit", "arctic_shift", "wsb_archive"})
# Sources whose link identifies the story: a link already stored is a duplicate.
URL_KEYED_SOURCES: frozenset[str] = frozenset({"news"})


def _merged_extras(old: dict[str, Any], new: dict[str, Any], new_source: str) -> dict[str, Any]:
    """Keep the stored post, refreshed with a newer copy's counts, comments and images."""
    merged = dict(old)
    for key in ("num_comments",):
        values = [v for v in (old.get(key), new.get(key)) if isinstance(v, int)]
        if values:
            merged[key] = max(values)
    if len(new.get("comments") or []) >= len(old.get("comments") or []) and new.get("comments"):
        merged["comments"] = new["comments"]
    for key in ("image_urls", "image_paths"):
        combined = list(old.get(key) or [])
        combined += [x for x in new.get(key) or [] if x not in combined]
        if combined:
            merged[key] = combined
    for key in ("flair", "subreddit"):
        if not merged.get(key) and new.get(key):
            merged[key] = new[key]
    seen = set(merged.get("seen_via") or [])
    seen.add(new_source)
    merged["seen_via"] = sorted(seen)
    return merged


def archive_item(
    sm: StateManager,
    item: RawItem,
) -> tuple[bool, int | None]:
    """Insert a RawItem; return (was_new, item_id_or_None_on_dup).

    If `(source, external_id)` already exists, returns `(False, None)` rather
    than raising — pipelines can use the boolean to decide whether to spawn
    extraction. A Reddit post already stored by any Reddit source (same post
    id) is not stored again: the stored item gets the newer score, comment
    count, comments and images instead. A news link already stored is a
    duplicate even under another headline.
    """
    if item.source in URL_KEYED_SOURCES and sm.research_item_with_url(
        source=item.source, url=item.url
    ):
        return False, None
    if item.source in REDDIT_SOURCES and item.external_id:
        existing = sm.find_research_item(external_id=item.external_id, sources=REDDIT_SOURCES)
        if existing is not None:
            old_score = existing.get("score")
            score = (
                max(s for s in (old_score, item.score) if s is not None)
                if (old_score is not None or item.score is not None)
                else None
            )
            sm.merge_research_item(
                int(existing["id"]),
                score=score,
                extras=_merged_extras(existing.get("extras") or {}, item.extras or {}, item.source),
            )
            return False, None
    posted_at_str = item.posted_at.isoformat() if item.posted_at is not None else None
    try:
        item_id = sm.create_research_item(
            source=item.source,
            external_id=item.external_id,
            url=item.url,
            title=item.title,
            body=item.body,
            author=item.author,
            posted_at=posted_at_str,
            score=item.score,
            extras=item.extras or None,
        )
        return True, item_id
    except DuplicateResearchItem:
        return False, None


def open_state_manager(db_path: Path | None = None) -> StateManager:
    """Convenience constructor for non-test callers."""
    return StateManager(db_path)


def row_to_raw_item(row: dict[str, Any]) -> RawItem:
    """Reconstruct a `RawItem` from a `research_items` DB row.

    Used by re-extraction: the API loads the persisted row and feeds it back
    to the extractor without re-fetching from the source.
    """
    extras_str = row.get("extras_json")
    extras = json.loads(extras_str) if isinstance(extras_str, str) and extras_str else {}
    posted_at_str = row.get("posted_at")
    posted_at = (
        datetime.fromisoformat(posted_at_str)
        if isinstance(posted_at_str, str) and posted_at_str
        else None
    )
    return RawItem(
        source=str(row["source"]),
        external_id=str(row["external_id"]),
        url=str(row["url"]),
        title=str(row.get("title") or ""),
        body=str(row.get("body") or ""),
        author=row.get("author"),
        posted_at=posted_at,
        score=row.get("score"),
        extras=extras if isinstance(extras, dict) else {},
    )
