// Production hardening (closure): next@15.5.27 pins postcss 8.4.31, which is affected by GHSA-6g55-p6wh-862q,
// GHSA-r28c-9q8g-f849, GHSA-qx2v-qp2m-jg93 and GHSA-fxqj-rqcc-2cmp (all fixed by >= 8.5.23). package.json
// overrides Next's postcss with the project's own ("$postcss"). npm can silently keep a stale nested copy (it did on
// the first attempt), so this test checks the package Next actually resolves, not the audit output.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const MIN_PATCHED_POSTCSS = [8, 5, 23];

function version(packageJson: string): number[] {
  return (JSON.parse(readFileSync(packageJson, "utf8")).version as string).split(".").map(Number);
}

function atLeast(actual: number[], minimum: number[]): boolean {
  for (let i = 0; i < minimum.length; i += 1) {
    if (actual[i] !== minimum[i]) return actual[i] > minimum[i];
  }
  return true;
}

describe("dependency pins", () => {
  const root = path.resolve(import.meta.dirname, "..");
  const fromRoot = createRequire(path.join(root, "package.json"));
  const fromNext = createRequire(fromRoot.resolve("next/package.json"));

  it("Next resolves a patched postcss, the same copy as the project", () => {
    const nextPostcss = fromNext.resolve("postcss/package.json");
    expect(atLeast(version(nextPostcss), MIN_PATCHED_POSTCSS)).toBe(true);
    expect(nextPostcss).toBe(fromRoot.resolve("postcss/package.json"));
  });

  it("keeps the override narrow: only Next's postcss is overridden", () => {
    const manifest = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
    expect(manifest.overrides).toEqual({ next: { postcss: "$postcss" } });
    expect(manifest.dependencies.next).toBe("15.5.27");
  });
});
