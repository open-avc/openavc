"""Bindings that name something the project does not contain.

Six shapes came back clean from the AI write door: a macro, a page, a device, a
command, a state key's device, and a ``value_map`` matching none of the select's
own options. The Builder's ``validateProject`` had caught most of them for a
while; the write door had never looked at any.

Every device, command and macro here is invented. This tests a platform
capability, not a driver.
"""

from __future__ import annotations

import pytest

from server.core.project_loader import ProjectConfig
from server.ui.page_references import reference_findings

PAGES = {"main", "audio"}
DEVICES = {"acme_amp", "acme_switcher"}
MACROS = {"system_on"}
COMMANDS = {"acme_amp": {"mute_on", "mute_off", "set_level"}}


def _press(*actions) -> dict:
    return {"bindings": {"do": {"press": list(actions)}}}


def _page(elements) -> object:
    project = ProjectConfig.model_validate({
        "project": {"id": "refs", "name": "Refs", "description": ""},
        "ui": {"settings": {}, "pages": [{
            "id": "main", "name": "Main", "elements": elements, "layouts": [],
        }], "master_elements": []},
    })
    return project.ui.pages[0]


def _findings(elements, **kwargs) -> list:
    return reference_findings(
        _page(elements),
        page_ids=PAGES,
        device_ids=DEVICES,
        macro_ids=MACROS,
        device_commands=COMMANDS.get,
        **kwargs,
    )


def _messages(elements, **kwargs) -> list[str]:
    return [f.message for f in _findings(elements, **kwargs)]


@pytest.mark.parametrize(
    ("element", "expected"),
    [
        pytest.param(
            {"id": "nav_bad", "type": "page_nav", "target_page": "nowhere"},
            "navigates to page 'nowhere', which does not exist",
            id="page_nav target",
        ),
        pytest.param(
            {"id": "btn", "type": "button",
             **_press({"action": "ui.navigate", "page": "nowhere"})},
            "navigates to page 'nowhere', which does not exist",
            id="navigate action",
        ),
        pytest.param(
            {"id": "btn", "type": "button",
             **_press({"action": "macro", "macro": "nope"})},
            "runs macro 'nope', which does not exist",
            id="macro",
        ),
        pytest.param(
            {"id": "btn", "type": "button",
             **_press({"action": "device.command", "device": "ghost", "command": "mute_on"})},
            "commands device 'ghost', which is not in this project",
            id="device",
        ),
        pytest.param(
            {"id": "btn", "type": "button",
             **_press({"action": "device.command", "device": "acme_amp", "command": "explode"})},
            "sends 'explode' to 'acme_amp', which its driver does not have",
            id="command",
        ),
        pytest.param(
            {"id": "lbl", "type": "label",
             "bindings": {"show": {"value": {"key": "device.ghost.level"}}}},
            "no device 'ghost' is in this project",
            id="state key device",
        ),
    ],
)
def test_each_dangling_shape_is_named(element, expected) -> None:
    messages = _messages([element])
    assert len(messages) == 1, messages
    assert expected in messages[0]
    # The valid set is in the sentence, because the caller cannot see it.
    assert "acme" in messages[0] or "main" in messages[0] or "system_on" in messages[0]


def test_a_value_map_key_no_option_can_produce() -> None:
    """The select's own options are the only values the map will ever be handed."""
    messages = _messages([{
        "id": "sel", "type": "select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
        "bindings": {"do": {"change": [{"action": "value_map", "map": {
            "x": {"action": "device.command", "device": "acme_amp", "command": "mute_on"},
            "a": {"action": "device.command", "device": "acme_amp", "command": "mute_off"},
        }}]}},
    }])
    assert len(messages) == 1, messages
    assert "maps 'x'" in messages[0]
    assert "not among its options (a, b)" in messages[0]


def test_a_value_map_is_checked_one_level_down() -> None:
    """The engine runs each mapped entry as an action, so each one answers too."""
    messages = _messages([{
        "id": "sel", "type": "select",
        "options": [{"label": "A", "value": "a"}],
        "bindings": {"do": {"change": [{"action": "value_map", "map": {
            "a": {"action": "device.command", "device": "acme_amp", "command": "explode"},
        }}]}},
    }])
    assert len(messages) == 1, messages
    assert "sends 'explode'" in messages[0]
    assert "['a']" in messages[0], "the mapped option is named, or it cannot be found"


def test_an_object_shaped_action_list_is_checked_too() -> None:
    """`ui_events` wraps a bare object before executing it, so this must too.

    A dict-shaped `do.press` runs -- `ui_events.py` normalises a non-list into a
    one-element one. Skipping it here would leave the shape that executes as the
    shape nothing checks.
    """
    messages = _messages([{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": {"action": "macro", "macro": "nope"}}},
    }])
    assert len(messages) == 1, messages
    assert "runs macro 'nope'" in messages[0]


def test_a_driver_with_no_opinion_produces_no_warning() -> None:
    """`None` commands means "not connected yet", which is not a mistake.

    This is the difference that decides whether the check is usable at all: a
    device that is disabled, unloaded, or backed by a driver that enumerates
    nothing must be silent, or every page written before commissioning warns
    about every button on it.
    """
    element = {
        "id": "btn", "type": "button",
        **_press({"action": "device.command", "device": "acme_switcher", "command": "whatever"}),
    }
    assert _messages([element]) == []
    assert reference_findings(
        _page([element]),
        page_ids=PAGES, device_ids=DEVICES, macro_ids=MACROS, device_commands=None,
    ) == []


def test_everything_that_resolves_stays_quiet() -> None:
    assert _messages([
        {"id": "nav", "type": "page_nav", "target_page": "audio"},
        {"id": "btn", "type": "button", **_press(
            {"action": "macro", "macro": "system_on"},
            {"action": "device.command", "device": "acme_amp", "command": "set_level"},
            {"action": "ui.navigate", "page": "main"},
        )},
        {"id": "lbl", "type": "label",
         "bindings": {"show": {"value": {"key": "device.acme_amp.channel.01.fader"}}}},
        # A var key is not checkable: a script or macro can create one.
        {"id": "lbl2", "type": "label",
         "bindings": {"show": {"value": {"key": "var.anything"}}}},
        # A select with no options has nothing to compare a map against.
        {"id": "sel", "type": "select",
         "bindings": {"do": {"change": [{"action": "value_map", "map": {
             "a": {"action": "macro", "macro": "system_on"},
         }}]}}},
    ]) == []


def test_a_write_answers_only_for_what_it_touched() -> None:
    """Re-reporting the rest of the page on every call is how the field gets skipped."""
    elements = [
        {"id": "mine", "type": "button", **_press({"action": "macro", "macro": "nope"})},
        {"id": "theirs", "type": "button", **_press({"action": "macro", "macro": "also_nope"})},
    ]
    findings = _findings(elements, touched={"mine"})
    assert [f.element_id for f in findings] == ["mine"]
