"""A query line that names a declared command runs as that command.

A poll, start-up, follow-up or read-back line that is the name of one of the
driver's commands is sent as that command (its framing, its fill, its reply
rules); any other line goes out as its own text. One rule
(``driver_model.runs_as_command``) answers it for the runtime's sender, the
checker's fill, the simulator's coverage and the Device Audit's status
queries, so the audit calls a command a status query exactly when the poll
runs it. An invented device (Acme) throughout.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import openavc
from openavc.audit.command_catalog import command_catalog
from openavc.audit.driver_reading import reading_of
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_model import runs_as_command

_DEFINITION = {
    "id": "acme_widget", "name": "Acme Widget", "manufacturer": "Acme",
    "category": "utility", "transport": "tcp",
    "commands": {
        "power_query": {"label": "Power?", "send": "PWR?"},
        "set_power": {"label": "Power", "send": "PWR {on}", "params": {"on": {"type": "boolean"}}},
    },
    "polling": {"queries": ["power_query", "INPUT?"]},
}


@pytest.mark.parametrize("commands,text,runs", [
    ({"power_query": {}}, "power_query", True),
    ({"power_query": {}}, "PWR?", False),
    ({"power_query": {}}, "", False),
    ({"power_query": {}}, None, False),
    ({"power_query": {}}, ["power_query"], False),
    (None, "power_query", False),
    (["power_query"], "power_query", False),
])
def test_the_rule(commands, text, runs):
    assert runs_as_command(commands, text) is runs


async def test_the_runtime_and_the_audit_agree_on_what_the_poll_runs():
    cls = create_configurable_driver_class(_DEFINITION)
    driver = cls("acme1", {"host": "127.0.0.1", "port": 23}, StateStore(), EventBus())
    as_command, as_text = [], []

    async def send_command(name, params=None):
        as_command.append(name)

    async def send_raw_line(transport, text):
        as_text.append(text)

    driver._send_command = send_command
    driver._send_raw_line = send_raw_line
    for line in _DEFINITION["polling"]["queries"]:
        await driver._dispatch_query(line)
    assert (as_command, as_text) == (["power_query"], ["INPUT?"])

    catalog = {entry["name"]: entry for entry in command_catalog(reading_of(driver))}
    polled = sorted(name for name, entry in catalog.items() if entry["polled"])
    assert polled == as_command


def test_no_reader_tests_a_line_against_the_commands_itself():
    """The sender's and the readers' own membership tests are gone: each asks
    ``runs_as_command``."""
    root = Path(openavc.__file__).parent
    readers = {
        "drivers/start_up_and_poll.py", "drivers/substitution_checks.py",
        "audit/driver_reading.py", "simulator/osc_coverage.py",
        "simulator/send_coverage.py", "simulator/reread_queries.py",
    }
    own_test = re.compile(
        r'\b(query|query_text|line|entry|text) (not )?in (commands\b|self\._definition\.get\("commands")'
    )
    for name in sorted(readers):
        source = (root / name).read_text(encoding="utf-8")
        assert "runs_as_command(" in source, name
        assert own_test.search(source) is None, (name, own_test.search(source))


def test_a_python_driver_has_no_poll_lines():
    """A Python driver's class sends what it sends; the audit reads no poll
    of its own into it."""
    driver = NS(DRIVER_INFO={"commands": {"power_query": {"label": "Power?"}}}, config={})
    (entry,) = command_catalog(reading_of(driver))
    assert entry["polled"] is False
