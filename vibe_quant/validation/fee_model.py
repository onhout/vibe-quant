"""A NautilusTrader FeeModel driven by a fixed maker/taker schedule (D3-1).

``MakerTakerFeeModel`` reads the maker/taker rates from the instrument
definition, which is shared catalog-wide, so a per-run fee schedule needs its
own model. ``ScheduleFeeModel`` uses the same notional/rounding/currency
handling as ``MakerTakerFeeModel`` and only swaps the source of the rates.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from nautilus_trader.backtest.config import FeeModelConfig
from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.model.enums import LiquiditySide
from nautilus_trader.model.objects import Money

if TYPE_CHECKING:
    from nautilus_trader.model.instruments.base import Instrument
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.orders.base import Order


class ScheduleFeeModelConfig(FeeModelConfig, frozen=True):
    """Configuration for :class:`ScheduleFeeModel`.

    Attributes:
        maker_rate: Maker fee as a string Decimal fraction of notional.
        taker_rate: Taker fee as a string Decimal fraction of notional.
    """

    maker_rate: str
    taker_rate: str


class ScheduleFeeModel(FeeModel):  # type: ignore[misc]
    """Charges the configured maker/taker rates on the fill's notional value.

    Same formula and currency handling as NT's ``MakerTakerFeeModel`` (quote
    currency for linear instruments, base currency for inverse ones), with the
    rates taken from the config instead of the instrument definition.
    """

    def __init__(self, config: ScheduleFeeModelConfig | None = None) -> None:
        super().__init__()
        if config is None:
            raise ValueError("ScheduleFeeModel requires a ScheduleFeeModelConfig")
        self._maker_rate = Decimal(config.maker_rate)
        self._taker_rate = Decimal(config.taker_rate)

    @property
    def maker_rate(self) -> Decimal:
        """Maker fee as a fraction of notional."""
        return self._maker_rate

    @property
    def taker_rate(self) -> Decimal:
        """Taker fee as a fraction of notional."""
        return self._taker_rate

    def get_commission(
        self,
        order: Order,
        fill_qty: Quantity,
        fill_px: Price,
        instrument: Instrument,
    ) -> Money:
        """Return the commission for a fill: notional x rate by liquidity side."""
        notional = instrument.notional_value(
            quantity=fill_qty,
            price=fill_px,
            use_quote_for_inverse=False,
        )

        if order.liquidity_side == LiquiditySide.MAKER:
            rate = self._maker_rate
        elif order.liquidity_side == LiquiditySide.TAKER:
            rate = self._taker_rate
        else:
            raise ValueError(f"invalid `LiquiditySide`, was {order.liquidity_side}")

        commission_value = notional.as_decimal() * rate
        if instrument.is_inverse:  # not using quote for inverse (see above)
            return Money(commission_value, instrument.get_base_currency())
        return Money(commission_value, instrument.quote_currency)
