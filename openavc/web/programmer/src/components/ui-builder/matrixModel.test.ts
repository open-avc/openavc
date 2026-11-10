/**
 * What a matrix axis resolves to, entry by entry, and how a routed value
 * compares: the Builder's copy of openavc/ui/matrix_model.py.
 *
 * The page review's matrix checks and the floor's row count read these lists,
 * and the parity suite only sees them through the sentences the review writes,
 * so a generator field nothing in the corpus names (its `values`, `labels`,
 * `exclude`, `overrides`, each key pattern) could change unseen. Each case here
 * states the entries in full.
 */
import { describe, expect, it } from "vitest";
import {
  matrixAxisCount,
  matrixRouteMatches,
  matrixRouteValue,
  matrixSourceReports,
  resolveMatrixAxis,
} from "./uiBuilderHelpers";

const destinations = (spec: unknown) => resolveMatrixAxis({ destinations: spec }, "destinations");
const sources = (spec: unknown) => resolveMatrixAxis({ sources: spec }, "sources");

describe("a generator", () => {
  it("stands for 1 to its count, whole numbers only, each captioned by position", () => {
    expect(destinations({ from: { count: 2.9 } })).toEqual([
      { value: 1, label: "Out 1" },
      { value: 2, label: "Out 2" },
    ]);
    expect(sources({ from: { count: "3" } }).map((e) => e.value)).toEqual([1, 2, 3]);
    expect(sources({ from: { count: 3 } }).map((e) => e.label)).toEqual(["In 1", "In 2", "In 3"]);
  });

  it("stands for nothing when its count is missing, not a number, below one or not finite", () => {
    for (const count of [undefined, "abc", 0, -2, "Infinity", Number.NaN]) {
      expect(destinations({ from: { count } })).toEqual([]);
    }
    expect(destinations({})).toEqual([]);
    expect(destinations({ from: [3] })).toEqual([]);
  });

  it("stands for its values list in place of a count, as written", () => {
    expect(destinations({ from: { values: [5, "hdmi", 7], count: 2 } })).toEqual([
      { value: 5, label: "Out 1" },
      { value: "hdmi", label: "Out 2" },
      { value: 7, label: "Out 3" },
    ]);
  });

  it("takes each typed name by position, and an empty one is no name", () => {
    expect(
      destinations({ from: { count: 3, labels: ["Main", "", null] } }).map((e) => e.label),
    ).toEqual(["Main", "Out 2", "Out 3"]);
    expect(destinations({ from: { count: 1, labels: "Main" } })[0].label).toBe("Out 1");
    expect(destinations({ from: { count: 1, labels: [42] } })[0].label).toBe("42");
  });

  it("leaves out an excluded value, matched as text, and captions the rest by where they were written", () => {
    expect(destinations({ from: { count: 3 }, exclude: [1] })).toEqual([
      { value: 2, label: "Out 2" },
      { value: 3, label: "Out 3" },
    ]);
    expect(destinations({ from: { count: 3 }, exclude: ["3"] }).map((e) => e.value)).toEqual([1, 2]);
    expect(destinations({ from: { count: 2 }, exclude: 1 }).map((e) => e.value)).toEqual([1, 2]);
  });

  it("fills each key pattern with the value, first star only, in the order an entry carries them", () => {
    const [entry] = destinations({
      from: {
        count: 1,
        labels: ["Main"],
        lock_key: "var.lock_*",
        audio_route_key: "device.sw.audio.*.input",
        route_key: "device.sw.output.*.input.*",
        label_key: "device.sw.output.*.name",
        route: ["route", "*"],
      },
    });
    expect(entry).toEqual({
      value: 1,
      label: "Main",
      label_key: "device.sw.output.1.name",
      route_key: "device.sw.output.1.input.*",
      audio_route_key: "device.sw.audio.1.input",
      lock_key: "var.lock_1",
      route: ["route", "*"],
    });
    expect(Object.keys(entry)).toEqual([
      "value", "label", "label_key", "route_key", "audio_route_key", "lock_key", "route",
    ]);
  });

  it("writes no key for an empty or non-text pattern, and no route that is not a list", () => {
    expect(
      destinations({ from: { count: 1, route_key: "", lock_key: 7, route: "x" } }),
    ).toEqual([{ value: 1, label: "Out 1" }]);
  });

  it("leaves an entry with a live name key uncaptioned, so the device names it", () => {
    expect(destinations({ from: { count: 1, label_key: "device.sw.output.*.name" } })).toEqual([
      { value: 1, label_key: "device.sw.output.1.name" },
    ]);
  });

  it("lets an override, keyed by the value as text, win over what was generated", () => {
    const entries = destinations({
      from: { count: 3, labels: ["A", "B", "C"], route_key: "device.sw.output.*.input" },
      overrides: {
        "2": { label: "Lobby", route_key: "var.lobby", route: ["encoder"] },
        "3": "not an object",
      },
    });
    expect(entries).toEqual([
      { value: 1, label: "A", route_key: "device.sw.output.1.input" },
      { value: 2, label: "Lobby", route_key: "var.lobby", route: ["encoder"] },
      { value: 3, label: "C", route_key: "device.sw.output.3.input" },
    ]);
  });

  it("captions an override that takes the name away, unless it names a live key", () => {
    expect(
      destinations({ from: { count: 2, labels: ["A", "B"] }, overrides: { "1": { label: "" }, "2": { label: "", label_key: "var.n" } } }),
    ).toEqual([
      { value: 1, label: "Out 1" },
      { value: 2, label: "", label_key: "var.n" },
    ]);
  });

  it("answers only its own keys from overrides, not ones an object inherits", () => {
    expect(
      destinations({ from: { values: ["constructor"] }, overrides: {} }),
    ).toEqual([{ value: "constructor", label: "Out 1" }]);
  });
});

describe("an authored list", () => {
  it("fills out each entry, a bare number or text standing for its value", () => {
    expect(sources([3, "hdmi", { value: 9, label: "Camera" }])).toEqual([
      { value: 3, label: "In 1" },
      { value: "hdmi", label: "In 2" },
      { value: 9, label: "Camera" },
    ]);
  });

  it("drops an entry with no value, and one that is neither an object nor a scalar, keeping later positions", () => {
    expect(sources([{ label: "no value" }, true, null, [1], { value: 4 }])).toEqual([
      { value: 4, label: "In 5" },
    ]);
  });

  it("leaves an entry with a live name key uncaptioned, and copies the rest as written", () => {
    expect(destinations([{ value: 1, label_key: "device.sw.output.1.name", route_key: "k" }])).toEqual([
      { value: 1, label_key: "device.sw.output.1.name", route_key: "k" },
    ]);
  });

  it("does not change what was written", () => {
    const raw = { value: 1 };
    sources([raw]);
    expect(raw).toEqual({ value: 1 });
  });
});

describe("an axis", () => {
  it("is nothing when the config or the axis is not there, or is neither form", () => {
    expect(resolveMatrixAxis(null, "sources")).toEqual([]);
    expect(resolveMatrixAxis([{ sources: [1] }], "sources")).toEqual([]);
    expect(resolveMatrixAxis({ sources: 4 }, "sources")).toEqual([]);
    expect(resolveMatrixAxis({ sources: "1,2" }, "sources")).toEqual([]);
  });

  it("counts as many entries as it resolves to", () => {
    expect(matrixAxisCount({ sources: { from: { count: 4 }, exclude: [2] } }, "sources")).toBe(3);
    expect(matrixAxisCount({ destinations: [1, { label: "x" }, 2] }, "destinations")).toBe(2);
  });
});

describe("a routed value", () => {
  it("is nothing for no value, zero, an empty or zero text, or a number that is not finite", () => {
    for (const value of [null, undefined, 0, "", "  ", "0", " 0 ", Number.NaN, Infinity]) {
      expect(matrixRouteValue(value)).toBeNull();
    }
  });

  it("compares a number written out as that number, and other text without case or spaces", () => {
    expect(matrixRouteValue(5)).toBe(5);
    expect(matrixRouteValue(-2)).toBe(-2);
    expect(matrixRouteValue(" 3 ")).toBe(3);
    expect(matrixRouteValue("1.5")).toBe(1.5);
    expect(matrixRouteValue("+4")).toBe(4);
    expect(matrixRouteValue(" IN1 ")).toBe("in1");
    expect(matrixRouteValue(true)).toBe("true");
    expect(matrixRouteValue("1e3")).toBe("1e3");
  });

  it("matches the same source however each side writes it", () => {
    expect(matrixRouteMatches(3, "3")).toBe(true);
    expect(matrixRouteMatches("IN1", "in1")).toBe(true);
    expect(matrixRouteMatches(3, "IN3")).toBe(true);
    expect(matrixRouteMatches("Input 2", "IN2")).toBe(true);
  });

  it("matches nothing for an unrouted side, two different names, or text with more than one number", () => {
    expect(matrixRouteMatches(0, 0)).toBe(false);
    expect(matrixRouteMatches(null, "1")).toBe(false);
    expect(matrixRouteMatches("apple", "roku")).toBe(false);
    expect(matrixRouteMatches("IN1-2", 1)).toBe(false);
    expect(matrixRouteMatches("IN3", 4)).toBe(false);
  });

  it("is what a source reports as, else what it sends", () => {
    expect(matrixSourceReports({ value: "0", report_value: "Mic" })).toBe("Mic");
    expect(matrixSourceReports({ value: "0", report_value: null })).toBe("0");
    expect(matrixSourceReports({ value: 2 })).toBe(2);
    expect(matrixSourceReports({ value: 2, report_value: 0 })).toBe(0);
    expect(matrixSourceReports(undefined)).toBeUndefined();
  });
});
