"""A saved project and a Builder draft draw the same bounded values.

The panel draws two kinds of project: a saved one, which the format migration
and the loader have read, and the Builder's unsaved draft, which neither has.
``web/panel/modules/project_values.js`` reads the project schema's generated
table for both. This holds what the panel draws for a value to what the loader
keeps of the same value after the migration, value for value, so a draft
previews what saving it would. The two durations' 0 is the case worth
watching: the migration removes it and the panel reads it as blank, so both
end at the default rather than at the bottom of the range.

Values the loader refuses outright (a fraction, words) are not here: a saved
project never holds one, and the panel's answer for a draft that does is
pinned in tests/fixtures/panel_harness.cjs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from openavc.core.project_loader import UIElement, UISettings
from openavc.core.project_migration import migrate_0_16_to_0_17
from tests import gates

MODULE = (
    Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules" / "project_values.js"
)

#: Stands for a value nobody wrote: the key is left out.
ABSENT = "<absent>"

#: The panel settings the panel reads through the table, each over the same
#: values. A null is not one: the loader refuses it for these, where a
#: control's decimals take it as unset.
SETTINGS = ("page_transition_duration", "element_stagger_ms")
SETTING_VALUES = [ABSENT, 0, -5, 5, 10, 45, 350, 1000, 1001, 99999]

DECIMAL_VALUES = [ABSENT, None, 0, -3, 1, 3, 20, 21, 25, 101, 999]

_SCRIPT = """
const { boundedNumber, panelSetting } = await import(process.argv[1]);
const { settings, decimals } = JSON.parse(process.argv[2]);
const absent = (value) => value === '<absent>';
const out = { settings: {}, decimals: [] };
for (const [field, values] of Object.entries(settings)) {
    out.settings[field] = values.map((value) => panelSetting(absent(value) ? {} : { [field]: value }, field));
}
out.decimals = decimals.map((value) => boundedNumber('UIElement', 'display_decimals', absent(value) ? undefined : value));
console.log(JSON.stringify(out));
"""


def _drawn() -> dict:
    """What the panel's reader gives for every case, run under Node."""
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    cases = {"settings": {field: SETTING_VALUES for field in SETTINGS}, "decimals": DECIMAL_VALUES}
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, MODULE.as_uri(), json.dumps(cases)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _saved_project(settings: dict, element: dict) -> dict:
    """A project an earlier release saved, as the migration brings it to this one."""
    data = {"ui": {"settings": settings, "master_elements": [], "pages": [{"id": "p", "elements": [element]}]}}
    return migrate_0_16_to_0_17(data)


def _saved_setting(field: str, value) -> object:
    settings = {} if value == ABSENT else {field: value}
    migrated = _saved_project(settings, {"id": "x", "type": "slider"})
    return getattr(UISettings.model_validate(migrated["ui"]["settings"]), field)


def _saved_decimals(value) -> object:
    element = {"id": "x", "type": "slider"}
    if value != ABSENT:
        element["display_decimals"] = value
    migrated = _saved_project({}, element)
    return UIElement.model_validate(migrated["ui"]["pages"][0]["elements"][0]).display_decimals


def test_a_draft_draws_each_panel_setting_as_the_saved_project_keeps_it() -> None:
    drawn = _drawn()["settings"]
    for field in SETTINGS:
        saved = [_saved_setting(field, value) for value in SETTING_VALUES]
        assert drawn[field] == saved, f"{field} over {SETTING_VALUES}"


def test_a_draft_draws_shown_decimals_as_the_saved_project_keeps_them() -> None:
    saved = [_saved_decimals(value) for value in DECIMAL_VALUES]
    assert _drawn()["decimals"] == saved, f"display_decimals over {DECIMAL_VALUES}"


def test_the_reader_writes_no_range_or_default_of_its_own() -> None:
    """Every number it holds a value to comes from the generated table."""
    source = MODULE.read_text(encoding="utf-8")
    assert "from './project_values.gen.js'" in source
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("*", "/**", "//")))
    stray = [token for token in ("200", "30", "20", "1000", "fade-up", "'none'") if token in code]
    assert not stray, f"the panel's reader writes its own {stray}; read it from PROJECT_VALUES"
