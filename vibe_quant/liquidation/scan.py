"""Binance USDT-M liquidation scanner on 1m mark-price series (isolated and cross).

Pure function over data: positions' fill/cash events plus 1m MARK-price bars in,
liquidation hits (and dropped positions) out. No engine integration; the
overlay that edits trades/ledgers builds on :func:`scan_liquidations`.

Semantics (user/architect/chief decisions 2026-10-12/13; "Assuming" marks a modelling choice):

* Trigger: the mark LOW (longs) / HIGH (shorts) of a 1m mark bar crosses the
  liquidation price (LP), ``low <= LP`` / ``high >= LP``. A long with LP <= 0
  never liquidates; a short with LP <= 0 (``tiers.InsolventPosition``) is
  insolvent at every price and liquidates on the first scanned bar. An LP the
  mark has already crossed (funding or a fill moved it) triggers on the first
  scanned bar.
* Fill rule (``fill_rule: lp_or_open_if_gapped``): long fill = min(LP, bar open),
  short fill = max(LP, bar open). A bar that opens already past the LP (a gap,
  or an LP crossed by an event) fills at its open; one that trades through the LP
  fills at the LP. The bar open is the series' real ``open``; a series without
  one raises ``MissingMarkSeries`` (a previous-close stand-in understated
  gap-through losses). The hit timestamp is the trigger bar's OPEN time, or the
  event ts when the bar contains the event (below).
* Exposure window (conservative): every 1m bar whose minute overlaps the open
  exposure interval, i.e. ``open_ts > entry_ts - 60 s`` and ``open_ts < exit_ts``.
  The entry minute's extreme counts even when it preceded the fill (pessimistic
  valuation, not a decision look-ahead); an exit exactly at a bar open excludes
  that bar (NT's exit at the same ts as a would-be trigger bar wins); a mid-minute
  exit still includes the bar that opened before it. Every later state change
  (scale-in, reduction, funding, cash) is treated like the entry (chief rule F2):
  the post-event state applies to the bar CONTAINING the event (open in
  ``(ts - 60 s, ts]``; funding settling at 08:00:00 applies to the 08:00 bar) and
  to every later bar. A hit on the containing bar is stamped at the event ts (never
  before the event; the overlay keeps fills with ``ts <= hit.ts``) and filled under
  the rule above from that bar's open; the bar was also scanned under the
  pre-event state, so the pessimistic outcome wins.
* Gap rule (``gap_rule: carry_forward_last_close_max_60m``): in cross a leg with
  no bar in a scanned minute is valued at its last known close (never a future
  bar). An event whose minute has no bar (no bar opens in ``(ts - 60 s, ts]``) is
  evaluated AT the event ts against the carried last close, which stands in for
  the bar open in the fill rule (long min(LP, close), short max(LP, close)); the
  hit timestamp is then the event ts. In cross the event ts is always a grid
  point: a leg whose bar contains the event is valued on that bar's low/high, and
  exits (non-triggering leg) at that bar's open, observed before the event (its
  close is still in the future). A hole of more than 60 minutes between
  consecutive bars (unobserved over ``(a + 60 s, b)``) that overlaps a scanned
  span ``[event ts, end)`` raises ``MissingMarkSeries``, also when events split it
  into shorter segments, and also for a cross leg carried past its own last bar
  for more than 60 minutes while another leg's bars extend the grid. The
  accepted blind spot: a crash and recovery wholly inside an allowed gap.
* Non-finite marks (in the scanned window), fills, cash or funding raise
  ``ValueError`` naming the symbol/ts: losses are never sanitised to zero.
* Isolated: margin = sum(entry notional) / leverage (reductions release margin
  pro rata); funding paid is taken from it, funding received adds to it;
  Assuming the entry fee is paid from the wallet, not the margin. LP is
  recomputed after every fill and funding event, with the maintenance tier
  chosen by the notional at LP (tiers.py). The position closes at the fill
  price at the hit; later positions are kept (NT's path) but ``path_divergence``
  is set because their sizing used pre-liquidation equity and missed re-entries
  cannot be reconstructed. An isolated loss never exceeds the remaining margin
  (the insurance fund bears a gap past bankruptcy), so ``realized_pnl`` is
  floored at minus the margin and is then not ``q * (price - avg)``; the
  shortfall is ``insurance_deficit``.
* Cross: wallet = starting balance + realized cash (commissions, realized PnL,
  funding, other cash); it may go negative while the margin balance stays above
  MM, and the LP uses the signed wallet (chief rule G2). Margin balance MB =
  wallet + sum(uPnL), each leg valued at its ADVERSE extreme inside the same
  minute (low for longs, high for shorts; conservative); MM = sum(table MM at
  that notional); trigger when ``MB <= MM``. A trigger is an account bust: the
  leg with the largest adverse uPnL is the triggering leg and closes at its cross
  LP (other legs' MM/uPnL at that minute) under the fill rule above; the other
  legs close at their own 1m mark close (the observed open at a mid-minute
  event point). Positions that open after the bust are
  DROPPED; equity is floored at 0 after ``bust_ts``.
* Clearance fee at a liquidation = ``min(rate * notional_at_close, max(MB, 0))``;
  a ``None`` rate (the shipped snapshot) forfeits the whole remaining margin
  balance. The loss of an isolated hit is price PnL + fee; with the forfeit
  rule this equals the position's remaining isolated margin. In cross only the
  triggering leg carries the fee, charged once out of the account margin balance
  after every leg closed, so under the forfeit rule total loss == wallet at bust
  (negative when the closes realise more than a negative wallet owed).
  A cross account cannot go below 0 either (chief rules G/G2): the insurance
  deficit is computed at ACCOUNT level, ``max(fee - (wallet + sum(price PnL)), 0)``
  (equity after every leg closed at its fill, minus the fee), with no per-leg
  cap. It is attributed to the hits pro rata to their price losses, or, when no
  leg lost (the shortfall is a negative wallet), pro rata to their closing
  notional; each hit's ``realized_pnl`` is its price PnL plus its share.
* A missing mark series for a traded symbol (or one that starts after the first
  fill) raises: callers fail closed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

import numpy as np

from vibe_quant.liquidation.tiers import (
    InsolventPosition,
    TierTable,
    TierTableExceeded,
    UnknownSymbol,
    cross_liq_price,
    isolated_liq_price,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "EventLike",
    "LiquidationHit",
    "LiquidationResult",
    "MarkLike",
    "MissingMarkSeries",
    "PositionView",
    "scan_liquidations",
]

Mode = Literal["isolated", "cross"]

_QTY_EPS = 1e-12
_MAX_NOTE_EVENTS = 100
_CLEARANCE_FORFEIT = "forfeit_remaining_margin"
_CLEARANCE_RATE = "min(rate*notional, max(margin_balance,0))"
_FILL_RULE = "lp_or_open_if_gapped"
_GAP_RULE = "carry_forward_last_close_max_60m"
_MIN_NS = 60_000_000_000
_MAX_GAP_NS = 60 * _MIN_NS


class MissingMarkSeries(ValueError):
    """A traded symbol has no (or too short) mark-price series."""


class MarkLike(Protocol):
    """1m mark-price bars: ``ts`` is the bar OPEN time (int64 ns, increasing).

    ``open`` (required) is the real bar open the fill rule uses.
    """

    @property
    def ts(self) -> np.ndarray: ...
    @property
    def open(self) -> np.ndarray: ...
    @property
    def low(self) -> np.ndarray: ...
    @property
    def high(self) -> np.ndarray: ...
    @property
    def close(self) -> np.ndarray: ...


class EventLike(Protocol):
    """A ledger event (``metrics.LedgerEvent``): ``qty`` is signed, 0 for cash-only."""

    @property
    def ts(self) -> int: ...
    @property
    def qty(self) -> float: ...
    @property
    def price(self) -> float: ...
    @property
    def cash(self) -> float: ...


@dataclass(frozen=True)
class PositionView:
    """One position's fills/cash events plus its funding (negative = paid by us).

    Positions of one instrument must not overlap in time (the DSL holds one
    position per instrument). ``key`` identifies the position to the caller.
    """

    key: str
    symbol: str
    instrument_id: str
    events: Sequence[EventLike]
    funding: Sequence[tuple[int, float]] = ()


@dataclass(frozen=True)
class LiquidationHit:
    """One liquidated position (closed at ``price`` at minute ``ts``).

    ``side``: +1 long / -1 short; ``qty`` is unsigned; ``mark`` is the trigger
    extreme (mark low for longs, high for shorts) of the trigger bar for the leg;
    ``margin_balance``/``maint_margin`` are the position's (isolated) or the
    account's (cross) values at the trigger; ``realized_pnl`` is the price PnL of
    the liquidating close of the remaining ``qty`` against its average entry,
    plus ``insurance_deficit`` (the hit's share of what the venue absorbs past
    bankruptcy; in cross computed for the account and shared pro rata, so a
    profitable leg can carry a share when the wallet was negative). ``ts`` is the
    trigger bar's open or the event ts when the bar contains the event.
    """

    key: str
    symbol: str
    ts: int
    price: float
    qty: float
    side: int
    mark: float
    margin_balance: float
    maint_margin: float
    clearance_fee: float
    realized_pnl: float
    insolvent: bool = False
    insurance_deficit: float = 0.0

    @property
    def loss(self) -> float:
        """Loss booked by the liquidating close: price loss + clearance fee."""
        return self.clearance_fee - self.realized_pnl

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "symbol": self.symbol,
            "ts": self.ts,
            "price": self.price,
            "qty": self.qty,
            "side": self.side,
            "mark": self.mark,
            "margin_balance": self.margin_balance,
            "maint_margin": self.maint_margin,
            "clearance_fee": self.clearance_fee,
            "forfeited": self.clearance_fee,
            "realized_pnl": self.realized_pnl,
            "insolvent": self.insolvent,
            "insurance_deficit": self.insurance_deficit,
            "loss": self.loss,
        }


@dataclass
class LiquidationResult:
    hits: list[LiquidationHit] = field(default_factory=list)
    dropped_keys: set[str] = field(default_factory=set)
    bust_ts: int | None = None
    path_divergence: bool = False
    mode: str = "isolated"
    leverage: float = 10.0
    tier_table_version: str | None = None
    clearance_rule: str = _CLEARANCE_FORFEIT

    @property
    def is_empty(self) -> bool:
        return not self.hits and not self.dropped_keys

    @property
    def total_loss(self) -> float:
        return float(sum(hit.loss for hit in self.hits))

    def to_notes(self, n_trades: int) -> dict[str, Any]:
        """JSON-safe summary for ``backtest_results.notes``; ``pct_trades`` is in percent."""
        count = len(self.hits)
        return {
            "mode": self.mode,
            "mark_source": "1m_mark",
            "tier_table_version": self.tier_table_version,
            "clearance_rule": self.clearance_rule,
            "leverage": self.leverage,
            "count": count,
            # explicit sums instead of an ambiguous "total_loss" (which read -10 for a cross
            # account that went bust to equity 0): forfeited >= 0 is what the venue took
            # beyond the price move; realized_pnl is signed (fill price PnL + deficit share)
            "total_forfeited": float(sum(h.clearance_fee for h in self.hits)),
            "total_realized_pnl": float(sum(h.realized_pnl for h in self.hits)),
            "total_insurance_deficit": float(sum(h.insurance_deficit for h in self.hits)),
            "pct_trades": (100.0 * count / n_trades) if n_trades > 0 else 0.0,
            "bust_ts": self.bust_ts,
            "path_divergence": self.path_divergence,
            "dropped": len(self.dropped_keys),
            "fill_rule": _FILL_RULE,
            "gap_rule": _GAP_RULE,
            "events": [hit.to_dict() for hit in self.hits[:_MAX_NOTE_EVENTS]],
        }


class _Book:
    """Average-cost netting position + isolated margin for one position."""

    __slots__ = ("avg", "margin", "qty")

    def __init__(self) -> None:
        self.qty = 0.0
        self.avg = 0.0
        self.margin = 0.0

    def apply(self, fill: float, price: float, leverage: float) -> float:
        """Apply a signed fill; return the realized price PnL of the closed part."""
        q = self.qty
        if q == 0.0 or (q > 0.0) == (fill > 0.0):
            new_q = q + fill
            self.avg = (abs(q) * self.avg + abs(fill) * price) / abs(new_q)
            self.qty = new_q
            self.margin += abs(fill) * price / leverage
            return 0.0
        closed = min(abs(fill), abs(q))
        realized = closed * (price - self.avg) * (1.0 if q > 0.0 else -1.0)
        new_q = q + fill
        if abs(new_q) <= _QTY_EPS * max(1.0, abs(q)):
            self.qty, self.avg, self.margin = 0.0, 0.0, 0.0
        elif (new_q > 0.0) != (q > 0.0):  # flipped through flat
            self.qty, self.avg = new_q, price
            self.margin = abs(new_q) * price / leverage
        else:  # partial reduction releases margin pro rata
            self.margin *= abs(new_q) / abs(q)
            self.qty = new_q
        return realized


class _VecTier:
    """Vectorised maintenance-margin lookup for one tier table."""

    __slots__ = ("caps", "cums", "floors", "mmrs", "table")

    def __init__(self, table: TierTable) -> None:
        self.table = table
        self.floors = np.array([b.floor for b in table.brackets], dtype=np.float64)
        self.caps = np.array([b.cap for b in table.brackets], dtype=np.float64)
        self.mmrs = np.array([b.mmr for b in table.brackets], dtype=np.float64)
        self.cums = np.array([b.cum for b in table.brackets], dtype=np.float64)

    def maintenance_margin(self, notional: np.ndarray) -> np.ndarray:
        if notional.size and float(notional.max()) >= self.caps[-1]:
            raise TierTableExceeded(
                f"{self.table.symbol}: notional {float(notional.max())} >= last cap "
                f"{float(self.caps[-1])}"
            )
        idx = np.searchsorted(self.floors, notional, side="right") - 1
        return notional * self.mmrs[idx] - self.cums[idx]


def scan_liquidations(
    views: Sequence[PositionView],
    marks: Mapping[str, MarkLike],
    *,
    mode: Mode,
    starting_balance: float,
    leverage: float,
    tables: Mapping[str, TierTable],
) -> LiquidationResult:
    """Find Binance USDT-M liquidations for ``views`` on 1m mark bars.

    Raises ``MissingMarkSeries`` / ``UnknownSymbol`` / ``TierTableExceeded`` /
    ``TierTableError`` instead of guessing (callers fail closed).
    """
    if mode not in ("isolated", "cross"):
        raise ValueError(f"mode must be 'isolated' or 'cross', got {mode!r}")
    if not math.isfinite(leverage) or leverage <= 0:
        raise ValueError(f"leverage must be finite and > 0, got {leverage!r}")
    if not math.isfinite(starting_balance) or starting_balance <= 0:
        raise ValueError(f"starting_balance must be finite and > 0, got {starting_balance!r}")

    for view in views:
        _validate_view(view)
    traded = [v for v in views if any(e.qty != 0.0 for e in v.events)]
    vec: dict[str, _VecTier] = {}
    for view in traded:
        _require_marks(view, marks)
        if view.symbol not in vec:
            vec[view.symbol] = _VecTier(_table_for(tables, view.symbol))

    result = LiquidationResult(
        mode=mode,
        leverage=float(leverage),
        tier_table_version=getattr(tables, "version", None),
        clearance_rule=_clearance_rule(vt.table for vt in vec.values()),
    )
    if not traded:
        return result
    if mode == "isolated":
        for view in traded:
            hit = _scan_isolated(view, marks[view.symbol], vec[view.symbol], leverage)
            if hit is not None:
                result.hits.append(hit)
        result.hits.sort(key=lambda h: (h.ts, h.key))
        result.path_divergence = bool(result.hits)
    else:
        _scan_cross(traded, marks, vec, starting_balance, leverage, result)
    return result


def _table_for(tables: Mapping[str, TierTable], symbol: str) -> TierTable:
    try:
        return tables[symbol]
    except KeyError:
        raise UnknownSymbol(symbol) from None


def _require_marks(view: PositionView, marks: Mapping[str, MarkLike]) -> None:
    series = marks.get(view.symbol)
    if series is None or len(series.ts) == 0:
        raise MissingMarkSeries(f"{view.symbol}: no mark-price series for position {view.key}")
    opens = getattr(series, "open", None)
    if opens is None or len(opens) != len(series.ts):
        raise MissingMarkSeries(
            f"{view.symbol}: mark series has no bar open array (the fill rule needs it)"
        )
    first_fill = min(int(e.ts) for e in view.events if e.qty != 0.0)
    if first_fill < int(series.ts[0]):
        raise MissingMarkSeries(
            f"{view.symbol}: mark series starts at {int(series.ts[0])}, after the first fill "
            f"{first_fill} of position {view.key}"
        )


def _clearance_rule(tables: Any) -> str:
    rates = {t.clearance_fee_rate is None for t in tables}
    if rates == {True} or not rates:
        return _CLEARANCE_FORFEIT
    return _CLEARANCE_RATE if rates == {False} else "mixed"


def _clearance_fee(table: TierTable, notional: float, margin_balance: float) -> float:
    remaining = max(margin_balance, 0.0)
    if table.clearance_fee_rate is None:
        return remaining
    return min(table.clearance_fee_rate * notional, remaining)


def _validate_view(view: PositionView) -> None:
    """Reject non-finite fills, cash and funding before any money calculation."""

    def bad(what: str, ts: object, value: object) -> ValueError:
        return ValueError(
            f"{view.symbol} position {view.key}: non-finite {what} {value!r} at ts {ts!r}"
        )

    for e in view.events:
        if not math.isfinite(e.qty):
            raise bad("fill qty", e.ts, e.qty)
        if not math.isfinite(e.cash):
            raise bad("cash", e.ts, e.cash)
        if e.qty != 0.0 and not (math.isfinite(e.price) and e.price > 0.0):
            raise bad("fill price", e.ts, e.price)
    for ts, amount in view.funding:
        if not math.isfinite(amount):
            raise bad("funding", ts, amount)


def _finite_or_raise(symbol: str, what: str, values: np.ndarray, ts: np.ndarray) -> None:
    if not np.isfinite(values).all():
        first = int(ts[int(np.argmin(np.isfinite(values)))])
        raise ValueError(f"{symbol}: non-finite mark {what} in the scanned window at ts {first}")


def _check_gaps(symbol: str, ts: np.ndarray, start_ns: int, end_ns: int | None) -> None:
    """Fail closed when a mark hole longer than ``_MAX_GAP_NS`` overlaps ``[start_ns, end_ns]``.

    Bars at ``a`` < ``b`` leave the mark unobserved over ``(a + 60 s, b)``. The bar
    before ``start_ns`` takes part, so a hole that events split into short segments
    is still seen. ``end_ns`` None scans to the series end.
    """
    n = len(ts)
    lo = max(int(np.searchsorted(ts, start_ns, side="right")) - 1, 0)
    hi = n if end_ns is None else min(int(np.searchsorted(ts, end_ns, side="left")) + 1, n)
    seg = ts[lo:hi]
    if seg.size and int(seg[0]) - start_ns > _MAX_GAP_NS:
        raise MissingMarkSeries(f"{symbol}: mark gap of more than 60 minutes after ts {start_ns}")
    if seg.size > 1:
        a, b = seg[:-1], seg[1:]
        holes = (b - a > _MAX_GAP_NS) & (b > start_ns)
        if end_ns is not None:
            holes &= a + _MIN_NS < end_ns
        if holes.any():
            first = int(a[int(holes.argmax())])
            raise MissingMarkSeries(
                f"{symbol}: mark gap of more than 60 minutes after ts {first} in the scanned window"
            )
    if end_ns is not None and hi == n and end_ns - int(ts[-1]) > _MAX_GAP_NS:
        raise MissingMarkSeries(f"{symbol}: mark gap of more than 60 minutes before ts {end_ns}")


def _crossed(side: int, lp: float | None, mark: float) -> bool:
    """LP crossed at ``mark`` (long low <= LP, short high >= LP); no LP = insolvent short."""
    if lp is None:
        return True
    return mark <= lp if side > 0 else mark >= lp


def _fill_price(side: int, lp: float | None, open_: float) -> float:
    """Rule B: long min(LP, open), short max(LP, open); no LP / LP <= 0 fills at the open."""
    if lp is None or lp <= 0.0:
        return open_
    return min(lp, open_) if side > 0 else max(lp, open_)


def _scan_isolated(
    view: PositionView, mark: MarkLike, vt: _VecTier, leverage: float
) -> LiquidationHit | None:
    """First isolated liquidation of one position, vectorised per constant-LP segment."""
    # fills before funding at the same ts (funding applies to the open position)
    items: list[tuple[int, int, int, float, float]] = []
    for seq, e in enumerate(view.events):
        if e.qty != 0.0:  # cash-only events do not touch isolated margin
            items.append((int(e.ts), 0, seq, float(e.qty), float(e.price)))
    for seq, (ts, amount) in enumerate(view.funding):
        items.append((int(ts), 1, seq, float(amount), 0.0))
    items.sort(key=lambda it: (it[0], it[1], it[2]))

    book = _Book()
    for k, (ts, kind, _seq, a, b) in enumerate(items):
        if kind == 0:
            book.apply(a, b, leverage)
        elif book.qty != 0.0:
            book.margin += a  # funding paid (negative) shrinks, received grows the margin
        if book.qty == 0.0:
            continue
        seg_end = items[k + 1][0] if k + 1 < len(items) else None
        if seg_end == ts:
            continue  # more events at this ts: the segment [ts, ts) is empty
        side = 1 if book.qty > 0.0 else -1
        # the bar containing the event (open in (ts - 60 s, ts]) takes the post-event state
        # (rule A for an entry, F2 for later events); without one the event is in a gap
        c = int(np.searchsorted(mark.ts, ts, side="right")) - 1
        if c < 0:
            raise MissingMarkSeries(f"{view.symbol}: mark series starts after ts {ts}")
        in_bar = int(mark.ts[c]) > ts - _MIN_NS
        lo = c if in_bar else c + 1
        hi = len(mark.ts) if seg_end is None else int(np.searchsorted(mark.ts, seg_end, "left"))
        extreme = mark.low[lo:hi] if side > 0 else mark.high[lo:hi]
        for what, arr in (
            ("open", mark.open),
            ("high", mark.high),
            ("low", mark.low),
            ("close", mark.close),
        ):  # every OHLC field of a scanned bar, not just the ones the trigger reads
            _finite_or_raise(view.symbol, what, arr[lo:hi], mark.ts[lo:hi])
        lp: float | None
        try:
            lp = isolated_liq_price(side, abs(book.qty), book.avg, max(book.margin, 0.0), vt.table)
        except InsolventPosition:
            lp = None  # short insolvent at every price: liquidates on the first scanned bar
        if lp is not None and lp <= 0.0:
            _check_gaps(view.symbol, mark.ts, ts, seg_end)
            continue  # a long that never liquidates
        if not in_bar:
            # an event inside a mark gap is evaluated at its own ts on the carried close
            carried = float(mark.close[c])
            if not math.isfinite(carried):
                raise ValueError(f"{view.symbol}: non-finite mark close at ts {int(mark.ts[c])}")
            if _crossed(side, lp, carried):
                _check_gaps(view.symbol, mark.ts, ts, ts)
                return _isolated_hit(view, vt, book, side, lp, ts, carried, carried)
        if lp is None:
            crossed = np.ones(hi - lo, dtype=bool)
        else:
            crossed = extreme <= lp if side > 0 else extreme >= lp
        if not crossed.any():
            _check_gaps(view.symbol, mark.ts, ts, seg_end)
            continue
        i = lo + int(crossed.argmax())
        hit_ts = max(int(mark.ts[i]), ts)  # the containing bar's hit is at the event
        _check_gaps(view.symbol, mark.ts, ts, hit_ts)
        return _isolated_hit(
            view, vt, book, side, lp, hit_ts, float(mark.open[i]), float(extreme[i - lo])
        )
    return None


def _isolated_hit(
    view: PositionView,
    vt: _VecTier,
    book: _Book,
    side: int,
    lp: float | None,
    ts: int,
    open_: float,
    extreme: float,
) -> LiquidationHit:
    qty = abs(book.qty)
    price = _fill_price(side, lp, open_)
    # an isolated loss never exceeds the position's margin (the rest of a gap
    # past bankruptcy is the venue's insurance fund)
    raw = side * qty * (price - book.avg)
    pnl = max(raw, -max(book.margin, 0.0))
    margin_balance = book.margin + pnl
    notional = qty * price
    return LiquidationHit(
        key=view.key,
        symbol=view.symbol,
        ts=ts,
        price=price,
        qty=float(qty),
        side=side,
        mark=extreme,
        margin_balance=float(margin_balance),
        maint_margin=float(vt.table.maintenance_margin(notional)),
        clearance_fee=float(_clearance_fee(vt.table, notional, margin_balance)),
        realized_pnl=float(pnl),
        insolvent=lp is None,
        insurance_deficit=float(pnl - raw),
    )


@dataclass
class _CrossEvent:
    ts: int
    order: int  # 0 fill, 1 cash event, 2 funding
    seq: int
    view: int
    qty: float
    price: float
    cash: float


def _scan_cross(
    traded: Sequence[PositionView],
    marks: Mapping[str, MarkLike],
    vec: dict[str, _VecTier],
    starting_balance: float,
    leverage: float,
    result: LiquidationResult,
) -> None:
    events: list[_CrossEvent] = []
    first_fill: dict[int, int] = {}
    seq = 0
    for vi, view in enumerate(traded):
        for e in view.events:
            if e.qty != 0.0:
                first_fill[vi] = min(first_fill.get(vi, int(e.ts)), int(e.ts))
                events.append(_CrossEvent(int(e.ts), 0, seq, vi, e.qty, e.price, e.cash))
            elif e.cash != 0.0:
                events.append(_CrossEvent(int(e.ts), 1, seq, vi, 0.0, 0.0, e.cash))
            seq += 1
        for ts, amount in view.funding:
            events.append(_CrossEvent(int(ts), 2, seq, vi, 0.0, 0.0, float(amount)))
            seq += 1
    events.sort(key=lambda ev: (ev.ts, ev.order, ev.seq))

    wallet = float(starting_balance)
    books: dict[int, _Book] = {}
    i, n = 0, len(events)
    while i < n:
        ts = events[i].ts
        while i < n and events[i].ts == ts:
            ev = events[i]
            wallet += ev.cash
            if ev.qty != 0.0:
                book = books.get(ev.view)
                if book is None:
                    book = books[ev.view] = _Book()
                wallet += book.apply(ev.qty, ev.price, leverage)
                if book.qty == 0.0:
                    del books[ev.view]
            i += 1
        if not books:
            continue
        seg_end = events[i].ts if i < n else None
        if _eval_cross_segment(traded, books, marks, vec, wallet, ts, seg_end, result):
            bust_ts = result.bust_ts
            assert bust_ts is not None
            hit_keys = {hit.key for hit in result.hits}
            result.dropped_keys = {
                traded[vi].key
                for vi, fts in first_fill.items()
                if fts > bust_ts and traded[vi].key not in hit_keys
            }
            result.hits.sort(key=lambda h: (h.ts, h.key))
            return


def _eval_cross_segment(
    traded: Sequence[PositionView],
    books: dict[int, _Book],
    marks: Mapping[str, MarkLike],
    vec: dict[str, _VecTier],
    wallet: float,
    event_ts: int,
    seg_end: int | None,
    result: LiquidationResult,
) -> bool:
    """Scan the segment ``[event_ts, seg_end)`` for MB <= MM; True on bust.

    The grid is the event ts plus every leg's bar opens after it. At the event point a
    leg is valued on the bar containing it (open in ``(event_ts - 60 s, event_ts]``:
    its low/high, rules A/F2) or, in a gap, on its carried close (rule F); at later
    points on its own bar or its carried close.
    """
    legs = sorted(books.items())
    grids = [np.array([event_ts], dtype=np.int64)]
    for vi, _book in legs:
        ts = marks[traded[vi].symbol].ts
        lo = int(np.searchsorted(ts, event_ts, side="right"))
        hi = len(ts) if seg_end is None else int(np.searchsorted(ts, seg_end, side="left"))
        grids.append(np.asarray(ts[lo:hi], dtype=np.int64))
    grid = np.concatenate(grids)
    if len(legs) > 1:
        grid = np.unique(grid)

    lows: list[np.ndarray] = []
    highs: list[np.ndarray] = []
    closes: list[np.ndarray] = []
    opens: list[np.ndarray] = []
    upnl: list[np.ndarray] = []
    mm: list[np.ndarray] = []
    for vi, book in legs:
        view = traded[vi]
        series = marks[view.symbol]
        idx = np.searchsorted(series.ts, grid, side="right") - 1
        if (idx < 0).any():
            raise MissingMarkSeries(f"{view.symbol}: mark series starts after the scanned minute")
        bar_ts = series.ts[idx]
        in_bar = bar_ts > grid - _MIN_NS  # the leg's bar contains the grid point
        last_close = series.close[idx]  # a missing minute carries the last known close
        # validate the raw bars before any substitution (a mid-minute exit replaces the
        # close with the open): every field of a bar containing a grid point, and the
        # carried close of a gap point
        for what, arr in (("open", series.open), ("high", series.high), ("low", series.low)):
            _finite_or_raise(view.symbol, what, arr[idx][in_bar], grid[in_bar])
        _finite_or_raise(view.symbol, "close", last_close, grid)
        low = np.where(in_bar, series.low[idx], last_close)
        high = np.where(in_bar, series.high[idx], last_close)
        # fill-rule open: the bar's real open, the carried close in a gap
        open_ = np.where(in_bar, series.open[idx], last_close)
        # exit price of a non-triggering leg: its bar close at a bar open, its observed
        # open when the event falls inside the bar (the close is still in the future)
        close = np.where(in_bar & (bar_ts != grid), series.open[idx], last_close)
        sign = 1.0 if book.qty > 0.0 else -1.0
        qty = abs(book.qty)
        adverse = low if sign > 0 else high
        lows.append(low)
        highs.append(high)
        closes.append(close)
        opens.append(open_)
        upnl.append(sign * qty * (adverse - book.avg))
        mm.append(vec[view.symbol].maintenance_margin(qty * adverse))
    margin_balance = wallet + np.sum(upnl, axis=0)
    maint = np.sum(mm, axis=0)
    triggered = margin_balance <= maint
    m = int(triggered.argmax()) if triggered.any() else -1
    # every leg must be observed (gaps <= 60 min) up to the bust, the segment end or,
    # for the last segment, the union grid's horizon (another leg's later bars)
    end = int(grid[m]) if m >= 0 else (seg_end if seg_end is not None else int(grid[-1]))
    for vi, _b in legs:
        _check_gaps(traded[vi].symbol, marks[traded[vi].symbol].ts, event_ts, end)
    if m < 0:
        return False

    bust_ts = int(grid[m])
    mb, tot_mm = float(margin_balance[m]), float(maint[m])
    trigger_leg = int(np.argmin([float(u[m]) for u in upnl]))  # largest adverse uPnL
    other_mm = tot_mm - float(mm[trigger_leg][m])
    other_upnl = float(sum(float(u[m]) for j, u in enumerate(upnl) if j != trigger_leg))
    fills: list[float] = []
    insolvent_leg = False
    for j, (vi, book) in enumerate(legs):
        side = 1 if book.qty > 0.0 else -1
        if j != trigger_leg:
            fills.append(float(closes[j][m]))
            continue
        lp: float | None
        try:
            # the signed wallet: a negative cross wallet is legal while MB > MM
            lp = cross_liq_price(
                side,
                abs(book.qty),
                book.avg,
                wallet,
                max(other_mm, 0.0),
                other_upnl,
                vec[traded[vi].symbol].table,
            )
        except InsolventPosition:
            lp, insolvent_leg = None, True  # short insolvent at every price
        fills.append(_fill_price(side, lp, float(opens[j][m])))
    raw = [
        (1 if b.qty > 0.0 else -1) * abs(b.qty) * (px - b.avg)
        for (_vi, b), px in zip(legs, fills, strict=True)
    ]
    # account margin balance once every leg is closed; the clearance fee is charged
    # once, on the triggering leg, out of this remainder
    mb_closed = wallet + sum(raw)
    trigger_vi, trigger_book = legs[trigger_leg]
    fee = _clearance_fee(
        vec[traded[trigger_vi].symbol].table,
        abs(trigger_book.qty) * fills[trigger_leg],
        mb_closed,
    )
    deficits = _attribute_deficit(max(fee - mb_closed, 0.0), raw, legs, fills)
    for j, ((vi, book), price) in enumerate(zip(legs, fills, strict=True)):
        view = traded[vi]
        side = 1 if book.qty > 0.0 else -1
        result.hits.append(
            LiquidationHit(
                key=view.key,
                symbol=view.symbol,
                ts=bust_ts,
                price=float(price),
                qty=float(abs(book.qty)),
                side=side,
                mark=float(lows[j][m]) if side > 0 else float(highs[j][m]),
                margin_balance=mb,
                maint_margin=tot_mm,
                clearance_fee=float(fee) if j == trigger_leg else 0.0,
                realized_pnl=float(raw[j] + deficits[j]),
                insolvent=insolvent_leg and j == trigger_leg,
                insurance_deficit=float(deficits[j]),
            )
        )
    result.bust_ts = bust_ts
    return True


def _attribute_deficit(
    deficit: float, raw: list[float], legs: list[tuple[int, _Book]], fills: list[float]
) -> list[float]:
    """Split the account-level insurance deficit over the hits (chief rule G2).

    ``deficit`` is what the venue absorbs so the account ends at 0: equity after every
    leg closed at its fill, minus the fee, floored at 0 (not capped by any leg's loss; a
    negative wallet makes it exceed them). It is attributed pro rata to the legs' price
    losses; when no leg lost (the shortfall is a negative wallet) pro rata to the
    closing notional ``qty * fill``.
    """
    if deficit <= 0.0:
        return [0.0] * len(raw)
    weights = [max(-r, 0.0) for r in raw]
    if sum(weights) <= 0.0:
        weights = [abs(b.qty) * px for (_vi, b), px in zip(legs, fills, strict=True)]
    total = sum(weights)
    return [deficit * w / total for w in weights]
