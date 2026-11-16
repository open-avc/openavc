import { describe, it, expect } from "vitest";
import type { MacroConfig, TriggerConfig } from "../../api/types";
import { convertedMacroAdvice } from "./macroHelpers";

function macro(triggers: Partial<TriggerConfig>[]): MacroConfig {
  return { id: "macro_on", name: "On", steps: [], triggers } as MacroConfig;
}

describe("what Convert to Script says to do with the macro", () => {
  it("says nothing when the macro has no trigger that runs", () => {
    expect(convertedMacroAdvice(macro([]))).toBe("");
    expect(
      convertedMacroAdvice(macro([{ type: "event", enabled: false, event_pattern: "custom.go" }])),
    ).toBe("");
  });

  it("asks for the triggers to go when the script now does their work", () => {
    const advice = convertedMacroAdvice(macro([{ type: "event", enabled: true, event_pattern: "custom.go" }]));
    expect(advice).toContain("Disable them or delete the macro");
    expect(advice).not.toContain("Schedule");
  });

  it("keeps a Schedule trigger, which the script cannot take over", () => {
    const advice = convertedMacroAdvice(
      macro([
        { type: "schedule", enabled: true, cron: "0 22 * * 1-5" },
        { type: "event", enabled: true, event_pattern: "custom.go" },
      ]),
    );
    expect(advice).toContain("Disable all but its Schedule trigger");
    expect(advice).toContain("Emit Event step");
    expect(advice).not.toContain("delete the macro");
  });

  it("says a Startup trigger's steps run in the script at the next start, not now", () => {
    const advice = convertedMacroAdvice(macro([{ type: "startup", enabled: true }]));
    expect(advice).toContain("Disable them or delete the macro");
    expect(advice).toContain("The script runs its Startup steps the next time the server starts.");
  });

  it("says it beside a Schedule trigger too", () => {
    const advice = convertedMacroAdvice(
      macro([
        { type: "schedule", enabled: true, cron: "0 22 * * 1-5" },
        { type: "startup", enabled: true },
      ]),
    );
    expect(advice).toContain("Disable all but its Schedule trigger");
    expect(advice).toContain("the next time the server starts");
  });

  it("says nothing about the next start for a disabled Startup trigger", () => {
    const advice = convertedMacroAdvice(
      macro([
        { type: "startup", enabled: false },
        { type: "event", enabled: true, event_pattern: "custom.go" },
      ]),
    );
    expect(advice).not.toContain("server starts");
  });
});
