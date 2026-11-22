"""The theme rule's table, generated into the panel and the Builder, and held
to what the panel's stylesheets and the docs say.

``openavc/core/themes.py`` declares every variable a theme sets (the custom
property it goes on, what the panel draws without it) and the project settings
that paint over a theme. ``python -m openavc.core.themes_gen`` renders that
into ``modules/theme_values.gen.js`` for the panel and ``themeValues.gen.ts``
for the Builder; this re-renders and compares both byte for byte. The panel's
stylesheets cannot import the table, so their ``:root`` variables and body
font are compared with it here, as are the docs' tables of the custom
properties a stylesheet or a custom control may read.

Same arrangement as tests/test_project_values_generated.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openavc.core.themes import SETTINGS_OVER_THEME, VARIABLES
from openavc.core.themes_gen import ARTIFACTS, render

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "openavc" / "web"

#: Custom properties the stylesheet's :root sets that are not theme variables:
#: the text size a control starts at belongs to the panel, not to a theme.
NOT_THEME_VARIABLES = {"--panel-font-size"}

#: Files that once wrote the variable list or its fallbacks themselves.
READERS = (
    WEB / "panel" / "modules" / "theme.js",
    WEB / "panel" / "modules" / "frames.js",
    WEB / "programmer" / "src" / "components" / "ui-builder" / "colorUtils.ts",
    WEB / "programmer" / "src" / "views" / "ui-builder" / "builderTheme.ts",
)


@pytest.mark.parametrize("artifact", sorted(ARTIFACTS))
def test_each_committed_artifact_matches_the_rule(artifact: str) -> None:
    path = REPO / artifact
    assert path.is_file(), f"{artifact} is missing -- run 'python -m openavc.core.themes_gen'"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render(artifact), (
        f"{artifact} does not match the theme rule -- regenerate with "
        f"'python -m openavc.core.themes_gen' (never edit it by hand)"
    )


def _declarations(css: str, selector: str) -> dict[str, str]:
    """The ``property: value`` pairs in the first rule for *selector*."""
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S), re.S)
    assert match, f"no {selector} rule"
    pairs = (line.split(":", 1) for line in match.group(1).split(";") if ":" in line)
    return {name.strip(): value.strip() for name, value in pairs}


def test_the_stylesheet_root_draws_each_variable_as_the_table_says() -> None:
    root = _declarations((WEB / "panel" / "panel-elements.css").read_text(encoding="utf-8"), ":root")
    table = {v.css_property: v.fallback for v in VARIABLES if v.css_property}
    themed = {name: value for name, value in root.items() if name not in NOT_THEME_VARIABLES}
    assert themed == table


def test_the_page_font_is_the_table_s_fallback_for_font_family() -> None:
    body = _declarations((WEB / "panel" / "panel.css").read_text(encoding="utf-8"), "body")
    (font,) = [v.fallback for v in VARIABLES if v.name == "font_family"]
    assert body["font-family"] == font


@pytest.mark.parametrize("doc", ["custom-controls.md", "ui-builder.md"])
def test_the_docs_list_every_theme_variable(doc: str) -> None:
    text = (REPO / "docs" / doc).read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `(--panel-[a-z-]+)` \|", text, flags=re.M))
    assert listed == {v.css_property for v in VARIABLES if v.css_property}


@pytest.mark.parametrize("path", READERS, ids=lambda p: p.name)
def test_no_reader_writes_the_variable_list_of_its_own(path: Path) -> None:
    """Each reader takes the list from the table: one naming half the
    variables' properties, or half their fallbacks, has a list of its own."""
    source = path.read_text(encoding="utf-8")
    assert "theme_values.gen" in source or "themeValues.gen" in source, f"{path.name} does not read the table"
    themed = [v for v in VARIABLES if v.css_property]
    properties = sorted(v.css_property for v in themed if f"'{v.css_property}'" in source or f'"{v.css_property}"' in source)
    fallbacks = sorted(v.fallback for v in themed if v.fallback in source)
    half = len(themed) // 2
    assert len(properties) < half, f"{path.name} lists {properties}; read them from the theme rule's table"
    assert len(fallbacks) < half, f"{path.name} lists {fallbacks}; read them from the theme rule's table"


def test_each_setting_over_a_theme_is_a_panel_setting_whose_blank_is_the_table_s() -> None:
    from openavc.core.project_loader import UISettings

    for entry in SETTINGS_OVER_THEME:
        field = UISettings.model_fields[entry.setting]
        blank = field.default_factory() if field.default_factory else field.default
        assert blank == entry.blank, entry.setting
        assert entry.variable is None or entry.variable in {v.name for v in VARIABLES}, entry.setting
