import { describe, it, expect } from "vitest";
import { evalVisibleWhen } from "./actionVisibility";
import { OPERATORS, OPERATOR_ALIASES } from "../../api/conditionOperators.gen";
import type { ActionVisibleWhen } from "../../api/types";

const state = { "device.amp.mode": "on" };
const when = (vw: unknown) => evalVisibleWhen(vw as ActionVisibleWhen, state, "amp");

describe("evalVisibleWhen: an operator nobody knows", () => {
  it("never holds, so an action shown only by it stays hidden", () => {
    const unknown = { key: "device.$id.mode", operator: "contains", value: "on" };
    expect(when({ key: "device.$id.mode", operator: "eq", value: "on" })).toBe(true);
    expect(when(unknown)).toBe(false);
    expect(when({ all: [{ key: "device.$id.mode", operator: "eq", value: "on" }, unknown] })).toBe(false);
    expect(when({ any: [unknown] })).toBe(false);
  });
});

describe("evalVisibleWhen: every spelling of an operator", () => {
  const level = (operator: string, value: unknown) =>
    evalVisibleWhen({ key: "device.$id.level", operator, value } as ActionVisibleWhen, { "device.amp.level": 5 }, "amp");

  it.each(Object.entries(OPERATOR_ALIASES))("reads %s as %s", (alias, canonical) => {
    for (const value of [4, 5, 6]) expect(level(alias, value)).toBe(level(canonical, value));
  });

  it("reads each operator by its own name", () => {
    expect(OPERATORS.map((op) => [op, level(op, 5)])).toEqual([
      ["eq", true], ["ne", false], ["gt", false], ["lt", false],
      ["gte", true], ["lte", true], ["truthy", true], ["falsy", false],
    ]);
  });
});
