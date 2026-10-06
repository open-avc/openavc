/**
 * The IDE's half of the parameter-name parity check.
 *
 * Runs the SAME corpus as tests/test_param_label_parity.py, over paramLabel.ts
 * instead of openavc/drivers/param_labels.py, so a field reads the same on a
 * form and in the device audit's trace.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { paramLabel } from "./paramLabel";

type Case = { name: string; key: string; label?: unknown; expected: string };

// Read off disk rather than imported: the corpus lives outside this Vite
// project, and vitest runs with openavc/web/programmer as the cwd.
const corpusPath = resolve(process.cwd(), "../../../tests/fixtures/param_label_cases.json");
const cases: Case[] = JSON.parse(readFileSync(corpusPath, "utf-8")).cases;

describe("parameter name parity corpus", () => {
  it("has cases", () => {
    expect(cases.length).toBeGreaterThan(20);
  });

  for (const c of cases) {
    it(c.name, () => {
      const def = "label" in c ? { type: "string", label: c.label } : { type: "string" };
      expect(paramLabel(c.key, def as never)).toBe(c.expected);
    });
  }

  it("reads the key when there is no definition", () => {
    expect(paramLabel("input_id", undefined)).toBe("Input ID");
  });
});
