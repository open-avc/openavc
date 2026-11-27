import { describe, it, expect } from "vitest";
import type { MacroConfig, TriggerConfig } from "../../api/types";
import { getConversionWarnings } from "./macroHelpers";

// A converted script's handler runs its steps each time it is called, so a
// trigger's Debounce, Cooldown and queued Overlap, and the macro's own
// Cooldown and Overlap, have no part in it. The preview says which are left
// out.

function macro(triggers: TriggerConfig[], own: Partial<MacroConfig> = {}): MacroConfig {
  return { id: "m", name: "Acme", steps: [], triggers, ...own } as MacroConfig;
}

const onChange = (patch: Partial<TriggerConfig>): TriggerConfig => ({
  id: "t1", type: "state_change", enabled: true, state_key: "var.acme_mode", ...patch,
});

const timing = (m: MacroConfig) => getConversionWarnings(m, [], [m]).filter(
  (w) => w.startsWith("Trigger ") || w.startsWith("The macro's own"),
);

describe("Convert to Script on a trigger's or a macro's timing", () => {
  it("names a trigger's Debounce and Cooldown", () => {
    expect(timing(macro([onChange({ debounce_seconds: 2, cooldown_seconds: 30 })]))).toEqual([
      'Trigger "t1": its Debounce and Cooldown are not converted, so the script runs on every change or event the trigger would wait out or turn away. To keep them, keep the trigger on this macro and replace the macro\'s steps with one Emit Event step for the script to listen for.',
    ]);
  });

  it("names a trigger's Overlap set to Queue", () => {
    const [warning] = timing(macro([onChange({ overlap: "queue" })]));
    expect(warning).toMatch(/^Trigger "t1": its Overlap \(Queue\) is not converted, .* To keep it, /);
  });

  it("names the macro's own Cooldown and Overlap", () => {
    expect(timing(macro([onChange({})], { cooldown_seconds: 10, overlap: "skip" }))).toEqual([
      "The macro's own Cooldown and Overlap are not converted, so the script runs its steps where the macro would turn a run away.",
    ]);
  });

  it("says nothing for the defaults, a trigger turned off, or a schedule", () => {
    expect(timing(macro([onChange({ debounce_seconds: 0, cooldown_seconds: 0, overlap: "skip" })], { overlap: "allow" }))).toEqual([]);
    expect(timing(macro([onChange({ enabled: false, cooldown_seconds: 30 })]))).toEqual([]);
    expect(timing(macro([onChange({ type: "schedule", cron: "0 9 * * *", cooldown_seconds: 30 })]))).toEqual([]);
  });

  it("says nothing of the macro's own settings when no trigger becomes a handler", () => {
    expect(timing(macro([], { cooldown_seconds: 10 }))).toEqual([]);
  });
});
