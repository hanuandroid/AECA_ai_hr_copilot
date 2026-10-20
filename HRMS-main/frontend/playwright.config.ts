// Playwright role-access suite (e2e/). Runs ONLY against the disposable stack in ../docker-compose.e2e.yml
// (web http://127.0.0.1:13000, API http://127.0.0.1:18000); see e2e/README.md. Vitest never collects these files
// (vitest.config.mts includes tests/** only).
//
// Failure evidence: screenshots on failure only. Traces and videos are OFF by default because a trace records every
// request header (tokens, the step-up password) and Playwright cannot mask it; E2E_TRACE=1 enables traces for local
// debugging of the synthetic stack only - never share such a trace.
import { defineConfig, devices } from "@playwright/test";

import { E2E } from "./e2e/support/env";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false, // one shared synthetic database; tests that change data are ordered within their files
  workers: 1,
  forbidOnly: true,
  retries: 0, // a flaky authorization result must be investigated, never retried into a pass
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: E2E.webUrl,
    screenshot: "only-on-failure",
    trace: process.env.E2E_TRACE === "1" ? "retain-on-failure" : "off",
    video: "off",
  },
  projects: [{ name: "chromium" }],
});
