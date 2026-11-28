"""A press, held to the server's copy of it.

The panel decides what a press sends (`web/panel/modules/press.js`): the mode
on the first action of `do.press`, a toggle with no key read as a tap, a
camera preset a tap whatever its action names, and a toggle's verdict -- `ui.toggle_off` while its key reads as its value, words
compared apart from case, else `ui.press`. The server never sees that choice,
only the frame, so anything that checks a toggle without pressing it (the
AI's simulated press, a panel verification) asks `press_mode` and
`resolve_press` in `core/ui_events.py`, a copy of those few lines.

Executed rather than read: Node runs the panel's own `pressOf` and
`frameAtPress` over one corpus, and the answers are compared with the
server's over the same corpus.

Both ask the platform's one comparison (`core/condition_eval.py` on the
server, its copy `modules/compare.js` on the panel), so they reach the same
verdict for every toggle value and reading below: a null on either side, and
a whole number written with a decimal point (Python's 1.0, which JSON hands
JavaScript as 1), included.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openavc.core.ui_events import TAP_ONLY_TYPES, press_mode, resolve_press
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

#: The controls a press is read on: a button, one with no type, and each the
#: server reads as a tap whatever its first action names (a camera preset).
TYPES = ["button", None, *sorted(TAP_ONLY_TYPES)]
SHAPED = [(kind, shape) for kind in TYPES for shape in SHAPES]

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
for (const { type, bindings } of corpus.shapes) {
    out.modes.push(press.pressOf({ type, bindings: { do: { press: bindings } } }).mode);
}
for (const { type, action, state } of corpus.toggles) {
    const p = press.pressOf({ type, bindings: { do: { press: [action] } } });
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


TOGGLES = [(kind, value, reading) for kind in TYPES for value in TOGGLE_VALUES for reading in READINGS]


@pytest.fixture(scope="module")
def panel_answers() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    corpus = json.dumps({
        "shapes": [{"type": kind, "bindings": shape} for kind, shape in SHAPED],
        "toggles": [{"type": k, "action": _toggle_action(v), "state": _state(r)} for k, v, r in TOGGLES],
    })
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, (PANEL_MODULES / "press.js").as_uri()],
        input=corpus, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _element(press: object, kind: str | None = "button") -> SimpleNamespace:
    return SimpleNamespace(type=kind, bindings={"do": {"press": press}})


class _State:
    def __init__(self, values: dict) -> None:
        self.values = values

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


def test_the_panel_reads_a_press_mode_the_way_the_server_does(panel_answers: dict) -> None:
    for (kind, shape), got in zip(SHAPED, panel_answers["modes"], strict=True):
        assert got == press_mode(_element(shape, kind))[0], (kind, shape)


def test_the_panel_decides_a_toggle_the_way_the_server_does(panel_answers: dict) -> None:
    differ = []
    for (kind, value, reading), got in zip(TOGGLES, panel_answers["toggles"], strict=True):
        event, _why = resolve_press(_element([_toggle_action(value)], kind), _State(_state(reading)))
        if got != f"ui.{event}":
            differ.append((kind, value, reading))
    assert not differ, f"the panel and the server disagree on these toggles: {differ}"


def test_the_corpus_reaches_both_verdicts(panel_answers: dict) -> None:
    """A corpus where every toggle reads off proves nothing about the comparison."""
    verdicts = panel_answers["toggles"]
    assert verdicts.count("ui.toggle_off") >= 10 and verdicts.count("ui.press") >= 10
    assert {"tap", "hold_repeat", "tap_hold", "toggle", "double"} <= set(panel_answers["modes"])


def test_a_camera_preset_is_a_tap_on_both_sides(panel_answers: dict) -> None:
    """Its editor offers actions and no mode, so a mode written by hand or by
    the assistant is never what a press of it sends."""
    modes = {got for (kind, _shape), got in zip(SHAPED, panel_answers["modes"], strict=True) if kind == "camera_preset"}
    assert modes == {"tap"}
    on = _element([_toggle_action("on")], "camera_preset")
    assert resolve_press(on, _State({"var.t": "on"})) == ("press", None)
