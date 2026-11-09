import { describe, it, expect } from "vitest";
import {
  actionDanglingRef,
  actionIncompleteCheck,
  actionsCommandDevice,
  elementCommandActions,
  projectCommandActions,
  slotActions,
  type BindingRefIds,
} from "./uiBuilderHelpers";
import type { ProjectConfig, UIElement } from "../../api/types";

// The binding card's Broken and Incomplete badges, the "does this control reach
// its device" check behind its Add-a-command button, and the walk that collects
// every command for the platform to judge. These pin what each says today,
// including where they read a value_map more loosely than Validate does (an
// option written as a list): that difference is the code as it stands, kept.

const IDS: BindingRefIds = {
  deviceIds: new Set(["amp"]),
  macroIds: new Set(["all_on"]),
  pageIds: new Set(["main"]),
};

describe("slotActions", () => {
  it("reads a list, dropping what is not an action", () => {
    const a = { action: "macro" };
    expect(slotActions({ press: [a, null, 3, "x", { action: "b" }] }, "press")).toEqual([a, { action: "b" }]);
  });

  it("reads one action written on its own, and nothing from an empty one", () => {
    expect(slotActions({ press: { action: "macro" } }, "press")).toEqual([{ action: "macro" }]);
    expect(slotActions({ press: {} }, "press")).toEqual([]);
  });

  it("reads nothing from a missing slot, a missing map, or a value that is not an object", () => {
    expect(slotActions({}, "press")).toEqual([]);
    expect(slotActions(undefined, "press")).toEqual([]);
    expect(slotActions({ press: "macro" }, "press")).toEqual([]);
  });
});

describe("actionDanglingRef: the Broken badge", () => {
  it("names a missing device, macro or page in the badge's words", () => {
    expect(actionDanglingRef({ action: "device.command", device: "ghost" }, IDS)).toBe('Device "ghost" not found');
    expect(actionDanglingRef({ action: "macro", macro: "ghost" }, IDS)).toBe('Macro "ghost" not found');
    expect(actionDanglingRef({ action: "ui.navigate", page: "ghost" }, IDS)).toBe('Page "ghost" not found');
  });

  it("takes what exists, the panel's own targets, and a field not filled in", () => {
    for (const a of [
      { action: "device.command", device: "amp" },
      { action: "macro", macro: "all_on" },
      { action: "ui.navigate", page: "main" },
      { action: "ui.navigate", page: "$back" },
      { action: "ui.navigate", page: "$dismiss" },
      { action: "device.command" },
      { action: "macro", macro: "" },
      { action: "ui.navigate" },
    ]) {
      expect(actionDanglingRef(a, IDS)).toBeNull();
    }
  });

  it("reads the field each action names and no other", () => {
    expect(actionDanglingRef({ action: "macro", macro: "all_on", device: "ghost", page: "ghost" }, IDS)).toBeNull();
    expect(actionDanglingRef({ action: "state.set", key: "x", device: "ghost", macro: "ghost", page: "ghost" }, IDS)).toBeNull();
  });

  it("finds the first broken option of a value_map, nested maps included", () => {
    const map = {
      action: "value_map",
      map: {
        a: { action: "macro", macro: "all_on" },
        b: { action: "value_map", map: { x: { action: "device.command", device: "ghost_deep" } } },
        c: { action: "macro", macro: "ghost_later" },
      },
    };
    expect(actionDanglingRef(map, IDS)).toBe('Device "ghost_deep" not found');
  });

  it("looks inside an option written as a list, and a map written as a list", () => {
    expect(actionDanglingRef(
      { action: "value_map", map: { a: [{ action: "macro", macro: "all_on" }, null, { action: "macro", macro: "ghost" }] } },
      IDS,
    )).toBe('Macro "ghost" not found');
    expect(actionDanglingRef({ action: "value_map", map: [{ action: "ui.navigate", page: "ghost" }] }, IDS))
      .toBe('Page "ghost" not found');
  });

  it("has nothing to say about a map that is not an object", () => {
    expect(actionDanglingRef({ action: "value_map", map: "macro" }, IDS)).toBeNull();
    expect(actionDanglingRef({ action: "value_map" }, IDS)).toBeNull();
  });
});

describe("actionIncompleteCheck: the Incomplete badge", () => {
  it("wants the field each action cannot run without", () => {
    const cases: [Record<string, unknown>, boolean][] = [
      [{ action: "device.command", device: "amp", command: "on" }, false],
      [{ action: "device.command", device: "amp" }, true],
      [{ action: "device.command", command: "on" }, true],
      [{ action: "macro", macro: "m" }, false],
      [{ action: "macro" }, true],
      [{ action: "state.set", key: "var.x" }, false],
      [{ action: "state.set", value: 1 }, true],
      [{ action: "ui.navigate", page: "main" }, false],
      [{ action: "ui.navigate" }, true],
      [{ action: "script.call", function: "f" }, false],
      [{ action: "script.call" }, true],
      [{ action: "event.emit", event: "custom.x" }, false],
      [{ action: "event.emit" }, true],
      [{ action: "something.else" }, false],
      [{}, true],
      [{ action: "" }, true],
    ];
    for (const [a, incomplete] of cases) {
      expect([a, actionIncompleteCheck(a)]).toEqual([a, incomplete]);
    }
  });

  it("calls an empty or missing value_map incomplete", () => {
    expect(actionIncompleteCheck({ action: "value_map" })).toBe(true);
    expect(actionIncompleteCheck({ action: "value_map", map: {} })).toBe(true);
    expect(actionIncompleteCheck({ action: "value_map", map: 5 })).toBe(true);
  });

  it("looks into each option, a list, a nested map, and skips what is not an action", () => {
    const done = { action: "macro", macro: "m" };
    expect(actionIncompleteCheck({ action: "value_map", map: { a: done, b: [done, null, 7] } })).toBe(false);
    expect(actionIncompleteCheck({ action: "value_map", map: { a: done, b: [done, { action: "macro" }] } })).toBe(true);
    expect(actionIncompleteCheck({ action: "value_map", map: { a: { action: "value_map", map: {} } } })).toBe(true);
    // Text where the map should be: it has keys, and none of them is an action.
    expect(actionIncompleteCheck({ action: "value_map", map: "ab" })).toBe(false);
  });
});

describe("actionsCommandDevice: does the control reach its device", () => {
  const cmd = { action: "device.command", device: "amp", command: "on" };

  it("sees a command in the list, or as an option of a value_map", () => {
    expect(actionsCommandDevice([{ action: "macro" }, cmd])).toBe(true);
    expect(actionsCommandDevice([{ action: "value_map", map: { a: cmd } }])).toBe(true);
    expect(actionsCommandDevice([{ action: "value_map", map: { a: [{ action: "macro" }, cmd] } }])).toBe(true);
    expect(actionsCommandDevice([{ action: "value_map", map: [cmd] }])).toBe(true);
  });

  it("counts a command with nothing filled in", () => {
    expect(actionsCommandDevice([{ action: "device.command" }])).toBe(true);
  });

  it("looks one value_map deep, and no deeper", () => {
    expect(actionsCommandDevice([{ action: "value_map", map: { a: { action: "value_map", map: { b: cmd } } } }])).toBe(false);
  });

  it("sees nothing in an empty list, other actions, or a map that is not an object", () => {
    expect(actionsCommandDevice([])).toBe(false);
    expect(actionsCommandDevice([{ action: "macro", macro: "m" }])).toBe(false);
    expect(actionsCommandDevice([{ action: "value_map", map: "device.command" }])).toBe(false);
    expect(actionsCommandDevice([{ action: "value_map", map: { a: null, b: "device.command" } }])).toBe(false);
  });
});

describe("elementCommandActions and projectCommandActions: what the platform is asked about", () => {
  const cmd = (name: string) => ({ action: "device.command", device: "amp", command: name });

  it("walks the slots in their fixed order, each action before its options, lists and nesting included", () => {
    const a = cmd("a"), b = cmd("b"), c = cmd("c"), d = cmd("d"), e = cmd("e");
    const el = {
      id: "s1", type: "select",
      bindings: {
        do: {
          change: [{ action: "value_map", map: { x: [b, { action: "value_map", map: { y: c } }], z: d } }],
          press: [a],
          route: e,
        },
      },
    } as unknown as UIElement;
    const found = elementCommandActions(el);
    expect(found).toEqual([a, b, c, d, e]);
    found.forEach((f, i) => expect(f).toBe([a, b, c, d, e][i]));
  });

  it("skips an option that is not an action, and a control with no bindings", () => {
    const a = cmd("a");
    const el = { id: "s1", type: "select", bindings: { do: { change: [{ action: "value_map", map: { x: null, y: "a", z: a } }] } } } as unknown as UIElement;
    expect(elementCommandActions(el)).toEqual([a]);
    expect(elementCommandActions({ id: "b", type: "button" } as unknown as UIElement)).toEqual([]);
    expect(projectCommandActions({ ui: { pages: [{ id: "p" }] }, macros: [] } as unknown as ProjectConfig)).toEqual([]);
  });

  it("collects a macro's group command and a command with no device, and not a step with no command", () => {
    const group = { action: "group.command", group: "rooms", command: "on" };
    const deviceless = { action: "device.command", command: "on" };
    const p = {
      ui: { pages: [], master_elements: [] },
      macros: [{ id: "m", name: "M", steps: [group, deviceless, { action: "group.command", group: "rooms" }, { action: "macro", macro: "x", command: "on" }] }],
    } as unknown as ProjectConfig;
    expect(projectCommandActions(p)).toEqual([group, deviceless]);
  });

  it("takes pages, then masters, then each macro's steps with a branch's steps after the step that holds them", () => {
    const onPage = cmd("page"), onMaster = cmd("master");
    const s1 = cmd("s1"), t1 = cmd("t1"), t2 = cmd("t2"), e1 = cmd("e1"), s2 = cmd("s2");
    const p = {
      ui: {
        pages: [{ id: "main", elements: [{ id: "b", type: "button", bindings: { do: { press: [onPage] } } }] }],
        master_elements: [{ id: "mb", type: "button", bindings: { do: { press: [onMaster] } } }],
      },
      macros: [
        { id: "m1", name: "M1", steps: [s1, { ...cmd("cond"), then_steps: [t1, { action: "conditional", then_steps: [t2] }], else_steps: [e1] }] },
        { id: "m2", name: "M2", steps: [s2] },
      ],
    } as unknown as ProjectConfig;
    expect(projectCommandActions(p).map((a) => a.command)).toEqual(["page", "master", "s1", "cond", "t1", "t2", "e1", "s2"]);
  });

  it("asks nothing of a project with no pages, masters or macros", () => {
    expect(projectCommandActions({} as ProjectConfig)).toEqual([]);
    expect(projectCommandActions({ ui: {}, macros: [{ id: "m", name: "M" }] } as unknown as ProjectConfig)).toEqual([]);
  });
});
