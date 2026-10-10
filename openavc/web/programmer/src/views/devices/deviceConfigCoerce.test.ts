import { describe, expect, it } from "vitest";
import { buildConfigFromForm, coerceConfigValue, prefillConfigValues } from "./deviceConfigCoerce";

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
    expect(buildConfigFromForm(form, schema, originals)).toEqual({
      ok: true,
      config: { tcp_keepalive: value, enable_meters: false, port: 4321 },
    });
  });

  it("keeps an undescribed text value that looks like a number as text", () => {
    const result = buildConfigFromForm({ display_id: "1" }, {}, { display_id: "1" });
    expect(result).toEqual({ ok: true, config: { display_id: "1" } });
  });

  it("keeps an edited undescribed text value as text", () => {
    const result = buildConfigFromForm({ display_id: "2" }, {}, { display_id: "1" });
    expect(result).toEqual({ ok: true, config: { display_id: "2" } });
  });

  it("reads an edited undescribed boolean as a boolean", () => {
    const result = buildConfigFromForm({ tcp_keepalive: "false" }, {}, { tcp_keepalive: true });
    expect(result).toEqual({ ok: true, config: { tcp_keepalive: false } });
  });

  it("stores an enum choice as the value the driver lists", () => {
    const enums = {
      channels: { type: "enum", values: [{ value: "2", label: "2" }, { value: "4", label: "4" }] },
      input: { type: "enum", values: [1, 2, 3] },
    };
    const result = buildConfigFromForm({ channels: "4", input: "2" }, enums, { channels: 4 });
    expect(result).toEqual({ ok: true, config: { channels: "4", input: 2 } });
  });

  it("names the field whose value does not parse", () => {
    const result = buildConfigFromForm(
      { headers: "{" }, { headers: { type: "object", label: "Headers" } }, {},
    );
    expect(result).toEqual({ ok: false, error: "Headers: must be valid JSON" });
  });

  it("leaves an empty value unset, except a cleared USB serial", () => {
    const result = buildConfigFromForm({ host: "", usb_serial: "" }, {}, {});
    expect(result).toEqual({ ok: true, config: { usb_serial: "" } });
  });
});
