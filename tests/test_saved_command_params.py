"""A secret command parameter saved in a macro step or a control's action.

A parameter the device's driver marks secret (a stream key, a pairing PIN)
is kept in this system's login file, as a device login is, and the step holds
a marker naming its saved value instead (``core/saved_params.py``). What is
pinned here:

- No copy holds the value: the project file, what the Programmer reads, what a
  panel is sent, and what the AI reads back.
- Every way it comes in moves it: an edit, a project loaded with one in the
  clear, a Project Library copy.
- It still runs: a macro step, a group step and a control's press send the
  saved value; two steps holding the same marker both do.
- Nothing else sends it: a marker with nothing saved here, a marker sent from
  any other door, one copied into a parameter the driver does not mark secret,
  and one reached through a variable are refused, and nothing is sent.
- The log, a simulated press and a step's progress keep the marker.
- Copies carry it as they carry a device login: a backup, an export sealed
  with a passphrase, a Project Library copy, and a rollback, which puts the
  value back in the step.

An invented device throughout.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from openavc.api import rest
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core import saved_params
from openavc.core.device_logins import (
    SAVED_LOGIN,
    DeviceLogins,
    in_project,
    move_logins,
    put_back_for_rollback,
)
from openavc.core.project_diff import ProjectOrigin
from openavc.core.project_loader import ProjectConfig
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver, CommandParamError
from openavc.main import app

DRIVER = "acme_streamer"
KEY = "live-Acme-KEY-7f3a"
PIN = "4821"


class _AcmeStreamer(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Streamer",
        "transport": "tcp",
        "default_config": {"port": 5600},
        "config_schema": {"host": {"type": "string", "required": True, "label": "IP Address"}},
        "state_variables": {"label": {"type": "string", "label": "Label"}},
        "commands": {
            "set_stream_key": {
                "label": "Set Stream Key",
                "params": {
                    "channel": {"type": "integer", "label": "Channel"},
                    "key": {"type": "string", "secret": True, "required": True, "label": "Stream Key"},
                },
            },
            # A parameter named password is a secret by its name alone.
            "pair": {"label": "Pair", "params": {"password": {"type": "string"}}},
            # Echoes its text into state: a marker must never be filled in here.
            "set_label": {"label": "Set Label", "params": {"text": {"type": "string", "label": "Text"}}},
        },
    }

    #: Every command the driver was handed, as (device id, command, params).
    sent: list[tuple[str, str, dict[str, Any]]] = []

    async def connect(self) -> None:
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        _AcmeStreamer.sent.append((self.device_id, command, dict(params or {})))
        if command == "set_label":
            self.set_state("label", (params or {}).get("text"))
        return None


@pytest.fixture(autouse=True)
def _driver(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    registry.register_driver(_AcmeStreamer)
    _AcmeStreamer.sent = []


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(folder))
    return folder


def _command(command: str = "set_stream_key", **params: Any) -> dict[str, Any]:
    return {"action": "device.command", "device": "enc1", "command": command,
            "params": params or {"channel": 1, "key": KEY}}


def _document(*, steps: list[dict[str, Any]] | None = None,
              press: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A project with two encoders in a group, a macro and a page with one button."""
    button: dict[str, Any] = {
        "id": "go_live", "type": "button", "label": "Go Live",
        "bindings": {"do": {"press": press if press is not None else [_command()]}},
    }
    return {
        "openavc_version": "0.13.0",
        "project": {"id": "hall", "name": "Hall"},
        "devices": [
            {"id": "enc1", "name": "Encoder 1", "driver": DRIVER},
            {"id": "enc2", "name": "Encoder 2", "driver": DRIVER},
        ],
        "connections": {"enc1": {"host": "192.0.2.21"}, "enc2": {"host": "192.0.2.22"}},
        "device_groups": [{"id": "encoders", "name": "Encoders", "device_ids": ["enc1", "enc2"]}],
        "macros": [{"id": "start", "name": "Start Stream",
                    "steps": steps if steps is not None else [_command()]}],
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [button]}]},
    }


async def _engine(tmp_path, document: dict[str, Any]):
    from openavc.core.engine import Engine

    path = tmp_path / "project" / "project.avc"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    eng = Engine(str(path))
    with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
        await eng.reload_project()
    for device_id in ("enc1", "enc2"):
        driver = _AcmeStreamer(device_id, {}, eng.state, eng.events)
        await driver.connect()
        eng.devices._devices[device_id] = driver
    return eng


@pytest.fixture
async def engine(data, tmp_path):
    eng = await _engine(tmp_path, _document(steps=[]))
    rest.set_engine(eng)
    try:
        yield eng
    finally:
        rest.set_engine(None)
        await eng.devices.disconnect_all()


async def _edit(engine, document: dict[str, Any]) -> None:
    """Save ``document`` the way the Programmer does."""
    await engine.apply_project(ProjectConfig.model_validate(document), origin=ProjectOrigin.EDIT)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


def _tools(engine) -> AIToolHandler:
    agent = MagicMock()
    agent.state = engine.state
    return AIToolHandler(agent, engine.devices, engine.events, project_path=engine.project_path)


def _nowhere(*texts: Any, value: str = KEY) -> None:
    for text in texts:
        assert value not in (text if isinstance(text, str) else json.dumps(text, default=str))


# --- no copy holds the value --------------------------------------------------------


async def test_a_key_typed_into_a_macro_step_is_in_no_copy_of_the_project(engine):
    await _edit(engine, _document())

    async with _client() as http:
        read = await http.get("/api/project")
    assert read.status_code == 200, read.text
    macro = await _tools(engine)._get_macro({"macro_id": "start"})
    _nowhere(
        engine.project_path.read_text(encoding="utf-8"),
        engine.project.model_dump(mode="json"),
        read.text,
        macro,
    )


async def test_a_key_on_a_control_never_reaches_a_panel(engine):
    held = dict(_command(), hold_action=_command(key=KEY, channel=2))
    await _edit(engine, _document(press=[held]))

    _nowhere(engine.project_path.read_text(encoding="utf-8"), engine.panel_ui())


async def test_a_project_loaded_with_a_key_in_the_clear_moves_it_before_anything_runs(data, tmp_path):
    eng = await _engine(tmp_path, _document())
    try:
        _nowhere(eng.project_path.read_text(encoding="utf-8"), eng.project.model_dump(mode="json"))
    finally:
        await eng.devices.disconnect_all()


async def test_the_ai_writing_a_key_into_a_macro_moves_it_too(engine):
    answer = await _tools(engine)._update_macro({"macro_id": "start", "steps": [_command()]})
    assert "error" not in answer, answer
    _nowhere(engine.project_path.read_text(encoding="utf-8"), engine.project.model_dump(mode="json"))


async def test_a_value_read_from_state_is_left_as_it_is(engine):
    await _edit(engine, _document(steps=[_command(channel=1, key="$var.stream_key")]))
    assert _step_params(engine)["key"] == "$var.stream_key"


# --- it still runs --------------------------------------------------------------


def _step_params(engine, index: int = 0) -> dict[str, Any]:
    return engine.project.macros[0].steps[index].params


async def test_the_macro_sends_the_saved_key(engine, caplog):
    await _edit(engine, _document())
    with caplog.at_level(logging.DEBUG, logger="openavc.core.macro_engine"):
        assert await engine.macros.execute("start") == "completed"
    assert _AcmeStreamer.sent == [("enc1", "set_stream_key", {"channel": 1, "key": KEY})]
    _nowhere(caplog.text)
    assert saved_params.is_marker(_step_params(engine)["key"])


async def test_a_group_step_sends_the_saved_key_to_every_member(engine):
    group = {"action": "group.command", "group": "encoders", "command": "set_stream_key",
             "params": {"channel": 3, "key": KEY}}
    await _edit(engine, _document(steps=[group]))
    assert await engine.macros.execute("start") == "completed"
    assert sorted(_AcmeStreamer.sent) == [
        ("enc1", "set_stream_key", {"channel": 3, "key": KEY}),
        ("enc2", "set_stream_key", {"channel": 3, "key": KEY}),
    ]


async def test_a_press_sends_the_saved_key_and_reports_the_marker(engine):
    await _edit(engine, _document())
    result = await engine.handle_ui_event("press", "go_live")
    assert _AcmeStreamer.sent == [("enc1", "set_stream_key", {"channel": 1, "key": KEY})]
    assert result[0]["sent"] is True
    _nowhere(result)


async def test_a_simulated_press_reports_the_marker(engine):
    await _edit(engine, _document())
    result = await engine.handle_ui_event("press", "go_live", dry_run=True)
    assert _AcmeStreamer.sent == []
    _nowhere(result)


async def test_a_password_parameter_is_saved_by_its_name(engine):
    await _edit(engine, _document(steps=[_command("pair", password=PIN)]))
    assert saved_params.is_marker(_step_params(engine)["password"])
    assert await engine.macros.execute("start") == "completed"
    assert _AcmeStreamer.sent == [("enc1", "pair", {"password": PIN})]


async def test_a_duplicated_step_shares_the_key_until_one_is_changed(engine):
    await _edit(engine, _document())
    saved = _step_params(engine)["key"]
    copy = _command(channel=2, key=saved)
    changed = _command(channel=3, key="other-KEY-9")
    await _edit(engine, _document(steps=[_command(channel=1, key=saved), copy, changed]))

    assert _step_params(engine, 1)["key"] == saved
    assert saved_params.is_marker(_step_params(engine, 2)["key"])
    assert _step_params(engine, 2)["key"] != saved
    assert await engine.macros.execute("start") == "completed"
    assert [p["key"] for _, _, p in _AcmeStreamer.sent] == [KEY, KEY, "other-KEY-9"]


# --- nothing else sends it --------------------------------------------------------


async def test_a_key_not_saved_here_is_refused_and_nothing_is_sent(data, tmp_path):
    missing = saved_params.marker("0123456789abcdef")
    eng = await _engine(tmp_path, _document(steps=[_command(channel=1, key=missing)],
                                           press=[_command(channel=1, key=missing)]))
    try:
        assert await eng.macros.execute("start") != "completed"
        result = await eng.handle_ui_event("press", "go_live")
        assert _AcmeStreamer.sent == []
        assert result[0]["sent"] is False
        assert "Stream Key is not saved on this system" in result[0]["error"]
    finally:
        await eng.devices.disconnect_all()


async def test_a_marker_from_any_other_door_is_refused(engine):
    await _edit(engine, _document())
    saved = _step_params(engine)["key"]
    for params, kwargs in (
        ({"channel": 1, "key": saved}, {}),                     # REST, WS, a script, the AI
        ({"channel": 1, "key": SAVED_LOGIN}, {"saved": {"key"}}),  # a device login's marker
    ):
        with pytest.raises(CommandParamError, match="Stream Key is not saved on this system"):
            await engine.devices.send_command("enc1", "set_stream_key", params, **kwargs)
    assert _AcmeStreamer.sent == []


async def test_a_marker_copied_into_a_parameter_that_is_not_secret_is_refused(engine):
    await _edit(engine, _document())
    saved = _step_params(engine)["key"]
    await _edit(engine, _document(steps=[_command("set_label", text=saved)]))
    assert await engine.macros.execute("start") != "completed"
    assert _AcmeStreamer.sent == []
    assert engine.state.get("device.enc1.label") is None


async def test_a_marker_reached_through_a_variable_is_refused(engine):
    await _edit(engine, _document())
    engine.state.set("var.stream_key", _step_params(engine)["key"])
    await _edit(engine, _document(steps=[_command(channel=1, key="$var.stream_key")]))
    assert await engine.macros.execute("start") != "completed"
    assert _AcmeStreamer.sent == []


# --- copies ---------------------------------------------------------------------


async def test_backups_exports_and_the_library_carry_the_key(engine, data):
    from openavc.core import login_bundle
    from openavc.core.device_logins import FoundView, StoreView, put_back, saved_logins_of

    await _edit(engine, _document())
    document = json.loads(engine.project_path.read_text(encoding="utf-8"))
    found = saved_logins_of(document, StoreView(engine.device_logins))
    # The macro step's and the button's, each saved under its own name.
    assert [f.fields for f in found if f.kind == "parameter"] == [{"value": KEY}, {"value": KEY}]

    sealed = login_bundle.seal(found, "correct horse battery")
    opened = login_bundle.open_bundle(sealed, "correct horse battery")
    restored = put_back(document, FoundView(opened))
    assert restored["macros"][0]["steps"][0]["params"]["key"] == KEY

    backup = engine.device_logins.backup_copy(in_project(document))
    assert backup is not None and KEY not in backup


async def test_a_step_that_is_gone_lets_its_key_go(engine):
    await _edit(engine, _document())
    name = saved_params.name_of(_step_params(engine)["key"])
    await _edit(engine, _document(steps=[], press=[]))
    engine.device_logins.sweep(in_project(engine.project))
    key = (None, "parameter", name, "")
    assert engine.device_logins._entries[key].removed_at is not None


async def test_a_rollback_puts_the_key_back_in_its_step(engine, data):
    await _edit(engine, _document())
    put_back_for_rollback(data, engine.project_path)
    document = json.loads(engine.project_path.read_text(encoding="utf-8"))
    assert document["macros"][0]["steps"][0]["params"]["key"] == KEY
    assert document["ui"]["pages"][0]["elements"][0]["bindings"]["do"]["press"][0]["params"]["key"] == KEY


def test_a_library_copy_moves_its_keys_under_its_own_id(data):
    logins = DeviceLogins(data)
    moved = move_logins(_document(), logins, typed=False, library="lobby")
    name = saved_params.name_of(moved["macros"][0]["steps"][0]["params"]["key"])
    assert logins.secret(("parameter", name, ""), "value", library="lobby") == KEY
    assert logins.secret(("parameter", name, ""), "value") is None


# --- the Programmer's box ---------------------------------------------------------


async def test_the_programmers_box_saves_a_value_and_gets_a_marker(engine):
    async with _client() as http:
        answer = await http.post("/api/saved-params", json={"value": KEY})
        assert answer.status_code == 200, answer.text
        saved = answer.json()["marker"]
        name = saved_params.name_of(saved)
        assert (await http.get(f"/api/saved-params/{name}")).json() == {"saved": True}
        assert (await http.get("/api/saved-params/0123456789abcdef")).json() == {"saved": False}
        assert (await http.get("/api/saved-params/not-a-name")).status_code == 404
        assert (await http.post("/api/saved-params", json={"value": saved})).status_code == 400
        assert (await http.post("/api/saved-params", json={"value": ""})).status_code == 422
    _nowhere(answer.text)
    await _edit(engine, _document(steps=[_command(channel=1, key=saved)]))
    assert await engine.macros.execute("start") == "completed"
    assert _AcmeStreamer.sent == [("enc1", "set_stream_key", {"channel": 1, "key": KEY})]


# --- after the fill, and the Programmer's lint ----------------------------------------


async def test_a_saved_key_is_masked_wherever_a_device_password_is(engine):
    """The log filter and the device traffic view mask what the registry holds
    for the device, as they mask its password."""
    from openavc.utils.log_redaction import get_secret_registry

    await _edit(engine, _document())
    assert KEY in get_secret_registry().secrets_for("enc1")


NOT_SAVED_HERE = ("Stream Key is not saved on this system. Enter it in the macro step or control "
                  "that sends Set Stream Key.")


async def test_the_programmers_lint_marks_a_key_not_saved_here(engine):
    missing = _command(channel=1, key=saved_params.marker("0123456789abcdef"))
    here = _command(channel=1, key=engine.save_param_value(KEY))
    async with _client() as http:
        lint = await http.post("/api/macros/validate", json={"macros": [
            {"id": "missing", "steps": [missing]}, {"id": "here", "steps": [here]},
        ]})
        actions = await http.post("/api/ui/validate-actions", json={"actions": {"missing": missing, "here": here}})
    assert lint.status_code == 200 and actions.status_code == 200
    messages = {mid: [i["message"] for i in entry["issues"]] for mid, entry in lint.json()["macros"].items()}
    assert messages == {"missing": [NOT_SAVED_HERE], "here": []}
    assert actions.json()["issues"] == {"missing": [NOT_SAVED_HERE]}


async def test_a_group_member_whose_driver_does_not_mark_it_secret_is_refused(engine, monkeypatch):
    """A group whose drivers disagree: the member that does not mark the
    parameter secret is never handed the saved value."""
    group = {"action": "group.command", "group": "encoders", "command": "set_stream_key",
             "params": {"channel": 3, "key": KEY}}
    await _edit(engine, _document(steps=[group]))
    plain = {**_AcmeStreamer.DRIVER_INFO["commands"]["set_stream_key"]["params"]["key"], "secret": False}
    driver = engine.devices._devices["enc2"]
    info = {**driver.DRIVER_INFO, "commands": {**driver.DRIVER_INFO["commands"], "set_stream_key": {
        "label": "Set Stream Key", "params": {"channel": {"type": "integer"}, "key": plain}}}}
    monkeypatch.setattr(driver, "DRIVER_INFO", info)
    await engine.macros.execute("start")
    assert _AcmeStreamer.sent == [("enc1", "set_stream_key", {"channel": 3, "key": KEY})]


async def test_testing_an_unsaved_action_fills_in_its_saved_key(engine):
    """The UI Builder's Test sends the action through the device's command
    door, naming the params that hold a saved value."""
    saved = engine.save_param_value(KEY)
    body = {"command": "set_stream_key", "params": {"channel": 1, "key": saved}}
    async with _client() as http:
        refused = await http.post("/api/devices/enc1/command", json=body)
        sent = await http.post("/api/devices/enc1/command", json={**body, "saved": ["key"]})
        plain = await http.post("/api/devices/enc1/command",
                                json={"command": "set_label", "params": {"text": saved}, "saved": ["text"]})
    assert refused.status_code == 400 and "Stream Key is not saved on this system" in refused.text
    assert sent.status_code == 200, sent.text
    assert plain.status_code == 400
    assert _AcmeStreamer.sent == [("enc1", "set_stream_key", {"channel": 1, "key": KEY})]
    _nowhere(sent.text)
