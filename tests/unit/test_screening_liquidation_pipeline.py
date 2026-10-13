"""Screening pipeline + CLI + sweep columns for opt-in liquidation (B1).

Contract rows: E10, D1, I1, I2, I7, I8, X1.

The one parse rule is :class:`LiquidationSettings` (bead vibe-quant-2gyk1.1.8):
``create_screening_pipeline`` derives it from the run's parameters (the single
source, no screening CLI flag) and passes it to ``NTScreeningRunner`` and to
``ScreeningPipeline``. A mode-on integrity failure
(:class:`~vibe_quant.errors.LiquidationIntegrityError`, a
``DataUnavailableError``) is re-raised by the sweep and fails the run; an
ordinary strategy error with the mode on still yields a -999 row and a
completed run (I7).
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

import pytest

from vibe_quant.db.state_manager import StateManager
from vibe_quant.dsl import parse_strategy_string
from vibe_quant.errors import LiquidationIntegrityError
from vibe_quant.liquidation_settings import LiquidationConfigError, LiquidationSettings
from vibe_quant.screening.pipeline import ScreeningPipeline, create_screening_pipeline
from vibe_quant.screening.types import BacktestMetrics, ScreeningResult

if TYPE_CHECKING:
    from pathlib import Path

_STRATEGY: dict[str, object] = {
    "name": "liq_probe",
    "timeframe": "4h",
    "indicators": {"rsi": {"type": "RSI", "period": 14}},
    "entry_conditions": {"long": ["rsi < 45"]},
    "exit_conditions": {"long": ["rsi > 55"]},
    "stop_loss": {"type": "fixed_pct", "percent": 2.0},
    "take_profit": {"type": "fixed_pct", "percent": 3.0},
}

_MINIMAL_YAML = """
name: liq_probe_min
timeframe: 5m
indicators:
  rsi:
    type: RSI
    period: 14
entry_conditions:
  long:
    - rsi < 30
stop_loss:
  type: fixed_pct
  percent: 2.0
take_profit:
  type: fixed_pct
  percent: 3.0
"""


def _dsl() -> Any:
    return parse_strategy_string(_MINIMAL_YAML)


# ---------------------------------------------------------------------------
# Picklable fake runners: they stand in for NTScreeningRunner in the pool.
# ---------------------------------------------------------------------------


class _MarkMissingRunner:
    """Raises the mode-on integrity error the runner wraps a MarkDataMissing in."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __call__(self, params: dict[str, float | int]) -> BacktestMetrics:
        from vibe_quant.data.mark_bars import MarkDataMissing

        raise LiquidationIntegrityError.wrap(
            MarkDataMissing("no MARK bars for BTCUSDT-PERP"), "isolated"
        )


class _PlainBoomRunner:
    """An ordinary strategy failure OUTSIDE the liquidation path (I7/I8)."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __call__(self, params: dict[str, float | int]) -> BacktestMetrics:
        raise ValueError("ordinary strategy failure")


def _install_fake_runner(
    monkeypatch: pytest.MonkeyPatch, runner_cls: type, workers: int
) -> None:
    """Route the real factory onto ``runner_cls`` and force ``workers``."""
    from vibe_quant.screening import pipeline as pipeline_mod

    real_factory = pipeline_mod.create_screening_pipeline
    monkeypatch.setattr(pipeline_mod, "resolve_parent_fill_ticks", lambda *a, **k: None)
    monkeypatch.setattr(
        "vibe_quant.screening.nt_runner.NTScreeningRunner", runner_cls
    )

    def fake_factory(dsl: Any, **kwargs: Any) -> ScreeningPipeline:
        kwargs["max_workers"] = workers
        return real_factory(dsl, **kwargs)

    monkeypatch.setattr(pipeline_mod, "create_screening_pipeline", fake_factory)


def _make_run(db: Path, parameters: dict[str, object]) -> int:
    state = StateManager(db)
    try:
        strategy_id = state.create_strategy(name="liq_probe", dsl_config=_STRATEGY)
        return state.create_backtest_run(
            strategy_id=strategy_id,
            run_mode="screening",
            symbols=["BTCUSDT"],
            timeframe="4h",
            start_date="2024-01-01",
            end_date="2024-03-01",
            parameters=parameters,
        )
    finally:
        state.close()


def _final_run(db: Path, run_id: int) -> dict[str, Any]:
    state = StateManager(db)
    try:
        run = state.get_backtest_run(run_id)
        rows = state.get_sweep_results(run_id)
    finally:
        state.close()
    assert run is not None
    return {"run": run, "rows": rows}


def _run_cli(db: Path, run_id: int) -> int:
    from vibe_quant.screening.__main__ import cmd_run

    return cmd_run(argparse.Namespace(run_id=run_id, db=str(db)))


# ---------------------------------------------------------------------------
# I1 / I2: a mode-on integrity failure fails the run (sequential and pooled)
# ---------------------------------------------------------------------------


def test_mode_on_mark_missing_fails_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I1: mode on, MarkDataMissing -> failed run, prefix message, 0 sweep rows, exit 1."""
    db = tmp_path / "i1.db"
    run_id = _make_run(db, {"liquidation_mode": "isolated"})
    _install_fake_runner(monkeypatch, _MarkMissingRunner, workers=1)

    assert _run_cli(db, run_id) == 1

    final = _final_run(db, run_id)
    assert final["run"]["status"] == "failed"
    assert final["run"]["error_message"].startswith(
        "LiquidationIntegrityError: liquidation_mode='isolated': MarkDataMissing: "
    )
    assert final["rows"] == []


def test_mode_on_mark_missing_fails_run_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2: same as I1 but the integrity error travels through the worker pool."""
    db = tmp_path / "i2.db"
    run_id = _make_run(db, {"liquidation_mode": "isolated"})
    _install_fake_runner(monkeypatch, _MarkMissingRunner, workers=2)

    assert _run_cli(db, run_id) == 1

    final = _final_run(db, run_id)
    assert final["run"]["status"] == "failed"
    assert final["run"]["error_message"].startswith(
        "LiquidationIntegrityError: liquidation_mode='isolated': MarkDataMissing: "
    )
    assert final["rows"] == []


def test_pipeline_sequential_reraises_integrity_error() -> None:
    """I1 (direct): the sweep re-raises the integrity error instead of a -999 row."""
    pipeline = ScreeningPipeline(
        dsl=_dsl(),
        backtest_runner=_MarkMissingRunner(),
        max_workers=1,
        liquidation=LiquidationSettings(mode="isolated", leverage=50.0),
    )
    with pytest.raises(LiquidationIntegrityError, match="MarkDataMissing"):
        pipeline.run(apply_dsr=False)


# ---------------------------------------------------------------------------
# E10: an invalid stored mode fails the run before any engine exists
# ---------------------------------------------------------------------------


def test_invalid_mode_fails_run_with_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E10: DB params mode 'x' -> failed run, config-error message, 0 rows, exit 1."""
    db = tmp_path / "e10.db"
    run_id = _make_run(db, {"liquidation_mode": "x"})

    assert _run_cli(db, run_id) == 1

    final = _final_run(db, run_id)
    assert final["run"]["status"] == "failed"
    assert final["run"]["error_message"].startswith(
        "LiquidationConfigError: liquidation_mode must be one of "
    )
    assert final["rows"] == []


@pytest.mark.parametrize("mode", ["x", "Isolated", ""])
def test_factory_rejects_invalid_mode_without_an_engine(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E10 (factory): the parse runs before the mock/real split, so it always raises."""
    from vibe_quant.screening import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "resolve_parent_fill_ticks", lambda *a, **k: None)
    with pytest.raises(LiquidationConfigError, match="liquidation_mode must be one of"):
        create_screening_pipeline(
            _dsl(), use_mock=True, parameters={"liquidation_mode": mode}
        )


# ---------------------------------------------------------------------------
# I7 / I8: ordinary strategy errors keep the -999 sentinel
# ---------------------------------------------------------------------------


def test_mode_on_ordinary_error_is_999_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I7: mode on, an error outside the liquidation path -> -999 row, completed run."""
    db = tmp_path / "i7.db"
    run_id = _make_run(db, {"liquidation_mode": "isolated"})
    _install_fake_runner(monkeypatch, _PlainBoomRunner, workers=1)

    assert _run_cli(db, run_id) == 0

    final = _final_run(db, run_id)
    assert final["run"]["status"] == "completed"
    assert len(final["rows"]) == 1
    assert final["rows"][0]["sharpe_ratio"] == -999.0


def test_mode_off_ordinary_error_is_999_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I8: mode off, the same error is byte-identical to today (-999, completed)."""
    db = tmp_path / "i8.db"
    run_id = _make_run(db, {})
    _install_fake_runner(monkeypatch, _PlainBoomRunner, workers=1)

    assert _run_cli(db, run_id) == 0

    final = _final_run(db, run_id)
    assert final["run"]["status"] == "completed"
    assert final["rows"][0]["sharpe_ratio"] == -999.0


# ---------------------------------------------------------------------------
# D1: sweep columns are written only when the mode is on
# ---------------------------------------------------------------------------


def test_save_results_writes_liquidation_only_when_on(tmp_path: Path) -> None:
    """D1: on -> int count (0 allowed) + float forfeited; off -> both NULL."""
    db = tmp_path / "d1.db"
    state = StateManager(db)
    try:
        strategy_id = state.create_strategy(name="liq_probe", dsl_config=_STRATEGY)

        def new_run(parameters: dict[str, object]) -> int:
            return state.create_backtest_run(
                strategy_id=strategy_id,
                run_mode="screening",
                symbols=["BTCUSDT"],
                timeframe="4h",
                start_date="2024-01-01",
                end_date="2024-03-01",
                parameters=parameters,
            )

        hit_run = new_run({"liquidation_mode": "isolated", "leverage": 50.0})
        miss_run = new_run({"liquidation_mode": "cross", "leverage": 20.0})
        off_run = new_run({})

        on = ScreeningPipeline(
            dsl=_dsl(), liquidation=LiquidationSettings(mode="isolated", leverage=50.0)
        )
        on.save_results(
            ScreeningResult(
                "liq_probe", 1, 1, 0.1,
                [BacktestMetrics(parameters={"a": 1}, sharpe_ratio=1.0,
                                 liquidation_count=3, liquidation_forfeited=12.5)],
                [0],
            ),
            state,
            hit_run,
        )
        on.save_results(
            ScreeningResult(
                "liq_probe", 1, 1, 0.1,
                [BacktestMetrics(parameters={"a": 1}, sharpe_ratio=1.0)],
                [0],
            ),
            state,
            miss_run,
        )
        ScreeningPipeline(dsl=_dsl()).save_results(
            ScreeningResult(
                "liq_probe", 1, 1, 0.1,
                [BacktestMetrics(parameters={"a": 1}, sharpe_ratio=1.0,
                                 liquidation_count=3, liquidation_forfeited=12.5)],
                [0],
            ),
            state,
            off_run,
        )

        def cols(run_id: int) -> tuple[Any, Any]:
            row = state.conn.execute(
                "SELECT liquidation_count, liquidation_forfeited "
                "FROM sweep_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return (row[0], row[1])

        assert cols(hit_run) == (3, 12.5)
        assert cols(miss_run) == (0, 0.0)
        assert cols(off_run) == (None, None)
    finally:
        state.close()


# ---------------------------------------------------------------------------
# Plumbing: the factory derives settings once and hands them to both sinks
# ---------------------------------------------------------------------------


def test_factory_passes_liquidation_to_runner_and_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe_quant.screening import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "resolve_parent_fill_ticks", lambda *a, **k: None)
    on = create_screening_pipeline(
        _dsl(),
        symbols=["BTCUSDT"],
        parameters={"liquidation_mode": "isolated", "leverage": 20.0},
    )
    assert on._runner._liquidation_mode == "isolated"
    assert on._runner._liquidation_leverage == 20.0
    assert on._liquidation.on
    assert on._liquidation.leverage == 20.0

    off = create_screening_pipeline(_dsl(), symbols=["BTCUSDT"])
    assert off._runner._liquidation_mode == "off"
    assert not off._liquidation.on
    assert off._liquidation.to_params() == {}
