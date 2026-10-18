import { describe, expect, it } from "vitest";
import type { DriverDefinition } from "../../api/types";
import {
  buildQueryEntry,
  carryQueryExtras,
  firstGateEntry,
  firstGateValue,
  gateValue,
  parseGateValues,
  queryWhenState,
  stateGateVariables,
  withWhenState,
} from "./queryEntryHelpers";

describe("when_state on a query entry", () => {
  it("gives a bare string the {send} form to carry a gate, and takes it back", () => {
    const gated = withWhenState("GET STANDBY\r\n", { standby: false });
    expect(gated).toEqual({ send: "GET STANDBY\r\n", when_state: { standby: false } });
    expect(queryWhenState(gated)).toEqual({ standby: false });
    expect(withWhenState(gated, undefined)).toBe("GET STANDBY\r\n");
  });

  it("keeps the other keys of a {send} entry when the gate is removed", () => {
    const entry = { send: "PWR?\r", query_for: "power", when_state: { standby: false } };
    expect(withWhenState(entry, undefined)).toEqual({ send: "PWR?\r", query_for: "power" });
  });

  it("rides a per-child entry and an OSC step", () => {
    const child = { each_child: "output", send: "GET MP out{child_id}\r\n" };
    expect(withWhenState(child, { standby: false })).toEqual({
      ...child,
      when_state: { standby: false },
    });
    const osc = { address: "/level", args: [{ type: "f", value: "0.5" }] };
    expect(queryWhenState(withWhenState(osc, { mode: [0, 2] }))).toEqual({ mode: [0, 2] });
  });

  it("survives an edit of the send, the scope and the page size", () => {
    const prev = {
      each_child: "output",
      send: "Q{child_id}\r",
      page_size: 16,
      when_state: { power_save: [0, 2] },
    };
    const edited = carryQueryExtras(prev, buildQueryEntry("R{child_id}\r", "output", ""));
    expect(edited).toEqual({
      each_child: "output",
      send: "R{child_id}\r",
      page_size: 16,
      when_state: { power_save: [0, 2] },
    });
    // Back to "send once": a plain string cannot hold the gate, so the
    // {send} form carries it.
    const once = carryQueryExtras(prev, buildQueryEntry("R\r", "", ""));
    expect(once).toEqual({ send: "R\r", when_state: { power_save: [0, 2] } });
  });
});

describe("gate values", () => {
  it("parses typed text by the variable's type", () => {
    expect(parseGateValues("0, 2", "integer")).toEqual([0, 2]);
    expect(parseGateValues("0, 1.5, x", "integer")).toEqual([0]);
    expect(parseGateValues("-1.5", "number")).toEqual([-1.5]);
    expect(parseGateValues(" on ,standby,", "string")).toEqual(["on", "standby"]);
  });

  it("stores one value as itself and several as a list", () => {
    expect(gateValue([false])).toBe(false);
    expect(gateValue([0, 2])).toEqual([0, 2]);
  });

  it("offers the device-level variables with their types and enum values", () => {
    const draft = {
      state_variables: {
        standby: { type: "boolean", label: "Standby" },
        power: { type: "enum", label: "Power", values: ["on", { value: "standby", label: "Standby" }] },
      },
    } as unknown as DriverDefinition;
    const vars = stateGateVariables(draft);
    expect(vars.map((v) => [v.name, v.type, v.values])).toEqual([
      ["standby", "boolean", []],
      ["power", "enum", ["on", "standby"]],
    ]);
    expect(firstGateValue(vars[0])).toBe(false);
    expect(firstGateValue(vars[1])).toBe("on");
  });

  it("starts a new condition on a variable with a value to show, not on text", () => {
    const draft = {
      state_variables: {
        input_name: { type: "string", label: "Input Name" },
        mode: { type: "integer", label: "Mode" },
        standby: { type: "boolean", label: "Standby" },
      },
    } as unknown as DriverDefinition;
    const vars = stateGateVariables(draft);
    expect(firstGateEntry(vars)).toEqual(["standby", false]);
    expect(firstGateEntry(vars, new Set(["standby"]))).toEqual(["mode", 0]);
    // Text only: the condition starts empty, which a save refuses until a
    // value is typed.
    expect(firstGateEntry(vars, new Set(["standby", "mode"]))).toEqual(["input_name", ""]);
    expect(firstGateEntry(vars, new Set(["standby", "mode", "input_name"]))).toBeUndefined();
  });
});
