"""Fail-closed buy-and-hold champion gate (D2-1, vibe-quant-wcn55).

Why: a GA champion can be de-risked beta -- its validated Sharpe can sit BELOW
the equal-weight, daily-rebalanced buy-and-hold of the same coins. Before a
champion is promoted, compare its daily Sharpe against that benchmark over the
candidate's EXACT UTC day set, with a direction-dependent bar.

Pure math: catalog daily closes in, a pass/fail verdict and a telemetry record
out. No pipeline wiring (that is task D2-3). Everything FAILS CLOSED -- a
missing close, a series shorter than :data:`MIN_OBSERVATIONS`, a flat series,
or a missing per-symbol series all reject.

Units: the Sharpe is computed in DAILY units (``mean / std(ddof=1)``);
annualised values are DISPLAYED with ``sqrt(365)`` and labelled "ann √365".
NT's ``SharpeRatio`` and the DSR machinery annualise with ``sqrt(252)`` -- the
label matters, and mixing the two is a silent 20% error.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np

from vibe_quant.validation.extraction import load_catalog_bars

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from vibe_quant.metrics import DailyReturns

#: Nanoseconds in one UTC day (metrics.DailyReturns day index = ts_ns // this).
DAY_NS: int = 86_400_000_000_000
#: Display-only annualisation factor. NOT NT/DSR's 252.
ANN_FACTOR: int = 365
#: A bar's ``ts_init`` may sit up to 1 ms before its close boundary.
_MS_NS: int = 1_000_000
#: Below this many daily observations a Sharpe is not meaningful (fail closed).
MIN_OBSERVATIONS: int = 30

_ANN_SQRT = math.sqrt(ANN_FACTOR)


def _day_to_date(day: int) -> str:
    """UTC calendar date (``YYYY-MM-DD``) of a day index since the epoch."""
    return datetime.fromtimestamp(day * 86400, tz=UTC).date().isoformat()


def _valid_close(value: float) -> bool:
    """A usable close is finite and strictly positive."""
    return math.isfinite(value) and value > 0.0


def load_daily_closes(
    symbols: Sequence[str], catalog_path: Path | str
) -> dict[str, dict[int, float]]:
    """``{symbol: {day: close}}`` from the catalog's 1d LAST bars.

    Reads ``f"{symbol}-PERP.BINANCE-1-DAY-LAST-EXTERNAL"`` and keeps only
    COMPLETE bars, the same rule as :mod:`vibe_quant.dsl.aux_data`: a bar's
    ``ts_init`` is its close, either exactly on the midnight boundary
    (``ts % DAY == 0``; the close belongs to the PREVIOUS UTC day, so the key
    is ``ts // DAY - 1``) or 1 ms before it (``(ts + 1ms) % DAY == 0``; key
    ``ts // DAY``). Anything else is an in-progress bar and is dropped.

    Raises:
        ValueError: the bar type is missing or empty for ``symbol``, or a
            complete bar has a non-finite / non-positive close (fail closed).
    """
    out: dict[str, dict[int, float]] = {}
    for symbol in symbols:
        bar_type = f"{symbol}-PERP.BINANCE-1-DAY-LAST-EXTERNAL"
        bars = load_catalog_bars(catalog_path, bar_type)
        if bars is None or bars.ts.size == 0:
            msg = f"No 1d catalog bars for {symbol} ({bar_type}) in {catalog_path}"
            raise ValueError(msg)
        raw = np.asarray(bars.ts, dtype=np.int64)
        close = np.asarray(bars.close, dtype=np.float64)
        on_midnight = raw % DAY_NS == 0
        complete = on_midnight | ((raw + _MS_NS) % DAY_NS == 0)
        day = np.where(on_midnight, raw // DAY_NS - 1, raw // DAY_NS)
        closes = {int(d): float(c) for d, c in zip(day[complete], close[complete], strict=True)}
        for d, c in closes.items():
            if not _valid_close(c):
                msg = f"invalid close {symbol} {_day_to_date(d)}: {c}"
                raise ValueError(msg)
        out[symbol] = closes
    return out


def buy_hold_daily(
    closes: Mapping[str, Mapping[int, float]],
    symbols: Sequence[str],
    first_day: int,
    n: int,
) -> tuple[np.ndarray | None, str | None]:
    """Equal-weight daily-rebalanced buy-and-hold returns over ``n`` days.

    ``bh_d = mean_s(close[s][d] / close[s][d-1] - 1)`` for
    ``d in first_day .. first_day + n - 1``.

    Fail closed: ANY missing close (any symbol, day ``d`` or ``d-1``) returns
    ``(None, "missing close <SYM> <YYYY-MM-DD>")`` -- stricter than the H-2b
    script, which skipped NaN days. A close that is non-finite or not strictly
    positive returns ``(None, "invalid close <SYM> <YYYY-MM-DD>: <value>")``;
    this is checked on the closes themselves (``finite / inf - 1`` is a
    plausible-looking ``-1``, so validating the derived return is too late).
    An EMPTY ``symbols`` returns ``(None, "no symbols")``: the equal-weight mean
    has no denominator, so this must reject rather than raise.
    """
    if not symbols:
        return None, "no symbols"
    if n <= 0:
        return np.zeros(0, dtype=np.float64), None
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        day = first_day + i
        total = 0.0
        for symbol in symbols:
            series = closes.get(symbol, {})
            prev = series.get(day - 1)
            if prev is None:
                return None, f"missing close {symbol} {_day_to_date(day - 1)}"
            cur = series.get(day)
            if cur is None:
                return None, f"missing close {symbol} {_day_to_date(day)}"
            for close_day, value in ((day - 1, prev), (day, cur)):
                if not _valid_close(value):
                    return None, f"invalid close {symbol} {_day_to_date(close_day)}: {value}"
            total += cur / prev - 1.0
        out[i] = total / len(symbols)
    return out, None


def daily_sharpe(values: Sequence[float] | np.ndarray) -> float | None:
    """Daily Sharpe (``mean / std(ddof=1)``), or ``None`` when unusable.

    ``None`` when there are fewer than :data:`MIN_OBSERVATIONS` values, any
    value is non-finite, or the sample standard deviation is zero.
    """
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if arr.size < MIN_OBSERVATIONS:
        return None
    if not bool(np.all(np.isfinite(arr))):
        return None
    std = float(np.std(arr, ddof=1))
    if std == 0.0:
        return None
    return float(np.mean(arr) / std)


def direction_bar(bh_sharpe: float, direction: str) -> float:
    """The buy-and-hold Sharpe a candidate must BEAT, per direction.

    ``long`` -> ``bh``, ``short`` -> ``-bh``, ``both`` -> ``|bh|``.

    Raises:
        ValueError: unknown ``direction``.
    """
    if direction == "long":
        return bh_sharpe
    if direction == "short":
        return -bh_sharpe
    if direction == "both":
        return abs(bh_sharpe)
    msg = f"Unknown buy-hold gate direction {direction!r}"
    raise ValueError(msg)


def _pooled_reason(
    sharpe_daily: float,
    bar_daily: float,
    direction: str,
    symbols: Sequence[str],
    n: int,
) -> str:
    return (
        f"Buy-hold: sharpe {sharpe_daily * _ANN_SQRT:.3f} <= bar {bar_daily * _ANN_SQRT:.3f} "
        f"({direction}, EW B&H {'+'.join(symbols)}, ann √365, T={n})"
    )


def _symbol_check(
    closes: Mapping[str, Mapping[int, float]],
    symbol: str,
    series: DailyReturns | None,
    direction: str,
) -> tuple[bool, dict[str, object], str | None]:
    """Worst-mode check of one symbol: candidate series vs its OWN buy-and-hold."""
    if series is None:
        return (
            False,
            {"sharpe_daily": None, "bh_sharpe_daily": None, "bar_daily": None, "passed": False},
            f"Buy-hold: {symbol} has no daily series",
        )
    n = len(series)
    sharpe = daily_sharpe(series.values)
    bh_values, bh_error = buy_hold_daily(closes, [symbol], series.first_day, n)
    bh_sharpe = None if bh_values is None else daily_sharpe(bh_values)
    bar = None if bh_sharpe is None else direction_bar(bh_sharpe, direction)
    if sharpe is None or bh_sharpe is None or bar is None:
        details: list[str] = []
        if sharpe is None:
            details.append("candidate daily sharpe unavailable")
        if bh_values is None:
            details.append(f"benchmark {bh_error or 'closes missing'}")
        elif bh_sharpe is None:
            details.append("benchmark daily sharpe unavailable")
        return (
            False,
            {
                "sharpe_daily": sharpe,
                "bh_sharpe_daily": bh_sharpe,
                "bar_daily": bar,
                "passed": False,
            },
            f"Buy-hold: {symbol} {'; '.join(details)} (T={n})",
        )
    passed = sharpe > bar
    reason = None
    if not passed:
        reason = (
            f"Buy-hold: {symbol} sharpe {sharpe * _ANN_SQRT:.3f} <= "
            f"bar {bar * _ANN_SQRT:.3f} (own EW B&H {symbol}, ann √365, T={n})"
        )
    return (
        passed,
        {
            "sharpe_daily": sharpe,
            "bh_sharpe_daily": bh_sharpe,
            "bar_daily": bar,
            "passed": passed,
        },
        reason,
    )


def buy_hold_gate(
    candidate: DailyReturns | None,
    direction: str,
    closes: Mapping[str, Mapping[int, float]],
    symbols: Sequence[str],
    per_symbol: Mapping[str, DailyReturns | None] | None = None,
) -> tuple[bool, dict[str, object], list[str]]:
    """Fail-closed gate: candidate daily Sharpe vs EW buy-and-hold, per direction.

    The benchmark is built on the candidate's EXACT day set
    (``candidate.first_day``, ``len(candidate)``). The candidate passes iff its
    daily Sharpe is STRICTLY greater than :func:`direction_bar` of the
    benchmark's.

    When ``per_symbol`` is given (worst mode) every symbol must ALSO clear the
    direction bar of ITS OWN buy-and-hold on its own day set; each failing
    symbol appends a reason.

    Returns:
        ``(passed, record, reasons)``. ``record`` carries ``series`` ("mtm"),
        ``sharpe_daily``/``sharpe_ann``, ``bh_sharpe_daily``/``bh_sharpe_ann``,
        ``bar_daily``/``bar_ann``, ``direction``, ``observations``,
        ``first_day``, ``passed``, and -- in worst mode -- ``symbols``. Nothing
        is rounded; ``None`` marks a value that could not be computed. Every
        key is present on every path except ``candidate is None`` (no
        ``first_day``/``observations`` exist: the record is ``{}``). In worst
        mode ``symbols`` is reported even when the pooled side is unavailable.
    """
    if candidate is None:
        return False, {}, ["Buy-hold: no daily series"]

    n = len(candidate)
    first_day = candidate.first_day
    sharpe_daily = daily_sharpe(candidate.values)
    bh_values, bh_error = buy_hold_daily(closes, symbols, first_day, n)
    bh_sharpe_daily = None if bh_values is None else daily_sharpe(bh_values)

    bar_daily = None if bh_sharpe_daily is None else direction_bar(bh_sharpe_daily, direction)

    record: dict[str, object] = {
        "series": "mtm",
        "sharpe_daily": sharpe_daily,
        "sharpe_ann": None if sharpe_daily is None else sharpe_daily * _ANN_SQRT,
        "bh_sharpe_daily": bh_sharpe_daily,
        "bh_sharpe_ann": None if bh_sharpe_daily is None else bh_sharpe_daily * _ANN_SQRT,
        "bar_daily": bar_daily,
        "bar_ann": None if bar_daily is None else bar_daily * _ANN_SQRT,
        "direction": direction,
        "observations": n,
        "first_day": first_day,
        "passed": False,
    }

    # Report exactly the unavailable side(s); a valid side is never blamed.
    reasons: list[str] = []
    if sharpe_daily is None:
        reasons.append(f"Buy-hold: candidate daily sharpe unavailable (T={n})")
    if bh_values is None:
        reasons.append(f"Buy-hold: benchmark {bh_error or 'closes missing'}")
    elif bh_sharpe_daily is None:
        reasons.append(f"Buy-hold: benchmark daily sharpe unavailable (T={n})")

    passed = False
    if sharpe_daily is not None and bar_daily is not None:
        passed = sharpe_daily > bar_daily
        if not passed:
            reasons.append(_pooled_reason(sharpe_daily, bar_daily, direction, symbols, n))

    if per_symbol is not None:
        symbols_record: dict[str, object] = {}
        for symbol in symbols:
            ok, entry, reason = _symbol_check(closes, symbol, per_symbol.get(symbol), direction)
            symbols_record[symbol] = entry
            if not ok:
                passed = False
                if reason is not None:
                    reasons.append(reason)
        record["symbols"] = symbols_record

    record["passed"] = passed
    return passed, record, reasons
