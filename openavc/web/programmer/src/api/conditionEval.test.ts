/**
 * The IDE's half of the condition parity check.
 *
 * Runs the SAME corpus as tests/test_condition_parity.py, over conditionEval.ts
 * instead of openavc/core/condition_eval.py, in both of the rule's modes. If
 * the two ever answer differently, one of the two suites goes red: the trigger
 * editor's "Evaluated now", a page review and the trigger that fires come from
 * one rule and must not disagree.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DECIDE, SURFACE, evalOperator, matchKey, type ConditionMode } from "./conditionEval";

type Case = {
  name: string;
  op: string;
  actual: unknown;
  target: unknown;
  expected: boolean | "error";
  surface?: boolean;
};

type KeyCase = { name: string; keys: string[]; reading: unknown; expected: string | null };

// Read off disk: the corpus lives outside this Vite project (it belongs to the
// Python suite just as much). vitest runs from openavc/web/programmer.
const corpusPath = resolve(
  process.cwd(), "../../../tests/fixtures/condition_parity_cases.json",
);
const CORPUS = JSON.parse(readFileSync(corpusPath, "utf-8"));
const CASES: Case[] = CORPUS.cases;
const KEYS: KeyCase[] = CORPUS.keys;

const expectedIn = (c: Case, mode: ConditionMode) =>
  mode === SURFACE && c.surface !== undefined ? c.surface : c.expected;

describe.each([DECIDE, SURFACE] as const)("evalOperator answers as the runtime does (%s)", (mode) => {
  it.each(CASES.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    if (c.expected === "error") {
      expect(() => evalOperator(c.op, c.actual, c.target, mode)).toThrow(/Unknown condition operator/);
    } else {
      expect(evalOperator(c.op, c.actual, c.target, mode)).toBe(expectedIn(c, mode));
    }
  });
});

describe("evalOperator", () => {
  it("decides as a reader that acts unless told otherwise", () => {
    for (const c of CASES) {
      if (c.expected !== "error") expect(evalOperator(c.op, c.actual, c.target)).toBe(c.expected);
    }
  });

  it("reads a key that is not in state as not reported", () => {
    expect(evalOperator("ne", undefined, "on")).toBe(false);
    expect(evalOperator("ne", undefined, "on", SURFACE)).toBe(true);
    expect(evalOperator("eq", undefined, "on", SURFACE)).toBe(false);
    expect(evalOperator("falsy", undefined, null)).toBe(true);
  });
});

describe("matchKey picks the key the runtime picks", () => {
  it.each(KEYS.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(matchKey(c.keys, c.reading)).toBe(c.expected);
    expect(matchKey([...c.keys].reverse(), c.reading)).toBe(c.expected);
  });

  it("picks from a map's own keys, which JavaScript lists number-like first", () => {
    expect(matchKey(Object.keys({ on: 1, "1": 2 }), true)).toBe("1");
  });
});
