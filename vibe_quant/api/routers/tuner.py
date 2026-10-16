"""Regime tuner router: candidates' journeys (attempts, AI parameter changes, per-market results)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from vibe_quant.api.deps import get_state_manager
from vibe_quant.api.schemas.tuner import (
    TunerCandidate,
    TunerCandidateSummary,
    TunerCurrent,
    TunerQueueRequest,
    TunerSettings,
    TunerStatusResponse,
    TunerWindowConfig,
)
from vibe_quant.db.state_manager import StateManager
from vibe_quant.research.tuner import CONFIG_PATH, STATE_PATH, TunerConfig, load_state, save_state

router = APIRouter(prefix="/api/tuner", tags=["tuner"])

StateMgr = Annotated[StateManager, Depends(get_state_manager)]


def _summary(c: dict[str, Any]) -> TunerCandidateSummary:
    attempts = c.get("attempts") or []
    return TunerCandidateSummary(
        root_strategy_id=int(c["root_strategy_id"]),
        root_name=str(c["root_name"]),
        timeframe=c.get("timeframe"),
        symbols=list(c.get("symbols") or []),
        status=str(c.get("status")),
        stage=str(c.get("stage")),
        current_strategy_id=int(c["current_strategy_id"]),
        round=int(c.get("round") or 0),
        attempts=len(attempts),
        verdict=c.get("verdict"),
        created_at=c.get("created_at"),
        settled_at=c.get("settled_at"),
        finished_at=c.get("finished_at"),
        paper=c.get("paper"),
        last_reason=next((a.get("reason") for a in reversed(attempts) if a.get("reason")), None),
    )


def _settings(cfg: TunerConfig) -> TunerSettings:
    return TunerSettings(
        min_return=cfg.min_return,
        min_profit_factor=cfg.min_profit_factor,
        min_stage_trades=dict(cfg.min_stage_trades),
        min_total_trades=cfg.min_total_trades,
        max_rounds=cfg.max_rounds,
        timeframes=list(cfg.timeframes),
        windows=[TunerWindowConfig(**w.__dict__) for w in cfg.windows],
        queued=list(cfg.candidate_ids),
        auto_paper=cfg.auto_paper,
    )


@router.get("", response_model=TunerStatusResponse)
async def get_tuner() -> TunerStatusResponse:
    state = load_state(STATE_PATH)
    current = state.get("current")
    candidates = sorted(
        (_summary(c) for c in (state.get("candidates") or {}).values()),
        key=lambda c: (c.status != "settled", c.created_at or ""),
    )
    return TunerStatusResponse(
        updated_at=state.get("updated_at"),
        current=TunerCurrent(**current) if isinstance(current, dict) else None,
        settings=_settings(TunerConfig.load(CONFIG_PATH)),
        candidates=candidates,
    )


@router.get("/candidates/{root_strategy_id}", response_model=TunerCandidate)
async def get_candidate(root_strategy_id: int) -> TunerCandidate:
    state = load_state(STATE_PATH)
    c = (state.get("candidates") or {}).get(str(root_strategy_id))
    if c is None:
        raise HTTPException(
            status_code=404, detail=f"strategy {root_strategy_id} is not a tuner candidate"
        )
    summary = _summary(c)
    return TunerCandidate(**summary.model_dump(), attempt_list=c.get("attempts") or [])


@router.post("/queue", response_model=TunerStatusResponse)
async def queue_strategy(body: TunerQueueRequest, state_mgr: StateMgr) -> TunerStatusResponse:
    """Tune this strategy next (and, with ``restart``, start its journey over)."""
    strategy = state_mgr.get_strategy(body.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"strategy {body.strategy_id} not found")
    cfg = TunerConfig.load(CONFIG_PATH)
    timeframe = (strategy.get("dsl_config") or {}).get("timeframe")
    if timeframe not in cfg.timeframes:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe {timeframe} is not tuned (allowed: {cfg.timeframes})",
        )
    if body.strategy_id not in cfg.candidate_ids:
        cfg.candidate_ids = [body.strategy_id, *cfg.candidate_ids]
        cfg.save(CONFIG_PATH)
    if body.restart:
        state = load_state(STATE_PATH)
        candidates = state.setdefault("candidates", {})
        if str(body.strategy_id) in candidates and candidates[str(body.strategy_id)].get(
            "status"
        ) in ("settled", "exhausted"):
            candidates.pop(str(body.strategy_id))
            save_state(state, STATE_PATH)
    return await get_tuner()
