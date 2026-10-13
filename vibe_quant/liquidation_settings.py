"""Run-level liquidation settings shared by screening, validation and discovery.

The one parse rule for ``liquidation_mode`` / ``leverage`` (bead vibe-quant-2gyk1.1.8). It
lives OUTSIDE :mod:`vibe_quant.liquidation` on purpose: a mode-off run must never import the
liquidation package (import isolation), and these settings are pure stdlib.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

LiquidationMode = Literal["off", "isolated", "cross"]
LIQUIDATION_MODES: tuple[LiquidationMode, ...] = ("off", "isolated", "cross")
DEFAULT_LEVERAGE = 10.0
MAX_LEVERAGE = 125.0


class LiquidationConfigError(ValueError):
    """A run's liquidation settings are invalid (they are never silently defaulted)."""


@dataclass(frozen=True)
class LiquidationSettings:
    """The validated ``liquidation_mode`` + ``leverage`` of one run.

    ``mode`` is case-sensitive and ``None`` is NOT "off" (P4). ``leverage`` is validated to
    be a number in (0, 125] only while the mode is on (L4); it is stored as a float (L2).
    """

    mode: LiquidationMode = "off"
    leverage: float = DEFAULT_LEVERAGE

    def __post_init__(self) -> None:
        if self.mode not in LIQUIDATION_MODES:
            msg = f"liquidation_mode must be one of {LIQUIDATION_MODES}, got {self.mode!r}"
            raise LiquidationConfigError(msg)
        lev = self.leverage
        if self.mode != "off" and (
            isinstance(lev, bool)
            or not isinstance(lev, (int, float))
            or not math.isfinite(lev)
            or not 0.0 < lev <= MAX_LEVERAGE
        ):
            msg = f"leverage must be a number in (0, 125], got {lev!r}"
            raise LiquidationConfigError(msg)
        if isinstance(lev, (int, float)) and not isinstance(lev, bool):
            object.__setattr__(self, "leverage", float(lev))

    @property
    def on(self) -> bool:
        """Whether liquidation runs at all."""
        return self.mode != "off"

    @classmethod
    def from_params(cls, params: Mapping[str, object]) -> LiquidationSettings:
        """Read ``liquidation_mode`` / ``leverage`` from a run's parameters.

        A missing mode key means off (P1/P2: old rows and clients).
        """
        return cls(
            mode=cast("LiquidationMode", params.get("liquidation_mode", "off")),
            leverage=cast("float", params.get("leverage", DEFAULT_LEVERAGE)),
        )

    def to_params(self) -> dict[str, object]:
        """The parameters to store: empty when off, so existing rows stay unchanged."""
        if self.mode == "off":
            return {}
        return {"liquidation_mode": self.mode, "leverage": self.leverage}

    def to_notes(self) -> dict[str, object] | None:
        """The ``notes["liquidation"]`` payload: None when off."""
        if self.mode == "off":
            return None
        return {"mode": self.mode, "leverage": self.leverage}
