import { describe, it, expect } from "vitest";
import type { ChildEntityStateVarDef } from "../../api/types";
import { childSchemaOptions, marksControls } from "./paramOptions";

// One block of an invented DSP, the way a driver describes it: two levels in
// dB, two on/off controls, a delay in ms, and a read-only meter.
const BLOCK: Record<string, ChildEntityStateVarDef> = {
  online: { type: "boolean", label: "Online" },
  gain: { type: "number", label: "Gain", unit: "dB", min: -80, max: 10, control: true },
  trim: { type: "number", label: "Trim", unit: "DB", min: 0, max: 48, control: true },
  mute: { type: "boolean", label: "Mute", control: true },
  polarity: { type: "boolean", label: "Polarity", control: true },
  delay: { type: "number", label: "Delay", unit: "ms", control: true },
  meter: { type: "number", label: "Meter", unit: "dB" },
};

const values = (opts: { value: string }[]) => opts.map((o) => o.value);

describe("the controls a cascade offers", () => {
  it("offers every control when the command does not narrow it", () => {
    expect(values(childSchemaOptions(BLOCK))).toEqual([
      "gain", "trim", "mute", "polarity", "delay",
    ]);
  });

  it("offers only the on/off controls to a command that takes booleans", () => {
    expect(values(childSchemaOptions(BLOCK, { types: ["boolean"] }))).toEqual([
      "mute", "polarity",
    ]);
  });

  it("offers only the dB levels to a command that steps a level, ignoring the unit's case", () => {
    expect(
      values(childSchemaOptions(BLOCK, { types: ["number"], units: ["dB"] })),
    ).toEqual(["gain", "trim"]);
  });

  it("leaves out a control with no unit when units are asked for", () => {
    expect(values(childSchemaOptions(BLOCK, { units: ["dB", "ms"] }))).toEqual([
      "gain", "trim", "delay",
    ]);
  });

  it("narrows a driver that flags nothing, without offering the platform's own keys", () => {
    const unflagged: Record<string, ChildEntityStateVarDef> = {
      online: { type: "boolean" },
      label: { type: "string" },
      mute: { type: "boolean" },
      level: { type: "number", unit: "dB" },
    };
    expect(values(childSchemaOptions(unflagged, { types: ["boolean"] }))).toEqual(["mute"]);
  });

  it("offers nothing from a child whose vars are all marked not a control", () => {
    // A meter block: readings marked control: false beside summary vars that
    // say nothing. It has no controls, narrowed or not.
    const meter: Record<string, ChildEntityStateVarDef> = {
      online: { type: "boolean" },
      name: { type: "string", label: "Name" },
      responding: { type: "boolean", label: "Responding" },
      peak: { type: "number", label: "Peak", unit: "dB", control: false },
    };
    expect(childSchemaOptions(meter)).toEqual([]);
    expect(childSchemaOptions(meter, { types: ["boolean"] })).toEqual([]);
    expect(marksControls(meter)).toBe(true);
  });

  it("says a child marks nothing when no var carries the flag", () => {
    expect(marksControls({ level: { type: "number" } })).toBe(false);
    expect(marksControls(undefined)).toBe(false);
  });
});
