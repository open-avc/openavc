import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { resolve } from "path";
import { removePage, removePageAndScrubRefs, renameElement, validateElementId } from "./uiBuilderHelpers";
import { removePageFromGroups } from "./projectRewrites";
import { elementActions } from "./reviewActions";
import type { MacroConfig, MasterElement, PageGroup, ScriptConfig, UIElement, UIPage, VariableConfig } from "../../api/types";

// Renaming a control and deleting a page each rewrite what the rest of the
// project says about them. What they rewrite, what they leave, and which
// arrays and objects come back as they were (the undo history snapshots only
// what a change hands back new).

const el = (id: string, extra: Record<string, unknown> = {}) =>
  ({ id, type: "button", style: {}, bindings: {}, ...extra }) as unknown as UIElement;
const page = (id: string, elements: UIElement[], extra: Record<string, unknown> = {}) =>
  ({ id, name: id, elements, layouts: [], ...extra }) as unknown as UIPage;
const macro = (id: string, extra: Record<string, unknown>) =>
  ({ id, name: id, steps: [], ...extra }) as unknown as MacroConfig;

describe("validateElementId", () => {
  const pages = [page("p1", [el("btn"), el("other")]), page("p2", [el("deep")])];
  const masters = [{ id: "logo" }] as unknown as MasterElement[];

  it("refuses an empty id", () => {
    expect(validateElementId("", "btn", pages, masters)).toBe("ID cannot be empty.");
  });

  it("takes the element's own id back, whatever its spelling", () => {
    expect(validateElementId("btn", "btn", pages, masters)).toBeNull();
    expect(validateElementId("Bad Id", "Bad Id", pages, masters)).toBeNull();
  });

  it("refuses anything but a lowercase letter then letters, digits and underscores", () => {
    const said =
      "ID must start with a lowercase letter and contain only lowercase letters, digits, and underscores.";
    for (const id of ["1btn", "_btn", "Btn", "btn-2", "btn 2", "bt.n", "btñ"]) {
      expect(validateElementId(id, "btn", pages, masters)).toBe(said);
    }
    expect(validateElementId("b", "btn", pages, masters)).toBeNull();
    expect(validateElementId("b_2_x", "btn", pages, masters)).toBeNull();
  });

  it("refuses an id another element on any page or a master already has", () => {
    expect(validateElementId("other", "btn", pages, masters)).toBe('An element with ID "other" already exists.');
    expect(validateElementId("deep", "btn", pages, masters)).toBe('An element with ID "deep" already exists.');
    expect(validateElementId("logo", "btn", pages, masters)).toBe('An element with ID "logo" already exists.');
  });

  it("checks the spelling before the collision", () => {
    const odd = [page("p1", [el("Taken")])];
    expect(validateElementId("Taken", "btn", odd, [])).toMatch(/^ID must start/);
  });
});

describe("renameElement", () => {
  const rename = (
    over: { pages?: UIPage[]; masters?: MasterElement[]; macros?: MacroConfig[]; variables?: VariableConfig[]; scripts?: ScriptConfig[] },
    oldId = "btn",
    newId = "go",
  ) => {
    const p = {
      pages: over.pages ?? [page("p1", [el("btn")])],
      masters: over.masters ?? [],
      macros: over.macros ?? [],
      variables: over.variables ?? [],
      scripts: over.scripts ?? [],
    };
    return { p, r: renameElement(p.pages, p.masters, p.macros, p.variables, p.scripts, oldId, newId) };
  };

  it("rewrites a key that starts ui.<old>. and nothing that only looks like one", () => {
    const keys = ["ui.btn.value", "ui.btn.", "ui.btn", "ui.btnx.value", "ui.btn2.value", "xui.btn.value", "device.btn.power", "UI.btn.value"];
    const steps = keys.map((key) => ({ action: "state.set", key }));
    const { r } = rename({ macros: [macro("m", { steps })] });
    expect(r.macros[0].steps.map((s) => s.key)).toEqual([
      "ui.go.value", "ui.go.", "ui.btn", "ui.btnx.value", "ui.btn2.value", "xui.btn.value", "device.btn.power", "UI.btn.value",
    ]);
  });

  it("rewrites every place a macro step holds a key, branches included", () => {
    const step = {
      action: "conditional",
      key: "ui.btn.a",
      params: { key: "ui.btn.b", nested: [{ state_key: "ui.btn.c" }, { source_key: "ui.btn.d", value: "ui.btn.e" }], text: "ui.btn.f" },
      condition: { key: "ui.btn.g", operator: "eq", value: "ui.btn.h" },
      skip_if: { key: "ui.btn.i", operator: "truthy" },
      then_steps: [{ action: "state.set", key: "ui.btn.j" }, { action: "delay", seconds: 1 }],
      else_steps: [{ action: "conditional", condition: { key: "ui.btn.k", operator: "falsy" }, then_steps: [{ action: "state.set", key: "ui.btn.l" }] }],
      value: "ui.btn.m",
      event: "ui.btn.n",
    };
    const { r } = rename({ macros: [macro("m", { steps: [step] })] });
    expect(r.macros[0].steps[0]).toEqual({
      action: "conditional",
      key: "ui.go.a",
      params: { key: "ui.go.b", nested: [{ state_key: "ui.go.c" }, { source_key: "ui.go.d", value: "ui.btn.e" }], text: "ui.btn.f" },
      condition: { key: "ui.go.g", operator: "eq", value: "ui.btn.h" },
      skip_if: { key: "ui.go.i", operator: "truthy" },
      then_steps: [{ action: "state.set", key: "ui.go.j" }, { action: "delay", seconds: 1 }],
      else_steps: [{ action: "conditional", condition: { key: "ui.go.k", operator: "falsy" }, then_steps: [{ action: "state.set", key: "ui.go.l" }] }],
      value: "ui.btn.m",
      event: "ui.btn.n",
    });
  });

  it("keeps the field order of a rewritten step", () => {
    const step = { skip_if: { key: "ui.btn.a", operator: "truthy" }, action: "state.set", key: "ui.btn.b", value: 1 };
    const { r } = rename({ macros: [macro("m", { steps: [step] })] });
    expect(Object.keys(r.macros[0].steps[0])).toEqual(["skip_if", "action", "key", "value"]);
  });

  it("hands back each untouched step, branch, condition and macro as it was", () => {
    const quiet = { action: "conditional", condition: { key: "var.x", operator: "truthy" }, then_steps: [{ action: "delay" }], else_steps: [{ action: "delay" }], params: { key: "var.y" }, skip_if: { key: "var.z", operator: "falsy" } };
    const loud = { action: "conditional", condition: { key: "var.x", operator: "truthy" }, then_steps: [{ action: "delay" }], else_steps: [{ action: "state.set", key: "ui.btn.v" }] };
    const still = macro("still", { steps: [quiet], triggers: [{ id: "t", type: "state_change", enabled: true, state_key: "var.a", conditions: [{ key: "var.b", operator: "eq" }] }] });
    const moved = macro("moved", { steps: [quiet, loud] });
    const { p, r } = rename({ macros: [still, moved] });
    expect(r.macros).not.toBe(p.macros);
    expect(r.macros[0]).toBe(still);
    const after = r.macros[1].steps;
    expect(after[0]).toBe(quiet);
    expect(after[1]).not.toBe(loud);
    expect(after[1].condition).toBe(loud.condition);
    expect(after[1].then_steps).toBe(loud.then_steps);
    expect(after[1].else_steps).not.toBe(loud.else_steps);
  });

  it("rewrites a trigger's state key and its conditions, and keeps what is untouched", () => {
    const untouchedCondition = { key: "var.b", operator: "eq" };
    const t1 = { id: "t1", type: "state_change", enabled: true, state_key: "ui.btn.value", conditions: [untouchedCondition, { key: "ui.btn.on", operator: "truthy" }] };
    const t2 = { id: "t2", type: "event", enabled: true, event_pattern: "ui.press.btn", conditions: [{ key: "var.q", operator: "truthy" }] };
    const steps = [{ action: "delay", seconds: 1 }];
    const m = macro("m", { steps, triggers: [t1, t2] });
    const { r } = rename({ macros: [m] });
    const after = r.macros[0];
    expect(after.triggers?.[0]).toEqual({ ...t1, state_key: "ui.go.value", conditions: [untouchedCondition, { key: "ui.go.on", operator: "truthy" }] });
    expect(after.triggers?.[0].conditions?.[0]).toBe(untouchedCondition);
    expect(after.triggers?.[1]).toEqual({ ...t2, event_pattern: "ui.press.go" });
    // The steps of a macro whose trigger alone moved come back as an equal
    // new array.
    expect(after.steps).toEqual(steps);
    expect(after.steps).not.toBe(steps);
    expect(after.steps[0]).toBe(steps[0]);
  });

  it("follows the control's panel events in an event pattern, and no other pattern", () => {
    const patterns = ["ui.press.btn", "ui.*.btn", "ui.toggle_off.btn", "ui.press.btn2", "ui.press.btn.x", "ui.btn.value", "ui..btn", "custom.press.btn", "ui.press.*"];
    const triggers = patterns.map((event_pattern, i) => ({ id: `t${i}`, type: "event", enabled: true, event_pattern }));
    const { r } = rename({ macros: [macro("m", { triggers })] });
    expect(r.macros[0].triggers?.map((t) => t.event_pattern)).toEqual([
      "ui.press.go", "ui.*.go", "ui.toggle_off.go", "ui.press.btn2", "ui.press.btn.x", "ui.btn.value", "ui..btn", "custom.press.btn", "ui.press.*",
    ]);
    expect(r.macros[0].triggers?.[3]).toBe(triggers[3]);
  });

  it("follows a $ui.<id>. value reference in an action, a step's value and its params", () => {
    const action = { action: "state.set", key: "var.a", value: "$ui.btn.value" };
    const untouched = { action: "state.set", key: "var.b", value: "$ui.btn2.value" };
    const plain = { action: "state.set", key: "var.c", value: "ui.btn.value" };
    const b = el("b", { bindings: { do: { press: [action, untouched, plain] } } });
    const step = { action: "state.set", key: "var.d", value: "$ui.btn.level" };
    const command = { action: "device.command", device: "amp", command: "set", params: { level: "$ui.btn.value" } };
    const { r } = rename({ pages: [page("p1", [el("btn"), b])], macros: [macro("m", { steps: [step, command] })] });
    const press = (r.pages[0].elements[1].bindings as unknown as { do: { press: unknown[] } }).do.press;
    expect(press).toEqual([{ ...action, value: "$ui.go.value" }, untouched, plain]);
    expect(press[1]).toBe(untouched);
    expect(r.macros[0].steps).toEqual([{ ...step, value: "$ui.go.level" }, { ...command, params: { level: "$ui.go.value" } }]);
  });

  it("a control named constructor moves only the layouts that hold it", () => {
    const box = { x: 1, y: 2, w: 3, h: 4 };
    const without = { id: "landscape", placements: { a: box }, hidden: [] };
    const { r } = rename({ pages: [page("p1", [el("constructor")], { layouts: [without] })] }, "constructor", "go");
    expect(r.pages[0].layouts?.[0]).toBe(without);
  });

  it("a trigger with neither field, or empty ones, is left as it was", () => {
    const bare = { id: "t", type: "startup", enabled: true };
    const empty = { id: "u", type: "state_change", enabled: true, state_key: "", conditions: [] };
    const keyless = { id: "v", type: "state_change", enabled: true, conditions: [{ operator: "truthy" }, { key: "", operator: "eq" }] };
    const m = macro("m", { triggers: [bare, empty, keyless] });
    const { r } = rename({ macros: [m] });
    expect(r.macros[0]).toBe(m);
  });

  it("a macro with no triggers that changes keeps saying it has none", () => {
    const m = macro("m", { steps: [{ action: "state.set", key: "ui.btn.v" }] });
    const { r } = rename({ macros: [m] });
    expect("triggers" in r.macros[0]).toBe(true);
    expect(r.macros[0].triggers).toBeUndefined();
  });

  it("rewrites a variable's source key, and only that", () => {
    const v1 = { id: "a", source_key: "ui.btn.value", default: "ui.btn.x" };
    const v2 = { id: "b", source_key: "var.c" };
    const v3 = { id: "c" };
    const { p, r } = rename({ variables: [v1, v2, v3] as unknown as VariableConfig[] });
    expect(r.variables[0]).toEqual({ id: "a", source_key: "ui.go.value", default: "ui.btn.x" });
    expect(r.variables[1]).toBe(v2);
    expect(r.variables[2]).toBe(v3);
    expect(r.variables).not.toBe(p.variables);
  });

  it("renames the element, its children's parent link and its bindings' keys", () => {
    const bindings = {
      show: { value: { key: "ui.btn.level" }, visible_when: { key: "ui.btn.on", operator: "truthy" } },
      do: { press: [{ action: "state.set", key: "ui.btn.pressed", value: "$ui.btn.level" }] },
    };
    const child = el("kid", { parent: "btn", bindings: { show: { text: { key: "ui.btn.label" } } } });
    const looseBefore = el("loose", { parent: null });
    const { r } = rename({ pages: [page("p1", [el("btn", { bindings }), child, looseBefore])] });
    const [b, k, loose] = r.pages[0].elements;
    expect(b.id).toBe("go");
    expect(b.bindings).toEqual({
      show: { value: { key: "ui.go.level" }, visible_when: { key: "ui.go.on", operator: "truthy" } },
      do: { press: [{ action: "state.set", key: "ui.go.pressed", value: "$ui.go.level" }] },
    });
    expect(k.parent).toBe("go");
    expect((k.bindings as Record<string, unknown>).show).toEqual({ text: { key: "ui.go.label" } });
    expect(loose).toBe(looseBefore);
  });

  it("rewrites an element's untyped visibility field too", () => {
    const vis = { key: "ui.btn.on", operator: "truthy", nested: [{ state_key: "ui.btn.x" }] };
    const other = el("other", { visibility: vis });
    const { r } = rename({ pages: [page("p1", [el("btn"), other])] });
    const after = r.pages[0].elements[1] as unknown as Record<string, unknown>;
    expect(after.visibility).toEqual({ key: "ui.go.on", operator: "truthy", nested: [{ state_key: "ui.go.x" }] });
    expect(Object.keys(after)).toEqual(Object.keys(other));
  });

  it("a key field holding something other than text is walked into", () => {
    const bindings = { show: { key: { key: "ui.btn.deep" }, state_key: 5, source_key: null } };
    const { r } = rename({ pages: [page("p1", [el("btn"), el("other", { bindings })])] });
    expect(r.pages[0].elements[1].bindings).toEqual({ show: { key: { key: "ui.go.deep" }, state_key: 5, source_key: null } });
  });

  it("hands back each untouched element, page, binding array and object as it was", () => {
    const quietBindings = { do: { press: [{ action: "macro", macro: "m" }] }, show: { text: { key: "var.t" } } };
    const quiet = el("quiet", { bindings: quietBindings, visibility: { key: "var.v" } });
    const press = [{ action: "state.set", key: "var.a" }, { action: "state.set", key: "ui.btn.b" }];
    const loud = el("loud", { bindings: { do: { press, release: [{ action: "delay" }] } } });
    const p2 = page("p2", [el("solo")]);
    const { p, r } = rename({ pages: [page("p1", [quiet, loud, el("btn")]), p2] });
    expect(r.pages).not.toBe(p.pages);
    expect(r.pages[1]).toBe(p2);
    const [q, l] = r.pages[0].elements;
    expect(q).toBe(quiet);
    expect(l).not.toBe(loud);
    const lb = l.bindings as unknown as { do: { press: unknown[]; release: unknown } };
    expect(lb.do.press[0]).toBe(press[0]);
    expect(lb.do.release).toBe((loud.bindings as unknown as { do: { release: unknown } }).do.release);
  });

  it("renames a master element's id and keys, and hands back untouched masters", () => {
    const logo = { id: "logo", type: "image", bindings: {} };
    const master = { id: "btn", type: "button", bindings: { show: { text: { key: "ui.btn.t" } } } };
    const { p, r } = rename({ pages: [page("p1", [])], masters: [logo, master] as unknown as MasterElement[] });
    expect(r.master_elements).not.toBe(p.masters);
    expect(r.master_elements[0]).toBe(logo);
    expect(r.master_elements[1]).toEqual({ id: "go", type: "button", bindings: { show: { text: { key: "ui.go.t" } } } });
    const none = rename({ pages: [page("p1", [])], masters: [logo] as unknown as MasterElement[] });
    expect(none.r.master_elements).toBe(none.p.masters);
  });

  it("a page whose layouts hold the id moves its placement and hidden entry; one that does not keeps them", () => {
    const box = { x: 1, y: 2, w: 3, h: 4 };
    const holds = { id: "landscape", placements: { a: box, btn: box, z: box }, hidden: ["btn"] };
    const hiddenOnly = { id: "portrait", placements: { a: box }, hidden: ["a", "btn"] };
    const neither = { id: "wide", placements: { a: box }, hidden: [] };
    const noPlacements = { id: "bare", hidden: ["btn"] };
    const { r } = rename({ pages: [page("p1", [el("btn")], { layouts: [holds, hiddenOnly, neither, noPlacements] })] });
    const L = r.pages[0].layouts as unknown as Record<string, unknown>[];
    expect(Object.keys(L[0].placements as object)).toEqual(["a", "go", "z"]);
    expect(L[0].hidden).toEqual(["go"]);
    expect(L[1].placements).toBe(hiddenOnly.placements);
    expect(L[1].hidden).toEqual(["a", "go"]);
    expect(L[2]).toBe(neither);
    expect(L[3]).toEqual({ id: "bare", hidden: ["go"] });
  });

  it("a page whose layout alone holds the id, with no such control on it, still moves it", () => {
    const box = { x: 1, y: 2, w: 3, h: 4 };
    const orphan = { id: "landscape", placements: { btn: box }, hidden: ["btn"] };
    const other = el("other");
    const p1 = page("p1", [other], { layouts: [orphan] });
    const { p, r } = rename({ pages: [p1, page("p2", [el("btn")])] });
    expect(r.pages[0]).not.toBe(p1);
    expect(r.pages[0].elements).toBe(p.pages[0].elements);
    expect(r.pages[0].layouts).toEqual([{ id: "landscape", placements: { go: box }, hidden: ["go"] }]);
  });

  it("a page with no layouts at all keeps that", () => {
    const bare = { id: "p1", name: "p1", elements: [el("btn")] } as unknown as UIPage;
    const { r } = rename({ pages: [bare] });
    expect(r.pages[0].layouts).toBeUndefined();
    expect("layouts" in r.pages[0]).toBe(true);
    expect(r.pages[0].elements[0].id).toBe("go");
  });

  it("lists every script with a file and an id for review, whatever it says", () => {
    const scripts = [
      { id: "a", file: "a.py" },
      { id: "", file: "b.py" },
      { id: "c", file: "" },
      { id: "d", file: "d.py" },
    ] as unknown as ScriptConfig[];
    const { r } = rename({ scripts });
    expect(r.scriptsToReview).toEqual(["a.py", "d.py"]);
  });

  it("a rename that reaches nothing hands every array back as it was", () => {
    const { p, r } = rename({ pages: [page("p1", [el("other")])] }, "ghost", "spirit");
    expect(r.pages).toBe(p.pages);
    expect(r.macros).toBe(p.macros);
    expect(r.variables).toBe(p.variables);
  });
});

describe("removePage", () => {
  const nav = (pg: string) => ({ action: "ui.navigate", page: pg });

  it("drops the page and empties a page nav that pointed at it", () => {
    const pages = [
      page("p1", [el("n1", { type: "page_nav", target_page: "p2" }), el("n2", { type: "page_nav", target_page: "p3" }), el("b", { target_page: "p2" })]),
      page("p2", []),
    ];
    const after = removePage(pages, "p2");
    expect(after.map((p) => p.id)).toEqual(["p1"]);
    expect(after[0].elements[0].target_page).toBe("");
    expect(after[0].elements[1].target_page).toBe("p3");
    expect(after[0].elements[2].target_page).toBe("p2");
  });

  it("removes moves to the page from each action slot, emptying a slot that held only those", () => {
    const keep = { action: "macro", macro: "m" };
    const bindings = {
      do: {
        press: [nav("gone"), keep, nav("other"), null, "text", nav("gone")],
        release: [nav("gone")],
        hold: nav("gone"),
        change: nav("other"),
        route: [keep],
        submit: [],
        custom_slot: [nav("gone"), keep],
        only_gone: [nav("gone")],
      },
      show: { text: { key: "var.x" } },
    };
    const after = removePage([page("p1", [el("b", { bindings })])], "gone")[0].elements[0];
    const d = (after.bindings as unknown as { do: Record<string, unknown> }).do;
    expect(d).toEqual({
      press: [keep, nav("other"), null, "text"],
      change: nav("other"),
      route: [keep],
      submit: [],
      custom_slot: [keep],
    });
    expect(Object.keys(d)).toEqual(["press", "change", "route", "submit", "custom_slot"]);
    expect(d.route).toBe(bindings.do.route);
    expect((after.bindings as unknown as { show: unknown }).show).toBe(bindings.show);
  });

  it("removes a value_map option, an off action and a long press action that move to the page", () => {
    const keep = { action: "macro", macro: "m" };
    const vm = { action: "value_map", map: { b: keep, a: nav("gone"), c: { ...keep, off_action: nav("gone") } } };
    const toggle = { action: "state.set", key: "var.x", mode: "toggle", off_action: nav("gone"), hold_action: { ...keep, off_action: nav("other") } };
    const quiet = { action: "value_map", map: { a: keep } };
    const bindings = { do: { change: [vm, quiet], press: [toggle] } };
    const pages = [page("p1", [el("b", { bindings })]), page("gone", [])];
    const after = removePage(pages, "gone")[0].elements[0];
    const d = (after.bindings as unknown as { do: Record<string, unknown[]> }).do;
    expect(d.change).toEqual([{ action: "value_map", map: { b: keep, c: keep } }, quiet]);
    expect(Object.keys((d.change[0] as { map: object }).map)).toEqual(["b", "c"]);
    expect(d.change[1]).toBe(quiet);
    expect(d.press).toEqual([{ action: "state.set", key: "var.x", mode: "toggle", hold_action: toggle.hold_action }]);
    expect((d.press[0] as Record<string, unknown>).hold_action).toBe(toggle.hold_action);
  });

  it("removes a matrix row's own moves to the page, a route left empty kept empty", () => {
    const keep = { action: "macro", macro: "m" };
    const kept = { value: 1, route: [keep] };
    const config = {
      sources: [{ value: 1 }],
      destinations: [kept, { value: 2, route: [nav("gone"), keep] }, { value: 3, route: [nav("gone")] }, { value: 4 }],
    };
    const mx = el("mx", { type: "matrix", matrix_config: config });
    const after = removePage([page("p1", [mx])], "gone")[0].elements[0] as unknown as { matrix_config: typeof config };
    expect(after.matrix_config.destinations).toEqual([kept, { value: 2, route: [keep] }, { value: 3, route: [] }, { value: 4 }]);
    expect(after.matrix_config.destinations[0]).toBe(kept);
    const generated = el("g", { type: "matrix", matrix_config: { sources: { from: 1, count: 2 }, destinations: { from: 1, count: 2 } } });
    expect(removePage([page("p1", [generated])], "gone")[0].elements[0]).toBe(generated);
  });

  it("hands back new pages and element arrays, and each element nothing pointed at as it was", () => {
    const quiet = el("q", { bindings: { do: { press: [nav("p3")] } } });
    const noDo = el("n", { bindings: { show: {} } });
    const noBindings = { id: "x", type: "label" } as unknown as UIElement;
    const p1 = page("p1", [quiet, noDo, noBindings]);
    const after = removePage([p1, page("p2", [])], "p2");
    expect(after[0]).not.toBe(p1);
    expect(after[0].elements).not.toBe(p1.elements);
    expect(after[0].elements[0]).toBe(quiet);
    expect(after[0].elements[1]).toBe(noDo);
    expect(after[0].elements[2]).toBe(noBindings);
  });

  it("an element that changes keeps its other bindings and fields", () => {
    const b = el("b", { label: "Go", bindings: { show: { text: { key: "var.t" } }, do: { press: [nav("gone")] } } });
    const after = removePage([page("p1", [b])], "gone")[0].elements[0] as unknown as Record<string, unknown>;
    expect(after).toEqual({ ...b, bindings: { show: { text: { key: "var.t" } }, do: {} } });
    expect(Object.keys(after.bindings as object)).toEqual(["show", "do"]);
  });
});

describe("removePageAndScrubRefs", () => {
  const masterOn = (id: string, pages: unknown) => ({ id, type: "label", pages }) as unknown as MasterElement;
  const cond = (key: string, value: unknown) => ({ key, operator: "eq", value });

  it("takes the page out of each master's page list, an emptied list meaning every page", () => {
    const all = masterOn("a", "*");
    const odd = masterOn("o", "p2");
    const other = masterOn("b", ["p1", "p3"]);
    const some = masterOn("c", ["p1", "p2", "p3"]);
    const only = masterOn("d", ["p2"]);
    const navigates = { id: "n", type: "button", pages: "*", bindings: { do: { press: [{ action: "ui.navigate", page: "p2" }] } } } as unknown as MasterElement;
    const r = removePageAndScrubRefs([page("p2", [])], "p2", [all, odd, other, some, only, navigates], []);
    expect(r.masterElements[5]).toEqual({ ...navigates, bindings: { do: {} } });
    expect(r.masterElements[0]).toBe(all);
    expect(r.masterElements[1]).toBe(odd);
    expect(r.masterElements[2]).toBe(other);
    expect(r.masterElements[3]).toEqual({ ...some, pages: ["p1", "p3"] });
    expect(r.masterElements[4]).toEqual({ ...only, pages: "*" });
  });

  it("hands back the masters array as it was when no master listed the page", () => {
    const masters = [masterOn("a", "*"), masterOn("b", ["p1"])];
    expect(removePageAndScrubRefs([], "p2", masters, []).masterElements).toBe(masters);
  });

  it("drops a trigger condition that matches on the page being current, and nothing else", () => {
    const keepKey = cond("system.current_page", "p1");
    const keepOther = cond("var.page", "p2");
    const keepOp = { key: "system.current_page", operator: "ne", value: "p3" };
    const t1 = { id: "t1", type: "startup", enabled: true, conditions: [keepKey, cond("system.current_page", "p2"), keepOther, keepOp] };
    const t2 = { id: "t2", type: "startup", enabled: true, conditions: [keepOther] };
    const t3 = { id: "t3", type: "startup", enabled: true };
    const m = macro("m", { triggers: [t1, t2, t3] });
    const r = removePageAndScrubRefs([], "p2", [], [m]);
    const after = r.macros[0];
    expect(after).toEqual({ ...m, triggers: [{ ...t1, conditions: [keepKey, keepOther, keepOp] }, t2, t3] });
    expect(after.triggers?.[1]).toBe(t2);
    expect(after.triggers?.[2]).toBe(t3);
    expect(after.triggers?.[0].conditions?.[0]).toBe(keepKey);
  });

  it("matches the page by value only, whatever the operator", () => {
    const t = { id: "t", type: "startup", enabled: true, conditions: [{ key: "system.current_page", operator: "ne", value: "p2" }] };
    const r = removePageAndScrubRefs([], "p2", [], [macro("m", { triggers: [t] })]);
    expect(r.macros[0].triggers?.[0].conditions).toEqual([]);
  });

  it("hands back the macros, and each untouched macro, as they were", () => {
    const quiet = macro("q", { triggers: [{ id: "t", type: "startup", enabled: true, conditions: [cond("var.x", 1)] }] });
    const none = macro("n", {});
    const macros = [quiet, none];
    expect(removePageAndScrubRefs([], "p2", [], macros).macros).toBe(macros);
    const loud = macro("l", { triggers: [{ id: "t", type: "startup", enabled: true, conditions: [cond("system.current_page", "p2")] }] });
    const r = removePageAndScrubRefs([], "p2", [], [quiet, loud, none]);
    expect(r.macros[0]).toBe(quiet);
    expect(r.macros[2]).toBe(none);
    expect(r.macros[1]).not.toBe(loud);
  });

  it("removes the page itself the way removePage does", () => {
    const pages = [page("p1", [el("n", { type: "page_nav", target_page: "p2" })]), page("p2", [])];
    const r = removePageAndScrubRefs(pages, "p2", [], []);
    expect(r.pages).toEqual(removePage(pages, "p2"));
    expect(Object.keys(r)).toEqual(["pages", "masterElements", "macros"]);
  });

  it("drops a macro step that moves to the page, in its branches too, and keeps the rest", () => {
    const keep = { action: "delay", seconds: 1 };
    const back = { action: "ui.navigate", page: "p1" };
    const branchy = { action: "conditional", then_steps: [{ action: "ui.navigate", page: "p2" }, keep], else_steps: [back] };
    const m = macro("m", { steps: [{ action: "ui.navigate", page: "p2" }, branchy, keep] });
    const quiet = macro("q", { steps: [back, keep] });
    const r = removePageAndScrubRefs([], "p2", [], [m, quiet]);
    expect(r.macros[0].steps).toEqual([{ ...branchy, then_steps: [keep] }, keep]);
    expect(r.macros[0].steps[0].else_steps).toBe(branchy.else_steps);
    expect(r.macros[1]).toBe(quiet);
  });

  it("takes the page out of each page group, and hands back the groups untouched when none held it", () => {
    const holds = { name: "A", pages: ["p1", "p2", "p3"] } as unknown as PageGroup;
    const not = { name: "B", pages: ["p1"] } as unknown as PageGroup;
    const after = removePageFromGroups([holds, not], "p2");
    expect(after).toEqual([{ name: "A", pages: ["p1", "p3"] }, not]);
    expect(after[1]).toBe(not);
    const groups = [not];
    expect(removePageFromGroups(groups, "p2")).toBe(groups);
  });

  it("leaves what the AI's page delete leaves", () => {
    // tests/test_ui_tools_doors_pinned.py runs the AI's delete_ui_page on the same project.
    type Side = { pages: UIPage[]; master_elements: MasterElement[]; macros: MacroConfig[]; page_groups: PageGroup[] };
    const fixture = JSON.parse(
      readFileSync(resolve(process.cwd(), "../../../tests/fixtures/page_delete_cases.json"), "utf-8"),
    ) as { page_id: string; before: Side; after: Side };
    const { before, after, page_id } = fixture;
    const r = removePageAndScrubRefs(before.pages, page_id, before.master_elements, before.macros);
    expect(r.pages).toEqual(after.pages);
    expect(r.masterElements).toEqual(after.master_elements);
    expect(r.macros).toEqual(after.macros);
    expect(removePageFromGroups(before.page_groups, page_id)).toEqual(after.page_groups);
  });

  it("leaves no move to the page in any place the page review walks, on a page or a master", () => {
    // elementActions is the list of places an action lives; a place added
    // there and not to page delete fails this.
    const go = { action: "ui.navigate", page: "gone" };
    const keep = { action: "macro", macro: "m" };
    const bindings = { do: {
      press: [{ ...keep, off_action: go, hold_action: { ...keep, off_action: go } }, go],
      change: [{ action: "value_map", map: { a: go, b: { ...keep, hold_action: go } } }],
      anything: go,
      route: [go, keep],
    } };
    const matrix_config = { sources: [{ value: 1 }], destinations: [{ value: 1, route: [go, { ...keep, off_action: go }] }] };
    const controls = [el("b", { bindings }), el("mx", { type: "matrix", bindings, matrix_config })];
    const masters = controls.map((c) => ({ ...c, id: `m_${c.id}`, pages: "*" }) as unknown as MasterElement);
    const before = [...controls, ...masters].flatMap((c) => elementActions(c as UIElement)).filter(([, a]) => a.page === "gone");
    expect(before.length).toBeGreaterThan(10);
    const r = removePageAndScrubRefs([page("p1", controls), page("gone", [])], "gone", masters, []);
    const left = [...r.pages[0].elements, ...r.masterElements]
      .flatMap((c) => elementActions(c as UIElement))
      .filter(([, a]) => a.action === "ui.navigate" && a.page === "gone");
    expect(left).toEqual([]);
  });
});
