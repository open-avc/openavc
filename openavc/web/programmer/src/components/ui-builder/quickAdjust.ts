/**
 * Quick Adjust's three presets, each one table read both ways: which preset a
 * theme is on (the one marked, and whether it moved since the theme was saved)
 * and what choosing one writes. A preset is a summary of values the Theme
 * section and the Elements section edit one at a time, so a theme edited there
 * may match none, and Quick Adjust then marks none and says what is set.
 *
 * - Roundness is the border radius variable, compared in pixels; the variable
 *   is stored in rem like every panel length (`storeStyleValue`).
 * - Surface style is read off the button alone (a shadow, else a border), but
 *   choosing one writes the borders of every bordered element type and the
 *   shadows of the raised ones, and lines the surfaces to match.
 * - Typography is read by what a font stack names, in the table's order (an
 *   "Inter" stack is Sans whatever else it lists), and written as one stack.
 */
import type { ThemeDefinition } from "../../api/restClient";
import { deriveSurfaceBorder } from "./colorUtils";
import { displayStyleValue, storeStyleValue, REM_BASE_PX } from "./uiBuilderHelpers";

type ElementDefaults = ThemeDefinition["element_defaults"];

export interface Preset {
  value: string;
  label: string;
}

// --- Roundness ---

/** The radius a theme with none draws at. */
const DEFAULT_RADIUS_PX = 8;

export const ROUNDNESS: readonly (Preset & { px: number })[] = [
  { value: "sharp", label: "Sharp", px: 0 },
  { value: "standard", label: "Standard", px: DEFAULT_RADIUS_PX },
  { value: "round", label: "Round", px: 16 },
];

/** The theme's border radius in pixels: the stored rem as px, or the
 *  panel's own 8px when the theme sets none (the panel skips an empty value
 *  too). Quick Adjust, the Border Radius field and the picker's cards all
 *  read it here. */
export function radiusPx(variables: Record<string, unknown>): number {
  const stored = variables.border_radius;
  const unset = stored === undefined || stored === null || stored === "";
  return Number(displayStyleValue("border_radius", unset ? DEFAULT_RADIUS_PX / REM_BASE_PX : stored));
}

/** The roundness preset at exactly this radius, or null. */
export function roundnessOf(px: number): string | null {
  return ROUNDNESS.find((r) => r.px === px)?.value ?? null;
}

/** The border radius variable a roundness preset stores. */
export function roundnessRadius(preset: string): unknown {
  return storeStyleValue("border_radius", ROUNDNESS.find((r) => r.value === preset)?.px);
}

// --- Surface style ---

export type SurfaceStyle = "flat" | "layered" | "outlined";

export const SURFACE_STYLES: readonly (Preset & { value: SurfaceStyle })[] = [
  { value: "flat", label: "Flat" },
  { value: "layered", label: "Layered" },
  { value: "outlined", label: "Outlined" },
];

/** The element types a surface style gives a border, or takes it from. */
const BORDERED = [
  "button", "label", "slider", "fader", "select", "text_input",
  "gauge", "level_meter", "list", "matrix", "group", "image", "clock",
];
/** The element types that stand off the page: Layered shadows them, the others take shadows away. */
const RAISED = ["button", "slider", "camera_preset", "page_nav"];
const HAIRLINE = 1 / REM_BASE_PX;
/** Layered's shadows, set on these types whether or not the theme has defaults for them. */
const LAYERED_SHADOWS: Record<string, string> = {
  button: "0 2px 4px rgba(0,0,0,0.3)",
  slider: "inset 0 1px 3px rgba(0,0,0,0.3)",
};
/** Layered's shadows for these types only when the theme already has defaults for them. */
const LAYERED_SHADOWS_IF_STYLED: Record<string, string> = {
  camera_preset: "0 2px 4px rgba(0,0,0,0.3)",
  page_nav: "0 1px 3px rgba(0,0,0,0.2)",
};

/** Which surface style a theme's element defaults are on, read off the button. */
export function surfaceStyleOf(defaults: ElementDefaults): SurfaceStyle {
  const shadow = String(defaults.button?.box_shadow ?? "");
  if (shadow !== "" && shadow !== "none") return "layered";
  return Number(defaults.button?.border_width ?? 1) === 0 ? "flat" : "outlined";
}

/**
 * The theme with a surface style applied: its element defaults restyled, and
 * its surface border lined to match.
 */
export function withSurfaceStyle(theme: ThemeDefinition, style: SurfaceStyle): ThemeDefinition {
  return {
    ...theme,
    variables: linedSurfaces(theme.variables || {}, style),
    element_defaults: restyled(theme.element_defaults || {}, style),
  };
}

/** Every bordered type's border (none for Flat, a hairline otherwise) and the raised types' shadows. */
function restyled(current: ElementDefaults, style: SurfaceStyle): ElementDefaults {
  const defaults: ElementDefaults = JSON.parse(JSON.stringify(current));
  const set = (type: string, key: string, value: unknown) => {
    if (!defaults[type]) defaults[type] = {};
    defaults[type][key] = value;
  };
  for (const type of BORDERED) set(type, "border_width", style === "flat" ? 0 : HAIRLINE);
  const shadows = style === "layered" ? layeredShadows(defaults) : Object.fromEntries(RAISED.map((t) => [t, "none"]));
  for (const [type, shadow] of Object.entries(shadows)) set(type, "box_shadow", shadow);
  return defaults;
}

function layeredShadows(defaults: ElementDefaults): Record<string, string> {
  const styled = Object.entries(LAYERED_SHADOWS_IF_STYLED).filter(([type]) => defaults[type]);
  return { ...LAYERED_SHADOWS, ...Object.fromEntries(styled) };
}

/** Flat lines the surfaces with their own colour; Layered and Outlined give a
 *  surface border that matched the surface a shade of its own, and leave one
 *  already set apart. No surface colour, no change. */
function linedSurfaces(current: Record<string, unknown>, style: SurfaceStyle): Record<string, unknown> {
  const variables = { ...current };
  const surface = variables.surface;
  if (!surface) return variables;
  if (style === "flat") variables.surface_border = String(surface);
  else if (String(surface) === String(variables.surface_border)) variables.surface_border = deriveSurfaceBorder(String(surface));
  return variables;
}

// --- Typography ---

/** The font a theme with none draws in. */
export const DEFAULT_FONT = "Inter, system-ui, sans-serif";

export const TYPOGRAPHY: readonly (Preset & { font: string; names: (font: string) => boolean })[] = [
  { value: "sans", label: "Sans", font: DEFAULT_FONT,
    names: (f) => f.includes("Inter") || f.startsWith("system-ui") },
  { value: "serif", label: "Serif", font: "Georgia, 'Times New Roman', serif",
    names: (f) => f.includes("Georgia") || (f.includes("serif") && !f.includes("sans-serif")) },
  { value: "mono", label: "Mono", font: "monospace",
    names: (f) => f === "monospace" || f.includes("Mono") || f.includes("Fira") },
];

/** The typography preset a font stack names, or null. */
export function typographyOf(font: string): string | null {
  return TYPOGRAPHY.find((t) => t.names(font))?.value ?? null;
}

/** The font stack a typography preset writes. */
export function typographyFont(preset: string): string | undefined {
  return TYPOGRAPHY.find((t) => t.value === preset)?.font;
}
