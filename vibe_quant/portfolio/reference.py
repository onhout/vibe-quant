"""Funding-carry reference simulator: the shared rules, no NautilusTrader (§1.2, §5).

:func:`simulate_reference` walks a run's decisions in order and executes them
with exactly the same rule functions the NT engine uses — :func:`decide`,
:class:`FundingAccumulator`, :func:`equity_at`,
:func:`ticks.release_tick`/:func:`ticks.check_release` and
:func:`sizing.nt_commission` — so the two executors produce bitwise-equal
outputs (the parity/exactness task proves it, R30). It re-implements no rule:

* a planned order fills at the symbol's first tick strictly after its decision
  (:func:`release_tick`, staleness via :func:`check_release`, R15b); a symbol
  with no later tick can never be released and fails closed (R15c);
* the fill price is that tick's price and the fee is the NT taker formula
  (:func:`nt_commission`, §5) on the exact quantity
  ``float(Decimal(steps) * step)``;
* funding is accrued window by window by the shared
  :class:`FundingAccumulator`, up to ``spec.end_ns``.

The book is one pooled account: ``decide`` sizes every symbol against the
equity the previous windows wrote (:func:`equity_at`), keyed off each symbol's
sizing tick; orders of one decision release in plan order (a flip's close
before its open) at the same tick.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from vibe_quant.portfolio.ledger import (
    CarryFill,
    CarryRun,
    FundingAccumulator,
    equity_at,
    steps_held,
)
from vibe_quant.portfolio.signal import trailing_funding
from vibe_quant.portfolio.sizing import decide, nt_commission
from vibe_quant.portfolio.spec import CarryExecutionError
from vibe_quant.portfolio.ticks import release_tick, sizing_price

if TYPE_CHECKING:
    from vibe_quant.portfolio.sizing import Decision
    from vibe_quant.portfolio.ticks import CarryInputs


def _fill(inputs: CarryInputs, symbol: str, steps: int, price: float, ts: int, reduce_only: bool) -> CarryFill:
    """One execution: price = the release tick, fee = the NT taker formula (§5)."""
    instrument = inputs.instruments[symbol]
    step = inputs.specs[symbol].step
    quantity = instrument.make_qty(Decimal(abs(steps)) * step)
    fee = nt_commission(instrument, quantity, instrument.make_price(price))
    return CarryFill(
        symbol=symbol,
        ts=ts,
        steps=steps,
        qty=float(Decimal(steps) * step),
        price=price,
        fee=fee,
        reduce_only=reduce_only,
    )


def simulate_reference(inputs: CarryInputs) -> CarryRun:
    """Simulate a carry run from the shared rules alone (``engine="reference"``)."""
    spec = inputs.spec
    funding = FundingAccumulator(inputs.calc, inputs.marks, inputs.specs, spec.start_ns)
    fills: list[CarryFill] = []
    decisions: list[Decision] = []
    for decision_ns in inputs.decisions:
        prices: dict[str, float] = {}
        for symbol in spec.symbols:
            tick = sizing_price(inputs.ticks[symbol], decision_ns)
            if tick is not None:
                prices[symbol] = tick.price
        funding.advance(fills, decision_ns)
        equity = equity_at(spec.initial_balance, fills, funding.payments, prices, decision_ns)
        signals: dict[str, float | None] = {}
        excluded: dict[str, str] = {}
        for symbol in spec.symbols:
            value = trailing_funding(inputs.settlements[symbol], decision_ns)
            if value is None:
                signals[symbol] = None
                excluded[symbol] = "funding_hole"
            elif symbol not in prices:
                signals[symbol] = None
                excluded[symbol] = "no_price"
            else:
                signals[symbol] = value
        current = {symbol: steps_held(fills, symbol, decision_ns) for symbol in spec.symbols}
        decision = decide(
            decision_ns, equity, signals, excluded, prices, current, inputs.specs, spec
        )
        decisions.append(decision)
        for order in decision.orders:
            tick = release_tick(order.symbol, inputs.ticks[order.symbol], decision_ns)
            if tick is None:
                raise CarryExecutionError(
                    f"unreleased orders for {order.symbol} at {decision_ns}: "
                    "no own tick after the decision before the run ends"
                )
            fills.append(
                _fill(inputs, order.symbol, order.steps, tick.price, tick.ts, order.reduce_only)
            )
    funding.advance(fills, spec.end_ns)
    # Stable sort on (ts, symbol): ties keep release order (a flip's close before its open).
    ordered = tuple(sorted(fills, key=lambda fill: (fill.ts, fill.symbol)))
    final_steps = {
        symbol: sum(fill.steps for fill in ordered if fill.symbol == symbol)
        for symbol in spec.symbols
    }
    return CarryRun(
        spec=spec,
        decisions=tuple(decisions),
        fills=ordered,
        payments=tuple(funding.payments),
        final_steps=final_steps,
        engine="reference",
    )
