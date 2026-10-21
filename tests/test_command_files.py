"""Commands that take a file or hand one back.

A Python driver declares a ``file`` param (it receives a CommandFile) or
``returns_file`` (it returns a DeviceFile). Such a command runs only from
Send Command's background runs and from a Device Audit; every other door is
refused at the one gate in ``DeviceManager.send_command``, and the authoring
checks say so before anything runs. The file goes up in chunks, is never
recorded by the traffic recorder, and is deleted when its command ends. All
against an invented device (Acme).
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from openavc.api import rest
from openavc.api.routes import command_files as file_routes
from openavc.api.routes import devices as device_routes
from openavc.core import file_staging as staging_mod
from openavc.core.command_params import file_command_reason
from openavc.core.command_runs import CANCELLED, DONE, ERROR, CommandRunInProgress, CommandRuns
from openavc.core.device_manager import DeviceManager
from openavc.core.device_traffic import RX, TX, get_traffic_recorder, record_traffic, serialize_entry
from openavc.core.event_bus import EventBus
from openavc.core.file_staging import FileStaging, StagingError
from openavc.core.macro_validation import macro_issues
from openavc.core.state_store import StateStore
from openavc.drivers.actions import resolve_device_actions
from openavc.drivers.base import BaseDriver
from openavc.drivers.command_files import CommandFile, DeviceFile, FileCommandError
from openavc.drivers.python_info import python_driver_info_issues
from openavc.drivers.spec import platform_requirements
from openavc.ui.page_references import reference_findings
from tests.test_audit_api import wired  # noqa: F401  (the fixture)

TAKES = "'Load Firmware' takes a file, so it runs from Send Command on the device's page."
GIVES = "'Back Up Settings' sends back a file, so it runs from Send Command on the device's page."


class AcmeFiles(BaseDriver):
    """Takes a firmware image a chunk at a time and hands back what it holds."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_files",
        "name": "Acme Files",
        "transport": "tcp",
        "min_platform_version": "0.37.2",
        "state_variables": {},
        "commands": {
            "load_firmware": {
                "label": "Load Firmware",
                "params": {
                    "image": {
                        "type": "file", "label": "Firmware", "required": True,
                        "accept": [".bin"], "max_bytes": 50_000_000,
                    },
                    "slot": {"type": "integer", "min": 1, "max": 2},
                },
            },
            "backup": {"label": "Back Up Settings", "returns_file": True},
            "power_on": {"label": "Power On"},
            "broken_backup": {"label": "Broken Backup", "returns_file": True},
        },
        "quick_actions": ["power_on"],
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.held = b""
        self.received: list[Any] = []
        self.gate: asyncio.Event | None = None

    async def _create_transport(self, transport_type):
        pass

    def _link_alive(self):
        return True

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        params = params or {}
        if command == "load_firmware":
            image = params["image"]
            self.received.append(image)
            data = bytearray()
            async for chunk in image.chunks(1000):
                # What a transport would record for the bytes it sent.
                record_traffic(self.device_id, TX, chunk, channel="tcp")
                data.extend(chunk)
                if self.gate is not None:
                    await self.gate.wait()
            record_traffic(self.device_id, RX, b"OK", channel="tcp")
            self.held = bytes(data)
            return {"stored": len(data)}
        if command == "backup":
            record_traffic(self.device_id, TX, b"DUMP", channel="tcp")
            record_traffic(self.device_id, RX, self.held, channel="tcp")
            return DeviceFile("acme-backup.cfg", self.held, "text/plain")
        if command == "broken_backup":
            return True
        return True


@pytest.fixture(autouse=True)
def _clean_recorder():
    yield
    get_traffic_recorder().clear()


def _manager() -> tuple[DeviceManager, AcmeFiles]:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    driver = AcmeFiles("dev1", {"host": "127.0.0.1", "port": 1}, state, events)
    driver.set_state("connected", True)
    dm._devices["dev1"] = driver
    dm._device_configs["dev1"] = {
        "id": "dev1", "driver": "acme_files", "name": "Dev 1",
        "config": {"host": "127.0.0.1", "port": 1}, "enabled": True,
    }
    return dm, driver


def _command_file(tmp_path, data: bytes, name: str = "fw.bin") -> CommandFile:
    path = tmp_path / f"{name}.staged"
    path.write_bytes(data)
    return CommandFile(name, len(data), path)


# --- the contract ---------------------------------------------------------------


def test_a_python_driver_declares_a_file_param_at_0_37_2():
    assert python_driver_info_issues(AcmeFiles.DRIVER_INFO) == []
    floors = dict(platform_requirements(AcmeFiles.DRIVER_INFO))
    assert floors["commands.load_firmware.params.image.type: file"] == "0.37.2"
    assert floors["commands.backup.returns_file"] == "0.37.2"
    info = {**AcmeFiles.DRIVER_INFO, "min_platform_version": "0.37.1"}
    assert any("0.37.2" in issue for issue in python_driver_info_issues(info))
    shaped = {"id": "acme_x", "name": "X", "transport": "tcp", "commands": {"up": {
        "params": {"f": {"type": "file", "default": "x", "accept": ["bin"], "max_bytes": 0}},
    }}}
    assert python_driver_info_issues(shaped) == [
        "Command 'up' param 'f': a file param takes no default",
        "Command 'up' param 'f': accept must list file-name endings, e.g. [\".bin\"]",
        "Command 'up' param 'f': max_bytes must be a whole number of bytes from 1 to "
        "1073741824 (1 GiB)",
    ]


# --- the one gate -----------------------------------------------------------------


async def test_every_door_without_a_file_is_refused_with_where_it_runs(tmp_path):
    dm, driver = _manager()
    with pytest.raises(FileCommandError) as exc:
        await dm.send_command("dev1", "load_firmware", {"image": "fw.bin"})
    assert str(exc.value) == TAKES
    with pytest.raises(FileCommandError) as exc:
        await dm.send_command("dev1", "backup")
    assert str(exc.value) == GIVES
    # Before the connected gate: the sentence that helps is where it runs.
    driver.set_state("connected", False)
    with pytest.raises(FileCommandError):
        await dm.send_command("dev1", "load_firmware", {})
    assert driver.received == []


async def test_only_a_file_fills_a_file_param(tmp_path):
    dm, driver = _manager()
    with pytest.raises(FileCommandError, match="Firmware takes a file chosen"):
        await dm.send_command(
            "dev1", "load_firmware", {"image": "C:/fw.bin"}, files_allowed=True,
        )
    data = bytes(range(256)) * 20
    result = await dm.send_command(
        "dev1", "load_firmware", {"image": _command_file(tmp_path, data), "slot": 2},
        files_allowed=True,
    )
    assert result == {"stored": len(data)} and driver.held == data
    assert driver.received[0].bytes_read == len(data)
    assert repr(driver.received[0]) == "fw.bin (5.1 KB)"


async def test_a_command_that_must_hand_back_a_file_and_does_not(tmp_path):
    dm, _driver = _manager()
    with pytest.raises(FileCommandError, match="'Broken Backup' finished without"):
        await dm.send_command("dev1", "broken_backup", files_allowed=True)


async def test_the_file_is_never_recorded_only_named(tmp_path):
    dm, driver = _manager()
    recorder = get_traffic_recorder()
    data = b"\x7fELF" + b"\xaa" * 3000
    await dm.send_command(
        "dev1", "load_firmware", {"image": _command_file(tmp_path, data)}, files_allowed=True,
    )
    entries = [serialize_entry(e) for e in recorder.get_recent("dev1")]
    assert entries[0]["channel"] == "file" and entries[0]["meta"]["file"] == "fw.bin"
    assert entries[0]["meta"]["size"] == len(data)
    sent = [e for e in entries if e["direction"] == TX and e["channel"] == "tcp"]
    assert sent and all(e["hex"] == "" and e["meta"]["withheld"] > 0 for e in sent)
    assert sum(e["meta"]["withheld"] for e in sent) == len(data)
    # The device's answer to an upload is kept whole.
    assert [e["text"] for e in entries if e["direction"] == RX] == ["OK"]

    held = await dm.send_command("dev1", "backup", files_allowed=True)
    assert isinstance(held, DeviceFile) and held.data == data
    tail = [serialize_entry(e) for e in recorder.get_recent("dev1")][len(entries):]
    assert [e["text"] for e in tail if e["direction"] == TX and e["channel"] == "tcp"] == ["DUMP"]
    received = [e for e in tail if e["direction"] == RX and e["channel"] == "tcp"]
    assert received[0]["hex"] == "" and received[0]["meta"]["withheld"] == len(data)
    assert tail[-1]["channel"] == "file" and tail[-1]["meta"]["file"] == "acme-backup.cfg"

    # Once the command is over, the device's traffic is kept whole again.
    record_traffic("dev1", TX, b"PWR?", channel="tcp")
    assert recorder.get_recent("dev1")[-1].data == b"PWR?"
    assert data not in b"".join(e.data for e in recorder.get_recent("dev1"))


# --- staging ------------------------------------------------------------------------


PDEF = AcmeFiles.DRIVER_INFO["commands"]["load_firmware"]["params"]["image"]


def _begin(staging: FileStaging, name="fw.bin", size=10, **kw):
    return staging.begin(owner="device:dev1", command="load_firmware", param="image",
                         pdef=kw.pop("pdef", PDEF), name=name, size=size, **kw)


async def test_a_file_that_will_not_do_is_refused_before_a_byte(tmp_path, monkeypatch):
    staging = FileStaging(tmp_path / "stage")
    with pytest.raises(StagingError) as exc:
        _begin(staging, size=60_000_000)
    assert exc.value.status == 413
    assert str(exc.value) == "fw.bin is 60 MB. Firmware takes a file up to 50 MB."
    with pytest.raises(StagingError, match=r"Firmware takes a \.bin file\."):
        _begin(staging, name="fw.zip")
    with pytest.raises(StagingError, match="fw.bin is empty"):
        _begin(staging, size=0)
    monkeypatch.setattr(
        staging_mod.shutil, "disk_usage", lambda _p: SimpleNamespace(free=1000),
    )
    with pytest.raises(StagingError) as exc:
        _begin(staging, size=10)
    assert exc.value.status == 507 and "doesn't have room for fw.bin" in str(exc.value)
    # A path in the name is never a path on the server.
    monkeypatch.undo()
    upload = _begin(staging, name="..\\..\\evil/fw.bin")
    assert upload.name == "fw.bin" and upload.path.parent == staging.root


async def test_chunks_arrive_in_order_and_the_file_is_bound_to_its_command(tmp_path):
    staging = FileStaging(tmp_path / "stage")
    upload = _begin(staging, size=6)
    await staging.write(upload.id, 0, b"abc")
    with pytest.raises(StagingError) as exc:
        await staging.write(upload.id, 0, b"abc")
    assert exc.value.status == 409 and exc.value.fields == {"received": 3}
    with pytest.raises(StagingError) as exc:
        await staging.write(upload.id, 3, b"defg")
    assert exc.value.status == 413
    with pytest.raises(StagingError, match="has not finished uploading"):
        staging.take(upload.id, owner="device:dev1", command="load_firmware", param="image")
    await staging.write(upload.id, 3, b"def")
    with pytest.raises(StagingError, match="chosen for a different command"):
        staging.take(upload.id, owner="device:dev2", command="load_firmware", param="image")
    file = staging.take(upload.id, owner="device:dev1", command="load_firmware", param="image")
    assert await file.read() == b"abcdef"
    staging.discard(upload.id)
    assert staging.staged_paths() == []


async def test_what_nobody_used_expires_and_a_restart_leaves_nothing(tmp_path):
    staging = FileStaging(tmp_path / "stage")
    upload = _begin(staging)
    held = staging.hold("device:dev1", DeviceFile("b.cfg", b"x"))
    later = upload.touched + staging_mod.UPLOAD_IDLE_SECONDS + 1
    staging.sweep(now=later)
    assert staging.staged_paths() == [] and staging.held(held.id) is None
    leftover = staging.root / "old.part"
    leftover.write_bytes(b"from an earlier run")
    staging.start()
    try:
        assert not leftover.exists()
    finally:
        await staging.stop()


# --- runs -----------------------------------------------------------------------------


class _Engine:
    def __init__(self, tmp_path) -> None:
        self.devices, self.driver = _manager()
        self.state = self.devices.state
        self.project = None
        self.file_staging = FileStaging(tmp_path / "stage")
        self.command_runs = CommandRuns(self, self.file_staging)
        self.messages: list[tuple[dict, str | None]] = []

    async def broadcast_ws(self, message, *, client_type=None):
        self.messages.append((message, client_type))


async def _staged(engine: _Engine, data: bytes) -> str:
    upload = _begin(engine.file_staging, size=len(data))
    await engine.file_staging.write(upload.id, 0, data)
    return upload.id


def _describe(exc: Exception) -> str:
    return str(exc)


async def test_a_run_goes_in_the_background_and_deletes_the_file(tmp_path):
    engine = _Engine(tmp_path)
    runs = engine.command_runs
    data = b"\x01" * 4000
    upload_id = await _staged(engine, data)
    run = runs.start("dev1", "load_firmware", {"slot": 1}, {"image": upload_id},
                     describe_error=_describe)
    await run.task
    assert run.status == DONE and run.result == {"stored": 4000}
    assert engine.driver.held == data
    assert engine.file_staging.staged_paths() == []
    kinds = {(m["type"], m["status"]) for m, _ in engine.messages}
    assert ("command.run", "running") in kinds and ("command.run", DONE) in kinds
    assert {who for _, who in engine.messages} == {"programmer"}
    last = engine.messages[-1][0]
    assert last["files"] == [{"param": "image", "name": "fw.bin", "size": 4000, "read": 4000}]

    backup = runs.start("dev1", "backup", {}, {}, describe_error=_describe)
    await backup.task
    assert backup.returned == {"name": "acme-backup.cfg", "size": 4000}
    held = runs.returned_file("dev1", backup.id)
    assert held.path.read_bytes() == data
    await runs.downloaded("dev1", backup.id)
    assert runs.returned_file("dev1", backup.id) is None
    assert engine.file_staging.staged_paths() == []


async def test_one_run_at_a_time_and_a_stopped_run_frees_the_device(tmp_path):
    engine = _Engine(tmp_path)
    engine.driver.gate = asyncio.Event()
    runs = engine.command_runs
    first = runs.start("dev1", "load_firmware", {}, {"image": await _staged(engine, b"a" * 3000)},
                       describe_error=_describe)
    await asyncio.sleep(0.05)
    second_upload = await _staged(engine, b"b" * 10)
    with pytest.raises(CommandRunInProgress, match="'Load Firmware' is still running"):
        runs.start("dev1", "load_firmware", {}, {"image": second_upload}, describe_error=_describe)
    runs.cancel("dev1", first.id)
    await asyncio.gather(first.task, return_exceptions=True)
    assert first.status == CANCELLED
    engine.driver.gate = None
    second = runs.start("dev1", "load_firmware", {}, {"image": second_upload},
                        describe_error=_describe)
    await second.task
    assert second.status == DONE and engine.file_staging.staged_paths() == []
    with pytest.raises(ValueError, match="'Power On' takes no file"):
        runs.start("dev1", "power_on", {}, {}, describe_error=_describe)


async def test_a_failed_run_is_worded_like_send_command(tmp_path):
    engine = _Engine(tmp_path)
    engine.driver.set_state("connected", False)
    run = engine.command_runs.start(
        "dev1", "backup", {}, {}, describe_error=lambda exc: f"worded: {exc}",
    )
    await run.task
    assert run.status == ERROR and run.error == "worded: Device 'dev1' is not connected"


# --- the HTTP doors --------------------------------------------------------------------


@pytest.fixture
async def http(tmp_path):
    engine = _Engine(tmp_path)
    app = FastAPI()
    app.include_router(file_routes.router, prefix="/api")
    app.include_router(device_routes.router, prefix="/api")
    rest.set_engine(engine)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://openavc") as client:
            yield client, engine
    finally:
        rest.set_engine(None)


async def _upload(client, data: bytes, *, name="fw.bin", headers=None) -> tuple[str, int]:
    opened = await client.post("/api/devices/dev1/command-files", json={
        "command": "load_firmware", "param": "image", "name": name, "size": len(data),
    }, headers=headers or {})
    assert opened.status_code == 200, opened.text
    body = opened.json()
    upload_id, chunk = body["upload"]["id"], body["chunk_size"]
    for offset in range(0, len(data), chunk):
        piece = data[offset:offset + chunk]
        put = await client.put(
            f"/api/command-files/{upload_id}", params={"offset": offset}, content=piece,
            headers={"content-type": "application/octet-stream"},
        )
        assert put.status_code == 200, put.text
    return upload_id, chunk


async def test_a_file_goes_up_in_chunks_and_comes_back_down(http):
    client, engine = http
    data = hashlib.sha256(b"seed").digest() * 200_000  # 6.4 MB: four tunnel chunks
    upload_id, chunk = await _upload(client, data, headers={"x-openavc-tunneled": "1"})
    assert chunk == staging_mod.CHUNK_BYTES_TUNNEL
    started = await client.post("/api/devices/dev1/command-runs", json={
        "command": "load_firmware", "params": {"slot": "2"}, "files": {"image": upload_id},
    })
    assert started.status_code == 202, started.text
    run_id = started.json()["run"]["run_id"]
    await engine.command_runs.get("dev1", run_id).task
    assert (await client.get(f"/api/devices/dev1/command-runs/{run_id}")).json()["run"]["status"] == DONE

    started = await client.post("/api/devices/dev1/command-runs", json={"command": "backup"})
    run_id = started.json()["run"]["run_id"]
    await engine.command_runs.get("dev1", run_id).task
    listed = (await client.get("/api/devices/dev1/command-runs")).json()["runs"]
    assert [r["command"] for r in listed] == ["load_firmware", "backup"]
    download = await client.get(f"/api/devices/dev1/command-runs/{run_id}/file")
    assert download.status_code == 200 and download.content == data
    assert "content-length" not in download.headers  # streamed through a tunnel
    assert 'filename="acme-backup.cfg"' in download.headers["content-disposition"]
    gone = await client.get(f"/api/devices/dev1/command-runs/{run_id}/file")
    assert gone.status_code == 404
    assert engine.file_staging.staged_paths() == []


async def test_a_direct_connection_takes_large_chunks_and_a_wrong_file_is_refused(http):
    client, _engine = http
    _upload_id, chunk = await _upload(client, b"x" * 100)
    assert chunk == staging_mod.CHUNK_BYTES_DIRECT
    refused = await client.post("/api/devices/dev1/command-files", json={
        "command": "load_firmware", "param": "image", "name": "notes.txt", "size": 5,
    })
    assert refused.status_code == 400 and refused.json()["detail"] == "Firmware takes a .bin file."
    wrong = await client.post("/api/devices/dev1/command-files", json={
        "command": "load_firmware", "param": "slot", "name": "fw.bin", "size": 5,
    })
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "'Load Firmware' has no file parameter named 'slot'."


async def test_send_command_refuses_a_file_command_with_the_sentence(http):
    client, _engine = http
    resp = await client.post("/api/devices/dev1/command", json={"command": "backup"})
    assert resp.status_code == 409 and resp.json()["detail"] == GIVES


# --- every other door ------------------------------------------------------------------


def _devices_with_driver() -> SimpleNamespace:
    driver = SimpleNamespace(DRIVER_INFO=AcmeFiles.DRIVER_INFO)
    return SimpleNamespace(get_driver=lambda device_id: driver if device_id == "dev1" else None)


def test_the_authoring_checks_say_a_macro_cannot_run_it():
    devices = _devices_with_driver()
    step = {"action": "device.command", "device": "dev1", "command": "load_firmware"}
    assert file_command_reason(step, devices=devices) == TAKES
    group = {"action": "group.command", "group": "racks", "command": "backup"}
    assert file_command_reason(group, devices=devices, groups={"racks": ["dev1"]}) == GIVES
    plain = {"action": "device.command", "device": "dev1", "command": "power_on"}
    assert file_command_reason(plain, devices=devices) is None
    issues = macro_issues(
        [plain, {"action": "conditional", "condition": {"key": "var.x"}, "then_steps": [step]}],
        [], file_command=lambda s: file_command_reason(s, devices=devices),
    )
    assert issues == [{
        "scope": "step", "index": 1, "path": "steps[1].then_steps[0]",
        "message": TAKES, "action": "device.command", "refused": "file",
    }]


def test_a_panel_binding_to_it_is_reported():
    devices = _devices_with_driver()
    page = SimpleNamespace(id="main", elements=[{
        "id": "btn", "type": "button", "bindings": {"do": {"press": [
            {"action": "device.command", "device": "dev1", "command": "backup"},
        ]}},
    }])
    findings = reference_findings(
        page, page_ids={"main"}, device_ids={"dev1"}, macro_ids=set(),
        device_commands=lambda _d: set(AcmeFiles.DRIVER_INFO["commands"]),
        missing_params=lambda _a: None,
        file_command=lambda a: file_command_reason(a, devices=devices),
    )
    assert [f.kind for f in findings] == ["unrunnable_command"]
    assert GIVES in findings[0].message


def test_no_quick_action_button_for_it():
    # A Python driver is not load-validated, so a promoted one is dropped.
    info = {
        **AcmeFiles.DRIVER_INFO,
        "quick_actions": ["power_on", "backup"],
        "actions": [{"id": "flash", "command": "load_firmware"}],
    }
    resolved = resolve_device_actions(info)
    assert [a["id"] for a in resolved] == ["power_on"]


async def test_the_cloud_portal_is_not_offered_it():
    from openavc.cloud.command_handler import CommandHandler

    sent: list[Any] = []
    agent = SimpleNamespace(send_message=lambda *a: _record(sent, a))
    devices = SimpleNamespace(
        list_devices=lambda: [{"id": "dev1", "name": "Dev 1"}],
        get_device_info=lambda _d: {"commands": AcmeFiles.DRIVER_INFO["commands"]},
    )
    handler = CommandHandler.__new__(CommandHandler)
    handler._agent = agent
    handler._devices = devices
    await handler._handle_get_device_commands({}, "r1", "tester")
    payload = sent[0][1]
    assert list(payload["devices"][0]["commands"]) == ["power_on"]


async def _record(into: list, args: tuple) -> bool:
    into.append(args)
    return True


async def test_a_macro_step_and_a_panel_press_are_refused_with_the_sentence():
    from openavc.core.macro_engine import MacroEngine

    dm, _driver = _manager()
    errors: list[dict] = []

    async def heard(_event, data):
        errors.append(data)

    dm.events.on("macro.step_error.*", heard)
    macros = MacroEngine(dm.state, dm.events, dm)
    macros.load_macros([{"id": "m", "name": "M", "steps": [
        {"action": "device.command", "device": "dev1", "command": "load_firmware"},
    ]}])
    await macros.execute("m")
    await asyncio.sleep(0)
    assert errors and errors[0]["error"] == TAKES


# --- the Device Audit ----------------------------------------------------------------


async def _until(predicate, timeout: float = 8.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def test_an_audit_sends_a_file_and_its_report_names_it_by_size(wired, tmp_path):  # noqa: F811
    from fastapi import HTTPException
    from starlette.requests import Request

    from openavc.api.models import (
        AuditCommandRequest,
        AuditConnectionRequest,
        AuditDriverRequest,
        AuditStartRequest,
        CommandFileBeginRequest,
    )
    from openavc.api.routes import audit as routes
    from openavc.audit.commands import DONE as TRIAL_DONE
    from openavc.audit.report import build_report, render_summary, render_timeline
    from openavc.drivers.registry import _DRIVER_REGISTRY

    _DRIVER_REGISTRY["acme_files"] = AcmeFiles
    wired.engine.file_staging = FileStaging(tmp_path / "stage")
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    data = b"\xa5" * 2500
    try:
        started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
        session_id = started["session"]["session_id"]
        await routes.run_network_check(session_id)
        await _until(lambda: wired.manager.current().footprint is not None)
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_files"))
        wired.engine.state.set("device.lobby.paused", True)
        await routes.set_session_connection(session_id, AuditConnectionRequest(
            config={"host": "127.0.0.1", "port": 9},
        ))
        await routes.connect_and_listen(session_id)
        session = wired.manager.current()
        await _until(lambda: session.runs[0].listen.connected_at is not None)
        # A short window after each send, as the audit's own tests use.
        session.runs[0].commands.window_seconds = 0.3
        session.runs[0].commands.flush_seconds = 0.05
        catalog = {c["name"]: c for c in session.runs[0].commands.catalog()}
        assert catalog["load_firmware"]["takes_file"] and catalog["backup"]["returns_file"]

        opened = await routes.begin_upload(session_id, CommandFileBeginRequest(
            command="load_firmware", param="image", name="fw.bin", size=len(data),
        ), request)
        upload_id = opened["upload"]["id"]
        await wired.engine.file_staging.write(upload_id, 0, data)
        await routes.send_command(session_id, "load_firmware", AuditCommandRequest(
            files={"image": upload_id},
        ))
        commands = session.runs[0].commands
        sent = commands.trials[0]
        await _until(lambda: sent.status == TRIAL_DONE)
        assert sent.error == "" and sent.file_command
        assert sent.to_dict()["params"] == {"image": "fw.bin (2.5 KB)"}
        assert wired.engine.file_staging.staged_paths() == []
        with pytest.raises(HTTPException) as exc:
            await routes.send_command(session_id, "load_firmware", AuditCommandRequest(again=1))
        assert exc.value.status_code == 409
        assert exc.value.detail == "Choose the file again and send Load Firmware from the list."

        await routes.send_command(session_id, "backup", AuditCommandRequest())
        back = commands.trials[1]
        await _until(lambda: back.status == TRIAL_DONE)
        assert back.to_dict()["returned_file"] == {"name": "acme-backup.cfg", "size": 2500}
        download = await routes.download_returned_file(session_id, 2)
        body = b"".join([piece async for piece in download.body_iterator])
        assert body == data

        report = build_report(session)
        flat = repr(report) + render_summary(report) + render_timeline(report)
        assert "fw.bin" in flat and "not recorded while a file command ran" in flat
        assert data.hex() not in flat and "a5a5a5a5" not in flat
        await routes.end_session(session_id, cancel=True)
    finally:
        _DRIVER_REGISTRY.pop("acme_files", None)
        await wired.manager.shutdown()


def test_the_log_never_carries_a_held_file():
    from openavc.transport.wire_log import format_wire_data, wire_held

    recorder = get_traffic_recorder()
    assert format_wire_data(b"CHUNK 00ff", "dev1", TX) == "CHUNK 00ff"
    with recorder.hold("dev1", tx=True):
        # The transports skip the line; one formatted anyway gives the size.
        assert wire_held("dev1", TX) and not wire_held("dev1", RX)
        assert format_wire_data(b"CHUNK 00ff", "dev1", TX) == (
            "(10 bytes, not recorded while a file command ran)"
        )
        assert format_wire_data(b"OK", "dev1", RX) == "OK"
    assert not wire_held("dev1", TX)
