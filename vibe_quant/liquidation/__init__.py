"""Binance USDT-M liquidation simulation primitives (tier tables, LP math, mark scanner)."""

from __future__ import annotations

from vibe_quant.liquidation.overlay import OverlayResult, liquidation_overlay, position_views
from vibe_quant.liquidation.scan import (
    EventLike,
    LiquidationHit,
    LiquidationResult,
    MarkLike,
    MissingMarkSeries,
    PositionView,
    scan_liquidations,
)
from vibe_quant.liquidation.tiers import (
    Bracket,
    InsolventPosition,
    TierTable,
    TierTableError,
    TierTableExceeded,
    TierTables,
    UnknownSymbol,
    cross_liq_price,
    isolated_liq_price,
    load_tier_tables,
)

__all__ = [
    "Bracket",
    "InsolventPosition",
    "EventLike",
    "LiquidationHit",
    "LiquidationResult",
    "MarkLike",
    "MissingMarkSeries",
    "OverlayResult",
    "PositionView",
    "TierTable",
    "TierTableError",
    "TierTables",
    "TierTableExceeded",
    "UnknownSymbol",
    "cross_liq_price",
    "isolated_liq_price",
    "liquidation_overlay",
    "load_tier_tables",
    "position_views",
    "scan_liquidations",
]
