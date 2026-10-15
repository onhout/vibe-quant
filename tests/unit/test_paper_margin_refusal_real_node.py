"""R42 on the REAL NautilusTrader ``TradingNode`` (bead vibe-quant-q7hcn).

``test_paper_startup_and_estimate.py`` pins R42 with a fake node, so it only proves
our ASSUMPTION about NT. Here a real ``TradingNode`` (no network) runs a stub exec
client whose ``_connect`` fails the way ``BinanceFuturesExecutionClient._connect``
does when the venue refuses the margin-type change (``set_margin_type`` raises a
``BinanceClientError`` out of its TaskGroup).

What real NT does (probed): ``LiveExecutionClient.connect`` runs ``_connect`` in a task
and only LOGS its exception; ``start_async`` waits ``timeout_connection`` for the engines
to connect and RETURNS without ``trader.start()``, and ``run_async`` keeps idling (no raise). So the guarantee
is "no strategy starts, no order exists", while the failure is silent - that is why
R42b's startup-connect timeout alert exists.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from nautilus_trader.adapters.binance.http.error import BinanceClientError
from nautilus_trader.cache.cache import Cache  # noqa: TCH002
from nautilus_trader.common.component import (
    LiveClock,  # noqa: TCH002
    MessageBus,  # noqa: TCH002
)
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import LoggingConfig
from nautilus_trader.live.config import (
    LiveExecClientConfig,
    LiveExecEngineConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.live.factories import LiveExecClientFactory
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import AccountId, ClientId, Venue
from nautilus_trader.trading.strategy import Strategy

from vibe_quant.nt_compat import retain_log_guard

#: Short so the refused-connect case (where NT waits the whole timeout) stays fast.
_CONNECT_TIMEOUT_S = 1.0
_VENUE = Venue("BINANCE")


class _StubExecClient(LiveExecutionClient):
    """Exec client whose ``_connect`` raises like a refused margin-type change."""

    refuse = True

    def __init__(self, loop: Any, msgbus: MessageBus, cache: Cache, clock: LiveClock) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId("BINANCE"),
            venue=_VENUE,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=USDT,
            instrument_provider=InstrumentProvider(),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )
        self.connect_attempts = 0

    async def _connect(self) -> None:
        self.connect_attempts += 1
        if _StubExecClient.refuse:
            # Binance -4046-style 400: "No need to change margin type" is benign, but
            # any refused change (open position / orders) raises out of the TaskGroup.
            raise BinanceClientError(  # type: ignore[no-untyped-call]
                400, {"code": -4048, "msg": "Margin type cannot be changed"}, {}
            )
        # Healthy venue: register the account so the exec engine counts as connected.
        self._set_account_id(AccountId("BINANCE-001"))

    async def _disconnect(self) -> None:
        return


class _StubExecFactory(LiveExecClientFactory):
    created: list[_StubExecClient] = []

    @staticmethod
    def create(  # type: ignore[override]
        loop: Any,
        name: str,
        config: LiveExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> _StubExecClient:
        client = _StubExecClient(loop, msgbus, cache, clock)
        _StubExecFactory.created.append(client)
        return client


class _RecordingStrategy(Strategy):  # type: ignore[misc]
    started = 0

    def on_start(self) -> None:
        type(self).started += 1


def _build_node() -> TradingNode:
    config = TradingNodeConfig(
        trader_id="PAPER-007",
        timeout_connection=_CONNECT_TIMEOUT_S,
        timeout_disconnection=1.0,
        timeout_post_stop=1.0,
        timeout_shutdown=1.0,
        logging=LoggingConfig(log_level="ERROR"),
        exec_engine=LiveExecEngineConfig(reconciliation=False),
        exec_clients={"BINANCE": LiveExecClientConfig()},
    )
    node = TradingNode(config)
    # Same order as PaperTradingNode._build_node: strategies before build().
    node.trader.add_strategy(_RecordingStrategy())
    node.add_exec_client_factory("BINANCE", _StubExecFactory)
    node.build()
    return node


async def _run_node(node: TradingNode, *, until_started: bool) -> None:
    """Run like ``PaperTradingNode._run_loop``: ``run_async`` as a task on our loop.

    Ends like ``PaperTradingNode._shutdown``: ``stop_async`` and no ``dispose()``
    (it stops the running loop; node.py skips it for the same reason).
    """
    run_task = asyncio.create_task(node.run_async())
    try:
        if until_started:
            # Healthy venue: run_async never returns (engine queues); wait for the start.
            for _ in range(200):
                if _RecordingStrategy.started or run_task.done():
                    break
                await asyncio.sleep(0.05)
        else:
            # Refused connect: NT logs the error, gives up on the start after
            # timeout_connection and run_async keeps idling (no raise, no trader).
            await asyncio.sleep(_CONNECT_TIMEOUT_S + 1.5)
            assert not run_task.done(), "run_async idles; it neither raises nor returns"
    finally:
        await node.stop_async()
        if not run_task.done():
            run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        # Rust logger inits once per process: keep the guard alive (nautilus-gotchas).
        retain_log_guard(node)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset() -> None:
    _RecordingStrategy.started = 0
    _StubExecFactory.created = []
    _StubExecClient.refuse = True


async def test_refused_margin_change_never_starts_trader() -> None:
    """R42 (real NT): ``_connect`` raises -> ``trader.start()`` never runs."""
    node = _build_node()
    trader = node.trader
    await _run_node(node, until_started=False)
    client = _StubExecFactory.created[0]
    assert client.connect_attempts == 1, "the stub exec client must really be connected"
    assert _RecordingStrategy.started == 0, "a refused connect must not start any strategy"
    assert client.is_connected is False, "the refused connect must leave the client down"
    assert trader.is_running is False
    assert all(state != "RUNNING" for state in trader.strategy_states().values())


async def test_healthy_connect_does_start_trader() -> None:
    """Teeth: with the same stub NOT raising, the strategy DOES start."""
    _StubExecClient.refuse = False
    node = _build_node()
    await _run_node(node, until_started=True)
    assert _StubExecFactory.created[0].connect_attempts == 1
    assert _RecordingStrategy.started == 1, "the control run must reach trader.start()"
