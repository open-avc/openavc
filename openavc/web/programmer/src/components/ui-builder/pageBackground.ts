/**
 * The theme's page background, its `page_defaults`, as the Theme Studio's Page
 * Background section reads and writes it: the keys panel.js's
 * `_themePageDefaultsToBackground` paints behind every page that sets none of
 * its own (`background_color`, `background_gradient` {from, to, angle}, and
 * `background_image` with its `_size`, `_position` and `_opacity`).
 *
 * Reading: a value of the wrong type reads as unset (a colour that is not text, a
 * gradient that is not an object), and a colour that is a token (`var(...)`)
 * reads as the theme's own page colour, though it stays stored until a colour is
 * chosen. Writing: every change lays a patch over the page background and then
 * removes each key left undefined, null or "", whichever key it is, so a null the
 * server sent goes with the next change (the rest of the theme's edits,
 * `themeEdits.ts`, keep a null). A gradient is written whole, from, to and angle
 * in that order, so a key of its own it carried is lost. The section shows
 * "modified" while the page background differs from the saved one as JSON text,
 * so the same values in another order count as a change.
 */

export type PageDefaults = Record<string, unknown>;

export interface Gradient {
  from?: string;
  to?: string;
  angle?: number;
}

/** The colour a new custom colour and a new gradient start from. */
export const DEFAULT_PAGE_COLOR = "#1a1a2e";
/** Where a new gradient ends. */
export const DEFAULT_GRADIENT_END = "#000000";
/** A new gradient's angle, and the angle shown for a gradient with none. */
export const DEFAULT_GRADIENT_ANGLE = 180;

/** The page background as the section shows it. */
export interface PageBackground {
  /** The colour typed as the theme's own, "" when it uses the page colour (none set, or a token). */
  customColor: string;
  gradient: Gradient | null;
  image: string;
  imageSize: string;
  imagePosition: string;
  imageOpacity: number;
}

const text = (value: unknown, otherwise: string) => (typeof value === "string" ? value : otherwise);

export function readBackground(pd: PageDefaults): PageBackground {
  const color = text(pd.background_color, "");
  const gradient = pd.background_gradient;
  return {
    customColor: color.startsWith("var(") ? "" : color,
    gradient: gradient && typeof gradient === "object" ? (gradient as Gradient) : null,
    image: text(pd.background_image, ""),
    imageSize: text(pd.background_image_size, "cover"),
    imagePosition: text(pd.background_image_position, "center"),
    imageOpacity: pd.background_image_opacity == null ? 1 : Number(pd.background_image_opacity),
  };
}

const isLeftEmpty = (value: unknown) => value === undefined || value === null || value === "";

/** The page background with `patch` laid over it and every cleared key removed. */
export function patched(pd: PageDefaults, patch: PageDefaults): PageDefaults {
  const next: PageDefaults = { ...pd, ...patch };
  for (const key of Object.keys(next)) if (isLeftEmpty(next[key])) delete next[key];
  return next;
}

/** The theme's page colour (the colour removed), or a custom one: the colour already typed, else the default. */
export function withCustomColor(pd: PageDefaults, custom: boolean): PageDefaults {
  if (!custom) return patched(pd, { background_color: undefined });
  return patched(pd, { background_color: readBackground(pd).customColor || DEFAULT_PAGE_COLOR });
}

/** A gradient turned off, or on from the custom colour (else the default) to black at 180 degrees. */
export function withGradient(pd: PageDefaults, on: boolean): PageDefaults {
  if (!on) return patched(pd, { background_gradient: undefined });
  const from = readBackground(pd).customColor || DEFAULT_PAGE_COLOR;
  return patched(pd, { background_gradient: { from, to: DEFAULT_GRADIENT_END, angle: DEFAULT_GRADIENT_ANGLE } });
}

/** One part of the gradient changed: the whole gradient written again, its unset parts at their defaults. */
export function withGradientPart(pd: PageDefaults, part: Gradient): PageDefaults {
  const gradient = readBackground(pd).gradient;
  return patched(pd, {
    background_gradient: {
      from: gradient?.from ?? DEFAULT_PAGE_COLOR,
      to: gradient?.to ?? DEFAULT_GRADIENT_END,
      angle: gradient?.angle ?? DEFAULT_GRADIENT_ANGLE,
      ...part,
    },
  });
}

/** An image set, or cleared together with its fit, position and opacity. */
export function withImage(pd: PageDefaults, url: string): PageDefaults {
  if (url) return patched(pd, { background_image: url });
  return patched(pd, {
    background_image: undefined,
    background_image_size: undefined,
    background_image_position: undefined,
    background_image_opacity: undefined,
  });
}

/** Whether the page background is not the one the theme was saved with, compared as JSON text. */
export function backgroundDiffers(pd: PageDefaults, saved: PageDefaults): boolean {
  return JSON.stringify(pd || {}) !== JSON.stringify(saved || {});
}
