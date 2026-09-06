import { describe, expect, it } from "vitest";
import { declaredValuesForKey, enumOptionValues } from "./declaredValues";

// An invented driver: a display-like widget with one declared enum, one
// string state written by an enum command, one string state written by a
// setter whose parameter is named differently, and a boolean.
const acme = {
  state_variables: {
    power: { type: "enum", values: ["off", "on"] },
    input: { type: "string" },
    indicator: { type: "string" },
    mute: { type: "boolean" },
    volume: { type: "integer" },
    model: { type: "string" },
  },
  commands: {
    power_on: {},
    set_input: {
      params: { input: { type: "enum", values: ["hdmi1", "hdmi2", "component"] } },
    },
    set_indicator: {
      params: { mode: { type: "enum", values: [{ value: "dim", label: "Dim" }, "bright"] } },
    },
    set_volume: { params: { level: { type: "integer" } } },
    set_scene: {
      params: {
        look: { type: "enum", values: ["a", "b"] },
        transition: { type: "enum", values: ["cut", "fade"] },
      },
    },
  },
};

describe("enumOptionValues", () => {
  it("reads bare values and {value, label} pairs as their wire value", () => {
    expect(enumOptionValues(["a", { value: "b", label: "Bee" }, 3 as unknown as string])).toEqual(["a", "b", "3"]);
  });
  it("is empty for anything that is not a list", () => {
    expect(enumOptionValues(undefined)).toEqual([]);
  });
});

describe("declaredValuesForKey", () => {
  it("prefers the state variable's own declared values, in declared order", () => {
    expect(declaredValuesForKey(acme, "power")).toEqual(["off", "on"]);
  });
  it("offers a boolean's two values before the device has reported one", () => {
    expect(declaredValuesForKey(acme, "mute")).toEqual(["true", "false"]);
  });
  it("reads the enum of the command parameter named after the key", () => {
    expect(declaredValuesForKey(acme, "input")).toEqual(["hdmi1", "hdmi2", "component"]);
  });
  it("falls back to the single enum parameter of set_<key>", () => {
    expect(declaredValuesForKey(acme, "indicator")).toEqual(["dim", "bright"]);
  });
  it("offers nothing when set_<key> has two enum parameters and neither is named after the key", () => {
    expect(declaredValuesForKey(acme, "scene")).toEqual([]);
  });
  it("offers nothing for a key no declaration constrains", () => {
    expect(declaredValuesForKey(acme, "volume")).toEqual([]);
    expect(declaredValuesForKey(acme, "model")).toEqual([]);
    expect(declaredValuesForKey(undefined, "input")).toEqual([]);
  });
  it("uses a child entity's own declared variable when the device has none", () => {
    expect(declaredValuesForKey(acme, "output.01.route", { values: ["1", "2", "3"] })).toEqual(["1", "2", "3"]);
  });
  it("matches a child key's command by its last segment", () => {
    expect(declaredValuesForKey(acme, "output.01.input", null)).toEqual(["hdmi1", "hdmi2", "component"]);
  });
});
