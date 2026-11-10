import { describe, it, expect } from "vitest";
import { validateProject } from "./validateProject";
import type { ProjectConfig } from "../../api/types";

// Validate is the list an integrator reads before handing a room over. These
// pin what it says today, check by check: the words, where each one points, the
// severity, and the order the list comes back in. A finding that moves, changes
// its wording or drops out is a change somebody reading the list will see.

type El = Record<string, unknown>;

/** Spaced out and given something to draw, so the page review stays quiet and
 *  only the reference checks speak. */
function placed(elements: El[]): Record<string, { x: number; y: number; w: number; h: number }> {
  return Object.fromEntries(
    elements.map((e, i) => [e.id, { x: (i % 4) * 25, y: Math.floor(i / 4) * 15, w: 20, h: 12.5 }]),
  );
}

interface Fixture {
  elements?: El[];
  pages?: Record<string, unknown>[];
  masters?: El[];
  macros?: Record<string, unknown>[];
  idle?: string;
}

function project({ elements = [], pages, masters = [], macros = [], idle }: Fixture): ProjectConfig {
  return {
    project: { id: "p", name: "P" },
    devices: [{ id: "amp", name: "Amp", driver: "acme_amp" }],
    macros: [{ id: "all_on", name: "All On", steps: [] }, ...macros],
    ui: {
      settings: idle === undefined ? {} : { idle_page: idle },
      master_elements: masters,
      pages: pages ?? [
        {
          id: "main",
          name: "Main",
          elements,
          layouts: [
            { id: "landscape", orientation: "landscape", primary: true, placements: placed(elements), hidden: [] },
          ],
        },
        { id: "other", name: "Other", elements: [], layouts: [] },
      ],
    },
  } as unknown as ProjectConfig;
}

/** What Validate says apart from the page review, which has its own pins and
 *  whose sentences open with the element's id and type. */
function checked(p: ProjectConfig, answers?: Map<Record<string, unknown>, string[]>) {
  return validateProject(p, answers).filter(
    (i) => !(i.elementId && i.message.startsWith(`${i.elementId} (`)),
  );
}

const at = (id: string, where = "") => ({
  location: `Main > ${id}${where}`,
  pageId: "main",
  elementId: id,
});

describe("validateProject: a page nav's target", () => {
  it("names a target page that is not in the project", () => {
    const nav = { id: "nav1", type: "page_nav", label: "Go", target_page: "ghost", bindings: {} };
    expect(checked(project({ elements: [nav] }))).toEqual([
      { severity: "error", message: 'Target page "ghost" does not exist', ...at("nav1") },
    ]);
  });

  it("takes a page that exists, the panel's own targets, and no target at all", () => {
    const elements = [
      { id: "nav1", type: "page_nav", label: "Go", target_page: "other", bindings: {} },
      { id: "nav2", type: "page_nav", label: "Back", target_page: "$back", bindings: {} },
      { id: "nav3", type: "page_nav", label: "Close", target_page: "$dismiss", bindings: {} },
      { id: "nav4", type: "page_nav", label: "None", target_page: "", bindings: {} },
    ];
    expect(checked(project({ elements }))).toEqual([]);
  });

  it("is asked only of a page nav", () => {
    const button = {
      id: "b1", type: "button", label: "B", target_page: "ghost",
      bindings: { do: { press: [{ action: "macro", macro: "all_on" }] } },
    };
    expect(checked(project({ elements: [button] }))).toEqual([]);
  });
});

describe("validateProject: a control's grant", () => {
  it("names each granted device that is gone, in the order granted", () => {
    const custom = { id: "c1", type: "custom", grant: { devices: ["amp", "ghost", "gone"] }, bindings: {} };
    expect(checked(project({ elements: [custom] }))).toEqual([
      { severity: "error", message: 'Granted device "ghost" not found, so this control cannot read or control it', ...at("c1") },
      { severity: "error", message: 'Granted device "gone" not found, so this control cannot read or control it', ...at("c1") },
    ]);
  });
});

describe("validateProject: what a control's actions name", () => {
  const button = (doMap: Record<string, unknown>) => ({ id: "b1", type: "button", label: "B", bindings: { do: doMap } });

  it("names a missing page, device and macro, each at its slot", () => {
    const el = button({
      press: [
        { action: "ui.navigate", page: "ghost" },
        { action: "device.command", device: "ghost_amp", command: "power_on" },
        { action: "macro", macro: "ghost_macro" },
      ],
    });
    expect(checked(project({ elements: [el] }))).toEqual([
      { severity: "error", message: 'Navigate to deleted page "ghost"', ...at("b1", " > press") },
      { severity: "error", message: 'Device "ghost_amp" not found', ...at("b1", " > press") },
      { severity: "error", message: 'Macro "ghost_macro" not found', ...at("b1", " > press") },
    ]);
  });

  it("takes the panel's own targets and everything that exists", () => {
    const el = button({
      press: [
        { action: "ui.navigate", page: "$back" },
        { action: "ui.navigate", page: "$dismiss" },
        { action: "ui.navigate", page: "other" },
        { action: "device.command", device: "amp", command: "power_on" },
        { action: "macro", macro: "all_on" },
        { action: "ui.navigate" },
        { action: "macro" },
        { action: "device.command" },
      ],
    });
    expect(checked(project({ elements: [el] }))).toEqual([]);
  });

  it("reads the field each action names and no other", () => {
    // A macro action's page and a navigate's device mean nothing to the runtime.
    const el = button({
      press: [
        { action: "macro", macro: "all_on", page: "ghost", device: "ghost" },
        { action: "ui.navigate", page: "other", macro: "ghost", device: "ghost" },
        { action: "state.set", key: "var.x", page: "ghost", macro: "ghost", device: "ghost" },
      ],
    });
    expect(checked(project({ elements: [el] }))).toEqual([]);
  });

  it("walks the slots in one fixed order, whatever order they were written in", () => {
    const el = {
      id: "m1", type: "matrix",
      bindings: {
        do: {
          audio_mute_route: [{ action: "macro", macro: "m_amr" }],
          route: [{ action: "macro", macro: "m_route" }],
          change: [{ action: "macro", macro: "m_change" }],
          press: [{ action: "macro", macro: "m_press" }],
          select: { action: "macro", macro: "m_select" },
          hold: [{ action: "macro", macro: "m_hold" }],
          release: [{ action: "macro", macro: "m_release" }],
          submit: [{ action: "macro", macro: "m_submit" }],
          mute_route: [{ action: "macro", macro: "m_mr" }],
          audio_route: [{ action: "macro", macro: "m_ar" }],
          not_a_slot: [{ action: "macro", macro: "m_never" }],
        },
      },
    };
    const said = checked(project({ elements: [el] }))
      .filter((i) => i.elementId === "m1" && i.severity === "error")
      .map((i) => [i.location, i.message]);
    expect(said).toEqual([
      ["Main > m1 > press", 'Macro "m_press" not found'],
      ["Main > m1 > release", 'Macro "m_release" not found'],
      ["Main > m1 > hold", 'Macro "m_hold" not found'],
      ["Main > m1 > change", 'Macro "m_change" not found'],
      ["Main > m1 > submit", 'Macro "m_submit" not found'],
      ["Main > m1 > select", 'Macro "m_select" not found'],
      ["Main > m1 > route", 'Macro "m_route" not found'],
      ["Main > m1 > audio_route", 'Macro "m_ar" not found'],
      ["Main > m1 > mute_route", 'Macro "m_mr" not found'],
      ["Main > m1 > audio_mute_route", 'Macro "m_amr" not found'],
    ]);
  });

  it("follows a value_map into each option, nested ones too, and says which option", () => {
    const el = {
      id: "sel1", type: "select",
      bindings: {
        do: {
          change: [{
            action: "value_map",
            map: {
              hdmi: { action: "device.command", device: "ghost_amp", command: "input" },
              more: { action: "value_map", map: { deep: { action: "macro", macro: "ghost_macro" } } },
              fine: { action: "macro", macro: "all_on" },
            },
          }],
        },
      },
    };
    expect(checked(project({ elements: [el] }))).toEqual([
      { severity: "error", message: 'Device "ghost_amp" not found', ...at("sel1", ' > change > "hdmi"') },
      { severity: "error", message: 'Macro "ghost_macro" not found', ...at("sel1", ' > change > "more" > "deep"') },
    ]);
  });

  it("does not look inside an option written as a list, or a map written as a list", () => {
    // The runtime runs one action per option. Validate reads only that shape
    // (the badge on the binding card reads more; see the badge's own pins).
    const el = {
      id: "sel1", type: "select",
      bindings: {
        do: {
          change: [
            { action: "value_map", map: { a: [{ action: "macro", macro: "ghost_in_list" }] } },
            { action: "value_map", map: [{ action: "macro", macro: "ghost_in_list_map" }] },
            { action: "value_map", map: "not a map" },
          ],
        },
      },
    };
    expect(checked(project({ elements: [el] }))).toEqual([]);
  });

  it("places what the platform said about a command after the command's own reference", () => {
    const send = { action: "device.command", device: "ghost_amp", command: "set_level", params: {} };
    const inMap = { action: "device.command", device: "amp", command: "set_input", params: {} };
    const el = {
      id: "f1", type: "fader",
      bindings: { do: { change: [send, { action: "value_map", map: { a: inMap } }] } },
    };
    const answers = new Map<Record<string, unknown>, string[]>([
      [send, ["first refusal", "second refusal"]],
      [inMap, ["refused in the map"]],
    ]);
    expect(checked(project({ elements: [el] }), answers)).toEqual([
      { severity: "error", message: 'Device "ghost_amp" not found', ...at("f1", " > change") },
      { severity: "error", message: "first refusal", ...at("f1", " > change") },
      { severity: "error", message: "second refusal", ...at("f1", " > change") },
      { severity: "error", message: "refused in the map", ...at("f1", ' > change > "a"') },
    ]);
  });

  it("asks the platform's answers of any action, not only a command", () => {
    // Keyed by the object, so whatever was asked about is reported.
    const macroAction = { action: "macro", macro: "all_on" };
    const el = button({ press: [macroAction] });
    const answers = new Map<Record<string, unknown>, string[]>([[macroAction, ["said about a macro"]]]);
    expect(checked(project({ elements: [el] }), answers)).toEqual([
      { severity: "error", message: "said about a macro", ...at("b1", " > press") },
    ]);
  });
});

describe("validateProject: the state a control shows", () => {
  it("names an unknown device in the value and in the appearance", () => {
    const el = {
      id: "l1", type: "label", text: "T",
      bindings: {
        show: {
          value: { source: "state", key: "device.ghost.volume" },
          look: { source: "state", key: "device.gone.power" },
        },
      },
    };
    expect(checked(project({ elements: [el] }))).toEqual([
      { severity: "warning", message: 'State key references unknown device "ghost"', ...at("l1", " > value") },
      { severity: "warning", message: 'State key references unknown device "gone"', ...at("l1", " > appearance") },
    ]);
  });

  it("takes a known device and a key that is not a device's", () => {
    const el = {
      id: "l1", type: "label", text: "T",
      bindings: {
        show: {
          value: { source: "state", key: "var.ghost" },
          look: { source: "state", key: "device.amp.power" },
        },
      },
    };
    expect(checked(project({ elements: [el] }))).toEqual([]);
  });

  it("names an unknown device in a visibility condition, one, all or any", () => {
    const one = { id: "l1", type: "label", text: "T", bindings: { show: { visible_when: { key: "device.ghost.power", equals: true } } } };
    const all = {
      id: "l2", type: "label", text: "T",
      bindings: { show: { visible_when: { all: [{ key: "device.g1.x" }, { key: "device.amp.x" }, { key: "device.g2.x" }] } } },
    };
    const any = { id: "l3", type: "label", text: "T", bindings: { show: { visible_when: { any: [{ key: "device.g3.x" }] } } } };
    const both = {
      id: "l4", type: "label", text: "T",
      bindings: { show: { visible_when: { all: [{ key: "device.from_all.x" }], any: [{ key: "device.from_any.x" }] } } },
    };
    const said = checked(project({ elements: [one, all, any, both] }));
    const visibility = (id: string, device: string) => ({
      severity: "warning",
      message: `Visibility condition references unknown device "${device}"`,
      ...at(id, " > visible_when"),
    });
    expect(said).toEqual([
      visibility("l1", "ghost"),
      visibility("l2", "g1"),
      visibility("l2", "g2"),
      visibility("l3", "g3"),
      visibility("l4", "from_all"),
    ]);
  });
});

describe("validateProject: a control that shows a device value", () => {
  const slider = (doMap: Record<string, unknown>, writeBack = false) => ({
    id: "s1", type: "slider",
    bindings: { show: { value: { source: "state", key: "device.amp.level", ...(writeBack ? { write_back: true } : {}) } }, do: doMap },
  });
  const noCommand = {
    severity: "warning",
    message: "This control shows a device value but has no command to change it. Add a command so touching it reaches the device",
    ...at("s1", " > value"),
  };

  it("says it has no command to change it", () => {
    expect(checked(project({ elements: [slider({ change: [{ action: "macro", macro: "all_on" }] })] }))).toEqual([noCommand]);
  });

  it("is satisfied by a command in its own interaction, or one inside a value_map there", () => {
    const direct = slider({ change: [{ action: "device.command", device: "amp", command: "set_level" }] });
    expect(checked(project({ elements: [direct] }))).toEqual([]);
    const mapped = slider({ change: [{ action: "value_map", map: { a: { action: "device.command", device: "amp", command: "x" } } }] });
    expect(checked(project({ elements: [mapped] }))).toEqual([]);
  });

  it("is not satisfied by a command in an interaction the control does not have", () => {
    const elsewhere = slider({ press: [{ action: "device.command", device: "amp", command: "set_level" }] });
    expect(checked(project({ elements: [elsewhere] }))).toEqual([noCommand]);
  });

  it("is asked only when the value can be driven back out, and only of a device's key", () => {
    const gauge = { id: "g1", type: "gauge", bindings: { show: { value: { source: "state", key: "device.amp.level" } } } };
    const onVariable = {
      id: "s2", type: "slider",
      bindings: { show: { value: { source: "state", key: "var.level" } }, do: { change: [{ action: "macro", macro: "all_on" }] } },
    };
    expect(checked(project({ elements: [gauge, onVariable] }))).toEqual([]);
  });

  it("comes before the visibility check and the no-action check", () => {
    const el = {
      id: "s1", type: "slider",
      bindings: { show: { value: { source: "state", key: "device.amp.level" }, visible_when: { key: "device.ghost.on" } } },
    };
    expect(checked(project({ elements: [el] })).map((i) => i.message)).toEqual([
      noCommand.message,
      'Visibility condition references unknown device "ghost"',
      "Interactive element has no action",
    ]);
  });
});

describe("validateProject: an interactive control that does nothing", () => {
  const noAction = (id: string) => ({ severity: "warning", message: "Interactive element has no action", ...at(id) });

  it("is said of every interactive type with no action and no two-way value", () => {
    const types = ["button", "slider", "fader", "select", "text_input", "keypad", "matrix", "list"];
    const elements = types.map((type, i) => ({ id: `e${i}`, type, label: "x", bindings: {} }));
    const said = checked(project({ elements })).filter((i) => i.message === "Interactive element has no action");
    expect(said).toEqual(types.map((_, i) => noAction(`e${i}`)));
  });

  it("is satisfied by any action in any slot, or a value written back", () => {
    const elements = [
      { id: "b1", type: "button", label: "x", bindings: { do: { audio_mute_route: [{ action: "macro", macro: "all_on" }] } } },
      { id: "b2", type: "button", label: "x", bindings: { do: { hold: { action: "macro", macro: "all_on" } } } },
      { id: "s1", type: "select", bindings: { show: { value: { source: "state", key: "var.v", write_back: true } } } },
    ];
    expect(checked(project({ elements }))).toEqual([]);
  });

  it("is not satisfied by an empty slot, an empty object, or an action in a slot nothing runs", () => {
    const elements = [
      { id: "b1", type: "button", label: "x", bindings: { do: { press: [] } } },
      { id: "b2", type: "button", label: "x", bindings: { do: { press: {} } } },
      { id: "b3", type: "button", label: "x", bindings: { do: { not_a_slot: [{ action: "macro", macro: "all_on" }] } } },
    ];
    expect(checked(project({ elements }))).toEqual([noAction("b1"), noAction("b2"), noAction("b3")]);
  });

  it("is not asked of a display", () => {
    const elements = [
      { id: "l1", type: "label", text: "x", bindings: {} },
      { id: "g1", type: "gauge", bindings: {} },
    ];
    expect(checked(project({ elements }))).toEqual([]);
  });
});

describe("validateProject: one control's findings, in order", () => {
  it("says the target, the grant, the actions, the state, the visibility, then the missing container", () => {
    const nav = {
      id: "nav1", type: "page_nav", label: "Go", target_page: "ghost_page", parent: "ghost_parent",
      grant: { devices: ["ghost_grant"] },
      bindings: {
        do: { press: [{ action: "macro", macro: "ghost_macro" }] },
        show: { look: { source: "state", key: "device.ghost_look.x" }, visible_when: { key: "device.ghost_vis.x" } },
      },
    };
    expect(checked(project({ elements: [nav] })).map((i) => [i.severity, i.message, i.location])).toEqual([
      ["error", 'Target page "ghost_page" does not exist', "Main > nav1"],
      ["error", 'Granted device "ghost_grant" not found, so this control cannot read or control it', "Main > nav1"],
      ["error", 'Macro "ghost_macro" not found', "Main > nav1 > press"],
      ["warning", 'State key references unknown device "ghost_look"', "Main > nav1 > appearance"],
      ["warning", 'Visibility condition references unknown device "ghost_vis"', "Main > nav1 > visible_when"],
      ["error", 'Container "ghost_parent" does not exist', "Main > nav1"],
    ]);
  });
});

describe("validateProject: a page and its arrangements", () => {
  it("names the page's own grant, then each arrangement's problems, then its controls, then the review", () => {
    const elements = [
      { id: "b1", type: "button", label: "B", bindings: { do: { press: [{ action: "macro", macro: "ghost" }] } } },
      // Nothing to draw: the review speaks for this one.
      { id: "l1", type: "label", bindings: {} },
    ];
    const pages = [{
      id: "main", name: "Main", grant: { devices: ["amp", "ghost_dev"] }, elements,
      layouts: [
        { id: "landscape", orientation: "landscape", primary: true, placements: { ...placed(elements), ghost_el: { x: 0, y: 0, w: 1, h: 1 } }, hidden: ["gone_el"] },
        { id: "portrait", orientation: "portrait", inherits: "nowhere", placements: {}, hidden: [] },
        { id: "tall", orientation: "portrait", inherits: "landscape", hidden: ["b1"] },
      ],
    }];
    expect(validateProject(project({ pages }))).toEqual([
      { severity: "error", message: 'Granted device "ghost_dev" not found, so the page shown here cannot read or control it', location: "Main > page", pageId: "main" },
      { severity: "warning", message: 'Arrangement "landscape" positions "ghost_el", which is not an element on this page', location: "Main > landscape", pageId: "main" },
      { severity: "warning", message: 'Arrangement "landscape" hides "gone_el", which is not an element on this page', location: "Main > landscape", pageId: "main" },
      { severity: "error", message: 'Arrangement inherits from "nowhere", which does not exist — controls without their own position here fall back to default boxes', location: "Main > portrait", pageId: "main" },
      { severity: "error", message: 'Macro "ghost" not found', ...at("b1", " > press") },
      {
        severity: "warning",
        message: "l1 (label) has no text, and no show.value to supply one, so it draws an empty box. Set text to the string to draw, or a show.value binding.",
        ...at("l1"),
      },
    ]);
  });

  it("checks every page, in the project's order", () => {
    const pages = ["first", "second"].map((id) => ({
      id, name: id.toUpperCase(),
      elements: [{ id: `b_${id}`, type: "button", label: "B", bindings: { do: { press: [{ action: "macro", macro: "ghost" }] } } }],
      layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: { [`b_${id}`]: { x: 0, y: 0, w: 20, h: 12.5 } }, hidden: [] }],
    }));
    expect(checked(project({ pages })).map((i) => i.location)).toEqual([
      "FIRST > b_first > press",
      "SECOND > b_second > press",
    ]);
  });
});

describe("validateProject: master elements", () => {
  it("runs the control checks with no page, then the master's own review, then its pages", () => {
    const master = {
      id: "mb", type: "button", label: "M", pages: ["main", "ghost_page", "gone_page"],
      placement: { x: 0, y: 85, w: 20, h: 12.5 },
      bindings: { do: { press: [{ action: "macro", macro: "ghost" }] } },
    };
    expect(checked(project({ masters: [master] }))).toEqual([
      { severity: "error", message: 'Macro "ghost" not found', location: "Master Elements > mb > press", pageId: "", elementId: "mb" },
      { severity: "error", message: 'References deleted page "ghost_page"', location: "Master Elements > mb" },
      { severity: "error", message: 'References deleted page "gone_page"', location: "Master Elements > mb" },
    ]);
  });

  it("takes a master shown on every page", () => {
    const master = {
      id: "mb", type: "button", label: "M", pages: "*",
      placement: { x: 0, y: 85, w: 20, h: 12.5 },
      bindings: { do: { press: [{ action: "macro", macro: "all_on" }] } },
    };
    expect(checked(project({ masters: [master] }))).toEqual([]);
  });

  it("reports the master's review with no page", () => {
    const master = { id: "ml", type: "label", pages: "*", placement: { x: 0, y: 85, w: 20, h: 12.5 }, bindings: {} };
    expect(validateProject(project({ masters: [master] }))).toEqual([
      {
        severity: "warning",
        message: "ml (label) has no text, and no show.value to supply one, so it draws an empty box. Set text to the string to draw, or a show.value binding.",
        location: "Master Elements > ml",
        elementId: "ml",
      },
    ]);
  });
});

describe("validateProject: the page review sees the master elements", () => {
  it("reports a page control laid over a master, on the page", () => {
    const master = {
      id: "mb", type: "button", label: "M", pages: "*",
      placements: { landscape: { x: 0, y: 0, w: 20, h: 12.5 } },
      bindings: { do: { press: [{ action: "macro", macro: "all_on" }] } },
    };
    const button = { id: "b1", type: "button", label: "B", bindings: { do: { press: [{ action: "macro", macro: "all_on" }] } } };
    const over = validateProject(project({ elements: [button], masters: [master] }))
      .filter((i) => i.pageId === "main" && i.elementId === "b1");
    const alone = validateProject(project({ elements: [button] }))
      .filter((i) => i.pageId === "main" && i.elementId === "b1");
    expect(alone).toEqual([]);
    expect(over).toHaveLength(1);
    expect(over[0]).toMatchObject({ severity: "warning", location: "Main > b1", pageId: "main", elementId: "b1" });
    expect(over[0].message).toContain("mb");
  });
});

describe("validateProject: macro steps", () => {
  it("names a missing device or macro at each step, nested branches included, in order", () => {
    const macros = [{
      id: "startup", name: "Startup",
      steps: [
        { action: "device.command", device: "ghost_dev", command: "on", description: "Projector on" },
        { action: "device.command", device: "ghost_dev2", command: "on" },
        {
          action: "conditional",
          then_steps: [
            { action: "macro", macro: "ghost_macro", description: "Run the rest" },
            { action: "conditional", else_steps: [{ action: "macro", macro: "deep_ghost" }] },
          ],
          else_steps: [{ action: "device.command", device: "else_ghost", command: "off" }],
        },
        { action: "macro", macro: "all_on" },
        { action: "device.command", device: "amp", command: "on" },
        { action: "group.command", group: "ghost_group", command: "on" },
      ],
    }];
    expect(checked(project({ macros }))).toEqual([
      { severity: "error", message: 'Device "ghost_dev" not found', location: 'Macro "Startup" > Projector on' },
      { severity: "error", message: 'Device "ghost_dev2" not found', location: 'Macro "Startup" > device.command' },
      { severity: "error", message: 'Macro "ghost_macro" not found', location: 'Macro "Startup" > Run the rest' },
      { severity: "error", message: 'Macro "deep_ghost" not found', location: 'Macro "Startup" > call macro' },
      { severity: "error", message: 'Device "else_ghost" not found', location: 'Macro "Startup" > device.command' },
    ]);
  });

  it("reports what the platform said about any step, after its own reference", () => {
    const step = { action: "device.command", device: "ghost_dev", command: "on" };
    const group = { action: "group.command", group: "rooms", command: "on", description: "All rooms" };
    const nested = { action: "macro", macro: "all_on" };
    const macros = [{ id: "m", name: "M", steps: [step, group, { action: "conditional", then_steps: [nested] }] }];
    const answers = new Map<Record<string, unknown>, string[]>([
      [step, ["step refused"]],
      [group, ["group refused"]],
      [nested, ["nested said"]],
    ]);
    expect(checked(project({ macros }), answers)).toEqual([
      { severity: "error", message: 'Device "ghost_dev" not found', location: 'Macro "M" > device.command' },
      { severity: "error", message: "step refused", location: 'Macro "M" > device.command' },
      { severity: "error", message: "group refused", location: 'Macro "M" > All rooms' },
      { severity: "error", message: "nested said", location: 'Macro "M" > macro' },
    ]);
  });

  it("says nothing about a step's page, the panel's own targets included", () => {
    const macros = [{
      id: "m", name: "M",
      steps: [
        { action: "ui.navigate", page: "ghost_page" },
        { action: "ui.navigate", page: "$back", description: "Back" },
      ],
    }];
    expect(checked(project({ macros }))).toEqual([]);
  });

  it("reads only the field the step's action names", () => {
    const macros = [{
      id: "m", name: "M",
      steps: [
        { action: "state.set", key: "var.x", value: 1, device: "ghost_dev", macro: "ghost_macro" },
        { action: "device.command", device: "amp", command: "on", macro: "ghost_macro" },
        { action: "macro", macro: "all_on", device: "ghost_dev" },
        { action: "delay", seconds: 1, device: "ghost_dev" },
      ],
    }];
    expect(checked(project({ macros }))).toEqual([]);
  });

  it("says nothing about a step whose id is empty or missing", () => {
    const macros = [{
      id: "m", name: "M",
      steps: [
        { action: "device.command", device: "", command: "on" },
        { action: "device.command", command: "on" },
        { action: "macro", macro: "" },
        { action: "macro" },
      ],
    }];
    expect(checked(project({ macros }))).toEqual([]);
  });

  it("writes an id that is not text as the text it reads as", () => {
    const macros = [{
      id: "m", name: "M",
      steps: [
        { action: "device.command", device: 5, command: "on" },
        { action: "macro", macro: 7, description: "Seven" },
      ],
    }];
    expect(checked(project({ macros }))).toEqual([
      { severity: "error", message: 'Device "5" not found', location: 'Macro "M" > device.command' },
      { severity: "error", message: 'Macro "7" not found', location: 'Macro "M" > Seven' },
    ]);
  });
});

describe("validateProject: what is left out of a project", () => {
  it("takes a control with no bindings, a page with no arrangements, an arrangement with no hidden list, and no masters", () => {
    const p = {
      project: { id: "p", name: "P" },
      devices: [],
      macros: [],
      ui: {
        settings: {},
        pages: [
          { id: "main", name: "Main", elements: [{ id: "l1", type: "label", text: "x" }] },
          { id: "two", name: "Two", elements: [], layouts: [{ id: "landscape", orientation: "landscape", primary: true, placements: {} }] },
        ],
      },
    } as unknown as ProjectConfig;
    expect(checked(p)).toEqual([]);
  });

  it("checks only a device's key in a visibility condition", () => {
    const el = { id: "l1", type: "label", text: "T", bindings: { show: { visible_when: { all: [{ key: "var.ghost" }, { value: 1 }] } } } };
    expect(checked(project({ elements: [el] }))).toEqual([]);
  });
});

describe("validateProject: the idle page, and the order of the whole list", () => {
  it("names an idle page that is not in the project, last", () => {
    const button = { id: "b1", type: "button", label: "B", bindings: { do: { press: [{ action: "macro", macro: "ghost" }] } } };
    const master = { id: "mb", type: "button", label: "M", pages: ["ghost_page"], placement: { x: 0, y: 85, w: 20, h: 12.5 }, bindings: { do: { press: [{ action: "macro", macro: "all_on" }] } } };
    const macros = [{ id: "m", name: "M", steps: [{ action: "macro", macro: "ghost" }] }];
    expect(checked(project({ elements: [button], masters: [master], macros, idle: "ghost_idle" })).map((i) => i.location)).toEqual([
      "Main > b1 > press",
      "Master Elements > mb",
      'Macro "M" > call macro',
      "Settings > Idle Page",
    ]);
    expect(checked(project({ idle: "ghost_idle" }))).toEqual([
      { severity: "error", message: 'Idle page "ghost_idle" does not exist', location: "Settings > Idle Page" },
    ]);
  });

  it("takes an idle page that exists, and none", () => {
    expect(checked(project({ idle: "other" }))).toEqual([]);
    expect(checked(project({ idle: "" }))).toEqual([]);
  });
});
