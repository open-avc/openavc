/**
 * The theme as the UI Builder shows it: the list of themes and the effective
 * default of every themed property for each control type. The canvas is the
 * panel itself, which reads the theme on its own.
 *
 * `effectiveThemeDefaults` is the Builder's copy of where the panel's
 * stylesheet gets each control's look: the theme's own `element_defaults`,
 * then the button colours for the button-like types, the accent and surface
 * for the interactive ones, the panel text colour for the rest, and the
 * corner radius and font for all. A property missing here shows blank in the
 * Properties panel instead of the value the control is drawn with, so a new
 * themed property is added here as well as in the panel's CSS. Text size is
 * not here: the canvas reports what it drew (uiBuilderStore.textDefaultsRem).
 */
import { useCallback, useEffect, useState } from "react";
import { getTunnelPrefix, listThemes } from "../../api/restClient";
import { showError } from "../../store/toastStore";

export type ThemeDefaults = Record<string, Record<string, unknown>>;

export interface ThemeSummary {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  preview_colors: string[];
  variables: Record<string, unknown>;
  source: string;
}

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

/**
 * The theme list, and the chosen theme read from the server: again when the
 * choice changes and on `refetch` (the Theme Studio saved the same theme id
 * with changed contents). A theme that cannot be read shows no defaults.
 */
export function useBuilderTheme(themeId: string | undefined) {
  const [themes, setThemes] = useState<ThemeSummary[]>([]);
  const [defaults, setDefaults] = useState<ThemeDefaults>({});
  const [fetchKey, setFetchKey] = useState(0);

  const loadThemes = useCallback(() => {
    listThemes().then(setThemes).catch(() => showError("Failed to load themes"));
  }, []);
  useEffect(() => { loadThemes(); }, [loadThemes]);

  useEffect(() => {
    const id = themeId || "dark-default";
    fetch(`${getTunnelPrefix()}/api/themes/${id}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((theme: ThemeFile | null) => {
        setDefaults(effectiveThemeDefaults(theme));
      })
      .catch(() => setDefaults({}));
  }, [themeId, fetchKey]);

  const refetch = useCallback(() => setFetchKey((k) => k + 1), []);
  return { themes, loadThemes, defaults, refetch };
}
