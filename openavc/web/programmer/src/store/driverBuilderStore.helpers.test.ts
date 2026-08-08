import { describe, it, expect } from "vitest";
import { cloneDraft } from "./driverBuilderStore.helpers";
import type { DriverDefinition } from "../api/types";

/**
 * `DriverDefinition` declares the collections as always-present, but that is
 * a promise about an editor draft: the contract requires only id/name/
 * transport, so a hand-authored .avcdriver, an imported file, or a driver
 * created through the API can omit any of them. TypeScript cannot see the
 * difference, so an editor indexing one (`draft.default_config[key]`) throws
 * and takes its whole tab down. cloneDraft is where that gets normalized.
 */
describe("cloneDraft", () => {
  // Minimal driver: exactly what the contract requires, nothing more.
  const minimal = {
    id: "acme_widget",
    name: "Acme Widget",
    transport: "osc",
  } as unknown as DriverDefinition;

  it("fills in every collection the editors index without a guard", () => {
    const draft = cloneDraft(minimal);

    // The Connection tab reads draft.default_config[key] directly — this is
    // the access that crashed with "reading 'port'".
    expect(() => (draft.default_config as Record<string, unknown>)["port"])
      .not.toThrow();

    expect(draft.default_config).toEqual({});
    expect(draft.config_schema).toEqual({});
    expect(draft.state_variables).toEqual({});
    expect(draft.commands).toEqual({});
    expect(draft.polling).toEqual({});
    expect(draft.responses).toEqual([]);
  });

  it("keeps every value a well-formed driver already has", () => {
    const full = {
      ...minimal,
      default_config: { port: 9000, host: "192.168.1.50" },
      state_variables: { power: { type: "boolean" } },
      commands: { on: { label: "On", send: "/on", params: {} } },
      responses: [{ match: "x" }],
    } as unknown as DriverDefinition;

    const draft = cloneDraft(full);

    expect(draft.default_config).toEqual({ port: 9000, host: "192.168.1.50" });
    expect(draft.state_variables).toEqual({ power: { type: "boolean" } });
    expect(draft.commands).toEqual({
      on: { label: "On", send: "/on", params: {} },
    });
    expect(draft.responses).toEqual([{ match: "x" }]);
  });

  it("does not invent keys the driver did not have beyond those collections", () => {
    const draft = cloneDraft(minimal) as unknown as Record<string, unknown>;

    // Backfilling a scalar would write a value into the driver on re-export —
    // a delimiter of "\r" on an HTTP driver, say. Only collections are added.
    expect(draft.delimiter).toBeUndefined();
    expect(draft.manufacturer).toBeUndefined();
    expect(draft.frame_parser).toBeUndefined();
  });

  it("clones rather than aliasing the source definition", () => {
    const source = {
      ...minimal,
      default_config: { port: 9000 },
    } as unknown as DriverDefinition;
    const draft = cloneDraft(source);

    (draft.default_config as Record<string, unknown>)["port"] = 1234;

    expect((source.default_config as Record<string, unknown>)["port"]).toBe(9000);
  });
});
