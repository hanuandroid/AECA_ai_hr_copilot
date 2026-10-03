import path from "node:path";

import { defineConfig } from "vitest/config";

// Unit/component tests only (no E2E framework in this repository). JSX via Vite's built-in transform.
export default defineConfig({
  oxc: { jsx: { runtime: "automatic" } },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
  test: {
    environment: "jsdom",
    pool: "threads", // the forks pool times out starting workers on this Windows host
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
