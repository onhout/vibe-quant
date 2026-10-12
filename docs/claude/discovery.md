# Discovery pipeline

Read before running discovery, interpreting or comparing its results, or changing discovery, fitness or the overfitting gates. Experiment history: [`../discovery-journal.md`](../discovery-journal.md).

## Current behaviour
- Discovery and screening share one code path (`NTScreeningRunner` → `StrategyCompiler`); a champion replays exactly within one run when `eval_windows=1`.
- **Fitness** = 35% Sharpe + 25% (1 − MaxDD) + 20% PF + 20% Return; 0 below 50 trades.
- **`eval_windows` (default 3) stores WORST-of-N sub-window metrics** (min Sharpe/return/PF, max DD; each window needs ≥ max(1, min_trades // (2N)) trades). A full-window replay legitimately differs (`ReplayResponse.metrics_note` explains it).
- **Gates fail closed** (0 champions + `guardrail_rejections`); every run keeps a default 20% holdout used once as the final gate.
- **Bootstrap-CI floor** (statistic = t-stat mean/std·√n): 1m 0.5, 4h/1d 0.0 (1.0 is unpassable at ~50–180 trades/yr). Every champion forced past it with `no_bootstrap_ci=true` and then validated collapsed (Batch 41: 5.40 → −2.78; Batch 43 RAMS: 0.59 → −0.36); treat a flagged strategy as overfit.
- **DSR gate N is cumulative:** this run's evaluated strategies + prior non-mock discovery runs with the same timeframe and an overlapping train window (`notes.dsr_trials.gate_n/prior_n/prior_run_ids`). On a DB error it falls back to the run-only N and records `prior_error: true`.
- **Buy-and-hold gate (fail closed):** champions must beat the directional equal-weight, daily-rebalanced buy-and-hold Sharpe of their symbols over the train window, measured on the mark-to-market daily series (`notes.buy_hold_gate`); `symbol_agg=worst` also checks each symbol against its own benchmark. Skips only for a non-NT backtest fn; `--no-buy-hold-gate` disables.
- **Seed:** `--seed N` (CLI; auto-drawn otherwise, persisted in `notes.seed`) replays a run exactly with the same config and code.
- **Fails loudly:** an all-errored eval batch raises `DiscoveryEvaluationError` → run `failed`, not "0 champions".
- Validation's consistency check (`validation/consistency.py`) flags screening→validation collapse against the same window; holdout-validated champions are compared on their holdout metrics.
- **Compiler version hash** (`compiler_version_hash()`, stored in run notes for staleness) changes with indicator/codegen changes: `bd recall discovery:compiler-hash`. Champion rankings: `bd recall discovery:champions`.
- **1m is slow:** Rust-native indicators (SMA/EMA/CCI/STOCH/ATR) run ~10× faster than pandas-path ones (ADX/MACD/BBANDS/KAMA).

## Comparability breaks

Results from before each date are NOT comparable with later ones:
- **2026-10-11** (pandas-ta-classic 0.6.52 -> 0.8.32, job 20261011-grind): indicators computed through `compute_fn` change. ADX: first valid bar 13 -> 27 for period 14 (TA-Lib seeding of DI, rma(dx) seeded at the first valid dx), early values differ up to ~2 ADX points and converge to ~1e-14 later; RSI first valid bar +1 (the price diff has no bar 0, so the smoothing seed window now starts at bar 1) and ATR/SQUEEZE_RATIO first valid bar +1 (true range has no bar 0); WMA/ATR last-digit (1e-14) diffs; STOCH is index-aligned (full-length series, same tail values); KAMA/MACD/BBANDS/KC/Donchian/VWAP/MFI/CCI unchanged on the probe data. Strategy 239 (BTCUSDT 2024-01-01..2026-03-17 and 2024-H1) and strategy 67 (ADX+RSI, 4h, 2024-H1) metrics are bit-identical (thresholds/warmup luck); strategy 192 (ADX+STOCH, 1m, 2024-H1) moves sharpe -1.5593 -> -1.5300 and PF 0.7761 -> 0.7806. Treat any strategy using ADX/RSI/ATR/SQUEEZE_RATIO via compute_fn as not comparable across this date. The numpy ports (ADX, KAMA) were re-derived bit-identically against 0.8.32.
- **2026-10-10** (job 20261010-edge-engine): per-instrument fill release in screening, open-position marking, Binance instrument specs (BTC min notional 50), multi-symbol validation no longer fills at the signal close. Single-symbol validation unchanged.
- **2026-10-03** (deep audit, `docs/reviews/2026-10-02-deep-audit.md`): trade-based PF capped at 100, mark-to-market max DD, funding in screening, adaptive same-bar ordering, TP/SL at limit/trigger, round-down sizing; worst-of-N windows, holdout gate, fail-closed gates.
- **2026-07-09** (`11c5f00`): screening loads only the strategy timeframe (an NT data-loading bug fed all timeframes); discovery scores ≤ run 854 are stale. Runs before `2944ad3` (`pos.side` bug) are invalid.
