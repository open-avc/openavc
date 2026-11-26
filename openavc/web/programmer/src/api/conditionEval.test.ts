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
import {
  DECIDE, SURFACE, evalOperator, matchKey, readTruth, takesOperator, writtenOperator, type ConditionMode,
} from "./conditionEval";
import type { ConditionPlace } from "./conditionOperators.gen";

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

// The words that mean true and false (openavc/drivers/truth_words.py), over
// the corpus tests/test_truth_words.py runs through read_truth and the panel.
const TRUTH_CASES: [unknown, boolean | null][] = JSON.parse(
  readFileSync(resolve(corpusPath, "..", "truth_word_cases.json"), "utf-8"),
).cases;

describe("readTruth reads what the runtime reads", () => {
  it.each(TRUTH_CASES.map((c) => [JSON.stringify(c[0]), c] as const))("%s", (_name, c) => {
    expect(readTruth(c[0])).toBe(c[1]);
  });
});

// What an operator reads as where it sits (core/condition_eval.py
// written_operator), over the cases tests/test_condition_written_operator.py
// runs: a State Change trigger saved with no operator, a null one or a blank
// one fires on any change, so its editor and summary say so too.
type WrittenCase = {
  name: string;
  where: ConditionPlace;
  operator?: string | null;
  written: string | null;
  taken: boolean;
};
const WRITTEN_CASES: WrittenCase[] = JSON.parse(
  readFileSync(resolve(corpusPath, "..", "written_operator_cases.json"), "utf-8"),
).cases;

describe("writtenOperator reads an operator as the runtime does where it sits", () => {
  it.each(WRITTEN_CASES.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const stored = "operator" in c ? c.operator : undefined;
    expect(writtenOperator(stored, c.where)).toBe(c.written);
    expect(takesOperator(writtenOperator(stored, c.where), c.where)).toBe(c.taken);
  });
});
