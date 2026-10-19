import { describe, expect, it } from "vitest";
import {
  buildConfigFromForm,
  coerceConfigValue,
  missingBridgeFields,
  missingRequiredFields,
  prefillConfigValues,
  requiredFieldsMessage,
} from "./deviceConfigCoerce";

// Nothing required, over the network: the case the coercion tests are about.
const NONE = { defaults: {}, connection: "network" as const };

// A driver's default_config is flattened into the dialog's string form when
// the driver is picked, then coerced back on save. Whatever the driver
// declared must survive that round trip, or the device is created with a
// default the driver never meant.
describe("prefillConfigValues", () => {
  const schema = {
    host: { type: "string" },
    port: { type: "number" },
    blocks: { type: "table" },
    headers: { type: "object" },
    password: { type: "password" },
    token: { type: "string", secret: true },
  };

  it("keeps a table default as rows through the round trip", () => {
    const rows = [
      { tag: "PgmLvl", type: "level", channels: "1" },
      { tag: "PgmMute", type: "mute", channels: "1" },
    ];
    const prefilled = prefillConfigValues({ blocks: rows }, schema);
    const coerced = coerceConfigValue(prefilled.blocks, "table", false);
    expect(coerced).toEqual({ ok: true, value: rows });
  });

  it("keeps an object default through the round trip", () => {
    const map = { Accept: "application/json" };
    const prefilled = prefillConfigValues({ headers: map }, schema);
    expect(coerceConfigValue(prefilled.headers, "object", false)).toEqual({ ok: true, value: map });
  });

  it("stringifies scalars and leaves empty defaults unset", () => {
    expect(prefillConfigValues({ host: "", port: 23, blocks: null }, schema)).toEqual({ port: "23" });
  });

  it("never pre-fills a secret", () => {
    const prefilled = prefillConfigValues({ password: "hunter2", token: "abc", port: 1 }, schema);
    expect(prefilled).toEqual({ port: "1" });
  });
});

// The Add and Edit dialogs hold every config key as text, including keys the
// driver's config_schema does not describe (a hidden default such as
// tcp_keepalive). Those keys must come back with the type they went in with:
// a saved "false" reads as on to the driver.
describe("buildConfigFromForm", () => {
  const schema = { enable_meters: { type: "boolean" }, port: { type: "integer" } };

  it.each([true, false])("keeps an undescribed %s a boolean", (value) => {
    const originals = { tcp_keepalive: value, enable_meters: false, port: 4321 };
    const form = { tcp_keepalive: String(value), enable_meters: "false", port: "4321" };
    expect(buildConfigFromForm(form, schema, originals, NONE)).toEqual({
      ok: true,
      config: { tcp_keepalive: value, enable_meters: false, port: 4321 },
    });
  });

  it("keeps an undescribed text value that looks like a number as text", () => {
    const result = buildConfigFromForm({ display_id: "1" }, {}, { display_id: "1" }, NONE);
    expect(result).toEqual({ ok: true, config: { display_id: "1" } });
  });

  it("keeps an edited undescribed text value as text", () => {
    const result = buildConfigFromForm({ display_id: "2" }, {}, { display_id: "1" }, NONE);
    expect(result).toEqual({ ok: true, config: { display_id: "2" } });
  });

  it("reads an edited undescribed boolean as a boolean", () => {
    const result = buildConfigFromForm({ tcp_keepalive: "false" }, {}, { tcp_keepalive: true }, NONE);
    expect(result).toEqual({ ok: true, config: { tcp_keepalive: false } });
  });

  it("stores an enum choice as the value the driver lists", () => {
    const enums = {
      channels: { type: "enum", values: [{ value: "2", label: "2" }, { value: "4", label: "4" }] },
      input: { type: "enum", values: [1, 2, 3] },
    };
    const result = buildConfigFromForm({ channels: "4", input: "2" }, enums, { channels: 4 }, NONE);
    expect(result).toEqual({ ok: true, config: { channels: "4", input: 2 } });
  });

  it("names the field whose value does not parse", () => {
    const result = buildConfigFromForm(
      { headers: "{" }, { headers: { type: "object", label: "Headers" } }, {}, NONE,
    );
    expect(result).toEqual({ ok: false, error: "Headers: must be valid JSON" });
  });

  it("leaves an empty value unset, except a cleared USB serial", () => {
    const result = buildConfigFromForm({ host: "", usb_serial: "" }, {}, {}, NONE);
    expect(result).toEqual({ ok: true, config: { usb_serial: "" } });
  });
});

// A config_schema field marked required is refused blank at Add and Edit
// Device, named by its label. Only what the device would run with counts: a
// driver default fills a field, and the connection picker's host and port are
// asked only of the connection that uses them.
// Through a bridge, the device needs the bridge and the port it is wired to:
// without the port the binding never resolves. The labels are the picker's.
describe("the bridge binding", () => {
  it("asks for the bridge, then the port on it", () => {
    expect(missingBridgeFields({}, "bridge")).toEqual(["Bridge"]);
    expect(missingBridgeFields({ bridge: "itach1" }, "bridge")).toEqual(["Bridge port"]);
    expect(missingBridgeFields({ bridge: "itach1", bridge_port: " " }, "bridge")).toEqual(["Bridge port"]);
    expect(missingBridgeFields({ bridge: "itach1", bridge_port: "serial:1" }, "bridge")).toEqual([]);
  });

  it("asks nothing of a network or direct serial connection", () => {
    expect(missingBridgeFields({}, "network")).toEqual([]);
    expect(missingBridgeFields({ bridge: "itach1" }, "serial")).toEqual([]);
  });

  it("asks an IR device only for the emitter port of a bridge it names", () => {
    expect(missingBridgeFields({}, "bridge", true)).toEqual([]);
    expect(missingBridgeFields({ bridge: "itach_ir" }, "bridge", true)).toEqual(["IR emitter port"]);
  });

  it("refuses the save with the bridge first, and never asks for an IP address", () => {
    const schema = {
      host: { type: "string", required: true, label: "IP Address" },
      model: { type: "string", required: true, label: "Model" },
    };
    const built = buildConfigFromForm({ bridge: "itach1" }, schema, {}, {
      defaults: {}, connection: "bridge",
    });
    expect(built).toEqual({ ok: false, error: "Bridge port and Model are required." });
  });
});

describe("required config fields", () => {
  const camera = {
    host: { type: "string", required: true, label: "IP Address" },
    port: { type: "integer", label: "VISCA Port" },
    model: {
      type: "enum",
      required: true,
      label: "Model",
      values: [{ value: "i12", label: "IV-CAM-I12" }, { value: "p20", label: "IV-CAM-P20" }],
    },
  };
  const defaults = { port: 5500 };

  it("refuses a blank required field by its label", () => {
    const result = buildConfigFromForm(
      { host: "10.0.0.5", port: "5500", model: "" }, camera, defaults,
      { defaults, connection: "network" },
    );
    expect(result).toEqual({ ok: false, error: "Model is required." });
  });

  it("accepts the field once it is filled", () => {
    const result = buildConfigFromForm(
      { host: "10.0.0.5", port: "5500", model: "i12" }, camera, defaults,
      { defaults, connection: "network" },
    );
    expect(result).toEqual({ ok: true, config: { host: "10.0.0.5", port: 5500, model: "i12" } });
  });

  it("names every blank required field in one sentence", () => {
    expect(missingRequiredFields({ host: " ", model: "" }, camera, defaults, "network")).toEqual([
      "IP Address",
      "Model",
    ]);
    expect(requiredFieldsMessage(["IP Address", "Model"])).toBe("IP Address and Model are required.");
    expect(requiredFieldsMessage(["IP Address", "Password", "Model"])).toBe(
      "IP Address, Password and Model are required.",
    );
  });

  it("asks for the IP address only on a network connection", () => {
    const form = { model: "i12" };
    expect(missingRequiredFields(form, camera, defaults, "network")).toEqual(["IP Address"]);
    expect(missingRequiredFields(form, camera, defaults, "serial")).toEqual([]);
    expect(missingRequiredFields(form, camera, defaults, "bridge")).toEqual([]);
  });

  it("asks for the port on a network or serial connection, never through a bridge", () => {
    const serialOnly = { port: { type: "string", required: true, label: "Serial Port" } };
    expect(missingRequiredFields({}, serialOnly, {}, "serial")).toEqual(["Serial Port"]);
    expect(missingRequiredFields({}, serialOnly, {}, "network")).toEqual(["Serial Port"]);
    expect(missingRequiredFields({}, serialOnly, {}, "bridge")).toEqual([]);
  });

  it("never asks for the picker's other fields", () => {
    const schema = {
      baudrate: { type: "integer", required: true, label: "Baud Rate" },
      bridge: { type: "string", required: true, label: "Bridge" },
    };
    expect(missingRequiredFields({}, schema, {}, "serial")).toEqual([]);
    expect(missingRequiredFields({}, schema, {}, "bridge")).toEqual([]);
  });

  it("counts a driver default as filled", () => {
    const schema = { port: { type: "integer", required: true, label: "Port" } };
    expect(missingRequiredFields({ port: "" }, schema, { port: 23 }, "network")).toEqual([]);
    expect(missingRequiredFields({ port: "" }, schema, { port: "" }, "network")).toEqual(["Port"]);
  });

  it("counts a secret that holds a value", () => {
    const schema = { password: { type: "string", secret: true, required: true, label: "Password" } };
    // A secret default is never pre-filled, but it is what the device runs with.
    expect(missingRequiredFields({}, schema, { password: "admin" }, "network")).toEqual([]);
    // The Edit form loads the stored value, shown masked.
    expect(missingRequiredFields({ password: "s3cret" }, schema, {}, "network")).toEqual([]);
    // Cleared, with no default to fall back on.
    expect(missingRequiredFields({ password: "" }, schema, {}, "network")).toEqual(["Password"]);
  });

  it("does not ask for a table field, which is set up on the device page", () => {
    const schema = { blocks: { type: "table", required: true, label: "Blocks" } };
    expect(missingRequiredFields({}, schema, {}, "network")).toEqual([]);
  });

  it("treats false and 0 as values", () => {
    const schema = {
      enabled: { type: "boolean", required: true, label: "Enabled" },
      level: { type: "integer", required: true, label: "Level" },
    };
    expect(missingRequiredFields({ enabled: "false", level: "0" }, schema, {}, "network")).toEqual([]);
  });
});
