import { expect, test } from "@playwright/test";

test.describe("Paper Trading page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/paper-trading");
  });

  test("renders page heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Paper Trading" })).toBeVisible();
  });

  test("renders start paper trading form when idle", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "START PAPER TRADING" })).toBeVisible();
  });

  test("strategy selector is present", async ({ page }) => {
    await expect(page.getByText("Select a strategy...").first()).toBeVisible();
  });

  test("testnet checkbox defaults to checked", async ({ page }) => {
    // Radix checkbox (button[role=checkbox]) labelled "Binance testnet ...".
    await expect(page.getByRole("checkbox", { name: /binance testnet/i })).toBeChecked();
  });

  test("sizing configuration fields are visible", async ({ page }) => {
    await expect(page.getByLabel("Max Leverage")).toBeVisible();
    await expect(page.getByLabel("Max Position %")).toBeVisible();
    await expect(page.getByLabel("Risk Per Trade %")).toBeVisible();
  });

  test("start button disabled without strategy", async ({ page }) => {
    const btn = page.getByRole("button", { name: /start paper trading/i });
    await expect(btn).toBeDisabled();
  });

  test("validated only checkbox filters strategies", async ({ page }) => {
    await expect(page.getByRole("checkbox", { name: /validated only/i })).toBeVisible();
  });
});
