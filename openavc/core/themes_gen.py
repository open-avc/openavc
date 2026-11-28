"""Render the theme rule's table into the panel and the Builder.

The variables a theme sets (the CSS custom property each goes on, what the
panel draws when a theme leaves one out) and the project settings that paint
over a theme are written once, in ``openavc/core/themes.py``. This writes that
table to ``openavc/web/panel/modules/theme_values.gen.js`` (the panel) and
``openavc/web/programmer/src/api/themeValues.gen.ts`` (the Builder).

Data only. Run:  python -m openavc.core.themes_gen
A test compares both committed files against a fresh render, so a variable
changed without regenerating, or a file edited by hand, fails CI.
"""

from __future__ import annotations

import json

from openavc.core.themes import theme_values
from openavc.utils.generated import write_artifacts

TS_ARTIFACT = "openavc/web/programmer/src/api/themeValues.gen.ts"
PANEL_ARTIFACT = "openavc/web/panel/modules/theme_values.gen.js"

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the theme rule (openavc/core/themes.py).
// Regenerate with:  python -m openavc.core.themes_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// `variables` is every variable a theme sets, in the order the panel puts them
// on: `css_property` is the custom property it becomes (null for the font,
// which goes on the page body), `fallback` what the panel draws when a theme
// leaves it out, and `kind` whether it is a `color`, a `length` (a number is
// rem) or a `font`. `light_fallback` is what the panel draws over those
// fallbacks for a light project whose theme cannot be had: the light built-in
// theme's variables. `settings_over_theme` is the project settings that paint
// over a theme's variables, in the order they apply: `variable` is the one
// each sets (null for `theme_overrides`, which names its own), and `blank` what
// the setting holds when it leaves the theme's own.
"""

TS_BODY = """
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

export const THEME_VALUES = %(table)s as const satisfies {
  readonly variables: readonly ThemeVariable[];
  readonly light_fallback: Readonly<Record<string, string | number>>;
  readonly settings_over_theme: readonly SettingOverTheme[];
};
"""

PANEL_BODY = """
export const THEME_VALUES = %(table)s;
"""

_BODIES = {TS_ARTIFACT: TS_BODY, PANEL_ARTIFACT: PANEL_BODY}

#: The committed files, each rendered from the same table.
ARTIFACTS = tuple(_BODIES)


def render(artifact: str) -> str:
    table = json.dumps(theme_values(), indent=2, ensure_ascii=False)
    return BANNER + _BODIES[artifact] % {"table": table}


def main() -> int:
    return write_artifacts(ARTIFACTS, render)


if __name__ == "__main__":
    raise SystemExit(main())
