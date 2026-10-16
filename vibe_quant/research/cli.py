"""CLI entry point for the research pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from vibe_quant.alerts.telegram import ConfigurationError
from vibe_quant.db.state_manager import StateManager
from vibe_quant.research.pipeline import run_scrape
from vibe_quant.research.sources import get_source, list_sources, load_builtin_sources
from vibe_quant.research.sources.reddit import VALID_LISTINGS, VALID_TIME_FILTERS

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibe_quant.research")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scrape = sub.add_parser("scrape", help="Run one scrape pass against a source")
    scrape.add_argument("--source", required=True, help="Registered source name (e.g., reddit)")
    scrape.add_argument("--limit", type=int, default=50, help="Max items per source")
    scrape.add_argument(
        "--no-extract",
        action="store_true",
        help="Skip the LLM extraction step (items left at extraction_status=pending)",
    )
    scrape.add_argument("--db-path", type=Path, default=None, help="Override SQLite path")
    scrape.add_argument(
        "--scrape-run-id",
        type=int,
        default=None,
        help="Adopt an existing scrape_run row (used by API-spawned subprocesses)",
    )
    scrape.add_argument(
        "--reddit-listing",
        choices=sorted(VALID_LISTINGS),
        default=None,
        help="Reddit-only: which listing to pull from (default: new)",
    )
    scrape.add_argument(
        "--reddit-time-filter",
        choices=sorted(VALID_TIME_FILTERS),
        default=None,
        help="Reddit-only: time window when --reddit-listing=top (e.g. week, month)",
    )
    scrape.add_argument("--log-level", default="INFO")

    sub.add_parser("sources", help="List registered sources")

    loop = sub.add_parser(
        "loop",
        help="Run the autonomous research loop (sources -> extraction -> discovery -> gates -> paper)",
    )
    loop.add_argument("--once", action="store_true", help="Run one cycle and exit")
    loop.add_argument(
        "--dry-run", action="store_true", help="Log what each stage would do; change nothing"
    )
    loop.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Loop config JSON (default data/state/research_loop_config.json)",
    )
    loop.add_argument(
        "--interval-min", type=int, default=None, help="Minutes between cycles (overrides config)"
    )
    loop.add_argument("--db-path", type=Path, default=None, help="Override SQLite path")
    loop.add_argument("--log-level", default="INFO")

    tune = sub.add_parser(
        "tune",
        help="Run the regime tuner (test bull/bear/other markets -> AI parameters -> settle -> paper)",
    )
    tune.add_argument("--once", action="store_true", help="Run one attempt and exit")
    tune.add_argument(
        "--strategy-id",
        type=int,
        action="append",
        default=None,
        help="Tune only this strategy (repeat for several, in order)",
    )
    tune.add_argument(
        "--until-done",
        action="store_true",
        help="With --strategy-id: keep going until each strategy settles or runs out of rounds",
    )
    tune.add_argument(
        "--dry-run", action="store_true", help="Log the runs it would launch; change nothing"
    )
    tune.add_argument("--log-level", default="INFO")

    groups = sub.add_parser(
        "groups", help="Print the research loop's ticker groups for a timeframe"
    )
    groups.add_argument("--timeframe", default="1d")
    groups.add_argument("--config", type=Path, default=None)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO)
        if hasattr(args, "log_level")
        else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_builtin_sources()

    if args.cmd == "sources":
        for n in list_sources():
            print(n)
        return 0

    if args.cmd == "tune":
        from vibe_quant.research.tuner import RegimeTuner

        if args.strategy_id and args.until_done and not args.dry_run:
            for strategy_id in args.strategy_id:
                while True:
                    cand = RegimeTuner().step(only=strategy_id)
                    if cand is None or cand["status"] in ("settled", "exhausted"):
                        break
            return 0
        tuner = RegimeTuner(dry_run=args.dry_run)
        if args.once or args.dry_run or args.strategy_id:
            tuner.step(only=args.strategy_id[0] if args.strategy_id else None)
        else:
            tuner.run_forever()
        return 0

    if args.cmd in ("loop", "groups"):
        from vibe_quant.research.loop import CONFIG_PATH, LoopConfig, ResearchLoop, ticker_groups

        cfg = LoopConfig.load(args.config or CONFIG_PATH)
        if args.cmd == "groups":
            for name, members in sorted(ticker_groups(cfg, args.timeframe).items()):
                print(f"{name}: {', '.join(members)}")
            return 0
        if args.interval_min is not None:
            cfg.interval_minutes = args.interval_min
        sm = StateManager(args.db_path)
        try:
            research_loop = ResearchLoop(sm, cfg, dry_run=args.dry_run)
            if args.once or args.dry_run:
                research_loop.cycle()
            else:
                research_loop.run_forever()
        finally:
            sm.close()
        return 0

    if args.cmd == "scrape":
        try:
            get_source(args.source)  # raises KeyError if unknown
        except KeyError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

        sm = StateManager(args.db_path)
        try:
            extract_fn = None
            if not args.no_extract:
                try:
                    from vibe_quant.research.extractor import get_default_extractor

                    extract_fn = get_default_extractor()
                except ImportError:
                    logger.warning(
                        "extractor module not available; running with --no-extract semantics"
                    )
                    extract_fn = None
                except ConfigurationError as e:
                    print(f"error: {e}", file=sys.stderr)
                    return 1
                except FileNotFoundError as e:
                    logger.warning(
                        "extractor unavailable: %s; items will land at extraction_status=pending", e
                    )
                    extract_fn = None
            source_kwargs: dict[str, object] = {}
            if args.source == "reddit":
                if args.reddit_listing is not None:
                    source_kwargs["listing"] = args.reddit_listing
                if args.reddit_time_filter is not None:
                    source_kwargs["time_filter"] = args.reddit_time_filter
            try:
                summary = run_scrape(
                    sm=sm,
                    source_name=args.source,
                    limit=args.limit,
                    extract_fn=extract_fn,
                    scrape_run_id=args.scrape_run_id,
                    source_kwargs=source_kwargs or None,
                )
            except ConfigurationError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
        finally:
            sm.close()

        print(
            f"scrape_run_id={summary.scrape_run_id} status={summary.status} "
            f"fetched={summary.items_fetched} new={summary.items_new} "
            f"extracted={summary.items_extracted} skipped={summary.items_skipped} "
            f"failed={summary.items_failed}"
        )
        return 0 if summary.status in ("completed", "killed") else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
