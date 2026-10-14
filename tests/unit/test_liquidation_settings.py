"""The one parse rule for liquidation settings (bead vibe-quant-2gyk1.1.8).

Contract rows P1-P4, L1-L4: the mode is case-sensitive, ``None`` is NOT "off", and the
leverage is validated in EVERY mode (L4) as a whole number in [1, 125].
"""

from __future__ import annotations

import pytest

from vibe_quant.liquidation_settings import (
    DEFAULT_LEVERAGE,
    LIQUIDATION_MODES,
    MAX_LEVERAGE,
    MIN_LEVERAGE,
    MISSING,
    LiquidationConfigError,
    LiquidationSettings,
    resolve_leverage,
)


def test_null_mode_is_not_off() -> None:
    """P4: a null mode is invalid, never a silent "off"."""
    with pytest.raises(LiquidationConfigError, match="liquidation_mode must be one of"):
        LiquidationSettings(mode=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["Isolated", "", 1, True, "on", "both", "margin"])
def test_bad_mode_is_case_sensitive(mode: object) -> None:
    with pytest.raises(LiquidationConfigError) as err:
        LiquidationSettings(mode=mode)  # type: ignore[arg-type]
    assert str(err.value) == (
        f"liquidation_mode must be one of {LIQUIDATION_MODES}, got {mode!r}"
    )


def test_defaults_are_off_at_ten_x() -> None:
    settings = LiquidationSettings()
    assert (settings.mode, settings.leverage) == ("off", DEFAULT_LEVERAGE)
    assert settings.on is False
    assert settings.to_params() == {}
    assert settings.to_notes() is None


@pytest.mark.parametrize("mode", ["off", "isolated", "cross"])
def test_every_valid_mode_is_accepted(mode: str) -> None:
    assert LiquidationSettings(mode=mode).mode == mode  # type: ignore[arg-type]


def test_p1_params_without_the_mode_key_mean_off() -> None:
    settings = LiquidationSettings.from_params({})
    assert (settings.mode, settings.leverage, settings.on) == ("off", DEFAULT_LEVERAGE, False)
    assert settings.to_params() == {} and settings.to_notes() is None


def test_p2_explicit_off_behaves_like_p1() -> None:
    settings = LiquidationSettings.from_params({"liquidation_mode": "off"})
    assert settings.to_params() == {} and settings.to_notes() is None


@pytest.mark.parametrize("mode", ["isolated", "cross"])
def test_p3_on_modes_round_trip_through_params_and_notes(mode: str) -> None:
    settings = LiquidationSettings(mode=mode, leverage=20)  # type: ignore[arg-type]
    assert settings.on is True
    assert settings.leverage == 20.0 and isinstance(settings.leverage, float)
    assert settings.to_params() == {"liquidation_mode": mode, "leverage": 20.0}
    assert settings.to_notes() == {"mode": mode, "leverage": 20.0}


def test_l1_on_without_leverage_defaults_to_ten() -> None:
    settings = LiquidationSettings.from_params({"liquidation_mode": "isolated"})
    assert settings.leverage == DEFAULT_LEVERAGE


@pytest.mark.parametrize("leverage", [1, 20, 125, 125.0])
def test_l2_valid_leverage_is_stored_as_float(leverage: float) -> None:
    settings = LiquidationSettings(mode="isolated", leverage=leverage)
    assert settings.leverage == float(leverage)
    assert isinstance(settings.leverage, float)


@pytest.mark.parametrize(
    "leverage",
    [0, -1, 126, 0.999, 2.5, 125.0001, float("nan"), float("inf"), True, "20", None],
)
def test_l3_bad_leverage_is_rejected(leverage: object) -> None:
    with pytest.raises(LiquidationConfigError) as err:
        LiquidationSettings(mode="isolated", leverage=leverage)  # type: ignore[arg-type]
    assert str(err.value) == f"leverage must be a whole number in [1, 125], got {leverage!r}"


@pytest.mark.parametrize("mode", ["off", "isolated", "cross"])
@pytest.mark.parametrize(
    "leverage",
    [10**400, -(10**400), 1e308, -1e308, float("inf"), float("-inf"), float("nan")],
    ids=["huge-int", "huge-neg-int", "1e308", "-1e308", "inf", "-inf", "nan"],
)
def test_l3b_out_of_float_range_leverage_is_a_config_error_in_every_mode(
    mode: str, leverage: float
) -> None:
    """``float(10**400)`` overflows: the rule must still raise LiquidationConfigError, never
    an OverflowError (the API would answer 500, validation would crash instead of failing)."""
    with pytest.raises(LiquidationConfigError, match=r"\[1, 125\]"):
        LiquidationSettings(mode=mode, leverage=leverage)  # type: ignore[arg-type]
    with pytest.raises(LiquidationConfigError, match=r"\[1, 125\]"):
        LiquidationSettings.from_params({"liquidation_mode": mode, "leverage": leverage})


def test_l4_off_validates_leverage_too() -> None:
    """L4: the domain is checked in EVERY mode, not only while liquidation is on."""
    with pytest.raises(LiquidationConfigError, match=r"\[1, 125\]"):
        LiquidationSettings(mode="off", leverage=0)
    assert LiquidationSettings(mode="off", leverage=3).leverage == 3.0


def test_l4_explicit_none_leverage_is_rejected() -> None:
    with pytest.raises(LiquidationConfigError, match=r"\[1, 125\]"):
        LiquidationSettings(leverage=None)  # type: ignore[arg-type]
    with pytest.raises(LiquidationConfigError, match=r"\[1, 125\]"):
        LiquidationSettings.from_params({"leverage": None})


def test_to_params_off_non_default_leverage() -> None:
    """Off at a non-default leverage stores just the value; off at 10 stays empty."""
    assert LiquidationSettings(mode="off", leverage=3).to_params() == {"leverage": 3.0}
    assert LiquidationSettings(mode="off", leverage=DEFAULT_LEVERAGE).to_params() == {}
    assert LiquidationSettings(mode="isolated", leverage=3).to_params() == {
        "liquidation_mode": "isolated",
        "leverage": 3.0,
    }


def test_from_params_reads_both_keys() -> None:
    settings = LiquidationSettings.from_params({"liquidation_mode": "cross", "leverage": 50})
    assert settings.mode == "cross" and settings.leverage == 50.0


def test_shared_constants() -> None:
    assert MAX_LEVERAGE == 125.0
    assert MIN_LEVERAGE == 1.0


# --------------------------------------------------------------- resolve_leverage (H1)


def test_resolve_leverage_picks_the_first_supplied() -> None:
    assert resolve_leverage(MISSING, MISSING) is MISSING
    assert resolve_leverage(3, MISSING) == 3
    assert resolve_leverage(MISSING, 20) == 20
    assert resolve_leverage(3, 3.0) == 3  # 3 == 3.0; the first value wins unchanged


def test_resolve_leverage_conflict_raises() -> None:
    with pytest.raises(LiquidationConfigError) as err:
        resolve_leverage(3, 20)
    assert str(err.value) == "conflicting leverage 3 and liquidation_leverage 20"
