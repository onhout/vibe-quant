"""vibe-quant-e70tl.23: frontend DslConfig -> StrategyDSL translator must not
silently drop exit conditions, indicator timeframe overrides, OR logic or a
percent trailing stop."""

from __future__ import annotations

from typing import Any

import pytest

from vibe_quant.dsl.compiler import StrategyCompiler
from vibe_quant.dsl.parser import validate_strategy_dict
from vibe_quant.dsl.translator import translate_dsl_config


def _cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "general": {"strategy_type": "momentum", "symbols": ["BTCUSDT"], "timeframe": "1h"},
        "indicators": [{"type": "RSI", "params": {"period": 14}}],
        "conditions": {
            "entry": [{"left": "RSI(14)", "operator": "<", "right": "30"}],
            "exit": [{"left": "RSI(14)", "operator": ">", "right": "70", "logic": None}],
        },
        "risk": {
            "stop_loss": {"type": "fixed_pct", "value": 2},
            "take_profit": {"type": "fixed_pct", "value": 4},
            "position_sizing": {"type": "percent_equity", "value": 10},
        },
        "time": {},
    }
    cfg.update(overrides)
    return cfg


def test_generic_exit_kept() -> None:
    out = translate_dsl_config(_cfg(), strategy_name="t_exit")
    assert out["entry_conditions"]["long"] == ["rsi_14 < 30"]
    assert out["exit_conditions"] == {"long": ["rsi_14 > 70"], "short": []}
    validate_strategy_dict(out)


def test_directional_exits_kept() -> None:
    conds = {
        "long_entry": [{"left": "RSI(14)", "operator": "<", "right": "30"}],
        "short_entry": [{"left": "RSI(14)", "operator": ">", "right": "70"}],
        "long_exit": [{"left": "RSI(14)", "operator": ">", "right": "55"}],
        "short_exit": [{"left": "RSI(14)", "operator": "<", "right": "45"}],
    }
    out = translate_dsl_config(_cfg(conditions=conds), strategy_name="t_dir")
    assert out["exit_conditions"] == {"long": ["rsi_14 > 55"], "short": ["rsi_14 < 45"]}
    validate_strategy_dict(out)


def test_or_logic_rejected() -> None:
    conds = {
        "entry": [
            {"left": "RSI(14)", "operator": "<", "right": "30"},
            {"left": "RSI(14)", "operator": ">", "right": "80", "logic": "or"},
        ]
    }
    with pytest.raises(ValueError, match="'or' logic"):
        translate_dsl_config(_cfg(conditions=conds), strategy_name="t_or")


def test_and_logic_accepted() -> None:
    conds = {
        "entry": [
            {"left": "RSI(14)", "operator": "<", "right": "30"},
            {"left": "RSI(14)", "operator": ">", "right": "10", "logic": "and"},
        ]
    }
    out = translate_dsl_config(_cfg(conditions=conds), strategy_name="t_and")
    assert out["entry_conditions"]["long"] == ["rsi_14 < 30", "rsi_14 > 10"]


def test_timeframe_override_kept_and_declared() -> None:
    inds = [
        {"type": "RSI", "params": {"period": 14}},
        {"type": "ADX", "params": {"period": 14}, "timeframe_override": "4h"},
    ]
    out = translate_dsl_config(_cfg(indicators=inds), strategy_name="t_tf")
    assert out["indicators"]["adx_14"]["timeframe"] == "4h"
    assert out["additional_timeframes"] == ["4h"]
    dsl = validate_strategy_dict(out)
    assert dsl.indicators["adx_14"].timeframe == "4h"


def test_trailing_stop_pct_rejected() -> None:
    risk = _cfg()["risk"] | {"trailing_stop_pct": 1.5}
    with pytest.raises(ValueError, match="trailing_stop_pct"):
        translate_dsl_config(_cfg(risk=risk), strategy_name="t_trail")


def test_stoch_k_d_periods_mapped() -> None:
    inds = [{"type": "STOCH", "params": {"k_period": 9, "d_period": 5}}]
    conds = {"entry": [{"left": "STOCH(9,5)", "operator": "<", "right": "20"}]}
    out = translate_dsl_config(_cfg(indicators=inds, conditions=conds), strategy_name="t_stoch")
    cfg = next(iter(out["indicators"].values()))
    assert (cfg["period"], cfg["d_period"]) == (9, 5)


# --- ATR stop / take-profit need an `indicator` reference (vibe-quant-yj57u) ---------------


@pytest.mark.parametrize("sl_type", ["atr_fixed", "atr_trailing"])
def test_atr_stop_reuses_ui_atr_indicator(sl_type: str) -> None:
    inds = [
        {"type": "RSI", "params": {"period": 14}},
        {"type": "ATR", "params": {"period": 20}},
    ]
    risk = _cfg()["risk"] | {"stop_loss": {"type": sl_type, "value": 2.5}}
    out = translate_dsl_config(_cfg(indicators=inds, risk=risk), strategy_name="t_atr_reuse")
    assert out["stop_loss"] == {"type": sl_type, "atr_multiplier": 2.5, "indicator": "atr_20"}
    assert list(out["indicators"]) == ["rsi_14", "atr_20"]  # nothing added
    dsl = validate_strategy_dict(out)
    assert dsl.stop_loss.indicator == "atr_20"
    StrategyCompiler().compile_to_module(dsl)


@pytest.mark.parametrize("sl_type", ["atr_fixed", "atr_trailing"])
def test_atr_stop_without_ui_atr_adds_default_indicator(sl_type: str) -> None:
    risk = _cfg()["risk"] | {"stop_loss": {"type": sl_type, "value": 1.5}}
    out = translate_dsl_config(_cfg(risk=risk), strategy_name="t_atr_add")
    assert out["indicators"]["atr_14"] == {"type": "ATR", "period": 14}
    assert out["stop_loss"] == {"type": sl_type, "atr_multiplier": 1.5, "indicator": "atr_14"}
    dsl = validate_strategy_dict(out)
    assert dsl.indicators["atr_14"].type == "ATR"
    StrategyCompiler().compile_to_module(dsl)


def test_atr_take_profit_gets_indicator_and_shares_it_with_stop() -> None:
    risk = _cfg()["risk"] | {
        "stop_loss": {"type": "atr_trailing", "value": 2},
        "take_profit": {"type": "atr_fixed", "value": 3},
    }
    out = translate_dsl_config(_cfg(risk=risk), strategy_name="t_atr_tp")
    assert [n for n, c in out["indicators"].items() if c["type"] == "ATR"] == ["atr_14"]
    assert out["take_profit"]["indicator"] == "atr_14"
    StrategyCompiler().compile_to_module(validate_strategy_dict(out))


def test_fixed_pct_stop_adds_no_atr_indicator() -> None:
    out = translate_dsl_config(_cfg(), strategy_name="t_nofix")
    assert list(out["indicators"]) == ["rsi_14"]
    assert "indicator" not in out["stop_loss"]


def test_two_atr_indicators_stop_and_take_profit_bind_to_the_first() -> None:
    # ATR(20) on 1d comes first, ATR(7) second: SL and TP must both use atr_20, not the last ATR
    inds = [
        {"type": "RSI", "params": {"period": 14}},
        {"type": "ATR", "params": {"period": 20}, "timeframe_override": "1d"},
        {"type": "ATR", "params": {"period": 7}},
    ]
    risk = _cfg()["risk"] | {
        "stop_loss": {"type": "atr_trailing", "value": 2},
        "take_profit": {"type": "atr_fixed", "value": 3},
    }
    out = translate_dsl_config(
        _cfg(indicators=inds, risk=risk, general={"timeframe": "4h", "symbols": ["BTCUSDT"]}),
        strategy_name="t_two_atr",
    )
    assert out["stop_loss"]["indicator"] == "atr_20"
    assert out["take_profit"]["indicator"] == "atr_20"
    assert out["indicators"]["atr_20"] == {"type": "ATR", "period": 20, "timeframe": "1d"}
    assert out["indicators"]["atr_7"] == {"type": "ATR", "period": 7}
    assert list(out["indicators"]) == ["rsi_14", "atr_20", "atr_7"]  # nothing added
    assert out["additional_timeframes"] == ["1d"]
    dsl = validate_strategy_dict(out)
    assert dsl.stop_loss.indicator == dsl.take_profit.indicator == "atr_20"
