"""The one parse rule for liquidation settings (bead vibe-quant-2gyk1.1.8).

Contract rows P1-P4, L1-L4: the mode is case-sensitive, ``None`` is NOT "off", and the
leverage is validated ((0, 125]) only while the mode is on.
"""

from __future__ import annotations

import pytest

from vibe_quant.liquidation_settings import (
    DEFAULT_LEVERAGE,
    LIQUIDATION_MODES,
    MAX_LEVERAGE,
    LiquidationConfigError,
    LiquidationSettings,
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


@pytest.mark.parametrize("leverage", [0.5, 1, 20, 125, 125.0])
def test_l2_valid_leverage_is_stored_as_float(leverage: float) -> None:
    settings = LiquidationSettings(mode="isolated", leverage=leverage)
    assert settings.leverage == float(leverage)
    assert isinstance(settings.leverage, float)


@pytest.mark.parametrize(
    "leverage", [0, -1, 125.0001, float("nan"), float("inf"), True, "20", None]
)
def test_l3_bad_leverage_is_rejected(leverage: object) -> None:
    with pytest.raises(LiquidationConfigError) as err:
        LiquidationSettings(mode="isolated", leverage=leverage)  # type: ignore[arg-type]
    assert str(err.value) == f"leverage must be a number in (0, 125], got {leverage!r}"


def test_l4_off_never_validates_leverage() -> None:
    assert LiquidationSettings(mode="off", leverage=0).leverage == 0.0
    assert LiquidationSettings(mode="off", leverage=float("nan")).to_params() == {}


def test_from_params_reads_both_keys() -> None:
    settings = LiquidationSettings.from_params({"liquidation_mode": "cross", "leverage": 50})
    assert settings.mode == "cross" and settings.leverage == 50.0


def test_max_leverage_is_the_shared_constant() -> None:
    assert MAX_LEVERAGE == 125.0
