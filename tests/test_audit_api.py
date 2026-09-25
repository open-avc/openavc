"""The device audit's HTTP and WebSocket doors.

Routes are called as functions against a real ``AuditManager`` and a stub
engine whose project holds real device models; the network check is replaced
with a stand-in so no listener opens. The route table's auth and rate tier
are pinned in ``test_route_auth_posture.py`` and ``test_rate_limit_tiers.py``.
"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openavc.api.models import AuditStartRequest, AuditTesterRequest
from openavc.api.routes import audit as routes
from openavc.audit import footprint as fpmod
from openavc.audit.report import ReportStore
from openavc.audit.session import AuditManager
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY


class _AcmeAuditDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_audit_api", "name": "Acme", "manufacturer": "Acme",
        "category": "utility", "transport": "tcp", "default_config": {"port": 7000},
        "commands": {}, "state_variables": {}, "config_schema": {},
    }


_DRIVER_REGISTRY["acme_audit_api"] = _AcmeAuditDriver


class FakeDevices:
    """The device manager's pause surface, recording what it was asked."""

    def __init__(self, running: set[str]):
        self.running = running
        self.paused: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    async def pause_device(self, device_id, ttl=None):
        from openavc.core.device_manager import DeviceNotFoundError

        if device_id not in self.running:
            raise DeviceNotFoundError(device_id)
        self.paused.add(device_id)
        self.calls.append(("pause", device_id))

    async def resume_device(self, device_id):
        self.paused.discard(device_id)
        self.calls.append(("resume", device_id))

    def is_paused(self, device_id):
        return device_id in self.paused


class FakeCheck:
    """Stands in for the network check: no listener, a finished footprint."""

    def __init__(self, session):
        from openavc.audit.footprint import Footprint

        self.session = session
        self.status = "idle"
        self.error = ""
        self.activities = {key: fpmod.Activity(key) for key in fpmod.ACTIVITIES}
        self.footprint = Footprint(address=session.target.address, ip=session.target.ip)
        self.footprint.verdict = {"state": "unknown", "sentence": "OpenAVC can see this device "
                                  "but does not recognize it.", "catalog": {"used": "none"}}

    async def run(self):
        self.footprint.finished_at = self.footprint.started_at + 1
        return self.footprint


@pytest.fixture
def wired(monkeypatch, tmp_path):
    state = StateStore()
    state.set_event_bus(EventBus())
    project = SimpleNamespace(
        devices=[
            DeviceConfig(id="lobby", driver="acme_audit_api", name="Lobby Display",
                         config={"host": "127.0.0.1"}),
            DeviceConfig(id="spare", driver="acme_audit_api", name="Spare",
                         config={"host": "127.0.0.1"}, enabled=False),
            DeviceConfig(id="other", driver="acme_audit_api", name="Hall",
                         config={"host": "10.9.9.9"}),
        ],
        connections={},
    )
    engine = SimpleNamespace(project=project, state=state)
    devices = FakeDevices(running={"lobby", "other"})
    manager = AuditManager(devices)
    store = ReportStore(tmp_path / "audit_reports")

    async def fake_open(session, _discovery):
        check = FakeCheck(session)
        session.check = check
        session.add_state_provider(lambda: fpmod.check_state(session))
        return check

    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    monkeypatch.setattr(routes, "open_for_session", fake_open)
    for name in ("_manager", "_discovery", "_store"):
        monkeypatch.setattr(routes, name, None)
    routes.configure(manager, discovery=None, store=store)
    yield SimpleNamespace(engine=engine, devices=devices, manager=manager, store=store)


# ---------------------------------------------------------------------------
# Request models declare every field
# ---------------------------------------------------------------------------


def test_the_request_models_declare_every_field_and_refuse_others():
    assert set(AuditStartRequest.model_fields) == {
        "address", "pause", "extended", "snmp_communities", "from_device",
    }
    assert set(AuditTesterRequest.model_fields) == {
        "name", "company", "email", "notes", "leave_out_serial",
    }
    with pytest.raises(ValidationError):
        AuditStartRequest(address="10.0.0.5", port_range="extended")
    with pytest.raises(ValidationError):
        AuditTesterRequest(phone="555")
    with pytest.raises(ValidationError):
        AuditStartRequest(address="")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def test_conflicts_lists_running_project_devices_at_the_address(wired):
    wired.engine.state.set("device.lobby.connected", True)
    result = await routes.audit_conflicts(address="127.0.0.1")
    assert result["ip"] == "127.0.0.1" and result["resolved"] is True
    assert [(d["device_id"], d["device_name"], d["connected"]) for d in result["devices"]] == [
        ("lobby", "Lobby Display", True),
    ]
    assert result["devices"][0]["port"] == 7000


async def test_start_pauses_then_finish_resumes(wired):
    started = await routes.start_session(AuditStartRequest(address="127.0.0.1", pause=["lobby"]))
    session_id = started["session"]["session_id"]
    assert started["session"]["paused"] == [
        {"device_id": "lobby", "name": "Lobby Display", "owned": True},
    ]
    assert wired.devices.paused == {"lobby"}
    current = await routes.current_session()
    assert current["session"]["session_id"] == session_id
    assert current["session"]["check"]["status"] == "idle"

    ended = await routes.end_session(session_id)
    assert ended["session"]["status"] == "finished"
    assert wired.devices.paused == set()
    assert (await routes.current_session())["session"] is None


async def test_start_refusals_say_why(wired):
    with pytest.raises(HTTPException) as exc:
        await routes.start_session(AuditStartRequest(address="no-such-device.invalid"))
    assert exc.value.status_code == 400 and "could not find" in exc.value.detail

    with pytest.raises(HTTPException) as exc:
        await routes.start_session(AuditStartRequest(address="127.0.0.1", pause=["ghost"]))
    assert exc.value.status_code == 404

    # In the project but not running: the audit does not start.
    with pytest.raises(HTTPException) as exc:
        await routes.start_session(AuditStartRequest(address="127.0.0.1", pause=["spare"]))
    assert exc.value.status_code == 409 and "could not be paused" in exc.value.detail
    assert wired.manager.current() is None

    await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    with pytest.raises(HTTPException) as exc:
        await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    assert exc.value.status_code == 409
    assert exc.value.detail == "An audit is already running. Finish it first."
    await wired.manager.shutdown()


async def test_the_check_runs_once_and_its_result_reaches_the_state(wired):
    import asyncio

    started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    session_id = started["session"]["session_id"]
    got: list[dict] = []
    wired.manager.current().subscribe(got.append)
    await routes.run_network_check(session_id)
    with pytest.raises(HTTPException) as exc:
        await routes.run_network_check(session_id)
    assert exc.value.status_code == 409
    for _ in range(50):
        if wired.manager.current().footprint is not None:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)
    state = (await routes.current_session())["session"]
    assert state["check"]["status"] == "done"
    assert state["check"]["result"]["verdict"]["state"] == "unknown"
    assert "network_check" in state["steps"]
    assert got[-1]["type"] == "audit.state"
    assert got[-1]["state"]["check"]["status"] == "done"
    await wired.manager.shutdown()


async def test_tester_report_and_recent_reports(wired):
    import asyncio

    started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    session_id = started["session"]["session_id"]
    with pytest.raises(HTTPException) as exc:
        await routes.session_report(session_id)
    assert exc.value.status_code == 409

    session = wired.manager.current()
    session.check = FakeCheck(session)
    await routes.run_network_check(session_id)
    await asyncio.sleep(0.05)

    tester = await routes.set_tester(
        session_id, AuditTesterRequest(name=" Pat ", company="", notes="Bench unit"),
    )
    assert tester["session"]["tester"] == {
        "name": "Pat", "notes": "Bench unit", "leave_out_serial": False,
    }

    record = await routes.session_report(session_id, format="json")
    assert record["report_version"] == 1
    assert record["session"]["tester"] == {"name": "Pat", "notes": "Bench unit"}

    response = await routes.session_report(session_id)
    assert response.media_type == "application/zip"
    name = response.filename
    assert name.startswith("openavc-device-audit-")
    with zipfile.ZipFile(io.BytesIO(wired.store.path(name).read_bytes())) as zf:
        assert set(zf.namelist()) == {"summary.html", "report.json", "timeline.txt"}

    listed = await routes.list_reports()
    assert [r["name"] for r in listed["reports"]] == [name]
    assert (await routes.get_report(name)).filename == name
    with pytest.raises(HTTPException):
        await routes.get_report("../../system.json")

    # Finishing saves the whole story over the same file.
    await routes.end_session(session_id)
    assert [r["name"] for r in (await routes.list_reports())["reports"]] == [name]
    with zipfile.ZipFile(io.BytesIO(wired.store.path(name).read_bytes())) as zf:
        report = json.loads(zf.read("report.json"))
    assert report["session"]["status"] == "finished"

    assert await routes.delete_report(name) == {"deleted": name}
    with pytest.raises(HTTPException) as exc:
        await routes.delete_report(name)
    assert exc.value.status_code == 404


async def test_a_session_that_ended_is_not_found(wired):
    started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    session_id = started["session"]["session_id"]
    await routes.end_session(session_id, cancel=True)
    for call in (routes.run_network_check, routes.end_session):
        with pytest.raises(HTTPException) as exc:
            await call(session_id)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# The WebSocket subscription
# ---------------------------------------------------------------------------


class _Hub:
    def __init__(self):
        self.sent: list[tuple[object, dict]] = []

    def send(self, ws, message):
        self.sent.append((ws, message))


async def test_a_programmer_subscribes_and_only_it_hears(wired):
    from openavc.api import ws as wsmod
    from tests.test_websocket_protocol import FakeWS, _make_engine

    engine = _make_engine()
    engine.ws = _Hub()
    started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    session_id = started["session"]["session_id"]
    first, second = FakeWS(), FakeWS()
    with patch("openavc.api._engine._engine", engine):
        await wsmod._handle_message(first, {"type": "audit.subscribe", "session_id": session_id},
                                    "programmer")
        assert first.sent[0]["type"] == "audit.state"
        assert first.sent[0]["state"]["session_id"] == session_id

        wired.manager.current().add_timeline("check.ports", "Open: 23.")
        assert [(ws, m["type"]) for ws, m in engine.ws.sent] == [(first, "audit.timeline")]

        await wsmod._handle_message(first, {"type": "audit.unsubscribe"}, "programmer")
        wired.manager.current().add_timeline("check.ports", "Not delivered.")
        assert len(engine.ws.sent) == 1

        # A stale session id is an error, not a silent subscription.
        await wsmod._handle_message(second, {"type": "audit.subscribe", "session_id": "gone"},
                                    "programmer")
        assert second.sent[0]["type"] == "error"
        assert "ended" in second.sent[0]["message"]

        # A panel cannot listen in.
        panel = FakeWS()
        await wsmod._handle_message(panel, {"type": "audit.subscribe", "session_id": session_id},
                                    "panel")
        assert panel.sent[0]["type"] == "error"
    wsmod._cleanup_audit_subscription(id(first))
    await wired.manager.shutdown()


async def test_a_disconnect_drops_the_subscription(wired):
    from openavc.api import ws as wsmod
    from tests.test_websocket_protocol import FakeWS, _make_engine

    engine = _make_engine()
    engine.ws = _Hub()
    started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    client = FakeWS()
    with patch("openavc.api._engine._engine", engine):
        await wsmod._handle_message(
            client, {"type": "audit.subscribe", "session_id": started["session"]["session_id"]},
            "programmer",
        )
    assert id(client) in wsmod._audit_subscriptions
    wsmod._cleanup_audit_subscription(id(client))
    wired.manager.current().add_timeline("check.ports", "Nobody to hear it.")
    assert engine.ws.sent == []
    await wired.manager.shutdown()
