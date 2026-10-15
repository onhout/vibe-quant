"""Run-level entry point shared by the screening and validation tiers.

``apply_liquidation`` turns a finished run's NT positions into an
:class:`~vibe_quant.liquidation.overlay.OverlayResult`: it loads the MARK series of every
traded symbol, fails closed unless they cover the whole run window, loads the tier tables
and calls :func:`~vibe_quant.liquidation.overlay.liquidation_overlay`. Callers then
recompute EVERY metric from ``OverlayResult.ledgers`` / ``hits_by_key`` /
``dropped_keys`` unless ``is_identity`` (see ``overlay.py`` for the ledger contract).

Import it as ``vibe_quant.liquidation.apply`` only on the ``liquidation_mode != "off"``
path: it pulls in the catalog/NautilusTrader stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from vibe_quant.data.mark_bars import load_mark_series, require_mark_coverage
from vibe_quant.liquidation.overlay import OverlayResult, liquidation_overlay
from vibe_quant.liquidation.tiers import load_tier_tables

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from vibe_quant.data.mark_bars import MarkSeries
    from vibe_quant.liquidation.scan import Mode
    from vibe_quant.liquidation.tiers import TierTable

__all__ = ["apply_liquidation", "traded_symbols"]

_MODES = ("isolated", "cross")


def traded_symbols(nt_positions: Iterable[Any]) -> list[str]:
    """Sorted mark/tier symbols of the positions (``BTCUSDT-PERP.BINANCE`` -> ``BTCUSDT``).

    Same mapping as ``overlay.position_views`` (the part before the first ``-``).
    """
    return sorted({str(p.instrument_id).split("-")[0] for p in nt_positions})


def apply_liquidation(
    nt_positions: Iterable[Any],
    *,
    mode: Mode,
    leverage: float,
    starting_balance: float,
    catalog_path: Path | str,
    start_ns: int,
    end_ns: int,
    execution_bar_ns: int,
    funding_by_position: Mapping[str, Sequence[tuple[int, float]]] | None,
    cash_by_position: Mapping[str, Sequence[tuple[int, float]]] | None,
    scan_fill_ts: Mapping[str, Mapping[int, int]] | None = None,
    tables: Mapping[str, TierTable] | None = None,
) -> OverlayResult:
    """Scan a run's positions on real MARK bars and build the truncated ledgers.

    The run window is half-open, ``[start_ns, end_ns)``, for everything the scan sees: only
    MARK bars opening inside it are scanned (plus one carry bar before ``start_ns``), so a
    position still open at the run end is never liquidated by a later price, and a fill or a
    funding/cash flow at ``ts >= end_ns`` is refused (it is never charged; see Raises).

    Args:
        nt_positions: Every position incarnation of the run (closed, snapshots and
            still-open ones); duck-typed as in ``overlay.position_views``.
        mode: ``"isolated"`` or ``"cross"``.
        leverage: Venue leverage (screening: the fixed 10x venue).
        starting_balance: Account balance at the start of the run.
        catalog_path: ParquetDataCatalog root holding the MARK bars.
        start_ns / end_ns: The run window ``[start_ns, end_ns)``; the MARK series of every
            traded symbol must cover it.
        execution_bar_ns: Finest execution timeframe in ns (``overlay.liquidation_overlay``).
        funding_by_position / cash_by_position: Per position key (``<id>@<ts_opened>``)
            funding and every other modeled cash flow, in LEDGER sign (negative = paid),
            untruncated: the overlay truncates them at a liquidation. Every ts < ``end_ns``.
        scan_fill_ts: Per position key, ``{fill index: refined ts}``: the timestamp the SCANNER
            sees for that fill (``None`` = every real timestamp, byte-identical to before).
            The ledgers, funding and MTM always keep the NT timestamps
            (``overlay.liquidation_overlay``).
        tables: Tier tables; ``None`` loads the shipped snapshot.

    Raises:
        ValueError: ``mode`` is not isolated/cross, the window is empty/not integral, or a
            position fill or a funding/cash flow lies at or after ``end_ns`` (a caller bug: it
            would liquidate on trades or money outside the run, and the caller's own
            accounting would disagree). Also a refined scan timestamp naming an unknown
            position or fill index.
        vibe_quant.data.mark_bars.MarkDataMissing: a traded symbol has no MARK bars, or
            they leave a hole in the window (the run fails closed).
        Whatever :func:`liquidation_overlay` raises (unknown symbol, non-replayable ledger,
        ``RefinedExitConflict``).
    """
    if mode not in _MODES:
        raise ValueError(f"liquidation mode must be one of {_MODES}, got {mode!r}")
    for name, value in (("start_ns", start_ns), ("end_ns", end_ns)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an int (ns), got {value!r}")
    if end_ns <= start_ns:
        raise ValueError(f"empty liquidation window [{start_ns}, {end_ns})")
    for name, flows in (("funding", funding_by_position), ("cash", cash_by_position)):
        for key, events in (flows or {}).items():
            late = [int(ts) for ts, _ in events if int(ts) >= end_ns]
            if late:
                raise ValueError(
                    f"{key}: {name} flow at ts {late[0]} is not before the run end {end_ns} "
                    f"(the liquidation window is [start_ns, end_ns))"
                )
    positions = list(nt_positions)
    for pos in positions:
        late = [int(f.ts_event) for f in pos.events if int(f.ts_event) >= end_ns]
        if late:
            raise ValueError(
                f"{pos.id}@{int(pos.ts_opened)}: fill at ts {late[0]} is not before the run end "
                f"{end_ns} (the liquidation window is [start_ns, end_ns))"
            )

    marks: dict[str, MarkSeries] = {}
    for symbol in traded_symbols(positions):
        series = load_mark_series(catalog_path, symbol)
        require_mark_coverage(series, symbol, start_ns, end_ns)
        marks[symbol] = _run_window(cast("MarkSeries", series), start_ns, end_ns)
    return liquidation_overlay(
        positions,
        marks,
        mode=mode,
        starting_balance=starting_balance,
        leverage=leverage,
        tables=load_tier_tables() if tables is None else tables,
        funding_by_position=funding_by_position,
        cash_by_position=cash_by_position,
        execution_bar_ns=execution_bar_ns,
        scan_fill_ts=scan_fill_ts,
    )


def _run_window(series: MarkSeries, start_ns: int, end_ns: int) -> MarkSeries:
    """The bars the scan may see: those opening in ``[start_ns, end_ns)`` (no look-ahead past
    the run end), plus the last bar opening at or before ``start_ns`` so an event at the start
    is priced even when the bar at the start itself is missing (a gap under an hour)."""
    import numpy as np

    lo = max(int(np.searchsorted(series.ts, start_ns, side="right")) - 1, 0)
    hi = int(np.searchsorted(series.ts, end_ns, side="left"))
    return type(series)(*(column[lo:hi] for column in series))
