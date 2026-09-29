import { describe, expect, it } from "vitest";
import { coerceConfigValue, prefillConfigValues } from "./deviceConfigCoerce";

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
