"""A command that is chosen, spelled right, and still refused.

Every static gate in the product used to stop one question short. It checked
that a device existed and that its driver had a command by that name, and then
said the control was finished -- while the driver declared a parameter as
required, nothing had filled it in, and the runtime refused every press with
``'set_fader': 'channel' is required``.

So the Builder's Validate answered "No Issues" for a dead control, the macro
editor's "won't run as built" banner cleared the moment a command was picked,
and the one card badge that had been honest went quiet at the same instant. The
device knew, the driver contract knew, and the button beside the form knew --
the gates somebody actually trusts at handover did not.

What is pinned here is that the four gates now ask, that they ask the SAME
question the runtime answers, and -- just as important -- that they all stay
silent when the driver is not there to ask. A page is very often built before
the hardware is on the bench, and a gate that complains then stops being read.

Every device, driver and command below is invented. This is a platform
capability, not a driver.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.rest import router, set_engine
from openavc.core.command_params import (
    device_groups,
    missing_params,
    missing_params_check,
)
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_validation import macro_issues, validate_macro
from openavc.core.project_loader import ProjectConfig
from openavc.core.state_store import StateStore
from openavc.drivers.base import (
    CommandParamError,
    missing_required_params,
    normalize_and_validate_command_params,
)
from openavc.ui.page_references import reference_findings

# The one command every case below is about: two required parameters and one
# optional, which is the shape that made the defect invisible -- the form draws
# three fields and two red "required" chips, and saving with all three empty
# was reported as complete.
SET_FADER = {
    "params": {
        "channel": {"type": "child_id", "required": True},
        "level": {"type": "number", "required": True},
        "ramp": {"type": "number"},
    },
}
DRIVER_INFO = {
    "commands": {
        "set_fader": SET_FADER,
        "mute_on": {},
    },
}


def _devices(**by_id):
    """A stand-in DeviceManager: the one method the lookup uses."""
    manager = MagicMock()
    manager.get_driver.side_effect = lambda device_id: by_id.get(device_id)
    return manager


def _driver(info=DRIVER_INFO):
    driver = MagicMock()
    driver.DRIVER_INFO = info
    return driver


AMP = _driver()


# --- The rule itself, next to the runtime that enforces it -----------------


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({}, ["channel", "level"], id="nothing supplied"),
        pytest.param({"channel": 1}, ["level"], id="one supplied"),
        pytest.param({"channel": 1, "level": -6}, [], id="both supplied"),
        pytest.param({"channel": None, "level": -6}, ["channel"], id="null is absent"),
        # A blank names no channel and no level, so neither is supplied. The
        # static side must not invent a rule the runtime does not have, in
        # either direction -- the runtime assertion below is what holds them
        # together. (A blank on a STRING param is still a value; that one is
        # pinned next to the runtime, in test_command_dispatch_messages.)
        pytest.param({"channel": "", "level": "  "}, ["channel", "level"], id="blank is nothing"),
        pytest.param({"ramp": 2}, ["channel", "level"], id="optional does not help"),
    ],
)
def test_the_rule_matches_what_the_runtime_does(params, expected):
    assert missing_required_params(SET_FADER["params"], params) == expected

    # The same call through the runtime gate: it refuses exactly when the rule
    # above names something, and names the first one.
    if expected:
        with pytest.raises(CommandParamError) as caught:
            normalize_and_validate_command_params("set_fader", SET_FADER["params"], params)
        assert str(caught.value) == f"'set_fader': '{expected[0]}' is required"
    else:
        normalize_and_validate_command_params("set_fader", SET_FADER["params"], params)


def test_a_dollar_reference_is_a_supplied_value():
    """The commonest way a required parameter is filled in on a panel.

    ``$value`` is what a fader's own position is written as, and it is resolved
    long before the runtime gate sees it -- so a gate that read it as "nothing
    there" would flag every two-way control in the product.
    """
    assert missing_required_params(
        SET_FADER["params"], {"channel": "$value", "level": "$var.house_level"},
    ) == []


def test_the_rule_says_nothing_about_a_command_with_no_declared_params():
    assert missing_required_params({}, {}) == []
    assert missing_required_params(None, {}) == []


# --- The lookup around it ---------------------------------------------------


def _action(**over):
    return {
        "action": "device.command", "device": "amp", "command": "set_fader",
        "params": {}, **over,
    }


def test_a_loaded_driver_names_what_is_missing():
    assert missing_params(_action(), devices=_devices(amp=AMP)) == ["channel", "level"]


@pytest.mark.parametrize(
    ("action", "devices", "why"),
    [
        pytest.param(_action(), _devices(), "the device has no driver loaded", id="no driver"),
        pytest.param(
            _action(command="reboot"), _devices(amp=AMP),
            "the driver does not declare that command", id="unknown command",
        ),
        pytest.param(
            _action(command=""), _devices(amp=AMP),
            "no command chosen yet", id="no command",
        ),
        pytest.param(
            {"action": "macro", "macro": "system_on"}, _devices(amp=AMP),
            "not a command action at all", id="other action",
        ),
        pytest.param(
            _action(), _devices(amp=_driver({})),
            "the driver enumerates nothing", id="driver declares nothing",
        ),
    ],
)
def test_no_opinion_is_the_answer_far_more_often_than_a_complaint(action, devices, why):
    assert missing_params(action, devices=devices) is None, why


def test_an_unknown_command_is_left_to_the_gate_that_already_reports_it():
    """Two complaints about one field would bury the one that matters."""
    assert missing_params(_action(command="reboot"), devices=_devices(amp=AMP)) is None


def test_a_group_command_is_checked_against_every_member_that_answers():
    """Unioned, not intersected: the runtime fans out and refuses per device.

    A parameter one member requires is a parameter that step fails on, and
    reporting only what EVERY member requires would stay quiet about a step
    that half works.
    """
    other = _driver({"commands": {"set_fader": {"params": {
        "zone": {"required": True}, "level": {"required": True},
    }}}})
    step = {"action": "group.command", "group": "amps", "command": "set_fader", "params": {}}
    assert missing_params(
        step, devices=_devices(amp=AMP, amp2=other), groups={"amps": ["amp", "amp2"]},
    ) == ["channel", "level", "zone"]

    # A group whose members are all unknown says nothing, like any other
    # unloaded driver.
    assert missing_params(
        step, devices=_devices(), groups={"amps": ["amp", "amp2"]},
    ) is None
    assert missing_params(step, devices=_devices(amp=AMP), groups={}) is None


def test_groups_are_read_off_a_project_however_it_is_held():
    project = ProjectConfig.model_validate({
        "project": {"id": "g", "name": "G", "description": ""},
        "device_groups": [{"id": "amps", "name": "Amps", "device_ids": ["amp"]}],
    })
    assert device_groups(project) == {"amps": ["amp"]}
    assert device_groups({"device_groups": [{"id": "amps", "device_ids": ["amp"]}]}) == {
        "amps": ["amp"]
    }
    assert device_groups(None) == {}


# --- Gate 1: the macro lint the editor draws --------------------------------


def _issue_messages(steps, devices, project=None):
    return [
        issue["message"]
        for issue in macro_issues(
            steps, [], missing_params=missing_params_check(devices, project),
        )
    ]


def test_the_macro_lint_reports_a_chosen_command_that_cannot_run():
    steps = [_action()]
    assert _issue_messages(steps, _devices(amp=AMP)) == [
        "'set_fader': 'channel' is required",
        "'set_fader': 'level' is required",
    ]


def test_the_macro_lint_places_it_on_the_row_somebody_has_to_open():
    nested = {
        "action": "conditional",
        "condition": {"key": "var.on", "operator": "truthy"},
        "then_steps": [_action()],
    }
    issues = macro_issues(
        [{"action": "delay", "seconds": 1}, nested],
        [],
        missing_params=missing_params_check(_devices(amp=AMP), None),
    )
    assert [(i["scope"], i["index"], i["path"]) for i in issues] == [
        ("step", 1, "steps[1].then_steps[0]"),
        ("step", 1, "steps[1].then_steps[0]"),
    ]


def test_the_macro_lint_is_silent_without_the_driver():
    assert _issue_messages([_action()], _devices()) == []


def test_a_filled_in_step_is_clean():
    step = _action(params={"channel": 1, "level": -6})
    assert _issue_messages([step], _devices(amp=AMP)) == []


def test_the_ai_refusal_and_the_editor_records_stay_one_traversal():
    """``validate_macro`` is written over ``macro_issues``; keep it that way."""
    steps = [_action()]
    check = missing_params_check(_devices(amp=AMP), None)
    err = validate_macro(steps, [], extra_actions=(), missing_params=check)
    assert err == (
        "Macro validation failed: steps[0]: 'set_fader': 'channel' is required; "
        "steps[0]: 'set_fader': 'level' is required"
    )


# --- Gate 2: the UI binding review the AI write door runs -------------------


def _page(action):
    project = ProjectConfig.model_validate({
        "project": {"id": "refs", "name": "Refs", "description": ""},
        "ui": {"settings": {}, "pages": [{
            "id": "main", "name": "Main", "layouts": [], "elements": [{
                "id": "fader_1", "type": "fader",
                "bindings": {"do": {"change": [action]}},
            }],
        }], "master_elements": []},
    })
    return project.ui.pages[0]


def _reference_messages(action, devices):
    return [
        f.message for f in reference_findings(
            _page(action),
            page_ids={"main"},
            device_ids={"amp"},
            macro_ids=set(),
            device_commands=lambda _id: set(DRIVER_INFO["commands"]),
            missing_params=missing_params_check(devices, None),
        )
    ]


def test_the_binding_review_names_the_parameters_and_quotes_the_refusal():
    assert _reference_messages(_action(), _devices(amp=AMP)) == [
        "fader_1 (fader) do.change[0] sends 'set_fader' to 'amp' without 'channel' and "
        "'level'. Its driver requires them, so the device refuses every press with "
        "\"'set_fader': 'channel' is required\"."
    ]


def test_one_missing_parameter_reads_as_one_thing():
    assert _reference_messages(
        _action(params={"level": -6}), _devices(amp=AMP),
    ) == [
        "fader_1 (fader) do.change[0] sends 'set_fader' to 'amp' without 'channel'. "
        "Its driver requires it, so the device refuses every press with "
        "\"'set_fader': 'channel' is required\"."
    ]


def test_a_command_the_driver_does_not_have_is_still_reported_as_that():
    """The older complaint wins; two sentences about one field help nobody."""
    messages = _reference_messages(_action(command="reboot"), _devices(amp=AMP))
    assert len(messages) == 1
    assert "which its driver does not have" in messages[0]


def test_the_binding_review_reaches_a_value_map_the_way_the_engine_does():
    action = {
        "action": "value_map",
        "map": {"1": _action()},
    }
    messages = _reference_messages(action, _devices(amp=AMP))
    assert messages == [
        "fader_1 (fader) do.change[0]['1'] sends 'set_fader' to 'amp' without 'channel' "
        "and 'level'. Its driver requires them, so the device refuses every press with "
        "\"'set_fader': 'channel' is required\"."
    ]


def test_the_binding_review_is_silent_without_the_driver():
    assert _reference_messages(_action(), _devices()) == []
    assert reference_findings(
        _page(_action()),
        page_ids={"main"}, device_ids={"amp"}, macro_ids=set(),
        device_commands=lambda _id: set(DRIVER_INFO["commands"]),
    ) == []


# --- Gate 3: the door the Builder's Validate asks through -------------------


@pytest.fixture
def client(tmp_path):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)

    engine = MagicMock()
    engine.macros = MacroEngine(state, events, MagicMock())
    engine.devices = _devices(amp=AMP)
    engine.project = None
    engine.project_path = tmp_path / "project.avc"
    engine._project_revision = 0
    engine.apply_project = AsyncMock(return_value=1)
    engine.broadcast_ws = AsyncMock()

    app = FastAPI()
    app.include_router(router)
    set_engine(engine)
    yield TestClient(app, raise_server_exceptions=False)
    set_engine(None)


def _validate(http, actions):
    resp = http.post("/api/ui/validate-actions", json={"actions": actions})
    assert resp.status_code == 200, resp.text
    return resp.json()["issues"]


def test_the_builder_gets_the_runtime_sentence_back(client):
    assert _validate(client, {"a": _action()}) == {
        "a": [
            "'set_fader': 'channel' is required",
            "'set_fader': 'level' is required",
        ],
    }


def test_an_action_with_nothing_wrong_is_simply_absent(client):
    assert _validate(client, {
        "ok": _action(params={"channel": 1, "level": -6}),
        "unknown_device": _action(device="nope"),
        "bad": _action(),
    }) == {"bad": [
        "'set_fader': 'channel' is required",
        "'set_fader': 'level' is required",
    ]}


def test_the_door_refuses_only_what_it_cannot_read(client):
    assert client.post("/api/ui/validate-actions", json={}).status_code == 400
    assert client.post("/api/ui/validate-actions", json={"actions": []}).status_code == 400
    assert client.post(
        "/api/ui/validate-actions", content=b"{not json",
    ).status_code == 400
    # A non-object entry is skipped rather than 400ing the whole request: one
    # malformed action must not cost the Builder every other answer.
    assert _validate(client, {"junk": "nope", "bad": _action()}) == {"bad": [
        "'set_fader': 'channel' is required",
        "'set_fader': 'level' is required",
    ]}
