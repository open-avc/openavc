/**
 * What the Theme Studio sends the server as a theme file, and the name and id
 * a new theme gets. A theme goes out without `_source`, the server's note of
 * where it found the theme; Save Changes, Save as Custom and Export also send
 * the four colours the picker's card shows. A new theme takes the first id not
 * already in the theme list, counting up from 2, and its name carries the same
 * number, so the picker never shows two cards called the same: Save as Custom
 * marks the copy "(Custom)" or "(Copy)" (unless the name already says so),
 * Duplicate "(Copy)" after the source's own name with any copy number taken
 * off, and an import kept beside the theme it collided with "(2)", "(3)".
 */
import type { ThemeDefinition } from "../../api/restClient";

/** The theme as the server takes it: the patch laid over it, without `_source`. */
export function themeFile(theme: ThemeDefinition, patch: Partial<ThemeDefinition> = {}): ThemeDefinition {
  const file = { ...theme, ...patch };
  delete file._source;
  return file;
}

/** The colours a theme's picker card shows: the page, a surface, the accent, the text. */
export function previewColors(vars: Record<string, unknown> | undefined): string[] {
  const v = vars || {};
  return [v.panel_bg, v.surface || v.button_bg, v.accent, v.panel_text].filter(Boolean) as string[];
}

/** The theme as Save Changes and Export write it. */
export function savedFile(theme: ThemeDefinition): ThemeDefinition {
  return themeFile(theme, { preview_colors: previewColors(theme.variables) });
}

export interface NewTheme {
  id: string;
  name: string;
}

/** `base`, else `base-2`, `base-3`, …: the first no theme has, with its number (1 for `base` itself). */
function freeId(base: string, taken: ReadonlySet<string>, from = 1): { id: string; n: number } {
  const idFor = (n: number) => (n === 1 ? base : `${base}-${n}`);
  let n = from;
  while (taken.has(idFor(n))) n++;
  return { id: idFor(n), n };
}

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/** Save as Custom's name and id for a theme called `name`; null when the name is blank. */
export function customCopy(name: string, fromCustom: boolean, taken: ReadonlySet<string>): NewTheme | null {
  let baseName = name.trim();
  if (!baseName) return null;
  if (!/ \((Copy|Custom)( \d+)?\)$/.test(baseName)) baseName += fromCustom ? " (Copy)" : " (Custom)";
  const { id, n } = freeId(slugify(baseName) || "custom-theme", taken);
  return { id, name: n === 1 ? baseName : `${baseName} ${n}` };
}

/** Duplicate's name and id for the theme `sourceId`, called `sourceName`. */
export function duplicateOf(sourceId: string, sourceName: string, taken: ReadonlySet<string>): NewTheme {
  const { id, n } = freeId(`${sourceId.replace(/-copy(-\d+)?$/, "")}-copy`, taken);
  const baseName = sourceName.replace(/\s*\(Copy(?:\s+\d+)?\)\s*$/, "");
  return { id, name: n === 1 ? `${baseName} (Copy)` : `${baseName} (Copy ${n})` };
}

/** Keep both's name and id for an imported theme that collided with the custom
 *  theme `collidingId`; `fileName` is the name in the file. */
export function keptBeside(collidingId: string, fileName: string | undefined, taken: ReadonlySet<string>): NewTheme {
  const { id, n } = freeId(collidingId, taken, 2);
  return { id, name: `${(fileName || collidingId).replace(/\s*\(\d+\)\s*$/, "")} (${n})` };
}
