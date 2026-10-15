"""One shared starting-balance rule (bead vibe-quant-zpbmo, task Z1).

Binding contract rows exercised here: R1 (key absent -> 1000, stored params
unchanged), R2 (whole numbers and whole floats -> the same int), R3/R4/R5 (every
other input -> ``BalanceConfigError`` naming the value; the type and range are
checked BEFORE any ``int()``/``float()``, so ``10**400`` never raises
OverflowError), and R13b (``recorded_balance`` is the ONE rule for reading a
recorded balance: NULL -> 1000).
"""

from __future__ import annotations

import math

import pytest

from vibe_quant.balance_settings import (
    DEFAULT_STARTING_BALANCE_USDT,
    MAX_STARTING_BALANCE,
    MIN_STARTING_BALANCE,
    BalanceConfigError,
    parse_initial_balance,
    recorded_balance,
    validate_starting_balance,
)

_INVALID = object()  # marker: the input must raise BalanceConfigError (R3-R5)

BALANCE_MESSAGE = "initial_balance must be a whole number in [1, 1000000000], got"

INPUT_DOMAIN = [
    pytest.param(1, 1, id="int-1"),
    pytest.param(1000, 1000, id="int-1000"),
    pytest.param(10000, 10000, id="int-10000"),
    pytest.param(1_000_000_000, 1_000_000_000, id="int-max"),
    pytest.param(10000.0, 10000, id="float-10000.0"),
    pytest.param(0, _INVALID, id="zero"),
    pytest.param(-5, _INVALID, id="negative"),
    pytest.param(1000.5, _INVALID, id="float-1000.5"),
    pytest.param(0.5, _INVALID, id="float-0.5"),
    pytest.param(1_000_000_001, _INVALID, id="above-max"),
    pytest.param(1e308, _INVALID, id="1e308"),
    pytest.param(10**400, _INVALID, id="10-to-400"),
    pytest.param(-(10**400), _INVALID, id="minus-10-to-400"),
    pytest.param(True, _INVALID, id="True"),
    pytest.param(False, _INVALID, id="False"),
    pytest.param("10000", _INVALID, id="str-10000"),
    pytest.param(None, _INVALID, id="None"),
    pytest.param([], _INVALID, id="list"),
    pytest.param({}, _INVALID, id="dict"),
    pytest.param(math.nan, _INVALID, id="nan"),
    pytest.param(math.inf, _INVALID, id="inf"),
    pytest.param(-math.inf, _INVALID, id="minus-inf"),
]


@pytest.mark.parametrize(("value", "expected"), INPUT_DOMAIN)
def test_initial_balance_input_domain(value: object, expected: object) -> None:
    """R2-R5: a whole number in [1, 1e9] parses; EVERY other input is a hard error."""
    if expected is _INVALID:
        with pytest.raises(BalanceConfigError) as excinfo:
            parse_initial_balance({"initial_balance": value})
        assert str(excinfo.value) == f"{BALANCE_MESSAGE} {value!r}"
    else:
        parsed = parse_initial_balance({"initial_balance": value})
        assert parsed == expected
        assert isinstance(parsed, int)


def test_key_absent_is_the_default_and_params_are_untouched() -> None:
    """R1: a missing key means 1000; the stored parameters are never rewritten."""
    params: dict[str, object] = {"rsi_period": 14}
    assert parse_initial_balance(params) == 1000
    assert params == {"rsi_period": 14}
    assert DEFAULT_STARTING_BALANCE_USDT == 1000


def test_validate_returns_a_plain_int_for_whole_floats() -> None:
    """R2: 10000.0 is the same value as an int (no truncation, no float left behind)."""
    parsed = validate_starting_balance(10000.0)
    assert parsed == 10000
    assert isinstance(parsed, int)
    assert validate_starting_balance(1) == 1


def test_bounds_are_the_contract_numbers() -> None:
    """The domain in every error message is [1, 1000000000]."""
    assert MIN_STARTING_BALANCE == 1
    assert MAX_STARTING_BALANCE == 1_000_000_000


def test_error_is_a_value_error() -> None:
    """Callers (the API schema) turn this into a 422 through the ValueError path."""
    assert issubclass(BalanceConfigError, ValueError)
    with pytest.raises(ValueError):
        validate_starting_balance(1000.5)


def test_recorded_balance_null_is_the_default() -> None:
    """R13b: a NULL recorded column (an older row) reads as 1000, not 0 and not 10k."""
    assert recorded_balance(None) == 1000


def test_recorded_balance_whole_value_is_an_int() -> None:
    """R13b: 10000.0 stored in sweep_results.starting_balance reads as 10000."""
    assert recorded_balance(10000.0) == 10000
    assert recorded_balance(250) == 250


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(1000.5, id="fractional"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(1_000_000_001, id="above-max"),
        pytest.param("10000", id="str"),
        pytest.param(math.nan, id="nan"),
        pytest.param(10**400, id="huge"),
    ],
)
def test_recorded_balance_invalid_raises(value: object) -> None:
    """R13b: a recorded balance is validated by the same rule, never silently defaulted."""
    with pytest.raises(BalanceConfigError):
        recorded_balance(value)


def test_venue_reexports_the_same_default_constant() -> None:
    """Existing importers of validation.venue.DEFAULT_STARTING_BALANCE_USDT keep working."""
    from vibe_quant.validation.venue import DEFAULT_STARTING_BALANCE_USDT as venue_default

    assert venue_default is DEFAULT_STARTING_BALANCE_USDT
