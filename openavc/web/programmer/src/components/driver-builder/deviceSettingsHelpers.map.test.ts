import { describe, expect, it } from "vitest";
import yaml from "js-yaml";

import type { DriverDeviceSettingDef } from "../../api/types";
import {
  cloneDraft,
  DRIVER_YAML_DUMP_OPTIONS,
  parseDriverDefinition,
} from "../../store/driverBuilderStore.helpers";
import {
  normalizeSettingMap,
  setSettingMapWord,
  settingMapRows,
} from "./deviceSettingsHelpers";

describe("the rows a setting's wire value map is edited in", () => {
  it("gives a boolean setting a Yes row and a No row", () => {
    expect(settingMapRows({ type: "boolean" })).toEqual([
      { key: "true", label: "Yes" },
      { key: "false", label: "No" },
    ]);
  });

  it("gives an enum setting one row per declared value, by its label", () => {
    expect(
      settingMapRows({
        type: "enum",
        values: ["off", { value: "auto", label: "Automatic" }],
      }),
    ).toEqual([
      { key: "off", label: "off" },
      { key: "auto", label: "Automatic" },
    ]);
  });

  it("leaves any other type to free rows", () => {
    expect(settingMapRows({ type: "integer" })).toBeNull();
    expect(settingMapRows({ type: "string" })).toBeNull();
  });
});

describe("reading and writing the words", () => {
  it("finds a boolean word whichever way the key is spelled", () => {
    expect(normalizeSettingMap({ True: "ON", FALSE: "OFF" }, "boolean")).toEqual({
      true: "ON",
      false: "OFF",
    });
    // An enum's values are case-sensitive and left as written.
    expect(normalizeSettingMap({ Auto: "AUTO" }, "enum")).toEqual({ Auto: "AUTO" });
    expect(normalizeSettingMap(undefined, "boolean")).toBeUndefined();
  });

  it("sets a word, and a blank word removes its entry", () => {
    const map = setSettingMapWord(undefined, "true", "ON");
    expect(map).toEqual({ true: "ON" });
    expect(setSettingMapWord(map, "false", "OFF")).toEqual({ true: "ON", false: "OFF" });
    expect(setSettingMapWord({ true: "ON", false: "OFF" }, "false", "")).toEqual({
      true: "ON",
    });
  });

  it("drops the map when no word is left", () => {
    expect(setSettingMapWord({ true: "ON" }, "true", "")).toBeUndefined();
  });
});

describe("the map survives a round trip through the editor", () => {
  const source = [
    "id: acme_widget",
    "name: Acme Widget",
    "transport: tcp",
    "device_settings:",
    "  high_density:",
    "    type: boolean",
    "    label: High Density",
    "    map:",
    '      "true": "ON"',
    '      "false": "OFF"',
    "    write:",
    '      send: "SET HIGH_DENSITY {value}\\r"',
    "  encryption:",
    "    type: enum",
    "    label: Encryption",
    '    values: ["off", auto]',
    '    map: { "off": "OFF", auto: AUTO }',
    "    write:",
    '      send: "SET ENCRYPTION {value}\\r"',
    "",
  ].join("\n");

  it("keeps every word from YAML to the editor and back", () => {
    const draft = cloneDraft(parseDriverDefinition(source));
    const settings = draft.device_settings as Record<string, DriverDeviceSettingDef>;
    const hd = settings.high_density;
    const map = normalizeSettingMap(hd.map, hd.type);
    expect(settingMapRows(hd)!.map((r) => map?.[r.key])).toEqual(["ON", "OFF"]);

    const reloaded = yaml.load(yaml.dump(draft, DRIVER_YAML_DUMP_OPTIONS)) as typeof draft;
    const back = reloaded.device_settings as Record<string, DriverDeviceSettingDef>;
    expect(back.high_density.map).toEqual({ true: "ON", false: "OFF" });
    expect(back.encryption.map).toEqual({ off: "OFF", auto: "AUTO" });
  });

  it("quotes every key and word YAML 1.1 would read as a boolean", () => {
    // The platform and the catalog read a driver file as YAML 1.1, where a
    // bare true, ON or off is a boolean.
    const draft = cloneDraft(parseDriverDefinition(source));
    const text = yaml.dump(draft, DRIVER_YAML_DUMP_OPTIONS);
    expect(text).toContain('"true": "ON"');
    expect(text).toContain('"false": "OFF"');
    expect(text).toContain('"off": "OFF"');
    expect(text).toContain('- "off"');
  });
});
