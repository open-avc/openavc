// Color parsing + WCAG contrast helpers for the Theme Studio.
//
// Pure functions (the one browser dependency, canvasParse, is feature-detected),
// kept in their own module so they can be unit-tested without standing up a React
// renderer. The contrast checker and the color picker both rely on parseColor so
// a valid CSS color the panel renders fine is never misread as "not a color".

export interface Rgba { r: number; g: number; b: number; a: number; }

export function clamp255(n: number): number {
  return Math.max(0, Math.min(255, Math.round(n)));
}

let _colorCanvasCtx: CanvasRenderingContext2D | null | undefined;
function canvasParse(value: string): Rgba | null {
  if (typeof document === "undefined") return null;
  if (_colorCanvasCtx === undefined) {
    _colorCanvasCtx = document.createElement("canvas").getContext("2d");
  }
  const ctx = _colorCanvasCtx;
  if (!ctx) return null;
  // The fillStyle setter silently ignores an invalid color, keeping the prior
  // value. Seed two different colors and set the candidate over each: a valid
  // color normalizes to the same string both times; an invalid one keeps the two
  // distinct seeds.
  ctx.fillStyle = "#000000";
  ctx.fillStyle = value;
  const a = ctx.fillStyle;
  ctx.fillStyle = "#ffffff";
  ctx.fillStyle = value;
  const b = ctx.fillStyle;
  if (a !== b) return null;
  return parseColor(a, true);
}

// Parse any CSS color the panel can render into RGBA. Handles #rgb / #rgba /
// #rrggbb / #rrggbbaa, rgb()/rgba() (legacy and space/slash syntax), and — via a
// one-off canvas — named colors, hsl(), etc. Returns null only for "transparent"
// or a value no browser can parse.
export function parseColor(value: string, fromCanvas = false): Rgba | null {
  if (!value) return null;
  const v = value.trim().toLowerCase();
  if (v === "transparent") return null;
  const hex = v.match(/^#([0-9a-f]{3,8})$/);
  if (hex) {
    const h = hex[1];
    const x = (s: string) => parseInt(s, 16);
    if (h.length === 3 || h.length === 4) {
      return { r: x(h[0] + h[0]), g: x(h[1] + h[1]), b: x(h[2] + h[2]), a: h.length === 4 ? x(h[3] + h[3]) / 255 : 1 };
    }
    if (h.length === 6 || h.length === 8) {
      return { r: x(h.slice(0, 2)), g: x(h.slice(2, 4)), b: x(h.slice(4, 6)), a: h.length === 8 ? x(h.slice(6, 8)) / 255 : 1 };
    }
    return null;
  }
  const rgb = v.match(/^rgba?\(([^)]+)\)$/);
  if (rgb) {
    const parts = rgb[1].split(/[,/\s]+/).filter(Boolean);
    if (parts.length >= 3) {
      const chan = (s: string) => (s.endsWith("%") ? (parseFloat(s) / 100) * 255 : parseFloat(s));
      const r = chan(parts[0]), g = chan(parts[1]), b = chan(parts[2]);
      const a = parts[3] != null ? (parts[3].endsWith("%") ? parseFloat(parts[3]) / 100 : parseFloat(parts[3])) : 1;
      if ([r, g, b].every((c) => !isNaN(c))) {
        return { r: clamp255(r), g: clamp255(g), b: clamp255(b), a: isNaN(a) ? 1 : Math.max(0, Math.min(1, a)) };
      }
    }
    return null;
  }
  // Named colors, hsl(), etc — defer to the browser once (fromCanvas stops recursion).
  return fromCanvas ? null : canvasParse(v);
}

export function rgbToHex6(c: Rgba): string {
  return `#${[c.r, c.g, c.b].map((n) => clamp255(n).toString(16).padStart(2, "0")).join("")}`;
}

export function relativeLuminance(r: number, g: number, b: number): number {
  const [rs, gs, bs] = [r, g, b].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

export function contrastRatio(c1: string, c2: string): number | null {
  const a = parseColor(c1);
  const b = parseColor(c2);
  // A fully-transparent (or unparseable) color has no contrast to compute —
  // surface that as n/a, never a red FAIL.
  if (!a || !b || a.a === 0 || b.a === 0) return null;
  const l1 = relativeLuminance(a.r, a.g, a.b);
  const l2 = relativeLuminance(b.r, b.g, b.b);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

export type WcagLevel = "AAA" | "AA" | "fail" | "na";
export function wcagLevel(ratio: number | null): WcagLevel {
  if (ratio == null) return "na";
  if (ratio >= 7) return "AAA";
  if (ratio >= 4.5) return "AA";
  return "fail";
}

export function adjustHex(hex: string, amount: number): string {
  const rgb = parseColor(hex);
  if (!rgb) return hex;
  const adjusted = [rgb.r, rgb.g, rgb.b].map((c) => {
    if (amount > 0) return Math.round(c + (255 - c) * amount);
    return Math.round(c * (1 + amount));
  });
  return `#${adjusted.map((c) => clamp255(c).toString(16).padStart(2, "0")).join("")}`;
}

// Classify a CSS color as a light or dark surface by WCAG relative luminance.
// Returns null when the value can't be parsed, so a caller can fall back to a
// heuristic. Used to derive a theme's light/dark mode from its actual panel
// background instead of guessing from the theme id.
export function isLightColor(value: string): boolean | null {
  const c = parseColor(value);
  if (!c) return null;
  return relativeLuminance(c.r, c.g, c.b) > 0.5;
}

export function deriveSurfaceBorder(surface: string): string {
  const rgb = parseColor(surface);
  if (!rgb) return surface;
  const lum = relativeLuminance(rgb.r, rgb.g, rgb.b);
  return adjustHex(surface, lum < 0.5 ? 0.2 : -0.15);
}

// Effective CSS-variable fallbacks (mirrors :root in panel-elements.css). Used so
// a cleared theme/element color shows the value the panel actually renders rather
// than a misleading blank/black field.
export const CSS_VAR_FALLBACKS: Record<string, string> = {
  panel_bg: "#1a1a2e",
  panel_text: "#ffffff",
  accent: "#2196F3",
  button_bg: "#424242",
  button_text: "#cccccc",
  button_border: "#555555",
  danger: "#F44336",
  success: "#4CAF50",
  warning: "#FF9800",
  surface: "#2a2a4a",
  surface_border: "#3a3a5c",
};
