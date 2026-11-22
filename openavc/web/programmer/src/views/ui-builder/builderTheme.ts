/**
 * The theme as the UI Builder shows it: the list of themes and the effective
 * default of every themed property for each control type. The canvas is the
 * panel itself, which reads the theme on its own.
 *
 * `projectThemeId` is the theme a project draws: its theme id, else the one the
 * project schema gives its light or dark setting (`blank_from` in
 * projectValues.gen.ts), as the panel reads it (modules/project_values.js). The
 * page reads the theme under it, and the theme list, the Theme Studio and the
 * Studio's Delete all take it from here.
 *
 * `effectiveThemeDefaults` is the Builder's copy of where the panel's
 * stylesheet gets each control's look: the theme's own `element_defaults`,
 * then the button colours for the button-like types, the accent and surface
 * for the interactive ones, the panel text colour for the rest, and the
 * corner radius and font for all. A property missing here shows blank in the
 * Properties panel instead of the value the control is drawn with, so a new
 * themed property is added here as well as in the panel's CSS. Text size is
 * not here: the canvas reports what it drew (uiBuilderStore.textDefaultsRem).
 *
 * `blankLook` is what the panel draws for a project that leaves its accent or
 * its font blank, which Panel Settings shows in those two fields.
 */
import { useCallback, useEffect, useState } from "react";
import { blankDrawn } from "../../api/projectValues";
import { PROJECT_VALUES } from "../../api/projectValues.gen";
import { THEME_VALUES } from "../../api/themeValues.gen";
import { getTunnelPrefix, listThemes } from "../../api/restClient";
import type { ThemeSummary } from "../../api/systemClient";
import type { UISettings } from "../../api/types";
import { CSS_VAR_FALLBACKS } from "../../components/ui-builder/colorUtils";
import { showError } from "../../store/toastStore";

export type ThemeDefaults = Record<string, Record<string, unknown>>;

export type { ThemeSummary };

interface ThemeFile {
  variables?: Record<string, unknown>;
  element_defaults?: Record<string, unknown>;
}

/** Types whose base colours come from the button_* variables. */
const BUTTON_TYPES = ["button", "page_nav", "camera_preset", "keypad"];
/** Types given the accent (--el-accent) and surface (--el-surface) at render. */
const INTERACTIVE_TYPES = ["slider", "fader", "select", "text_input", "page_nav", "keypad"];
/** Types that inherit the panel's text colour through CSS. */
const PANEL_TEXT_TYPES = [
  "label", "slider", "fader", "gauge", "level_meter", "list", "select", "text_input",
  "group", "clock", "matrix", "status_led",
];
/** Where each button colour comes from, and the property it fills. */
const BUTTON_COLOURS: Array<[string, string]> = [
  ["button_bg", "bg_color"],
  ["button_text", "text_color"],
  ["button_border", "border_color"],
];

/** Every type's effective defaults for a theme, or none for no theme. */
export function effectiveThemeDefaults(theme: ThemeFile | null): ThemeDefaults {
  const vars = theme?.variables || {};
  const out: ThemeDefaults = {};
  for (const [type, defaults] of Object.entries(theme?.element_defaults || {})) {
    out[type] = { ...(defaults as Record<string, unknown>) };
  }
  fillButtonColours(out, vars);
  fillInteractive(out, vars);
  fillPanelText(out, vars);
  fillBaseline(out, vars);
  return out;
}

function entry(out: ThemeDefaults, type: string): Record<string, unknown> {
  return (out[type] ??= {});
}

/** The button colours, where the theme's own defaults leave them unset. */
function fillButtonColours(out: ThemeDefaults, vars: Record<string, unknown>) {
  for (const t of BUTTON_TYPES) {
    const d = entry(out, t);
    for (const [variable, prop] of BUTTON_COLOURS) {
      if (vars[variable] && !d[prop]) d[prop] = vars[variable];
    }
  }
}

/** The accent and surface, set at render, so they win over the defaults. */
function fillInteractive(out: ThemeDefaults, vars: Record<string, unknown>) {
  for (const t of INTERACTIVE_TYPES) {
    const d = entry(out, t);
    if (vars.accent) d.accent_color = vars.accent;
    if (vars.surface) d.track_color = vars.surface;
  }
}

/** The panel's text colour, where the type has none of its own. */
function fillPanelText(out: ThemeDefaults, vars: Record<string, unknown>) {
  if (!vars.panel_text) return;
  for (const t of PANEL_TEXT_TYPES) {
    const d = entry(out, t);
    if (!d.text_color) d.text_color = vars.panel_text;
  }
}

/** The corner radius and font every type inherits when nothing else sets them. */
function fillBaseline(out: ThemeDefaults, vars: Record<string, unknown>) {
  const baseline: Record<string, unknown> = {};
  if (vars.border_radius) baseline.border_radius = vars.border_radius;
  if (vars.font_family) baseline.font_family = vars.font_family;
  for (const d of Object.values(out)) {
    for (const [k, v] of Object.entries(baseline)) {
      if (d[k] === undefined) d[k] = v;
    }
  }
}

/** The font the panel's stylesheet draws (panel.css, its body) when neither the
 *  theme nor the project names one: the theme rule's fallback for font_family. */
export const PANEL_FONT = THEME_VALUES.variables.find((v) => v.name === "font_family")!.fallback;

/** What the panel draws for a blank Accent Color and a blank Font Family. */
export interface BlankLook {
  accent: string;
  font: string;
}

/**
 * What the panel draws for a blank accent and font (web/panel/modules/theme.js):
 * the project's theme overrides over the theme's own variables, else the
 * panel stylesheet's. A theme that could not be read (`null`) draws the
 * stylesheet's, overrides and all, as the panel's fallback applies none.
 */
export function blankLook(
  variables: Record<string, unknown> | null,
  overrides: Record<string, unknown> | undefined,
): BlankLook {
  const vars: Record<string, unknown> = variables ? { ...variables, ...overrides } : {};
  return {
    accent: String(vars.accent ?? "") || CSS_VAR_FALLBACKS.accent,
    font: String(vars.font_family ?? "") || PANEL_FONT,
  };
}

/** A project's light or dark setting and its theme id, as written: either may be absent. */
export type ThemeChoice = Partial<Pick<UISettings, "theme" | "theme_id">> | undefined;

const SETTINGS = PROJECT_VALUES.UISettings;

/** THE theme a project draws: its theme id, else `blankThemeId`'s. */
export function projectThemeId(settings: ThemeChoice): string {
  return settings?.theme_id || blankThemeId(settings);
}

/**
 * The theme a project with no theme id draws: the one the schema names for its
 * light or dark setting, a word it does not name reading as the default
 * setting's.
 */
export function blankThemeId(settings: ThemeChoice): string {
  return blankDrawn(SETTINGS.theme_id, settings?.theme);
}

/**
 * The theme list, and the theme `themeId` read from the server: again when it
 * changes and on `refetch` (the Theme Studio saved the same theme id with
 * changed contents): each control type's defaults, and the theme's own
 * variables. A theme that cannot be read shows no defaults, and its variables
 * are null, as they are until the first read answers. `id` is the theme read.
 */
export function useBuilderTheme(themeId: string) {
  const [themes, setThemes] = useState<ThemeSummary[]>([]);
  const [defaults, setDefaults] = useState<ThemeDefaults>({});
  const [variables, setVariables] = useState<Record<string, unknown> | null>(null);
  const [fetchKey, setFetchKey] = useState(0);

  const loadThemes = useCallback(() => {
    listThemes().then(setThemes).catch(() => showError("Failed to load themes"));
  }, []);
  useEffect(() => { loadThemes(); }, [loadThemes]);

  useEffect(() => {
    fetch(`${getTunnelPrefix()}/api/themes/${themeId}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((theme: ThemeFile | null) => {
        setDefaults(effectiveThemeDefaults(theme));
        setVariables(theme ? theme.variables ?? {} : null);
      })
      .catch(() => {
        setDefaults({});
        setVariables(null);
      });
  }, [themeId, fetchKey]);

  const refetch = useCallback(() => setFetchKey((k) => k + 1), []);
  return { id: themeId, themes, loadThemes, defaults, variables, refetch };
}
