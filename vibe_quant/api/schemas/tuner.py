"""Schemas for the regime tuner (``vibe_quant.research.tuner``)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TunerWindowResult(BaseModel):
    label: str
    stage: str
    start: str
    end: str
    symbols: list[str]
    note: str = ""
    run_id: int | None = None
    verdict: str | None = None  # win | lose | flat | error | None (running)
    total_return: float | None = None
    profit_factor: float | None = None
    trades: int | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    error: str | None = None
    run_note: str | None = None


class TunerAI(BaseModel):
    reasoning: str | None = None
    notes: list[str] = Field(default_factory=list)
    new_strategy_id: int | None = None
    suggestion: dict[str, object] | None = None
    error: str | None = None


class TunerAttempt(BaseModel):
    n: int
    strategy_id: int
    strategy_name: str
    stage: str
    dsl: dict[str, object]
    windows: list[TunerWindowResult]
    started_at: str | None = None
    finished_at: str | None = None
    passed: bool | None = None
    reason: str | None = None
    ai: TunerAI | None = None


class TunerPaper(BaseModel):
    state: str
    validation_run_id: int | None = None
    started_at: str | None = None
    note: str | None = None
    error: str | None = None


class TunerCandidateSummary(BaseModel):
    root_strategy_id: int
    root_name: str
    timeframe: str | None = None
    symbols: list[str]
    status: str  # testing | tuning | settled | exhausted
    stage: str
    current_strategy_id: int
    round: int
    attempts: int
    verdict: str | None = None
    created_at: str | None = None
    settled_at: str | None = None
    finished_at: str | None = None
    paper: TunerPaper | None = None
    last_reason: str | None = None


class TunerCandidate(TunerCandidateSummary):
    attempt_list: list[TunerAttempt]


class TunerWindowConfig(BaseModel):
    label: str
    stage: str
    start: str
    end: str
    symbols: list[str]
    note: str = ""


class TunerSettings(BaseModel):
    min_return: float
    min_profit_factor: float
    min_stage_trades: dict[str, int]
    min_total_trades: int
    max_rounds: int
    timeframes: list[str]
    windows: list[TunerWindowConfig]
    queued: list[int]
    auto_paper: bool


class TunerCurrent(BaseModel):
    root_strategy_id: int
    strategy_id: int
    stage: str
    since: str | None = None


class TunerStatusResponse(BaseModel):
    updated_at: str | None = None
    current: TunerCurrent | None = None
    settings: TunerSettings
    candidates: list[TunerCandidateSummary]


class TunerQueueRequest(BaseModel):
    strategy_id: int = Field(ge=1)
    restart: bool = False  # re-tune a candidate that already settled or ran out of rounds
