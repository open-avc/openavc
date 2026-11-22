// GENERATED FILE - DO NOT EDIT.
// Rendered from the theme rule (openavc/core/themes.py).
// Regenerate with:  python -m openavc.core.themes_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// `variables` is every variable a theme sets, in the order the panel puts them
// on: `css_property` is the custom property it becomes (null for the font,
// which goes on the page body), `fallback` what the panel draws when a theme
// leaves it out, and `kind` whether it is a `color`, a `length` (a number is
// rem) or a `font`. `settings_over_theme` is the project settings that paint
// over a theme's variables, in the order they apply: `variable` is the one
// each sets (null for `theme_overrides`, which names its own), and `blank` what
// the setting holds when it leaves the theme's own.

/** One variable a theme sets. */
export interface ThemeVariable {
  readonly name: string;
  readonly css_property: string | null;
  readonly fallback: string;
  readonly kind: "color" | "length" | "font";
}

/** One project setting that paints over a theme's variables. */
export interface SettingOverTheme {
  readonly setting: string;
  readonly variable: string | null;
  readonly blank: string | Readonly<Record<string, never>>;
}

export const THEME_VALUES = {
  "variables": [
    {
      "name": "panel_bg",
      "css_property": "--panel-bg",
      "fallback": "#1a1a2e",
      "kind": "color"
    },
    {
      "name": "panel_text",
      "css_property": "--panel-text",
      "fallback": "#ffffff",
      "kind": "color"
    },
    {
      "name": "accent",
      "css_property": "--panel-accent",
      "fallback": "#2196F3",
      "kind": "color"
    },
    {
      "name": "button_bg",
      "css_property": "--panel-button-bg",
      "fallback": "#424242",
      "kind": "color"
    },
    {
      "name": "button_text",
      "css_property": "--panel-button-text",
      "fallback": "#cccccc",
      "kind": "color"
    },
    {
      "name": "button_border",
      "css_property": "--panel-button-border",
      "fallback": "#555555",
      "kind": "color"
    },
    {
      "name": "danger",
      "css_property": "--panel-danger",
      "fallback": "#F44336",
      "kind": "color"
    },
    {
      "name": "success",
      "css_property": "--panel-success",
      "fallback": "#4CAF50",
      "kind": "color"
    },
    {
      "name": "warning",
      "css_property": "--panel-warning",
      "fallback": "#FF9800",
      "kind": "color"
    },
    {
      "name": "surface",
      "css_property": "--panel-surface",
      "fallback": "#2a2a4a",
      "kind": "color"
    },
    {
      "name": "surface_border",
      "css_property": "--panel-surface-border",
      "fallback": "#3a3a5c",
      "kind": "color"
    },
    {
      "name": "border_radius",
      "css_property": "--panel-border-radius",
      "fallback": "0.5714rem",
      "kind": "length"
    },
    {
      "name": "font_family",
      "css_property": null,
      "fallback": "'Inter', system-ui, -apple-system, sans-serif",
      "kind": "font"
    }
  ],
  "settings_over_theme": [
    {
      "setting": "theme_overrides",
      "variable": null,
      "blank": {}
    },
    {
      "setting": "accent_color",
      "variable": "accent",
      "blank": ""
    },
    {
      "setting": "font_family",
      "variable": "font_family",
      "blank": ""
    }
  ]
} as const satisfies {
  readonly variables: readonly ThemeVariable[];
  readonly settings_over_theme: readonly SettingOverTheme[];
};
