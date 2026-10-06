import { describe, expect, it } from "vitest";

import {
  BOOLEAN_OPTIONS,
  booleanWord,
  booleanWordFor,
  valueText,
} from "./booleanWords";
import { settingValueText } from "./paramOptions";
import { settingMapRows } from "../driver-builder/deviceSettingsHelpers";
import { monitorReading } from "../../api/monitorHelpers";
import { formatMetric } from "../plugins/pluginExtensionHelpers";

describe("the words a boolean reads as", () => {
  it("is Yes for true and No for false", () => {
    expect(booleanWord(true)).toBe("Yes");
    expect(booleanWord(false)).toBe("No");
  });

  it("offers Yes then No, keeping the stored values true and false", () => {
    expect(BOOLEAN_OPTIONS).toEqual([
      { value: "true", label: "Yes" },
      { value: "false", label: "No" },
    ]);
  });

  it("reads a boolean spelled as text in any case", () => {
    expect(booleanWordFor(true)).toBe("Yes");
    expect(booleanWordFor("false")).toBe("No");
    expect(booleanWordFor(" TRUE ")).toBe("Yes");
    expect(booleanWordFor("on")).toBeNull();
    expect(booleanWordFor(1)).toBeNull();
    expect(booleanWordFor(null)).toBeNull();
  });
});

describe("a value in a readout", () => {
  it("shows a real boolean as Yes / No", () => {
    expect(valueText(true)).toBe("Yes");
    expect(valueText(false)).toBe("No");
  });

  it("shows text a device reported as it arrived, even when it spells a boolean", () => {
    expect(valueText("true")).toBe("true");
    expect(valueText("ON")).toBe("ON");
  });

  it("shows numbers as numbers and no value as the caller's mark", () => {
    expect(valueText(0)).toBe("0");
    expect(valueText(41.5)).toBe("41.5");
    expect(valueText(null)).toBe("");
    expect(valueText(undefined, "—")).toBe("—");
  });
});

describe("every surface reads a boolean the same way", () => {
  it("a device setting's value on the device page", () => {
    const def = { type: "boolean" };
    expect(settingValueText(def, true)).toBe("Yes");
    expect(settingValueText(def, false)).toBe("No");
    expect(settingValueText(def, "false")).toBe("No");
  });

  it("an enum setting keeps its declared label", () => {
    const def = { type: "enum", values: [{ value: "0", label: "Low" }, "auto"] };
    expect(settingValueText(def, "0")).toBe("Low");
    expect(settingValueText(def, "auto")).toBe("auto");
  });

  it("the Driver Builder's wire value map rows", () => {
    expect(settingMapRows({ type: "boolean" })?.map((r) => r.label)).toEqual(["Yes", "No"]);
  });

  it("a Dashboard monitor tile with no words of its own", () => {
    expect(monitorReading({ key: "device.acme.ready" }, true)).toBe("Yes");
    expect(monitorReading({ key: "device.acme.ready" }, false)).toBe("No");
  });

  it("a plugin status card's boolean metric", () => {
    expect(formatMetric(true, "boolean")).toBe("Yes");
    expect(formatMetric("false", "boolean")).toBe("No");
  });
});
