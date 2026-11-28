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

A toggle presses the way its light is drawn, so a reading from a device that
is away reads as off. The panel judges that with the light's own rule
(`modules/availability.js` `readingOffline`), the server with its copy
(`ui/reading_offline.py`), and the second corpus runs both: every presence
word, a press that wakes the device or another one, and the platform's own
keys, which keep telling the truth while the device is away.
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

#: The keys a toggle can watch: one no device reports, a device's reading, a
#: child's reading, and a platform key of each, which reports on the device
#: rather than from it.
AWAY_KEYS = ["var.t", "device.amp.mute", "device.amp.zone.1.mute", "device.amp.connected", "device.amp.zone.1.online"]
#: What the device's presence word can read: no word yet, each word, and a
#: value that is no word.
PRESENCE = [_MISSING, "online", "offline", "asleep", "paused", "restarting", "connecting", "disabled",
            "waiting_for_driver", 1]
#: The devices the press wakes: none, the toggle's own, another one.
WAKES = [(), ("amp",), ("tv",)]
AWAY = [(key, word, wakes) for key in AWAY_KEYS for word in PRESENCE for wakes in WAKES]

# The corpus goes in on stdin: on the command line it is past the length
# Windows allows (WinError 206).
_SCRIPT = """
const press = await import(process.argv[1]);
const { Availability } = await import(process.argv[2]);
const { readFileSync } = await import('node:fs');
const corpus = JSON.parse(readFileSync(0, 'utf8'));
const out = { modes: [], toggles: [], away: [] };
for (const { type, bindings } of corpus.shapes) {
    out.modes.push(press.pressOf({ type, bindings: { do: { press: bindings } } }).mode);
}
for (const { type, action, state } of corpus.toggles) {
    const p = press.pressOf({ type, bindings: { do: { press: [action] } } });
    out.toggles.push(press.frameAtPress(p, (key) => state[key]));
}
// The panel's press host: the light's judgment, for this control.
for (const { action, state, wakes } of corpus.away) {
    const element = { type: 'button', wake_devices: wakes, bindings: { do: { press: [action] } } };
    const room = new Availability({ state: () => state, editMode: () => false, elementMap: () => ({}) });
    const offline = (key) => room.readingOffline({ elementDef: element, binding: { key } });
    out.away.push(press.frameAtPress(press.pressOf(element), (key) => state[key], offline));
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


def _away_action(key: str) -> dict:
    """A toggle that reads as on: its key reads its value."""
    return {**_MACRO, "mode": "toggle", "toggle_key": key, "toggle_value": True}


def _away_state(key: str, word: object) -> dict:
    state = {key: True}
    if word is not _MISSING:
        state["device.amp.presence"] = word
    return state


@pytest.fixture(scope="module")
def panel_answers() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    corpus = json.dumps({
        "shapes": [{"type": kind, "bindings": shape} for kind, shape in SHAPED],
        "toggles": [{"type": k, "action": _toggle_action(v), "state": _state(r)} for k, v, r in TOGGLES],
        "away": [{"action": _away_action(k), "state": _away_state(k, w), "wakes": list(wakes)} for k, w, wakes in AWAY],
    })
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, (PANEL_MODULES / "press.js").as_uri(),
         (PANEL_MODULES / "availability.js").as_uri()],
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


def test_a_toggle_whose_device_is_away_reads_as_off_on_both_sides(panel_answers: dict) -> None:
    differ = []
    for (key, word, wakes), got in zip(AWAY, panel_answers["away"], strict=True):
        element = _element([_away_action(key)])
        event, _why = resolve_press(element, _State(_away_state(key, word)), wakes)
        if got != f"ui.{event}":
            differ.append((key, word, wakes))
    assert not differ, f"the panel and the server disagree on these toggles: {differ}"


def test_the_away_corpus_reaches_both_verdicts(panel_answers: dict) -> None:
    """Every case reads as on unless its device is away, so each verdict
    below is the away rule's, and agreeing on the wrong one fails here."""
    verdict = dict(zip(AWAY, panel_answers["away"], strict=True))
    assert verdict[("device.amp.mute", "online", ())] == "ui.toggle_off"
    assert verdict[("device.amp.mute", _MISSING, ())] == "ui.toggle_off", "no word yet draws normally"
    assert verdict[("device.amp.mute", "offline", ())] == "ui.press", "away reads as off"
    assert verdict[("device.amp.zone.1.mute", "offline", ())] == "ui.press", "a child reads from its device"
    assert verdict[("device.amp.connected", "offline", ())] == "ui.toggle_off", "a platform key stays true"
    assert verdict[("device.amp.zone.1.online", "offline", ())] == "ui.toggle_off", "a child's own key stays true"
    assert verdict[("device.amp.mute", "asleep", ("amp",))] == "ui.toggle_off", "a press that wakes it keeps its reading"
    assert verdict[("device.amp.mute", "asleep", ("tv",))] == "ui.press", "waking another device keeps nothing"
    assert verdict[("device.amp.mute", "paused", ("amp",))] == "ui.press", "only a sleep keeps a wake press's reading"


def test_a_toggle_whose_device_is_away_says_so() -> None:
    element = _element([_away_action("device.amp.mute")])
    event, why = resolve_press(element, _State({"device.amp.mute": True, "device.amp.presence": "offline"}))
    assert event == "press"
    assert "device 'amp', which is offline" in why and "reads as off" in why, why
