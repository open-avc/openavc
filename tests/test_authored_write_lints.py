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


# --- through the editor's doors ------------------------------------------------


@pytest.fixture
def http(tmp_path):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openavc.api.rest import router, set_engine
    from openavc.core.event_bus import EventBus
    from openavc.core.macro_engine import MacroEngine
    from openavc.core.project_loader import ProjectConfig, ProjectMeta
    from openavc.core.state_store import StateStore

    state = StateStore()
    engine = MagicMock()
    engine.macros = MacroEngine(state, EventBus(), MagicMock())
    engine.project_path = tmp_path / "project.avc"
    (tmp_path / "scripts").mkdir()
    engine.project = ProjectConfig(project=ProjectMeta(id="p", name="P"))
    app = FastAPI()
    app.include_router(router)
    set_engine(engine)
    yield TestClient(app, raise_server_exceptions=False)
    set_engine(None)


def test_the_macro_editors_door_carries_the_warning_and_the_refusal(http):
    reply = http.post("/api/macros/validate", json={"macros": [{
        "id": "room_off",
        "steps": [_step("device.acme_widget.power"), _step("system.version", "1.0")],
        "triggers": [],
    }]})

    assert reply.status_code == 200, reply.text
    issues = reply.json()["macros"]["room_off"]["issues"]
    assert [(i["index"], i.get("warning"), i.get("refused")) for i in issues] == [
        (0, True, None), (1, None, "platform_key"),
    ]


def test_the_script_editors_door_carries_each_write_kind(http):
    source = textwrap.dedent("""\
        from openavc import state
        state.set("device.acme_widget.power", "on")
        state.set("system.version", "1.0")
        state.set("var.rows", [1, 2])
    """)
    reply = http.post("/api/scripts/validate", json={"scripts": [{"id": "room", "source": source}]})

    assert reply.status_code == 200, reply.text
    issues = reply.json()["scripts"]["room"]["issues"]
    assert [(i["kind"], i["line"]) for i in issues] == [
        ("device_key", 2), ("platform_key", 3), ("not_one_value", 4),
    ]
