"""A slider's and a fader's output range, held across the server and the panel.

A slider or fader with an output range sends its display value; the server
stretches it onto the device's range (`UIEventRuntime.scale_value_forward` in
`core/ui_events.py`). The device's reading comes back, and the panel places it
on the control through its own copy of the rule, read backwards
(`LevelScale` in `web/panel/modules/level.js`). The two are one rule in two
languages: where they disagree, the control sends one number and then draws
another, and it jumps when the device echoes the value back.

One corpus, `tests/fixtures/level_scale_cases.json`, holds both halves. Each
case is a control's definition, the value it sends at each end of its travel,
and pairs of a display value and the device value it becomes. The server's
function turns each display value into its device value (compared by repr, so
the whole number an untyped command parameter needs is held too); Node builds
the panel's own control from the same definition and turns each device value
back into the display value it draws. Together they are the round trip,
display to device to display, and a change to either half fails it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.core.project_loader import UIElement
from openavc.core.ui_events import UIEventRuntime
from tests import gates

ROOT = Path(__file__).resolve().parents[1]
LEVEL_JS = ROOT / "openavc" / "web" / "panel" / "modules" / "level.js"
CASES = json.loads((ROOT / "tests" / "fixtures" / "level_scale_cases.json").read_text(encoding="utf-8"))["cases"]
NAMES = [case["name"] for case in CASES]

# The panel's half: each control built the way the panel builds it, asked
# what it sends at each end of its travel and where it draws a reading. The
# corpus goes in on stdin, as the other parity corpora do.
_SCRIPT = """
const level = await import(process.argv[1]);
const { readFileSync } = await import('node:fs');
const cases = JSON.parse(readFileSync(0, 'utf8'));
const host = { sendValue() {}, keepTimer() {}, read() {}, design: () => false, offline: () => false };
const out = {};
for (const { name, element, pairs } of cases) {
    const def = { id: 'c', ...element };
    const control = def.type === 'fader'
        ? new level.FaderControl(def, {}, host)
        : new level.SliderControl(def, host);
    const ends = def.type === 'fader'
        ? [control.valueAtTravel(0), control.valueAtTravel(1)]
        : [control.valueAtPosition(0), control.valueAtPosition(control.steps)];
    out[name] = { ends, back: pairs.map(([, device]) => control.readingOf(device)) };
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def panel_answers() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, LEVEL_JS.as_uri()],
        input=json.dumps(CASES), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _sent(case: dict, display: object) -> object:
    return UIEventRuntime.scale_value_forward(UIElement(id="c", **case["element"]), display)


@pytest.mark.parametrize("case", CASES, ids=NAMES)
def test_the_server_sends_each_display_value_as_its_device_value(case: dict) -> None:
    got = [(display, _sent(case, display)) for display, _device in case["pairs"]]
    assert [repr(device) for _display, device in got] == [repr(device) for _d, device in case["pairs"]], got


@pytest.mark.parametrize("case", CASES, ids=NAMES)
def test_the_panel_sends_the_ends_and_draws_each_device_value_back(case: dict, panel_answers: dict) -> None:
    answer = panel_answers[case["name"]]
    assert answer["ends"] == pytest.approx(case["ends"]), "what the control sends at each end of its travel"
    # Only the server rounds (to nine places), so the panel's stretch back
    # lands within float noise of the display value, never further.
    displays = [display for display, _device in case["pairs"]]
    assert answer["back"] == pytest.approx(displays, rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("case", CASES, ids=NAMES)
def test_every_pair_is_a_value_the_control_can_send(case: dict) -> None:
    low, high = sorted(case["ends"])
    assert all(low <= display <= high for display, _device in case["pairs"]), case["pairs"]


def test_the_corpus_reaches_every_form_of_the_rule() -> None:
    """A corpus of stretched sliders alone proves nothing about the dead space or a flat range."""
    elements = [case["element"] for case in CASES]
    assert {el["type"] for el in elements} == {"slider", "fader"}
    stretched = [el for el in elements if "output_min" in el and el.get("scale_to_full") is not False]
    held = [el for el in elements if el.get("scale_to_full") is False]
    assert stretched and held and any("output_min" not in el for el in elements)
    assert any(el["output_min"] == el["output_max"] for el in stretched + held)
    assert any(el["min"] == el["max"] for el in elements if "min" in el)
    assert any(el["output_min"] > el["output_max"] for el in stretched)
