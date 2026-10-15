"""Bridge between NautilusTrader positions and the liquidation scanner.

``position_views`` turns NT positions into scanner input; ``liquidation_overlay``
scans them and returns the truncated per-instrument account ledgers the metric
code (``extraction.mark_to_market_paths(ledgers=...)``) recomputes equity from.
No runner integration lives here.

Ledger identity (decided 2026-10-13, T2b; revised in fix rounds 5 and 6). REAL events are
immutable: every NT fill keeps its price, qty, commission cash and resting flag, every
funding / other cash event its amount, sign and timestamp. A liquidated position ``H``
keeps every real event with ``ts <= H.ts`` (the scanner applies all events at a timestamp
before it evaluates the bar, so the fill that crossed the liquidation price stays),
loses everything later, and gains ONE synthetic closing event: ``qty = -side * H.qty``,
``cash = -H.clearance_fee``, and a price ``P`` with ``side * H.qty * (P - avg) ==
H.realized_pnl`` (``avg`` = the kept fills' average cost). ``H.realized_pnl`` is the price
PnL FLOORED by the insurance fund (``raw + H.insurance_deficit``), so

    realized PnL of the close event + its cash = H.realized_pnl - H.clearance_fee = -H.loss

and ``P == H.price`` exactly when there is no insurance deficit (otherwise ``P`` is the
bankruptcy-equivalent price; the fund's part is never charged to the account). The venue
fill price stays ``H.price`` in ``OverlayResult.exit_override``. Funding paid before
``H.ts`` stays booked as itself: the isolated hit loss (the remaining margin) already
nets it out of the margin, so the account's total loss for the position is its initial
margin, as in the scanner. Positions dropped by a cross bust vanish entirely (fills,
commissions, cash).

Valuation (``execution_bar_ns`` is REQUIRED: the finest execution timeframe in ns;
catalog ``ts_init`` is the bar's close less 1 ms, so a bar is ``[k*bar, (k+1)*bar)`` in
event time). The close carries ``LedgerEvent.liquidation``; the real events of the position
stamped at ``H.ts`` carry their margin cash in ``LedgerEvent.liq_margin`` (isolated: funding;
cross: all their cash, the cross wallet is the margin). ``pnl_path`` replays every real
event at its normal place with its normal valuation, the close right after the last real
event at ``H.ts``, and from that instant on values ONLY the liquidated quantity at no worse
than the bankruptcy price at the state's margin (``metrics._liquidation_walk``, with worked
examples). Genuine losses before the instant stay visible; there is no equity floor.
Supported input is what the DSL produces; what cannot be replayed exactly raises
``ValueError`` naming the position: kept fills of one position at the same ``ts`` that mix
resting and non-resting orders, a resting fill EARLIER than the hit in the hit's own
execution bar (it would replay after the instant), a scale-in or a flip at ``H.ts``, and an
event of another position of the instrument in that bar at or after ``H.ts`` (or resting).

After a cross bust under the forfeit clearance rule the scanner's account loss is the
wallet at bust, so the ledger's equity is ``0`` from ``bust_ts`` on; a float residual
is folded into the carrier's closing event so it is exactly the floored value. A
residual above ``_RESIDUAL_TOL`` (absolute, in quote currency) means the ledger and the
scanner disagree and raises (fail closed). Pass ``result.bust_ts`` as ``floor_from_ns`` to
``mark_to_market_drawdown`` / ``metrics.mark_to_market_equity``: the summed per-instrument
intrabar minimum can pair one leg's dip before an intrabar bust with another leg's
post-bust value, and the account itself is never below 0 from the bust bar on.

Per-position cash. ``funding_by_position`` is funding (the scanner takes it out of an
isolated margin); ``cash_by_position`` is every other modeled cash flow (slippage, ...),
which the scanner only books to the cross wallet. Both are in LEDGER sign (negative =
paid) and are truncated at the liquidation. Callers pass ALL such cash here and must
not combine ``ledgers=`` with ``extra_cash=`` in ``mark_to_market_paths`` (it raises):
that would book cash twice or after a bust.

Refined exits (2026-10-15, vjno2). ``scan_fill_ts`` moves the timestamp the SCANNER sees
for named fills (``{position key: {fill index: refined ts}}``) without touching the real
events: the ledgers, funding and MTM keep NT timestamps, so a run with ``None`` is
byte-identical to before. It exists because a resting exit is triggered inside a bar the
strategy bar only closes later: the scan must end the exposure at the refined instant.
The two timestamp sets must not disagree about which fills exist at a liquidation instant;
``_check_refined_consistency`` fails closed with ``RefinedExitConflict`` when they would.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from vibe_quant.liquidation.scan import (
    LiquidationHit,
    LiquidationResult,
    PositionView,
    scan_liquidations,
)
from vibe_quant.metrics import LedgerEvent
from vibe_quant.metrics import _Book as _LedgerBook  # the book pnl_path replays with

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from vibe_quant.liquidation.scan import MarkLike, Mode
    from vibe_quant.liquidation.tiers import TierTable

__all__ = [
    "OverlayResult",
    "RefinedExitConflict",
    "default_symbol",
    "enum_name",
    "liquidation_overlay",
    "position_views",
]

_QTY_TOL = 1e-9
_RESIDUAL_TOL = 1e-6  # absolute (quote currency); float noise is ~1e-12
_FILL, _CASH = 0, 1  # sort rank at equal ts: cash events after fills (as in position_ledger_events)
_LAST = 1 << 30
_OTHER_CASH = 1 << 20  # sort offset of cash_by_position behind funding at equal ts


@dataclass(frozen=True)
class OverlayResult:
    """Scanner result plus the truncated ledgers built from it.

    ``ledgers`` is ``None`` when ``is_identity`` (no hit, nothing dropped): callers
    then keep their own ledger path untouched. ``exit_override[key] = (ts, price)``
    is the synthetic exit of a liquidated position (for
    ``accrue_position_funding(..., exit_ns=ts)`` and to skip open-position marking).
    """

    result: LiquidationResult
    hits_by_key: dict[str, LiquidationHit]
    dropped_keys: frozenset[str]
    ledgers: dict[str, list[LedgerEvent]] | None
    exit_override: dict[str, tuple[int, float]] = field(default_factory=dict)

    @property
    def is_identity(self) -> bool:
        return not self.hits_by_key and not self.dropped_keys


class RefinedExitConflict(ValueError):
    """A refined scan timestamp straddles a liquidation instant (R24b, vjno2).

    A fill's refined scan timestamp ``r`` is at or before the instant ``X`` the ledgers truncate
    at (a hit's ts, or a cross bust's ts) while its NT timestamp ``n`` is after it. The scan
    (which applies the fill at ``r``) and the truncated ledgers (whose ``ts <= X`` filter drops
    it at ``n``) then disagree about the fills that exist at ``X``: a partial exit changes the
    liquidated quantity, a full exit gives the position no hit at all. Callers fail closed.
    """


@dataclass(frozen=True)
class _PosData:
    key: str
    symbol: str
    instrument_id: str
    fills: list[LedgerEvent]
    funding: list[tuple[int, float]]
    cash: list[tuple[int, float]]


def enum_name(value: Any) -> str:
    """The upper-case enum name of ``value`` (its ``.name``, else ``str(value)``)."""
    name = getattr(value, "name", None)
    return str(name if name is not None else value).upper()


def default_symbol(instrument_id: str) -> str:
    """The symbol of an NT instrument id (``BTCUSDT-PERP.BINANCE`` -> ``BTCUSDT``)."""
    return instrument_id.split("-")[0]


def _collect(
    nt_positions: Iterable[Any],
    funding_by_position: Mapping[str, Sequence[tuple[int, float]]] | None,
    cash_by_position: Mapping[str, Sequence[tuple[int, float]]] | None,
    symbol_of: Callable[[str], str] | None,
) -> list[_PosData]:
    """Per-position fill events from the same NT fields ``position_ledger_events`` reads.

    Positions are ordered by open time (as there). A key is ``<position id>@<ts_opened>``:
    NT netting reuses position ids, the open time separates the incarnations.
    """
    symbol_fn = symbol_of or default_symbol
    ordered = sorted(
        nt_positions,
        key=lambda p: (int(p.ts_opened), int(p.ts_closed) or 2**63 - 1),
    )
    out: list[_PosData] = []
    seen: set[str] = set()
    for pos in ordered:
        key = f"{pos.id}@{int(pos.ts_opened)}"
        if key in seen:
            raise ValueError(f"duplicate position key {key!r}")
        seen.add(key)
        raw_fills = pos.events
        if not raw_fills:
            raise ValueError(f"position {key} has no fill events")
        fills: list[LedgerEvent] = []
        for fill in raw_fills:
            side = enum_name(fill.order_side)
            if side not in ("BUY", "SELL"):
                raise ValueError(f"position {key}: unknown order side {side!r}")
            fills.append(
                LedgerEvent(
                    ts=int(fill.ts_event),
                    qty=(1.0 if side == "BUY" else -1.0) * float(fill.last_qty),
                    price=float(fill.last_px),
                    cash=-float(fill.commission),
                    resting=enum_name(fill.order_type) != "MARKET",
                )
            )
        instrument_id = str(pos.instrument_id)
        funding = sorted(
            ((int(ts), float(a)) for ts, a in (funding_by_position or {}).get(key, ())),
            key=lambda item: item[0],
        )
        cash = sorted(
            ((int(ts), float(a)) for ts, a in (cash_by_position or {}).get(key, ())),
            key=lambda item: item[0],
        )
        out.append(_PosData(key, symbol_fn(instrument_id), instrument_id, fills, funding, cash))
    for what, given in (("funding", funding_by_position), ("cash", cash_by_position)):
        unknown = set(given or {}) - seen
        if unknown:
            raise ValueError(f"{what} for unknown position keys: {sorted(unknown)}")
    return out


def _view(data: _PosData, scan_fill_ts: Mapping[int, int] | None = None) -> PositionView:
    # other modeled cash is a cash-only event: the scanner books it to the cross wallet
    # and leaves an isolated margin alone
    fills: Sequence[LedgerEvent] = data.fills
    if scan_fill_ts:
        # the scanner sees the refined instant; the real events (and so the ledgers) keep NT ts
        fills = [
            e if i not in scan_fill_ts else replace(e, ts=scan_fill_ts[i])
            for i, e in enumerate(data.fills)
        ]
    events = [*fills, *(LedgerEvent(ts=ts, cash=a) for ts, a in data.cash)]
    return PositionView(data.key, data.symbol, data.instrument_id, events, data.funding)


def _scan_overrides(
    data: Sequence[_PosData], scan_fill_ts: Mapping[str, Mapping[int, int]] | None
) -> dict[str, Mapping[int, int]]:
    """The per-position refined scan timestamps, checked against the positions of this run."""
    if not scan_fill_ts:
        return {}
    by_key = {d.key: d for d in data}
    unknown = set(scan_fill_ts) - set(by_key)
    if unknown:
        raise ValueError(f"refined scan timestamps for unknown position keys: {sorted(unknown)}")
    for key, overrides in scan_fill_ts.items():
        n_fills = len(by_key[key].fills)
        for index in overrides:
            if not 0 <= index < n_fills:
                raise ValueError(
                    f"{key}: refined scan timestamp for fill index {index}, but the position "
                    f"has {n_fills} fills"
                )
    return dict(scan_fill_ts)


def position_views(
    nt_positions: Iterable[Any],
    funding_by_position: Mapping[str, Sequence[tuple[int, float]]] | None = None,
    cash_by_position: Mapping[str, Sequence[tuple[int, float]]] | None = None,
    *,
    symbol_of: Callable[[str], str] | None = None,
) -> list[PositionView]:
    """Scanner input from NT positions (duck-typed: no NautilusTrader import needed).

    Reads ``id, ts_opened, ts_closed, instrument_id, events`` of each position and
    ``order_side, order_type, ts_event, last_qty, last_px, commission`` of each fill.
    ``funding_by_position`` maps a position key (``<id>@<ts_opened>``) to its funding
    cash ``(ts, amount)`` in LEDGER sign (negative = paid by us, i.e. ``-payment``);
    ``cash_by_position`` the same for every other modeled cash flow (slippage, ...).
    ``symbol_of`` maps an instrument id to the mark/tier symbol (default: the part
    before the first ``-``, ``BTCUSDT-PERP.BINANCE`` -> ``BTCUSDT``).
    """
    data = _collect(nt_positions, funding_by_position, cash_by_position, symbol_of)
    return [_view(d) for d in data]


def liquidation_overlay(
    nt_positions: Iterable[Any],
    marks: Mapping[str, MarkLike],
    *,
    mode: Mode,
    starting_balance: float,
    leverage: float,
    tables: Mapping[str, TierTable],
    funding_by_position: Mapping[str, Sequence[tuple[int, float]]] | None = None,
    cash_by_position: Mapping[str, Sequence[tuple[int, float]]] | None = None,
    symbol_of: Callable[[str], str] | None = None,
    execution_bar_ns: int,
    scan_fill_ts: Mapping[str, Mapping[int, int]] | None = None,
) -> OverlayResult:
    """Scan ``nt_positions`` on mark bars and build the truncated ledgers.

    ``scan_fill_ts`` overrides the timestamp the scanner sees for named fills
    (``{position key: {fill index: refined ts}}``); the real events, and so the ledgers,
    keep their NT timestamps (see the module docstring). ``None`` leaves the scan unchanged.

    Raises whatever :func:`scan_liquidations` raises (``MissingMarkSeries``,
    ``UnknownSymbol``, ``ValueError`` for non-finite inputs, ...): callers fail closed.
    ``RefinedExitConflict`` when a refined fill would make the scan and the ledgers disagree
    at a liquidation instant, and ``ValueError`` for a refined timestamp naming an unknown
    position key or fill index.
    """
    if isinstance(execution_bar_ns, bool) or not isinstance(execution_bar_ns, int):
        raise ValueError(f"execution_bar_ns must be an int > 0, got {execution_bar_ns!r}")
    if execution_bar_ns <= 0:
        raise ValueError(f"execution_bar_ns must be an int > 0, got {execution_bar_ns!r}")
    data = _collect(nt_positions, funding_by_position, cash_by_position, symbol_of)
    refined = _scan_overrides(data, scan_fill_ts)
    result = scan_liquidations(
        [_view(d, refined.get(d.key)) for d in data],
        marks,
        mode=mode,
        starting_balance=starting_balance,
        leverage=leverage,
        tables=tables,
    )
    hits = {h.key: h for h in result.hits}
    dropped = frozenset(result.dropped_keys)
    if not hits and not dropped:
        return OverlayResult(result, hits, dropped, None)
    keys = {d.key for d in data}
    if not (set(hits) | dropped) <= keys:
        raise ValueError("scanner returned keys that are not positions of this run")
    _check_refined_consistency(data, hits, dropped, result, refined)
    ledgers = _truncated_ledgers(data, hits, dropped, result, starting_balance, execution_bar_ns)
    exit_override = {k: (h.ts, h.price) for k, h in hits.items()}
    return OverlayResult(result, hits, dropped, ledgers, exit_override)


def _check_refined_consistency(
    data: Sequence[_PosData],
    hits: Mapping[str, LiquidationHit],
    dropped: frozenset[str],
    result: LiquidationResult,
    scan_fill_ts: Mapping[str, Mapping[int, int]],
) -> None:
    """Fail closed when a refined scan timestamp straddles the instant the ledgers truncate at.

    R24b: write ``r`` for a fill's refined scan timestamp and ``n`` for its NT timestamp. If
    ``r <= X < n`` the scan (which applies the fill at ``r``) and the ledger (whose ``ts <= X``
    filter drops it at ``n``) disagree about the fills that exist at ``X``: a partial exit
    changes the liquidated qty, a full exit drops the position's hit. Two instants are checked:
    in isolated mode each hit's own position at ``X = hit.ts``; in cross mode every position
    that survives the bust at ``X = bust_ts`` (a cross bust is the only instant a cross run
    truncates at, and a dropped position's fills and ledgers both vanish).
    """
    if not scan_fill_ts:
        return
    by_key = {d.key: d for d in data}
    checked: list[tuple[_PosData, int]] = []
    if result.mode == "cross":
        if result.bust_ts is None:
            return
        checked = [(d, result.bust_ts) for d in data if d.key not in dropped]
    else:
        checked = [(by_key[h.key], h.ts) for h in hits.values()]
    for d, x in checked:
        for index, r in sorted((scan_fill_ts.get(d.key) or {}).items()):
            n = d.fills[index].ts
            if r <= x < n:
                raise RefinedExitConflict(
                    f"{d.key}: fill {index} refined to {r} is scanned at or before {x} but its "
                    f"NT ts {n} is after {x}; the scan and the ledgers disagree on the fills at {x}"
                )


def _truncated_ledgers(
    data: Sequence[_PosData],
    hits: Mapping[str, LiquidationHit],
    dropped: frozenset[str],
    result: LiquidationResult,
    starting_balance: float,
    execution_bar_ns: int,
) -> dict[str, list[LedgerEvent]]:
    # (ts, rank, position order, index) keeps position_ledger_events' order for kept events
    entries: dict[str, list[tuple[tuple[int, int, int, int], LedgerEvent]]] = {}
    closes: dict[str, tuple[str, LedgerEvent]] = {}  # hit key -> (instrument id, closing event)
    cross = result.mode == "cross"
    for order, d in enumerate(data):
        if d.key in dropped:
            continue
        hit = hits.get(d.key)
        bucket = entries.setdefault(d.instrument_id, [])
        if hit is None:
            fills, funding, cash = d.fills, d.funding, d.cash
        else:
            fills = [e for e in d.fills if e.ts <= hit.ts]
            funding = [f for f in d.funding if f[0] <= hit.ts]
            cash = [c for c in d.cash if c[0] <= hit.ts]
        own: list[tuple[tuple[int, int, int, int], LedgerEvent]] = []
        for i, e in enumerate(fills):
            own.append(((e.ts, _FILL, order, i), e))
        for i, (ts, amount) in enumerate(funding):
            own.append(((ts, _CASH, order, i), LedgerEvent(ts=ts, cash=amount)))
        for i, (ts, amount) in enumerate(cash):
            own.append(((ts, _CASH, order, _OTHER_CASH + i), LedgerEvent(ts=ts, cash=amount)))
        if hit is not None:
            _check_replayable(d.key, fills, hit, execution_bar_ns)
            own = [(k, _with_margin(k, e, hit, cross)) for k, e in own]
            close = _closing_event(d.key, fills, hit)
            # after this position's kept fills and cash at the same ts
            own.append(((hit.ts, _CASH, order, _LAST), close))
            own.sort(key=lambda item: item[0])
            _check_instant(d.key, own, hit)
            closes[d.key] = (d.instrument_id, close)
        bucket.extend(own)

    _check_bar_alone(entries, hits, data, execution_bar_ns)
    ledgers = {
        iid: [e for _k, e in sorted(items, key=lambda item: item[0])]
        for iid, items in entries.items()
        if items
    }
    _book_bust_residual(ledgers, closes, hits, result, starting_balance)
    return ledgers


def _with_margin(
    key: tuple[int, int, int, int], e: LedgerEvent, hit: LiquidationHit, cross: bool
) -> LedgerEvent:
    """Annotate a real event stamped at the hit with the part of its cash that is margin of
    the liquidated quantity (isolated: funding only; cross: all cash, the wallet is margin)."""
    if e.ts != hit.ts or e.cash == 0.0:
        return e
    is_funding = key[1] == _CASH and key[3] < _OTHER_CASH
    if not (cross or is_funding):
        return e
    return replace(e, liq_margin=e.cash)


def _check_replayable(
    key: str, fills: Sequence[LedgerEvent], hit: LiquidationHit, execution_bar_ns: int
) -> None:
    """Raise unless ``pnl_path`` can replay this position's fills and its close exactly."""
    flags_at: dict[int, set[bool]] = {}
    for e in fills:
        flags_at.setdefault(e.ts, set()).add(e.resting)
    for ts, flags in flags_at.items():
        if len(flags) > 1:
            raise ValueError(
                f"{key}: fills at ts {ts} mix resting and non-resting orders; "
                "their replay order inside the execution bar cannot be reproduced exactly"
            )
    bar = hit.ts // execution_bar_ns
    if any(e.resting and e.ts != hit.ts and e.ts // execution_bar_ns == bar for e in fills):
        raise ValueError(
            f"{key}: a resting fill precedes the liquidation at ts {hit.ts} inside the same "
            f"{execution_bar_ns} ns execution bar; the close cannot be replayed after it exactly"
        )


def _check_instant(
    key: str,
    own: Sequence[tuple[tuple[int, int, int, int], LedgerEvent]],
    hit: LiquidationHit,
) -> None:
    """Raise unless the fills at ``hit.ts`` only reduce (or open) the position and the
    synthetic close flattens it: the valuation of the instant assumes exactly that."""
    book = _LedgerBook()
    for _k, e in own:
        if e.ts == hit.ts and book.qty != 0.0 and e.qty != 0.0 and not e.liquidation:
            if (e.qty > 0.0) == (book.qty > 0.0):
                raise ValueError(
                    f"{key}: a scale-in fill at the liquidation ts {hit.ts} cannot be "
                    "replayed exactly"
                )
            if abs(e.qty) > abs(book.qty) * (1.0 + _QTY_TOL) + _QTY_TOL:
                raise ValueError(f"{key}: a fill at the liquidation ts {hit.ts} flips the position")
        book.apply(e)
    if book.qty != 0.0:
        raise ValueError(f"{key}: position still open after its liquidation close")


def _check_bar_alone(
    entries: Mapping[str, Sequence[tuple[tuple[int, int, int, int], LedgerEvent]]],
    hits: Mapping[str, LiquidationHit],
    data: Sequence[_PosData],
    execution_bar_ns: int,
) -> None:
    """Raise when another position of the instrument has an event in a hit's execution bar
    at or after the hit (or a resting fill before it): ``pnl_path`` would replay it inside
    the liquidation instant and net it against the liquidated position."""
    order_of = {d.key: n for n, d in enumerate(data)}
    for hit_key, hit in hits.items():
        d = data[order_of[hit_key]]
        bar = hit.ts // execution_bar_ns
        for (_ts, _rank, order, _i), e in entries.get(d.instrument_id, ()):
            if order == order_of[hit_key] or e.ts // execution_bar_ns != bar:
                continue
            if e.ts >= hit.ts or e.resting:
                raise ValueError(
                    f"{hit_key}: position {data[order].key} has an event at ts {e.ts} in the "
                    f"liquidation's {execution_bar_ns} ns execution bar; not replayable exactly"
                )


def _closing_event(key: str, fills: Sequence[LedgerEvent], hit: LiquidationHit) -> LedgerEvent:
    """The synthetic event that closes a liquidated position (see the module docstring)."""
    book = _LedgerBook()
    for e in fills:
        book.apply(e)
    if not math.isclose(book.qty, hit.side * hit.qty, rel_tol=_QTY_TOL, abs_tol=_QTY_TOL):
        raise ValueError(
            f"{key}: fills up to the liquidation leave qty {book.qty}, scanner closed "
            f"{hit.side * hit.qty}"
        )
    price = hit.price
    if hit.insurance_deficit != 0.0:
        # bankruptcy-equivalent price: its raw loss is the floored one the account bears
        price = book.avg + hit.realized_pnl / (hit.side * hit.qty)
        if not math.isfinite(price):
            raise ValueError(f"{key}: non-finite bankruptcy price for liquidation at {hit.ts}")
    return LedgerEvent(
        ts=hit.ts, qty=-hit.side * hit.qty, price=price, cash=-hit.clearance_fee, liquidation=True
    )


def _book_bust_residual(
    ledgers: dict[str, list[LedgerEvent]],
    closes: Mapping[str, tuple[str, LedgerEvent]],
    hits: Mapping[str, LiquidationHit],
    result: LiquidationResult,
    starting_balance: float,
) -> None:
    """Cross + forfeit: the account is wiped at ``bust_ts``; make the ledger land on 0."""
    if result.mode != "cross" or result.bust_ts is None or not hits:
        return
    if result.clearance_rule != "forfeit_remaining_margin":
        return  # a fee rate can leave positive equity at the bust
    total = 0.0
    for events in ledgers.values():
        book = _LedgerBook()
        for e in events:
            book.apply(e)
        total += book.realized
    residual = starting_balance + total
    if abs(residual) > _RESIDUAL_TOL:
        raise ValueError(
            f"liquidation ledger does not wipe the account at bust: equity {residual!r} "
            f"(starting balance {starting_balance!r})"
        )
    if residual == 0.0:
        return
    carrier = max(hits.values(), key=lambda h: h.clearance_fee)
    iid, event = closes[carrier.key]
    events = ledgers[iid]
    at = next(i for i, e in enumerate(events) if e is event)
    new_cash = event.cash - residual
    if new_cash > 0.0:  # keep the cash <= 0 invariant of the closing event (price absorbs it)
        events[at] = replace(event, cash=0.0, price=event.price - new_cash / event.qty)
    else:
        events[at] = replace(event, cash=new_cash)
