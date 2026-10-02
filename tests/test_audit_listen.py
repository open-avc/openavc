"""Connect and listen: the driver brought up as production does, watched closely.

Against a loopback fake device, with the listening window shortened: the
timeline (first bytes, first reply, connected, first reports), the window,
the status table with the rules behind an empty value, contract faults, the
live messages the wizard gets, a connection that fails with its reason, the
front-panel check, and teardown.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from fastapi import HTTPException

from openavc.api.models import (
    AuditConnectionRequest,
    AuditDriverRequest,
    AuditFrontPanelRequest,
    AuditStartRequest,
)
from openavc.api.routes import audit as routes
from openavc.audit.driver_choice import DriverChoice
from openavc.audit.listen import (
    DONE,
    FAILED,
    LISTENING,
    NOT_CONNECTED,
    next_step,
    start_listen,
)
from openavc.audit.passes import DriverRun
from openavc.audit.session import AuditError, AuditOptions, AuditSession, AuditTarget
from openavc.core.device_traffic import get_traffic_recorder
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import get_secret_registry
from tests.test_audit_api import wired  # noqa: F401  (the fixture)

DRIVER = {
    "id": "acme_listen",
    "name": "Acme Listen",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 0.1},
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "volume": {"type": "integer", "label": "Volume"},
    },
    "commands": {},
    "polling": {"queries": ["PWR?\\r"]},
    "responses": [
        {"match": r"PWR=(\w+)", "set": {"power": "$1"}},
        {"match": r"VOL=(\d+)", "set": {"volume": "$1"}},
    ],
}

FAST = {"min_seconds": 0.6, "min_cycles": 3, "max_seconds": 5.0, "flush_seconds": 0.05}


@pytest.fixture
def driver():
    _DRIVER_REGISTRY["acme_listen"] = create_configurable_driver_class(DRIVER)
    yield
    _DRIVER_REGISTRY.pop("acme_listen", None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


async def _fake_device():
    """Answers each query with its power, and one line nothing understands."""

    async def handle(reader, writer):
        try:
            while True:
                await reader.readuntil(b"\r")
                writer.write(b"PWR=on\rLAMP=450\r")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _session_and_run(port: int) -> tuple[AuditSession, DriverRun, list[dict]]:
    session = AuditSession("lst1", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    heard: list[dict] = []
    session.subscribe(heard.append)
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id="acme_listen", identity={"name": "Acme Listen", "version": "1.0.0"},
    ))
    run.config = {"host": "127.0.0.1", "port": port}
    session.runs.append(run)
    return session, run, heard


async def _until(predicate, timeout: float = 8.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def test_connect_and_listen_records_the_whole_story(driver):
    server, port = await _fake_device()
    session, run, heard = _session_and_run(port)
    try:
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.status == DONE)
        kinds = [e.kind for e in session.timeline]
        for kind in ("listen.connecting", "listen.first_tx", "listen.first_rx",
                     "listen.connected", "listen.reported", "listen.done"):
            assert kind in kinds, kind
        # No sign-in and no start-up steps: nothing is sent until the first poll.
        assert kinds.index("listen.connected") < kinds.index("listen.first_tx")
        assert kinds.index("listen.first_tx") < kinds.index("listen.first_rx")
        assert "contract.unmatched_response" in kinds

        state = listen.to_dict()
        assert state["declared"] == 2 and state["reported"] == 1
        table = {v["name"]: v for v in state["status_table"]["variables"]}
        assert table["power"]["reported"] and table["power"]["value"] is True
        # An empty value comes with the rule that would have set it.
        assert not table["volume"]["reported"]
        assert table["volume"]["sources"] == [r"a reply matching /VOL=(\d+)/"]
        assert state["contract"]["counts"]["unmatched_response"] >= 1
        assert state["traffic"]["sent"] >= 3  # polled at the driver's cadence
        assert state["traffic"]["not_captured"] is False

        # The wizard heard it live, in batches, and nobody else was told.
        traffic = [m for m in heard if m["type"] == "audit.traffic"]
        assert traffic and traffic[0]["entries"][0]["direction"] == "tx"
        assert any(m["type"] == "audit.listen" for m in heard)
        # The driver stays connected after the window, for the next step.
        assert run.active
    finally:
        await run.stop()
        server.close()
    assert not run.active and run.finished_at is not None
    assert run.sandbox.device_state() == {}
    assert run.to_dict()["listen"]["status"] == DONE


async def test_the_wizard_hears_that_the_run_has_started(driver):
    """The Connect reply leaves before the run starts, and the listen's own
    updates do not carry the run, so its start goes out as the whole state."""
    from openavc.audit.passes import open_runs

    server, port = await _fake_device()
    session, run, heard = _session_and_run(port)
    open_runs(session)
    try:
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.connected_at is not None)
        states = [m["state"] for m in heard if m["type"] == "audit.state"]
        assert states and states[-1]["runs"][0]["started_at"] == run.started_at
        assert run.started_at is not None
    finally:
        await run.stop()
        server.close()


async def test_a_device_that_refuses_is_reported_with_its_reason(driver):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    session, run, _ = _session_and_run(port)
    try:
        listen = await start_listen(session, run, **FAST)
        # A refusal takes a couple of seconds to arrive on some systems, longer
        # than this shortened window, so the attempt ends as failed.
        await _until(lambda: listen.status == FAILED)
        assert listen.offline["code"] == "connection_refused"
        assert listen.offline["next_step"].startswith("OpenAVC keeps trying")
        assert "listen.failed" in [e.kind for e in session.timeline]
        assert NOT_CONNECTED != FAILED
    finally:
        await run.stop()


def test_a_setting_problem_is_fixed_on_the_connection_step():
    assert next_step("auth_failed").startswith("Change the setting on the Connection step")
    assert next_step("unreachable").startswith("OpenAVC keeps trying")


async def test_keep_listening_and_the_front_panel_check(driver):
    server, port = await _fake_device()
    session, run, _ = _session_and_run(port)
    try:
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.status == LISTENING)
        before = listen.ends_at
        listen.extend(1.0)
        assert listen.ends_at > before
        listen.extend(100.0)
        assert listen.ends_at == pytest.approx(listen.started_at + FAST["max_seconds"])
        await _until(lambda: listen.status == DONE)
        with pytest.raises(AuditError, match="at most"):
            listen.extend()

        listen.answer_front_panel("showed", "turned the volume")
        panel = listen.to_dict()["front_panel"]
        assert panel["answer"] == "showed" and panel["note"] == "turned the volume"
        assert any(c["key"] == "power" for c in panel["changes"])
    finally:
        await run.stop()
        server.close()


async def test_nothing_to_listen_with_until_the_connection_is_set(driver):
    session = AuditSession("lst2", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(driver_id="acme_listen"))
    with pytest.raises(AuditError, match="connection settings"):
        await start_listen(session, run)


async def test_a_driver_that_owns_its_connection_is_named_as_such(driver):
    from openavc.drivers.base import BaseDriver

    class AcmeOwnSocket(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_own", "name": "Acme Own", "transport": "tcp",
            "state_variables": {"power": {"type": "boolean"}},
        }

        async def _create_transport(self, transport_type):
            self._alive = True  # its own session, no platform transport

        def _link_alive(self):
            return True

        async def _initial_sync(self):
            self.set_state("power", True)

        async def send_command(self, command, params=None):
            return None

    _DRIVER_REGISTRY["acme_own"] = AcmeOwnSocket
    session = AuditSession("lst3", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(driver_id="acme_own"))
    run.config = {"host": "127.0.0.1", "port": 9}
    try:
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.status == DONE)
        assert listen.to_dict()["traffic"]["not_captured"] is True
    finally:
        await run.stop()
        _DRIVER_REGISTRY.pop("acme_own", None)


async def test_the_routes(wired, driver):  # noqa: F811
    server, port = await _fake_device()
    try:
        started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
        session_id = started["session"]["session_id"]
        await routes.run_network_check(session_id)
        await _until(lambda: wired.manager.current().footprint is not None)
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_listen"))
        with pytest.raises(HTTPException) as exc:
            await routes.front_panel_check(session_id, AuditFrontPanelRequest(answer="showed"))
        assert exc.value.status_code == 409

        # A project device at this address the audit did not pause is refused.
        with pytest.raises(HTTPException) as exc:
            await routes.connect_and_listen(session_id)
        assert exc.value.status_code == 409 and "Lobby Display" in exc.value.detail
        wired.engine.state.set("device.lobby.paused", True)

        await routes.set_session_connection(session_id, AuditConnectionRequest(
            config={"host": "127.0.0.1", "port": port},
        ))
        result = await routes.connect_and_listen(session_id)
        assert "listen" in result["session"]["steps"]
        assert result["session"]["runs"][0]["listen"]["status"] in ("connecting", "listening")
        # Connecting again ends the first attempt before the second starts:
        # one driver on the device at a time.
        await routes.connect_and_listen(session_id)
        session = wired.manager.current()
        first, second = session.runs[0].listens
        assert first.status == "stopped" and not first.sandbox.started
        await _until(lambda: second.status == LISTENING)
        await routes.keep_listening(session_id)
        await routes.front_panel_check(session_id, AuditFrontPanelRequest(answer="did_not"))
        assert session.runs[0].listen.front_panel["answer"] == "did_not"
        await routes.end_session(session_id, cancel=True)
        assert not session.runs[0].active
    finally:
        server.close()
        await wired.manager.shutdown()


async def test_a_value_the_driver_writes_before_the_device_answers_is_not_reported(driver):
    """A value from the driver's own config, written before the device said
    anything, is the driver's: "set by the driver", never "reported", and
    the timeline, the listen step and the summary count it the same way."""
    from openavc.audit.report import build_report, suggested_confidence

    info = {**DRIVER, "id": "acme_zones", "state_variables": {
        **DRIVER["state_variables"], "zones": {"type": "integer", "label": "Zones"},
    }}
    base = create_configurable_driver_class(info)

    class AcmeZones(base):
        async def connect(self):
            self.set_state("zones", 2)  # from its config, before anything is sent
            await super().connect()

    _DRIVER_REGISTRY["acme_zones"] = AcmeZones
    server, port = await _fake_device()
    session, run, _ = _session_and_run(port)
    run.choice = DriverChoice(driver_id="acme_zones", identity={"name": "Acme", "version": "1"})
    try:
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.status == DONE)
        state = listen.to_dict()
        assert state["declared"] == 3 and state["reported"] == 1
        assert state.get("set_by_driver") == 1
        table = {v["name"]: v for v in state["status_table"]["variables"]}
        assert table["zones"]["value"] == 2
        assert not table["zones"]["reported"] and table["zones"]["set_by_driver"]
        assert table["power"]["reported"] and not table["power"]["set_by_driver"]
        texts = [e.text for e in session.timeline]
        assert not any(t.startswith("Zones reported") for t in texts)
        assert any(t.endswith("1 of 3 status values reported, 1 more set by the driver itself.")
                   for t in texts)
    finally:
        await run.stop()
        server.close()
        _DRIVER_REGISTRY.pop("acme_zones", None)
    section = build_report(session)["drivers"][0]
    reasons = [r["text"] for r in suggested_confidence(section)["reasons"]]
    assert "1 of 3 status values were reported. Never reported: Volume, Zones." in reasons


def test_the_counts_say_what_the_listen_step_says():
    from openavc.audit.report import suggested_confidence

    def section(*variables):
        attempt = {"connected_at": 1.0, "traffic": {"received": 5, "sent": 5},
                   "status_table": {"variables": list(variables)}}
        return {"attempts": [attempt]}

    power = {"name": "power", "label": "Power", "reported": True}
    error = {"name": "last_error", "label": "Last error", "reported": False}
    reasons = [r["text"] for r in suggested_confidence(section(power, error))["reasons"]]
    assert ("Every status value was reported (1 of 2, Last error aside: the driver writes it "
            "only when something goes wrong).") in reasons
    reasons = [r["text"] for r in suggested_confidence(
        section(power, {**error, "reported": True}))["reasons"]]
    assert "Every status value was reported (2 of 2)." in reasons


async def test_a_drop_after_listening_is_not_the_listen_attempts(driver):
    writers: list = []

    async def handle(reader, writer):
        writers.append(writer)
        try:
            while True:
                await reader.readuntil(b"\r")
                writer.write(b"PWR=on\r")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    session, run, _ = _session_and_run(server.sockets[0].getsockname()[1])
    try:
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.status == DONE)
        for w in writers:
            w.close()  # the link drops after the window (a command, a test)
        await _until(lambda: listen.drops + getattr(listen, "later_drops", 0) == 1)
        state = listen.to_dict()
        assert state["drops"] == 0
        assert state.get("later_drops") == 1
    finally:
        await run.stop()
        server.close()


async def test_the_wizard_hears_connected_while_the_drivers_start_up_is_still_running(driver):
    """A driver that connects and then runs long start-up steps is connected
    and listening from the moment it connects: the wizard hears that then,
    not when the start-up steps finish (it read "Connecting." and 0 values
    beside a timeline already saying "Connected")."""
    base = create_configurable_driver_class(DRIVER)
    done = asyncio.Event()

    class AcmeSlowStart(base):
        async def connect(self):
            await super().connect()
            await asyncio.sleep(2.0)  # start-up steps still going
            done.set()

    _DRIVER_REGISTRY["acme_listen"] = AcmeSlowStart
    server, port = await _fake_device()
    session, run, heard = _session_and_run(port)
    try:
        await start_listen(session, run, **FAST)

        def listening_heard() -> bool:
            return any(
                m["type"] == "audit.listen" and m["listen"]["status"] == LISTENING for m in heard
            )

        await _until(listening_heard, timeout=1.8)
        assert not done.is_set()
    finally:
        await run.stop()
        server.close()
