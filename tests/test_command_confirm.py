"""A command's ``confirm``: asked where a person sends it by hand, nowhere else.

The driver declares it on the command (``confirm: true`` or the sentence to
ask). The server reads it off ``DRIVER_INFO`` one way
(``command_model.confirm_sentence``) and serves the question to every door a
person sends from by hand: the device page's Send Command
(``command_confirms``), its promoted buttons (each action's ``confirm``) and
the device audit's Commands step (the Driver Builder's Live Test reads the
draft it is editing through the IDE's copy). The runtime's own send path never
reads it, which is how macros, triggers and panel buttons send it without
asking.
"""

from __future__ import annotations

from pathlib import Path

from openavc.audit.command_catalog import command_catalog
from openavc.audit.driver_reading import reading_of
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.actions import resolve_device_actions
from openavc.drivers.command_model import confirm_sentence, read_command
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition
from openavc.drivers.python_info import (
    extract_python_driver_info_full,
    python_driver_info_issues,
)
from openavc.drivers.spec import platform_requirements

ACME_WIDGET = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 4000},
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {
        "power_on": {"label": "Power On", "send": "PWR 1\\r"},
        "factory_reset": {
            "label": "Factory Reset",
            "send": "RESET ALL\\r",
            "confirm": "Erases every preset and returns the unit to DHCP.",
        },
        "clear_log": {"label": "Clear Log", "send": "LOG CLEAR\\r", "confirm": True},
    },
}


class FakeTransport:
    connected = True

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _driver(definition=ACME_WIDGET):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("widget1", {"host": "127.0.0.1", "port": 4000}, state, events)
    driver.transport = FakeTransport()
    driver.set_state("connected", True)
    return driver


def test_a_yaml_drivers_confirm_reaches_driver_info():
    # Every by-hand door reads DRIVER_INFO, so a YAML driver's confirm has to
    # be carried there or it is inert for every YAML driver.
    commands = _driver().DRIVER_INFO["commands"]
    assert commands["factory_reset"]["confirm"] == (
        "Erases every preset and returns the unit to DHCP."
    )
    assert commands["clear_log"]["confirm"] is True
    assert "confirm" not in commands["power_on"]


async def test_the_runtime_sends_a_confirm_command_without_asking():
    # Macros, triggers and panel buttons all reach the driver through
    # send_command, which asks nothing.
    driver = _driver()
    await driver.send_command("factory_reset")
    assert driver.transport.sent == [b"RESET ALL\r"]


def test_a_confirm_needs_platform_0_36_0():
    floors = dict(platform_requirements(ACME_WIDGET))
    assert floors["commands.factory_reset.confirm"] == "0.36.0"
    assert floors["commands.clear_log.confirm"] == "0.36.0"


def test_a_confirm_that_is_neither_a_flag_nor_a_sentence_is_refused():
    definition = {**ACME_WIDGET, "commands": {
        "factory_reset": {"label": "Factory Reset", "send": "RESET\\r", "confirm": 5},
    }}
    errors = validate_driver_definition(definition)
    assert "Command 'factory_reset': 'confirm' must be a boolean or a message string" in errors
    assert validate_driver_definition(ACME_WIDGET) == []


def test_a_python_drivers_confirm_is_checked_by_the_same_rule():
    info = {"commands": {
        "factory_reset": {"label": "Factory Reset", "confirm": ["no"]},
        "clear_log": {"label": "Clear Log", "confirm": True},
    }}
    assert python_driver_info_issues(info) == [
        "Command 'factory_reset': 'confirm' must be a boolean or a message string",
    ]


def test_a_python_confirm_read_from_a_constant_is_not_judged(tmp_path: Path):
    source = tmp_path / "acme_widget.py"
    source.write_text(
        "from openavc.drivers.base import BaseDriver\n"
        "RESET_WARNING = 'Erases every preset.'\n"
        "class AcmeWidget(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        "        'id': 'acme_widget',\n"
        "        'commands': {\n"
        "            'factory_reset': {'label': 'Factory Reset', 'confirm': RESET_WARNING},\n"
        "        },\n"
        "    }\n",
        encoding="utf-8",
    )
    info, _ = extract_python_driver_info_full(source)
    assert python_driver_info_issues(info) == []


def test_a_promoted_button_asks_what_its_command_asks():
    info = {**_driver().DRIVER_INFO, "quick_actions": ["factory_reset", "power_on"]}
    actions = {a["id"]: a for a in resolve_device_actions(info)}
    assert actions["factory_reset"]["confirm"] == (
        "Erases every preset and returns the unit to DHCP."
    )
    assert actions["power_on"]["confirm"] is None


def test_an_action_inherits_the_confirm_unless_it_sets_its_own():
    info = {**_driver().DRIVER_INFO, "actions": [
        {"id": "reset_button", "kind": "command", "command": "factory_reset"},
        {"id": "log_button", "kind": "command", "command": "clear_log", "confirm": False},
        {"id": "worded", "kind": "command", "command": "clear_log", "confirm": "Clears it."},
    ]}
    actions = {a["id"]: a for a in resolve_device_actions(info)}
    assert actions["reset_button"]["confirm"] == (
        "Erases every preset and returns the unit to DHCP."
    )
    assert actions["log_button"]["confirm"] is None
    assert actions["worded"]["confirm"] == "Clears it."


def test_the_audit_asks_before_a_confirm_command_and_never_suggests_it():
    info = dict(_driver().DRIVER_INFO)
    info["quick_actions"] = ["power_on", "factory_reset"]
    driver = _driver()
    driver.DRIVER_INFO = info
    catalog = {c["name"]: c for c in command_catalog(reading_of(driver))}
    assert catalog["factory_reset"]["confirm"] == (
        "Erases every preset and returns the unit to DHCP."
    )
    assert catalog["clear_log"]["confirm"] == "Send Clear Log?"
    assert catalog["power_on"]["confirm"] == ""
    assert [n for n, c in catalog.items() if c["suggested"]] == ["power_on"]


# --- one reading, everywhere it is asked ------------------------------------------
#
# The sentence is the driver's own, trimmed; a blank one asks nothing; true asks
# one plain question naming what is sent. The resolver words a button's question
# with the button's label, the audit and the device page with the command's.

def _with_commands(**commands):
    return {**ACME_WIDGET, "commands": {**ACME_WIDGET["commands"], **commands}}


def _catalog(info):
    driver = _driver()
    driver.DRIVER_INFO = info
    return {c["name"]: c for c in command_catalog(reading_of(driver))}


def test_confirm_true_asks_one_question_naming_what_is_sent():
    assert confirm_sentence(True, "Clear Log") == "Send Clear Log?"
    assert read_command("clear_log", {"label": "Clear Log", "confirm": True}).confirm == "Send Clear Log?"
    assert read_command("clear_log", {"confirm": True}).confirm == "Send clear_log?"
    info = {**_driver().DRIVER_INFO, "quick_actions": ["clear_log"]}
    assert resolve_device_actions(info)[0]["confirm"] == "Send Clear Log?"
    assert _catalog(info)["clear_log"]["confirm"] == "Send Clear Log?"


def test_a_blank_confirm_asks_nothing_anywhere():
    info = _with_commands(wipe={"label": "Wipe", "send": "WIPE\\r", "confirm": " \n "})
    info["quick_actions"] = ["wipe"]
    assert read_command("wipe", info["commands"]["wipe"]).confirm is None
    assert resolve_device_actions(info)[0]["confirm"] is None
    entry = _catalog(info)["wipe"]
    assert entry["confirm"] == "" and entry["sendable_without_asking"] and entry["suggested"]


def test_a_padded_sentence_is_asked_without_its_padding():
    sentence = "  Erases every preset.\n"
    info = _with_commands(wipe={"label": "Wipe", "send": "WIPE\\r", "confirm": sentence})
    info["quick_actions"] = ["wipe"]
    assert confirm_sentence(sentence, "Wipe") == "Erases every preset."
    assert resolve_device_actions(info)[0]["confirm"] == "Erases every preset."
    assert _catalog(info)["wipe"]["confirm"] == "Erases every preset."


def test_a_buttons_true_is_worded_with_the_buttons_label():
    info = {**_driver().DRIVER_INFO, "actions": [
        {"id": "own", "kind": "command", "command": "power_on", "label": "Wake", "confirm": True},
        {"id": "inherited", "kind": "command", "command": "clear_log", "label": "Empty Log"},
    ]}
    actions = {a["id"]: a for a in resolve_device_actions(info)}
    assert actions["own"]["confirm"] == "Send Wake?"
    assert actions["inherited"]["confirm"] == "Send Empty Log?"
    # The audit lists the command, so it asks with the command's own label
    # when the command confirms, and the button's question when only the
    # button does.
    catalog = _catalog(info)
    assert catalog["clear_log"]["confirm"] == "Send Clear Log?"
    assert catalog["power_on"]["confirm"] == "Send Wake?"


def test_a_blank_or_empty_button_confirm_keeps_the_commands_question():
    # Only false turns a confirming command's question off for its button: a
    # blank, empty or null one is no confirm of the button's own, so the
    # command's still asks rather than the command going out unasked.
    info = {**_driver().DRIVER_INFO, "actions": [
        {"id": "blank", "kind": "command", "command": "factory_reset", "confirm": "  "},
        {"id": "empty", "kind": "command", "command": "factory_reset", "confirm": ""},
        {"id": "null", "kind": "command", "command": "factory_reset", "confirm": None},
        {"id": "off", "kind": "command", "command": "factory_reset", "confirm": False},
    ]}
    actions = {a["id"]: a["confirm"] for a in resolve_device_actions(info)}
    erases = "Erases every preset and returns the unit to DHCP."
    assert actions == {"blank": erases, "empty": erases, "null": erases, "off": None}


def test_a_setup_actions_true_leaves_the_wizard_its_own_words():
    # The wizard always asks before it runs; true adds no sentence of its own,
    # so the wizard shows its own description. A sentence is shown trimmed.
    info = {"commands": {}, "actions": [
        {"id": "provision", "kind": "setup", "label": "Provision", "confirm": True},
        {"id": "scan", "kind": "setup", "label": "Scan", "confirm": " Registers every light. "},
        {"id": "probe", "kind": "setup", "label": "Probe", "confirm": "   "},
    ]}
    actions = {a["id"]: a["confirm"] for a in resolve_device_actions(info)}
    assert actions == {"provision": None, "scan": "Registers every light.", "probe": None}


def test_the_device_page_is_served_each_commands_question():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    definition = _with_commands(
        wipe={"label": "Wipe", "send": "WIPE\\r", "confirm": " "},
        padded={"label": "Padded", "send": "PAD\\r", "confirm": "Clears it.\n"},
    )
    cls = create_configurable_driver_class(definition)
    dm._devices["widget1"] = cls("widget1", {"host": "h", "port": 1}, state, events)
    dm._device_configs["widget1"] = {"name": "Widget", "driver": "acme_widget"}
    info = dm.get_device_info("widget1")
    assert info["command_confirms"] == {
        "factory_reset": "Erases every preset and returns the unit to DHCP.",
        "clear_log": "Send Clear Log?",
        "padded": "Clears it.",
    }
    # The declaration itself is served as the driver wrote it.
    assert info["commands"]["clear_log"]["confirm"] is True
