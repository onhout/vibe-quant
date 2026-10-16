"""Validation venue configuration for NautilusTrader backtesting."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from nautilus_trader.backtest.config import (
    ImportableFeeModelConfig,
    ImportableFillModelConfig,
    ImportableLatencyModelConfig,
)
from nautilus_trader.config import (
    BacktestVenueConfig,
    LatencyModelConfig,
)

from vibe_quant.balance_settings import DEFAULT_STARTING_BALANCE_USDT
from vibe_quant.fees import BINANCE_USDTM_TIERS, FeeSchedule
from vibe_quant.validation.fill_model import (
    ScreeningFillModelConfig,
    VolumeSlippageFillModelConfig,
)
from vibe_quant.validation.latency import (
    LATENCY_PRESETS,
    LatencyPreset,
    LatencyValues,
    get_latency_model,
)

# Default Binance VIP0 fee rates (fraction of notional), single-sourced from
# the tier table in vibe_quant.fees so constants and schedule cannot diverge.
BINANCE_MAKER_FEE = BINANCE_USDTM_TIERS["VIP0"][0]  # 0.02%
BINANCE_TAKER_FEE = BINANCE_USDTM_TIERS["VIP0"][1]  # 0.05%

# Fee model used for a non-default schedule (the default keeps NT's model).
SCHEDULE_FEE_MODEL_PATH = "vibe_quant.validation.fee_model:ScheduleFeeModel"
SCHEDULE_FEE_MODEL_CONFIG_PATH = "vibe_quant.validation.fee_model:ScheduleFeeModelConfig"

# Single source of truth for the default account size lives in
# vibe_quant.balance_settings (imported above and re-exported here for the existing
# importers; the API schema must not pull the validation stack just to read the
# default). The results API fallback MUST match it — a mismatched fallback (e.g. 10k
# vs 1k) scales every equity-curve/drawdown/monthly-return percentage by 10x.


@dataclass
class VenueConfig:
    """Configuration for a simulated trading venue.

    Attributes:
        name: Venue identifier ("BINANCE" for USDT-M perps, "ALPACA" for US stocks).
        starting_balance_usdt: Starting account balance in ``base_currency``.
        account_type: NT account type: "MARGIN" (perps) or "CASH" (stocks: no
            leverage, and NT rejects sells that would open a short).
        base_currency: Account currency ("USDT" on Binance, "USD" on Alpaca).
        default_leverage: Default leverage for all instruments.
        leverages: Per-instrument leverage overrides.
        latency_preset: Latency preset or None for no latency.
        latency_config: Custom latency config (overrides preset).
        use_volume_slippage: Use volume-based slippage (validation mode).
        fill_config: Fill model configuration.
        fee_schedule: Binance fee schedule (VIP tier + optional BNB discount).
            The default keeps NT's MakerTakerFeeModel path (rates read from
            the instrument definition); ``maker_fee`` / ``taker_fee`` are
            read-only views derived from it.
        bar_adaptive_high_low_ordering: When one bar touches both a long's
            SL and TP, NT's default O->H->L->C path books longs at TP and
            shorts at SL. Adaptive ordering (extreme nearer the open first)
            is direction-symmetric. SPEC asks for SL-first, which NT cannot
            express (ordering is per bar, not per order) — bd
            vibe-quant-e70tl.12. Shared by screening and validation.
    """

    name: str = "BINANCE"
    starting_balance_usdt: int = DEFAULT_STARTING_BALANCE_USDT
    account_type: str = "MARGIN"
    base_currency: str = "USDT"
    default_leverage: Decimal = Decimal("10")
    leverages: dict[str, Decimal] = field(default_factory=dict)

    # Latency configuration
    latency_preset: LatencyPreset | str | None = None
    latency_config: LatencyModelConfig | None = None

    # Fill model configuration
    use_volume_slippage: bool = False
    fill_config: VolumeSlippageFillModelConfig | ScreeningFillModelConfig | None = None

    # Fee configuration. The default schedule (VIP0, no BNB) keeps NT's
    # MakerTakerFeeModel path, so every pre-existing number is bit-identical;
    # a non-default schedule swaps in ScheduleFeeModel with its rates.
    fee_schedule: FeeSchedule = field(default_factory=FeeSchedule)

    # Same-bar SL/TP ambiguity resolution (see docstring)
    bar_adaptive_high_low_ordering: bool = True

    @property
    def maker_fee(self) -> Decimal:
        """Maker fee rate (fraction of notional) derived from the schedule."""
        return self.fee_schedule.maker_rate

    @property
    def taker_fee(self) -> Decimal:
        """Taker fee rate (fraction of notional) derived from the schedule."""
        return self.fee_schedule.taker_rate


# Fixed seed for the screening fill model's probabilistic slippage draws. NT's
# FillModel is unseeded by default, which made identical screening replays (and
# GA fitness evals) drift in the low-order digits run-to-run. Pinning it makes
# the no-latency screening/discovery path byte-reproducible (bd vibe-quant-1gvyc).
SCREENING_FILL_SEED = 42



def _account(venue: str, default_leverage: Decimal) -> tuple[str, str, str, Decimal]:
    """(name, account_type, base_currency, leverage) of a venue.

    Alpaca US stocks run on a USD cash account (no leverage; commission-free, so the
    default MakerTakerFeeModel reads the Equity instrument's zero fees).
    """
    if venue == "ALPACA":
        return "ALPACA", "CASH", "USD", Decimal("1")
    if venue != "BINANCE":
        raise ValueError(f"unknown backtest venue {venue!r}")
    return "BINANCE", "MARGIN", "USDT", default_leverage


def create_venue_config_for_screening(
    starting_balance_usdt: int = DEFAULT_STARTING_BALANCE_USDT,
    default_leverage: Decimal = Decimal("10"),
    leverages: dict[str, Decimal] | None = None,
    venue: str = "BINANCE",
) -> VenueConfig:
    """Create VenueConfig optimized for screening mode.

    Screening mode uses:
    - No latency (fast execution)
    - Simple probabilistic fill model
    - Standard fees from instrument definition

    Args:
        starting_balance_usdt: Starting balance.
        default_leverage: Default leverage.
        leverages: Per-instrument leverage overrides.
        venue: "BINANCE" (USDT-M perps) or "ALPACA" (US stocks, USD cash account).

    Returns:
        VenueConfig for screening.
    """
    name, account_type, base_currency, leverage = _account(venue, default_leverage)
    return VenueConfig(
        starting_balance_usdt=starting_balance_usdt,
        leverages=leverages or {},
        latency_preset=None,
        name=name,
        account_type=account_type,
        base_currency=base_currency,
        default_leverage=leverage,
        use_volume_slippage=False,
        fill_config=ScreeningFillModelConfig(
            prob_fill_on_limit=0.8,
            prob_slippage=0.5,
            random_seed=SCREENING_FILL_SEED,
        ),
    )


def create_venue_config_for_validation(
    starting_balance_usdt: int = DEFAULT_STARTING_BALANCE_USDT,
    default_leverage: Decimal = Decimal("10"),
    leverages: dict[str, Decimal] | None = None,
    latency_preset: LatencyPreset | str | None = LatencyPreset.CLOUD,
    impact_coefficient: float = 0.1,
    venue: str = "BINANCE",
) -> VenueConfig:
    """Create VenueConfig optimized for validation mode.

    Validation mode uses:
    - Realistic latency simulation (when latency_preset is set)
    - Post-fill SPEC slippage estimation (engine tick slippage disabled)
    - Standard fees from instrument definition

    When latency_preset is None (sub-5m bar data), LatencyModel is skipped
    because NT defers orders to the next bar regardless of actual delay.
    Instead we use a probabilistic synthetic order book plus stricter
    limit-touch fills to introduce moderate degradation without full-bar lag.

    Args:
        starting_balance_usdt: Starting balance.
        default_leverage: Default leverage.
        leverages: Per-instrument leverage overrides.
        latency_preset: Latency preset for execution delays. None = no latency.
        impact_coefficient: Market impact coefficient for slippage.
        venue: "BINANCE" (USDT-M perps) or "ALPACA" (US stocks, USD cash account).

    Returns:
        VenueConfig for validation.
    """
    # With a latency model, keep market fills optimistic and let the latency path
    # plus post-fill SPEC slippage provide degradation. Without latency (sub-5m),
    # use a probabilistic synthetic book to make aggressive orders worse 30% of
    # the time and lower limit-touch fill quality to 70%.
    if latency_preset is None:
        prob_fill_on_limit = 0.7
        prob_best_price_fill = 0.7
        max_adverse_ticks = 2
    else:
        prob_fill_on_limit = 0.8
        prob_best_price_fill = 1.0
        max_adverse_ticks = 1

    name, account_type, base_currency, leverage = _account(venue, default_leverage)
    return VenueConfig(
        starting_balance_usdt=starting_balance_usdt,
        leverages=leverages or {},
        latency_preset=latency_preset,
        use_volume_slippage=True,
        name=name,
        account_type=account_type,
        base_currency=base_currency,
        default_leverage=leverage,
        fill_config=VolumeSlippageFillModelConfig(
            impact_coefficient=impact_coefficient,
            prob_fill_on_limit=prob_fill_on_limit,
            prob_best_price_fill=prob_best_price_fill,
            max_adverse_ticks=max_adverse_ticks,
            prob_slippage=0.0,
        ),
    )


def create_backtest_venue_config(config: VenueConfig) -> BacktestVenueConfig:
    """Create NautilusTrader BacktestVenueConfig from VenueConfig.

    Args:
        config: Venue configuration.

    Returns:
        Configured BacktestVenueConfig.
    """
    # Build starting balances list
    starting_balances = [f"{config.starting_balance_usdt} {config.base_currency}"]

    # Build leverages dict with proper string keys
    leverages: dict[str, float] | None = None
    if config.leverages:
        leverages = {k: float(v) for k, v in config.leverages.items()}

    # Create latency model config if specified
    latency_model: ImportableLatencyModelConfig | None = None
    if config.latency_config is not None:
        latency_model = _create_importable_latency_model_config(config.latency_config)
    elif config.latency_preset is not None:
        latency_config = get_latency_model(config.latency_preset)
        latency_model = _create_importable_latency_model_config(latency_config)

    # Create fill model config
    fill_model = _create_importable_fill_model_config(config)

    # A non-default schedule would silently change a non-Binance venue's fee
    # semantics (Ethereal fills on its own schedule), so refuse it.
    if config.name != "BINANCE" and not config.fee_schedule.is_default:
        raise ValueError(
            f"fee_schedule is only supported for the BINANCE venue, not {config.name!r}"
        )

    # Create fee model config from the schedule (default keeps MakerTakerFeeModel)
    fee_model = _create_importable_fee_model_config(config)

    return BacktestVenueConfig(
        name=config.name,
        oms_type="NETTING",
        account_type=config.account_type,
        base_currency=config.base_currency if config.account_type == "CASH" else None,
        starting_balances=starting_balances,
        default_leverage=float(config.default_leverage),
        leverages=leverages,
        fill_model=fill_model,
        latency_model=latency_model,
        fee_model=fee_model,
        bar_execution=True,
        bar_adaptive_high_low_ordering=config.bar_adaptive_high_low_ordering,
        reject_stop_orders=False,
        support_gtd_orders=True,
        support_contingent_orders=True,
        use_position_ids=True,
        use_reduce_only=True,
    )


def _create_importable_fill_model_config(
    config: VenueConfig,
) -> ImportableFillModelConfig:
    """Create ImportableFillModelConfig for BacktestVenueConfig.

    Note: BacktestVenueConfig expects ImportableFillModelConfig which has
    fill_model_path, config_path, and config fields. For simpler cases,
    we use FillModelConfig directly through the config parameter.
    """

    if config.use_volume_slippage:
        # Use our custom VolumeSlippageFillModel
        fill_cfg = config.fill_config
        if fill_cfg is None:
            fill_cfg = VolumeSlippageFillModelConfig()

        if not isinstance(fill_cfg, VolumeSlippageFillModelConfig):
            fill_cfg = VolumeSlippageFillModelConfig()

        return ImportableFillModelConfig(
            fill_model_path="vibe_quant.validation.fill_model:VolumeSlippageFillModel",
            config_path="vibe_quant.validation.fill_model:VolumeSlippageFillModelConfig",
            config={
                "impact_coefficient": fill_cfg.impact_coefficient,
                "prob_fill_on_limit": fill_cfg.prob_fill_on_limit,
                "prob_best_price_fill": fill_cfg.prob_best_price_fill,
                "max_adverse_ticks": fill_cfg.max_adverse_ticks,
                "prob_slippage": fill_cfg.prob_slippage,
                "random_seed": fill_cfg.random_seed,
                "stop_slippage_ticks": fill_cfg.stop_slippage_ticks,
            },
        )
    else:
        # Use standard FillModel for screening
        fill_cfg = config.fill_config
        if fill_cfg is None:
            fill_cfg = ScreeningFillModelConfig()

        if not isinstance(fill_cfg, ScreeningFillModelConfig):
            fill_cfg = ScreeningFillModelConfig()

        # For standard FillModel, use the built-in FillModelConfig
        # BacktestVenueConfig will handle this internally
        return ImportableFillModelConfig(
            fill_model_path="nautilus_trader.backtest.models:FillModel",
            config_path="nautilus_trader.config:FillModelConfig",
            config={
                "prob_fill_on_limit": fill_cfg.prob_fill_on_limit,
                "prob_slippage": fill_cfg.prob_slippage,
                "random_seed": fill_cfg.random_seed,
            },
        )


def _create_importable_latency_model_config(
    latency_config: LatencyModelConfig,
) -> ImportableLatencyModelConfig:
    """Create ImportableLatencyModelConfig for BacktestVenueConfig."""
    return ImportableLatencyModelConfig(
        latency_model_path="nautilus_trader.backtest.models:LatencyModel",
        config_path="nautilus_trader.config:LatencyModelConfig",
        config={
            "base_latency_nanos": latency_config.base_latency_nanos,
            "insert_latency_nanos": latency_config.insert_latency_nanos,
            "update_latency_nanos": latency_config.update_latency_nanos,
            "cancel_latency_nanos": latency_config.cancel_latency_nanos,
        },
    )


def _create_importable_fee_model_config(config: VenueConfig) -> ImportableFeeModelConfig:
    """Create ImportableFeeModelConfig from the venue's fee schedule.

    The default schedule keeps NT's MakerTakerFeeModel (fees read from the
    instrument definition), so the default path is unchanged; any other
    schedule uses :class:`ScheduleFeeModel` with the schedule's rates.
    """
    schedule = config.fee_schedule
    if schedule.is_default:
        return ImportableFeeModelConfig(
            fee_model_path="nautilus_trader.backtest.models:MakerTakerFeeModel",
            config_path="nautilus_trader.config:MakerTakerFeeModelConfig",
            config={},
        )
    return ImportableFeeModelConfig(
        fee_model_path=SCHEDULE_FEE_MODEL_PATH,
        config_path=SCHEDULE_FEE_MODEL_CONFIG_PATH,
        config={
            "maker_rate": str(schedule.maker_rate),
            "taker_rate": str(schedule.taker_rate),
        },
    )


# Re-export latency presets for convenience
__all__ = [
    "VenueConfig",
    "DEFAULT_STARTING_BALANCE_USDT",
    "BINANCE_MAKER_FEE",
    "BINANCE_TAKER_FEE",
    "LATENCY_PRESETS",
    "LatencyPreset",
    "LatencyValues",
    "create_venue_config_for_screening",
    "create_venue_config_for_validation",
    "create_backtest_venue_config",
]
