"""The script check and the macro lint say, while a write is being written,
what the runtime will do with it (``core/state_store.check_authored_write``).

A ``system.`` or ``isc.`` key, and a value written as a list or a dict, are
refused when they run: marked with the runtime's own sentence. A ``device.``
key runs and lasts until the device's next report: a warning the macro lint
does not count as a step that won't run, and the AI's macro tools do not
refuse.

Every script, macro, device and variable here is invented.
"""

from __future__ import annotations

import textwrap

import pytest

from openavc.core.event_references import script_issues
from openavc.core.macro_validation import macro_issues, validate_macro, validate_macro_step
from openavc.core.state_store import DEVICE_WRITE_NOTE, check_authored_write


_WRITE_KINDS = ("platform_key", "not_one_value", "device_key")


def _issues(source: str) -> list[dict]:
    """The script check's write issues on one script (the handlers here wait
    for events nothing emits, which is its own issue)."""
    found = script_issues({"room": textwrap.dedent(source)}).get("room", [])
    return [issue for issue in found if issue["kind"] in _WRITE_KINDS]


# --- the script check ----------------------------------------------------------


def test_a_platform_key_written_or_removed_is_marked_on_its_line():
    issues = _issues("""\
        from openavc import state, on_event

        @on_event("custom.go")
        async def go(event):
            state.set("system.help_state", "idle")
            state.delete("isc.lobby.var.mode")
            state.set(f"system.trigger.{event.name}.last_fired", 0)
    """)

    assert [(i["kind"], i["line"]) for i in issues] == [
        ("platform_key", 5), ("platform_key", 6), ("platform_key", 7),
    ]
    assert issues[0]["message"] == check_authored_write("system.help_state", "idle")
    assert issues[0]["event"] == "system.help_state"
    assert issues[2]["event"] == "system.trigger.*.last_fired"


@pytest.mark.parametrize("value", ["[1, 2]", "{'a': 1}", "(1, 2)", "{1, 2}", "[n for n in range(3)]"])
def test_a_value_written_as_more_than_one_value_is_marked(value):
    issues = _issues(f'from openavc import state\nstate.set("var.rows", {value})\n')

    assert [(i["kind"], i["line"]) for i in issues] == [("not_one_value", 2)]
    assert issues[0]["message"].startswith("State values must be a flat primitive")


def test_a_device_key_is_a_warning_with_what_replaces_it():
    issues = _issues("""\
        import openavc
        openavc.state.set("device.acme_widget.power", "on")
    """)

    assert issues == [{
        "kind": "device_key", "line": 2, "event": "device.acme_widget.power",
        "message": DEVICE_WRITE_NOTE.format(key="device.acme_widget.power"),
    }]


def test_what_cannot_be_read_or_is_allowed_is_left_alone():
    assert _issues("""\
        from openavc import state

        key = "system.version"
        state.set(key, "1.0")
        state.set("var.room_mode", "lecture")
        state.set("ui.btn_power.label", "Off")
        state.delete("var.scratch")
        state.get("system.version")
        state.delete("device.acme_widget.power")
        settings = {}
        settings.set = None
    """) == []


# --- the macro lint -------------------------------------------------------------


def _step(key, value="on"):
    return {"action": "state.set", "key": key, "value": value}


def test_a_set_variable_on_a_platform_key_is_refused_in_the_runtimes_words():
    issues = macro_issues([_step("system.help_state", "idle")], [])

    assert len(issues) == 1
    assert issues[0]["refused"] == "platform_key"
    assert issues[0]["message"] == check_authored_write("system.help_state", "idle")
    assert "warning" not in issues[0]
    assert validate_macro_step(_step("isc.lobby.var.mode"), "steps[0]")


def test_a_set_variable_whose_value_is_a_list_is_refused():
    issues = macro_issues([_step("var.rows", [1, 2])], [])

    assert [i.get("refused") for i in issues] == ["value"]
    assert issues[0]["message"] == check_authored_write("var.rows", [1, 2])


def test_a_set_variable_inside_a_branch_is_found():
    branch = {
        "action": "conditional",
        "condition": {"key": "var.room_mode", "operator": "eq", "value": "off"},
        "then_steps": [_step("system.version", "1.0")],
    }
    issues = macro_issues([branch], [])

    assert [(i["index"], i["path"], i.get("refused")) for i in issues] == [
        (0, "steps[0].then_steps[0]", "platform_key"),
    ]


def test_a_set_variable_on_a_device_key_is_a_warning_nothing_refuses():
    step = _step("device.acme_widget.power")
    issues = macro_issues([step], [])

    assert issues == [{
        "scope": "step", "index": 0, "path": "steps[0]",
        "message": DEVICE_WRITE_NOTE.format(key="device.acme_widget.power"),
        "action": "state.set", "warning": True,
    }]
    # The AI's macro tools refuse on findings; a warning is not one.
    assert validate_macro_step(step, "steps[0]") == []
    assert validate_macro([step], []) is None


@pytest.mark.parametrize("key,value", [
    ("var.room_mode", "lecture"), ("ui.btn_power.label", "Off"),
    ("var.copied", "$trigger.data"), ("room_mode", 1),
])
def test_any_other_set_variable_is_clean(key, value):
    assert macro_issues([_step(key, value)], []) == []
