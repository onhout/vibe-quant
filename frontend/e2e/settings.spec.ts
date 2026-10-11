import { expect, test } from "@playwright/test";

const tabs = ["Sizing", "Risk", "Latency", "Database", "System"] as const;

test.describe("Settings page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/settings");
  });

  test("renders page heading", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  });

  test("all 5 tabs are visible", async ({ page }) => {
    for (const tab of tabs) {
      await expect(page.getByRole("tab", { name: tab })).toBeVisible();
    }
  });

  for (const tab of tabs) {
    test(`${tab} tab renders content`, async ({ page }) => {
      await page.getByRole("tab", { name: tab }).click();

      // Radix keeps every panel mounted (inactive ones hidden); the panel is
      // named by its tab, so getByRole picks the active one.
      const tabPanel = page.getByRole("tabpanel", { name: tab });
      await expect(tabPanel).toBeVisible();
      await expect(tabPanel).not.toBeEmpty();
    });
  }

  test("Sizing tab New Config button shows form", async ({ page }) => {
    await page.getByRole("tab", { name: "Sizing" }).click();
    const tabPanel = page.getByRole("tabpanel", { name: "Sizing" });
    await expect(tabPanel).toBeVisible();

    const newConfigBtn = tabPanel.getByRole("button", { name: "New Config" });
    // The config list loads async; allow for a slow backend under parallel load.
    await expect(newConfigBtn.first()).toBeVisible({ timeout: 15_000 });
    await newConfigBtn.first().click();

    // Form should now appear with Name input and method selector
    await expect(tabPanel.getByText("Name").first()).toBeVisible();
    await expect(tabPanel.getByText("Method").first()).toBeVisible();
    await expect(tabPanel.getByRole("button", { name: "Create" })).toBeVisible();
    await expect(tabPanel.getByRole("button", { name: "Cancel" })).toBeVisible();
  });

  test("Sizing tab form Cancel hides form", async ({ page }) => {
    await page.getByRole("tab", { name: "Sizing" }).click();
    const tabPanel = page.getByRole("tabpanel", { name: "Sizing" });

    const newConfigBtn = tabPanel.getByRole("button", { name: "New Config" });
    await newConfigBtn.first().click();
    await expect(tabPanel.getByRole("button", { name: "Create" })).toBeVisible();

    await tabPanel.getByRole("button", { name: "Cancel" }).click();
    // Form should be hidden, New Config button should reappear
    await expect(newConfigBtn.first()).toBeVisible();
  });

  test("switching tabs preserves tab panel rendering", async ({ page }) => {
    await page.getByRole("tab", { name: "Risk" }).click();
    const riskPanel = page.getByRole("tabpanel", { name: "Risk" });
    await expect(riskPanel).toBeVisible();
    await expect(riskPanel).not.toBeEmpty();

    await page.getByRole("tab", { name: "Latency" }).click();
    const latencyPanel = page.getByRole("tabpanel", { name: "Latency" });
    await expect(latencyPanel).toBeVisible();
    await expect(latencyPanel).not.toBeEmpty();

    await page.getByRole("tab", { name: "Risk" }).click();
    await expect(page.getByRole("tabpanel", { name: "Risk" })).toBeVisible();
    await expect(page.getByRole("tabpanel", { name: "Risk" })).not.toBeEmpty();
  });
});
