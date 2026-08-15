/**
 * The IDE's half of the monitor parity check.
 *
 * Runs the SAME corpus as tests/test_monitor_parity.py, over monitorHelpers.ts
 * instead of openavc/core/monitors.py. If the two ever answer differently about
 * a reading, one of these two suites goes red — which is the point: the tile a
 * person is looking at and the alert arriving on their phone come from one
 * declaration and must not disagree about it.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  ABNORMAL,
  NORMAL,
  NO_VALUE,
  UNSET,
  monitorReading,
  monitorStatus,
  monitorWord,
} from "./monitorHelpers";
import type { MonitorConfig } from "./types";

type Case = {
  name: string;
  monitor: MonitorConfig;
  value: unknown;
  status: string;
  word: string | null;
  reading: string;
};

// Read off disk rather than imported: the corpus lives outside this Vite
// project (it belongs to the Python suite just as much) and `import.meta.url`
// is not a file URL under jsdom. vitest runs with its config's directory as the
// cwd, which is openavc/web/programmer.
const corpusPath = resolve(
  process.cwd(), "../../../tests/fixtures/monitor_parity_cases.json",
);
const cases: Case[] = JSON.parse(readFileSync(corpusPath, "utf-8")).cases;

describe("monitor parity corpus", () => {
  it("has cases", () => {
    expect(cases.length).toBeGreaterThan(10);
  });

  for (const c of cases) {
    it(c.name, () => {
      expect(monitorStatus(c.monitor, c.value)).toBe(c.status);
      expect(monitorWord(c.monitor, c.value)).toBe(c.word);
      expect(monitorReading(c.monitor, c.value)).toBe(c.reading);
    });
  }

  it("reaches every verdict", () => {
    const produced = new Set(cases.map((c) => monitorStatus(c.monitor, c.value)));
    expect([...produced].sort()).toEqual([ABNORMAL, NORMAL, NO_VALUE, UNSET].sort());
  });

  it("never renders a boolean as true or false", () => {
    for (const c of cases) {
      expect(["True", "False", "true", "false"]).not.toContain(
        monitorReading(c.monitor, c.value),
      );
    }
  });
});
