"""The theme a project with no theme id draws is the schema's, everywhere.

``UISettings`` in ``openavc/core/project_loader.py`` decides it: the project's
``theme_id``, else the theme its light or dark ``theme`` names, a word it does
not name reading as the default ``theme``. The generator renders that into the
project-value table, and the panel (``web/panel/modules/project_values.js``,
for a saved project and a Builder draft alike) and the Builder
(``views/ui-builder/builderTheme.ts``) read it there. Before, the panel drew a
light project with no id in Light Modern while the Builder's theme defaults,
its theme list and the Theme Studio all said Dark Default.

One case table (``tests/fixtures/blank_theme_cases.json``) runs through the
schema and the panel here, and through the Builder in ``builderTheme.test.ts``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.core.project_loader import UISettings
from openavc.core.project_values_gen import project_values
from tests import gates

REPO = Path(__file__).resolve().parents[1]
CASES = json.loads((REPO / "tests" / "fixtures" / "blank_theme_cases.json").read_text(encoding="utf-8"))["cases"]
PANEL_MODULES = REPO / "openavc" / "web" / "panel" / "modules"
BUILDER = REPO / "openavc" / "web" / "programmer" / "src"

#: Every place that names the theme a project draws, none of which may write a
#: theme id of its own: the panel's theme and its table reader, and the
#: Builder's theme read, page, dialogs, Properties panel and Theme Studio.
READERS = (
    PANEL_MODULES / "theme.js",
    PANEL_MODULES / "project_values.js",
    BUILDER / "views" / "ui-builder" / "builderTheme.ts",
    BUILDER / "views" / "UIBuilderView.tsx",
    BUILDER / "views" / "ui-builder" / "BuilderDialogs.tsx",
    BUILDER / "views" / "ui-builder" / "BuilderProperties.tsx",
    BUILDER / "components" / "ui-builder" / "PropertiesPanel.tsx",
    BUILDER / "components" / "ui-builder" / "ThemeStudio.tsx",
)

_PANEL_SCRIPT = """
const { themeIdOf } = await import(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map((c) => themeIdOf(c.settings))));
"""


@pytest.mark.parametrize("case", [c for c in CASES if not c.get("draft_only")], ids=lambda c: c["name"])
def test_the_schema_answers_each_case(case: dict) -> None:
    assert UISettings.model_validate(case["settings"]).drawn_theme_id() == case["expected"]


def test_the_project_keeps_a_blank_id_blank() -> None:
    """What is drawn is answered, never written back: a light project saved
    with no id stays one, and follows its light or dark setting."""
    settings = UISettings.model_validate({"theme": "light"})
    assert settings.theme_id == ""
    assert settings.model_dump()["theme_id"] == ""


def test_the_panel_draws_each_case_as_the_schema_says() -> None:
    """Run under Node: what modules/project_values.js gives for every case,
    the drafts the loader would refuse among them."""
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _PANEL_SCRIPT,
         (PANEL_MODULES / "project_values.js").as_uri(), json.dumps(CASES)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    drawn = json.loads(proc.stdout)
    assert {c["name"]: d for c, d in zip(CASES, drawn)} == {c["name"]: c["expected"] for c in CASES}


def test_the_panel_asks_the_table_reader_for_its_theme() -> None:
    source = (PANEL_MODULES / "theme.js").read_text(encoding="utf-8")
    assert "themeIdOf" in source and "from './project_values.js'" in source
    assert "function themeIdOf" not in source, "theme.js decides the theme itself; ask modules/project_values.js"


def test_the_table_carries_the_rule() -> None:
    entry = project_values()["UISettings"]["theme_id"]
    assert entry == {
        "default": "", "blank_from": {"theme": {"light": "light-modern", "dark": "dark-default"}}, "otherwise": "dark",
    }


def test_every_theme_the_rule_gives_is_built_in() -> None:
    """A project with no id draws a theme every install has."""
    _, by_theme = UISettings.BLANK_FROM["theme_id"]
    for theme_id in by_theme.values():
        assert (REPO / "openavc" / "themes" / f"{theme_id}.json").is_file(), theme_id
    assert UISettings.model_fields["theme"].default in by_theme


def _code_lines(path: Path) -> list[str]:
    comment = ("*", "/*", "//", "{/*")
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith(comment)]


@pytest.mark.parametrize("path", READERS, ids=lambda p: p.name)
def test_no_reader_writes_a_theme_id_of_its_own(path: Path) -> None:
    """The theme ids the rule gives are written once, in the schema."""
    _, by_theme = UISettings.BLANK_FROM["theme_id"]
    stray = sorted({theme_id for line in _code_lines(path) for theme_id in by_theme.values() if theme_id in line})
    assert not stray, f"{path.name} writes {stray}; read the theme a project draws from the project-value table"
