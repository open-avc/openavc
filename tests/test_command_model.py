"""A command declaration, read once: whether it can be sent without asking.

The Device Audit sends some commands on the person's behalf (Run all status
queries, the read before a setting is written). Each asks one answer, which
holds only for a command that needs nothing from the person (no required
value, no file) and that a single Send would not warn about (no confirmation,
no restart).
"""

from __future__ import annotations

import pytest

from openavc.drivers.command_model import read_command


def test_a_plain_query_is_sendable_without_asking():
    command = read_command("query_power", {"label": "Query Power", "send": "PWR?\\r", "query_for": "power"})
    assert command.sendable_without_asking
    assert not command.needs_input and not command.moves_a_file
    assert command.confirm is None and command.restarts_device_for is None


@pytest.mark.parametrize("cdef", [
    # A value nobody has given yet.
    {"params": {"level": {"type": "integer", "required": True}}},
    # A file to give, or one handed back.
    {"params": {"firmware": {"type": "file"}}},
    {"returns_file": True},
    # The driver's confirmation, as a sentence or as true.
    {"confirm": "Erases every preset."},
    {"confirm": True},
    # A restart.
    {"restarts_device_for": 30},
])
def test_a_command_that_needs_or_asks_something_is_not(cdef):
    assert not read_command("cmd", {"label": "Cmd", **cdef}).sendable_without_asking


@pytest.mark.parametrize("cdef", [
    # An optional value, with or without a default, is not one it needs.
    {"params": {"level": {"type": "integer"}}},
    {"params": {"level": {"type": "integer", "default": 5}}},
    {"confirm": False},
    {"confirm": ""},
    # A blank sentence asks nothing.
    {"confirm": " \n "},
    {"restarts_device_for": 0},
    {"returns_file": False},
])
def test_what_asks_nothing_leaves_it_sendable(cdef):
    assert read_command("cmd", {"label": "Cmd", **cdef}).sendable_without_asking


def test_confirm_true_reads_as_a_plain_question_naming_the_command():
    assert read_command("reset", {"label": "Factory Reset", "confirm": True}).confirm == "Send Factory Reset?"
    assert read_command("reset", {"confirm": True}).confirm == "Send reset?"


def test_the_drivers_sentence_is_read_trimmed():
    assert read_command("reset", {"confirm": "  Erases every preset.\n"}).confirm == "Erases every preset."


@pytest.mark.parametrize("value,seconds", [
    (30, 30), (None, None), ("soon", None),
    # No window opens for 0 or less, so there is no restart to warn about.
    (-5, None),
    # Neither raises nor counts: the command list is still served.
    (float("inf"), None), (float("nan"), None), (True, None), pytest.param(10**400, None, id="past-a-float"),
    # The runtime's reading (tests/test_restart_window.py has the whole table).
    (700, 600), (0.4, 1),
])
def test_the_restart_is_read_as_whole_seconds(value, seconds):
    command = read_command("reboot", {"restarts_device_for": value})
    assert command.restarts_device_for == seconds
    assert command.sendable_without_asking is (seconds is None)


def test_a_declaration_that_is_not_a_mapping_reads_as_one_that_declares_nothing():
    command = read_command("odd", "PWR?")
    assert command.sendable_without_asking and command.confirm is None
