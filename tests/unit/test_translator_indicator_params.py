"""Frontend DslConfig -> StrategyDSL translator keeps every indicator param the user edited.

Params the registry spec declares (ICHIMOKU tenkan/kijun/senkou, ADAPTIVE_RSI alpha,
SQUEEZE length, ...) used to be dropped, so the backtest ran spec defaults instead of the
edited strategy. Params nobody knows must fail loudly instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from vibe_quant.dsl.compiler import StrategyCompiler
from vibe_quant.dsl.indicators import IndicatorSpec, indicator_registry
from vibe_quant.dsl.parser import validate_strategy_dict
from vibe_quant.dsl.plugins.example_adaptive_rsi import compute_adaptive_rsi
from vibe_quant.dsl.translator import translate_dsl_config

if TYPE_CHECKING:
    from collections.abc import Iterator


def _cfg(indicators: list[dict[str, Any]], entry: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "general": {"strategy_type": "momentum", "symbols": ["BTCUSDT"], "timeframe": "1h"},
        "indicators": indicators,
        "conditions": {
            "entry": entry or [{"left": "price", "operator": ">", "right": "100"}],
            "exit": [],
        },
        "risk": {
            "stop_loss": {"type": "fixed_pct", "value": 2},
            "take_profit": {"type": "fixed_pct", "value": 4},
        },
        "time": {},
    }


def test_ichimoku_params_reach_canonical_config_and_compiled_strategy() -> None:
    cfg = _cfg([{"type": "ICHIMOKU", "params": {"tenkan": 7, "kijun": 30, "senkou": 60}}])
    out = translate_dsl_config(cfg, strategy_name="t_ichi")
    assert out["indicators"]["ichimoku"] == {
        "type": "ICHIMOKU",
        "tenkan": 7,
        "kijun": 30,
        "senkou": 60,
    }
    dsl = validate_strategy_dict(out)
    assert dsl.indicators["ichimoku"].model_extra == {"tenkan": 7, "kijun": 30, "senkou": 60}
    code = StrategyCompiler().compile(dsl)
    assert "ichimoku_tenkan: int = 7" in code
    assert "ichimoku_kijun: int = 30" in code
    assert "ichimoku_senkou: int = 60" in code


def test_plugin_alpha_reaches_canonical_config_typed_as_float() -> None:
    # The editor sends numbers; an integral alpha still has to satisfy the float param_schema
    cfg = _cfg([{"type": "ADAPTIVE_RSI", "params": {"period": 21, "alpha": 0.8}}])
    out = translate_dsl_config(cfg, strategy_name="t_arsi")
    assert out["indicators"]["adaptive_rsi_21"] == {
        "type": "ADAPTIVE_RSI",
        "period": 21,
        "alpha": 0.8,
    }
    dsl = validate_strategy_dict(out)
    assert dsl.indicators["adaptive_rsi_21"].model_extra == {"alpha": 0.8}
    assert "adaptive_rsi_21_alpha: float = 0.8" in StrategyCompiler().compile(dsl)

    whole = translate_dsl_config(
        _cfg([{"type": "ADAPTIVE_RSI", "params": {"period": 21, "alpha": 1}}]), "t_arsi_int"
    )
    alpha = whole["indicators"]["adaptive_rsi_21"]["alpha"]
    assert alpha == 1.0
    assert isinstance(alpha, float)


def test_squeeze_length_and_multipliers_are_kept() -> None:
    cfg = _cfg(
        [{"type": "SQUEEZE_RATIO", "params": {"length": 30, "bb_mult": 2.5, "kc_mult": 1.2}}]
    )
    out = translate_dsl_config(cfg, strategy_name="t_squeeze")
    (name,) = out["indicators"]
    assert out["indicators"][name] == {
        "type": "SQUEEZE_RATIO",
        "length": 30,
        "bb_mult": 2.5,
        "kc_mult": 1.2,
    }
    assert validate_strategy_dict(out).indicators[name].model_extra == {
        "length": 30,
        "bb_mult": 2.5,
        "kc_mult": 1.2,
    }


def test_refs_to_indicators_with_extra_params_still_resolve() -> None:
    cfg = _cfg(
        [{"type": "ADAPTIVE_RSI", "params": {"period": 21, "alpha": 0.8}}],
        entry=[{"left": "ADAPTIVE_RSI(21,0.8)", "operator": "<", "right": "30"}],
    )
    out = translate_dsl_config(cfg, strategy_name="t_refs")
    assert out["entry_conditions"]["long"] == ["adaptive_rsi_21 < 30"]


@pytest.mark.parametrize(
    ("ind_type", "params", "bad"),
    [
        ("RSI", {"period": 14, "bogus": 3}, "bogus"),
        ("ICHIMOKU", {"tenkan": 9, "kijun": 26, "senkou_b": 52}, "senkou_b"),
        ("ADAPTIVE_RSI", {"period": 14, "alphaa": 0.5}, "alphaa"),
    ],
)
def test_unknown_indicator_param_raises_naming_indicator_and_param(
    ind_type: str, params: dict[str, Any], bad: str
) -> None:
    with pytest.raises(ValueError, match=rf"(?s)(?=.*{ind_type})(?=.*'{bad}')"):
        translate_dsl_config(_cfg([{"type": ind_type, "params": params}]), strategy_name="t_bad")


def test_fractional_value_for_integer_param_raises() -> None:
    # int(9.5) would silently run tenkan=9 while the user configured 9.5
    cfg = _cfg([{"type": "ICHIMOKU", "params": {"tenkan": 9.5, "kijun": 26, "senkou": 52}}])
    with pytest.raises(ValueError, match=r"ICHIMOKU.*'tenkan'"):
        translate_dsl_config(cfg, strategy_name="t_frac")


def test_integral_float_for_integer_param_is_accepted() -> None:
    # JSON round-trips can turn 9 into 9.0
    cfg = _cfg([{"type": "ICHIMOKU", "params": {"tenkan": 9.0, "kijun": 26, "senkou": 52}}])
    out = translate_dsl_config(cfg, strategy_name="t_integral")
    tenkan = out["indicators"]["ichimoku"]["tenkan"]
    assert tenkan == 9
    assert isinstance(tenkan, int)


_PLUGIN_NAME = "TEST_NATIVE_ALIAS"
_PLUGIN_STR_NAME = "TEST_NATIVE_ALIAS_STR"


@pytest.fixture
def alias_plugin() -> Iterator[IndicatorSpec]:
    """Plugin whose params (``fast``/``signal``/``mode``) collide with legacy editor spellings."""
    spec = IndicatorSpec(
        name=_PLUGIN_NAME,
        nt_class=None,
        pandas_ta_func=None,
        default_params={"fast": 10, "signal": False, "mode": "sma"},
        param_schema={"fast": int, "signal": bool, "mode": str},
        compute_fn=compute_adaptive_rsi,
        display_name="Native Alias Plugin",
        description="Synthetic plugin — do not ship.",
        category="Custom",
    )
    indicator_registry.register_spec(spec)
    try:
        yield spec
    finally:
        indicator_registry.unregister(_PLUGIN_NAME)


@pytest.fixture
def alias_str_plugin() -> Iterator[IndicatorSpec]:
    """Plugin whose ``fast`` param is a string (not a numeric period)."""
    spec = IndicatorSpec(
        name=_PLUGIN_STR_NAME,
        nt_class=None,
        pandas_ta_func=None,
        default_params={"fast": "sma"},
        param_schema={"fast": str},
        compute_fn=compute_adaptive_rsi,
        display_name="Native Alias String Plugin",
        description="Synthetic plugin — do not ship.",
        category="Custom",
    )
    indicator_registry.register_spec(spec)
    try:
        yield spec
    finally:
        indicator_registry.unregister(_PLUGIN_STR_NAME)


@pytest.mark.parametrize(
    "params",
    [
        {"fast": 18},
        {"signal": True},
        {"fast": 18, "signal": True, "mode": "ema"},
    ],
    ids=["int", "bool", "all"],
)
def test_plugin_native_params_win_over_legacy_synonyms(
    alias_plugin: IndicatorSpec, params: dict[str, Any]
) -> None:
    """A registry-native ``fast``/``signal`` is the plugin's own param, not an editor alias.

    Folding them onto ``fast_period``/``signal_period`` (or coercing them numerically)
    silently ran the plugin default plus an unrelated canonical field.
    """
    name = _PLUGIN_NAME.lower()
    expected = {"type": _PLUGIN_NAME, **params}
    out = translate_dsl_config(_cfg([{"type": _PLUGIN_NAME, "params": params}]), "t_native")
    assert out["indicators"] == {name: expected}
    dsl = validate_strategy_dict(out)
    assert dsl.indicators[name].model_extra == params
    # The canonical form translates to the same values the UI form produced.
    canonical = {**out, "indicators": {name: expected}}
    assert translate_dsl_config(canonical, strategy_name="t_native")["indicators"] == {
        name: expected
    }


def test_plugin_native_string_param_survives_ui_translation(
    alias_str_plugin: IndicatorSpec,
) -> None:
    """A string alias is passed through, not coerced to a number (which used to raise)."""
    name = _PLUGIN_STR_NAME.lower()
    expected = {"type": _PLUGIN_STR_NAME, "fast": "ema"}
    out = translate_dsl_config(
        _cfg([{"type": _PLUGIN_STR_NAME, "params": {"fast": "ema"}}]), "t_native_str"
    )
    assert out["indicators"] == {name: expected}
    assert validate_strategy_dict(out).indicators[name].model_extra == {"fast": "ema"}
