import { expect, test } from "@playwright/test";

test.describe("Data Management page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/data");
  });

  test("renders page heading and ingest form", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Data Management" })).toBeVisible();
    await expect(page.getByText("Data Ingest")).toBeVisible();
    await expect(page.getByRole("button", { name: "Start Download" })).toBeVisible();
  });

  test("renders download history section", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Download History" })).toBeVisible();
  });

  test("renders the symbol coverage table", async ({ page }) => {
    // GET /api/data/coverage walks the full archive and was once the reason this
    // spec lived in its own project; it now runs in the API threadpool
    // (vibe-quant-11uqu), so it no longer blocks the event loop. Allow a
    // generous timeout for the cold archive scan under parallel load.
    test.setTimeout(60_000);
    await expect(page.getByRole("heading", { name: "Symbol Coverage" })).toBeVisible({
      timeout: 55_000,
    });
    await expect(page.getByText("Total Symbols")).toBeVisible();

    const table = page.locator("table").first();
    await expect(table).toBeVisible();
    await expect(table.getByRole("columnheader", { name: /symbol/i })).toBeVisible();
  });
});
