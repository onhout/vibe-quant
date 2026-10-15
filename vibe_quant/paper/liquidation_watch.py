"""Per-symbol forceOrders watch loop (bead vibe-quant-2gyk1.1.11, task P3a2).

Task T6 detects exchange-initiated closes (liquidation, ADL) that NautilusTrader
never sees. :mod:`vibe_quant.paper.force_orders` (P3a1) is the read-only fetch and
decode half; this module is the poll loop and its per-symbol state, and P3b wires
it into :mod:`vibe_quant.paper.node`.

**REPORT ONLY.** The watcher holds no reference to the strategy, the guard or the
exec engine (R49): it cannot submit, cancel, close or halt anything, so a bug here
can lose alerts but never move a position. It turns forceOrders rows into
``on_liquidation`` calls and warning/recovery alerts, and keeps one cursor, one
consecutive-failure count and one backoff per symbol (R50, R52).

Contract rows implemented here:

* **R46** — each cycle, every symbol due by its own backoff gets exactly one request
  with ``startTime = max(cursor[s] - overlap_ms, end - lookback_cap_ms + 60_000)``,
  ``endTime = end``, and one ``on_warning`` per symbol per 7-day clamp episode.
* **R47 / R47b** — rows are emitted in ``updateTime`` order; per row the handler runs
  first and the key is marked seen after it, and ``cursor[s] = end`` only after every
  row of that symbol.
* **R48** — ``executedQty == 0`` rows are skipped and never marked seen.
* **R50** — :meth:`state` is ``{"cursors": {symbol: ms}, "seen": {key: update_ms}}``
  with ``key = f"{symbol}:{order_id}"``; restore reads the passed maps and ``seen`` is
  pruned to ``update_ms >= min(cursor) - overlap_ms``. That floor is at or below the
  start of every later window (``max(cursor - overlap_ms, end - cap + slack)`` is at
  least ``cursor - overlap_ms``, which is at least ``min(cursor) - overlap_ms``), so
  pruning can only drop keys no later window can return again: within one process a
  key is delivered at most once (R52b) even while a symbol more than 7 days behind
  keeps the R46 clamp active. Pruning too little costs memory; it can never cost an
  alert.
* **R51** — a full page raises from the fetch and fails that symbol's cycle: no
  events, cursor unchanged.
* **R52 / R52b / R52c / R52f** — failures are per symbol, the n-th one delays the
  next attempt by ``min(interval_s * 2**(n-1), max_backoff_s)``, one ``on_warning`` at
  ``degraded_after`` failures, ``on_recovered`` on the first success after that, and a
  handler error fails that symbol's cycle without advancing its cursor. Each fetch is
  cut off after ``fetch_timeout_s`` (30 s by default): a hung request is that symbol's
  failure, so it can neither block the other symbols nor keep :meth:`run` from seeing
  the stop event. Only ``asyncio.CancelledError`` leaves :meth:`run`.
* **R53** — :meth:`run` returns as soon as the stop event is set, and cancellation
  propagates.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from vibe_quant.paper.force_orders import Fetch, ForceOrder

logger = logging.getLogger(__name__)

#: R46: the clamped window is the lookback cap minus one minute.
_CLAMP_SLACK_MS = 60_000
_MS_PER_DAY = 86_400_000

type LiquidationHandler = Callable[[ForceOrder], Awaitable[None]]
type WarningHandler = Callable[[str], Awaitable[None]]
type RecoveredHandler = Callable[[str, int], Awaitable[None]]


class LiquidationWatcher:
    """Poll forceOrders per symbol and report exchange-initiated closes (R46-R53).

    Args:
        fetch: P3a1's read-only ``Fetch``: ``(symbol, start_ms, end_ms) -> rows``.
        symbols: Symbols to watch, in request order; duplicates are dropped.
        on_liquidation: Async handler for one new row (P3b writes the LIQUIDATION
            event and sends the critical alert). A raise fails that symbol's cycle
            and leaves its cursor unchanged (R52c).
        on_warning: Async handler for a degraded or clamped alert message (R46, R52).
        on_recovered: Async handler ``(symbol, failures)`` on the first success after
            the symbol was reported degraded, where ``failures`` is the run of
            consecutive failures that just ended (R52).
        now_ms: Injected clock in epoch milliseconds, read once per cycle (R46).
        cursors: Restored per-symbol cursors from the checkpoint, or ``None``. A
            symbol with no restored cursor starts at the current ``now_ms`` (R50).
        seen: Restored ``key -> update_ms`` map, or ``None`` (R50).
        interval_s: Healthy cadence in seconds.
        max_backoff_s: Cap on the per-symbol backoff, in seconds (R52).
        overlap_ms: How far behind each cursor a request re-reads, in milliseconds.
        degraded_after: Consecutive failures that raise the single degraded warning.
        lookback_cap_ms: Hard cap on how far back one request may reach (R46).
        fetch_timeout_s: Deadline for one fetch, in seconds (R52f). ``asyncio``
            cancels a fetch that outlives it, which counts as that symbol's failure
            (R52) so one hung symbol cannot stall the others or the stop event.

    Raises:
        ValueError: If ``interval_s``, ``max_backoff_s``, ``overlap_ms``,
            ``degraded_after``, ``lookback_cap_ms`` or ``fetch_timeout_s`` is not
            positive.
    """

    def __init__(
        self,
        fetch: Fetch,
        symbols: Sequence[str],
        *,
        on_liquidation: LiquidationHandler,
        on_warning: WarningHandler,
        on_recovered: RecoveredHandler,
        now_ms: Callable[[], int],
        cursors: Mapping[str, int] | None,
        seen: Mapping[str, int] | None,
        interval_s: int = 60,
        max_backoff_s: int = 600,
        overlap_ms: int = 300_000,
        degraded_after: int = 3,
        lookback_cap_ms: int = 604_800_000,
        fetch_timeout_s: float = 30.0,
    ) -> None:
        for name, value in (
            ("interval_s", interval_s),
            ("max_backoff_s", max_backoff_s),
            ("overlap_ms", overlap_ms),
            ("degraded_after", degraded_after),
            ("lookback_cap_ms", lookback_cap_ms),
            ("fetch_timeout_s", fetch_timeout_s),
        ):
            if value <= 0:
                raise ValueError(f"liquidation watcher {name} must be positive, got {value}")

        self._fetch = fetch
        self._symbols = list(dict.fromkeys(symbols))
        self._on_liquidation = on_liquidation
        self._on_warning = on_warning
        self._on_recovered = on_recovered
        self._now_ms = now_ms
        self._interval_s = interval_s
        self._max_backoff_s = max_backoff_s
        self._overlap_ms = overlap_ms
        self._degraded_after = degraded_after
        self._lookback_cap_ms = lookback_cap_ms
        self._fetch_timeout_s = fetch_timeout_s

        node_start_ms = now_ms()
        restored = cursors or {}
        # R50: fresh start = node start ms; restore = the checkpoint's per-symbol
        # cursor, else the node start ms.
        self._cursors = {
            symbol: int(restored.get(symbol, node_start_ms)) for symbol in self._symbols
        }
        self._seen = {key: int(value) for key, value in (seen or {}).items()}
        self._failures: dict[str, int] = dict.fromkeys(self._symbols, 0)
        self._next_attempt_ms: dict[str, int] = dict.fromkeys(self._symbols, 0)
        self._degraded: set[str] = set()
        self._clamped: set[str] = set()
        self._prune_seen()

    def state(self) -> dict[str, dict[str, int]]:
        """Return the checkpoint payload, already pruned (R50)."""
        self._prune_seen()
        return {"cursors": dict(self._cursors), "seen": dict(self._seen)}

    async def poll_once(self) -> None:
        """Run one cycle over the symbols that are due by their own backoff (R46).

        Only ``asyncio.CancelledError`` escapes: a fetch, decode or handler error
        fails that symbol alone (R52).
        """
        end_ms = self._now_ms()
        for symbol in self._symbols:
            if self._next_attempt_ms[symbol] > end_ms:
                continue
            try:
                await self._poll_symbol(symbol, end_ms)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._note_failure(symbol, exc)
            else:
                await self._note_success(symbol)
        self._prune_seen()

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set or the task is cancelled (R53).

        Waits on the stop event rather than sleeping blind, so a shutdown during a
        backoff returns immediately.
        """
        while not stop.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("liquidation watch cycle failed")
            timeout_s = self._time_to_next_attempt_s()
            if timeout_s <= 0:
                await asyncio.sleep(0)
                continue
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=timeout_s)

    async def _poll_symbol(self, symbol: str, end_ms: int) -> None:
        start_ms = self._start_ms(symbol, end_ms)
        await self._warn_if_clamped(symbol, start_ms)
        # R52f: a fetch that hangs is cut off and counted as this symbol's failure,
        # so the symbols after it still run and run() still sees the stop event.
        orders = await asyncio.wait_for(
            self._fetch(symbol, start_ms, end_ms),
            timeout=self._fetch_timeout_s,
        )
        # R47: emission order is updateTime order whatever the API returned.
        for order in sorted(orders, key=lambda row: row.update_time_ms):
            if order.executed_qty == 0.0:  # R48: never marked seen
                continue
            key = f"{order.symbol}:{order.order_id}"  # R50
            if key in self._seen:  # R52b: at most one delivery per process
                continue
            # R47b: the handler (event + alert) runs before the key is marked seen,
            # so a handler error leaves the rest for the next cycle.
            await self._on_liquidation(order)
            self._seen[key] = order.update_time_ms
        self._cursors[symbol] = end_ms  # R47b: only after every row of the symbol

    def _start_ms(self, symbol: str, end_ms: int) -> int:
        """R46: the per-cycle request window."""
        return max(
            self._cursors[symbol] - self._overlap_ms,
            end_ms - self._lookback_cap_ms + _CLAMP_SLACK_MS,
        )

    async def _warn_if_clamped(self, symbol: str, start_ms: int) -> None:
        """R46: one warning per symbol per clamp episode."""
        clamped = start_ms > self._cursors[symbol] - self._overlap_ms
        if not clamped:
            self._clamped.discard(symbol)
            return
        if symbol in self._clamped:
            return
        self._clamped.add(symbol)
        await self._alert_warning(
            f"liquidation detector lookback clamped to "
            f"{self._lookback_cap_ms // _MS_PER_DAY} days: {symbol}",
        )

    async def _note_failure(self, symbol: str, exc: Exception) -> None:
        """R52: per-symbol backoff and the single degraded warning."""
        failures = self._failures[symbol] + 1
        self._failures[symbol] = failures
        backoff_s = min(self._interval_s * 2 ** (failures - 1), self._max_backoff_s)
        self._next_attempt_ms[symbol] = self._now_ms() + backoff_s * 1_000
        if failures == self._degraded_after:
            self._degraded.add(symbol)
            await self._alert_warning(
                f"liquidation detector degraded: {symbol} {type(exc).__name__}",
            )

    async def _note_success(self, symbol: str) -> None:
        """R52: healthy cadence again, plus the recovery alert if it was degraded."""
        failures = self._failures[symbol]
        self._failures[symbol] = 0
        self._next_attempt_ms[symbol] = self._now_ms() + self._interval_s * 1_000
        if symbol not in self._degraded:
            return
        self._degraded.discard(symbol)
        await self._alert_handled(self._on_recovered, symbol, failures)

    async def _alert_warning(self, message: str) -> None:
        """Chief ruling R52d(3): a failing alert never affects detection (R52)."""
        await self._alert_handled(self._on_warning, message)

    async def _alert_handled(self, handler: Callable[..., Awaitable[None]], *args: object) -> None:
        try:
            await handler(*args)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("liquidation detector alert handler failed")

    def _prune_seen(self) -> None:
        """R50: keep only keys the next request window can return again."""
        if not self._cursors:
            return
        floor_ms = min(self._cursors.values()) - self._overlap_ms
        self._seen = {key: ms for key, ms in self._seen.items() if ms >= floor_ms}

    def _time_to_next_attempt_s(self) -> float:
        if not self._next_attempt_ms:
            return float(self._interval_s)
        return max(min(self._next_attempt_ms.values()) - self._now_ms(), 0) / 1_000


__all__ = [
    "LiquidationHandler",
    "LiquidationWatcher",
    "RecoveredHandler",
    "WarningHandler",
]
