/**
 * The edits the Theme Studio makes to the theme it holds, each a pure function
 * from one theme to the next. The Studio's Unsaved mark, Discard and the saved
 * file all read the theme as JSON text, so two rules hold in these edits: a
 * cleared value deletes its key rather than storing "" (the panel ignores "",
 * and the file would keep it), and an element type left with no defaults, or
 * page defaults left empty, lose their entry, so a theme edited back to where
 * it was compares equal again. (The page background's next page defaults are
 * worked out by `pageBackground.ts`, with its own rule for a cleared field,
 * before they reach `withPageDefaults`.) A key set again after it was deleted lands at
 * the end of its object, and that does read as unsaved: the comparison is of
 * text.
 */
import type { ThemeDefinition } from "../../api/restClient";

type ElementDefaults = ThemeDefinition["element_defaults"];

/** A theme as the Studio holds it: variables and element defaults copied (and
 *  added, empty, when the server sent none), so no edit reaches the answer the
 *  theme came in. */
export function editableCopy(theme: ThemeDefinition): ThemeDefinition {
  return {
    ...theme,
    variables: { ...(theme.variables || {}) },
    element_defaults: JSON.parse(JSON.stringify(theme.element_defaults || {})),
  };
}

/** Whether an editor's value means "not set": the key is deleted, not stored. */
export function isCleared(value: unknown): boolean {
  return value === undefined || value === "";
}

/** Whether a field's value is not the one the theme was saved with, compared as
 *  the field shows it, so an unset value and an empty one are the same. */
export function differsFromSaved(value: unknown, saved: unknown): boolean {
  return String(value ?? "") !== String(saved ?? "");
}

/**
 * Set or clear one variable. The border radius is also an element default, and
 * an element's own radius wins over the variable, so a change to the radius
 * would seem to do nothing: it carries along every element whose radius still
 * equals the old one, and leaves one set apart on purpose (a group at 12 under
 * a global 8) where it is.
 */
export function withVariable(theme: ThemeDefinition, key: string, value: unknown): ThemeDefinition {
  const variables = { ...(theme.variables || {}) };
  if (isCleared(value)) delete variables[key];
  else variables[key] = value;
  if (key !== "border_radius") return { ...theme, variables };
  return { ...theme, variables, element_defaults: radiusCarriedAlong(theme, value) };
}

function radiusCarriedAlong(theme: ThemeDefinition, radius: unknown): ElementDefaults {
  const old = theme.variables?.border_radius;
  const defaults: ElementDefaults = JSON.parse(JSON.stringify(theme.element_defaults || {}));
  for (const type of Object.keys(defaults)) {
    const own = defaults[type]?.border_radius;
    if (own !== undefined && String(own) === String(old)) defaults[type].border_radius = radius;
  }
  return defaults;
}

/** Set or clear one element type's default; a type left with none loses its entry. */
export function withElementDefault(
  theme: ThemeDefinition,
  type: string,
  key: string,
  value: unknown,
): ThemeDefinition {
  const defaults = { ...(theme.element_defaults || {}) };
  const own = { ...(defaults[type] || {}) };
  if (isCleared(value)) delete own[key];
  else own[key] = value;
  if (Object.keys(own).length === 0) delete defaults[type];
  else defaults[type] = own;
  return { ...theme, element_defaults: defaults };
}

/** Replace the page defaults whole (the Page Background section works out the
 *  next shape); nothing left removes the key. */
export function withPageDefaults(theme: ThemeDefinition, next: Record<string, unknown>): ThemeDefinition {
  if (!next || Object.keys(next).length === 0) {
    const cleaned = { ...theme };
    delete cleaned.page_defaults;
    return cleaned;
  }
  return { ...theme, page_defaults: next };
}
