"""Results & analytics router."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from vibe_quant.api.deps import get_state_manager
from vibe_quant.api.schemas.backtest import BacktestRunResponse
from vibe_quant.api.schemas.result import (
    AiSummaryResponse,
    BacktestResultResponse,
    ComparisonResponse,
    DrawdownPoint,
    EquityCurvePoint,
    MonthlyReturn,
    NotesUpdateRequest,
    RunListResponse,
    RunSummaryItem,
    RunSummaryResponse,
    SweepResultResponse,
    TradeResponse,
)
from vibe_quant.balance_settings import recorded_balance
from vibe_quant.db.state_manager import StateManager
from vibe_quant.validation.venue import DEFAULT_STARTING_BALANCE_USDT

logger = logging.getLogger(__name__)


def _enrich_result_with_notes(row: dict[str, Any]) -> dict[str, Any]:
    """Parse `notes` JSON and merge overfitting/cross-window fields into row.

    The discovery pipeline stores a structured JSON payload in
    backtest_results.notes including top_strategies[0] with nested
    `cross_window` and `wfa` sub-objects. Surface these as typed fields so
    the UI can render them without re-parsing JSON.

    Defensive: any parse error or missing key → field stays None.
    """
    enriched = dict(row)
    notes = enriched.get("notes")
    if not notes or not isinstance(notes, str):
        return enriched
    try:
        payload = json.loads(notes)
    except (json.JSONDecodeError, TypeError):
        return enriched
    if not isinstance(payload, dict):
        return enriched

    top = payload.get("top_strategies")
    if not isinstance(top, list) or not top or not isinstance(top[0], dict):
        return enriched
    best = top[0]

    cw = best.get("cross_window")
    if isinstance(cw, dict):
        windows = cw.get("windows")
        offsets = payload.get("cross_window_months")
        min_sharpe = payload.get("cross_window_min_sharpe", 0.5)
        if isinstance(windows, list):
            mapped: list[dict[str, Any]] = []
            for i, w in enumerate(windows):
                if not isinstance(w, dict):
                    continue
                sharpe = w.get("sharpe")
                ret = w.get("return_pct")  # fraction despite the name
                # windows[0] is the original (in-sample) window; windows[i>=1]
                # are shifted by cross_window_months[i-1] (discovery pipeline).
                if i == 0:
                    offset: int | None = 0
                elif isinstance(offsets, list) and i - 1 < len(offsets):
                    offset = offsets[i - 1]
                else:
                    offset = None
                # Same rule as DiscoveryPipeline._evaluate_cross_windows.
                passed = (
                    isinstance(sharpe, (int, float))
                    and isinstance(ret, (int, float))
                    and isinstance(min_sharpe, (int, float))
                    and ret > 0
                    and sharpe >= min_sharpe
                )
                mapped.append(
                    {
                        "offset": offset,
                        "in_sample": i == 0,
                        "sharpe": sharpe,
                        "return_pct": ret,
                        "max_dd": w.get("max_dd"),
                        "trades": w.get("trades"),
                        "passed": passed,
                    }
                )
            enriched["cross_window_results"] = mapped

        if isinstance(cw.get("passed"), bool):
            enriched["cross_window_passed"] = cw["passed"]

    wfa = best.get("wfa")
    if isinstance(wfa, dict):
        sc = wfa.get("sharpe_consistency")
        if isinstance(sc, (int, float)):
            enriched["wfa_sharpe_consistency"] = float(sc)
        # The discovery WFA gate is the return-based consistency, not Sharpe's.
        rc = wfa.get("consistency")
        if isinstance(rc, (int, float)):
            enriched["wfa_consistency"] = float(rc)
        if isinstance(wfa.get("passed"), bool):
            enriched["wfa_passed"] = wfa["passed"]

    # Effective bootstrap floor the run was gated with (timeframe default or override).
    bms = payload.get("bootstrap_min_sharpe")
    if isinstance(bms, (int, float)):
        enriched["bootstrap_min_sharpe"] = float(bms)

    bootstrap = payload.get("bootstrap_ci") or best.get("bootstrap_ci")
    if isinstance(bootstrap, dict):
        lo = bootstrap.get("lower")
        hi = bootstrap.get("upper")
        lvl = bootstrap.get("level")
        if isinstance(lo, (int, float)):
            enriched["bootstrap_sharpe_lower"] = float(lo)
        if isinstance(hi, (int, float)):
            enriched["bootstrap_sharpe_upper"] = float(hi)
        if isinstance(lvl, (int, float)):
            enriched["bootstrap_ci_level"] = float(lvl)

    cross_regime = payload.get("cross_regime_results")
    if isinstance(cross_regime, list):
        enriched["cross_regime_results"] = cross_regime

    rsb = payload.get("random_short_baseline_pct")
    if isinstance(rsb, (int, float)):
        enriched["random_short_baseline_pct"] = float(rsb)

    return enriched

router = APIRouter(prefix="/api/results", tags=["results"])

StateMgr = Annotated[StateManager, Depends(get_state_manager)]


_VALID_RUN_MODES = {"screening", "validation", "discovery"}
_VALID_STATUSES = {"pending", "running", "completed", "failed", "killed"}


@router.get("/runs/summary", response_model=RunSummaryResponse)
async def list_runs_summary(
    mgr: StateMgr,
    strategy_id: int | None = None,
    run_mode: str | None = None,
    status: str | None = None,
) -> RunSummaryResponse:
    if run_mode and run_mode not in _VALID_RUN_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid run_mode '{run_mode}'. Must be one of: {', '.join(_VALID_RUN_MODES)}",
        )
    if status and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {', '.join(_VALID_STATUSES)}",
        )
    rows = mgr.list_runs_with_results(strategy_id=strategy_id, run_mode=run_mode, status=status)
    runs = [RunSummaryItem(**r) for r in rows]
    return RunSummaryResponse(runs=runs)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    mgr: StateMgr,
    status: str | None = None,
    strategy_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> RunListResponse:
    if status and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {', '.join(_VALID_STATUSES)}",
        )
    rows = mgr.list_backtest_runs(strategy_id=strategy_id, status=status)
    runs = [BacktestRunResponse(**r) for r in rows]
    if start_date:
        runs = [r for r in runs if r.created_at and r.created_at[:10] >= start_date[:10]]
    if end_date:
        runs = [r for r in runs if r.created_at and r.created_at[:10] <= end_date[:10]]
    return RunListResponse(runs=runs)


@router.get("/compare", response_model=ComparisonResponse)
async def compare_runs(
    mgr: StateMgr,
    run_ids: Annotated[str, Query(description="Comma-separated run IDs")] = "",
) -> ComparisonResponse:
    if not run_ids.strip():
        raise HTTPException(status_code=400, detail="run_ids query param required")
    parts = [x.strip() for x in run_ids.split(",") if x.strip()]
    bad = [x for x in parts if not x.isdigit()]
    if bad:
        raise HTTPException(status_code=422, detail=f"run_ids must be integers, got {bad}")
    id_list = [int(x) for x in parts]
    results: list[BacktestResultResponse] = []
    for rid in id_list:
        row = mgr.get_backtest_result(rid)
        if row is not None:
            results.append(BacktestResultResponse(**_enrich_result_with_notes(row)))
            continue
        best = _best_sweep_row(mgr, rid)  # screening runs: same result as GET /runs/{id}
        if best is not None:
            results.append(_best_sweep_result(best, rid))
    return ComparisonResponse(runs=results)


@router.get("/runs/{run_id}/meta", response_model=BacktestRunResponse)
async def get_run_meta(run_id: int, mgr: StateMgr) -> BacktestRunResponse:
    """Get run metadata (strategy_id, symbols, timeframe, dates)."""
    row = mgr.get_backtest_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return BacktestRunResponse(**row)


@router.get("/runs/{run_id}", response_model=BacktestResultResponse)
async def get_run_summary(run_id: int, mgr: StateMgr) -> BacktestResultResponse:
    row = mgr.get_backtest_result(run_id)
    if row is not None:
        return BacktestResultResponse(**_enrich_result_with_notes(row))

    # Screening runs don't populate backtest_results — synthesize from best sweep
    best = _best_sweep_row(mgr, run_id)
    if best is None:
        # Ensure run exists at all
        run = mgr.get_backtest_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        raise HTTPException(status_code=404, detail="No results available for this run yet")
    return _best_sweep_result(best, run_id)


def _best_sweep_result(best: dict[str, Any], run_id: int) -> BacktestResultResponse:
    """A screening run's result: its best sweep row (screening doesn't fill backtest_results)."""
    return BacktestResultResponse(
        id=best["id"],
        run_id=run_id,
        total_return=best.get("total_return"),
        cagr=None,
        sharpe_ratio=best.get("sharpe_ratio"),
        sortino_ratio=best.get("sortino_ratio"),
        calmar_ratio=None,
        max_drawdown=best.get("max_drawdown"),
        max_drawdown_duration_days=None,
        volatility_annual=None,
        total_trades=best.get("total_trades"),
        winning_trades=None,
        losing_trades=None,
        win_rate=best.get("win_rate"),
        profit_factor=best.get("profit_factor"),
        avg_win=None,
        avg_loss=None,
        largest_win=None,
        largest_loss=None,
        avg_trade_duration_hours=None,
        max_consecutive_wins=None,
        max_consecutive_losses=None,
        total_fees=best.get("total_fees"),
        total_funding=best.get("total_funding"),
        total_slippage=None,
        deflated_sharpe=None,
        walk_forward_efficiency=None,
        purged_kfold_mean_sharpe=None,
        execution_time_seconds=best.get("execution_time_seconds"),
        # The balance the screening run actually used, recorded on every sweep row
        # (R12). A row written before the column existed is NULL and reads as the
        # default through the ONE shared rule (R13b) — never the requested
        # initial_balance, and never a hard-coded fallback. The UI derives net PnL
        # and fee ratios from it.
        starting_balance=recorded_balance(best.get("starting_balance")),
        notes=None,
        created_at=None,
    )


@router.get("/runs/{run_id}/trades", response_model=list[TradeResponse])
async def get_trades(
    run_id: int,
    mgr: StateMgr,
    symbol: str | None = None,
    direction: str | None = None,
) -> list[TradeResponse]:
    _ensure_run_exists(mgr, run_id)
    rows = mgr.get_trades(run_id)
    trades = [TradeResponse(**r) for r in rows]
    if symbol:
        trades = [t for t in trades if t.symbol == symbol]
    if direction:
        trades = [t for t in trades if t.direction == direction]
    return trades


@router.get("/runs/{run_id}/sweeps", response_model=list[SweepResultResponse])
async def get_sweeps(
    run_id: int,
    mgr: StateMgr,
    pareto_only: bool = False,
) -> list[SweepResultResponse]:
    _ensure_run_exists(mgr, run_id)
    rows = mgr.get_sweep_results(run_id, pareto_only=pareto_only)
    return [SweepResultResponse(**r) for r in rows]


@router.get("/runs/{run_id}/equity-curve", response_model=list[EquityCurvePoint])
async def get_equity_curve(run_id: int, mgr: StateMgr) -> list[EquityCurvePoint]:
    _ensure_run_exists(mgr, run_id)
    rows = mgr.get_trades(run_id)
    if not rows:
        return []
    # Sort by exit_time, skip open trades
    closed = sorted(
        (r for r in rows if r.get("exit_time")),
        key=lambda r: r["exit_time"],
    )
    if not closed:
        return []
    equity = _get_starting_balance(mgr, run_id)
    points = [EquityCurvePoint(timestamp=closed[0]["entry_time"], equity=equity)]
    for t in closed:
        equity += float(t.get("net_pnl") or 0)
        points.append(EquityCurvePoint(timestamp=t["exit_time"], equity=round(equity, 2)))
    return points


@router.get("/runs/{run_id}/drawdown", response_model=list[DrawdownPoint])
async def get_drawdown(run_id: int, mgr: StateMgr) -> list[DrawdownPoint]:
    _ensure_run_exists(mgr, run_id)
    rows = mgr.get_trades(run_id)
    if not rows:
        return []
    closed = sorted(
        (r for r in rows if r.get("exit_time")),
        key=lambda r: r["exit_time"],
    )
    if not closed:
        return []
    equity = _get_starting_balance(mgr, run_id)
    peak = equity
    points: list[DrawdownPoint] = []
    for t in closed:
        equity += float(t.get("net_pnl") or 0)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        points.append(DrawdownPoint(timestamp=t["exit_time"], drawdown=round(dd, 6)))
    return points


@router.get("/runs/{run_id}/monthly-returns", response_model=list[MonthlyReturn])
async def get_monthly_returns(run_id: int, mgr: StateMgr) -> list[MonthlyReturn]:
    _ensure_run_exists(mgr, run_id)
    rows = mgr.get_trades(run_id)
    if not rows:
        return []
    closed = sorted(
        (r for r in rows if r.get("exit_time")),
        key=lambda r: r["exit_time"],
    )
    if not closed:
        return []
    monthly: dict[tuple[int, int], float] = {}
    for t in closed:
        ts = t["exit_time"][:7]  # "YYYY-MM"
        parts = ts.split("-")
        if len(parts) >= 2:
            key = (int(parts[0]), int(parts[1]))
            monthly[key] = monthly.get(key, 0.0) + float(t.get("net_pnl") or 0)
    # Convert to percentage of starting equity
    starting = _get_starting_balance(mgr, run_id)
    return [
        MonthlyReturn(year=y, month=m, return_pct=round(pnl / starting * 100, 2))
        for (y, m), pnl in sorted(monthly.items())
    ]


@router.put("/runs/{run_id}/notes", response_model=BacktestResultResponse)
async def update_notes(
    run_id: int,
    body: NotesUpdateRequest,
    mgr: StateMgr,
) -> BacktestResultResponse:
    """Save the user's free-text notes (``user_notes``).

    Never touches ``notes``, which holds machine JSON (discovery results,
    consistency flags) that promote/export depend on.
    """
    _ensure_run_exists(mgr, run_id)
    if not mgr.update_result_user_notes(run_id, body.notes):
        raise HTTPException(
            status_code=404, detail="No results for this run — notes require completed results"
        )
    row = mgr.get_backtest_result(run_id)
    if row is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="Result disappeared after update")
    return BacktestResultResponse(**_enrich_result_with_notes(row))


_AI_SUMMARY_PROMPT = """You review one backtest for a trader. Reply in markdown, at most 220 words, with
these bold headings: **Verdict** (one line), **What it trades** (one sentence from the rules),
**Results** (what return, Sharpe, max drawdown, profit factor, win rate and trade count mean
together), **Red flags** (e.g. few trades, one trade making most of the profit, costs eating the
edge, drawdown large against the return, a short window, overfitting signs; say "none" if none),
**Next step** (one concrete experiment). Use only the numbers below and say when one is missing.

{context}
"""


def _ai_summary_context(mgr: StateManager, run_id: int) -> dict[str, Any]:
    """Run setup, strategy rules, metrics and trade statistics for the summary prompt."""
    run = mgr.get_backtest_run(run_id) or {}
    strategy = mgr.get_strategy(int(run["strategy_id"])) if run.get("strategy_id") else None
    result = mgr.get_backtest_result(run_id) or _best_sweep_row(mgr, run_id) or {}
    metric_keys = (
        "total_return", "cagr", "sharpe_ratio", "sortino_ratio", "max_drawdown", "total_trades",
        "win_rate", "profit_factor", "avg_win", "avg_loss", "largest_win", "largest_loss",
        "total_fees", "total_funding", "total_slippage", "deflated_sharpe", "starting_balance",
    )
    trades = mgr.get_trades(run_id)
    pnls = sorted((float(t.get("net_pnl") or 0.0), t) for t in trades)
    total_pnl = sum(p for p, _ in pnls)
    best = pnls[-1][0] if pnls else None
    exit_reasons: dict[str, int] = {}
    for t in trades:
        reason = str(t.get("exit_reason") or "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    return {
        "run": {k: run.get(k) for k in ("run_mode", "symbols", "timeframe", "start_date", "end_date")},
        "strategy": {
            "name": strategy.get("name") if strategy else None,
            "rules": strategy.get("dsl_config") if strategy else None,
        },
        "metrics": {k: result.get(k) for k in metric_keys if result.get(k) is not None},
        "trades": {
            "count": len(trades),
            "long": sum(1 for t in trades if t.get("direction") == "long"),
            "short": sum(1 for t in trades if t.get("direction") == "short"),
            "total_net_pnl": round(total_pnl, 2),
            "best_trade_share_of_pnl": round(best / total_pnl, 2) if best and total_pnl > 0 else None,
            "exit_reasons": exit_reasons,
            "best": [_trade_brief(t) for _, t in pnls[-3:][::-1]],
            "worst": [_trade_brief(t) for _, t in pnls[:3]],
        },
    }


def _trade_brief(t: dict[str, Any]) -> dict[str, Any]:
    keys = ("symbol", "direction", "entry_time", "exit_time", "net_pnl", "roi_percent", "exit_reason")
    return {k: t.get(k) for k in keys}


@router.get("/runs/{run_id}/ai-summary", response_model=AiSummaryResponse)
async def get_ai_summary(run_id: int, mgr: StateMgr) -> AiSummaryResponse:
    _ensure_run_exists(mgr, run_id)
    row = mgr.get_backtest_ai_summary(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No summary yet")
    return AiSummaryResponse(**row)


@router.post("/runs/{run_id}/ai-summary", response_model=AiSummaryResponse)
def create_ai_summary(run_id: int, mgr: StateMgr) -> AiSummaryResponse:
    """(Re)write the plain-language review of a run with the research model."""
    from vibe_quant.research import openai_chat

    _ensure_run_exists(mgr, run_id)
    context = _ai_summary_context(mgr, run_id)
    if not context["metrics"] and not context["trades"]["count"]:
        raise HTTPException(status_code=409, detail="This run has no results to summarize yet")
    prompt = _AI_SUMMARY_PROMPT.format(context=json.dumps(context, default=str, indent=1))
    try:
        text = openai_chat.research_completion(prompt, timeout_seconds=120, temperature=0.2)
    except Exception as e:  # noqa: BLE001 — model down: tell the user, change nothing
        raise HTTPException(status_code=503, detail=f"research model unavailable: {e}") from e
    mgr.save_backtest_ai_summary(run_id, summary=text.strip(), model=openai_chat.research_model_label())
    row = mgr.get_backtest_ai_summary(run_id)
    assert row is not None
    return AiSummaryResponse(**row)


@router.get("/runs/{run_id}/export/csv")
async def export_csv(run_id: int, mgr: StateMgr) -> StreamingResponse:
    _ensure_run_exists(mgr, run_id)
    trades = mgr.get_trades(run_id)

    buf = io.StringIO()
    if trades:
        writer = csv.DictWriter(buf, fieldnames=list(trades[0].keys()))
        writer.writeheader()
        writer.writerows(trades)
    else:
        buf.write("")

    buf.seek(0)
    filename = f"run_{run_id}_trades_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ensure_run_exists(mgr: StateManager, run_id: int) -> None:
    run = mgr.get_backtest_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")


def _best_sweep_row(mgr: StateManager, run_id: int) -> dict[str, Any] | None:
    """The row of a run's best sweep: Pareto-optimal first, else every sweep.

    Ordered by sharpe DESC in the database. The screening summary and the
    chart endpoints' balance fallback must agree on which sweep row is "best"
    (R13c), so the lookup lives here once instead of at each call site.
    """
    sweeps = mgr.get_sweep_results(run_id, pareto_only=True) or mgr.get_sweep_results(run_id)
    return sweeps[0] if sweeps else None


def _get_starting_balance(mgr: StateManager, run_id: int) -> float:
    """The balance the equity/drawdown/monthly endpoints scale by (R13c).

    ONE rule, the same one the summary reports: a run's own
    ``backtest_results.starting_balance`` when it has a result row; else the
    SAME best sweep row the summary synthesizes from; else the venue default
    (1_000 — must match VenueConfig, not an arbitrary 10k: a mismatched
    fallback mis-scales every equity/drawdown/monthly %). Every recorded value
    is read through ``recorded_balance``, where a NULL column (a row written
    before the column existed) means the default — there is no second NULL
    rule and the requested ``initial_balance`` is never used.
    """
    result = mgr.get_backtest_result(run_id)
    if result is not None:
        return float(recorded_balance(result.get("starting_balance")))
    best = _best_sweep_row(mgr, run_id)
    if best is not None:
        return float(recorded_balance(best.get("starting_balance")))
    return float(DEFAULT_STARTING_BALANCE_USDT)
