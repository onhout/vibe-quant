import { expect, type Page, test } from "@playwright/test";

test.describe("Backtest Launch page", () => {
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

  // The sweep toggle appears only for a strategy with sweepable params (ga_macd_willr_bidir).
  async function selectSweepableStrategy(page: Page) {
    await page.locator("#strategy-select").click();
    await page.getByRole("option", { name: /^ga_macd_willr_bidir \(v/ }).click();
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

    await expect(page.getByRole("button", { name: "Disabled", exact: true })).toBeEnabled();
    await expect(page.getByText("Indicator catalog failed to load")).toHaveCount(0);
  });
});
