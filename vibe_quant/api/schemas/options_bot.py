"""Options bot schemas: the Reddit-options trading bot that runs as the trading-paper user service."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApprovalMode = Literal["auto", "manual"]
TradingMode = Literal["paper", "live"]
BotCommand = Literal["research_now", "trade", "skip", "close", "report"]


class BotServiceState(BaseModel):
    installed: bool
    active: str | None = None
    sub_state: str | None = None
    since: str | None = None
    error: str | None = None


class BotSettings(BaseModel):
    approval_mode: ApprovalMode
    trading_mode: TradingMode


class BotSettingsUpdate(BaseModel):
    """Partial settings update. Switching ``trading_mode`` restarts a running bot."""

    model_config = ConfigDict(extra="forbid")

    approval_mode: ApprovalMode | None = None
    trading_mode: TradingMode | None = None


class BotCommandRequest(BaseModel):
    """A command for the running bot; trade/skip/close need ``proposal_id``."""

    model_config = ConfigDict(extra="forbid")

    command: BotCommand
    proposal_id: str | None = None


class BotCommandResponse(BaseModel):
    id: int
    created_at: str | None = None
    command: str | None = None
    args: dict[str, object] = Field(default_factory=dict)
    status: str
    result: str | None = None
    done_at: str | None = None


class BotStatusResponse(BaseModel):
    service: BotServiceState
    bot: dict[str, object] | None = None
    bot_stale: bool
    bot_age_secs: int | None = None
    settings: BotSettings
    commands: list[BotCommandResponse]
    week_new_positions: int
    max_new_positions_per_week: int = 1
    today: str


class ProposalSummary(BaseModel):
    proposal_id: str
    trading_date: str
    proposed_at: str | None = None
    status: str
    ticker: str
    thesis: str | None = None
    thesis_source: str | None = None
    contract: str | None = None
    expiration: str | None = None
    strike: float | None = None
    option_type: str | None = None
    dte: int | None = None
    proposed_quantity: int | None = None
    proposed_limit: float | None = None
    maximum_debit: float | None = None
    underlying_at_proposal: float | None = None
    option_bid_at_proposal: float | None = None
    option_ask_at_proposal: float | None = None
    option_delta_at_proposal: float | None = None
    selected_setup: str | None = None
    combined_setup_score: float | None = None
    social_score: float | None = None
    entry_score: float | None = None
    decision: str | None = None
    fatal_flags: list[str] | None = None
    missing_fields: list[str] | None = None
    entry_eligible: bool | None = None
    trade_eligible: bool | None = None
    ai_summary: str | None = None
    trade: dict[str, object] | None = None
    shadow: dict[str, object] | None = None


class ProposalListResponse(BaseModel):
    date: str
    proposals: list[ProposalSummary]


class ProposalDate(BaseModel):
    date: str
    count: int


class ProposalEvent(BaseModel):
    id: int
    at: str
    type: str
    payload: dict[str, object]


class ProposalDetailResponse(BaseModel):
    proposal: dict[str, object]
    events: list[ProposalEvent]
    evidence: dict[str, object]


class OutcomeStats(BaseModel):
    count: int
    wins: int
    win_rate_pct: float | None = None
    avg_return_pct: float | None = None
    total_pnl: float


class ProposalHistoryResponse(BaseModel):
    trades: OutcomeStats
    shadows: OutcomeStats
    proposals: int
    rows: list[ProposalSummary]


class BotReportResponse(BaseModel):
    date: str
    markdown: str


class BotLogsResponse(BaseModel):
    unit: str
    text: str
