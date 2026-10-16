"""Alpaca brokerage account schemas (read-only views of the paper or live account)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

AlpacaAccountKind = Literal["paper", "live"]


class AlpacaAccountResponse(BaseModel):
    account: AlpacaAccountKind
    account_number: str  # masked: last four digits only
    status: str
    currency: str
    equity: float | None = None
    portfolio_value: float | None = None
    cash: float
    buying_power: float | None = None
    options_buying_power: float | None = None
    options_trading_level: int | None = None
    pattern_day_trader: bool | None = None
    trading_blocked: bool | None = None


class AlpacaPositionResponse(BaseModel):
    symbol: str
    asset_class: str
    side: str
    qty: float
    avg_entry_price: float | None = None
    current_price: float | None = None
    market_value: float | None = None
    cost_basis: float | None = None
    unrealized_pl: float | None = None
    unrealized_pl_pct: float | None = None


class AlpacaOrderResponse(BaseModel):
    id: str
    client_order_id: str
    symbol: str
    asset_class: str | None = None
    side: str
    type: str
    time_in_force: str
    status: str
    qty: float | None = None
    filled_qty: float
    filled_avg_price: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    position_intent: str | None = None
    created_at: str
    filled_at: str | None = None


class AlpacaFillResponse(BaseModel):
    id: str
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    transaction_time: str
