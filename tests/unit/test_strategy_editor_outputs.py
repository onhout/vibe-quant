"""What the visual/YAML editor saves for bundled templates must stay valid strategies.

The vitest suite (frontend/src/components/strategies/editor/canonical.test.ts) runs the editor's
TS logic (YAML keystroke round trip, every stop-loss/take-profit choice of the Risk tab) on the 12
bundled templates and pins the dicts it would PUT in ``__fixtures__/editor_outputs.json``
(regenerate: ``pnpm vitest run -u src/components/strategies/editor``).
Here the backend judges those dicts: they must pass the translator, the DSL validator and the
compiler, and a YAML keystroke must hand back the stored template unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vibe_quant.dsl.compiler import StrategyCompiler
from vibe_quant.dsl.parser import validate_strategy_dict
from vibe_quant.dsl.schema import VALID_STOP_LOSS_TYPES, VALID_TAKE_PROFIT_TYPES
from vibe_quant.dsl.translator import translate_dsl_config
from vibe_quant.strategies.templates._metadata import list_template_payloads

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/components/strategies/editor/__fixtures__/editor_outputs.json"
)
_OUTPUTS: dict[str, dict[str, Any]] = json.loads(_FIXTURE.read_text())
_STORED = {p["name"]: p["dsl_config"] for p in list_template_payloads()}
_SCENARIOS = (
    ["yaml_keystroke"]
    + [f"sl_{t}" for t in sorted(VALID_STOP_LOSS_TYPES)]
    + [f"tp_{t}" for t in sorted(VALID_TAKE_PROFIT_TYPES)]
)
_KEYS = [f"{name}::{scenario}" for name in _STORED for scenario in _SCENARIOS]


def test_fixture_covers_every_template_and_every_backend_risk_type() -> None:
    """The Risk tab must offer exactly the backend's types (a missing/extra scenario fails)."""
    assert sorted(_OUTPUTS) == sorted(_KEYS)
    assert len(_STORED) == 12


@pytest.mark.parametrize("key", _KEYS)
def test_editor_output_is_a_valid_compilable_strategy(key: str) -> None:
    entry = _OUTPUTS[key]
    assert "error" not in entry, (
        f"{key}: editor refused an edit the templates should allow: {entry}"
    )
    dsl_dict = entry["ok"]
    name = key.split("::")[0]
    assert translate_dsl_config(dsl_dict, name) == dsl_dict  # stays canonical, nothing rewritten
    strategy = validate_strategy_dict(dsl_dict)
    compile(StrategyCompiler().compile(strategy), key, "exec")


@pytest.mark.parametrize("name", list(_STORED))
def test_yaml_keystroke_returns_the_stored_template_unchanged(name: str) -> None:
    assert _OUTPUTS[f"{name}::yaml_keystroke"]["ok"] == _STORED[name]
