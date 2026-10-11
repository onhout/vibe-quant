import { expect, test } from "@playwright/test";

test.describe("Strategy Management page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/strategies");
  });

  test("renders page heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Strategy Management" })).toBeVisible();
  });

  test("New Strategy button opens dialog", async ({ page }) => {
    await page.getByRole("button", { name: "New Strategy" }).click();

    // Dialog should appear with form fields
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("create dialog shows Blank Strategy and Create/Cancel buttons", async ({ page }) => {
    await page.getByRole("button", { name: "New Strategy" }).click();
    const dialog = page.getByRole("dialog");

    await expect(dialog.getByText("Create Strategy")).toBeVisible();
    await expect(dialog.getByText("Blank Strategy")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Create" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeVisible();
  });

  test("create dialog closes on Cancel", async ({ page }) => {
    await page.getByRole("button", { name: "New Strategy" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();

    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("dialog")).toBeHidden();
  });

  test("Wizard button opens wizard view", async ({ page }) => {
    await page.getByRole("button", { name: /wizard/i }).click();

    // Wizard replaces the page content -- should see wizard UI
    await expect(page.getByText(/wizard|step|template/i).first()).toBeVisible();
  });

  test("search input is rendered with placeholder", async ({ page }) => {
    await expect(page.getByPlaceholder("Search…")).toBeVisible({ timeout: 20_000 });
  });

  test("search input accepts text", async ({ page }) => {
    const searchInput = page.getByPlaceholder("Search…");
    await searchInput.fill("momentum");
    await expect(searchInput).toHaveValue("momentum");
  });

  test("type filter defaults to All", async ({ page }) => {
    await expect(page.getByRole("button", { name: "ALL", exact: true })).toBeVisible();
  });

  test("sort dropdown defaults to Last Updated", async ({ page }) => {
    const sort = page.getByRole("combobox").first();
    await expect(sort).toBeVisible();
    await expect(sort).toContainText(/Last Updated/i);
  });
});
