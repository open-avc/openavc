"""A button's press, held to the server's copy of it.

The panel decides what a press sends (`web/panel/modules/press.js`): the mode
on the first action of `do.press`, a toggle with no key read as a tap, and a
toggle's verdict -- `ui.toggle_off` while its key reads as its value, words
compared apart from case, else `ui.press`. The server never sees that choice,
only the frame, so anything that checks a toggle without pressing it (the
AI's simulated press, a panel verification) asks `press_mode` and
`resolve_press` in `core/ui_events.py`, a copy of those few lines.

Executed rather than read: Node runs the panel's own `pressOf` and
`frameAtPress` over one corpus, and the answers are compared with the
server's over the same corpus.

The two sides disagree in two places today, both asserted below so a fix on
either side shows up here:
- a null, on either side of the comparison: the panel compares it as the
  word "null", so a toggle whose value is null (or "null") reads as on while
  its key reads null; the server reads a null as off;
- a whole number written with a decimal point: Python spells 1.0 "1.0",
  JavaScript "1", so a reading of 1.0 is the toggle value "1" to the panel
  and not to the server, and the other way round for a reading of "1.0".
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openavc.core.ui_events import press_mode, resolve_press
from tests import gates

PANEL_MODULES = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules"

_MACRO = {"action": "macro", "macro": "m1"}
_MISSING = object()

#: Press bindings of every shape a project can hold, one per mode.
SHAPES = [
    [], None, [None], "x", {**_MACRO}, [{**_MACRO}],
    [{**_MACRO, "mode": "tap"}], [{**_MACRO, "mode": "hold_repeat"}], [{**_MACRO, "mode": "tap_hold"}],
    [{**_MACRO, "mode": "double"}], [{**_MACRO, "mode": ""}],
    [{**_MACRO, "mode": "toggle"}], [{**_MACRO, "mode": "toggle", "toggle_key": ""}],
    [{**_MACRO, "mode": "toggle", "toggle_key": "var.t", "toggle_value": "on"}],
    {**_MACRO, "mode": "toggle", "toggle_key": "var.t", "toggle_value": "on"},
    [{**_MACRO, "mode": "tap"}, {**_MACRO, "mode": "toggle", "toggle_key": "var.t"}],
]

#: The values a toggle is written to mean on, and what its key can read.
TOGGLE_VALUES = ["on", "ON", True, False, 1, 0, "1", "true", "", None, "null", 1.0, _MISSING]
READINGS = ["on", "On", "off", True, False, 1, 0, 1.0, "1", "1.0", "true", "True", "", None, "null", _MISSING]

# The corpus goes in on stdin: on the command line it is past the length
# Windows allows (WinError 206).
_SCRIPT = """
const press = await import(process.argv[1]);
const { readFileSync } = await import('node:fs');
const corpus = JSON.parse(readFileSync(0, 'utf8'));
const out = { modes: [], toggles: [] };
for (const bindings of corpus.shapes) {
    out.modes.push(press.pressOf({ bindings: { do: { press: bindings } } }).mode);
}
for (const { action, state } of corpus.toggles) {
    const p = press.pressOf({ bindings: { do: { press: [action] } } });
    out.toggles.push(press.frameAtPress(p, (key) => state[key]));
}
console.log(JSON.stringify(out));
"""


def _toggle_action(value: object) -> dict:
    action = {**_MACRO, "mode": "toggle", "toggle_key": "var.t"}
    if value is not _MISSING:
        action["toggle_value"] = value
    return action


def _state(reading: object) -> dict:
    return {} if reading is _MISSING else {"var.t": reading}


TOGGLES = [(value, reading) for value in TOGGLE_VALUES for reading in READINGS]


@pytest.fixture(scope="module")
def panel_answers() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    corpus = json.dumps({
        "shapes": SHAPES,
        "toggles": [{"action": _toggle_action(v), "state": _state(r)} for v, r in TOGGLES],
    })
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, (PANEL_MODULES / "press.js").as_uri()],
        input=corpus, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _element(press: object) -> SimpleNamespace:
    return SimpleNamespace(bindings={"do": {"press": press}})


class _State:
    def __init__(self, values: dict) -> None:
        self.values = values

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


def _null_on_a_side(value: object, reading: object) -> bool:
    """The panel compares a null as the word "null"; the server reads it as off."""
    return value is None and reading is not _MISSING or (reading is None and value is not _MISSING)


def _decimal_point_spelling(value: object, reading: object) -> bool:
    """A whole number with a decimal point is "1.0" in Python and "1" in JavaScript."""
    return any(isinstance(x, float) and x.is_integer() for x in (value, reading)) or "1.0" in (value, reading)


def test_the_panel_reads_a_press_mode_the_way_the_server_does(panel_answers: dict) -> None:
    for shape, got in zip(SHAPES, panel_answers["modes"], strict=True):
        assert got == press_mode(_element(shape))[0], shape


def test_the_panel_decides_a_toggle_the_way_the_server_does(panel_answers: dict) -> None:
    differ = []
    for (value, reading), got in zip(TOGGLES, panel_answers["toggles"], strict=True):
        event, _why = resolve_press(_element([_toggle_action(value)]), _State(_state(reading)))
        if got != f"ui.{event}":
            differ.append((value, reading))
    unexplained = [
        pair for pair in differ
        if not (_null_on_a_side(*pair) or _decimal_point_spelling(*pair))
    ]
    assert not unexplained, f"the panel and the server disagree on these toggles: {unexplained}"
    # Guard the guard: both named disagreements are still there, so a fix on
    # either side updates the docstring and this test rather than passing it by.
    assert any(_null_on_a_side(*pair) for pair in differ)
    assert any(_decimal_point_spelling(*pair) and not _null_on_a_side(*pair) for pair in differ)


def test_the_corpus_reaches_both_verdicts(panel_answers: dict) -> None:
    """A corpus where every toggle reads off proves nothing about the comparison."""
    verdicts = panel_answers["toggles"]
    assert verdicts.count("ui.toggle_off") >= 10 and verdicts.count("ui.press") >= 10
    assert {"tap", "hold_repeat", "tap_hold", "toggle", "double"} <= set(panel_answers["modes"])
