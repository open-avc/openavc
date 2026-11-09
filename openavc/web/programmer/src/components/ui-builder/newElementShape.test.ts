import { describe, it, expect } from "vitest";
import {
  ELEMENT_TYPES,
  createDefaultElement,
  defaultAspectLock,
  defaultElementSize,
  DEFAULT_ELEMENT_SIZES,
  FALLBACK_ELEMENT_SIZE,
  VIDEO_ASPECT_LOCK,
} from "./uiBuilderHelpers";
import type { PluginExtension } from "../../api/pluginClient";

// What a control fresh from the palette is, field by field and in the order
// its fields are written: the project file the Builder saves keeps that order,
// so a reordering shows up as a diff in every saved project.

const BASE = (id: string, type: string, aspect: number | null = null) =>
  `{"id":"${id}","type":"${type}","parent":null,"aspect_lock":${aspect},"style":{},"bindings":{}`;

const SHAPES: Record<string, string> = {
  button: BASE("button_1", "button") + `,"label":"Button"}`,
  label: BASE("label_1", "label") + `,"text":"Label"}`,
  status_led: BASE("status_led_1", "status_led", 1) + `,"label":"Status"}`,
  slider: BASE("slider_1", "slider") +
    `,"label":"Slider","min":0,"max":100,"step":1,"scale_to_full":true,"response":"linear"}`,
  page_nav: BASE("page_nav_1", "page_nav") + `,"label":"Next Page","target_page":"","highlight_current":true}`,
  select: BASE("select_1", "select") +
    `,"label":"Select","options":[{"label":"Option 1","value":"option_1"},{"label":"Option 2","value":"option_2"}]}`,
  text_input: BASE("text_input_1", "text_input") + `,"label":"Input","placeholder":"Type here..."}`,
  image: BASE("image_1", "image") + `,"label":""}`,
  camera_preset: BASE("camera_preset_1", "camera_preset", 1) + `,"label":"Preset","preset_number":1}`,
  gauge: `{"id":"gauge_1","type":"gauge","parent":null,"aspect_lock":null,` +
    `"style":{"gauge_width":8,"show_value":true,"show_ticks":true,"tick_count":5},"bindings":{},` +
    `"label":"Gauge","min":0,"max":100,"unit":"%","arc_angle":240}`,
  level_meter: `{"id":"level_meter_1","type":"level_meter","parent":null,"aspect_lock":null,` +
    `"style":{"meter_segments":20,"show_peak":true,"peak_hold_ms":1500},"bindings":{},` +
    `"label":"Level","min":-60,"max":0,"orientation":"vertical"}`,
  fader: `{"id":"fader_1","type":"fader","parent":null,"aspect_lock":null,` +
    `"style":{"show_value":true,"show_scale":true},"bindings":{},"label":"Fader","min":0,"max":100,"step":1,` +
    `"unit":"%","scale_to_full":true,"response":"linear","orientation":"vertical"}`,
  group: BASE("group_1", "group") + `,"label":"Container","label_position":"top-left"}`,
  clock: BASE("clock_1", "clock") + `,"clock_mode":"time"}`,
  keypad: BASE("keypad_1", "keypad") +
    `,"label":"Keypad","digits":4,"auto_send":false,"keypad_style":"numeric","show_display":true}`,
  list: BASE("list_1", "list") +
    `,"label":"Sources","list_style":"selectable","item_height":${44 / 14},` +
    `"items":[{"label":"Item 1","value":"1"},{"label":"Item 2","value":"2"},{"label":"Item 3","value":"3"}]}`,
  matrix: BASE("matrix_1", "matrix") +
    `,"label":"Video Routing","matrix_config":{"sources":{"from":{"count":4,"labels":["Input 1","Input 2","Input 3","Input 4"]}},` +
    `"destinations":{"from":{"count":4,"labels":["Output 1","Output 2","Output 3","Output 4"]}}},"matrix_style":"tiles"}`,
  custom: BASE("custom_1", "custom") + `,"custom_file":"","custom_config":{}}`,
};

describe("createDefaultElement", () => {
  it("every palette type has a pinned shape here", () => {
    for (const t of ELEMENT_TYPES) expect(Object.keys(SHAPES)).toContain(t.type);
  });

  it.each(Object.keys(SHAPES))("%s starts as it always has, fields in order", (type) => {
    expect(JSON.stringify(createDefaultElement(type, new Set()))).toBe(SHAPES[type]);
  });

  it("a type it does not know is labelled with its own name", () => {
    expect(JSON.stringify(createDefaultElement("spacer", new Set()))).toBe(
      BASE("spacer_1", "spacer") + `,"label":"spacer"}`,
    );
  });

  it("a plugin element is typed plugin, its id built from the whole spelling", () => {
    expect(JSON.stringify(createDefaultElement("plugin:acme_tiles:status_tile", new Set()))).toBe(
      `{"id":"plugin:acme_tiles:status_tile_1","type":"plugin","parent":null,"aspect_lock":null,"style":{},` +
        `"bindings":{},"plugin_type":"status_tile","plugin_id":"acme_tiles","plugin_config":{}}`,
    );
  });

  it("a plugin type may itself hold a colon", () => {
    const el = createDefaultElement("plugin:acme:a:b", new Set());
    expect([el.plugin_id, el.plugin_type, el.id]).toEqual(["acme", "a:b", "plugin:acme:a:b_1"]);
  });

  it("a plugin spelling with no type part gives an empty type", () => {
    const el = createDefaultElement("plugin:acme", new Set());
    expect([el.type, el.plugin_id, el.plugin_type, el.aspect_lock]).toEqual(["plugin", "acme", "", null]);
    const bare = createDefaultElement("plugin:", new Set());
    expect([bare.plugin_id, bare.plugin_type]).toEqual(["", ""]);
  });

  it("a word that only starts like a plugin is an unknown type", () => {
    const el = createDefaultElement("plugins", new Set());
    expect([el.type, el.label, el.plugin_id]).toEqual(["plugins", "plugins", undefined]);
  });

  it("a video plugin element is born locked to 16:9", () => {
    for (const t of ["video_stream", "video_panel", "camera_panel"]) {
      expect(createDefaultElement(`plugin:acme:${t}`, new Set()).aspect_lock).toBe(VIDEO_ASPECT_LOCK);
    }
    expect(createDefaultElement("plugin:acme:video_wall", new Set()).aspect_lock).toBeNull();
  });

  it("takes the first free id for its type", () => {
    expect(createDefaultElement("button", new Set(["button_1", "button_2", "button_4"])).id).toBe("button_3");
    expect(createDefaultElement("button", new Set(["label_1"])).id).toBe("button_1");
    expect(createDefaultElement("plugin:a:b", new Set(["plugin:a:b_1"])).id).toBe("plugin:a:b_2");
  });

  it("each call builds its own objects, nothing shared between two controls", () => {
    for (const type of [...Object.keys(SHAPES), "plugin:acme:tile"]) {
      const a = createDefaultElement(type, new Set()) as unknown as Record<string, unknown>;
      const b = createDefaultElement(type, new Set()) as unknown as Record<string, unknown>;
      expect(a).not.toBe(b);
      for (const [key, value] of Object.entries(a)) {
        if (value && typeof value === "object") expect(b[key]).not.toBe(value);
      }
    }
    const one = createDefaultElement("matrix", new Set()) as unknown as { matrix_config: { sources: unknown } };
    const two = createDefaultElement("matrix", new Set()) as unknown as { matrix_config: { sources: unknown } };
    expect(one.matrix_config.sources).not.toBe(two.matrix_config.sources);
    const s1 = createDefaultElement("select", new Set()).options as unknown[];
    const s2 = createDefaultElement("select", new Set()).options as unknown[];
    expect(s1[0]).not.toBe(s2[0]);
  });
});

describe("defaultElementSize", () => {
  const ext = (plugin_id: string, type: string, default_size?: { w?: number; h?: number }) =>
    ({ plugin_id, type, default_size }) as unknown as PluginExtension;

  it("a palette type gets its own size, the very object in the table", () => {
    for (const [type, size] of Object.entries(DEFAULT_ELEMENT_SIZES)) {
      expect(defaultElementSize(type)).toBe(size);
    }
  });

  it("a type with no size gets the fallback object", () => {
    expect(defaultElementSize("spacer")).toBe(FALLBACK_ELEMENT_SIZE);
  });

  it("a plugin element takes the size its extension declares, side by side", () => {
    const panel = [ext("acme", "tile", { w: 10, h: 20 }), ext("acme", "half", { w: 12 }), ext("acme", "a:b", { h: 7 })];
    expect(defaultElementSize("plugin:acme:tile", panel)).toEqual({ w: 10, h: 20 });
    expect(defaultElementSize("plugin:acme:half", panel)).toEqual({ w: 12, h: FALLBACK_ELEMENT_SIZE.h });
    expect(defaultElementSize("plugin:acme:a:b", panel)).toEqual({ w: FALLBACK_ELEMENT_SIZE.w, h: 7 });
  });

  it("a plugin element nothing declares gets a fresh copy of the fallback", () => {
    const panel = [ext("other", "tile", { w: 10, h: 20 }), ext("acme", "tile2", { w: 10, h: 20 })];
    const size = defaultElementSize("plugin:acme:tile", panel);
    expect(size).toEqual(FALLBACK_ELEMENT_SIZE);
    expect(size).not.toBe(FALLBACK_ELEMENT_SIZE);
    expect(defaultElementSize("plugin:acme:tile")).toEqual(FALLBACK_ELEMENT_SIZE);
    expect(defaultElementSize("plugin:acme:tile", [ext("acme", "tile")])).toEqual(FALLBACK_ELEMENT_SIZE);
  });

  it("matches a plugin's id and type exactly, the first declaration winning", () => {
    const panel = [ext("acme", "tile", { w: 1, h: 2 }), ext("acme", "tile", { w: 3, h: 4 })];
    expect(defaultElementSize("plugin:acme:tile", panel)).toEqual({ w: 1, h: 2 });
    expect(defaultElementSize("plugin:acm:tile", panel)).toEqual(FALLBACK_ELEMENT_SIZE);
  });
});

describe("defaultAspectLock", () => {
  it("locks only the types that have a lock", () => {
    expect(defaultAspectLock("status_led")).toBe(1);
    expect(defaultAspectLock("camera_preset")).toBe(1);
    expect(defaultAspectLock("image")).toBeNull();
    expect(defaultAspectLock("button")).toBeNull();
  });

  it("locks a video plugin element by its type part alone", () => {
    expect(defaultAspectLock("plugin:any:camera_panel")).toBe(VIDEO_ASPECT_LOCK);
    expect(defaultAspectLock("plugin:any:x:camera_panel")).toBeNull();
    expect(defaultAspectLock("plugin:any")).toBeNull();
    expect(defaultAspectLock("video_panel")).toBeNull();
  });
});
