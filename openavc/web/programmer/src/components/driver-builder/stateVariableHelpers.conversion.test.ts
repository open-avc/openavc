import { describe, expect, it } from "vitest";

import { applyChildVarTypeChange } from "./childEntityTypesHelpers";
import {
  applyStateVarTypeChange,
  formatUnknownCodes,
  parseUnknownCodes,
} from "./stateVariableHelpers";

describe("no-reading values", () => {
  it("reads numbers as numbers and anything else as text", () => {
    expect(parseUnknownCodes("255, 65535")).toEqual([255, 65535]);
    expect(parseUnknownCodes("255, N/A")).toEqual([255, "N/A"]);
    expect(parseUnknownCodes("-1")).toEqual([-1]);
  });

  it("clears the field when nothing is typed", () => {
    expect(parseUnknownCodes("")).toBeUndefined();
    expect(parseUnknownCodes(" , ")).toBeUndefined();
  });

  it("shows a stored list as the text that reads back to it", () => {
    expect(formatUnknownCodes([255, "N/A"])).toBe("255, N/A");
    expect(parseUnknownCodes(formatUnknownCodes([255, "N/A"]))).toEqual([255, "N/A"]);
    expect(formatUnknownCodes(undefined)).toBe("");
  });
});

describe("leaving a numeric type drops the conversion", () => {
  const numeric = { type: "integer", label: "Gain", offset: -18, scale: 1, unknown: [255] };

  it("on a state variable", () => {
    const next = applyStateVarTypeChange(numeric, "string") as Record<string, unknown>;
    expect(next.offset).toBeUndefined();
    expect(next.scale).toBeUndefined();
    expect(next.unknown).toBeUndefined();
    expect(applyStateVarTypeChange(numeric, "number")).toMatchObject({ offset: -18 });
  });

  it("on a child state variable", () => {
    const next = applyChildVarTypeChange(numeric, "boolean") as Record<string, unknown>;
    expect(next.offset).toBeUndefined();
    expect(next.unknown).toBeUndefined();
  });
});
