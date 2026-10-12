# H-13 implementation conventions (frozen 2026-10-12, before any evaluator; no values analysed)

Pre-registration: docs/discovery-journal.md "PRE-REGISTRATION H-13" + its amendment. These conventions only pin details the entry leaves open, so that two independent implementations agree exactly. They do not change the hypothesis, thresholds or windows.

Inputs
- Metrics: data/swarm/20261012-nextsteps/metrics/{SYM}.parquet (columns ts [UTC], sum_open_interest, ...), SYM in BTCUSDT ETHUSDT SOLUSDT BNBUSDT.
- Daily closes: `CatalogManager().get_bars(SYM, "1d")` (vibe_quant.data.catalog). Day key D = bar.ts_init // 86_400_000_000_000 (UTC day; the close is the end of day D). Drop a bar whose ts_init % 86_400_000_000_000 is not 86_399_999_000_000 or 0 (partial bar).
- Funding: `vibe_quant.dsl.aux_data.settlement_series(SYM)` → (times ns, rates).

Daily signal (day t = UTC day key)
- OI_i,t: row with ts == t 23:55:00; else the row with the largest ts in [t 23:00:00, t 23:55:00]; else missing. OI <= 0 or NaN = missing.
- dOI_i,t = ln(OI_i,t / OI_i,t-1); missing if either side missing.
- Coins "with data" on t: dOI_i,t present AND close_i,t and close_i,t-1 present. Need >= 3; else D_t, R_t missing (no signal).
- D_t = mean dOI over those coins; R_t = mean (close_i,t / close_i,t-1 - 1) over the same coins.
- pct10_t = numpy.percentile(H, 10, method="linear") where H = non-missing D_s for s in [t-365, t-1] (calendar days); needs len(H) >= 180.
- flush_t = (D_t <= pct10_t) and (R_t < 0).

Events (decision window B = days t with 2022-06-01 <= t <= 2026-09-30)
- Scan t ascending over all days with a signal (A and B; state carries over). If flat and flush_t: enter at close t with the coins "with data" on t, weight 0.25 each (quantities q_i = 0.25 / close_i,t on capital 1), hold to close t+3, exit. Next possible entry: day t+3 (a flush at t+3 enters again). Skip an event whose close t+3 is missing for any held coin (record count).
- An event belongs to B if its entry day t is in B.
- Event net return = sum_i q_i * close_i,t+3 - sum_i q_i * close_i,t ... expressed on capital 1:
  gross = sum_i 0.25 * (close_i,t+3 / close_i,t - 1); cost = 0.0005 * (entry notional + exit notional) = 0.0005 * (0.25 * k + sum_i 0.25 * close_i,t+3 / close_i,t), k = #coins held;
  funding = sum_i sum_{s in settlements_i, T_t < s <= T_t+3} q_i * mark_i(s) * rate_i(s), with T_x = (x+1) * 86_400e9 (close time of day x) and mark_i(s) = close_i of day (s - 1) // 86_400e9 (last daily close at or before s; a settlement exactly at a close uses that close).
  net = gross - cost - funding.

Daily series for Sharpe (B only, every calendar day 2022-06-01..2026-09-30)
- r_d = 0 when flat at close d-1. When held over (close d-1, close d]: r_d = (V_d - V_{d-1} - c_d - f_d) / V_{d-1}, V_x = sum_i q_i close_i,x; c_d = entry cost 0.0005 * V_t if d-1 == t (entry close) plus exit cost 0.0005 * V_{t+3} if d == t+3; f_d = funding with settlements in (T_{d-1}, T_d].
- Sharpe = mean(r) / std(r, ddof=1) * sqrt(365).

Nulls (B, 20,000 draws, rng = numpy.random.default_rng(13))
- Candidate start days: primary = days t in B with R_t defined and R_t < 0 and closes t..t+3 present for every coin "with data" on t; secondary = same without the R_t < 0 condition.
- One draw: order = rng.permutation(candidates); accept a day if its hold [t, t+3] overlaps no accepted hold (|t - t'| >= 3); stop at n accepted (n = #B events); null mean = mean of their net returns (same formula). Primary draws first (20,000), then secondary (20,000), same rng object.
- p = (1 + #{null mean >= observed mean}) / 20001.

Report (JSON): n_B, mean_net_B, p_primary, p_secondary, sharpe_B, events skipped, coverage counts, each event (t, coins, gross, cost, funding, net), PASS/REJECT per condition (1)-(4).
