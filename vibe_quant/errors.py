"""Shared run-level error types."""

from __future__ import annotations


class DataUnavailableError(ValueError):
    """Input data a run needs is missing (bars, funding archive, ...).

    A configuration/data problem, not a backtest failure: runners re-raise it
    instead of masking it as a -inf or zero-trade result.
    """


class LiquidationIntegrityError(DataUnavailableError):
    """The mode-on liquidation path failed (MARK bars, a tier table or a ledger it needs).

    Raised once, by :meth:`vibe_quant.screening.nt_runner.NTScreeningRunner._extract_metrics`,
    around the MARK scan and the truncated-ledger recompute, so a run that asked for
    liquidation fails closed instead of silently scoring -inf. It is transported by the
    multiprocessing pool, so it must pickle with the same message.
    """

    @classmethod
    def wrap(cls, exc: BaseException, mode: str) -> LiquidationIntegrityError:
        """Wrap ``exc`` with the run's mode and the original exception class name."""
        return cls(f"liquidation_mode={mode!r}: {type(exc).__name__}: {exc}")
