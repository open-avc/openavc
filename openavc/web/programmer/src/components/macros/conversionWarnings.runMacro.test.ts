import { describe, it, expect } from "vitest";
import type { MacroConfig, MacroStep, TriggerConfig } from "../../api/types";
import { getConversionWarnings } from "./macroHelpers";

// A macro run by a Run Macro step reads what ran the macro that called it.
// A converted script runs it with macros.execute, which passes none of that,
// so a macro below it that reads $trigger values reads them as empty.

const ON_CHANGE = { id: "t1", type: "state_change", enabled: true, state_key: "var.acme_mode" } as TriggerConfig;

function macro(id: string, steps: MacroStep[], triggers: TriggerConfig[] = []): MacroConfig {
  return { id, name: `Acme ${id}`, steps, triggers } as MacroConfig;
}

const run = (id: string): MacroStep => ({ action: "macro", macro: id });
const READS_VALUE = macro("reads_value", [{ action: "state.set", key: "var.acme_seen", value: "$trigger.new_value" }]);
const READS_KEY = macro("reads_key", [{
  action: "device.command", device: "acme_widget", command: "beep",
  skip_if: { key: "trigger.new_value", operator: "eq", value: "off" },
}]);
const READS_NOTHING = macro("reads_nothing", [{ action: "state.set", key: "var.acme_seen", value: "$var.acme_mode" }]);
const CALLS_READER = macro("calls_reader", [run("reads_value")]);
// Steps a script writes as a comment, which the macro engine still runs with
// the run's values.
const ASKS_FOR_HELP = macro("asks_for_help", [{ action: "help.request", message: "Acme $trigger.source is down" }]);
const RUNS_PLUGIN = macro("runs_plugin", [{ action: "acme.flash", params: { times: "$trigger.new_value" } }]);
const EMITS = macro("emits", [{ action: "event.emit", event: "custom.acme", payload: { level: "$trigger.new_value" } }]);
const MACROS = [READS_VALUE, READS_KEY, READS_NOTHING, CALLS_READER, ASKS_FOR_HELP, RUNS_PLUGIN, EMITS];

function runMacroWarnings(m: MacroConfig, macros: MacroConfig[] = [...MACROS, m]): string[] {
  return getConversionWarnings(m, [], macros).filter((w) => w.includes("macros.execute"));
}

describe("Convert to Script on a macro that runs another", () => {
  it("names a macro that reads the trigger's values", () => {
    const warnings = runMacroWarnings(macro("parent", [run("reads_value")], [ON_CHANGE]));
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('"Acme reads_value"');
  });

  it("names one whose condition reads them, and one in a Conditional's branch", () => {
    const warnings = runMacroWarnings(macro("parent", [{
      action: "conditional",
      condition: { key: "var.acme_mode", operator: "eq", value: "on" },
      then_steps: [run("reads_key")],
      else_steps: [],
    }], [ON_CHANGE]));
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('"Acme reads_key"');
  });

  it("names a macro whose own Run Macro step reaches one that reads them", () => {
    const warnings = runMacroWarnings(macro("parent", [run("calls_reader")], [ON_CHANGE]));
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('"Acme calls_reader"');
  });

  it("names one that reads them where only the macro engine does", () => {
    for (const child of ["asks_for_help", "runs_plugin", "emits"]) {
      const warnings = runMacroWarnings(macro("parent", [run(child)], [ON_CHANGE]));
      expect(warnings, child).toHaveLength(1);
      expect(warnings[0]).toContain(`"Acme ${child}"`);
    }
  });

  it("says nothing of a macro that reads none", () => {
    expect(runMacroWarnings(macro("parent", [run("reads_nothing")], [ON_CHANGE]))).toEqual([]);
  });

  it("says nothing when no trigger runs the script's handler", () => {
    expect(runMacroWarnings(macro("parent", [run("reads_value")]))).toEqual([]);
    const off = { ...ON_CHANGE, enabled: false } as TriggerConfig;
    expect(runMacroWarnings(macro("parent", [run("reads_value")], [off]))).toEqual([]);
  });

  it("stops at a macro that runs itself", () => {
    const loop = macro("loop", [run("loop"), run("reads_value")]);
    const warnings = runMacroWarnings(macro("parent", [run("loop")], [ON_CHANGE]), [...MACROS, loop]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('"Acme loop"');
  });
});
