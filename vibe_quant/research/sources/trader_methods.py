"""Well-known traders' published methods as research items.

For each named method (``VQ_TRADER_METHODS``, comma-separated, or the default
list) the configured research LLM writes a precise rule summary — entries,
exits, filters, parameters as the trader published them — which then goes
through the normal extraction, so it is turned into a DSL strategy and
screened like any Reddit idea. Needs ``VQ_EXTRACTOR=openai`` (or the ``claude``
CLI). The text is model-written: items carry ``extras.model_written = True``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from vibe_quant.research import openai_chat
from vibe_quant.research.schema import RawItem
from vibe_quant.research.sources import register_source

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

ENV_METHODS = "VQ_TRADER_METHODS"
DEFAULT_METHODS = (
    "Mark Minervini: Volatility Contraction Pattern (VCP) breakouts",
    "William O'Neil: CAN SLIM cup-with-handle breakouts",
    "Nicolas Darvas: Darvas box breakouts",
    "Kristjan Kullamagi (Qullamaggie): episodic pivots and momentum breakouts",
    "Larry Connors: RSI(2) mean reversion",
    "Linda Raschke: Holy Grail ADX pullback",
    "Richard Dennis: Turtle trading Donchian breakouts",
    "Stan Weinstein: Stage 2 breakouts above the 30-week moving average",
    "Jesse Livermore: pivotal point breakouts",
    "Oliver Kell: EMA crossback and wedge pop",
)
TIMEOUT_SECONDS = 180

PROMPT = (
    "Write the trading method below as precise, testable rules exactly as the trader published or "
    "taught it, for a daily or 4-hour US stock chart: the indicators with their lookbacks, the "
    "exact entry trigger, the stop, the exit/target, any trend or volume filters, and the "
    "parameter values the trader recommends (with the ranges worth testing). Plain prose and a "
    "rule list, no hype, no code. If the trader's rules are discretionary, give the closest "
    "mechanical version and say which part is an approximation.\n\nMethod: {method}"
)


def methods_from_env() -> list[str]:
    raw = os.getenv(ENV_METHODS, "").strip()
    if not raw:
        return list(DEFAULT_METHODS)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _write_up(method: str) -> str:
    return openai_chat.research_completion(PROMPT.format(method=method), timeout_seconds=TIMEOUT_SECONDS)


@register_source("trader_methods")
class TraderMethodsSource:
    """One model-written rule summary per named trading method."""

    name: ClassVar[str] = "trader_methods"

    def __init__(self, methods: list[str] | None = None) -> None:
        self._methods = methods if methods is not None else methods_from_env()

    def close(self) -> None:
        return None

    def fetch(self, since: datetime | None, limit: int) -> Iterable[RawItem]:  # noqa: ARG002
        for method in self._methods[:limit]:
            try:
                body = _write_up(method)
            except (ValueError, FileNotFoundError, subprocess.TimeoutExpired) as e:
                logger.warning("trader_methods: %s failed: %s", method, e)
                continue
            yield RawItem(
                source=self.name,
                external_id=hashlib.sha256(method.encode()).hexdigest()[:16],
                url="",
                title=method,
                body=body,
                author=method.split(":", 1)[0].strip() or None,
                posted_at=datetime.now(tz=UTC),
                score=None,
                extras={"model_written": True},
            )
