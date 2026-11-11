import { useState } from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Placement, ProjectConfig, UIElement, UIPage } from "../../../api/types";
import { usePluginStore } from "../../../store/pluginStore";
import { BasicProperties } from "./BasicProperties";

// What the Basic section draws for every element type, and the patch each of
// its controls sends. The pickers and dialogs it hands values to are other
// files' business, so each is a stand-in that shows the props it was given and
// offers buttons that call its callbacks with a sample value: what is pinned is
// what this section passes in and does with what comes back.
//
// Every control is driven from a fresh render, inside an editor that applies
// each patch the way the Builder does, and the record says whether the control
// is still the same node afterwards (a control that is torn down and rebuilt
// loses focus mid-typing).

const { makeStub, calls } = vi.hoisted(() => {
  const calls: string[] = [];
  type Emit = [callback: string, argument: unknown];
  function makeStub(react: typeof import("react"), name: string, emits: Emit[]) {
    return function Stub(props: Record<string, unknown>) {
      const data: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(props)) {
        if (typeof value !== "function") data[key] = value;
      }
      return react.createElement(
        "div",
        { "data-stub": name, "data-props": JSON.stringify(data) },
        ...emits.map(([callback, argument], i) =>
          react.createElement(
            "button",
            {
              key: i,
              "data-emit": `${callback} ${JSON.stringify(argument)}`,
              onClick: () => (props[callback] as ((v: unknown) => void) | undefined)?.(argument),
            },
            `${name}.${callback}`,
          ),
        ),
      );
    };
  }
  return { makeStub, calls };
});

vi.mock("../../shared/CopyButton", async () => {
  const react = await import("react");
  return { CopyButton: makeStub(react, "CopyButton", []) };
});
vi.mock("../IconPicker", async () => {
  const react = await import("react");
  return { IconPicker: makeStub(react, "IconPicker", [["onChange", "power"], ["onChange", ""]]) };
});
vi.mock("../AssetPicker", async () => {
  const react = await import("react");
  return { AssetPicker: makeStub(react, "AssetPicker", [["onChange", "assets://pic.png"], ["onChange", ""]]) };
});
vi.mock("../../shared/InlineColorPicker", async () => {
  const react = await import("react");
  return { InlineColorPicker: makeStub(react, "InlineColorPicker", [["onChange", "#123456"], ["onChange", ""]]) };
});
vi.mock("../../shared/VariableKeyPicker", async () => {
  const react = await import("react");
  return { VariableKeyPicker: makeStub(react, "VariableKeyPicker", [["onChange", "var.start"], ["onChange", ""]]) };
});
vi.mock("../../shared/OptionSourcePicker", async () => {
  const react = await import("react");
  return { OptionSourcePicker: makeStub(react, "OptionSourcePicker", [["onChange", "cam_2"]]) };
});
vi.mock("../../shared/RefPickers", async () => {
  const react = await import("react");
  return {
    MacroRefPicker: makeStub(react, "MacroRefPicker", [["onChange", "macro_1"]]),
    DeviceRefPicker: makeStub(react, "DeviceRefPicker", [["onChange", "dev_1"]]),
  };
});
vi.mock("../../shared/SearchableSelect", async () => {
  const react = await import("react");
  return { SearchableSelect: makeStub(react, "SearchableSelect", [["onChange", "settings"], ["onChange", "$back"]]) };
});
vi.mock("../BindingEditor/DeviceValuePicker", async (importOriginal) => {
  const react = await import("react");
  return {
    ...await importOriginal<typeof import("../BindingEditor/DeviceValuePicker")>(),
    MatchDriverRangeRow: makeStub(react, "MatchDriverRangeRow", [["onElementPatch", { min: -10, max: 10 }]]),
  };
});
vi.mock("../MatrixSetupDialog", async () => {
  const react = await import("react");
  return {
    MatrixSetupDialog: makeStub(react, "MatrixSetupDialog", [
      ["onApply", { matrix_config: { sources: [{ value: 1 }], destinations: [{ value: 1 }] } }],
      ["onClose", undefined],
    ]),
  };
});
vi.mock("./CustomControlConfig", async () => {
  const react = await import("react");
  return { CustomControlConfig: makeStub(react, "CustomControlConfig", [["onChange", { custom_file: "dial/index.html" }]]) };
});
vi.mock("./GrantEditor", async () => {
  const react = await import("react");
  return {
    GrantEditor: makeStub(react, "GrantEditor", [
      ["onChange", { devices: ["acme_widget"], variables: [], macros: false, navigate: false }],
    ]),
  };
});
vi.mock("../../../store/toastStore", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../../store/toastStore")>(),
  showInfo: (message: string) => { calls.push(`toast ${JSON.stringify(message)}`); },
}));

/** A value as written, keeping what JSON drops: undefined, NaN, key order. */
function repr(value: unknown): string {
  if (value === undefined) return "undefined";
  if (typeof value === "number") return Object.is(value, -0) ? "-0" : String(value);
  if (typeof value === "string") return JSON.stringify(value);
  if (value === null || typeof value === "boolean") return String(value);
  if (typeof value === "function") return "fn";
  if (Array.isArray(value)) return `[${value.map(repr).join(", ")}]`;
  if (typeof value === "object") {
    return `{${Object.entries(value).map(([k, v]) => `${k}: ${repr(v)}`).join(", ")}}`;
  }
  return String(value);
}

const ATTRIBUTES = [
  "type", "placeholder", "min", "max", "step", "rows", "title", "readonly", "style", "data-stub",
  "data-props", "data-emit",
];

/** The rendered section as an outline: tags, the attributes that matter (the
 *  inline style among them) and each control's live value, nested as drawn. */
function outline(node: Node, depth = 0, lines: string[] = []): string[] {
  const pad = "  ".repeat(depth);
  if (node.nodeType === Node.TEXT_NODE) {
    const text = (node.textContent ?? "").replace(/\s+/g, " ").trim();
    if (text) lines.push(`${pad}"${text}"`);
    return lines;
  }
  if (!(node instanceof HTMLElement)) return lines;
  const parts = [node.tagName.toLowerCase()];
  for (const name of ATTRIBUTES) {
    const value = node.getAttribute(name);
    if (value !== null) parts.push(`${name}=${JSON.stringify(value)}`);
  }
  if (node instanceof HTMLInputElement) {
    parts.push(node.type === "checkbox" ? `checked=${node.checked}` : `value=${JSON.stringify(node.value)}`);
  } else if (node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement) {
    parts.push(`value=${JSON.stringify(node.value)}`);
  } else if (node instanceof HTMLOptionElement) {
    parts.push(`value=${JSON.stringify(node.value)}`);
  }
  lines.push(pad + parts.join(" "));
  if (node.dataset.stub) return lines;
  node.childNodes.forEach((child) => outline(child, depth + 1, lines));
  return lines;
}

const NUMBERS = ["7", "", "2.5", "0", "40", "-3"];
const TEXTS = ["abc", "", '{"a":1}'];

interface Step {
  name: string;
  fire: (control: HTMLElement) => void;
}

const change = (value: string): Step => ({
  name: `change ${JSON.stringify(value)}`,
  fire: (control) => fireEvent.change(control, { target: { value } }),
});

/** The interactions a control is put through, by what kind of control it is. */
function stepsFor(control: HTMLElement): Step[][] {
  if (control instanceof HTMLButtonElement) {
    return [[{ name: "click", fire: (c) => fireEvent.click(c) }]];
  }
  if (control instanceof HTMLSelectElement) {
    return Array.from(control.options).map((option) => [change(option.value)]);
  }
  if (control instanceof HTMLTextAreaElement) return TEXTS.map((v) => [change(v)]);
  if (!(control instanceof HTMLInputElement)) return [];
  switch (control.type) {
    case "checkbox":
      return [[{ name: "click", fire: (c) => fireEvent.click(c) }]];
    case "number":
      // A field already empty hears no change to "", so clearing is typed after a value.
      return [...NUMBERS.map((v) => [change(v)]), [change("7"), change("")]];
    case "range":
      return [[change("0.5")], [change("1")]];
    case "datetime-local":
      return [[change("2026-01-02T03:04")], [change("")]];
    default: {
      const key = (k: string): Step => ({ name: `key ${k}`, fire: (c) => fireEvent.keyDown(c, { key: k }) });
      const blur: Step = { name: "blur", fire: (c) => fireEvent.blur(c) };
      // Real focus, so the field's own blur() on Enter and Escape fires.
      const focus: Step = { name: "focus", fire: (c) => c.focus() };
      return [
        ...TEXTS.map((v) => [change(v)]),
        [change("abc"), change("")],
        [focus, blur],
        [focus, change(" new_id "), blur],
        [focus, change("abc"), key("Enter")],
        [focus, change("abc"), key("Escape")],
      ];
    }
  }
}

const project = {
  devices: [{ id: "sw", driver: "acme_switch", name: "Switch" }],
  macros: [{ id: "macro_1", name: "All On" }],
  ui: { pages: [] },
} as unknown as ProjectConfig;
const pages = [
  { id: "main", name: "Main", elements: [] },
  { id: "settings", name: "Settings", page_type: "overlay", elements: [] },
] as unknown as UIPage[];
const macros = [{ id: "macro_1", name: "All On" }, { id: "macro_2", name: "" }];
const placement: Placement = { x: 10, y: 20, w: 30, h: 12.5 };

type Shape = "page" | "master";

function Editor({ initial, shape }: { initial: UIElement; shape: Shape }) {
  const [element, setElement] = useState(initial);
  const [box, setBox] = useState(placement);
  return (
    <BasicProperties
      element={element}
      project={project}
      pages={pages}
      macros={macros}
      placement={shape === "page" ? box : undefined}
      onChangePlacement={shape === "page" ? (next) => {
        calls.push(`placement ${repr(next)}`);
        setBox(next);
      } : undefined}
      onChange={(patch) => {
        calls.push(`patch ${repr(patch)}`);
        setElement((prev) => ({ ...prev, ...patch }));
      }}
      onRename={shape === "page" ? (id) => { calls.push(`rename ${repr(id)}`); } : undefined}
    />
  );
}

interface Scenario {
  name: string;
  element: Record<string, unknown>;
  /** Buttons pressed, by their text, before anything is recorded. */
  prepare?: string[];
  shapes?: Shape[];
  panelElements?: unknown[];
}

const probes: string[] = [];
class ImageProbe {
  onload: (() => void) | null = null;
  naturalWidth = 0;
  naturalHeight = 0;
  set src(value: string) { probes.push(value); }
}

function mount(scenario: Scenario, shape: Shape) {
  const view = render(
    <Editor initial={{ id: "el", ...scenario.element } as unknown as UIElement} shape={shape} />,
  );
  for (const text of scenario.prepare ?? []) {
    const button = Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === text);
    if (!button) throw new Error(`no button "${text}"`);
    fireEvent.click(button);
  }
  return view;
}

const controlsOf = (root: HTMLElement) =>
  Array.from(root.querySelectorAll<HTMLElement>("input, select, textarea, button"));

/** What one scenario draws and what every control does, as lines. */
function record(scenario: Scenario, shape: Shape): string[] {
  calls.length = 0;
  probes.length = 0;
  const first = mount(scenario, shape);
  const lines = outline(first.container.firstElementChild!);
  if (probes.length) lines.push(`probe ${probes.join(" ")}`);
  if (calls.length) lines.push(...calls.map((c) => `on mount: ${c}`));
  const count = controlsOf(first.container).length;
  cleanup();

  for (let index = 0; index < count; index += 1) {
    const probe = mount(scenario, shape);
    const control = controlsOf(probe.container)[index];
    const label = outline(control)[0].replace(/ style="[^"]*"/, "");
    const runs = stepsFor(control).length;
    cleanup();
    for (let run = 0; run < runs; run += 1) {
      const view = mount(scenario, shape);
      const target = controlsOf(view.container)[index];
      const steps = stepsFor(target)[run];
      calls.length = 0;
      for (const step of steps) step.fire(target);
      const heard = calls.length ? calls.join(" ; ") : "nothing";
      const kept = target.isConnected ? "" : " [control replaced]";
      lines.push(`#${index} ${label} :: ${steps.map((s) => s.name).join(", ")} -> ${heard}${kept}`);
      cleanup();
    }
  }
  return lines;
}

const look = (extra: Record<string, unknown>) => ({ bindings: { show: { look: extra } } });

const SCENARIOS: Scenario[] = [
  { name: "an unknown type", element: { type: "widget" } },
  { name: "a type named like an object method", element: { type: "constructor" } },
  { name: "button, bare", element: { type: "button" } },
  { name: "button, multi-line label", element: { type: "button", label: "Line 1\nLine 2", style: { white_space: "pre-line", bg_color: "#111" } } },
  { name: "button, pre-wrap label", element: { type: "button", label: "x", style: { white_space: "pre-wrap" } } },
  { name: "button, icon and text", element: { type: "button", display_mode: "icon_text", icon: "power", icon_position: "top", icon_size: 2, icon_color: "#fff" } },
  { name: "button, image with no picture", element: { type: "button", display_mode: "image" } },
  {
    name: "button, image with tint and states",
    element: {
      type: "button", display_mode: "image_text", button_image: "assets://logo.png", image_fit: "contain",
      image_blend_mode: "multiply", image_opacity: 0.5, frameless: true, style: { bg_color: "#222" },
      ...look({ key: "var.on", states: { off: { bg_color: "#000" }, on: {} } }),
    },
  },
  {
    name: "button, recolor with unbound states",
    element: {
      type: "button", display_mode: "image", button_image: "assets://logo.png", image_blend_mode: "mask",
      ...look({ key: "", states: { a: { bg_color: "#abc" } } }),
    },
  },
  {
    name: "button, recolor with an on/off look",
    element: {
      type: "button", display_mode: "image", button_image: "logo.png", image_blend_mode: "screen", frameless: true,
      ...look({ style_active: { bg_color: "#0f0" } }),
    },
  },
  {
    name: "button, tint with no bindings",
    element: { type: "button", display_mode: "image", button_image: "logo.png", image_blend_mode: "multiply" },
  },
  {
    name: "button, tint with only an off look",
    element: {
      type: "button", display_mode: "image", button_image: "logo.png", image_blend_mode: "multiply",
      ...look({ style_inactive: {} }),
    },
  },
  {
    name: "button, a look whose states are not a map",
    element: {
      type: "button", display_mode: "image", button_image: "logo.png", image_blend_mode: "multiply",
      ...look({ states: 0, style_active: { bg_color: "#0f0" } }),
    },
  },
  {
    name: "button, tint with no look",
    element: { type: "button", display_mode: "image", button_image: "logo.png", image_blend_mode: "mask", bindings: { show: { value: { key: "var.x" } } } },
  },
  { name: "label, bare", element: { type: "label" } },
  { name: "label, multi-line text and decimals", element: { type: "label", text: "Hi", display_decimals: 2, style: { white_space: "pre-wrap" }, icon: "info" } },
  { name: "page nav, bare", element: { type: "page_nav" } },
  { name: "page nav, set", element: { type: "page_nav", label: "Go", target_page: "settings", highlight_current: true } },
  { name: "camera preset, bare", element: { type: "camera_preset" } },
  { name: "camera preset, set", element: { type: "camera_preset", preset_number: 3, label: "Wide", icon: "camera" } },
  { name: "slider, bare", element: { type: "slider" }, shapes: ["page", "master"] },
  {
    name: "slider, set",
    element: {
      type: "slider", min: -10, max: 10, step: 0.5, orientation: "vertical", thumb_size: 2.5,
      style: { show_value: true }, output_min: 0, output_max: 255, scale_to_full: false,
      response: "logarithmic", response_db_range: 40, unit: "dB", display_decimals: 1,
      send_throttle_ms: 20,
    },
    shapes: ["page", "master"],
  },
  { name: "slider, on release", element: { type: "slider", send_on_release: true, response: "linear" } },
  { name: "text input, bare", element: { type: "text_input" } },
  { name: "text input, set", element: { type: "text_input", placeholder: "Name" } },
  { name: "image, bare", element: { type: "image" } },
  { name: "image, asset", element: { type: "image", src: "assets://logo.png", label: "Logo", object_fit: "cover" } },
  { name: "image, url with a ratio", element: { type: "image", src: "https://example.test/a.png", aspect_lock: 1.5 } },
  { name: "gauge, bare", element: { type: "gauge" } },
  {
    name: "gauge, set",
    element: {
      type: "gauge", min: 0, max: 200, unit: "W", display_decimals: 0, arc_angle: 180,
      style: { gauge_color: "#f00", gauge_bg_color: "#333", gauge_width: 12, show_value: false, show_ticks: true, tick_count: 8 },
      zones: [{ from: 0, to: 100, color: "#4CAF50" }, { from: 100, to: 200, color: "#F44336" }],
    },
  },
  { name: "gauge, one zone", element: { type: "gauge", zones: [{ from: 0, to: 10, color: "#fff" }], min: 5 } },
  { name: "gauge, no ticks", element: { type: "gauge", style: { show_ticks: false }, max: 7 } },
  { name: "level meter, bare", element: { type: "level_meter" }, shapes: ["page", "master"] },
  {
    name: "level meter, set",
    element: {
      type: "level_meter", min: -80, max: 6, orientation: "horizontal",
      style: { meter_segments: 30, show_peak: true, peak_hold_ms: 800, green_to: -20, yellow_to: -6 },
    },
  },
  { name: "level meter, no peak", element: { type: "level_meter", style: { show_peak: false } } },
  { name: "fader, bare", element: { type: "fader" }, shapes: ["page", "master"] },
  {
    name: "fader, set",
    element: {
      type: "fader", min: 0, max: 1, step: 0.01, unit: "%", orientation: "horizontal",
      style: { show_value: false, show_scale: false }, output_min: -60, output_max: 0, scale_to_full: true,
      response: "logarithmic", send_on_release: true,
    },
  },
  { name: "group, bare", element: { type: "group" } },
  { name: "group, set", element: { type: "group", label: "Zone", label_position: "bottom-right" } },
  { name: "clock, bare", element: { type: "clock" } },
  { name: "clock, date", element: { type: "clock", clock_mode: "date", format: "D MMM" } },
  { name: "clock, date and time", element: { type: "clock", clock_mode: "datetime", timezone: "Europe/London" } },
  { name: "clock, countdown to a time", element: { type: "clock", clock_mode: "countdown", target_time: "2026-05-01T09:00" } },
  {
    name: "clock, countdown from state",
    element: { type: "clock", clock_mode: "countdown", bindings: { show: { value: { source: "state", key: "var.end" }, look: { key: "var.x" } }, do: { press: {} } } },
  },
  { name: "clock, countdown from a start key", element: { type: "clock", clock_mode: "countdown", start_key: "var.legacy" } },
  { name: "clock, countdown with only a value binding", element: { type: "clock", clock_mode: "countdown", bindings: { show: { value: { key: "var.end" } } } } },
  { name: "clock, elapsed", element: { type: "clock", clock_mode: "elapsed", start_key: "var.started" } },
  { name: "clock, meeting", element: { type: "clock", clock_mode: "meeting", duration_minutes: 30 } },
  { name: "clock, unknown mode", element: { type: "clock", clock_mode: "lunar" } },
  { name: "list, bare", element: { type: "list" } },
  {
    name: "list, set",
    element: { type: "list", list_style: "action", item_height: 3, items: [{ label: "A", value: "a" }, { label: "B", value: "b" }] },
  },
  { name: "matrix, bare", element: { type: "matrix" } },
  {
    name: "matrix, generator",
    element: {
      type: "matrix", matrix_style: "list",
      matrix_config: {
        sources: { from: { count: 2, labels: ["Laptop", "PC"], label_key: "device.sw.input_*_name" }, exclude: [3] },
        destinations: {
          from: { count: 3, labels: ["A"], route_key: "device.sw.output_*_source", audio_route_key: "device.sw.output_*_audio", label_key: "device.sw.output_*_name", lock_key: "var.lock_*" },
        },
        audio_follow_video: true, show_lock: true, show_mute: true,
        presets: [{ name: "All to 1", macro: "macro_1" }, { name: "Off" }],
      },
      bindings: { do: { audio_route: { action: "macro" }, mute_route: { action: "macro" }, audio_mute_route: {} } },
      style: { crosspoint_active_color: "#0f0", crosspoint_inactive_color: "#333", cell_size: 3 },
    },
  },
  {
    name: "matrix, one preset",
    element: { type: "matrix", matrix_config: { presets: [{ name: "Only" }] } },
  },
  {
    name: "matrix, unbacked lock and missing routes",
    element: {
      type: "matrix",
      matrix_config: { sources: { from: { count: 2 } }, destinations: { from: { count: 2, lock_key: "device.sw.lock_*" } }, show_lock: true, show_mute: true, audio_follow_video: true },
    },
  },
  {
    name: "matrix, mute left on by default with audio follow",
    element: { type: "matrix", matrix_config: { audio_follow_video: true } },
  },
  {
    name: "matrix, lists on both axes",
    element: {
      type: "matrix",
      matrix_config: {
        sources: [{ value: 1, label: "Laptop" }, 2, null],
        destinations: [{ value: "out1", route_key: "device.sw.o1", audio_route_key: "device.sw.a1", lock_key: "var.l1" }, { value: "out2", label: "B" }, "x"],
        show_lock: true, show_mute: false,
      },
    },
  },
  {
    name: "matrix, lists without audio",
    element: { type: "matrix", matrix_config: { sources: [], destinations: [{ value: 1 }], show_lock: true } },
  },
  {
    name: "matrix, sources a list and destinations a generator",
    element: { type: "matrix", matrix_config: { sources: [{ value: 1 }], destinations: { from: { count: 1 } } } },
  },
  {
    name: "matrix, setup dialog open",
    element: { type: "matrix", matrix_config: { sources: [{ value: 1 }], destinations: [{ value: 1 }] } },
    prepare: ["Set up again from a device…"],
  },
  { name: "matrix, odd axes", element: { type: "matrix", matrix_config: { sources: "x", destinations: { from: [1, 2] } } } },
  { name: "keypad, bare", element: { type: "keypad" } },
  { name: "keypad, phone sending on its own", element: { type: "keypad", keypad_style: "phone", digits: 6, auto_send: true, auto_send_delay_ms: 800 } },
  { name: "keypad, no display", element: { type: "keypad", show_display: false, mask_display: true } },
  { name: "keypad, masked", element: { type: "keypad", mask_display: true, auto_send: false } },
  { name: "custom, bare", element: { type: "custom" } },
  { name: "custom, set", element: { type: "custom", custom_file: "dial/index.html", custom_config: { a: 1 }, grant: { devices: ["sw"] } } },
  { name: "plugin, with no schema", element: { type: "plugin", plugin_id: "acme", plugin_type: "tile", plugin_config: { a: 1 } } },
  {
    name: "plugin, with a schema",
    element: {
      type: "plugin", plugin_id: "acme", plugin_type: "tile",
      plugin_config: { flag: false, count: 3, ratio: null, src: "cam_1" },
    },
    panelElements: [
      { plugin_id: "acme", type: "other", config_schema: [{ key: "x", label: "X", type: "string" }] },
      {
        plugin_id: "acme", type: "tile",
        config_schema: [
          { key: "flag", label: "Flag", type: "boolean", default: true },
          { key: "on", label: "On", type: "boolean", default: true },
          { key: "mode", label: "Mode", type: "select", options: ["a", "b"], default: "a" },
          { key: "src", label: "Source", type: "select", options_source: "video_streams" },
          { key: "pick", label: "Pick", type: "select" },
          { key: "kind", label: "Kind", type: "enum", options: ["x"] },
          { key: "count", label: "Count", type: "integer", default: 2 },
          { key: "ratio", label: "Ratio", type: "float" },
          { key: "level", label: "Level", type: "number", default: 0 },
          { key: "watch", label: "Watch", type: "state_key" },
          { key: "run", label: "Run", type: "macro_ref", default: "macro_1" },
          { key: "dev", label: "Device", type: "device_ref" },
          { key: "notes", label: "Notes", type: "text", default: "hello" },
          { key: "memo", label: "Memo", type: "text" },
          { key: "name", label: "Name", type: "string" },
        ],
      },
    ],
  },
  {
    name: "plugin, schema edited as JSON",
    element: { type: "plugin", plugin_id: "acme", plugin_type: "tile" },
    panelElements: [{ plugin_id: "acme", type: "tile", config_schema: [{ key: "name", label: "Name", type: "string" }] }],
    prepare: ["Edit as JSON"],
  },
  {
    name: "plugin, empty schema",
    element: { type: "plugin" },
    panelElements: [{ plugin_id: "", type: "", config_schema: [] }],
  },
  { name: "select, bare", element: { type: "select" } },
  { name: "select, set", element: { type: "select", options: [{ label: "One", value: "1" }, { label: "Two", value: "2" }] } },
];

const emptyExtensions = usePluginStore.getState().extensions;

beforeEach(() => {
  vi.stubGlobal("Image", ImageProbe);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  usePluginStore.setState({ extensions: emptyExtensions });
});

describe("the Basic section, every element type", () => {
  for (const scenario of SCENARIOS) {
    for (const shape of scenario.shapes ?? ["page"]) {
      it(`${scenario.name} (${shape})`, () => {
        if (scenario.panelElements) {
          usePluginStore.setState({
            extensions: { ...emptyExtensions, panel_elements: scenario.panelElements } as typeof emptyExtensions,
          });
        }
        expect(record(scenario, shape).join("\n")).toMatchSnapshot();
      });
    }
  }
});

/** The control in the row labelled `label`, or the stand-in named `stub`. */
function rowControl(root: HTMLElement, label: string): Element | null {
  if (label.startsWith("stub:")) return root.querySelector(`[data-stub="${label.slice(5)}"]`);
  const caption = Array.from(root.querySelectorAll("label")).find((l) => l.textContent === label);
  return caption?.parentElement?.querySelector("input, select, textarea, [data-stub]") ?? null;
}

function Switcher({ first, second }: { first: UIElement; second: UIElement }) {
  const [element, setElement] = useState(first);
  return (
    <>
      <BasicProperties element={element} project={project} pages={pages} macros={macros} onChange={() => {}} />
      <button onClick={() => setElement(second)}>Select the other</button>
    </>
  );
}

// Selecting another element of a type that shares rows with this one keeps
// those rows' controls, as it did when every type's fields were one component.
describe("selecting an element of another type", () => {
  it.each([
    ["a button, then a page nav", "button", "page_nav", ["Label", "Icon", "Icon Size", "Icon Color"]],
    ["a label, then a camera preset", "label", "camera_preset", ["Icon", "Position"]],
    ["a custom control, then a plugin element", "custom", "plugin", ["stub:GrantEditor"]],
    ["a gauge, then a fader", "gauge", "fader", ["Label"]],
  ])("%s keeps the rows they share", (_name, firstType, secondType, rows) => {
    const view = render(
      <Switcher
        first={{ id: "a", type: firstType, icon: "power" } as unknown as UIElement}
        second={{ id: "b", type: secondType, icon: "power" } as unknown as UIElement}
      />,
    );
    const before = rows.map((row) => rowControl(view.container, row));
    before.forEach((node, i) => expect(node, rows[i]).not.toBeNull());
    fireEvent.click(view.getByText("Select the other"));
    rows.forEach((row, i) => {
      expect(rowControl(view.container, row), row).toBe(before[i]);
    });
  });
});
