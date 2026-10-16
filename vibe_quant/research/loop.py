"""Autonomous research loop: sources -> extraction -> screening -> discovery -> gates -> paper.

One long-running process (``python -m vibe_quant.research loop``) that drives
the existing pipeline unattended, every ``interval_minutes``:

1. **Ideas.** Each due source is scraped through :func:`run_scrape` with the
   default extractor, so every new item is tagged, extracted, DSL-validated and
   auto-screened exactly as a manual scrape. Default schedule: ``reddit`` (the
   saved subreddits' new posts since the last run) and ``news`` (market and
   company headlines, 8-K filings, upcoming earnings) every cycle, the
   ``wsb_archive`` backfill every cycle, ``arctic_shift`` settled high-score
   posts daily, ``trader_methods`` weekly. A post seen by several Reddit
   sources is one item.
2. **Promote ideas.** Parsed extractions whose auto-screen clears the promote
   thresholds become strategies (:func:`promote_extraction`), and their
   indicators steer the next discovery runs' indicator pool — Reddit decides
   what the GA searches.
3. **Discovery per ticker group.** Groups are built each cycle: WallStreetBets
   hot names (30-day title mentions), WSB favourites (1 year), volatility
   buckets (calm/normal/wild by median daily range), and configured static
   groups — each filtered to symbols the catalog has bars for. One group per
   cycle (round robin) gets a discovery run through ``POST /api/discovery/launch``
   with the default holdout and fail-closed gates, outside market hours only.
4. **Validate champions.** Champions of finished loop runs (they passed every
   gate) are promoted with ``mode=validation`` on the held-out range.
5. **Trade.** A holdout validation that clears the paper thresholds is queued for
   the regime tuner (``require_regime_pass``): it must make money in bull and bear
   markets first, and the tuner starts the paper session (``POST /api/paper/start``)
   once it settles. With ``require_regime_pass`` off
   the loop starts the session itself when none is running.

Heavy jobs go through the backend API so they are tracked and visible in the
UI like manual launches; the loop's own bookkeeping lives in
``data/state/research_loop.json``. Config: defaults below, overridden by
``data/state/research_loop_config.json`` (same keys) and a few env vars.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx

from vibe_quant.research.pipeline import (
    PromoteError,
    promote_extraction,
    reap_orphan_scrape_runs,
    run_scrape,
)

if TYPE_CHECKING:
    from vibe_quant.db.state_manager import StateManager

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
STATE_DIR = Path("data/state")
STATE_PATH = STATE_DIR / "research_loop.json"
CONFIG_PATH = STATE_DIR / "research_loop_config.json"
ENV_API_URL = "VQ_API_URL"


# Re-read this much before the last run's start: archives index posts and articles late.
SINCE_OVERLAP = timedelta(hours=3)


@dataclass
class SourceSchedule:
    name: str
    limit: int
    every_cycles: int = 1
    # Only what appeared since the source's last completed run (minus SINCE_OVERLAP).
    since_last_run: bool = False


def _default_sources() -> list[SourceSchedule]:
    return [
        SourceSchedule("reddit", 15, since_last_run=True),  # per subreddit
        SourceSchedule("news", 150, since_last_run=True),
        SourceSchedule("wsb_archive", 10),
        SourceSchedule("arctic_shift", 10, every_cycles=24),  # per subreddit
        SourceSchedule("trader_methods", 10, every_cycles=168),
    ]


def _default_timeframes() -> list[str]:
    if os.getenv("VQ_RESEARCH_MARKET", "").strip().lower() == "us_stocks":
        return ["1d"]
    return ["4h"]


@dataclass
class LoopConfig:
    api_url: str = field(default_factory=lambda: os.getenv(ENV_API_URL, "http://127.0.0.1:8000"))
    interval_minutes: int = 60
    sources: list[SourceSchedule] = field(default_factory=_default_sources)
    # Promote an extracted idea when its auto-screen shows this much.
    promote_min_trades: int = 10
    promote_min_profit_factor: float = 1.2
    promote_min_return: float = 0.0
    # Discovery
    discovery_timeframes: list[str] = field(default_factory=_default_timeframes)
    discovery_lookback_days: int = 3 * 365
    discovery_population: int = 12
    discovery_generations: int = 8
    discovery_max_workers: int = 1
    discovery_symbol_agg: str = "worst"
    discovery_outside_market_hours: bool = True
    group_size: int = 8
    static_groups: dict[str, list[str]] = field(default_factory=dict)
    idea_lookback_days: int = 30
    # Paper sessions started by the loop
    auto_paper: bool = True
    # A holdout winner must also make money in bear and bull markets: it is queued for the
    # regime tuner (vibe_quant.research.tuner), which starts the paper session once it settles.
    require_regime_pass: bool = True
    paper_min_trades: int = 5
    paper_min_profit_factor: float = 1.2
    paper_min_return: float = 0.0
    paper_max_position_pct: float = 1.0  # the full account per position (Peter: no 50% cap)
    paper_max_position_count: int = 1

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> LoopConfig:
        cfg = cls()
        if path.exists():
            raw = json.loads(path.read_text())
            for key, value in raw.items():
                if key == "sources":
                    cfg.sources = [SourceSchedule(**s) for s in value]
                elif hasattr(cfg, key):
                    setattr(cfg, key, value)
                else:
                    logger.warning("research loop config: unknown key %r ignored", key)
        return cfg


@dataclass
class LoopState:
    cycle: int = 0
    last_source_cycle: dict[str, int] = field(default_factory=dict)
    group_cursor: int = 0
    discovery_runs: dict[str, dict[str, Any]] = field(default_factory=dict)  # run_id -> {group, ...}
    validations: dict[str, dict[str, Any]] = field(default_factory=dict)  # run_id -> {strategy_id, ...}
    paper_started: list[int] = field(default_factory=list)
    promoted_extractions: list[int] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path = STATE_PATH) -> LoopState:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path = STATE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=1, sort_keys=True))
        tmp.replace(path)


# -- helpers ---------------------------------------------------------------------------------------


def in_market_hours(now: datetime | None = None) -> bool:
    """US regular session (with a little margin), weekdays, New York time."""
    local = (now or datetime.now(tz=UTC)).astimezone(NEW_YORK)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return 9 * 60 <= minutes < 16 * 60 + 30


def catalog_symbols(symbols: list[str], timeframe: str) -> list[str]:
    """The subset of ``symbols`` the catalog has ``timeframe`` bars for."""
    try:
        from vibe_quant.data.catalog import DEFAULT_CATALOG_PATH, CatalogManager

        catalog = CatalogManager(DEFAULT_CATALOG_PATH)
    except Exception:  # noqa: BLE001
        logger.warning("research loop: catalog unavailable", exc_info=True)
        return []
    out = []
    for symbol in symbols:
        with contextlib.suppress(Exception):
            if catalog.get_bar_date_range(symbol, timeframe) is not None:
                out.append(symbol)
    return out


def median_daily_range_pct(symbol: str, bars: int = 120) -> float | None:
    """Median (high - low) / close of the last ``bars`` daily bars, in percent."""
    try:
        from vibe_quant.data.catalog import DEFAULT_CATALOG_PATH, CatalogManager

        rows = CatalogManager(DEFAULT_CATALOG_PATH).get_bars(symbol, "1d")[-bars:]
    except Exception:  # noqa: BLE001
        return None
    ranges = [
        (float(b.high) - float(b.low)) / float(b.close) * 100 for b in rows if float(b.close) > 0
    ]
    return statistics.median(ranges) if len(ranges) >= 20 else None


# Tagged research posts (market_tags) feed the "reddit_tagged" group: tickers of
# stock / option / ETF posts fetched in the last TAGGED_GROUP_DAYS days.
TAGGED_GROUP_DAYS = 14
TAGGED_GROUP_MARKETS = ["us_stocks", "options", "etf_index"]


def ticker_groups(
    cfg: LoopConfig, timeframe: str, sm: StateManager | None = None
) -> dict[str, list[str]]:
    """Auto groups for discovery, each limited to symbols with catalog data."""
    from vibe_quant.research.sources.wsb_archive import mention_counts

    def ranked(days: int) -> list[str]:
        counts = mention_counts(days)
        return [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])][: cfg.group_size * 20]

    hot = catalog_symbols(ranked(30), timeframe)[: cfg.group_size]
    favourites = catalog_symbols(ranked(365), timeframe)[: cfg.group_size]
    groups: dict[str, list[str]] = {"wsb_hot": hot, "wsb_favorites": favourites}
    if sm is not None:
        tagged = [t for t, _ in sm.recent_tagged_tickers(days=TAGGED_GROUP_DAYS, markets=TAGGED_GROUP_MARKETS)]
        groups["reddit_tagged"] = catalog_symbols(tagged[: cfg.group_size * 5], timeframe)[: cfg.group_size]
    for name, members in cfg.static_groups.items():
        groups[name] = catalog_symbols([m.upper() for m in members], timeframe)
    pool = sorted({s for members in groups.values() for s in members})
    buckets: dict[str, list[str]] = {"calm": [], "normal": [], "wild": []}
    for symbol in pool:
        rng = median_daily_range_pct(symbol)
        if rng is None:
            continue
        bucket = "calm" if rng < 2.5 else "normal" if rng <= 4.5 else "wild"
        buckets[bucket].append(symbol)
    groups.update({k: v[: cfg.group_size] for k, v in buckets.items()})
    return {k: v for k, v in groups.items() if v}


def idea_indicators(sm: StateManager, days: int) -> list[str]:
    """Indicator types used by recent parsed extractions (Reddit's ideas steer the GA pool)."""
    since = (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    types: set[str] = set()
    for raw in sm.list_recent_extraction_dsls(since):
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            for spec in (json.loads(raw).get("indicators") or {}).values():
                if isinstance(spec, dict) and spec.get("type"):
                    types.add(str(spec["type"]).upper())
    return sorted(types)


# -- the loop --------------------------------------------------------------------------------------


class ResearchLoop:
    def __init__(
        self,
        sm: StateManager,
        cfg: LoopConfig | None = None,
        client: httpx.Client | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.sm = sm
        self.cfg = cfg or LoopConfig.load()
        self.state = LoopState.load()
        self.http = client or httpx.Client(base_url=self.cfg.api_url, timeout=60.0)
        self.dry_run = dry_run

    # -- stage 1: ideas ----------------------------------------------------------------------------

    def run_sources(self) -> None:
        from vibe_quant.research.extractor import get_default_extractor

        if not self.dry_run:
            reap_orphan_scrape_runs(self.sm)  # this loop's own runs from before a restart

        try:
            extractor = get_default_extractor()
        except (FileNotFoundError, ImportError) as e:
            logger.warning("research loop: no extractor (%s); items stay pending", e)
            extractor = None
        for spec in self.cfg.sources:
            last = self.state.last_source_cycle.get(spec.name)
            if last is not None and self.state.cycle - last < spec.every_cycles:
                continue
            if self.sm.list_active_scrape_runs(source=spec.name):
                logger.info("research loop: %s already scraping; skipped", spec.name)
                continue
            if self.dry_run:
                logger.info("research loop (dry run): would scrape %s limit=%d", spec.name, spec.limit)
                continue
            try:
                since = self._since_last_run(spec.name) if spec.since_last_run else None
                summary = run_scrape(
                    sm=self.sm, source_name=spec.name, limit=spec.limit, extract_fn=extractor, since=since
                )
                logger.info(
                    "research loop: %s fetched=%d new=%d duplicate=%d extracted=%d skipped=%d failed=%d status=%s",
                    spec.name, summary.items_fetched, summary.items_new,
                    summary.items_fetched - summary.items_new, summary.items_extracted,
                    summary.items_skipped, summary.items_failed, summary.status,
                )
            except Exception:  # noqa: BLE001 — one bad source never stops the loop
                logger.exception("research loop: source %s failed", spec.name)
            self.state.last_source_cycle[spec.name] = self.state.cycle

    def _since_last_run(self, source: str) -> datetime | None:
        started = self.sm.last_completed_scrape_start(source)
        if not started:
            return None
        return datetime.fromisoformat(started).replace(tzinfo=UTC) - SINCE_OVERLAP

    # -- stage 2: promote ideas --------------------------------------------------------------------

    def promote_ideas(self) -> list[int]:
        cfg = self.cfg
        rows = self.sm.list_promotable_extractions(
            min_trades=cfg.promote_min_trades,
            min_profit_factor=cfg.promote_min_profit_factor,
            min_return=cfg.promote_min_return,
        )
        promoted = []
        for row in rows:
            extraction_id, trades, pf, ret = row["id"], row["screen_trades"], row["screen_pf"], row["screen_return"]
            if self.dry_run:
                logger.info("research loop (dry run): would promote extraction %s", extraction_id)
                continue
            try:
                strategy_id = promote_extraction(self.sm, int(extraction_id))
            except PromoteError as e:
                logger.info("research loop: extraction %s not promoted: %s", extraction_id, e)
                continue
            logger.info(
                "research loop: promoted extraction %s -> strategy %s (screen trades=%s pf=%s return=%s)",
                extraction_id, strategy_id, trades, pf, ret,
            )
            self.state.promoted_extractions.append(int(extraction_id))
            promoted.append(strategy_id)
        return promoted

    # -- stage 3: discovery ------------------------------------------------------------------------

    def _api(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    def launch_discovery(self) -> int | None:
        cfg = self.cfg
        if cfg.discovery_outside_market_hours and in_market_hours():
            logger.info("research loop: market hours; discovery waits")
            return None
        try:
            jobs = self._api("GET", "/api/discovery/jobs")
        except (httpx.HTTPError, RuntimeError) as e:
            logger.warning("research loop: backend unreachable (%s); discovery skipped", e)
            return None
        if any(j.get("status") in ("running", "pending") for j in jobs):
            logger.info("research loop: a discovery run is in progress; waiting")
            return None
        timeframe = cfg.discovery_timeframes[self.state.cycle % len(cfg.discovery_timeframes)]
        groups = ticker_groups(cfg, timeframe, self.sm)
        if not groups:
            logger.info("research loop: no ticker group has %s catalog data yet; discovery skipped", timeframe)
            return None
        names = sorted(groups)
        name = names[self.state.group_cursor % len(names)]
        self.state.group_cursor += 1
        pool_names = {str(p["name"]).upper(): str(p["name"]) for p in self._api("GET", "/api/discovery/indicator-pool")}
        ideas = [pool_names[t] for t in idea_indicators(self.sm, cfg.idea_lookback_days) if t in pool_names]
        end = datetime.now(tz=UTC).date()
        start = end - timedelta(days=cfg.discovery_lookback_days)
        body: dict[str, Any] = {
            "symbols": groups[name],
            "timeframes": [timeframe],
            "population": cfg.discovery_population,
            "generations": cfg.discovery_generations,
            "max_workers": cfg.discovery_max_workers,
            "symbol_agg": cfg.discovery_symbol_agg,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        if len(ideas) >= 2:
            body["indicator_pool"] = ideas
        if self.dry_run:
            logger.info("research loop (dry run): would launch discovery %s", json.dumps(body))
            return None
        job = self._api("POST", "/api/discovery/launch", json=body)
        run_id = int(job["run_id"])
        self.state.discovery_runs[str(run_id)] = {
            "group": name,
            "symbols": groups[name],
            "timeframe": timeframe,
            "indicator_pool": body.get("indicator_pool"),
            "launched_at": datetime.now(tz=UTC).isoformat(),
            "processed": False,
        }
        logger.info("research loop: discovery run %s on %s (%s): %s", run_id, name, timeframe, groups[name])
        return run_id

    # -- stage 4: validate champions ---------------------------------------------------------------

    def validate_champions(self) -> list[int]:
        launched = []
        for run_id, info in self.state.discovery_runs.items():
            if info.get("processed"):
                continue
            try:
                job = self._api("GET", f"/api/discovery/jobs/{run_id}")
            except (httpx.HTTPError, RuntimeError):
                continue
            status = job.get("status")
            if status in ("running", "pending"):
                continue
            info["processed"] = True
            info["status"] = status
            if status != "completed":
                continue
            results = self._api("GET", f"/api/discovery/results/{run_id}")
            champions = results.get("strategies") or []
            info["champions"] = len(champions)
            info["reason"] = results.get("reason")
            logger.info("research loop: discovery %s finished with %d champion(s)", run_id, len(champions))
            for index in range(len(champions)):
                if self.dry_run:
                    continue
                try:
                    promoted = self._api(
                        "POST",
                        f"/api/discovery/results/{run_id}/promote/{index}",
                        params={"mode": "validation", "validation_range": "holdout"},
                    )
                except RuntimeError as e:
                    logger.warning("research loop: champion %s/%d not validated: %s", run_id, index, e)
                    continue
                self.state.validations[str(promoted["run_id"])] = {
                    "strategy_id": promoted["strategy_id"],
                    "discovery_run_id": int(run_id),
                    "champion_index": index,
                    "date_range": promoted.get("date_range"),
                    "processed": False,
                }
                launched.append(int(promoted["run_id"]))
        return launched

    # -- stage 5: trade winners --------------------------------------------------------------------

    def start_paper(self) -> int | None:
        cfg = self.cfg
        for run_id, info in self.state.validations.items():
            if info.get("processed"):
                continue
            try:
                job = self._api("GET", f"/api/backtest/jobs/{run_id}")
            except (httpx.HTTPError, RuntimeError):
                continue
            if job.get("status") in ("running", "pending"):
                continue
            info["processed"] = True
            try:
                result = self._api("GET", f"/api/results/runs/{run_id}")
            except RuntimeError as e:
                info["verdict"] = f"no result: {e}"
                continue
            trades = result.get("total_trades") or 0
            pf = result.get("profit_factor") or 0.0
            ret = result.get("total_return") or 0.0
            passed = trades >= cfg.paper_min_trades and pf >= cfg.paper_min_profit_factor and ret > cfg.paper_min_return
            info.update({"trades": trades, "profit_factor": pf, "total_return": ret, "passed": passed})
            logger.info(
                "research loop: holdout validation %s trades=%s pf=%s return=%s -> %s",
                run_id, trades, pf, ret, "PASS" if passed else "fail",
            )
            if not passed or not cfg.auto_paper or self.dry_run:
                continue
            if cfg.require_regime_pass:
                from vibe_quant.research.tuner import TunerConfig

                tuner_cfg = TunerConfig.load()
                strategy_id = int(info["strategy_id"])
                if strategy_id not in tuner_cfg.candidate_ids:
                    tuner_cfg.candidate_ids.append(strategy_id)
                    tuner_cfg.save()
                info["verdict"] = "passed the holdout; queued for the regime tuner (bull + bear) before paper"
                logger.info("research loop: strategy %s queued for the regime tuner", strategy_id)
                continue
            status = self._api("GET", "/api/paper/status")
            if status.get("state") not in ("idle", None):
                info["verdict"] = "passed; waiting for the running paper session to end"
                info["processed"] = False
                continue
            body = {
                "validation_run_id": int(run_id),
                "testnet": True,
                "max_position_pct": cfg.paper_max_position_pct,
                "max_position_count": cfg.paper_max_position_count,
            }
            try:
                started = self._api("POST", "/api/paper/start", json=body)
            except RuntimeError as e:
                info["verdict"] = f"paper start failed: {e}"
                logger.warning("research loop: paper start for validation %s failed: %s", run_id, e)
                continue
            self.state.paper_started.append(int(run_id))
            info["verdict"] = "paper started"
            logger.info("research loop: paper session started from validation %s: %s", run_id, started)
            return int(run_id)
        return None

    # -- driver ------------------------------------------------------------------------------------

    def cycle(self) -> None:
        logger.info("research loop: cycle %d", self.state.cycle)
        for stage in (self.run_sources, self.promote_ideas, self.validate_champions, self.start_paper, self.launch_discovery):
            try:
                stage()
            except Exception:  # noqa: BLE001 — a failing stage never stops the loop
                logger.exception("research loop: stage %s failed", stage.__name__)
            if not self.dry_run:
                self.state.save()
        self.state.cycle += 1
        if not self.dry_run:
            self.state.save()

    def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            self.cycle()
            elapsed = time.monotonic() - started
            time.sleep(max(60.0, self.cfg.interval_minutes * 60 - elapsed))
