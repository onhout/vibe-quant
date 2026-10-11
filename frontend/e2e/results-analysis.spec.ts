import { expect, test } from "@playwright/test";

test.describe("Results Analysis page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/results");
  });

  test("renders page heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Results Analysis" })).toBeVisible();
  });

  test("renders runs table with mode filters", async ({ page }) => {
    // The page is a single runs list; filters are comboboxes above the table.
    await expect(page.getByRole("combobox").filter({ hasText: "Validation only" })).toBeVisible();
    // The runs list is div-based (no table/columnheader roles); wait for a header
    // to prove the list rendered.
    await expect(page.getByText("Sharpe").first()).toBeVisible({ timeout: 20_000 });
  });
});
