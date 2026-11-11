/**
 * The Theme Studio's element gallery: the page its preview shows by default,
 * one of every common control laid out on a 16-by-16 grid of the preview page
 * (the arrangement as percentages), and the state those controls show. A change
 * to a control's shape (its options, its sizes, how a matrix lists its routes)
 * is made here, beside the demo state: a control shows a demo value only where
 * its binding's key, or a matrix destination's route key, is one
 * `GALLERY_DEMO_STATE` sets.
 */
import type { UIPage, UIElement, Placement } from "../../api/types";
import { REM_BASE_PX } from "./uiBuilderHelpers";

export const GALLERY_PAGE_ID = "_studio_gallery";

// The gallery's arrangement, as percentages of the preview page: every edge on
// a sixteenth of it, as the page's snap grid is.
const GALLERY_PLACEMENTS: Record<string, Placement> = {
  g_header: { x: 0, y: 0, w: 100, h: 6.25 },
  g_button: { x: 0, y: 6.25, w: 18.75, h: 12.5 },
  g_button_active: { x: 18.75, y: 6.25, w: 18.75, h: 12.5 },
  g_label_demo: { x: 37.5, y: 6.25, w: 18.75, h: 12.5 },
  g_led_ok: { x: 56.25, y: 6.25, w: 6.25, h: 12.5 },
  g_led_warn: { x: 62.5, y: 6.25, w: 6.25, h: 12.5 },
  g_clock: { x: 68.75, y: 6.25, w: 18.75, h: 12.5 },
  g_camera: { x: 87.5, y: 6.25, w: 12.5, h: 12.5 },
  g_slider: { x: 0, y: 18.75, w: 31.25, h: 12.5 },
  g_pagenav: { x: 31.25, y: 18.75, w: 25, h: 12.5 },
  g_select: { x: 56.25, y: 18.75, w: 18.75, h: 12.5 },
  g_text: { x: 75, y: 18.75, w: 25, h: 12.5 },
  g_gauge: { x: 0, y: 31.25, w: 18.75, h: 37.5 },
  g_meter: { x: 18.75, y: 31.25, w: 6.25, h: 37.5 },
  g_fader: { x: 25, y: 31.25, w: 18.75, h: 37.5 },
  g_keypad: { x: 43.75, y: 31.25, w: 31.25, h: 37.5 },
  g_list: { x: 75, y: 31.25, w: 25, h: 37.5 },
  g_matrix: { x: 0, y: 68.75, w: 50, h: 31.25 },
};

const GALLERY_ELEMENTS: UIElement[] = [
  {
    id: "g_header",
    type: "label",
    text: "Theme Preview Gallery",
    // A title, so bigger than the controls under it, which start at the
    // panel's own text default (28px on the reference).
    style: { font_size: 36 / REM_BASE_PX, text_align: "center", font_weight: 700 },
    bindings: {},
  },
  {
    id: "g_button",
    type: "button",
    label: "Button",
    style: {},
    bindings: {},
  },
  {
    // Active button shows what an "on" / pressed state looks like in this
    // theme. Active styling derives from Accent (so editing accent updates
    // it live) plus Button Text for label color.
    id: "g_button_active",
    type: "button",
    label: "Active",
    style: {
      bg_color: "var(--panel-accent)",
      text_color: "var(--panel-button-text)",
    },
    bindings: {},
  },
  {
    id: "g_label_demo",
    type: "label",
    text: "Demo label text",
    style: {},
    bindings: {},
  },
  // Status LEDs — color bindings reference theme tokens so each LED
  // visibly demos its semantic color (success/warning/danger).
  {
    id: "g_led_ok",
    type: "status_led",
    label: "OK",
    style: {},
    bindings: {
      color: {
        key: "gallery.led_on",
        map: { true: "var(--panel-success)" },
        default: "#9E9E9E",
      },
    },
  },
  {
    id: "g_led_warn",
    type: "status_led",
    label: "Warn",
    style: {},
    bindings: {
      color: {
        key: "gallery.led_on",
        map: { true: "var(--panel-warning)" },
        default: "#9E9E9E",
      },
    },
  },
  {
    id: "g_clock",
    type: "clock",
    clock_mode: "current_time",
    format: "HH:mm",
    style: {},
    bindings: {},
  },
  {
    id: "g_camera",
    type: "camera_preset",
    label: "Preset",
    preset_number: 1,
    style: {},
    bindings: {},
  },
  {
    id: "g_slider",
    type: "slider",
    label: "Volume",
    min: 0,
    max: 100,
    orientation: "horizontal",
    style: { show_value: true },
    bindings: { value: { key: "gallery.slider" } },
  },
  {
    id: "g_pagenav",
    type: "page_nav",
    label: "Pages",
    target_page: GALLERY_PAGE_ID,
    style: {},
    bindings: {},
  },
  {
    id: "g_select",
    type: "select",
    options: [
      { label: "HDMI 1", value: "hdmi1" },
      { label: "HDMI 2", value: "hdmi2" },
      { label: "USB-C", value: "usbc" },
    ],
    style: {},
    bindings: { value: { key: "gallery.select" } },
  },
  {
    id: "g_text",
    type: "text_input",
    placeholder: "Type here…",
    style: {},
    bindings: { value: { key: "gallery.text" } },
  },
  {
    id: "g_gauge",
    type: "gauge",
    label: "CPU",
    min: 0,
    max: 100,
    unit: "%",
    style: {},
    bindings: { value: { key: "gallery.gauge" } },
  },
  {
    id: "g_meter",
    type: "level_meter",
    min: -60,
    max: 0,
    orientation: "vertical",
    style: {},
    bindings: { value: { key: "gallery.meter" } },
  },
  {
    id: "g_fader",
    type: "fader",
    label: "Mic 1",
    min: -60,
    max: 10,
    orientation: "vertical",
    style: {},
    bindings: { value: { key: "gallery.fader" } },
  },
  {
    id: "g_keypad",
    type: "keypad",
    digits: 4,
    keypad_style: "numeric",
    show_display: true,
    style: {},
    bindings: {},
  },
  {
    id: "g_list",
    type: "list",
    items: [
      { label: "Lecture mode", value: "p1" },
      { label: "Discussion", value: "p2" },
      { label: "Presentation", value: "p3" },
      { label: "Hybrid", value: "p4" },
    ],
    style: {},
    bindings: { selected: { key: "gallery.list" } },
  },
  {
    id: "g_matrix",
    type: "matrix",
    // Written out rather than generated: the gallery is a picture of a
    // matrix, and this is the form somebody reading it should see.
    matrix_config: {
      sources: [
        { value: 1, label: "Cam 1" },
        { value: 2, label: "PC" },
        { value: 3, label: "Doc" },
      ],
      destinations: [
        { value: 1, label: "Main", route_key: "gallery.route.1" },
        { value: 2, label: "Conf", route_key: "gallery.route.2" },
        { value: 3, label: "Stream", route_key: "gallery.route.3" },
      ],
    },
    style: { cell_size: 32 / REM_BASE_PX },
    bindings: {},
  },
];

/** The gallery page, its controls a fresh copy each time. */
export function galleryPage(): UIPage {
  return {
    id: GALLERY_PAGE_ID,
    name: "Element Gallery",
    page_type: "page",
    snap: { enabled: true, x: 100 / 16, y: 100 / 16 },
    elements: structuredClone(GALLERY_ELEMENTS),
    layouts: [
      {
        id: "landscape",
        orientation: "landscape",
        primary: true,
        placements: GALLERY_PLACEMENTS,
        hidden: [],
      },
    ],
  };
}

/** The state the gallery's controls show. */
export const GALLERY_DEMO_STATE: Record<string, unknown> = {
  "gallery.btn_active": true,
  "gallery.led_on": true,
  "gallery.led_off": false,
  "gallery.slider": 65,
  "gallery.select": "hdmi2",
  "gallery.text": "Conference Room A",
  "gallery.gauge": 72,
  "gallery.meter": -8,
  "gallery.fader": -6,
  "gallery.list": "p2",
  "gallery.route.Main": "Cam 1",
  "gallery.route.Conf": "PC",
  "gallery.route.Stream": "Doc",
};
