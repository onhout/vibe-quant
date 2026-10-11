# E2E tests (Playwright)

Specs in this directory drive the Vite app against a running backend.

## One command

`scripts/agents/e2e.sh <worktree>` boots the throwaway ui-check stack
(backend `:8001` + Vite `:5188` on a **copy** of the state DB), runs the
`chromium` project and, when it started the stack itself, ALWAYS tears it down
(pass, fail, Ctrl-C). Its exit code is Playwright's — except `3` when `up` found
`:8001`/`:5188` busy (a live stack owned by another agent), which it leaves
running instead of killing it.

```bash
scripts/agents/e2e.sh /path/to/worktree                       # everything
scripts/agents/e2e.sh /path/to/worktree e2e/settings.spec.ts  # one file
scripts/agents/e2e.sh /path/to/worktree -g "Settings page"    # one scenario
```

It exports `E2E_BASE_URL=http://localhost:5188` and
`E2E_API_URL=http://127.0.0.1:8001` — IPv4 on purpose: uvicorn binds IPv4 only
and Node resolves `localhost` to `::1` first, which made the API specs
`ECONNREFUSED` under load. It never runs `pnpm install`; `ui-check.sh` symlinks
`node_modules` from the main checkout.

To drive a stack you started yourself, call Playwright directly:

```bash
scripts/agents/ui-check.sh up <worktree>
cd <worktree>/frontend
E2E_BASE_URL=http://localhost:5188 E2E_API_URL=http://127.0.0.1:8001 \
  pnpm exec playwright test --project=chromium
scripts/agents/ui-check.sh down <worktree>   # always run this, pass or fail
```

`E2E_BASE_URL` overrides `baseURL` and omits the `pnpm dev` `webServer`; without
it the config keeps the default behaviour (`http://localhost:5173` + `pnpm dev`).
When `E2E_BASE_URL` is set the worker count is capped (`E2E_WORKERS`, default 3,
used for any empty/invalid value): the stack runs a single uvicorn, so a
full-core fan-out starves it.

## Projects

A single `chromium` project runs every spec. `GET /api/data/coverage` used to
block the API event loop and forced the coverage check into a separate, deferred
project; the endpoint now runs in the API threadpool (vibe-quant-11uqu), so that
project was folded back — the coverage check lives in `data-management.spec.ts`
with its own 60 s timeout.

## Specs

- `discovery-flow.spec.ts` — discovery launch (Select All; UI + `/api/discovery/indicator-pool`
  names must equal the fixed GA pool list in the spec; tiny config; the run is then polled
  and must stay running-with-progress/completed, never `failed`) -> champion export ->
  validation launch of the exported strategy on BTCUSDT 1m / 6M (polled to `completed`,
  `error_message` is shown on failure) -> results render (headline metrics + equity chart).
  Takes ~1 min. Teeth: it fails if the pool names mismatch the GA (vibe-quant-rubp1) or the
  validation subprocess crashes after a 201 (vibe-quant-wrlea). Update `GA_INDICATOR_POOL`
  when an indicator is added to / removed from the GA pool.
- `data-management.spec.ts` — the /data page: ingest form, download history and the
  symbol-coverage table (the last one exercises the archive scan; 60 s timeout).
- `paper-trading.spec.ts` — the "Validated only" checkbox is asserted as a filter: the Strategy
  select's options must equal the API-derived set (strategies with a completed validation run)
  with the box checked, all strategies unchecked, and the validated set again. The Auto-Stop
  inputs must render their defaults (20 / 5 / 5 / 3).
- `results-analysis.spec.ts` — the Overfitting Filters card on `/results/<id>`: the first discovery
  run on the DB copy with gate data (picked by data, not id) must show the PASS/FAIL/N/A verdict for
  Walk-Forward (discovery verdict, else efficiency > 0.5), Cross-Window and Cross-Regime, and the
  value for Purged K-Fold, DSR and Bootstrap (their thresholds are covered by
  `src/__tests__/audit-results-display.test.ts`). Without a gate-data run the test skips with a
  message. A completed validation run (no gate data) must show "Not Run" and N/A rows.
- Page specs: `backtest-launch`, `discovery`, `navigation`, `paper-trading`,
  `results-analysis`, `settings`, `strategy-management`, `workflow`.

`test-results/` and `playwright-report/` are gitignored. All of `e2e/**` is
linted by biome (`cd frontend && pnpm exec biome check e2e`).
