"""Screening run summary exposes the screening venue's real starting balance.

Screening runs have no backtest_results row, so the summary is synthesized from
the best sweep row. The UI derives net PnL / fee drag / fees-per-year from
``starting_balance``; ``None`` left every screening ratio at n/a.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TCH003
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from vibe_quant.api.app import create_app
from vibe_quant.db.state_manager import StateManager
from vibe_quant.validation.venue import create_venue_config_for_screening

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture()
async def api(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StateManager]]:
    app = create_app()
    state = StateManager(db_path=tmp_path / "test.db")
    _ = state.conn
    app.state.state_manager = state
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, state
    state.close()


async def test_screening_summary_carries_screening_venue_balance(
    api: tuple[AsyncClient, StateManager],
) -> None:
    client, state = api
    run_id = state.create_backtest_run(
        strategy_id=None,
        run_mode="screening",
        symbols=["BTCUSDT"],
        timeframe="4h",
        start_date="2024-01-01",
        end_date="2024-12-31",
        # A requested initial_balance is never what the screening venue used.
        parameters={"initial_balance": 5000},
    )
    state.save_sweep_result(
        run_id,
        {
            "parameters": {"x": 1},
            "sharpe_ratio": 1.0,
            "total_return": 0.05,
            "total_trades": 10,
            "total_fees": 12.5,
        },
    )

    r = await client.get(f"/api/results/runs/{run_id}")

    assert r.status_code == 200
    body = r.json()
    venue_balance = float(create_venue_config_for_screening().starting_balance_usdt)
    assert body["starting_balance"] == venue_balance
    assert body["starting_balance"] != 5000
    # net PnL the UI derives from it is the screening venue's, not a guess
    assert body["total_return"] * body["starting_balance"] == pytest.approx(0.05 * venue_balance)
