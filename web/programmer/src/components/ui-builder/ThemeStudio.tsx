import { useEffect, useMemo, useRef, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import {
  X, Copy, Trash2, Save, Upload, Download, RotateCcw, AlertTriangle, Check, FilePlus,
} from "lucide-react";
import {
  getTheme, createTheme, updateTheme, deleteTheme, importTheme,
  type ThemeDefinition, type ThemeSummary,
} from "../../api/restClient";
import type { ProjectConfig, UIPage, UIElement } from "../../api/types";
import { ConfirmDialog } from "../shared/ConfirmDialog";

// --- Constants ---

const HEX_RE = /^#[0-9a-f]{6}$/i;
const CHECKER_BG =
  "linear-gradient(45deg, #888 25%, transparent 25%, transparent 75%, #888 75%), " +
  "linear-gradient(45deg, #888 25%, transparent 25%, transparent 75%, #888 75%)";

interface ThemeTokenDef {
  key: string;
  label: string;
  type: "color" | "number" | "font";
  hint?: string;
}

// Foundational theme tokens — CSS variables consumed across many elements.
// Each token is the single canonical edit location for its concept; nothing
// in ELEMENT_CONTROLS duplicates these.
const THEME_TOKENS: ThemeTokenDef[] = [
  { key: "panel_bg", label: "Page Background", type: "color", hint: "Behind every page" },
  { key: "panel_text", label: "Text Color", type: "color", hint: "Default text on labels, sliders, gauges, lists, inputs" },
  { key: "surface", label: "Surface", type: "color", hint: "Slider tracks, dropdowns, text inputs, list rows, fader track, keypad display" },
  { key: "surface_border", label: "Surface Border", type: "color", hint: "Thin border around surfaces above" },
  { key: "accent", label: "Accent", type: "color", hint: "Slider fill, fader handle, focus, active button, page nav text" },
  { key: "danger", label: "Danger", type: "color", hint: "Red zone of level meters, mute indicators, error states" },
  { key: "success", label: "Success", type: "color", hint: "Green zone of level meters, OK states" },
  { key: "warning", label: "Warning", type: "color", hint: "Yellow zone of level meters, lock indicators, caution states" },
  { key: "border_radius", label: "Border Radius (px)", type: "number", hint: "Roundness of every element. 0 sharp, 16+ very round" },
  { key: "grid_gap", label: "Grid Gap (px)", type: "number", hint: "Space between elements on every page" },
  { key: "font_family", label: "Font Family", type: "font", hint: "Typeface across the entire panel" },
];

// Per-element style controls. Each control routes to either a CSS variable
// (kind: "var") or an element_defaults entry (kind: "default"). Combining
// both sources here lets us present one coherent section per element.
type ControlSource = { kind: "var"; key: string } | { kind: "default"; key: string };

interface ElementControl {
  label: string;
  type: "color" | "number" | "text";
  source: ControlSource;
  hint?: string;
}

// Per-element style controls. Each control routes to either a CSS variable
// (kind: "var") or an element_defaults entry (kind: "default"). The source
// is determined by where the value canonically lives so there's never a
// duplicate edit path.
//
// Button-derivative types (page_nav, camera_preset, keypad) inherit colors
// from button_* variables — they're not listed here because exposing the
// same controls under multiple sections would create the "edit one, change
// everywhere" confusion. Phase 5 (inheritance UI) will let designers
// override per-type from the studio; until then per-instance overrides via
// Properties panel are the way.

const STANDARD_BORDER_CONTROLS: ElementControl[] = [
  { label: "Border Color", type: "color", source: { kind: "default", key: "border_color" } },
  { label: "Border Width (px)", type: "number", source: { kind: "default", key: "border_width" } },
];

const ELEMENT_CONTROLS: Record<string, ElementControl[]> = {
  button: [
    // Button colors live as CSS variables so they cascade to page_nav,
    // camera_preset, keypad keys, matrix preset buttons — anything visually
    // a button picks them up automatically. Active button bg derives from
    // Accent (no separate token); active text uses Button Text.
    { label: "Background", type: "color", source: { kind: "var", key: "button_bg" }, hint: "Also applies to page nav, camera presets, keypads" },
    { label: "Text", type: "color", source: { kind: "var", key: "button_text" }, hint: "Also used as active button text color" },
    { label: "Border Color", type: "color", source: { kind: "var", key: "button_border" }, hint: "Also applies to page nav, camera presets, keypads" },
    { label: "Border Width (px)", type: "number", source: { kind: "default", key: "border_width" } },
    { label: "Box Shadow", type: "text", source: { kind: "default", key: "box_shadow" }, hint: "CSS syntax, e.g. '0 2px 4px rgba(0,0,0,0.3)' or 'none'" },
  ],
  label: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" }, hint: "Overrides Page Text just for labels" },
    ...STANDARD_BORDER_CONTROLS,
  ],
  slider: [
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Track uses Surface, fill uses Accent" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    { label: "Thumb Size (px)", type: "number", source: { kind: "default", key: "thumb_size" }, hint: "Larger = easier to grab on touch (44 is standard)" },
    ...STANDARD_BORDER_CONTROLS,
  ],
  fader: [
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Track uses Surface, handle uses Accent" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  status_led: [
    { label: "Active Color", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Color shown when bound state is true" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    { label: "Border Color", type: "color", source: { kind: "default", key: "border_color" } },
  ],
  gauge: [
    { label: "Gauge Color", type: "color", source: { kind: "default", key: "gauge_color" }, hint: "Filled portion" },
    { label: "Gauge Background", type: "color", source: { kind: "default", key: "gauge_bg_color" }, hint: "Empty portion" },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  level_meter: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "Zone colors come from Success / Warning / Danger" },
    ...STANDARD_BORDER_CONTROLS,
    { label: "Green Threshold (dB)", type: "number", source: { kind: "default", key: "green_to" }, hint: "Levels below are green; default −12" },
    { label: "Yellow Threshold (dB)", type: "number", source: { kind: "default", key: "yellow_to" }, hint: "Above is red; default −3" },
  ],
  list: [
    { label: "Item Background", type: "color", source: { kind: "default", key: "item_bg" } },
    { label: "Selected Item", type: "color", source: { kind: "default", key: "item_active_bg" }, hint: "Highlighted row" },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  matrix: [
    { label: "Active Route", type: "color", source: { kind: "default", key: "crosspoint_active_color" }, hint: "Crosspoint dot when route is connected" },
    { label: "Inactive Route", type: "color", source: { kind: "default", key: "crosspoint_inactive_color" } },
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  select: [
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "The dropdown itself uses Surface" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  text_input: [
    { label: "Wrapper Background", type: "color", source: { kind: "default", key: "bg_color" }, hint: "The input itself uses Surface" },
    { label: "Label Color", type: "color", source: { kind: "default", key: "text_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  image: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
  spacer: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
  ],
  group: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    { label: "Title Color", type: "color", source: { kind: "default", key: "text_color" }, hint: "Group label text" },
    ...STANDARD_BORDER_CONTROLS,
  ],
  clock: [
    { label: "Background", type: "color", source: { kind: "default", key: "bg_color" } },
    { label: "Text Color", type: "color", source: { kind: "default", key: "text_color" } },
    ...STANDARD_BORDER_CONTROLS,
  ],
};

// Order in which element sections render. Buttons first (most common), then
// other interactive elements, then displays, then layout/utility.
const ELEMENT_ORDER = [
  "button", "label", "slider", "fader", "select", "text_input",
  "status_led", "gauge", "level_meter",
  "list", "matrix", "group",
  "image", "clock", "spacer",
];

const ELEMENT_TYPE_LABELS: Record<string, string> = {
  button: "Button",
  label: "Label",
  slider: "Slider",
  page_nav: "Page Nav",
  select: "Select",
  text_input: "Text Input",
  status_led: "Status LED",
  image: "Image",
  spacer: "Spacer",
  camera_preset: "Camera Preset",
  gauge: "Gauge",
  level_meter: "Level Meter",
  fader: "Fader",
  group: "Group",
  clock: "Clock",
  keypad: "Keypad",
  list: "List",
  matrix: "Matrix",
};

// --- WCAG ---

function hexToRgb(hex: string): [number, number, number] | null {
  const m = hex.replace("#", "").match(/^([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (!m) return null;
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

function relativeLuminance(r: number, g: number, b: number): number {
  const [rs, gs, bs] = [r, g, b].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

function contrastRatio(hex1: string, hex2: string): number | null {
  const rgb1 = hexToRgb(hex1);
  const rgb2 = hexToRgb(hex2);
  if (!rgb1 || !rgb2) return null;
  const l1 = relativeLuminance(...rgb1);
  const l2 = relativeLuminance(...rgb2);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

type WcagLevel = "AAA" | "AA" | "fail";
function wcagLevel(ratio: number): WcagLevel {
  if (ratio >= 7) return "AAA";
  if (ratio >= 4.5) return "AA";
  return "fail";
}

// --- Color utilities for Quick Adjust ---

function adjustHex(hex: string, amount: number): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  const adjusted = rgb.map((c) => {
    if (amount > 0) return Math.round(c + (255 - c) * amount);
    return Math.round(c * (1 + amount));
  });
  return `#${adjusted.map((c) => Math.max(0, Math.min(255, c)).toString(16).padStart(2, "0")).join("")}`;
}

function deriveSurfaceBorder(surface: string): string {
  const rgb = hexToRgb(surface);
  if (!rgb) return surface;
  const lum = relativeLuminance(...rgb);
  return adjustHex(surface, lum < 0.5 ? 0.2 : -0.15);
}

// --- ColorPickerCell ---

interface ColorPickerCellProps {
  value: string;
  onChange: (next: string) => void;
}

function ColorPickerCell({ value, onChange }: ColorPickerCellProps) {
  const isHex = HEX_RE.test(value);
  const isTransparent = value === "transparent";

  // No "T" toggle button — it caused one-click data loss. To set transparent,
  // type "transparent" into the text field next to the picker.
  if (isHex) {
    return (
      <input
        type="color"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: 28,
          height: 22,
          padding: 0,
          border: "1px solid var(--border-color)",
          borderRadius: 3,
          cursor: "pointer",
          flexShrink: 0,
        }}
      />
    );
  }
  return (
    <button
      type="button"
      onClick={() => onChange("#000000")}
      title={
        isTransparent
          ? "Transparent — click to switch to a hex color"
          : `${value} — click to switch to a hex color`
      }
      style={{
        width: 28,
        height: 22,
        padding: 0,
        border: "1px solid var(--border-color)",
        borderRadius: 3,
        cursor: "pointer",
        background: isTransparent ? CHECKER_BG : value,
        backgroundSize: isTransparent ? "8px 8px" : undefined,
        backgroundPosition: isTransparent ? "0 0, 4px 4px" : undefined,
        flexShrink: 0,
      }}
    />
  );
}

// --- Element gallery (synthetic preview page showing every common element type) ---

const GALLERY_PAGE_ID = "_studio_gallery";

function buildGalleryPage(): UIPage {
  const elements: UIElement[] = [
    {
      id: "g_header",
      type: "label",
      text: "Theme Preview Gallery",
      grid_area: { col: 1, row: 1, col_span: 16, row_span: 1 },
      style: { font_size: 16, text_align: "center", font_weight: 700 },
      bindings: {},
    },
    {
      id: "g_button",
      type: "button",
      label: "Button",
      grid_area: { col: 1, row: 2, col_span: 3, row_span: 2 },
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
      grid_area: { col: 4, row: 2, col_span: 3, row_span: 2 },
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
      grid_area: { col: 7, row: 2, col_span: 3, row_span: 2 },
      style: {},
      bindings: {},
    },
    // Status LEDs — color bindings reference theme tokens so each LED
    // visibly demos its semantic color (success/warning/danger).
    {
      id: "g_led_ok",
      type: "status_led",
      label: "OK",
      grid_area: { col: 10, row: 2, col_span: 1, row_span: 2 },
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
      grid_area: { col: 11, row: 2, col_span: 1, row_span: 2 },
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
      grid_area: { col: 12, row: 2, col_span: 3, row_span: 2 },
      style: {},
      bindings: {},
    },
    {
      id: "g_camera",
      type: "camera_preset",
      label: "Preset",
      preset_number: 1,
      grid_area: { col: 15, row: 2, col_span: 2, row_span: 2 },
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
      grid_area: { col: 1, row: 4, col_span: 5, row_span: 2 },
      style: { show_value: true },
      bindings: { value: { key: "gallery.slider" } },
    },
    {
      id: "g_pagenav",
      type: "page_nav",
      label: "Pages",
      target_page: GALLERY_PAGE_ID,
      grid_area: { col: 6, row: 4, col_span: 4, row_span: 2 },
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
      grid_area: { col: 10, row: 4, col_span: 3, row_span: 2 },
      style: {},
      bindings: { value: { key: "gallery.select" } },
    },
    {
      id: "g_text",
      type: "text_input",
      placeholder: "Type here…",
      grid_area: { col: 13, row: 4, col_span: 4, row_span: 2 },
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
      grid_area: { col: 1, row: 6, col_span: 3, row_span: 6 },
      style: {},
      bindings: { value: { key: "gallery.gauge" } },
    },
    {
      id: "g_meter",
      type: "level_meter",
      min: -60,
      max: 0,
      orientation: "vertical",
      grid_area: { col: 4, row: 6, col_span: 1, row_span: 6 },
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
      grid_area: { col: 5, row: 6, col_span: 3, row_span: 6 },
      style: {},
      bindings: { value: { key: "gallery.fader" } },
    },
    {
      id: "g_keypad",
      type: "keypad",
      digits: 4,
      keypad_style: "numeric",
      show_display: true,
      grid_area: { col: 8, row: 6, col_span: 5, row_span: 6 },
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
      grid_area: { col: 13, row: 6, col_span: 4, row_span: 6 },
      style: {},
      bindings: { selected: { key: "gallery.list" } },
    },
    {
      id: "g_matrix",
      type: "matrix",
      matrix_config: {
        input_count: 3,
        output_count: 3,
        input_labels: ["Cam 1", "PC", "Doc"],
        output_labels: ["Main", "Conf", "Stream"],
        route_key_pattern: "gallery.route.*",
      },
      grid_area: { col: 1, row: 12, col_span: 8, row_span: 5 },
      style: { cell_size: 32 },
      bindings: {},
    },
  ];

  return {
    id: GALLERY_PAGE_ID,
    name: "Element Gallery",
    page_type: "page",
    grid: { columns: 16, rows: 16 },
    grid_gap: 8,
    elements,
  };
}

const GALLERY_DEMO_STATE: Record<string, unknown> = {
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

// --- Live preview iframe (scaled to fit the column at fixed panel dimensions) ---

interface StudioPreviewProps {
  project: ProjectConfig;
  pageId: string | null;
  inlineTheme: ThemeDefinition | null;
  demoState?: Record<string, unknown>;
  panelWidth: number;
  panelHeight: number;
}

function StudioPreview({
  project,
  pageId,
  inlineTheme,
  demoState,
  panelWidth,
  panelHeight,
}: StudioPreviewProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  const projectRef = useRef(project);
  const inlineThemeRef = useRef(inlineTheme);
  const demoStateRef = useRef(demoState);
  useEffect(() => { projectRef.current = project; }, [project]);
  useEffect(() => { inlineThemeRef.current = inlineTheme; }, [inlineTheme]);
  useEffect(() => { demoStateRef.current = demoState; }, [demoState]);

  useEffect(() => {
    const update = () => {
      const el = containerRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const padding = 24;
      const sx = (rect.width - padding) / panelWidth;
      const sy = (rect.height - padding) / panelHeight;
      setScale(Math.max(0.05, Math.min(sx, sy, 1)));
    };
    update();
    if (typeof ResizeObserver === "undefined" || !containerRef.current) return;
    const ro = new ResizeObserver(update);
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [panelWidth, panelHeight]);

  const handleLoad = () => {
    if (!pageId) return;
    iframeRef.current?.contentWindow?.postMessage(
      {
        type: "openavc:editor-init",
        project: projectRef.current,
        pageId,
        showGrid: false,
        demoState: demoStateRef.current,
        inlineTheme: inlineThemeRef.current,
      },
      "*",
    );
  };

  useEffect(() => {
    if (!pageId) return;
    const timer = setTimeout(() => {
      iframeRef.current?.contentWindow?.postMessage(
        {
          type: "openavc:editor-project",
          project,
          pageId,
          showGrid: false,
          demoState,
          inlineTheme,
        },
        "*",
      );
    }, 40);
    return () => clearTimeout(timer);
  }, [project, pageId, demoState, inlineTheme]);

  return (
    <div
      ref={containerRef}
      style={{
        height: "100%",
        width: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
        background: "var(--bg-base)",
      }}
    >
      {pageId ? (
        <div
          style={{
            width: panelWidth,
            height: panelHeight,
            transform: `scale(${scale})`,
            transformOrigin: "center center",
            flexShrink: 0,
            borderRadius: 8,
            overflow: "hidden",
            boxShadow: "0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.08)",
          }}
        >
          <iframe
            key={pageId}
            ref={iframeRef}
            src={`/panel?page=${encodeURIComponent(pageId)}&edit=1`}
            onLoad={handleLoad}
            title="Theme preview"
            style={{
              width: panelWidth,
              height: panelHeight,
              border: "none",
              background: "var(--bg-base)",
              display: "block",
            }}
          />
        </div>
      ) : (
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>No page to preview.</div>
      )}
    </div>
  );
}

// --- Main component ---

export interface ThemeStudioProps {
  open: boolean;
  onClose: () => void;
  themes: ThemeSummary[];
  project: ProjectConfig;
  currentThemeId: string;
  themeOverrides: Record<string, unknown>;
  onChangeTheme: (themeId: string) => void;
  /** Called to clear project-level theme_overrides after save (they're now baked in). */
  onClearOverrides: () => void;
  onRefreshThemes: () => void;
  /** Called after Save Changes so the canvas iframe re-fetches the updated theme. */
  onThemeSaved?: () => void;
  onResetElementStyles?: () => void;
  panelWidth?: number;
  panelHeight?: number;
}

export function ThemeStudio({
  open,
  onClose,
  themes,
  project,
  currentThemeId,
  themeOverrides,
  onChangeTheme,
  onClearOverrides,
  onRefreshThemes,
  onThemeSaved,
  onResetElementStyles,
  panelWidth = 1280,
  panelHeight = 800,
}: ThemeStudioProps) {
  // Working copy of the theme being edited. All mutations go here. The
  // preview iframe consumes this directly via postMessage as inlineTheme.
  // The "saved" copy is kept so we can detect dirty state and discard.
  const [working, setWorking] = useState<ThemeDefinition | null>(null);
  const [savedJson, setSavedJson] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ kind: "info" | "error"; text: string } | null>(null);
  const [pendingThemeSwitch, setPendingThemeSwitch] = useState<string | null>(null);
  const [pendingClose, setPendingClose] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [previewView, setPreviewView] = useState<string>("gallery");
  const [focusedElement, setFocusedElement] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const galleryPage = useMemo(() => buildGalleryPage(), []);
  const savedTheme = useMemo<ThemeDefinition | null>(() => {
    if (!savedJson) return null;
    try { return JSON.parse(savedJson) as ThemeDefinition; }
    catch { return null; }
  }, [savedJson]);

  // Load the theme PRISTINE into the working copy. Project-level overrides
  // are a separate layer surfaced by the override banner — merging them in
  // here would make every theme look like it has the override-applied values
  // and silently mislead the user. The studio edits the THEME; overrides are
  // managed via the banner's "Clear overrides" button.
  useEffect(() => {
    if (!open || !currentThemeId) return;
    let cancelled = false;
    setStatusMsg(null);
    getTheme(currentThemeId)
      .then((t) => {
        if (cancelled) return;
        const cloned: ThemeDefinition = {
          ...t,
          variables: { ...(t.variables || {}) },
          element_defaults: JSON.parse(JSON.stringify(t.element_defaults || {})),
        };
        setWorking(cloned);
        setSavedJson(JSON.stringify(cloned));
      })
      .catch(() => {
        if (!cancelled) setStatusMsg({ kind: "error", text: `Failed to load "${currentThemeId}"` });
      });
    return () => { cancelled = true; };
  }, [open, currentThemeId]);

  useEffect(() => {
    if (open) setPreviewView("gallery");
  }, [open]);

  const isCustom = working?._source === "custom";
  const isDirty = working ? JSON.stringify(working) !== savedJson : false;

  // Edit a CSS variable on the working copy
  const setVar = (key: string, value: unknown) => {
    setWorking((prev) => {
      if (!prev) return prev;
      const newVars = { ...(prev.variables || {}) };
      if (value === undefined || value === "") {
        delete newVars[key];
      } else {
        newVars[key] = value;
      }
      // border_radius exists in both variables and element_defaults — if only
      // the variable updates, element_defaults inline styles override it and
      // the change appears to do nothing. Keep them in sync.
      if (key === "border_radius") {
        const defs = JSON.parse(JSON.stringify(prev.element_defaults || {}));
        for (const elType of Object.keys(defs)) {
          if (defs[elType]?.border_radius !== undefined) {
            defs[elType].border_radius = value;
          }
        }
        return { ...prev, variables: newVars, element_defaults: defs };
      }
      return { ...prev, variables: newVars };
    });
  };

  const setElementDefault = (elType: string, key: string, value: unknown) => {
    setWorking((prev) => {
      if (!prev) return prev;
      const defaults = { ...(prev.element_defaults || {}) };
      defaults[elType] = { ...(defaults[elType] || {}), [key]: value };
      return { ...prev, element_defaults: defaults };
    });
  };

  const applySurfaceStyle = (style: "flat" | "layered" | "outlined") => {
    setWorking((prev) => {
      if (!prev) return prev;
      const vars = { ...(prev.variables || {}) };
      const defaults = JSON.parse(JSON.stringify(prev.element_defaults || {}));
      const borderEls = [
        "button", "label", "slider", "fader", "select", "text_input",
        "gauge", "level_meter", "list", "matrix", "group", "image", "clock",
      ];
      const shadowEls = ["button", "slider", "camera_preset", "page_nav"];
      if (style === "flat") {
        for (const el of borderEls) {
          if (!defaults[el]) defaults[el] = {};
          defaults[el].border_width = 0;
        }
        for (const el of shadowEls) {
          if (!defaults[el]) defaults[el] = {};
          defaults[el].box_shadow = "none";
        }
        if (vars.surface) vars.surface_border = String(vars.surface);
      } else if (style === "layered") {
        for (const el of borderEls) {
          if (!defaults[el]) defaults[el] = {};
          defaults[el].border_width = 1;
        }
        if (!defaults.button) defaults.button = {};
        defaults.button.box_shadow = "0 2px 4px rgba(0,0,0,0.3)";
        if (!defaults.slider) defaults.slider = {};
        defaults.slider.box_shadow = "inset 0 1px 3px rgba(0,0,0,0.3)";
        if (defaults.camera_preset) defaults.camera_preset.box_shadow = "0 2px 4px rgba(0,0,0,0.3)";
        if (defaults.page_nav) defaults.page_nav.box_shadow = "0 1px 3px rgba(0,0,0,0.2)";
        if (vars.surface && String(vars.surface) === String(vars.surface_border)) {
          vars.surface_border = deriveSurfaceBorder(String(vars.surface));
        }
      } else {
        for (const el of borderEls) {
          if (!defaults[el]) defaults[el] = {};
          defaults[el].border_width = 1;
        }
        for (const el of shadowEls) {
          if (!defaults[el]) defaults[el] = {};
          defaults[el].box_shadow = "none";
        }
        if (vars.surface && String(vars.surface) === String(vars.surface_border)) {
          vars.surface_border = deriveSurfaceBorder(String(vars.surface));
        }
      }
      return { ...prev, variables: vars, element_defaults: defaults };
    });
  };

  const setName = (name: string) => setWorking((prev) => (prev ? { ...prev, name } : prev));
  const setDesc = (description: string) => setWorking((prev) => (prev ? { ...prev, description } : prev));

  // Auto-clear info status; keep errors
  useEffect(() => {
    if (!statusMsg || statusMsg.kind === "error") return;
    const t = setTimeout(() => setStatusMsg(null), 2500);
    return () => clearTimeout(t);
  }, [statusMsg]);

  // Build the previewProject and inlineTheme that drive the iframe
  const isGallery = previewView === "gallery";
  const previewProject = useMemo<ProjectConfig>(() => {
    if (!working) return project;
    // Settings carry the working theme's id (so applyTheme matches inlineTheme)
    // AND blank out the project-level overrides so the studio's working theme
    // is what's actually rendered. Without these blanks, panel.js's
    // "per-setting overrides take priority" path would clobber the user's
    // accent / font_family edits with the saved project values.
    const settings = {
      ...project.ui.settings,
      theme_id: working.id,
      theme_overrides: {},
      accent_color: "",
      font_family: "",
    };
    if (isGallery) {
      return { ...project, ui: { ...project.ui, pages: [galleryPage], settings } };
    }
    return { ...project, ui: { ...project.ui, settings } };
  }, [working, project, isGallery, galleryPage]);
  const previewPageId = isGallery ? GALLERY_PAGE_ID : previewView;
  const previewDemoState = isGallery ? GALLERY_DEMO_STATE : undefined;

  const slugify = (s: string) =>
    s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  const refreshAndReload = async (themeId: string) => {
    onRefreshThemes();
    const reloaded = await getTheme(themeId);
    const merged: ThemeDefinition = {
      ...reloaded,
      variables: { ...(reloaded.variables || {}) },
      element_defaults: JSON.parse(JSON.stringify(reloaded.element_defaults || {})),
    };
    setWorking(merged);
    setSavedJson(JSON.stringify(merged));
  };

  // --- Save Changes (custom only) --- returns true on success
  const handleSaveChanges = async (): Promise<boolean> => {
    if (!working || !isCustom) return false;
    setBusy(true);
    setStatusMsg(null);
    try {
      const payload: ThemeDefinition = {
        ...working,
        preview_colors: derivePreviewColors(working.variables),
      };
      delete (payload as { _source?: string })._source;
      await updateTheme(working.id, payload);
      if (Object.keys(themeOverrides).length > 0) onClearOverrides();
      await refreshAndReload(working.id);
      onThemeSaved?.();
      setStatusMsg({ kind: "info", text: `Saved "${working.name}"` });
      setBusy(false);
      return true;
    } catch (e) {
      setStatusMsg({ kind: "error", text: e instanceof Error ? e.message : "Save failed" });
      setBusy(false);
      return false;
    }
  };

  // --- Save as Custom ---
  // Auto-renames + auto-disambiguates the id so we can never collide with a
  // built-in or an existing custom (which would 409 the POST).
  const handleSaveAsCustom = async (): Promise<boolean> => {
    if (!working) return false;
    let baseName = working.name.trim();
    if (!baseName) {
      setStatusMsg({ kind: "error", text: "Enter a theme name" });
      return false;
    }
    // Visually distinguish the new theme from its source so the picker
    // doesn't end up with two cards labeled the same.
    const suffix = isCustom ? " (Copy)" : " (Custom)";
    const alreadySuffixed = / \((Copy|Custom)( \d+)?\)$/.test(baseName);
    if (!alreadySuffixed) baseName = `${baseName}${suffix}`;
    let baseSlug = slugify(baseName);
    if (!baseSlug) baseSlug = "custom-theme";
    // Find an unused id (and matching unused name) by appending -2, -3…
    const existing = new Set(themes.map((t) => t.id));
    let id = baseSlug;
    let name = baseName;
    let n = 2;
    while (existing.has(id)) {
      id = `${baseSlug}-${n}`;
      name = `${baseName} ${n}`;
      n++;
    }
    setBusy(true);
    setStatusMsg(null);
    try {
      const payload: ThemeDefinition = {
        ...working,
        id,
        name,
        author: "Custom",
        preview_colors: derivePreviewColors(working.variables),
      };
      delete (payload as { _source?: string })._source;
      await createTheme(payload);
      if (Object.keys(themeOverrides).length > 0) onClearOverrides();
      onChangeTheme(id);
      onRefreshThemes();
      setStatusMsg({ kind: "info", text: `Saved as "${name}"` });
      setBusy(false);
      return true;
    } catch (e) {
      setStatusMsg({ kind: "error", text: e instanceof Error ? e.message : "Save failed" });
      setBusy(false);
      return false;
    }
  };

  // --- Discard ---
  const handleDiscard = () => {
    if (!savedJson) return;
    setWorking(JSON.parse(savedJson));
    setStatusMsg({ kind: "info", text: "Reverted to saved values" });
  };

  // --- Delete (custom only) ---
  const handleDeleteTheme = async () => {
    if (!working || !isCustom) return;
    setConfirmDelete(false);
    setBusy(true);
    try {
      await deleteTheme(working.id);
      if (Object.keys(themeOverrides).length > 0) onClearOverrides();
      onChangeTheme("dark-default");
      onRefreshThemes();
      setStatusMsg({ kind: "info", text: `Deleted "${working.name}"` });
    } catch (e) {
      setStatusMsg({ kind: "error", text: e instanceof Error ? e.message : "Delete failed" });
    }
    setBusy(false);
  };

  // --- Duplicate (from picker hover button) ---
  const handleDuplicate = async (sourceId: string, sourceName: string) => {
    setBusy(true);
    setStatusMsg(null);
    try {
      const source = await getTheme(sourceId);
      const baseSlug = sourceId.replace(/-copy(-\d+)?$/, "");
      const existing = new Set(themes.map((t) => t.id));
      let newId = `${baseSlug}-copy`;
      let n = 2;
      while (existing.has(newId)) {
        newId = `${baseSlug}-copy-${n++}`;
      }
      const baseName = sourceName.replace(/\s*\(Copy(?:\s+\d+)?\)\s*$/, "");
      const copyName = n === 2 ? `${baseName} (Copy)` : `${baseName} (Copy ${n - 1})`;
      const payload: ThemeDefinition = { ...source, id: newId, name: copyName, author: "Custom" };
      delete (payload as { _source?: string })._source;
      await createTheme(payload);
      onChangeTheme(newId);
      onRefreshThemes();
      setStatusMsg({ kind: "info", text: `Duplicated as "${copyName}"` });
    } catch (e) {
      setStatusMsg({ kind: "error", text: e instanceof Error ? e.message : "Duplicate failed" });
    }
    setBusy(false);
  };

  // --- Export / Import ---
  const handleExport = () => {
    if (!working) return;
    const exportData: ThemeDefinition = {
      ...working,
      preview_colors: derivePreviewColors(working.variables),
    };
    delete (exportData as { _source?: string })._source;
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${working.id}.avctheme`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setStatusMsg(null);
    try {
      const result = await importTheme(file);
      onChangeTheme(result.id);
      onRefreshThemes();
      setStatusMsg({ kind: "info", text: `Imported "${result.name}"` });
    } catch (err) {
      setStatusMsg({ kind: "error", text: err instanceof Error ? err.message : "Import failed" });
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
    setBusy(false);
  };

  // --- Theme switch (confirm if dirty) ---
  const handlePickTheme = (id: string) => {
    if (id === currentThemeId) return;
    if (isDirty) {
      setPendingThemeSwitch(id);
      return;
    }
    onChangeTheme(id);
  };

  const handleConfirmThemeSwitch = () => {
    if (!pendingThemeSwitch) return;
    onChangeTheme(pendingThemeSwitch);
    setPendingThemeSwitch(null);
  };

  // --- Close (confirm if dirty) ---
  const handleClose = () => {
    if (isDirty) {
      setPendingClose(true);
      return;
    }
    onClose();
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  // handleClose closes over isDirty; safe to capture latest each render
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isDirty]);

  // Listen for element clicks from the preview iframe
  useEffect(() => {
    if (!open) return;
    const onMsg = (e: MessageEvent) => {
      if (e.data?.type === "openavc:theme-element-click") {
        const elType = e.data.elementType as string;
        if (elType && ELEMENT_CONTROLS[elType]) {
          setFocusedElement(elType);
        } else {
          // Derivative types (page_nav, camera_preset, keypad) map to button
          const buttonDerivatives = ["page_nav", "camera_preset", "keypad"];
          if (elType && buttonDerivatives.includes(elType)) {
            setFocusedElement("button");
          }
        }
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [open]);

  // Contrast checks on working copy variables
  const contrastChecks = useMemo(() => {
    const v = (working?.variables || {}) as Record<string, string>;
    const checks: { label: string; fg: string; bg: string; ratio: number | null; level: WcagLevel }[] = [];
    const pairs: [string, string, string][] = [
      ["Text on Background", "panel_text", "panel_bg"],
      ["Button Text on Button", "button_text", "button_bg"],
      ["Accent on Background", "accent", "panel_bg"],
      ["Button Text on Active", "button_text", "accent"],
      ["Danger on Background", "danger", "panel_bg"],
      ["Success on Background", "success", "panel_bg"],
      ["Warning on Background", "warning", "panel_bg"],
    ];
    for (const [label, fgKey, bgKey] of pairs) {
      const fg = v[fgKey];
      const bg = v[bgKey];
      if (fg && bg) {
        const ratio = contrastRatio(fg, bg);
        checks.push({ label, fg, bg, ratio, level: ratio ? wcagLevel(ratio) : "fail" });
      }
    }
    return checks;
  }, [working]);

  const failingContrasts = contrastChecks.filter((c) => c.level === "fail");

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Theme Studio"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.75)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={handleClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "92vw",
          height: "92vh",
          maxWidth: 1800,
          background: "var(--bg-base)",
          border: "1px solid var(--border-color)",
          borderRadius: 8,
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 24px 64px rgba(0,0,0,0.5)",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 16px",
            borderBottom: "1px solid var(--border-color)",
            background: "var(--bg-surface)",
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
            Theme Studio
          </div>
          {working && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              Editing <strong style={{ color: "var(--text-primary)" }}>{working.name}</strong>
              <span style={{ marginLeft: 6, color: "var(--text-muted)" }}>
                ({isCustom ? "custom" : "built-in"})
              </span>
              {isDirty && (
                <span
                  title="Unsaved changes"
                  style={{
                    marginLeft: 8,
                    padding: "1px 8px",
                    background: "rgba(255,167,38,0.18)",
                    color: "#ffa726",
                    borderRadius: 10,
                    fontSize: 11,
                    fontWeight: 600,
                  }}
                >
                  Unsaved
                </span>
              )}
            </div>
          )}
          <div style={{ flex: 1 }} />
          <select
            value={previewView}
            onChange={(e) => setPreviewView(e.target.value)}
            title="What the preview pane shows"
            style={{
              padding: "4px 8px",
              background: "var(--bg-surface)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-color)",
              borderRadius: 4,
              fontSize: 12,
            }}
          >
            <option value="gallery">Preview: Element Gallery</option>
            {project.ui.pages.map((p) => (
              <option key={p.id} value={p.id}>
                Preview: {p.name || p.id}
              </option>
            ))}
          </select>
          {onResetElementStyles && (
            <button
              onClick={onResetElementStyles}
              title="Remove per-element style overrides so all elements inherit from the theme"
              style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "4px 10px", borderRadius: 4, fontSize: 11,
                background: "transparent", border: "1px solid var(--border-color)",
                color: "var(--text-secondary)", cursor: "pointer",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            >
              Reset Element Styles
            </button>
          )}
          <button
            onClick={handleClose}
            title="Close (Esc)"
            style={{
              width: 28,
              height: 28,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "transparent",
              border: "1px solid var(--border-color)",
              borderRadius: 4,
              cursor: "pointer",
              color: "var(--text-secondary)",
            }}
          >
            <X size={14} />
          </button>
        </div>

        <div style={{ flex: 1, overflow: "hidden" }}>
          <PanelGroup direction="horizontal" autoSaveId="themeStudio">
            <Panel defaultSize={18} minSize={14} maxSize={28}>
              <ThemePickerColumn
                themes={themes}
                currentThemeId={currentThemeId}
                busy={busy}
                isDirty={isDirty}
                onPick={handlePickTheme}
                onDuplicate={handleDuplicate}
                onImport={() => fileInputRef.current?.click()}
              />
            </Panel>
            <PanelResizeHandle
              style={{ width: 4, background: "var(--border-color)", cursor: "col-resize" }}
            />
            <Panel defaultSize={36} minSize={26}>
              <EditorColumn
                working={working}
                isCustom={isCustom}
                isDirty={isDirty}
                busy={busy}
                statusMsg={statusMsg}
                contrastChecks={contrastChecks}
                failingContrasts={failingContrasts}
                onSetName={setName}
                onSetDesc={setDesc}
                onSetVar={setVar}
                onSetElementDefault={setElementDefault}
                onApplySurfaceStyle={applySurfaceStyle}
                savedVars={savedTheme?.variables || {}}
                savedDefaults={savedTheme?.element_defaults || {}}
                focusedElement={focusedElement}
                onClearFocus={() => setFocusedElement(null)}
                onSaveChanges={handleSaveChanges}
                onSaveAsCustom={handleSaveAsCustom}
                onDiscard={handleDiscard}
                onExport={handleExport}
                onDelete={() => setConfirmDelete(true)}
                themeOverrides={themeOverrides}
                onClearOverrides={onClearOverrides}
              />
            </Panel>
            <PanelResizeHandle
              style={{ width: 4, background: "var(--border-color)", cursor: "col-resize" }}
            />
            <Panel defaultSize={46} minSize={28}>
              <div
                style={{
                  height: "100%",
                  display: "flex",
                  flexDirection: "column",
                  background: "var(--bg-surface)",
                }}
              >
                <div
                  style={{
                    padding: "6px 10px",
                    fontSize: 10,
                    color: "var(--text-muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    borderBottom: "1px solid var(--border-color)",
                    background: "var(--bg-base)",
                  }}
                >
                  {isGallery
                    ? "Live preview — element gallery (one of every type)"
                    : `Live preview — ${(project.ui.pages.find((p) => p.id === previewView)?.name) || previewView}`}
                </div>
                <div style={{ flex: 1, position: "relative" }}>
                  <StudioPreview
                    project={previewProject}
                    pageId={previewPageId}
                    inlineTheme={working}
                    demoState={previewDemoState}
                    panelWidth={panelWidth}
                    panelHeight={panelHeight}
                  />
                </div>
              </div>
            </Panel>
          </PanelGroup>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept=".avctheme,.json"
          style={{ display: "none" }}
          onChange={handleImport}
        />

        {pendingThemeSwitch && (
          <ConfirmDialog
            title="Discard unsaved changes?"
            confirmLabel="Discard and switch"
            cancelLabel="Keep editing"
            onCancel={() => setPendingThemeSwitch(null)}
            onConfirm={handleConfirmThemeSwitch}
            message={
              <p>
                You have unsaved changes to <strong>"{working?.name}"</strong>. Switch theme anyway?
                The edits will be lost.
              </p>
            }
          />
        )}

        {pendingClose && (
          <ConfirmDialog
            title="Save changes before closing?"
            confirmLabel={isCustom ? "Save Changes" : "Save as Custom"}
            cancelLabel="Discard"
            onConfirm={async () => {
              setPendingClose(false);
              const ok = isCustom
                ? await handleSaveChanges()
                : await handleSaveAsCustom();
              if (ok) onClose();
              // If save failed, status message is already shown. Studio stays
              // open so the user can retry or discard.
            }}
            onCancel={() => {
              setPendingClose(false);
              onClose();
            }}
            message={
              <>
                <p style={{ marginBottom: 8 }}>
                  You have unsaved edits to <strong>"{working?.name}"</strong>.
                </p>
                <p style={{ color: "var(--text-muted)", fontSize: 12 }}>
                  {isCustom
                    ? "Save Changes overwrites this theme. Discard reloads it."
                    : "Built-in themes can't be modified. Save as Custom creates an editable copy. Discard loses your edits."}
                </p>
              </>
            }
          />
        )}

        {confirmDelete && working && (
          <ConfirmDialog
            title="Delete custom theme?"
            confirmLabel="Delete"
            cancelLabel="Cancel"
            onCancel={() => setConfirmDelete(false)}
            onConfirm={handleDeleteTheme}
            message={
              <>
                <p style={{ marginBottom: 8 }}>
                  Delete <strong>"{working.name}"</strong>? This permanently removes the theme file
                  from this project's <code>themes/</code> directory.
                </p>
                <p style={{ color: "var(--text-muted)", fontSize: 12 }}>
                  Pages still set to this theme will fall back to <strong>dark-default</strong>.
                </p>
              </>
            }
          />
        )}
      </div>
    </div>
  );
}

function derivePreviewColors(vars: Record<string, unknown> | undefined): string[] {
  const v = vars || {};
  return [v.panel_bg, v.surface || v.button_bg, v.accent, v.panel_text].filter(Boolean) as string[];
}

// --- Picker column ---

/**
 * Mini panel mockup rendered with the theme's actual variables. Shows the
 * theme's font, button colors, surface, accent, and page bg in one glance —
 * so users pick by feel, not by trying to imagine 4 abstract color dots
 * stitched into a real interface.
 */
function ThemeCardPreview({ theme }: { theme: ThemeSummary }) {
  const v = (theme.variables || {}) as Record<string, string | number | undefined>;
  const bg = String(v.panel_bg ?? "#1a1a2e");
  const text = String(v.panel_text ?? "#ffffff");
  const accent = String(v.accent ?? "#2196F3");
  const buttonBg = String(v.button_bg ?? "#424242");
  const buttonText = String(v.button_text ?? "#cccccc");
  const buttonBorder = String(v.button_border ?? "#555555");
  const surface = String(v.surface ?? "#2a2a4a");
  const surfaceBorder = String(v.surface_border ?? "#3a3a5c");
  const fontFamily = String(v.font_family ?? "Inter, system-ui, sans-serif");
  const radius = Number(v.border_radius ?? 8);
  // Mini elements use a smaller corner — heavy radius on tiny shapes looks bulbous.
  const miniRadius = Math.min(radius, 6);

  return (
    <div
      style={{
        background: bg,
        padding: "10px 12px",
        fontFamily,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ color: text, fontSize: 11, fontWeight: 600, letterSpacing: 0.2 }}>
        Sample Panel
      </div>
      <div style={{ display: "flex", gap: 4 }}>
        <div
          style={{
            background: buttonBg,
            color: buttonText,
            border: `1px solid ${buttonBorder}`,
            borderRadius: miniRadius,
            padding: "3px 8px",
            fontSize: 10,
            flex: 1,
            textAlign: "center",
            fontFamily,
          }}
        >
          Button
        </div>
        <div
          style={{
            background: accent,
            color: buttonText,
            borderRadius: miniRadius,
            padding: "3px 8px",
            fontSize: 10,
            flex: 1,
            textAlign: "center",
            fontFamily,
            fontWeight: 600,
          }}
        >
          Active
        </div>
      </div>
      <div
        style={{
          background: surface,
          border: `1px solid ${surfaceBorder}`,
          borderRadius: 3,
          height: 5,
          position: "relative",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            background: accent,
            width: "62%",
            height: "100%",
            opacity: 0.85,
          }}
        />
      </div>
    </div>
  );
}

interface ThemePickerColumnProps {
  themes: ThemeSummary[];
  currentThemeId: string;
  busy: boolean;
  isDirty: boolean;
  onPick: (id: string) => void;
  onDuplicate: (id: string, name: string) => void;
  onImport: () => void;
}

function ThemePickerColumn({
  themes,
  currentThemeId,
  busy,
  isDirty,
  onPick,
  onDuplicate,
  onImport,
}: ThemePickerColumnProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const builtins = themes.filter((t) => t.source === "builtin");
  const customs = themes.filter((t) => t.source !== "builtin");

  const renderCard = (t: ThemeSummary) => {
    const isSelected = t.id === currentThemeId;
    const isHovered = hoveredId === t.id;
    const showDirty = isSelected && isDirty;
    return (
      <div
        key={t.id}
        onClick={() => onPick(t.id)}
        onMouseEnter={() => setHoveredId(t.id)}
        onMouseLeave={() => setHoveredId((id) => (id === t.id ? null : id))}
        style={{
          position: "relative",
          borderRadius: 6,
          border: isSelected ? "2px solid var(--accent)" : "1px solid var(--border-color)",
          background: "var(--bg-surface)",
          cursor: "pointer",
          marginBottom: 8,
          overflow: "hidden",
          transition: "transform 0.12s, box-shadow 0.12s",
          transform: isHovered && !isSelected ? "translateY(-1px)" : "none",
          boxShadow: isHovered && !isSelected ? "0 4px 12px rgba(0,0,0,0.18)" : "none",
        }}
      >
        <ThemeCardPreview theme={t} />
        <div
          style={{
            padding: "6px 10px 8px",
            background: isSelected ? "var(--accent-dim, rgba(33,150,243,0.12))" : "var(--bg-surface)",
            borderTop: "1px solid var(--border-color)",
          }}
        >
          <div
            style={{
              fontSize: 12,
              fontWeight: isSelected ? 700 : 600,
              color: "var(--text-primary)",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            {t.name}
            {showDirty && (
              <span
                title="Unsaved changes"
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: "#ffa726",
                  display: "inline-block",
                  flexShrink: 0,
                }}
              />
            )}
          </div>
          {t.description && (
            <div
              style={{
                fontSize: 10,
                color: "var(--text-muted)",
                marginTop: 2,
                lineHeight: 1.35,
                overflow: "hidden",
                textOverflow: "ellipsis",
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
              }}
            >
              {t.description}
            </div>
          )}
        </div>
        {isHovered && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDuplicate(t.id, t.name);
            }}
            disabled={busy}
            title={`Duplicate "${t.name}" as a custom theme`}
            style={{
              position: "absolute",
              top: 6,
              right: 6,
              width: 24,
              height: 24,
              padding: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "rgba(0,0,0,0.6)",
              border: "1px solid rgba(255,255,255,0.3)",
              borderRadius: 4,
              cursor: busy ? "wait" : "pointer",
              color: "#fff",
              backdropFilter: "blur(4px)",
            }}
          >
            <Copy size={13} />
          </button>
        )}
      </div>
    );
  };

  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        borderRight: "1px solid var(--border-color)",
        background: "var(--bg-surface)",
      }}
    >
      <div
        style={{
          padding: "10px 12px",
          fontSize: 10,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          borderBottom: "1px solid var(--border-color)",
        }}
      >
        Themes
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: 8 }}>
        {builtins.length > 0 && (
          <>
            <div
              style={{
                fontSize: 10,
                color: "var(--text-muted)",
                fontWeight: 600,
                textTransform: "uppercase",
                padding: "4px 4px",
              }}
            >
              Built-in
            </div>
            {builtins.map(renderCard)}
          </>
        )}
        {customs.length > 0 && (
          <>
            <div
              style={{
                fontSize: 10,
                color: "var(--text-muted)",
                fontWeight: 600,
                textTransform: "uppercase",
                padding: "8px 4px 4px",
              }}
            >
              Custom
            </div>
            {customs.map(renderCard)}
          </>
        )}
        {customs.length === 0 && builtins.length > 0 && (
          <div
            style={{
              padding: "8px",
              fontSize: 11,
              color: "var(--text-muted)",
              textAlign: "center",
              fontStyle: "italic",
            }}
          >
            No custom themes yet. Hover any theme above and click <Copy size={10} /> to duplicate it.
          </div>
        )}
      </div>
      <div
        style={{
          padding: 8,
          borderTop: "1px solid var(--border-color)",
        }}
      >
        <button
          onClick={onImport}
          style={{
            width: "100%",
            padding: "6px 10px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            borderRadius: 4,
            cursor: "pointer",
            fontSize: 12,
            color: "var(--text-secondary)",
          }}
        >
          <Upload size={12} /> Import .avctheme
        </button>
      </div>
    </div>
  );
}

// --- Quick Adjust components ---

function SegmentedControl({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: { value: string; label: string }[];
  value: string | null;
  onChange: (value: string) => void;
  ariaLabel?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      style={{
        display: "inline-flex",
        borderRadius: 6,
        overflow: "hidden",
        border: "1px solid var(--border-color)",
      }}
    >
      {options.map((opt, i) => {
        const selected = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(opt.value)}
            style={{
              padding: "5px 14px",
              fontSize: 11,
              fontWeight: selected ? 600 : 400,
              background: selected ? "var(--accent)" : "var(--bg-hover)",
              color: selected ? "#fff" : "var(--text-secondary)",
              border: "none",
              borderRight: i < options.length - 1 ? "1px solid var(--border-color)" : "none",
              cursor: "pointer",
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

interface QuickAdjustProps {
  vars: Record<string, unknown>;
  defaults: Record<string, Record<string, unknown>>;
  savedVars: Record<string, unknown>;
  savedDefaults: Record<string, Record<string, unknown>>;
  onSetVar: (key: string, value: unknown) => void;
  onApplySurfaceStyle: (style: "flat" | "layered" | "outlined") => void;
}

function QuickAdjustSection({ vars, defaults, savedVars, savedDefaults, onSetVar, onApplySurfaceStyle }: QuickAdjustProps) {
  const accent = String(vars.accent ?? "#2196F3");
  const borderRadius = Number(vars.border_radius ?? 8);
  const fontFamily = String(vars.font_family ?? "Inter, system-ui, sans-serif");

  const roundnessPreset =
    borderRadius === 0 ? "sharp" : borderRadius === 8 ? "standard" : borderRadius === 16 ? "round" : null;

  const buttonShadow = String(defaults.button?.box_shadow ?? "");
  const buttonBorderWidth = Number(defaults.button?.border_width ?? 1);
  const hasShadow = buttonShadow !== "" && buttonShadow !== "none";
  const surfaceStyle: string | null =
    !hasShadow && buttonBorderWidth === 0 ? "flat" : hasShadow ? "layered" : "outlined";

  const typographyPreset =
    fontFamily.includes("Inter") || fontFamily.startsWith("system-ui") ? "sans"
    : fontFamily.includes("Georgia") || (fontFamily.includes("serif") && !fontFamily.includes("sans-serif")) ? "serif"
    : fontFamily === "monospace" || fontFamily.includes("Mono") || fontFamily.includes("Fira") ? "mono"
    : null;

  // Detect which controls have been modified from the saved theme
  const accentModified = String(vars.accent ?? "") !== String(savedVars.accent ?? "");
  const roundnessModified = String(vars.border_radius ?? "") !== String(savedVars.border_radius ?? "");
  const fontModified = String(vars.font_family ?? "") !== String(savedVars.font_family ?? "");
  const savedButtonShadow = String(savedDefaults.button?.box_shadow ?? "");
  const savedButtonBorderWidth = Number(savedDefaults.button?.border_width ?? 1);
  const savedHasShadow = savedButtonShadow !== "" && savedButtonShadow !== "none";
  const savedSurfaceStyle =
    !savedHasShadow && savedButtonBorderWidth === 0 ? "flat" : savedHasShadow ? "layered" : "outlined";
  const surfaceModified = surfaceStyle !== savedSurfaceStyle;

  const modifiedPill = (
    <span style={{ fontSize: 9, color: "var(--accent)", fontWeight: 600, marginLeft: 6 }}>
      modified
    </span>
  );

  const labelStyle: React.CSSProperties = {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-primary)",
    marginBottom: 2,
  };
  const hintStyle: React.CSSProperties = {
    fontSize: 10,
    color: "var(--text-muted)",
    lineHeight: 1.3,
    marginBottom: 4,
  };

  return (
    <div
      style={{
        background: "var(--bg-surface)",
        borderRadius: 6,
        border: "1px solid var(--border-color)",
        marginBottom: 10,
      }}
    >
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          color: "var(--text-secondary)",
          padding: "8px 10px",
          borderBottom: "1px solid var(--border-color)",
        }}
      >
        Quick Adjust
      </div>
      <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 14 }}>
        {/* Brand Accent */}
        <div>
          <div style={labelStyle}>Brand Accent{accentModified && modifiedPill}</div>
          <div style={hintStyle}>
            Active buttons, slider fills, fader handles, focus rings, and highlights
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              type="color"
              aria-label="Brand accent color"
              value={HEX_RE.test(accent) ? accent : "#2196F3"}
              onChange={(e) => onSetVar("accent", e.target.value)}
              style={{
                width: 36,
                height: 28,
                padding: 0,
                border: "1px solid var(--border-color)",
                borderRadius: 4,
                cursor: "pointer",
                flexShrink: 0,
              }}
            />
            <input
              type="text"
              aria-label="Brand accent hex value"
              value={accent}
              onChange={(e) => onSetVar("accent", e.target.value)}
              style={{ flex: 1, fontSize: 11, fontFamily: "monospace" }}
            />
          </div>
        </div>

        {/* Roundness */}
        <div>
          <div style={labelStyle}>Roundness{roundnessModified && modifiedPill}</div>
          <div style={hintStyle}>Corner radius for buttons, sliders, and all elements</div>
          <SegmentedControl
            ariaLabel="Roundness"
            options={[
              { value: "sharp", label: "Sharp" },
              { value: "standard", label: "Standard" },
              { value: "round", label: "Round" },
            ]}
            value={roundnessPreset}
            onChange={(v) => {
              const map: Record<string, number> = { sharp: 0, standard: 8, round: 16 };
              onSetVar("border_radius", map[v]);
            }}
          />
          {roundnessPreset === null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 3 }}>
              Custom ({borderRadius}px) — adjust in the Theme section below
            </div>
          )}
        </div>

        {/* Surface Style */}
        <div>
          <div style={labelStyle}>Surface Style{surfaceModified && modifiedPill}</div>
          <div style={hintStyle}>
            How elements sit on the page — flat, with depth shadows, or with outlines
          </div>
          <SegmentedControl
            ariaLabel="Surface style"
            options={[
              { value: "flat", label: "Flat" },
              { value: "layered", label: "Layered" },
              { value: "outlined", label: "Outlined" },
            ]}
            value={surfaceStyle}
            onChange={(v) => onApplySurfaceStyle(v as "flat" | "layered" | "outlined")}
          />
        </div>

        {/* Typography */}
        <div>
          <div style={labelStyle}>Typography{fontModified && modifiedPill}</div>
          <div style={hintStyle}>Font family across the entire panel</div>
          <SegmentedControl
            ariaLabel="Typography"
            options={[
              { value: "sans", label: "Sans" },
              { value: "serif", label: "Serif" },
              { value: "mono", label: "Mono" },
            ]}
            value={typographyPreset}
            onChange={(v) => {
              const fonts: Record<string, string> = {
                sans: "Inter, system-ui, sans-serif",
                serif: "Georgia, 'Times New Roman', serif",
                mono: "monospace",
              };
              onSetVar("font_family", fonts[v]);
            }}
          />
          {typographyPreset === null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 3 }}>
              Custom: {fontFamily}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// --- Editor column ---

interface EditorColumnProps {
  working: ThemeDefinition | null;
  isCustom: boolean;
  isDirty: boolean;
  busy: boolean;
  statusMsg: { kind: "info" | "error"; text: string } | null;
  contrastChecks: { label: string; fg: string; bg: string; ratio: number | null; level: WcagLevel }[];
  failingContrasts: { label: string }[];
  themeOverrides: Record<string, unknown>;
  onSetName: (name: string) => void;
  onSetDesc: (desc: string) => void;
  onSetVar: (key: string, value: unknown) => void;
  onSetElementDefault: (elType: string, key: string, value: unknown) => void;
  onApplySurfaceStyle: (style: "flat" | "layered" | "outlined") => void;
  savedVars: Record<string, unknown>;
  savedDefaults: Record<string, Record<string, unknown>>;
  focusedElement: string | null;
  onClearFocus: () => void;
  onSaveChanges: () => void;
  onSaveAsCustom: () => void;
  onDiscard: () => void;
  onExport: () => void;
  onDelete: () => void;
  onClearOverrides: () => void;
}

function EditorColumn({
  working,
  isCustom,
  isDirty,
  busy,
  statusMsg,
  contrastChecks,
  failingContrasts,
  themeOverrides,
  onSetName,
  onSetDesc,
  onSetVar,
  onSetElementDefault,
  onApplySurfaceStyle,
  savedVars,
  savedDefaults,
  focusedElement,
  onClearFocus,
  onSaveChanges,
  onSaveAsCustom,
  onDiscard,
  onExport,
  onDelete,
  onClearOverrides,
}: EditorColumnProps) {
  const elementRefs = useRef<Record<string, HTMLDetailsElement | null>>({});
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (!focusedElement) return;
    // Cancel any in-flight highlight timer and clear all outlines
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    for (const ref of Object.values(elementRefs.current)) {
      if (ref) {
        ref.style.outline = "";
        ref.style.outlineOffset = "";
        ref.style.borderRadius = "";
      }
    }
    // Collapse all, expand target, scroll into view, briefly highlight
    for (const [elType, ref] of Object.entries(elementRefs.current)) {
      if (ref) ref.open = elType === focusedElement;
    }
    const target = elementRefs.current[focusedElement];
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      target.style.outline = "2px solid var(--accent)";
      target.style.outlineOffset = "-2px";
      target.style.borderRadius = "4px";
      highlightTimer.current = setTimeout(() => {
        target.style.outline = "";
        target.style.outlineOffset = "";
        target.style.borderRadius = "";
        highlightTimer.current = undefined;
      }, 1200);
    }
    onClearFocus();
  }, [focusedElement, onClearFocus]);

  useEffect(() => {
    return () => { if (highlightTimer.current) clearTimeout(highlightTimer.current); };
  }, []);

  // Keyboard arrows navigate between element sections
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      // Only activate when a summary or details has focus
      const active = document.activeElement;
      if (!active || !container.contains(active)) return;
      const summary = active.closest("summary");
      if (!summary) return;
      const details = summary.parentElement as HTMLDetailsElement | null;
      if (!details) return;
      const currentIdx = ELEMENT_ORDER.indexOf(
        Object.entries(elementRefs.current).find(([, ref]) => ref === details)?.[0] || "",
      );
      if (currentIdx < 0) return;
      e.preventDefault();
      const nextIdx = e.key === "ArrowDown"
        ? Math.min(currentIdx + 1, ELEMENT_ORDER.length - 1)
        : Math.max(currentIdx - 1, 0);
      const nextType = ELEMENT_ORDER[nextIdx];
      const nextDetails = elementRefs.current[nextType];
      if (nextDetails) {
        for (const [, ref] of Object.entries(elementRefs.current)) {
          if (ref) ref.open = false;
        }
        nextDetails.open = true;
        const nextSummary = nextDetails.querySelector("summary");
        nextSummary?.focus();
        nextDetails.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    };
    container.addEventListener("keydown", onKey);
    return () => container.removeEventListener("keydown", onKey);
  }, []);

  if (!working) {
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--text-muted)",
          padding: 16,
        }}
      >
        Loading theme…
      </div>
    );
  }

  const sectionStyle: React.CSSProperties = {
    background: "var(--bg-surface)",
    borderRadius: 6,
    border: "1px solid var(--border-color)",
    marginBottom: 10,
  };
  const sectionTitleStyle: React.CSSProperties = {
    fontSize: 10,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.5px",
    color: "var(--text-secondary)",
    padding: "8px 10px",
    borderBottom: "1px solid var(--border-color)",
  };

  const vars = working.variables || {};
  const defaults = working.element_defaults || {};

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Action bar */}
      <div
        style={{
          padding: "8px 10px",
          background: "var(--bg-surface)",
          borderBottom: "1px solid var(--border-color)",
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        {isCustom ? (
          <button
            onClick={onSaveChanges}
            disabled={busy || !isDirty}
            title={isDirty ? "Save changes to this theme file" : "No changes to save"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "6px 12px",
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              cursor: busy || !isDirty ? "not-allowed" : "pointer",
              fontSize: 12,
              fontWeight: 600,
              opacity: busy || !isDirty ? 0.55 : 1,
            }}
          >
            <Save size={14} /> Save Changes
          </button>
        ) : (
          <div
            title="Built-in themes can't be modified. Save as Custom to keep your edits."
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "6px 10px",
              background: "rgba(255,167,38,0.12)",
              color: "#ffa726",
              border: "1px solid rgba(255,167,38,0.3)",
              borderRadius: 4,
              fontSize: 11,
              fontWeight: 600,
            }}
          >
            <AlertTriangle size={12} /> Built-in (read-only)
          </div>
        )}
        <button
          onClick={onSaveAsCustom}
          disabled={busy || !working.name.trim()}
          title={isCustom ? "Save a new copy under a different name" : "Create an editable custom copy"}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "6px 12px",
            background: isCustom ? "var(--bg-hover)" : "var(--accent)",
            color: isCustom ? "var(--text-primary)" : "#fff",
            border: isCustom ? "1px solid var(--border-color)" : "none",
            borderRadius: 4,
            cursor: busy ? "not-allowed" : "pointer",
            fontSize: 12,
            opacity: busy ? 0.55 : 1,
          }}
        >
          {isCustom ? <Copy size={14} /> : <FilePlus size={14} />}
          {isCustom ? "Save as Copy" : "Save as Custom"}
        </button>
        <button
          onClick={onDiscard}
          disabled={busy || !isDirty}
          title={isDirty ? "Reload theme and discard your edits" : "No edits to discard"}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "6px 12px",
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            borderRadius: 4,
            cursor: !isDirty ? "not-allowed" : "pointer",
            fontSize: 12,
            color: "var(--text-secondary)",
            opacity: !isDirty ? 0.5 : 1,
          }}
        >
          <RotateCcw size={14} /> Discard
        </button>
        <button
          onClick={onExport}
          title="Download theme JSON"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "6px 12px",
            background: "var(--bg-hover)",
            border: "1px solid var(--border-color)",
            borderRadius: 4,
            cursor: "pointer",
            fontSize: 12,
            color: "var(--text-secondary)",
          }}
        >
          <Download size={14} /> Export
        </button>
        {isCustom && (
          <button
            onClick={onDelete}
            disabled={busy}
            title="Delete this custom theme"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "6px 12px",
              background: "transparent",
              border: "1px solid var(--border-color)",
              borderRadius: 4,
              cursor: busy ? "not-allowed" : "pointer",
              fontSize: 12,
              color: "#ef5350",
              marginLeft: "auto",
            }}
          >
            <Trash2 size={14} /> Delete
          </button>
        )}
      </div>

      {statusMsg && (
        <div
          style={{
            padding: "6px 12px",
            fontSize: 11,
            color: statusMsg.kind === "error" ? "#ef5350" : "#66bb6a",
            background: "var(--bg-surface)",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          {statusMsg.text}
        </div>
      )}

      {/* Project-level override banner — visible whenever the project has
          theme_overrides set. Without this, overrides silently shift every
          theme's editor values and the user has no way to see why. */}
      {Object.keys(themeOverrides).length > 0 && (
        <div
          style={{
            padding: "8px 12px",
            background: "rgba(255,167,38,0.12)",
            borderBottom: "1px solid rgba(255,167,38,0.4)",
            color: "#ffa726",
            fontSize: 11,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <AlertTriangle size={14} style={{ flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <strong>{Object.keys(themeOverrides).length} project override{Object.keys(themeOverrides).length === 1 ? "" : "s"}</strong>
            {" "}applied on top of this theme:{" "}
            <code style={{ fontSize: 10 }}>{Object.keys(themeOverrides).join(", ")}</code>
            <div style={{ color: "var(--text-muted)", marginTop: 2 }}>
              The panel may not match what the editor shows until these are cleared or saved into the theme.
            </div>
          </div>
          <button
            onClick={onClearOverrides}
            style={{
              padding: "4px 10px",
              background: "transparent",
              border: "1px solid #ffa726",
              borderRadius: 4,
              color: "#ffa726",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 600,
              flexShrink: 0,
            }}
          >
            Clear overrides
          </button>
        </div>
      )}

      <div ref={scrollContainerRef} style={{ flex: 1, overflow: "auto", padding: 10 }}>
        {/* Theme info */}
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Theme Info</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <label style={{ width: 70, fontSize: 11, color: "var(--text-secondary)" }}>Name</label>
              <input
                value={working.name}
                onChange={(e) => onSetName(e.target.value)}
                style={{ flex: 1, fontSize: 12 }}
              />
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <label style={{ width: 70, fontSize: 11, color: "var(--text-secondary)", paddingTop: 4 }}>
                Description
              </label>
              <input
                value={working.description || ""}
                onChange={(e) => onSetDesc(e.target.value)}
                style={{ flex: 1, fontSize: 12 }}
              />
            </div>
          </div>
        </div>

        {/* Quick Adjust — composed controls for the most common tweaks */}
        <QuickAdjustSection
          vars={vars}
          defaults={defaults}
          savedVars={savedVars}
          savedDefaults={savedDefaults}
          onSetVar={onSetVar}
          onApplySurfaceStyle={onApplySurfaceStyle}
        />

        {/* Theme tokens — foundational values used across many elements */}
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Theme</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: 10 }}>
            {THEME_TOKENS.map((tok) => {
              const val = vars[tok.key];
              const saved = savedVars[tok.key];
              const isModified = String(val ?? "") !== String(saved ?? "");
              return (
                <div key={tok.key}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <label
                      style={{
                        width: 130,
                        fontSize: 11,
                        color: isModified ? "var(--accent)" : "var(--text-secondary)",
                        fontWeight: isModified ? 600 : 400,
                      }}
                      title={tok.hint}
                    >
                      {tok.label}
                    </label>
                    {tok.type === "color" ? (
                      <>
                        <ColorPickerCell
                          value={String(val ?? "")}
                          onChange={(next) => onSetVar(tok.key, next)}
                        />
                        <input
                          type="text"
                          value={String(val ?? "")}
                          onChange={(e) => onSetVar(tok.key, e.target.value)}
                          style={{ flex: 1, fontSize: 11, fontFamily: "monospace" }}
                        />
                      </>
                    ) : tok.type === "number" ? (
                      <input
                        type="number"
                        value={Number(val || 0)}
                        onChange={(e) => onSetVar(tok.key, Number(e.target.value))}
                        min={0}
                        max={64}
                        style={{ flex: 1, fontSize: 11 }}
                      />
                    ) : (
                      <select
                        value={String(val ?? "Inter, system-ui, sans-serif")}
                        onChange={(e) => onSetVar(tok.key, e.target.value)}
                        style={{ flex: 1, fontSize: 11 }}
                      >
                        <option value="Inter, system-ui, sans-serif">Inter</option>
                        <option value="system-ui, sans-serif">System UI</option>
                        <option value="'Roboto', sans-serif">Roboto</option>
                        <option value="'Segoe UI', sans-serif">Segoe UI</option>
                        <option value="Georgia, 'Times New Roman', serif">Serif</option>
                        <option value="monospace">Monospace</option>
                      </select>
                    )}
                    {isModified && (
                      <button
                        type="button"
                        onClick={() => onSetVar(tok.key, saved)}
                        title={`Reset to saved: ${saved}`}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          padding: 2,
                          background: "transparent",
                          border: "none",
                          cursor: "pointer",
                          color: "var(--accent)",
                          flexShrink: 0,
                          opacity: 0.7,
                        }}
                      >
                        <RotateCcw size={11} />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Per-element styling */}
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Elements</div>
          <div style={{ padding: 10 }}>
            {ELEMENT_ORDER.map((elType) => {
              const controls = ELEMENT_CONTROLS[elType];
              if (!controls) return null;
              return (
                <details
                  key={elType}
                  ref={(el) => { elementRefs.current[elType] = el; }}
                  style={{ marginBottom: 6 }}
                >
                  <summary
                    style={{
                      cursor: "pointer",
                      fontSize: 12,
                      fontWeight: 600,
                      color: "var(--text-primary)",
                      padding: "4px 0",
                    }}
                  >
                    {ELEMENT_TYPE_LABELS[elType] || elType}
                  </summary>
                  <div style={{ paddingLeft: 12, paddingTop: 4, display: "flex", flexDirection: "column", gap: 4 }}>
                    {controls.map((control) => {
                      const val =
                        control.source.kind === "var"
                          ? vars[control.source.key]
                          : defaults[elType]?.[control.source.key];
                      const savedVal =
                        control.source.kind === "var"
                          ? savedVars[control.source.key]
                          : savedDefaults[elType]?.[control.source.key];
                      const isModified = String(val ?? "") !== String(savedVal ?? "");
                      const setControl = (next: unknown) => {
                        if (control.source.kind === "var") {
                          onSetVar(control.source.key, next);
                        } else {
                          onSetElementDefault(elType, control.source.key, next);
                        }
                      };
                      const resetControl = () => {
                        if (control.source.kind === "var") {
                          onSetVar(control.source.key, savedVal);
                        } else {
                          onSetElementDefault(elType, control.source.key, savedVal);
                        }
                      };
                      const isBoxShadow = control.source.kind === "default" && control.source.key === "box_shadow";
                      return (
                        <div key={`${control.source.kind}:${control.source.key}`}>
                          <div
                            style={{
                              display: "flex",
                              gap: 6,
                              alignItems: isBoxShadow ? "flex-start" : "center",
                            }}
                          >
                            <label
                              title={control.hint}
                              style={{
                                width: 130,
                                fontSize: 10,
                                color: isModified ? "var(--accent)" : "var(--text-muted)",
                                fontWeight: isModified ? 600 : 400,
                                paddingTop: isBoxShadow ? 4 : 0,
                              }}
                            >
                              {control.label}
                            </label>
                            {control.type === "color" ? (
                              <>
                                <ColorPickerCell
                                  value={String(val ?? "")}
                                  onChange={(next) => setControl(next)}
                                />
                                <input
                                  type="text"
                                  value={String(val ?? "")}
                                  onChange={(e) => setControl(e.target.value)}
                                  style={{ flex: 1, fontSize: 10, fontFamily: "monospace" }}
                                />
                              </>
                            ) : control.type === "number" ? (
                              <input
                                type="number"
                                value={val == null || val === "" ? "" : Number(val)}
                                onChange={(e) =>
                                  setControl(e.target.value === "" ? undefined : Number(e.target.value))
                                }
                                style={{ flex: 1, fontSize: 10 }}
                              />
                            ) : isBoxShadow ? (
                              <textarea
                                value={String(val ?? "")}
                                onChange={(e) => setControl(e.target.value)}
                                placeholder="none"
                                title={String(val ?? "")}
                                rows={2}
                                spellCheck={false}
                                style={{
                                  flex: 1,
                                  fontSize: 10,
                                  fontFamily: "monospace",
                                  resize: "vertical",
                                  minHeight: 32,
                                  lineHeight: 1.3,
                                }}
                              />
                            ) : (
                              <input
                                type="text"
                                value={String(val ?? "")}
                                onChange={(e) => setControl(e.target.value)}
                                placeholder="none"
                                title={String(val ?? "")}
                                style={{ flex: 1, fontSize: 10, fontFamily: "monospace" }}
                              />
                            )}
                            {isModified && (
                              <button
                                type="button"
                                onClick={resetControl}
                                title={`Reset to saved: ${savedVal}`}
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  padding: 2,
                                  background: "transparent",
                                  border: "none",
                                  cursor: "pointer",
                                  color: "var(--accent)",
                                  flexShrink: 0,
                                  opacity: 0.7,
                                }}
                              >
                                <RotateCcw size={10} />
                              </button>
                            )}
                          </div>
                          {control.hint && (
                            <div
                              style={{
                                fontSize: 9,
                                color: "var(--text-muted)",
                                paddingLeft: 136,
                                marginTop: 1,
                                lineHeight: 1.3,
                              }}
                            >
                              {control.hint}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </details>
              );
            })}
          </div>
        </div>

        {/* Contrast */}
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>
            Contrast (WCAG)
            {failingContrasts.length > 0 && (
              <span style={{ color: "#ef5350", marginLeft: 6, fontWeight: 600 }}>
                {failingContrasts.length} failing
              </span>
            )}
          </div>
          <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 4 }}>
            {contrastChecks.map((c) => (
              <div
                key={c.label}
                style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}
              >
                {c.level === "fail" ? (
                  <AlertTriangle size={12} style={{ color: "#ef5350", flexShrink: 0 }} />
                ) : (
                  <Check size={12} style={{ color: "#66bb6a", flexShrink: 0 }} />
                )}
                <span style={{ flex: 1, color: "var(--text-secondary)" }}>{c.label}</span>
                <div style={{ display: "flex", gap: 2 }}>
                  <div
                    style={{
                      width: 12,
                      height: 12,
                      borderRadius: 2,
                      background: c.fg,
                      border: "1px solid rgba(128,128,128,0.3)",
                    }}
                  />
                  <div
                    style={{
                      width: 12,
                      height: 12,
                      borderRadius: 2,
                      background: c.bg,
                      border: "1px solid rgba(128,128,128,0.3)",
                    }}
                  />
                </div>
                <span
                  style={{
                    fontFamily: "monospace",
                    fontSize: 10,
                    fontWeight: 600,
                    color: c.level === "fail" ? "#ef5350" : c.level === "AAA" ? "#66bb6a" : "#ffa726",
                    width: 56,
                    textAlign: "right",
                  }}
                >
                  {c.ratio ? `${c.ratio.toFixed(1)}:1` : "—"}{" "}
                  <span style={{ fontSize: 8 }}>{c.level}</span>
                </span>
              </div>
            ))}
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.4 }}>
              WCAG AA requires 4.5:1 for normal text. AAA requires 7:1.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
