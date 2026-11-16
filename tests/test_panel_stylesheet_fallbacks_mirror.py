"""The Builder shows the colours and the font the panel's stylesheet draws.

When neither the theme nor the project sets a panel colour, the panel draws
the one its stylesheet declares (the ``--panel-*`` custom properties on
``:root`` in ``web/panel/panel-elements.css``), and when neither sets a font,
the one ``web/panel/panel.css`` gives the body. The Builder shows those same
values where an author left the setting blank: the Theme Studio's colour fields
and contrast checks (``CSS_VAR_FALLBACKS`` in ``colorUtils.ts``), and Panel
Settings' blank accent and font (``blankLook`` in ``builderTheme.ts``). Two
hand-written copies in two languages: a colour changed in the stylesheet only
would show the author one colour and draw another. Each custom property is
matched to the theme variable it draws through the panel's own table
(``CSS_VARIABLES`` in ``web/panel/modules/theme.js``).

Read with a regex rather than executed, like ``test_theme_studio_names_mirror.py``:
the point is to catch one side being edited without the other.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "openavc" / "web"
ELEMENTS_CSS = WEB / "panel" / "panel-elements.css"
THEME_JS = WEB / "panel" / "modules" / "theme.js"
PANEL_CSS = WEB / "panel" / "panel.css"
UI_BUILDER = WEB / "programmer" / "src" / "components" / "ui-builder"
COLOR_UTILS = UI_BUILDER / "colorUtils.ts"
BUILDER_THEME = WEB / "programmer" / "src" / "views" / "ui-builder" / "builderTheme.ts"

#: `--panel-accent: #2196F3;` -- a colour, so the radius and sizes are left out.
_CSS_COLOUR = re.compile(r"(--panel-[a-z-]+):\s*(#[0-9A-Fa-f]{3,8})\s*;")

#: `panel_bg: '--panel-bg',` inside the panel's CSS_VARIABLES object.
_PANEL_VARIABLE = re.compile(r"""\b([a-z_]+):\s*'(--panel-[a-z-]+)'""")

#: `accent: "#2196F3",` inside the CSS_VAR_FALLBACKS object.
_TS_COLOUR = re.compile(r"""\b([a-z_]+):\s*"(#[0-9A-Fa-f]{3,8})\"""")


def _block(src: str, opener: str) -> str:
    start = src.index(opener)
    return src[start:src.index("}", start)]


def _variable_of() -> dict[str, str]:
    """Each custom property, by the theme variable the panel sets it from."""
    table = _block(THEME_JS.read_text(encoding="utf-8"), "const CSS_VARIABLES = {")
    found = {prop: variable for variable, prop in _PANEL_VARIABLE.findall(table)}
    assert found, "found no entries in CSS_VARIABLES (theme.js) -- the regex is stale"
    return found


def _stylesheet_colours() -> dict[str, str]:
    """The colour the stylesheet draws for each theme variable."""
    root = _block(ELEMENTS_CSS.read_text(encoding="utf-8"), ":root {")
    variable_of = _variable_of()
    colours = _CSS_COLOUR.findall(root)
    assert colours, "found no --panel-* colours on :root in panel-elements.css -- the regex is stale"
    unknown = sorted(prop for prop, _ in colours if prop not in variable_of)
    assert not unknown, f"no theme variable sets {unknown} (CSS_VARIABLES in theme.js)"
    return {variable_of[prop]: value.lower() for prop, value in colours}


def _builder_colours() -> dict[str, str]:
    body = _block(COLOR_UTILS.read_text(encoding="utf-8"), "export const CSS_VAR_FALLBACKS")
    found = {name: value.lower() for name, value in _TS_COLOUR.findall(body)}
    assert found, "found no colours in CSS_VAR_FALLBACKS -- the regex is stale"
    return found


def _body_font() -> str:
    src = PANEL_CSS.read_text(encoding="utf-8")
    body = _block(src, "\nhtml, body {")
    match = re.search(r"font-family:\s*([^;]+);", body)
    assert match, "found no font-family on html, body in panel.css -- the regex is stale"
    return match.group(1).strip()


def test_the_builder_shows_each_colour_the_stylesheet_draws():
    stylesheet = _stylesheet_colours()
    builder = _builder_colours()
    assert builder == stylesheet, (
        "CSS_VAR_FALLBACKS (colorUtils.ts) and the :root colours in panel-elements.css have drifted.\n"
        f"  in the stylesheet only, or drawn otherwise: {sorted(set(stylesheet.items()) - set(builder.items())) or 'none'}\n"
        f"  in the Builder only, or shown otherwise: {sorted(set(builder.items()) - set(stylesheet.items())) or 'none'}\n"
        "Update both."
    )


def test_panel_settings_names_the_font_the_stylesheet_draws():
    match = re.search(r'export const PANEL_FONT = "([^"]+)";', BUILDER_THEME.read_text(encoding="utf-8"))
    assert match, "found no PANEL_FONT in builderTheme.ts -- the regex is stale"
    assert match.group(1) == _body_font(), (
        "PANEL_FONT (builderTheme.ts) is not the body font in panel.css. Update both."
    )
