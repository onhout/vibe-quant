"""Download US stock 1m bars from Alpaca (through NautilusTrader's Alpaca adapter).

The Binance counterpart is :mod:`vibe_quant.data.downloader`. Bars come from Alpaca's SIP
feed (all US exchanges), split-adjusted, regular trading hours only (09:30-16:00 New
York), as archive kline tuples ``(open_time, open, high, low, close, volume, close_time,
quote_volume, trade_count)``. The free Alpaca plan serves SIP history up to 15 minutes
ago, so requests end 16 minutes before now.

Credentials come from the environment (``ALPACA_PAPER_API_KEY`` /
``ALPACA_PAPER_API_SECRET``; market data is the same for the paper and live keys).
Historical prices are split-adjusted as of the download: re-ingest a symbol after a split.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from nautilus_trader.adapters.alpaca.http.market import AlpacaMarketHttpAPI

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
SESSION_OPEN_MIN = 9 * 60 + 30
SESSION_CLOSE_MIN = 16 * 60
SESSION_MINUTES = SESSION_CLOSE_MIN - SESSION_OPEN_MIN  # 390 one-minute bars per full session
SIP_DELAY = timedelta(minutes=16)


class AlpacaDownloadError(Exception):
    """Alpaca bars could not be fetched (missing keys, HTTP error)."""


def _market_api() -> AlpacaMarketHttpAPI:
    from nautilus_trader.adapters.alpaca import AlpacaEnvironment, get_cached_alpaca_http_client
    from nautilus_trader.adapters.alpaca.http.market import AlpacaMarketHttpAPI
    from nautilus_trader.common.component import LiveClock

    try:
        client = get_cached_alpaca_http_client(clock=LiveClock(), environment=AlpacaEnvironment.PAPER)
    except RuntimeError as exc:  # get_env_key: variable not set
        raise AlpacaDownloadError(
            f"{exc}: set ALPACA_PAPER_API_KEY and ALPACA_PAPER_API_SECRET to download Alpaca bars"
        ) from exc
    return AlpacaMarketHttpAPI(client)


def in_regular_session(open_time_ms: int) -> bool:
    """A 1m bar opening inside the 09:30-16:00 New York session on a weekday."""
    local = datetime.fromtimestamp(open_time_ms / 1000, tz=NEW_YORK)
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and SESSION_OPEN_MIN <= minute < SESSION_CLOSE_MIN


def bars_to_klines(bars: list[Any]) -> list[tuple[Any, ...]]:
    """Alpaca bars -> archive kline tuples, regular session only."""
    import pandas as pd

    klines: list[tuple[Any, ...]] = []
    for b in bars:
        open_ms = int(pd.Timestamp(b.t).value // 1_000_000)
        if not in_regular_session(open_ms):
            continue
        quote_volume = float(b.vw) * float(b.v) if b.vw is not None else None
        klines.append(
            (open_ms, b.o, b.h, b.l, b.c, b.v, open_ms + 59_999, quote_volume, b.n)
        )
    return klines


def download_klines(symbol: str, start: datetime, end: datetime) -> list[tuple[Any, ...]]:
    """1m regular-session klines for ``symbol`` in ``[start, end)`` (end clamped to 16 min ago).

    Raises:
        AlpacaDownloadError: keys missing or the request failed.
    """
    from nautilus_trader.adapters.alpaca import AlpacaDataFeed

    end = min(end, datetime.now(UTC) - SIP_DELAY)
    if end <= start:
        return []
    api = _market_api()

    async def fetch() -> list[Any]:
        bars = await api.query_stock_bars(
            [symbol],
            "1Min",
            start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            AlpacaDataFeed.SIP,
            adjustment="split",
        )
        return bars.get(symbol, [])

    try:
        bars = asyncio.run(fetch())
    except Exception as exc:
        raise AlpacaDownloadError(f"{symbol} {start:%Y-%m-%d}..{end:%Y-%m-%d}: {exc}") from exc
    return bars_to_klines(bars)


def expected_month_klines(year: int, month: int) -> int:
    """Upper bound of regular-session 1m bars in a month (weekdays x 390; holidays not excluded)."""
    _, days = calendar.monthrange(year, month)
    weekdays = sum(1 for d in range(1, days + 1) if date(year, month, d).weekday() < 5)
    return weekdays * SESSION_MINUTES
