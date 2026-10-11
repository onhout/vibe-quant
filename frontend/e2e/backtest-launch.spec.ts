import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

// 127.0.0.1, not localhost: uvicorn binds IPv4 only (see discovery-flow.spec.ts).
const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";

/**
 * A canonical StrategyDSL with EMA periods, so `sweepableParamsFor` yields
 * ema_fast_period / ema_slow_period and the sweep toggle renders. Created via the API
 * in beforeAll so these tests do not depend on any strategy already in the DB copy.
 */
const SWEEPABLE_DSL = {
  name: "e2e_sweep",
  timeframe: "1h",
  indicators: {
    ema_fast: { type: "EMA", period: 12, source: "close" },
    ema_slow: { type: "EMA", period: 26, source: "close" },
  },
  entry_conditions: {
    long: ["ema_fast crosses_above ema_slow"],
    short: ["ema_fast crosses_below ema_slow"],
  },
  exit_conditions: {
    long: ["ema_fast crosses_below ema_slow"],
    short: ["ema_fast crosses_above ema_slow"],
  },
  stop_loss: { type: "fixed_pct", percent: 2.0 },
  take_profit: { type: "risk_reward", risk_reward_ratio: 2.0 },
};

/** Created once per worker; selected by its unique name, deleted in afterAll. */
let sweepStrategyId = 0;
let sweepStrategyName = "";

async function createSweepableStrategy(request: APIRequestContext): Promise<void> {
  sweepStrategyName = `e2e-sweep-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const resp = await request.post(`${API}/api/strategies`, {
    data: {
      name: sweepStrategyName,
      dsl_config: SWEEPABLE_DSL,
      strategy_type: "trend_following",
    },
  });
  const body = await resp.text();
  expect(resp.status(), body).toBe(201);
  sweepStrategyId = (JSON.parse(body) as { id: number }).id;
}

test.describe("Backtest Launch page", () => {
  test.beforeAll(async ({ request }) => {
    await createSweepableStrategy(request);
  });

  test.afterAll(async ({ request }) => {
    if (sweepStrategyId) await request.delete(`${API}/api/strategies/${sweepStrategyId}`);
  });

  test.beforeEach(async ({ page }) => {
    await page.goto("/backtest");
  });

  test("renders launch form", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Backtest Launch" })).toBeVisible();
  });

  test("renders preflight status component", async ({ page }) => {
    await expect(page.getByRole("button", { name: /preflight/i })).toBeVisible();
  });

  test("renders active jobs panel", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Active Jobs" })).toBeVisible();
  });

  test("strategy selector renders with placeholder", async ({ page }) => {
    await expect(page.getByLabel("Strategy")).toBeVisible();
    await expect(page.getByText("Select a strategy...").first()).toBeVisible();
  });

  test("mode toggle shows Screening and Validation buttons", async ({ page }) => {
    await expect(page.getByRole("button", { name: "screening", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "validation", exact: true })).toBeVisible();
  });

  test("clicking Validation mode shows validation settings", async ({ page }) => {
    await page.getByRole("button", { name: "validation", exact: true }).click();
    await expect(page.getByText("Validation Settings")).toBeVisible();
    await expect(page.getByLabel("Latency Preset")).toBeVisible();
  });

  test("clicking Screening mode hides validation settings", async ({ page }) => {
    // Switch to validation first
    await page.getByRole("button", { name: "validation", exact: true }).click();
    await expect(page.getByText("Validation Settings")).toBeVisible();

    // Switch back to screening
    await page.getByRole("button", { name: "screening", exact: true }).click();
    await expect(page.getByText("Validation Settings")).toBeHidden();
  });

  test("symbols section renders", async ({ page }) => {
    await expect(page.getByText(/Symbols \(\d+ selected\)/)).toBeVisible();
  });

  test("date preset buttons are visible", async ({ page }) => {
    for (const label of ["1M", "3M", "6M", "1Y", "2Y"]) {
      await expect(page.getByRole("button", { name: label, exact: true })).toBeVisible();
    }
  });

  test("date preset populates start and end dates", async ({ page }) => {
    await page.getByRole("button", { name: "3M", exact: true }).click();

    // The range pickers are popover-trigger buttons; the preset fills both labels.
    await expect(page.locator("#start-date")).toHaveText(/\d{4}/);
    await expect(page.locator("#end-date")).toHaveText(/\d{4}/);
  });

  test("launch button is disabled without strategy selected", async ({ page }) => {
    const launchBtn = page.getByRole("button", { name: "Launch Screening", exact: true });
    await expect(launchBtn).toBeDisabled();
  });

  test("initial balance and leverage inputs render with defaults", async ({ page }) => {
    const balanceInput = page.getByLabel("Initial Balance (USD)");
    const leverageInput = page.getByLabel("Leverage (1-125)");
    await expect(balanceInput).toBeVisible();
    await expect(leverageInput).toBeVisible();
    await expect(balanceInput).toHaveValue("10000");
    await expect(leverageInput).toHaveValue("10");
  });

  // The sweep toggle appears only for a strategy with sweepable params (EMA periods here).
  async function selectSweepableStrategy(page: Page) {
    const name = new RegExp(`^${sweepStrategyName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\(v`);
    await page.locator("#strategy-select").click();
    await page.getByRole("option", { name }).click();
  }

  test("sweep toggle is disabled with a failure note when the indicator catalog fails", async ({
    page,
  }) => {
    await page.route("**/api/indicators/catalog", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "catalog unavailable" }),
      }),
    );
    await page.reload();
    await selectSweepableStrategy(page);

    const toggle = page.getByRole("button", { name: "Disabled", exact: true });
    await expect(
      page.getByText("Indicator catalog failed to load: sweeps are unavailable."),
    ).toBeVisible();
    await expect(toggle).toBeDisabled();
  });

  test("sweep toggle is enabled once the indicator catalog loads", async ({ page }) => {
    await selectSweepableStrategy(page);

    const toggle = page.getByRole("button", { name: "Disabled", exact: true });
    // Wait for the catalog to load (toggle enables) BEFORE asserting absence of the notes,
    // otherwise the count-0 assertion can pass while the catalog is still loading.
    await expect(toggle).toBeEnabled();
    await expect(page.getByText("Loading indicator catalog...")).toHaveCount(0);
    await expect(page.getByText("Indicator catalog failed to load")).toHaveCount(0);
  });
});
