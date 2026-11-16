"""Serial line settings: one table, every spelling read the same everywhere.

``SERIAL_LINE_SETTINGS`` and ``read_line_setting`` in ``openavc/drivers/serial_line.py``
are THE rule for what a baud rate, data bits, parity, stop bits and flow
control may be and how each way of writing one reads (``Even``, ``even`` and
``E`` are one parity; ``None`` is no parity; ``"1"`` is one stop bit). The
transport ladder opens the port with it, the checker judges a driver's
``default_config`` with it, the bridges are handed the settings it read, and
the Programmer's Builder and device form read the generated copy through
``serialLine.ts``. Both readers run one corpus here and are compared answer
for answer, and the tripwire at the end fails a module that keeps a table of
its own.

Every value here is invented. This tests a platform rule.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

from openavc.drivers.serial_line import (
    SERIAL_LINE_SETTINGS,
    read_line_setting,
    with_line_settings_read,
)

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = OPENAVC_ROOT / "tests" / "fixtures" / "serial_line_cases.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "serial_line_parity_harness.cjs"
PROGRAMMER_SRC = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src"
SERIAL_LINE_TS = PROGRAMMER_SRC / "components" / "shared" / "serialLine.ts"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"


def _same(got: object, expected: object) -> bool:
    """Equal, and the same kind: 1 stop bit is an int, 1.5 a float."""
    return got == expected and type(got) is type(expected)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus_case(case):
    got = read_line_setting(case["key"], case["value"])
    assert _same(got, case["expected"]), (case["name"], got)


def test_the_corpus_reaches_every_choice_and_a_value_with_no_reading():
    """Parity over cases that never take a branch passes for free."""
    for key, setting in SERIAL_LINE_SETTINGS.items():
        cases = [c for c in CASES if c["key"] == key]
        assert any(c["expected"] is None for c in cases), f"{key}: no value with no reading"
        for choice in setting.get("choices", ()):
            # Reached from another way of writing it: a word, text, a whole float.
            assert any(
                _same(c["expected"], choice["value"]) and not _same(c["value"], choice["value"])
                for c in cases
            ), (key, choice["value"])
    assert any(c["value"] is None for c in CASES), "no null"
    assert any(c["value"] is True for c in CASES), "no true read as a number"
    assert any(isinstance(c["value"], float) and c["value"].is_integer() for c in CASES), "no whole float"


def test_each_default_and_each_offered_baud_reads_as_itself():
    for key, setting in SERIAL_LINE_SETTINGS.items():
        assert _same(read_line_setting(key, setting["default"]), setting["default"]), key
    for baud in SERIAL_LINE_SETTINGS["baudrate"]["offered"]:
        assert _same(read_line_setting("baudrate", baud), baud)


def test_each_refusal_names_every_choice():
    """The words a refusal says a setting must be cover what the table takes:
    a choice added to the table and left out of its sentence is caught here."""
    for key, setting in SERIAL_LINE_SETTINGS.items():
        for choice in setting.get("choices", ()):
            assert str(choice["value"]) in setting["allowed"], (key, choice["value"])


def test_the_settings_a_bridge_is_handed_are_read_by_the_table():
    config = {
        "bridge": "b1",
        "bridge_port": "serial:1",
        "baudrate": "19200",
        "parity": "Even",
        "stopbits": "1",
        "flow_control": "RTS/CTS",
        "bytesize": "X",
    }
    read = with_line_settings_read(config)
    assert read == {
        "bridge": "b1",
        "bridge_port": "serial:1",
        "baudrate": 19200,
        "parity": "E",
        "stopbits": 1,
        "flow_control": "hardware",
        # No reading: handed on as written, for the bridge to refuse in its words.
        "bytesize": "X",
    }
    assert config["parity"] == "Even", "the device's own config is not changed"
    assert with_line_settings_read({"host": "192.0.2.5"}) == {"host": "192.0.2.5"}


# --- the Programmer gives the same answers ---------------------------------------


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not (NODE_MODULES / "esbuild").is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not SERIAL_LINE_TS.is_file():
        return "serialLine.ts missing"
    return None


@pytest.fixture(scope="module")
def programmer_answers() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(SERIAL_LINE_TS), str(CORPUS_PATH)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"serial line parity harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    return json.loads(proc.stdout)["answers"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_programmer_gives_the_same_answer(case, programmer_answers):
    expected = read_line_setting(case["key"], case["value"])
    assert _same(programmer_answers[case["name"]], expected), case["name"]


# --- the tripwire -------------------------------------------------------------
#
# A module with its own list of parities, flow-control words or baud rates is a
# second table, and the two drift the first time either is edited: that is how
# the device form came to offer three parities while the port took five.

PY_PATTERNS = (
    re.compile(r"""["']rts/?cts["']""", re.IGNORECASE),
    re.compile(r"""["']xon/?xoff["']""", re.IGNORECASE),
    re.compile(r"""\(\s*["']N["']\s*,\s*["']E["']"""),
)
TS_PATTERNS = (
    re.compile(r"RTS/CTS|XON/XOFF"),
    re.compile(r"""value=["'][NEOMS]["']\s*>\s*(?:None|Even|Odd|Mark|Space)"""),
    re.compile(r"""\[\s*["']?1200["']?\s*,"""),
    re.compile(r"""\[\s*["']N["']\s*,\s*["']None["']\s*\]"""),
)
#: The table itself, and its generated copy.
PY_OWNER = OPENAVC_ROOT / "openavc" / "drivers" / "serial_line.py"
TS_OWNER = PROGRAMMER_SRC / "api" / "types.gen.ts"


def _hits(root: Path, suffixes: tuple[str, ...], patterns: tuple[re.Pattern[str], ...], owner: Path) -> list[str]:
    hits = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in suffixes or path == owner or "node_modules" in path.parts:
            continue
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{path.relative_to(OPENAVC_ROOT)}:{line}: {match.group(0)}")
    return hits


def test_no_server_module_keeps_its_own_line_setting_table():
    assert _hits(OPENAVC_ROOT / "openavc", (".py",), PY_PATTERNS, PY_OWNER) == []


def test_no_programmer_module_keeps_its_own_line_setting_table():
    assert _hits(PROGRAMMER_SRC, (".ts", ".tsx"), TS_PATTERNS, TS_OWNER) == []
