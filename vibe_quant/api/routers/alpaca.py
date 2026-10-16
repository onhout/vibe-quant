"""Alpaca brokerage account router (/api/alpaca): read-only views of the paper or live account.

Requests go through the NautilusTrader Alpaca adapter's HTTP API. Keys come from the backend
environment only: ``ALPACA_PAPER_API_KEY`` / ``ALPACA_PAPER_API_SECRET`` for the paper account and
``ALPACA_API_KEY`` / ``ALPACA_API_SECRET`` for live. No endpoint here places or cancels orders.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query
from nautilus_trader.adapters.alpaca import AlpacaEnvironment, get_cached_alpaca_http_client
from nautilus_trader.adapters.alpaca.http.account import AlpacaAccountHttpAPI
from nautilus_trader.adapters.alpaca.http.error import AlpacaClientError, AlpacaError
from nautilus_trader.common.component import LiveClock

from vibe_quant.api.schemas.alpaca import (
    AlpacaAccountKind,
    AlpacaAccountResponse,
    AlpacaFillResponse,
    AlpacaOrderResponse,
    AlpacaPositionResponse,
)

if TYPE_CHECKING:
    from nautilus_trader.adapters.alpaca.common.schemas.account import AlpacaOrder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alpaca", tags=["alpaca"])

_CLOCK = LiveClock()
_KEY_ENV = {
    "paper": ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET"),
    "live": ("ALPACA_API_KEY", "ALPACA_API_SECRET"),
}


def _api(account: AlpacaAccountKind) -> AlpacaAccountHttpAPI:
    key_var, secret_var = _KEY_ENV[account]
    key, secret = os.environ.get(key_var), os.environ.get(secret_var)
    if not key or not secret:
        missing = [v for v, value in ((key_var, key), (secret_var, secret)) if not value]
        raise HTTPException(
            status_code=503,
            detail=f"Alpaca {account} credentials missing: set {', '.join(missing)} in the backend environment",
        )
    environment = AlpacaEnvironment.LIVE if account == "live" else AlpacaEnvironment.PAPER
    client = get_cached_alpaca_http_client(clock=_CLOCK, environment=environment, api_key=key, api_secret=secret)
    return AlpacaAccountHttpAPI(client)


def _upstream_error(account: AlpacaAccountKind, exc: AlpacaError) -> HTTPException:
    if isinstance(exc, AlpacaClientError) and exc.status in (401, 403):
        return HTTPException(
            status_code=503,
            detail=f"Alpaca rejected the {account} API keys ({exc.status} {exc.text}): check the key id and secret",
        )
    return HTTPException(status_code=502, detail=f"Alpaca {account} request failed ({exc.status}): {exc.text}")


def _num(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


def _order(o: AlpacaOrder) -> AlpacaOrderResponse:
    return AlpacaOrderResponse(
        id=o.id,
        client_order_id=o.client_order_id,
        symbol=o.symbol,
        asset_class=o.asset_class,
        side=o.side.value,
        type=o.alpaca_type.value,
        time_in_force=o.time_in_force.value,
        status=o.status.value,
        qty=_num(o.qty),
        filled_qty=float(o.filled_qty or 0),
        filled_avg_price=_num(o.filled_avg_price),
        limit_price=_num(o.limit_price),
        stop_price=_num(o.stop_price),
        position_intent=o.position_intent,
        created_at=o.created_at,
        filled_at=o.filled_at,
    )


@router.get("/account", response_model=AlpacaAccountResponse)
async def get_account(account: AlpacaAccountKind = "paper") -> AlpacaAccountResponse:
    try:
        a = await _api(account).query_account()
    except AlpacaError as exc:
        raise _upstream_error(account, exc) from exc
    return AlpacaAccountResponse(
        account=account,
        account_number=f"••••{a.account_number[-4:]}",
        status=a.status,
        currency=a.currency,
        equity=_num(a.equity),
        portfolio_value=_num(a.portfolio_value),
        cash=float(a.cash or 0),
        buying_power=_num(a.buying_power),
        options_buying_power=_num(a.options_buying_power),
        options_trading_level=a.options_trading_level,
        pattern_day_trader=a.pattern_day_trader,
        trading_blocked=a.trading_blocked,
    )


@router.get("/positions", response_model=list[AlpacaPositionResponse])
async def get_positions(account: AlpacaAccountKind = "paper") -> list[AlpacaPositionResponse]:
    try:
        positions = await _api(account).query_positions()
    except AlpacaError as exc:
        raise _upstream_error(account, exc) from exc
    out = []
    for p in positions:
        cost = _num(p.cost_basis)
        pl = _num(p.unrealized_pl)
        out.append(
            AlpacaPositionResponse(
                symbol=p.symbol,
                asset_class=p.asset_class,
                side=p.side,
                qty=float(Decimal(p.qty)),
                avg_entry_price=_num(p.avg_entry_price),
                current_price=_num(p.current_price),
                market_value=_num(p.market_value),
                cost_basis=cost,
                unrealized_pl=pl,
                unrealized_pl_pct=round(pl / abs(cost) * 100, 2) if pl is not None and cost else None,
            ),
        )
    return out


@router.get("/orders", response_model=list[AlpacaOrderResponse])
async def get_orders(
    account: AlpacaAccountKind = "paper",
    status: str = Query("all", pattern="^(open|closed|all)$"),
    days: int = Query(14, ge=1, le=90),
) -> list[AlpacaOrderResponse]:
    after = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        orders = await _api(account).orders.list(status=status, after=after if status != "open" else None)
    except AlpacaError as exc:
        raise _upstream_error(account, exc) from exc
    return [_order(o) for o in sorted(orders, key=lambda o: o.created_at, reverse=True)]


@router.get("/fills", response_model=list[AlpacaFillResponse])
async def get_fills(
    account: AlpacaAccountKind = "paper",
    days: int = Query(14, ge=1, le=90),
) -> list[AlpacaFillResponse]:
    after = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        fills = await _api(account).query_fills(after=after)
    except AlpacaError as exc:
        raise _upstream_error(account, exc) from exc
    return [
        AlpacaFillResponse(
            id=f.id,
            order_id=f.order_id,
            symbol=f.symbol,
            side=f.side,
            qty=float(Decimal(f.qty)),
            price=float(Decimal(f.price)),
            transaction_time=f.transaction_time,
        )
        for f in reversed(fills)
    ]
