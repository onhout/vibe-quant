import { defineConfig, devices } from "@playwright/test";

// When E2E_BASE_URL is set the caller already runs the stack (e.g. the
// ui-check script on :8001/:5188) — target it and do NOT start a dev server.
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

// Against the shared ui-check stack a single uvicorn process serves every
// spec, so cap concurrency (a full-core fan-out starved it → ECONNREFUSED on
// the discovery-flow API reads). Override with E2E_WORKERS to measure; an
// empty/invalid value falls back to 3 (a bare Number("") is 0, which left
// Playwright with no workers at all).
const onUiCheck = !!process.env.E2E_BASE_URL;
const requestedWorkers = Number.parseInt(process.env.E2E_WORKERS ?? "", 10);
const workers = process.env.CI ? 1 : onUiCheck ? (requestedWorkers > 0 ? requestedWorkers : 3) : undefined;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers,
  reporter: [["html", { open: "never" }]],
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Default (no E2E_BASE_URL) is unchanged: start/reuse `pnpm dev` on :5173.
  ...(process.env.E2E_BASE_URL
    ? {}
    : {
        webServer: {
          command: "pnpm dev",
          url: baseURL,
          reuseExistingServer: !process.env.CI,
        },
      }),
});
