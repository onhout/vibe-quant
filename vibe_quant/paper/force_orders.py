"""Read-only Binance forceOrders fetch and decode (bead vibe-quant-2gyk1.1.11, P3a1).

The venue can close a paper position on its own (liquidation, or ADL) without
telling anyone: NT never sees the fill, so the node keeps strategy state and
protective orders for a position that no longer exists. Task T6 detects those
exchange-initiated closes by asking Binance's ``GET /fapi/v1/forceOrders``.

This module is the decode/fetch half (P3a1) and nothing else: the poll loop is
P3a2 and the node wiring is P3b.

* :meth:`ForceOrder.from_binance` decodes one response row into a value object
  (R47);
* :func:`build_binance_fetch` binds credentials to a ``Fetch`` callable that
  signs one request per call (R46) and refuses a full page (R51).

The query is READ-ONLY: it signs a GET through NautilusTrader's cached Binance
HTTP client and never submits, amends or cancels an order, so it cannot change
venue state even if it misbehaves. There is no paging by design — a 100-row
page raises :class:`IncompleteForceOrders` so that symbol's cycle fails closed
instead of quietly reporting a partial answer.
"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Binance USD-M futures endpoint for exchange-initiated (liquidation/ADL) orders.
FORCE_ORDERS_PATH: Final = "/fapi/v1/forceOrders"

#: Binance's page size for the endpoint; a full page means "there may be more".
FORCE_ORDERS_PAGE_LIMIT: Final = 100

#: ``clientOrderId`` prefixes Binance stamps on its own closes (R47).
_ADL_CLIENT_ORDER_ID: Final = "adl_autoclose"
_LIQUIDATION_CLIENT_ORDER_ID: Final = "autoclose-"


class IncompleteForceOrders(Exception):
    """A forceOrders page came back full, so the answer may be truncated (R51)."""


@dataclass(frozen=True)
class ForceOrder:
    """One exchange-initiated close, decoded from a forceOrders row (R47).

    Attributes:
        symbol: The contract, e.g. ``BTCUSDT``.
        order_id: Binance's ``orderId``.
        side: ``BUY`` or ``SELL`` — the side of the closing fill.
        auto_close_type: ``LIQUIDATION``, ``ADL`` or ``OTHER``, classified from
            the ``autoclose-*`` / ``adl_autoclose*`` client order id (R47).
        avg_price: Average fill price (a ``float``, so it is JSON-native).
        executed_qty: Filled quantity; ``0.0`` means the order never executed
            and the watcher must skip it (R48).
        update_time_ms: Binance ``updateTime``, in epoch milliseconds.
        ts_ns: ``update_time_ms * 1_000_000`` as an ``int``.
        status: Binance order status, e.g. ``FILLED``.
        client_order_id: Binance's ``clientOrderId`` for the close.
    """

    symbol: str
    order_id: int
    side: str
    auto_close_type: str
    avg_price: float
    executed_qty: float
    update_time_ms: int
    ts_ns: int
    status: str
    client_order_id: str

    @classmethod
    def from_binance(cls, row: Mapping[str, object]) -> ForceOrder:
        """Decode one forceOrders row.

        Raises:
            ValueError: If a required field is missing, null or not of the
                expected type; the message names the offending field.
        """
        client_order_id = _str_field(row, "clientOrderId")
        update_time_ms = _int_field(row, "updateTime")
        executed_qty = _float_field(row, "executedQty")
        if executed_qty < 0.0:
            raise ValueError(
                f"forceOrders row field 'executedQty' is negative: {executed_qty!r}",
            )
        return cls(
            symbol=_str_field(row, "symbol"),
            order_id=_int_field(row, "orderId"),
            side=_str_field(row, "side"),
            auto_close_type=_auto_close_type(client_order_id),
            avg_price=_float_field(row, "avgPrice"),
            executed_qty=executed_qty,
            update_time_ms=update_time_ms,
            ts_ns=update_time_ms * 1_000_000,
            status=_str_field(row, "status"),
            client_order_id=client_order_id,
        )


#: ``Fetch(symbol, start_ms, end_ms) -> list[ForceOrder]``, newest state first.
Fetch = Callable[[str, int, int], Awaitable[list[ForceOrder]]]


def _auto_close_type(client_order_id: str) -> str:
    """Classify Binance's own close from its client order id (R47)."""
    if client_order_id.startswith(_ADL_CLIENT_ORDER_ID):
        return "ADL"
    if client_order_id.startswith(_LIQUIDATION_CLIENT_ORDER_ID):
        return "LIQUIDATION"
    return "OTHER"


def _field(row: Mapping[str, object], key: str) -> object:
    if key not in row:
        raise ValueError(f"forceOrders row is missing the {key!r} field")
    value = row[key]
    if value is None:
        raise ValueError(f"forceOrders row has a null {key!r} field")
    return value


def _str_field(row: Mapping[str, object], key: str) -> str:
    value = _field(row, key)
    if not isinstance(value, str):
        raise ValueError(f"forceOrders row field {key!r} is not a string: {value!r}")
    return value


def _int_field(row: Mapping[str, object], key: str) -> int:
    value = _field(row, key)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"forceOrders row field {key!r} is not an integer: {value!r}")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"forceOrders row field {key!r} is not an integer: {value!r}",
        ) from exc


def _float_field(row: Mapping[str, object], key: str) -> float:
    """Parse a numeric field into a FINITE float (R47d).

    ``NaN``/``Infinity`` strings and integers too large for a ``float``
    (``OverflowError``) name the field and fail closed, because either would
    silently poison every downstream comparison.
    """
    value = _field(row, key)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"forceOrders row field {key!r} is not numeric: {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"forceOrders row field {key!r} is not numeric: {value!r}",
        ) from exc
    if not math.isfinite(number):
        raise ValueError(
            f"forceOrders row field {key!r} is not finite: {value!r}",
        )
    return number


def build_binance_fetch(
    api_key: str,
    api_secret: str,
    account_type: str,
    testnet: bool,
) -> Fetch:
    """Bind Binance credentials to a read-only forceOrders ``Fetch`` (R46).

    The returned callable signs one ``GET /fapi/v1/forceOrders`` per call with
    the caller-supplied ``timestamp`` (R46: ``symbol``, ``startTime``,
    ``endTime``, ``limit='100'``, ``recvWindow='5000'``, ``timestamp`` and no
    ``autoCloseType``), and raises :class:`IncompleteForceOrders` when the page
    comes back full (R51).

    Args:
        api_key: Binance API key.
        api_secret: Binance API secret.
        account_type: A :class:`BinanceAccountType` value, e.g. ``USDT_FUTURES``.
        testnet: Use the testnet venue (``demo-fapi.binance.com`` for USD-M).

    Raises:
        ValueError: If ``account_type`` is not a known Binance account type.
    """
    from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
    from nautilus_trader.adapters.binance.factories import get_cached_binance_http_client
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.core.nautilus_pyo3 import HttpMethod

    clock = LiveClock()
    client = get_cached_binance_http_client(
        clock=clock,
        account_type=BinanceAccountType(account_type),
        api_key=api_key,
        api_secret=api_secret,
        environment=BinanceEnvironment.TESTNET if testnet else BinanceEnvironment.LIVE,
    )

    async def fetch(symbol: str, start_ms: int, end_ms: int) -> list[ForceOrder]:
        payload = {
            "symbol": symbol,
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": str(FORCE_ORDERS_PAGE_LIMIT),
            "recvWindow": "5000",
            "timestamp": str(clock.timestamp_ms()),
        }
        body = await client.sign_request(HttpMethod.GET, FORCE_ORDERS_PATH, payload)
        rows = json.loads(body)
        if not isinstance(rows, list):
            raise ValueError(
                f"forceOrders response for {symbol} is not a list: {type(rows).__name__}",
            )
        # Check the page before decoding: a truncated answer must not emit events
        # for the rows it happens to contain (R51).
        if len(rows) >= FORCE_ORDERS_PAGE_LIMIT:
            raise IncompleteForceOrders(
                f"forceOrders for {symbol} returned a full page of {len(rows)} rows, "
                "so the answer may be truncated; refusing to page",
            )
        orders = [ForceOrder.from_binance(row) for row in rows]
        orders.sort(key=lambda order: order.update_time_ms)
        return orders

    return fetch


__all__ = [
    "FORCE_ORDERS_PATH",
    "Fetch",
    "ForceOrder",
    "IncompleteForceOrders",
    "build_binance_fetch",
]
