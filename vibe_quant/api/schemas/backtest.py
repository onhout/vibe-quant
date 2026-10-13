"""Backtest domain schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from vibe_quant.api.schemas._validators import KNOWN_TIMEFRAMES, check_date_range, clean_symbols
from vibe_quant.liquidation_settings import MAX_LEVERAGE


def _positive_number(params: dict[str, object], key: str, upper: float | None = None) -> None:
    if key not in params or params[key] is None:
        return
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"parameters.{key} must be a number, got {value!r}")
    if value <= 0 or (upper is not None and value > upper):
        bound = f"in (0, {upper:g}]" if upper is not None else "> 0"
        raise ValueError(f"parameters.{key} must be {bound}, got {value!r}")


class BacktestLaunchRequest(BaseModel):
    # Unknown fields are rejected: the API used to accept sizing_config_id /
    # risk_config_id / overfitting_filters and silently never apply them.
    model_config = ConfigDict(extra="forbid")

    strategy_id: int
    symbols: list[str]
    timeframe: str
    start_date: str
    end_date: str
    parameters: dict[str, object]
    latency_preset: str | None = None
    # Charge the BNB-discounted (x0.9) fee schedule. Stored into the run
    # parameters only when true, so default runs' stored params are unchanged
    # (screening and discovery accept it too, bd vibe-quant-2gyk1.4 D3-2).
    bnb_discount: StrictBool = False
    # Post-only maker entries are validation-only and opt-in: the entry is a
    # limit priced at the outbox release. Screening refuses a non-market
    # entry_order, and the three fields are stored in the run parameters only
    # when entry_order == "post_only" (D3-6).
    entry_order: Literal["market", "post_only"] = "market"
    maker_offset_ticks: int = Field(1, ge=1)
    maker_entry_timeout_bars: int = Field(3, ge=1)
    # Run-level liquidation mode. Stored into the run parameters only when on,
    # so default runs' stored params are unchanged (E1). The run's existing
    # parameters.leverage is the venue/liquidation leverage (vibe-quant-2gyk1.1.10).
    liquidation_mode: Literal["off", "isolated", "cross"] = "off"

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        return clean_symbols(v)

    @field_validator("timeframe")
    @classmethod
    def _timeframe(cls, v: str) -> str:
        if v not in KNOWN_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {sorted(KNOWN_TIMEFRAMES)}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _ranges(self) -> BacktestLaunchRequest:
        check_date_range(self.start_date, self.end_date, allow_equal=False)
        # The mode is a top-level field: a nested parameters.liquidation_mode
        # would be silently ignored, so reject it loudly (E4).
        if "liquidation_mode" in self.parameters:
            msg = (
                "parameters.liquidation_mode is not allowed; "
                "use the top-level liquidation_mode field"
            )
            raise ValueError(msg)
        # Runners fall back to 10x / 1000 USDT on non-positive values — reject instead.
        _positive_number(self.parameters, "leverage", upper=MAX_LEVERAGE)
        _positive_number(self.parameters, "initial_balance")
        return self


class BacktestRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int | None = None
    run_mode: str
    symbols: list[str]
    timeframe: str
    start_date: str | None = None
    end_date: str | None = None
    parameters: dict[str, object] | None = None
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
    created_at: str


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    pid: int | None
    job_type: str
    status: str
    heartbeat_at: str | None
    started_at: str | None
    completed_at: str | None
    is_stale: bool


class HeartbeatRequest(BaseModel):
    run_id: int


class TradesBatchRequest(BaseModel):
    trades: list[dict[str, object]]


class SweepResultsBatchRequest(BaseModel):
    results: list[dict[str, object]]


class ParetoMarkRequest(BaseModel):
    result_ids: list[int]


class CoverageCheckRequest(BaseModel):
    symbols: list[str]
    timeframe: str
    start_date: str
    end_date: str

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        return clean_symbols(v)

    @model_validator(mode="after")
    def _ranges(self) -> CoverageCheckRequest:
        check_date_range(self.start_date, self.end_date, allow_equal=True)
        return self


class CoverageCheckResponse(BaseModel):
    coverage: dict[str, object]
