/**
 * The IDE's half of the condition parity check.
 *
 * Runs the SAME corpus as tests/test_condition_parity.py, over conditionEval.ts
 * instead of openavc/core/condition_eval.py. If the two ever answer
 * differently, one of the two suites goes red: the trigger editor's "Evaluated
 * now" and the trigger that fires come from one rule and must not disagree.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { evalOperator } from "./conditionEval";

type Case = {
  name: string;
  op: string;
  actual: unknown;
  target: unknown;
  expected: boolean | "error";
};

// Read off disk: the corpus lives outside this Vite project (it belongs to the
// Python suite just as much). vitest runs from openavc/web/programmer.
const corpusPath = resolve(
  process.cwd(), "../../../tests/fixtures/condition_parity_cases.json",
);
const CASES: Case[] = JSON.parse(readFileSync(corpusPath, "utf-8")).cases;

describe("evalOperator answers as the runtime does", () => {
  it.each(CASES.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    if (c.expected === "error") {
      expect(() => evalOperator(c.op, c.actual, c.target)).toThrow(/Unknown condition operator/);
    } else {
      expect(evalOperator(c.op, c.actual, c.target)).toBe(c.expected);
    }
  });

  it("reads a key that is not in state as not reported", () => {
    expect(evalOperator("ne", undefined, "on")).toBe(false);
    expect(evalOperator("falsy", undefined, null)).toBe(true);
  });
});
