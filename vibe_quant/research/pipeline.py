"""Orchestrator: source.fetch → archive → (extract).

Designed to be invoked from the CLI as a long-lived subprocess. Maintains a
heartbeat row in `research_scrape_runs` so the API can detect crashed
runners.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibe_quant.research.archive import REDDIT_SOURCES, archive_item
from vibe_quant.research.extraction_log import log_dir_for_scrape, write_extraction_log
from vibe_quant.research.extractor import extractor_version
from vibe_quant.research.image_archive import archive_item_images
from vibe_quant.research.sources import get_source, load_builtin_sources
from vibe_quant.research.sources.arctic_shift import ENV_SUBREDDITS as ARCTIC_ENV_SUBREDDITS

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from vibe_quant.db.state_manager import StateManager
    from vibe_quant.research.market_tags import MarketTagger
    from vibe_quant.research.schema import ExtractionBatch, ExtractionResult, RawItem

    ExtractFn = Callable[[RawItem, int], ExtractionBatch]

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30


@dataclass
class ScrapeSummary:
    scrape_run_id: int
    items_fetched: int
    items_new: int
    items_extracted: int
    items_failed: int
    items_skipped: int
    status: str
    error_message: str | None


def _build_source(
    source_name: str,
    sm: StateManager,
    source_kwargs: dict[str, object] | None = None,
) -> object:
    load_builtin_sources()
    cls = get_source(source_name)
    kwargs: dict[str, object] = dict(source_kwargs or {})
    if source_name in ("reddit", "arctic_shift"):
        saved = sm.get_research_subreddits(source_name)
        if not saved and source_name == "arctic_shift" and not os.getenv(ARCTIC_ENV_SUBREDDITS):
            # Same subreddits as the reddit source unless arctic_shift has its own list.
            saved = sm.get_research_subreddits("reddit")
        if saved and "subreddits" not in kwargs:
            kwargs["subreddits"] = [s.lower() for s in saved]
    elif source_name == "news" and "tickers" not in kwargs:
        from vibe_quant.research.sources.news import watch_tickers

        kwargs["tickers"] = watch_tickers(sm)
    elif source_name == "wsb_archive" and "exclude_ids" not in kwargs:
        # A backfill: skip posts any Reddit source already stored, so each run adds new ones.
        kwargs["exclude_ids"] = sm.list_research_external_ids(REDDIT_SOURCES)
    return cls(**kwargs)  # each source supplies its own env-driven defaults


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # someone else's process: assume it's ours, don't reap
    return True


def reap_orphan_scrape_runs(sm: StateManager) -> list[int]:
    """Mark 'running' scrape runs whose process is gone as killed; return their ids.

    A run's process can die without finalizing its row (service restart,
    crash, SIGKILL), which would block new scrapes of that source with a 409
    forever.
    """
    from datetime import UTC, datetime

    reaped: list[int] = []
    now = datetime.now(tz=UTC)
    for row in sm.list_active_scrape_runs():
        # Live runs heartbeat every HEARTBEAT_INTERVAL_SECONDS: only a run whose
        # heartbeat stopped AND whose process is gone (or never recorded) is an orphan.
        stamp = row.get("heartbeat_at") or row.get("started_at")
        try:
            last = datetime.fromisoformat(str(stamp)).replace(tzinfo=UTC)
        except ValueError:
            last = now
        if (now - last).total_seconds() < 4 * HEARTBEAT_INTERVAL_SECONDS:
            continue
        pid = row.get("pid")
        if isinstance(pid, int) and pid > 0 and _pid_alive(pid):
            continue
        sm.complete_scrape_run(int(row["id"]), status="killed", error_message="process gone (reaped)")
        reaped.append(int(row["id"]))
    if reaped:
        logger.warning("reaped %d orphan scrape run(s): %s", len(reaped), reaped)
    return reaped


def _market_tagger(sm: StateManager) -> MarketTagger | None:
    from vibe_quant.research.market_tags import MarketTagger

    try:
        return MarketTagger(sm)
    except Exception:  # noqa: BLE001
        logger.exception("market tagger unavailable; items stay untagged until the worker runs")
        return None


def run_scrape(
    *,
    sm: StateManager,
    source_name: str,
    limit: int,
    extract_fn: ExtractFn | None = None,
    scrape_run_id: int | None = None,
    source_kwargs: dict[str, object] | None = None,
    since: datetime | None = None,
) -> ScrapeSummary:
    """Run one scrape pass and return a summary.

    Creates the `research_scrape_runs` row (or adopts an existing one when
    `scrape_run_id` is provided — used by the API to pre-create rows before
    spawning subprocesses), drives the source, archives every `RawItem`,
    optionally invokes `extract_fn` per archived item, and finalizes the run row.

    Args:
        sm: A `StateManager` already pointed at the right DB.
        source_name: Registered source name (e.g., `"reddit"`).
        limit: Per-source cap on items to scrape.
        extract_fn: Optional callable `(item: RawItem, item_id: int) -> ExtractionResult`.
            If `None`, items land at `extraction_status='pending'`.
        scrape_run_id: When set, adopt this existing row instead of creating
            a new one. The caller is responsible for the row's existence.
    """
    # Build the source up-front so unknown name / missing creds / config
    # errors don't leave an orphan scrape_run row in the DB.
    source = _build_source(source_name, sm, source_kwargs)

    pid = os.getpid()
    if scrape_run_id is None:
        config: dict[str, object] = {
            "source": source_name,
            "limit": limit,
            "extract": extract_fn is not None,
        }
        if source_kwargs:
            config["source_kwargs"] = source_kwargs
        scrape_run_id = sm.create_scrape_run(
            source=source_name,
            pid=pid,
            config=config,
        )
    else:
        sm.adopt_scrape_run(scrape_run_id, pid)

    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.is_set():
            with contextlib.suppress(Exception):
                sm.update_scrape_run_heartbeat(scrape_run_id)
            stop_event.wait(HEARTBEAT_INTERVAL_SECONDS)

    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()

    # SIGTERM → finalize as killed and let the iterator unwind
    kill_flag = threading.Event()
    prev_handlers: dict[signal.Signals, object] = {}

    def _signal_handler(signum: int, _frame: object) -> None:  # noqa: ARG001
        logger.warning("scrape_run %s received signal %s; finalizing as killed", scrape_run_id, signum)
        kill_flag.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(ValueError):
            prev_handlers[sig] = signal.signal(sig, _signal_handler)

    fetched = new = extracted = failed = skipped = 0
    error_message: str | None = None
    final_status = "completed"
    tagger = _market_tagger(sm)

    try:
        # A source may opt items out of the extraction call (e.g. news without rules).
        wants_extraction = getattr(source, "wants_extraction", None)
        for item in source.fetch(since=since, limit=limit):  # type: ignore[attr-defined]
            if kill_flag.is_set():
                final_status = "killed"
                break
            fetched += 1
            # Download any post images before archiving so the persisted
            # extras_json carries local `image_paths` for the vision extractor.
            # Best-effort: a download failure must not sink the whole item.
            try:
                archive_item_images(item)
            except Exception:  # noqa: BLE001
                logger.exception("image archive failed for %s/%s", item.source, item.external_id)
            try:
                was_new, item_id = archive_item(sm, item)
            except Exception:  # noqa: BLE001
                logger.exception("archive failed for %s/%s", item.source, item.external_id)
                failed += 1
                sm.increment_scrape_run_counters(scrape_run_id, fetched=1, failed=1)
                continue
            sm.increment_scrape_run_counters(
                scrape_run_id, fetched=1, new=1 if was_new else 0
            )
            if was_new:
                new += 1
                if tagger is not None and item_id is not None:
                    # Market tags for every new item (before extraction so auto-screen
                    # can use its tickers). Never sinks the item: the worker retries.
                    try:
                        tagger.tag(item_id, item)
                    except Exception:  # noqa: BLE001
                        logger.exception("market tagging failed for item_id=%s", item_id)
                if item_id is not None and callable(wants_extraction) and not wants_extraction(item):
                    sm.update_research_item_status(item_id, "skipped")
                    skipped += 1
                    continue
                if extract_fn is not None and item_id is not None:
                    try:
                        batch: ExtractionBatch = extract_fn(item, item_id)
                    except Exception:  # noqa: BLE001
                        logger.exception("extractor failed for item_id=%s", item_id)
                        failed += 1
                        sm.update_research_item_status(item_id, "failed")
                        sm.increment_scrape_run_counters(scrape_run_id, failed=1)
                        continue
                    write_extraction_log(
                        log_dir=log_dir_for_scrape(scrape_run_id),
                        item_id=item_id,
                        batch=batch,
                        extractor_version=extractor_version(),
                        scrape_run_id=scrape_run_id,
                    )
                    results = batch.results
                    persist_extractions(sm, item_id, results, prompt=batch.prompt)
                    item_parsed = sum(1 for r in results if r.status == "parsed")
                    item_failed = sum(1 for r in results if r.status == "failed")
                    item_skipped = sum(1 for r in results if r.status == "skipped")
                    extracted += item_parsed
                    failed += item_failed
                    skipped += item_skipped
                    if item_parsed:
                        sm.increment_scrape_run_counters(scrape_run_id, extracted=item_parsed)
                    if item_failed:
                        sm.increment_scrape_run_counters(scrape_run_id, failed=item_failed)
    except KeyboardInterrupt:
        final_status = "killed"
    except Exception as e:  # noqa: BLE001
        logger.exception("scrape_run %s failed", scrape_run_id)
        final_status = "failed"
        error_message = f"{type(e).__name__}: {e}"
    finally:
        stop_event.set()
        hb_thread.join(timeout=HEARTBEAT_INTERVAL_SECONDS + 1)
        # restore signal handlers
        for sig, prev in prev_handlers.items():
            with contextlib.suppress(ValueError, TypeError):
                signal.signal(sig, prev)  # type: ignore[arg-type]
        with contextlib.suppress(Exception):
            source.close()  # type: ignore[attr-defined]
        sm.complete_scrape_run(
            scrape_run_id,
            status=final_status,
            error_message=error_message,
        )

    return ScrapeSummary(
        scrape_run_id=scrape_run_id,
        items_fetched=fetched,
        items_new=new,
        items_extracted=extracted,
        items_failed=failed,
        items_skipped=skipped,
        status=final_status,
        error_message=error_message,
    )


_ITEM_STATUS_MAP = {"parsed": "extracted", "failed": "failed", "skipped": "skipped"}
_ITEM_STATUS_PRIORITY = {"extracted": 0, "failed": 1, "skipped": 2}


def persist_extractions(
    sm: StateManager,
    item_id: int,
    results: list[ExtractionResult],
    *,
    prompt: str | None = None,
) -> None:
    """Write one extraction row per result + roll up to a single item status.

    Item status precedence: any parsed → "extracted"; else any failed →
    "failed"; else "skipped". Empty results land as "failed" so the item
    isn't left at "running".
    """
    if not results:
        sm.update_research_item_status(item_id, "failed")
        return
    from vibe_quant.research import auto_screen as _auto_screen

    for result in results:
        extraction_id = sm.create_extraction(
            research_item_id=item_id,
            status=result.status,
            llm_model=result.llm_model,
            confidence=result.confidence,
            rationale=result.rationale,
            raw_response=result.raw_response,
            dsl_yaml=result.dsl_yaml,
            parsed_dsl_json=result.parsed_dsl_json,
            parse_error=result.parse_error,
            evidence_level=result.evidence_level,
            completeness=result.completeness,
            proposed_indicators_json=result.proposed_indicators_json,
            prompt=prompt,
            risk_management_json=result.risk_management_json,
            notable_parameters_json=result.notable_parameters_json,
        )
        if result.status == "parsed" and result.parsed_dsl_json:
            _auto_screen.auto_screen_extraction(
                sm, extraction_id, result.parsed_dsl_json
            )
    item_statuses = [_ITEM_STATUS_MAP.get(r.status, "failed") for r in results]
    rolled = min(item_statuses, key=lambda s: _ITEM_STATUS_PRIORITY.get(s, 99))
    sm.update_research_item_status(item_id, rolled)


class PromoteError(ValueError):
    """An extraction that can't be promoted (``status_code`` maps to the API's HTTP status)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def promote_extraction(sm: StateManager, extraction_id: int) -> int:
    """Turn a parsed extraction into a ``strategies`` row; return its id.

    Shared by the API (``POST /api/research/extractions/{id}/promote``) and the
    research loop.
    """
    import json

    ex = sm.get_extraction(extraction_id)
    if not ex:
        raise PromoteError(f"extraction {extraction_id} not found", 404)
    status = ex.get("status")
    if status == "promoted":
        raise PromoteError(f"already promoted to strategy_id={ex.get('strategy_id')}")
    if status == "rejected":
        raise PromoteError("cannot promote a rejected extraction; re-extract first")
    if status != "parsed":
        raise PromoteError(f"cannot promote unparsed extraction (status={status})")
    parsed_json = ex.get("parsed_dsl_json")
    if not isinstance(parsed_json, str) or not parsed_json:
        raise PromoteError("extraction has no parsed_dsl_json")
    try:
        dsl_dict = json.loads(parsed_json)
    except json.JSONDecodeError as e:
        raise PromoteError(f"corrupt parsed_dsl_json: {e}", 500) from e
    item = sm.get_research_item(int(ex["research_item_id"]))
    base_name = (
        f"{item['source']}_{item['external_id']}" if item else f"research_extraction_{extraction_id}"
    )
    name = base_name.lower().replace("-", "_")[:100]
    strategy_id = sm.create_strategy(name=name, dsl_config=dsl_dict)
    sm.update_extraction_status(extraction_id, status="promoted", strategy_id=strategy_id)
    return int(strategy_id)


def persist_extraction(sm: StateManager, item_id: int, result: ExtractionResult) -> None:
    """Back-compat single-result wrapper. Prefer ``persist_extractions``."""
    persist_extractions(sm, item_id, [result])
