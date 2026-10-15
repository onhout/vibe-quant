"""Trailing funding signal and the cross-sectional ranking (design §2).

``trailing_funding`` sums the raw per-settlement rates over the fixed calendar
window ``(L - 7 d, L]``, ``L = D - 60 s``: Binance already scales each rate by
``8 / N``, so a 4h symbol's half-size rates and an 8h symbol's rates give
comparable 7-day totals (R4). A symbol is rankable only when every 8h UTC
boundary in the window has an archived row within 60 s and a sizing tick
exists; otherwise it is excluded that week, never forward-filled.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibe_quant.portfolio.spec import LOOKBACK_NS, SIGNAL_SAFETY_NS

if TYPE_CHECKING:
    from collections.abc import Mapping

_EIGHT_HOURS_NS = 8 * 3_600 * 10**9


@dataclass(frozen=True)
class Settlements:
    """Archived settlement times (int ns, ascending) and their rates."""

    times_ns: tuple[int, ...]
    rates: tuple[float, ...]


def _covered(times_ns: tuple[int, ...], boundary: int) -> bool:
    index = bisect.bisect_left(times_ns, boundary - SIGNAL_SAFETY_NS)
    return index < len(times_ns) and times_ns[index] <= boundary + SIGNAL_SAFETY_NS


def trailing_funding(settlements: Settlements, decision_ns: int) -> float | None:
    """7-day funding sum for ``decision_ns``; ``None`` = an 8h boundary is uncovered."""
    upper_ns = decision_ns - SIGNAL_SAFETY_NS
    lower_ns = upper_ns - LOOKBACK_NS
    boundary = (lower_ns // _EIGHT_HOURS_NS + 1) * _EIGHT_HOURS_NS
    while boundary <= upper_ns:
        if not _covered(settlements.times_ns, boundary):
            return None
        boundary += _EIGHT_HOURS_NS
    first = bisect.bisect_right(settlements.times_ns, lower_ns)
    last = bisect.bisect_right(settlements.times_ns, upper_ns)
    return math.fsum(settlements.rates[first:last])


def rank_weights(
    signals: Mapping[str, float | None],
    legs_divisor: int,
    gross: float,
) -> dict[str, float]:
    """Long the lowest ``k`` signals, short the highest; ``k = max(1, n // divisor)``."""
    ranked = sorted((value, symbol) for symbol, value in signals.items() if value is not None)
    weights: dict[str, float] = dict.fromkeys(signals, 0.0)
    if len(ranked) < 2:
        return weights
    legs = max(1, len(ranked) // legs_divisor)
    each = gross / (2 * legs)
    for _, symbol in ranked[:legs]:
        weights[symbol] = each
    for _, symbol in ranked[-legs:]:
        weights[symbol] = -each
    return weights
