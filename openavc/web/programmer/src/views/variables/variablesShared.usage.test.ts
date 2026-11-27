import { describe, it, expect } from "vitest";
import { buildStateUsageMap, buildUsageMap } from "./variablesShared";
import type { ProjectConfig } from "../../api/types";

// What the Variables view lists under Used By, and what Delete Unused treats
// as unused: a variable nobody reads is offered for deletion, so every place
// that reads one has to be counted.

const cond = (key: string, value: unknown = "on") => ({ key, operator: "eq", value });

function project(parts: Partial<Record<"macros" | "elements" | "masters", unknown[]>>): ProjectConfig {
  return {
    project: { id: "p", name: "P" },
    devices: [],
    variables: [{ id: "shown" }, { id: "wanted" }, { id: "limit" }, { id: "on_master" }],
    macros: parts.macros ?? [],
    ui: {
      settings: {},
      master_elements: parts.masters ?? [],
      pages: [{ id: "main", name: "Main", elements: parts.elements ?? [], layouts: [] }],
    },
  } as unknown as ProjectConfig;
}

const shownWhen = (id: string, visible_when: unknown) => ({ id, type: "label", bindings: { show: { visible_when } } });

describe("Used By counts what a control's visibility reads", () => {
  it("counts a visibility condition's key, on a page and on a master element", () => {
    const p = project({
      elements: [shownWhen("l1", { any: [cond("var.shown"), cond("device.proj.power")] })],
      masters: [shownWhen("m1", cond("var.on_master"))],
    });
    const vars = buildUsageMap(p);
    expect(vars.get("shown")?.map((u) => u.detail)).toEqual(["Visibility condition"]);
    expect(vars.get("on_master")?.map((u) => u.detail)).toEqual(["Visibility condition"]);
    const keys = buildStateUsageMap(p);
    expect(keys.get("device.proj.power")?.map((u) => u.detail)).toEqual(["Visibility condition"]);
  });

  it("counts the key a visibility condition's value names", () => {
    const p = project({ elements: [shownWhen("l1", cond("device.proj.power", "$var.wanted"))] });
    expect(buildUsageMap(p).get("wanted")?.map((u) => u.detail)).toEqual(["Visibility condition"]);
    expect(buildStateUsageMap(p).get("var.wanted")?.map((u) => u.detail)).toEqual(["Visibility condition"]);
  });
});
