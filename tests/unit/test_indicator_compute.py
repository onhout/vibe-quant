"""Chart-overlay indicator computation (``vibe_quant.data.indicator_compute``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta_classic  # noqa: F401 — registers .ta accessor

from vibe_quant.data.indicator_compute import compute_indicators


def _ohlcv(n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    high = close * (1.0 + rng.uniform(0.0005, 0.01, n))
    low = close * (1.0 - rng.uniform(0.0005, 0.01, n))
    return pd.DataFrame(
        {
            "open_time": np.arange(n, dtype=np.int64) * 3_600_000,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(1.0, 10.0, n),
        }
    )


def _values(series: object) -> np.ndarray:
    return np.array([np.nan if p.value is None else p.value for p in series.data])  # type: ignore[attr-defined]


def test_keltner_channel_overlay_returns_lower_middle_upper_bands() -> None:
    df = _ohlcv()
    out = compute_indicators(df, [{"type": "KC", "period": 20, "atr_multiplier": 1.5}])

    by_name = {s.output_name: s for s in out}
    assert set(by_name) == {"lower", "middle", "upper"}

    ref = df.ta.kc(high=df["high"], low=df["low"], close=df["close"], length=20, scalar=1.5)
    cols = ref.columns.tolist()
    expected = {"lower": cols[0], "middle": cols[1], "upper": cols[2]}
    for name, col in expected.items():
        np.testing.assert_array_equal(_values(by_name[name]), ref[col].to_numpy(), err_msg=name)
    assert by_name["upper"].display_label == "KC Upper(20)"


def test_donchian_channel_overlay_returns_lower_middle_upper_bands() -> None:
    df = _ohlcv()
    out = compute_indicators(df, [{"type": "DONCHIAN", "period": 20}])

    by_name = {s.output_name: s for s in out}
    assert set(by_name) == {"lower", "middle", "upper"}

    ref = df.ta.donchian(high=df["high"], low=df["low"], lower_length=20, upper_length=20)
    for name, col in {"lower": "DCL_20_20", "middle": "DCM_20_20", "upper": "DCU_20_20"}.items():
        np.testing.assert_array_equal(_values(by_name[name]), ref[col].to_numpy(), err_msg=name)
