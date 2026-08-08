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

from openavc.core.project_loader import ProjectConfig
from openavc.ui.page_references import reference_findings

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
    findings = _findings([{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": {"action": "macro", "macro": "nope"}}},
    }])
    kinds = sorted(f.kind for f in findings)
    assert kinds == ["dangling_reference", "object_action_list"], kinds
    assert any("runs macro 'nope'" in f.message for f in findings)


def test_the_object_shape_warns_and_does_not_claim_it_is_broken() -> None:
    """It runs. Saying otherwise would be the review inventing a rule.

    `ui_events.py` wraps a non-list before executing, and handles the dict form
    explicitly for off_action and hold_action. What the shape costs is that it
    holds exactly one action -- worth a warning, not a rejection.
    """
    findings = _findings([{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": {
            "action": "device.command", "device": "acme_amp", "command": "mute_on",
        }}},
    }])
    assert [f.kind for f in findings] == ["object_action_list"]
    message = findings[0].message
    assert "does run it" in message
    assert "holds exactly one" in message


def test_an_array_of_one_is_not_the_object_shape() -> None:
    assert _findings([{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [
            {"action": "device.command", "device": "acme_amp", "command": "mute_on"},
        ]}},
    }]) == []


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


# --- Plugin elements ------------------------------------------------------
#
# `plugin` is a real type, so the type check passes it and nothing looked
# inside. The renderer needs both ids, both matching [A-Za-z0-9_-]+, and builds
# /api/plugins/<id>/panel/<type>.html from them -- so a miss draws a dashed grey
# box that reads as a loading state rather than a mistake.

PLUGIN_ELEMENTS = {"acme": {"meter", "fader"}}


def _plugin(**fields) -> list[str]:
    return _messages(
        [{"id": "widget", "type": "plugin", **fields}],
        plugin_elements=PLUGIN_ELEMENTS.get,
    )


def test_a_plugin_element_with_neither_id_is_named() -> None:
    messages = _plugin()
    assert len(messages) == 1, messages
    assert "no plugin_id and no plugin_type" in messages[0]


def test_a_plugin_element_missing_one_id_is_named() -> None:
    messages = _plugin(plugin_id="acme")
    assert len(messages) == 1, messages
    assert "no plugin_type" in messages[0]
    assert "plugin_id" not in messages[0].split("has no")[1].split(",")[0]


def test_an_invented_plugin_type_is_caught_against_what_it_declares() -> None:
    messages = _plugin(plugin_id="acme", plugin_type="invented_widget")
    assert len(messages) == 1, messages
    assert "does not declare" in messages[0]
    assert "fader, meter" in messages[0], "the real ones are named"


def test_a_name_the_renderer_cannot_put_in_a_url_is_caught() -> None:
    """The panel tests both ids against [A-Za-z0-9_-]+ before building the URL."""
    messages = _plugin(plugin_id="acme", plugin_type="a/b")
    assert len(messages) == 1, messages
    assert "not a name the panel accepts" in messages[0]


def test_a_plugin_that_is_not_loaded_here_says_nothing() -> None:
    """Not installed yet is a deployment fact, not an authoring mistake.

    Warning would fire on every page written before commissioning, which is
    when panels are usually built.
    """
    assert _plugin(plugin_id="not_installed", plugin_type="whatever") == []
    assert _messages([{"id": "widget", "type": "plugin",
                       "plugin_id": "acme", "plugin_type": "meter"}]) == []


def test_a_correctly_configured_plugin_element_is_silent() -> None:
    assert _plugin(plugin_id="acme", plugin_type="meter") == []
