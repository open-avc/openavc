import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { MacroConfig } from "../../api/types";
import { blockingIssues, lintPayload, issuesAt, issueLabel, issueSummary, issueText, useMacroLint, withMacroNames } from "./macroLint";
import type { MacroIssue } from "./macroLint";

vi.mock("../../api/restClient", () => ({
  validateMacros: vi.fn(),
}));
import * as api from "../../api/restClient";

const validateMacros = api.validateMacros as unknown as ReturnType<typeof vi.fn>;

function macro(over: Partial<MacroConfig> = {}): MacroConfig {
  return { id: "macro_a", name: "Start", steps: [], ...over };
}

function issue(over: Partial<MacroIssue> = {}): MacroIssue {
  return { scope: "step", index: 0, path: "steps[0]", message: "delay step requires 'seconds'", ...over };
}

describe("lintPayload", () => {
  it("carries only what the rules read", () => {
    expect(lintPayload([macro({ steps: [{ action: "delay" }], cancel_group: "system" })])).toEqual([
      { id: "macro_a", steps: [{ action: "delay" }], triggers: [] },
    ]);
  });

  it("is unchanged by a rename, so typing a name spends no request", () => {
    const before = JSON.stringify(lintPayload([macro({ name: "Start" })]));
    const after = JSON.stringify(lintPayload([macro({ name: "Start the room" })]));
    expect(after).toBe(before);
  });

  it("changes when a step does", () => {
    const before = JSON.stringify(lintPayload([macro({ steps: [{ action: "delay" }] })]));
    const after = JSON.stringify(lintPayload([macro({ steps: [{ action: "delay", seconds: 2 }] })]));
    expect(after).not.toBe(before);
  });
});

describe("issuesAt", () => {
  const issues = [
    issue({ scope: "step", index: 0 }),
    issue({ scope: "step", index: 2, path: "steps[2].then_steps[0]" }),
    issue({ scope: "trigger", index: 0, path: "triggers[0]", message: "cron expression must have 5 or 6 fields, got 4" }),
  ];

  it("picks the rows of one list", () => {
    expect(issuesAt(issues, "step", 2)).toHaveLength(1);
    expect(issuesAt(issues, "trigger", 0)).toHaveLength(1);
  });

  it("does not confuse a step index with a trigger index", () => {
    expect(issuesAt(issues, "trigger", 2)).toEqual([]);
    expect(issuesAt(issues, "step", 1)).toEqual([]);
  });

  it("is empty for a macro with nothing wrong", () => {
    expect(issuesAt(undefined, "step", 0)).toEqual([]);
  });
});

describe("issueText", () => {
  it("names a field left empty by its label on the form", () => {
    expect(issueText(issue({
      message: "device.command step requires 'device'", action: "device.command", missing: "device",
    }))).toBe("Device is required.");
    expect(issueText(issue({ action: "state.set", missing: "key" }))).toBe("Variable is required.");
    expect(issueText(issue({ scope: "trigger", action: "state_change", missing: "state_key" })))
      .toBe("When is required.");
  });

  it("names a command parameter as the step's form does", () => {
    const missing = { message: "Set Level: Level Value is required.", action: "device.command", missing: "param" };
    expect(issueText(issue({ ...missing, param: "level_value", param_label: "Level Value" })))
      .toBe("Level Value is required.");
    // A driver that is not loaded gives no label: the key made readable.
    expect(issueText(issue({ ...missing, param: "level_value" }))).toBe("Level Value is required.");
  });

  it("says where a condition's state key goes, since its picker has no label", () => {
    expect(issueText(issue({ action: "conditional", missing: "condition.key" })))
      .toBe("Choose a state key for If.");
    expect(issueText(issue({ action: "delay", missing: "skip_if.key" })))
      .toBe("Choose a state key for Skip this step if.");
    expect(issueText(issue({ scope: "trigger", action: "event", missing: "conditions.key" })))
      .toBe("Choose a state key.");
  });

  it("says a trigger never fires, and which trigger has its id", () => {
    const shared = { scope: "trigger" as const, path: "triggers[0]", message: "trigger id 't1' is also the id of triggers[1]" };
    expect(issueText(issue({ ...shared, same_id_as: { index: 1 } })))
      .toBe("This trigger never fires because Trigger 2 has the same id. Delete this trigger and add it again.");
    expect(issueText(issue({ ...shared, same_id_as: { macro: "macro_b", macro_name: "Evening", index: 0 } })))
      .toBe('This trigger never fires because Trigger 1 of "Evening" has the same id. Delete this trigger and add it again.');
    // A macro the editor cannot name is named by its id.
    expect(issueText(issue({ ...shared, same_id_as: { macro: "macro_b", index: 0 } })))
      .toBe('This trigger never fires because Trigger 1 of "macro_b" has the same id. Delete this trigger and add it again.');
  });

  it("keeps the platform's sentence for anything else", () => {
    const other = issue({ message: "delay 'seconds' must be a non-negative number", action: "delay" });
    expect(issueText(other)).toBe("delay 'seconds' must be a non-negative number");
    expect(issueText(issue({ message: "x", action: "some.plugin_action", missing: "target" }))).toBe("x");
  });
});

describe("issueLabel", () => {
  it("counts from one, because the editor draws a numbered list for a person", () => {
    expect(issueLabel(issue({ index: 0, path: "steps[0]" }))).toBe("Step 1");
    expect(issueLabel(issue({ scope: "trigger", index: 1, path: "triggers[1]" }))).toBe("Trigger 2");
  });

  it("says which branch, because a conditional has two that look alike", () => {
    expect(issueLabel(issue({ index: 1, path: "steps[1].then_steps[0]" })))
      .toBe("Step 2 \u2192 Then step 1");
    expect(issueLabel(issue({ index: 1, path: "steps[1].else_steps[2]" })))
      .toBe("Step 2 \u2192 Else step 3");
  });

  it("names a trigger's guard the way the trigger editor does", () => {
    expect(issueLabel(issue({ scope: "trigger", index: 0, path: "triggers[0].conditions[1]" })))
      .toBe("Trigger 1 \u2192 Condition 2");
  });

  it("falls back to the row when the path says nothing more", () => {
    expect(issueLabel(issue({ index: 0, path: "" }))).toBe("Step 1");
    expect(issueLabel(issue({ index: 0, path: "steps[0].mystery[3]" }))).toBe("Step 1");
  });
});

describe("issueSummary", () => {
  it("counts rows, not messages -- two problems on one step is one step", () => {
    expect(issueSummary([issue({ index: 1 }), issue({ index: 1, message: "other" })])).toBe("1 step");
  });

  it("names both lists when both are marked", () => {
    expect(
      issueSummary([issue({ index: 0 }), issue({ index: 3 }), issue({ scope: "trigger", index: 0 })]),
    ).toBe("2 steps and 1 trigger");
  });
});

describe("blockingIssues", () => {
  it("leaves out a warning, which marks its step but runs", () => {
    const device = issue({ index: 2, action: "state.set", warning: true, message: "replaced at the next report" });
    const refused = issue({ index: 1, action: "state.set", refused: "platform_key" });
    expect(blockingIssues([device, refused])).toEqual([refused]);
    expect(blockingIssues([device])).toEqual([]);
    expect(blockingIssues(undefined)).toEqual([]);
    expect(issuesAt([device, refused], "step", 2)).toEqual([device]);
  });
});

describe("useMacroLint", () => {
  /** Let the mocked request settle (and any timer due by `ms` fire). */
  const settle = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

  beforeEach(() => {
    vi.useFakeTimers();
    validateMacros.mockReset();
    validateMacros.mockResolvedValue({ macro_a: { issues: [issue()] } });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks immediately on the first pass -- a project nobody touches is the point", async () => {
    const macros = [macro({ steps: [{ action: "delay" }] })];
    const { result } = renderHook(() => useMacroLint(macros));
    await settle();
    expect(validateMacros).toHaveBeenCalledTimes(1);
    expect(result.current.macro_a).toHaveLength(1);
  });

  it("does not ask again until the typing stops", async () => {
    let macros = [macro({ steps: [{ action: "delay" }] })];
    const { rerender } = renderHook(() => useMacroLint(macros));
    await settle();
    expect(validateMacros).toHaveBeenCalledTimes(1);

    for (const seconds of [1, 12, 120]) {
      macros = [macro({ steps: [{ action: "delay", seconds }] })];
      rerender();
      await settle(200);
    }
    expect(validateMacros).toHaveBeenCalledTimes(1);

    await settle(1000);
    expect(validateMacros).toHaveBeenCalledTimes(2);
    expect(validateMacros).toHaveBeenLastCalledWith([
      { id: "macro_a", steps: [{ action: "delay", seconds: 120 }], triggers: [] },
    ]);
  });

  it("keeps the marks when the request fails -- clearing them would read as fixed", async () => {
    const macros = [macro({ steps: [{ action: "delay" }] })];
    const { result, rerender } = renderHook(() => useMacroLint(macros));
    await settle();
    expect(result.current.macro_a).toHaveLength(1);

    validateMacros.mockRejectedValueOnce(new Error("offline"));
    macros[0].steps = [{ action: "delay" }, { action: "macro" }];
    rerender();
    await settle(1000);
    expect(result.current.macro_a).toHaveLength(1);
  });

  it("drops a macro once its last problem is fixed", async () => {
    let macros = [macro({ steps: [{ action: "delay" }] })];
    const { result, rerender } = renderHook(() => useMacroLint(macros));
    await settle();
    expect(result.current.macro_a).toHaveLength(1);

    validateMacros.mockResolvedValue({ macro_a: { issues: [] } });
    macros = [macro({ steps: [{ action: "delay", seconds: 2 }] })];
    rerender();
    await settle(1000);
    expect(result.current.macro_a).toBeUndefined();
  });

  it("names the other macro a same-id finding points at, and follows a rename", async () => {
    const sameId = issue({ scope: "trigger", path: "triggers[0]", same_id_as: { macro: "macro_b", index: 0 } });
    validateMacros.mockResolvedValue({ macro_a: { issues: [sameId] } });
    let macros = [macro(), macro({ id: "macro_b", name: "Evening" })];
    const { result, rerender } = renderHook(() => useMacroLint(macros));
    await settle();
    expect(result.current.macro_a[0].same_id_as?.macro_name).toBe("Evening");

    macros = [macros[0], { ...macros[1], name: "Night" }];
    rerender();
    expect(result.current.macro_a[0].same_id_as?.macro_name).toBe("Night");
    // A rename is no request.
    await settle(1000);
    expect(validateMacros).toHaveBeenCalledTimes(1);
  });

  it("asks nothing at all when there are no macros", async () => {
    renderHook(() => useMacroLint([]));
    await settle(2000);
    expect(validateMacros).not.toHaveBeenCalled();
  });
});

describe("withMacroNames", () => {
  it("hands back the same issues when nothing points at another macro", () => {
    const issues = { macro_a: [issue()] };
    expect(withMacroNames(issues, [macro()])).toBe(issues);
  });
});
