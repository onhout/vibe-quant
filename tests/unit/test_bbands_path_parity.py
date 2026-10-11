"""BBANDS reads the same numbers on the NT path and the compute_fn (pandas-ta) path.

The NT path derives ``percent_b`` / ``bandwidth`` with ``derived.py`` from a live
``BollingerBands``; the compute_fn path returns them straight from
``compute_bbands``. A DSL threshold such as ``bb.bandwidth > 0.05`` (the GA samples
[0, 0.2]) must mean the same on both. pandas-ta's raw BBB is percent (x100), so
``compute_bbands`` converts it to a fraction. NT's BollingerBands averages the
typical price (H+L+C)/3, so ``compute_bbands`` feeds pandas-ta the same series.
The first tests use bars with high == low == close (typical price == close); the last
one uses real high-low ranges.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from nautilus_trader.indicators import BollingerBands

from vibe_quant.dsl import derived
from vibe_quant.dsl.compute_builtins import compute_bbands


def _flat_bars(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0})


@pytest.mark.parametrize(("period", "std_dev"), [(20, 2.0), (10, 1.5), (30, 3.0)])
def test_bbands_outputs_match_nt_path_on_same_bars(period: int, std_dev: float) -> None:
    df = _flat_bars()
    nt = BollingerBands(period, std_dev)
    rows = []
    for c in df["close"]:
        nt.update_raw(c, c, c)
        rows.append(
            {
                "upper": nt.upper,
                "middle": nt.middle,
                "lower": nt.lower,
                "percent_b": derived.compute_percent_b(nt, c),
                "bandwidth": derived.compute_bandwidth(nt, c),
            }
        )
    nt_df = pd.DataFrame(rows)

    got = compute_bbands(df, {"period": period, "std_dev": std_dev})

    warm = slice(period + 5, None)  # past NT's initialisation window
    for name in ("upper", "middle", "lower", "percent_b", "bandwidth"):
        np.testing.assert_allclose(
            got[name].iloc[warm].to_numpy(),
            nt_df[name].iloc[warm].to_numpy(),
            rtol=1e-9,
            atol=1e-12,
            err_msg=name,
        )


@pytest.mark.parametrize("level", [100.0, 0.5, 63_000.0])
def test_flat_window_matches_nt_guards_exactly(level: float) -> None:
    """Zero-width bands: NT/derived give bandwidth 0.0 and percent_b 0.5 exactly.

    pandas-ta adds an epsilon (bandwidth 2.2e-18, percent_b 1.0) which would flip
    ``bandwidth <= 0`` / ``percent_b`` thresholds between the two paths.
    """
    n, period = 30, 20
    df = pd.DataFrame({k: np.full(n, level) for k in ("open", "high", "low", "close", "volume")})
    nt = BollingerBands(period, 2.0)
    for c in df["close"]:
        nt.update_raw(c, c, c)

    got = compute_bbands(df, {"period": period, "std_dev": 2.0})

    assert float(got["bandwidth"].iloc[-1]) == derived.compute_bandwidth(nt, level) == 0.0
    assert float(got["percent_b"].iloc[-1]) == derived.compute_percent_b(nt, level) == 0.5
    assert bool((got["bandwidth"].iloc[period:] <= 0).all())
    # Warm-up rows (bands undefined) stay NaN rather than turning into 0.0 / 0.5.
    assert got["bandwidth"].iloc[: period - 1].isna().all()
    assert got["percent_b"].iloc[: period - 1].isna().all()


@pytest.mark.parametrize(
    ("low", "high"), [(-1.0, 1.0), (-101.0, -99.0)], ids=["zero_middle", "negative_middle"]
)
def test_non_positive_middle_matches_nt_guard_exactly(low: float, high: float) -> None:
    """Non-flat windows with middle == 0 / middle < 0: bandwidth is exactly 0.0.

    (upper - lower) / middle would be inf (middle 0) or negative (middle < 0) and
    flip ``bandwidth >= 0`` thresholds; NT/derived return the 0.0 fallback. The bands
    are wide (percent_b is a real 0.25 / 0.75, not the flat-window 0.5).
    """
    n, period = 40, 20
    close = np.where(np.arange(n) % 2 == 0, low, high)
    df = pd.DataFrame({k: close for k in ("open", "high", "low", "close", "volume")})
    nt = BollingerBands(period, 2.0)
    nt_bw, nt_pb = [], []
    for c in close:
        nt.update_raw(float(c), float(c), float(c))
        nt_bw.append(derived.compute_bandwidth(nt, float(c)))
        nt_pb.append(derived.compute_percent_b(nt, float(c)))

    got = compute_bbands(df, {"period": period, "std_dev": 2.0})

    warm = slice(period + 5, None)
    assert (got["middle"].iloc[warm] <= 0).all()
    assert (got["upper"].iloc[warm] > got["lower"].iloc[warm]).all()
    assert got["bandwidth"].iloc[warm].tolist() == nt_bw[warm] == [0.0] * len(nt_bw[warm])
    assert got["percent_b"].iloc[warm].tolist() == nt_pb[warm]
    assert {0.25, 0.75} == set(nt_pb[warm])
    # Warm-up rows (bands undefined) stay NaN.
    assert got["bandwidth"].iloc[: period - 1].isna().all()
    assert got["percent_b"].iloc[: period - 1].isna().all()


def _ohlc_bars(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    high = close * (1.0 + rng.uniform(0.0005, 0.01, n))
    low = close * (1.0 - rng.uniform(0.0005, 0.01, n))
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 1.0})


@pytest.mark.parametrize(("period", "std_dev"), [(20, 2.0), (10, 1.5), (30, 3.0)])
def test_bbands_match_nt_typical_price_on_real_ohlc_bars(period: int, std_dev: float) -> None:
    """NT's BollingerBands averages the typical price (H+L+C)/3, not the close.

    On bars with a real high-low range the bands from a close-only pandas-ta call
    sit ~0.1% off NT's; ``percent_b`` still compares the close with those bands.
    """
    df = _ohlc_bars()
    nt = BollingerBands(period, std_dev)
    rows = []
    for h, low, c in zip(df["high"], df["low"], df["close"], strict=True):
        nt.update_raw(h, low, c)
        rows.append(
            {
                "upper": nt.upper,
                "middle": nt.middle,
                "lower": nt.lower,
                "percent_b": derived.compute_percent_b(nt, c),
                "bandwidth": derived.compute_bandwidth(nt, c),
            }
        )
    nt_df = pd.DataFrame(rows)

    got = compute_bbands(df, {"period": period, "std_dev": std_dev})

    warm = slice(period + 5, None)
    for name in ("upper", "middle", "lower", "percent_b", "bandwidth"):
        np.testing.assert_allclose(
            got[name].iloc[warm].to_numpy(),
            nt_df[name].iloc[warm].to_numpy(),
            rtol=0.0,
            atol=1e-9,
            err_msg=name,
        )
