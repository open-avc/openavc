"""The words that mean true and false: one list, asked by every reader.

``drivers/truth_words.read_truth`` is the rule. Each reader that turns a value
into a flag asks it: the three wire forms a reply arrives in, the condition
rule's boolean branch, a driver's opt-in switch, a device setting, a learned
value and the audit's effect check. One table runs through all of them, so a
reader that keeps a list of its own fails here.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.audit.effects import same_value as effect_holds
from openavc.core.condition_eval import reads_as_on, same_value
from openavc.core.event_bus import EventBus
from openavc.core.learned_config import accepted_value
from openavc.core.state_store import StateStore
from openavc.drivers import compiled_protocol
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.param_validation import (
    DeviceSettingValueError,
    validate_device_setting_value,
)
from openavc.drivers.truth_words import FALSE_WORDS, TRUE_WORDS, read_truth
from tests import gates

#: (value, what it reads as), None where it is neither: the shared corpus,
#: which the IDE's copy runs too, and what JSON cannot carry to it.
CORPUS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "truth_word_cases.json").read_text(encoding="utf-8")
)["cases"]
JSON_ROWS = [(raw, reads) for raw, reads in CORPUS]
TABLE = JSON_ROWS + [
    (0.0, False),
    (2.5, True),
    (10**400, True),
    (math.nan, None),
    (math.inf, None),
]

WORDS = [raw for raw, reads in TABLE if isinstance(raw, str) and reads is not None]
UNREAD_WORDS = [raw for raw, reads in TABLE if isinstance(raw, str) and reads is None]


def test_the_lists():
    assert TRUE_WORDS == ("on", "true", "yes", "enabled")
    assert FALSE_WORDS == ("off", "false", "no", "disabled")


@pytest.mark.parametrize("raw,reads", TABLE)
def test_the_rule(raw, reads):
    assert read_truth(raw) is reads


# ── A reply: each wire form reads a boolean variable by the rule ──


@pytest.mark.parametrize("raw,reads", [r for r in TABLE if isinstance(r[0], str)])
def test_a_text_reply(raw, reads):
    assert compiled_protocol.coerce_value(raw, "boolean") is reads


@pytest.mark.parametrize("raw,reads", TABLE)
def test_a_json_reply(raw, reads):
    assert compiled_protocol.coerce_json_value(raw, "boolean") is reads


@pytest.mark.parametrize("raw,reads", TABLE)
def test_an_osc_argument(raw, reads):
    assert compiled_protocol.coerce_osc_value(raw, "boolean") is reads


def _driver():
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "transport": "tcp",
        "default_config": {"host": "127.0.0.1", "port": 0, "poll_interval": 0},
        "state_variables": {"power": {"type": "boolean", "label": "Power"}},
        "commands": {},
        "responses": [{"match": r"PWR=(.*)", "set": {"power": "$1"}}],
    }
    cls = create_configurable_driver_class(definition)
    return cls("acme_1", {"host": "127.0.0.1", "port": 1}, StateStore(), EventBus())


async def test_enabled_reads_as_true_and_disabled_as_false():
    driver = _driver()
    await driver.on_data_received(b"PWR=enabled\r")
    assert driver.get_state("power") is True
    await driver.on_data_received(b"PWR=Disabled\r")
    assert driver.get_state("power") is False


async def test_a_word_on_neither_list_leaves_no_value_rather_than_off():
    driver = _driver()
    await driver.on_data_received(b"PWR=on\r")
    assert driver.get_state("power") is True
    await driver.on_data_received(b"PWR=standby\r")
    assert driver.get_state("power") is None


def test_a_state_gate_holds_open_only_for_a_value_that_reads():
    driver = _driver()
    declared = {"power": {"type": "boolean"}}
    driver.set_state("power", "standby")
    assert driver._state_allows(declared, "power", "maybe") is False
    driver.set_state("power", True)
    assert driver._state_allows(declared, "power", "enabled") is True
    assert driver._state_allows(declared, "power", ["maybe", "on"]) is True
    assert driver._state_allows(declared, "power", "disabled") is False


# ── A condition: the boolean branch, and a driver's opt-in switch ──


@pytest.mark.parametrize("raw,reads", TABLE)
def test_a_condition_against_true_and_false(raw, reads):
    if raw is None:
        return
    assert same_value(raw, True) is (reads is True)
    assert same_value(raw, False) is (reads is False)


@pytest.mark.parametrize("raw,reads", [r for r in TABLE if r[1] is not None])
def test_an_opt_in_switch_reads_the_words(raw, reads):
    assert reads_as_on(raw) is reads


@pytest.mark.parametrize("raw", [None, ""])
def test_an_unset_switch_is_off(raw):
    assert reads_as_on(raw) is False


def test_a_switch_set_to_a_word_on_neither_list_counts_as_set():
    assert reads_as_on("maybe") is True


# ── A device setting a person writes, and a value learned from a device ──


@pytest.mark.parametrize("raw,reads", [r for r in TABLE if r[0] is not None])
def test_a_boolean_setting(raw, reads):
    sdef = {"type": "boolean", "label": "Flag"}
    if reads is None:
        with pytest.raises(DeviceSettingValueError, match="must be true or false"):
            validate_device_setting_value("flag", sdef, raw)
    else:
        assert validate_device_setting_value("flag", sdef, raw) is reads


@pytest.mark.parametrize("raw,reads", [r for r in TABLE if r[0] is not None])
def test_a_learned_boolean(raw, reads):
    assert accepted_value({"type": "boolean"}, raw) == (
        (False, None) if reads is None else (True, reads)
    )


# ── The Device Audit: a reported flag against a command's declared effect ──


@pytest.mark.parametrize("raw", WORDS)
def test_an_effect_on_a_flag(raw):
    reads = read_truth(raw)
    assert effect_holds(True, raw) is reads
    assert effect_holds(False, raw) is (not reads)


@pytest.mark.parametrize("raw", UNREAD_WORDS)
def test_an_effect_against_a_word_on_neither_list_does_not_hold(raw):
    assert effect_holds(True, raw) is False
    assert effect_holds(False, raw) is False


# ── The panel's copy: truthOf, and the matrix's lock, run by Node ──

PANEL_MODULES = (
    Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules"
)
_PANEL_SCRIPT = """
const { pathToFileURL } = require('url');
const path = require('path');
const dir = process.argv[1];
const rows = JSON.parse(process.argv[2]);
(async () => {
    const { truthOf } = await import(pathToFileURL(path.join(dir, 'compare.js')).href);
    const { lockEngaged } = await import(pathToFileURL(path.join(dir, 'matrix.js')).href);
    const out = rows.map((raw) => [truthOf(raw), lockEngaged(raw)]);
    process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def panel_answers() -> list[list]:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "-e", _PANEL_SCRIPT, str(PANEL_MODULES),
         json.dumps([raw for raw, _ in JSON_ROWS])],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_panel_reads_the_same_words(panel_answers):
    assert [truth for truth, _ in panel_answers] == [reads for _, reads in JSON_ROWS]


def test_the_matrix_lock_reads_true_and_the_word_locked(panel_answers):
    assert [locked for _, locked in panel_answers] == [reads is True for _, reads in JSON_ROWS]
