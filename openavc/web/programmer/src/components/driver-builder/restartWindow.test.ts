import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { RESTART_SECONDS_MAX, RESTART_SECONDS_MIN, restartWindowSeconds } from "./restartWindow";

// The platform's case table (tests/test_restart_window.py runs it against
// drivers/restart_window.py). Read off disk: the corpus lives outside this
// Vite project, and vitest runs with openavc/web/programmer as its cwd.
type Case = { name: string; value: unknown; seconds: number | null };
const corpusPath = resolve(process.cwd(), "../../../tests/fixtures/restart_seconds_cases.json");
const cases: Case[] = JSON.parse(readFileSync(corpusPath, "utf-8")).cases;

describe("restart window parity corpus", () => {
  it("has cases, and the contract's range", () => {
    expect(cases.length).toBeGreaterThan(5);
    expect([RESTART_SECONDS_MIN, RESTART_SECONDS_MAX]).toEqual([1, 600]);
  });

  for (const c of cases) {
    it(c.name, () => {
      expect(restartWindowSeconds(c.value)).toBe(c.seconds ?? undefined);
    });
  }

  it("reads no infinity or NaN as a restart", () => {
    expect(restartWindowSeconds(Infinity)).toBeUndefined();
    expect(restartWindowSeconds(NaN)).toBeUndefined();
  });
});
