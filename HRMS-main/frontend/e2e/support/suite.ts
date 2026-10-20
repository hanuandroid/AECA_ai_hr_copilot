// Shared test wiring: every spec skips (reported as skipped = NOT_TESTED) when the disposable stack or the synthetic
// credentials are not available, instead of passing.
import fs from "node:fs";
import path from "node:path";

import { test as base } from "@playwright/test";

import { apiContext, fixtures, tokenFor, type RuntimeFixtures } from "./api";
import { E2E, MISSING_CREDENTIALS, type SeededRole } from "./env";

/** Checked when a test runs (global setup writes the file after the specs are collected). */
export const suiteReady = () => fs.existsSync(path.join(E2E.runtimeDir, "fixtures.json"));
export const NOT_READY_REASON = `${MISSING_CREDENTIALS}, or global setup did not complete`;

type Fixtures = {
  data: RuntimeFixtures;
  tokens: Record<SeededRole, string>;
};

export const test = base.extend<Fixtures>({
  data: async ({}, use) => {
    base.skip(!suiteReady(), NOT_READY_REASON);
    await use(fixtures());
  },
  tokens: async ({}, use) => {
    base.skip(!suiteReady(), NOT_READY_REASON);
    const api = await apiContext();
    const tokens = {
      ADMIN: await tokenFor(api, "ADMIN"),
      MANAGER: await tokenFor(api, "MANAGER"),
      EMPLOYEE: await tokenFor(api, "EMPLOYEE"),
    };
    await use(tokens);
    await api.dispose();
  },
});

export { expect } from "@playwright/test";
