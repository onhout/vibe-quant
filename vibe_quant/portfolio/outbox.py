"""Per-instrument command outbox for the funding-carry book (design §1.3).

The decision callback never submits an order: it queues it at the decision
instant ``D``, and each instrument's OWN next trade tick (``ts_init > queue_ts``,
strictly) submits it. An NT venue-wide ``LatencyModel`` would release every
queued order on any symbol's next datum; the book must instead be sized at the
decision and filled at the deciding symbol's own fill tick, so the queue is
keyed by symbol. Same semantics as ``dsl/templates.py``
``_send_command``/``_flush_outbox``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_trader.model.orders import MarketOrder


class InstrumentOutbox:
    """Queued market orders per symbol, in FIFO order, released strictly after the queue ts."""

    def __init__(self) -> None:
        self._queued: dict[str, list[tuple[int, MarketOrder]]] = {}

    def queue(self, symbol: str, ts: int, order: MarketOrder) -> None:
        """Queue ``order`` for release by ``symbol``'s own first tick after ``ts``."""
        self._queued.setdefault(symbol, []).append((ts, order))

    def release(self, symbol: str, ts: int) -> list[tuple[int, MarketOrder]]:
        """Remove and return ``symbol``'s orders queued strictly before ``ts``.

        A datum stamped at the queue ts itself never releases, and another
        symbol's tick never releases this symbol's orders.
        """
        queued = self._queued.get(symbol)
        if not queued:
            return []
        ready = [item for item in queued if item[0] < ts]
        if ready:
            self._queued[symbol] = [item for item in queued if item[0] >= ts]
        return ready

    def pending(self) -> int:
        """The number of orders still queued across every symbol."""
        return sum(len(queued) for queued in self._queued.values())
