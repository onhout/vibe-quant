import { expect, test } from "@playwright/test";

const pages = [
  { nav: "Strategy Management", path: "/strategies", heading: "Strategy Management" },
  { nav: "Discovery", path: "/discovery", heading: "Discovery" },
  { nav: "Backtest Launch", path: "/backtest", heading: "Backtest" },
  { nav: "Results Analysis", path: "/results", heading: "Results Analysis" },
  { nav: "Paper Trading", path: "/paper-trading", heading: "Paper Trading" },
  { nav: "Data Management", path: "/data", heading: "Data" },
  { nav: "Settings", path: "/settings", heading: "Settings" },
] as const;

test.describe("Sidebar navigation", () => {
  for (const page of pages) {
    test(`navigates to ${page.nav}`, async ({ page: p }) => {
      await p.goto("/");

      // Click sidebar link
      await p.getByRole("link", { name: page.nav }).click();
      await expect(p).toHaveURL(new RegExp(page.path));

      // Verify heading renders
      await expect(p.getByRole("heading", { name: page.heading }).first()).toBeVisible();
    });
  }

  test("no console errors on page load", async ({ page: p }) => {
    const errors: string[] = [];
    p.on("console", (msg) => {
      if (msg.type() === "error") {
        errors.push(msg.text());
      }
    });

    await p.goto("/strategies");
    await expect(p.getByRole("heading", { name: "Strategy Management" })).toBeVisible();
    // The page holds a jobs WebSocket open, so `networkidle` never fires; wait
    // for the strategy-list query to settle instead — the search input only
    // renders once the list has loaded (no skeleton).
    await expect(p.getByPlaceholder("Search…")).toBeVisible({ timeout: 20_000 });

    // Filter out expected API errors (backend not running in E2E)
    const unexpected = errors.filter(
      (e) => !e.includes("Failed to fetch") && !e.includes("ERR_CONNECTION_REFUSED"),
    );
    expect(unexpected).toEqual([]);
  });
});
