import { describe, it, expect } from "vitest";
import { resolveTestParams, testBlockedMessage } from "./testActionParams";

describe("a test send it cannot make", () => {
  it("refuses a field that waits for the panel, naming it the way the form does", () => {
    const result = resolveTestParams({ input_id: "$value" }, {});
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(testBlockedMessage(result)).toBe(
      'Can\'t test: "Input ID" uses $value, which only has a value when the panel control ' +
        "fires. Enter a fixed value to test.",
    );
    expect(testBlockedMessage(result, "Source")).toContain('"Source" uses $value');
  });

  it("refuses a field whose state key has no value yet", () => {
    const result = resolveTestParams({ gain_db: "$var.gain" }, {});
    if (result.ok) throw new Error("expected a refusal");
    expect(testBlockedMessage(result)).toBe(
      'Can\'t test: "Gain (dB)" references $var.gain, which has no current value.',
    );
  });
});
