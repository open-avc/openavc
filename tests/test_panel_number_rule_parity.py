"""The panel's rule for what reads as a number, held to the platform's.

A gauge, slider, fader or level meter draws its placeholder for a reading that
is not a number, and decides that with `asNumber` in
`web/panel/modules/readings.js`. The platform's own rule is
`core/monitors.as_number` (mirrored by `monitorHelpers.ts` in the IDE): a
finite number, or text that spells one with spaces around it, never a boolean.
The panel's copy is run in Node over the same values as the server's, and the
two must answer alike, so a reading a monitor calls a number is never one a
gauge on the same panel draws as "--".
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.core.monitors import as_number
from tests import gates

READINGS_JS = (
    Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules" / "readings.js"
)

#: Every kind of value a reading can arrive as over the panel's socket (JSON:
#: no NaN or infinity as numbers), spelled every way the rule has to decide.
VALUES = [
    0, 1, -1, 40, 0.5, -0.04, 1e21, -1e-9, 1.0,
    "55", " 7 ", "\t7\n", ".5", "5.", "1e3", "1E-3", "-20", "+3", "-.5", "007",
    "", " ", "abc", "0x10", "Infinity", "-Infinity", "NaN", "1,000", "1_000",
    "5 dB", "1e", "e3", ".", "+", "--5", "1.2.3",
    True, False, None, [], {}, [5], {"v": 5},
]

_SCRIPT = """
const { asNumber } = await import(process.argv[1]);
const values = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(values.map((v) => asNumber(v))));
"""


@pytest.fixture(scope="module")
def panel_answers() -> list:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, READINGS_JS.as_uri(), json.dumps(VALUES)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_panel_reads_a_number_as_the_platform_does(panel_answers):
    wrong = [
        f"{value!r}: panel {panel}, platform {as_number(value)}"
        for value, panel in zip(VALUES, panel_answers)
        if panel != as_number(value)
    ]
    assert not wrong, "; ".join(wrong)


def test_the_values_reach_both_answers(panel_answers):
    """Parity over values that are all numbers, or none, proves nothing."""
    assert any(a is None for a in panel_answers)
    assert any(a is not None for a in panel_answers)
