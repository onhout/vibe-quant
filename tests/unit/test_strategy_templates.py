"""Bundled strategy templates: every YAML is a valid, compilable strategy."""

from __future__ import annotations

from pathlib import Path

import pytest

from vibe_quant.dsl.compiler import StrategyCompiler
from vibe_quant.dsl.parser import parse_strategy
from vibe_quant.strategies.templates import _metadata
from vibe_quant.strategies.templates._metadata import TEMPLATES, list_template_payloads

_TEMPLATE_FILES = sorted(Path(_metadata.__file__).parent.glob("*.yaml"))


def test_registry_matches_template_directory() -> None:
    assert [p.name for p in _TEMPLATE_FILES] == sorted(t.file_name for t in TEMPLATES)


@pytest.mark.parametrize("path", _TEMPLATE_FILES, ids=lambda p: p.stem)
def test_template_parses_and_compiles(path: Path) -> None:
    dsl = parse_strategy(path)
    assert dsl.name == path.stem
    source = StrategyCompiler().compile(dsl)
    compile(source, path.name, "exec")  # generated module is valid Python


def test_payloads_cover_every_template_with_dialog_fields() -> None:
    payloads = list_template_payloads()
    assert [p["name"] for p in payloads] == [t.file_name.removesuffix(".yaml") for t in TEMPLATES]
    for p in payloads:
        assert isinstance(p["description"], str) and p["description"]
        assert isinstance(p["strategy_type"], str) and p["strategy_type"]
        dsl_config = p["dsl_config"]
        assert isinstance(dsl_config, dict)
        assert dsl_config["name"] == p["name"]
        assert dsl_config["indicators"]


def test_invalid_template_is_excluded_with_logged_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: bad\ntimeframe: 1h\n")
    meta = _metadata.TemplateMeta(
        file_name="bad.yaml",
        display_name="Bad",
        category="Trend",
        difficulty="Beginner",
        description="broken",
        market_conditions="-",
        instruments="-",
        tags=(),
    )
    monkeypatch.setattr(_metadata, "_TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(_metadata, "TEMPLATES", [meta])
    with caplog.at_level("WARNING"):
        assert list_template_payloads() == []
    assert "bad.yaml" in caplog.text


_TEMPLATES_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/components/strategies/editor/__fixtures__/templates.json"
)


def test_frontend_fixture_matches_served_payloads() -> None:
    """vitest runs the editor's canonical parser on this fixture; it must be the real payloads.

    Regenerate: json.dump of {name, description, strategy_type, dsl_config} per
    ``list_template_payloads()`` into the fixture path.
    """
    import json

    fixture = json.loads(_TEMPLATES_FIXTURE.read_text())
    expected = [
        {k: p[k] for k in ("name", "description", "strategy_type", "dsl_config")}
        for p in list_template_payloads()
    ]
    assert fixture == expected


def test_template_strategy_type_mapping() -> None:
    # "mean-reversion"/"breakout" tags win over the category default (stochastic_reversal is
    # Momentum, donchian_breakout is Trend).
    assert {p["name"]: p["strategy_type"] for p in list_template_payloads()} == {
        "rsi_mean_reversion": "mean_reversion",
        "rsi_divergence": "momentum",
        "macd_crossover": "momentum",
        "stochastic_reversal": "mean_reversion",
        "ema_ribbon": "trend_following",
        "donchian_breakout": "breakout",
        "dual_ema_crossover": "trend_following",
        "bollinger_squeeze": "volatility",
        "keltner_trend": "volatility",
        "mtf_trend_scalp": "trend_following",
        "mtf_momentum_filter": "trend_following",
        "vwap_intraday": "mean_reversion",
    }
