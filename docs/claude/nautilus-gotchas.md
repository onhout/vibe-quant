# NautilusTrader gotchas

Read before writing or changing code that builds or runs a NautilusTrader engine or node: screening/validation runners, the paper `TradingNode`, fill/latency models, `BacktestDataConfig`, generated strategy templates. Each item cost real debugging time.

- **`BacktestDataConfig.data_cls` must be the class object** (`from nautilus_trader.model.data import Bar`), not the import string: `config.query` compares `data_cls is Bar`, so a string silently disables `bar_types`/`bar_spec` narrowing and loads the whole catalog (~300× data, changed fills). Reported upstream: nautechsystems/nautilus_trader#5291.
- **Call `vibe_quant.nt_compat.retain_log_guard(engine)` before disposing any engine.** Creating a `BacktestEngine`/`BacktestNode` after disposing one in the same process otherwise hard-aborts (the Rust logger inits once; no Python traceback).
- **NT ≥ 1.226 config decoding rejects unknown fields:** forward only params the generated `StrategyConfig` declares (`__struct_fields__` filter in both runners). Run-level knobs (`initial_balance`, `leverage`) belong on the venue.
- **`node.build()` swallows engine-build exceptions** (`get_engine()` → `None`, "engine not found"). Set `BacktestRunConfig(raise_exception=True)` in runner code.
- `BacktestResult.elapsed_time` is the simulated window in seconds; `Iterations` ≈ bars processed (far above the expected bar count = a data-loading bug).
- An invalid `TraderId` (e.g. `paper_1`, no `-`) aborts the process from Rust; validate with `vibe_quant/paper/config.py`'s regex first.
- `TradingState.REDUCING` does not stop a flat account from opening; paper uses its own `TradingGuard` (`paper/guard.py`).
- `BacktestEngine.run()` stops all strategies when it ends — assert mid-run state from a scheduled actor.
- Generated strategy modules are content-addressed (`{name}_{hash}`): use `module.__name__`, never rebuild the path from `dsl.name`.
- ADX stays on the pandas path (NT's `DirectionalMovement.value` is always 0).
- NT's `LatencyModel` releases queued orders venue-wide — keep it off bar data; the generated strategy's `command_release` outbox releases per instrument.
- A `BacktestDataConfig` for an instrument missing from the catalog only warns; synthetic feeds need an explicit coverage check (`fill_ticks` does).
- DONCHIAN `upper` includes the current bar's high (NT updates before `on_bar`), so `close crosses_above donchian.upper` never fires — use `upper_prev`/`lower_prev`.
