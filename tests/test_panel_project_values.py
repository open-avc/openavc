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

from openavc.core.project_loader import PANEL_PIN_MAX_DIGITS, UIElement, UISettings
from openavc.core.project_migration import migrate_0_16_to_0_17
from tests import gates

MODULES = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules"
MODULE = MODULES / "project_values.js"

#: Stands for a value nobody wrote: the key is left out.
ABSENT = "<absent>"

#: The panel settings the panel reads through the table, each over the same
#: values. A null is not one: the loader refuses it for these, where a
#: control's decimals take it as unset.
SETTINGS = ("page_transition_duration", "element_stagger_ms")
SETTING_VALUES = [ABSENT, 0, -5, 5, 10, 45, 350, 1000, 1001, 99999]

#: The panel settings with a blank and no range, each over its own values, and
#: the module that reads it.
BLANKS = {
    "idle_page": ([ABSENT, "", "lobby"], "lock.js"),
    "show_error_messages": ([ABSENT, True, False], "failure_band.js"),
}

DECIMAL_VALUES = [ABSENT, None, 0, -3, 1, 3, 20, 21, 25, 101, 999]

#: A PIN as it might be typed at the lock screen, or saved before the schema
#: held it to what that screen takes.
PINS = [
    "", "0", "0123", "123456", "1234567", "12345678901", "12 34", " 1234 ",
    "1234\n", "12a4", "é12", "pässwörd", "١٢٣٤", "1-2-3-4-5-6-7",
]

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
    settings = {field: SETTING_VALUES for field in SETTINGS}
    settings.update({field: values for field, (values, _) in BLANKS.items()})
    cases = {"settings": settings, "decimals": DECIMAL_VALUES}
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, MODULE.as_uri(), json.dumps(cases)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


_PIN_SCRIPT = """
const { longestText, typedText } = await import(process.argv[1]);
const pins = JSON.parse(process.argv[2]);
console.log(JSON.stringify({
    longest: longestText('UISettings', 'lock_code'),
    typed: pins.map((pin) => typedText('UISettings', 'lock_code', pin)),
}));
"""


def _typed_pins() -> dict:
    """What the lock screen's box keeps of each PIN typed into it, run under Node."""
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _PIN_SCRIPT, MODULE.as_uri(), json.dumps(PINS)],
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


def test_a_draft_reads_each_blank_setting_as_the_saved_project_keeps_it() -> None:
    """Empty text is blank, as the panel and Panel Settings read it: the
    loader keeps it as written, and both draw the default it fills for an
    absent one."""
    drawn = _drawn()["settings"]
    for field, (values, _) in BLANKS.items():
        saved = [_saved_setting(field, ABSENT if value == "" else value) for value in values]
        assert drawn[field] == saved, f"{field} over {values}"


def test_the_panel_reads_each_blank_setting_through_the_table() -> None:
    """The idle page and the failure message are read by panelSetting, not
    filled with a default the module writes itself."""
    for field, (_, module) in BLANKS.items():
        source = (MODULES / module).read_text(encoding="utf-8")
        assert "panelSetting(" in source and f"'{field}')" in source, (
            f"{module} reads {field} without panelSetting (modules/project_values.js)"
        )


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


def test_a_pin_typed_at_the_lock_screen_is_what_an_older_saved_one_becomes() -> None:
    """The box keeps the digits 0 to 9 typed, as many as a PIN holds; a PIN
    an earlier release saved becomes what the box keeps of it typed, so the
    PIN its integrator knows unlocks the panel."""
    saved = [_saved_setting("lock_code", pin) for pin in PINS]
    assert _typed_pins()["typed"] == saved, f"lock_code over {PINS}"


def test_the_lock_screen_builds_its_box_from_the_table() -> None:
    assert _typed_pins()["longest"] == PANEL_PIN_MAX_DIGITS
    source = (MODULES / "lock.js").read_text(encoding="utf-8")
    assert "longestText('UISettings', 'lock_code')" in source
    assert 'maxlength="${PIN_LENGTH}"' in source
    assert "typedText('UISettings', 'lock_code', input.value)" in source
    assert 'maxlength="6"' not in source


#: A press's timings, each over the same values: one written, the blanks, and
#: values that are no number of milliseconds (a negative one, words).
TIMINGS = ("hold_repeat_ms", "hold_threshold_ms")
TIMING_VALUES = [ABSENT, None, 0, "", 150, 2500, "300", -5, "-5", "abc", True]
#: The mode whose press reads each timing.
TIMING_MODES = {"hold_repeat_ms": ("hold_repeat", "repeatMs"), "hold_threshold_ms": ("tap_hold", "thresholdMs")}

_PRESS_SCRIPT = """
const { pressOf } = await import(process.argv[1]);
const { timings, modes } = JSON.parse(process.argv[2]);
const out = {};
for (const [field, values] of Object.entries(timings)) {
    const [mode, read] = modes[field];
    out[field] = values.map((value) => {
        const action = { action: 'macro', macro: 'm', mode };
        if (value !== '<absent>') action[field] = value;
        return pressOf({ type: 'button', bindings: { do: { press: [action] } } })[read];
    });
}
console.log(JSON.stringify(out));
"""


def _pressed_timings() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    cases = {"timings": {field: TIMING_VALUES for field in TIMINGS}, "modes": TIMING_MODES}
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _PRESS_SCRIPT, (MODULES / "press.js").as_uri(), json.dumps(cases)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_a_press_reads_its_timings_as_the_schema_declares_them() -> None:
    """A timing that is a number of milliseconds above 0 is kept; any other is
    the schema's default. A negative one or words would repeat a held press
    as fast as the browser runs a timer, or make every press a long one. The
    press reads it through the table, so the Builder's fields, which read the
    same table, show what a press does."""
    declared = UIElement.KEY_DEFAULTS["bindings"]
    pressed = _pressed_timings()
    for field in TIMINGS:
        default = declared[f"do.press.{field}"]
        expected = [_milliseconds(value) or default for value in TIMING_VALUES]
        assert pressed[field] == expected, f"{field} over {TIMING_VALUES}"


def _milliseconds(value) -> float | None:
    """A timing as a number above 0, or None."""
    if isinstance(value, bool) or value in (ABSENT, None):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number > 0 else None


def test_the_press_writes_no_timing_of_its_own() -> None:
    source = (MODULES / "press.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("*", "/**", "//")))
    assert "pressTiming(" in code, "press.js reads its timings without pressTiming (modules/project_values.js)"
    stray = [token for token in ("200", "500") if token in code]
    assert not stray, f"press.js writes its own {stray}; read it from PROJECT_VALUES"

