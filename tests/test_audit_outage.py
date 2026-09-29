"""Power cycle and cable pull: the device goes away and comes back.

Against a loopback unit that can be switched off (its connections close and
it refuses new ones) and on again, with a stand-in for the ping the audit
sends a real device (loopback always answers). The test is measured through
the sandbox manager's own detection and reconnect loop: when OpenAVC noticed
and why, when the device answered again, when the driver reconnected, and
which status values were reported again (a same-value write counts). The
listeners' per-message hooks and the check's log of what the audited device
announced are tested here too.
"""

from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openavc.api.models import (
    AuditConnectionRequest,
    AuditDriverRequest,
    AuditOutageMarkRequest,
    AuditStartRequest,
)
from openavc.api.routes import audit as routes
from openavc.audit.commands import commands_for
from openavc.audit.driver_choice import DriverChoice
from openavc.audit.listen import start_listen
from openavc.audit.outage import (
    DONE,
    POWER_CYCLE,
    STOPPED,
    outage_sentence,
    start_outage,
)
from openavc.audit.passes import DriverRun
from openavc.audit.report import build_report, render_summary
from openavc.audit.session import AuditError, AuditOptions, AuditSession, AuditTarget
from openavc.core.device_traffic import get_traffic_recorder
from openavc.discovery.amx_ddp_scanner import AMXDDPScanner
from openavc.discovery.mdns_scanner import DNS_TYPE_PTR, MDNSScanner, encode_dns_name
from openavc.discovery.ssdp_scanner import SSDPScanner
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import get_secret_registry
from tests.test_audit_api import wired  # noqa: F401  (the fixture)

DRIVER = {
    "id": "acme_outage",
    "name": "Acme Outage",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 0.2},
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "volume": {"type": "integer", "label": "Volume"},
        "input": {"type": "string", "label": "Input"},
    },
    "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
    "polling": {"queries": ["PWR?\\r", "VOL?\\r"]},
    "responses": [
        {"match": r"PWR=(\w+)", "set": {"power": "$1"}},
        {"match": r"VOL=(\d+)", "set": {"volume": "$1"}},
    ],
}

FAST = {"min_seconds": 0.3, "min_cycles": 1, "max_seconds": 30.0, "flush_seconds": 0.05}
QUICK = {"ping_seconds": 0.05, "flush_seconds": 0.05, "repopulate_seconds": 0.6}


@pytest.fixture
def driver():
    _DRIVER_REGISTRY["acme_outage"] = create_configurable_driver_class(DRIVER)
    yield
    _DRIVER_REGISTRY.pop("acme_outage", None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


class Unit:
    """A device on loopback that can be switched off and on at its port."""

    def __init__(self) -> None:
        self.on = True
        self.port = 0
        self._server: asyncio.base_events.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer) -> None:
        self._writers.append(writer)
        try:
            while True:
                line = (await reader.readuntil(b"\r")).decode().strip()
                if line == "PWR?":
                    writer.write(b"PWR=on\r")
                elif line == "VOL?":
                    writer.write(b"VOL=30\r")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def power_off(self) -> None:
        self.on = False
        # Connections first: a server's wait_closed waits for them all.
        for writer in self._writers:
            writer.close()
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await asyncio.wait_for(self._server.wait_closed(), timeout=5.0)
            self._server = None

    async def power_on(self) -> None:
        self.on = True
        await self.start()

    async def close(self) -> None:
        await self.power_off()


async def _until(predicate, timeout: float = 15.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def _connected(unit: Unit, heard: list | None = None):
    session = AuditSession("out1", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    session.check = SimpleNamespace(
        source_ip="",
        heard_since=lambda since, until=None: [
            m for m in (heard or []) if m["t"] >= since and (until is None or m["t"] <= until)
        ],
    )
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id="acme_outage", identity={"name": "Acme Outage", "version": "1.0.0"},
    ))
    run.config = {"host": "127.0.0.1", "port": unit.port}
    session.runs.append(run)
    listen = await start_listen(session, run, **FAST)
    await _until(lambda: listen.sandbox.device_state().get("volume") == 30)
    return session, run


# ---------------------------------------------------------------------------
# The listeners say when the device spoke
# ---------------------------------------------------------------------------


def test_each_listener_passes_on_what_one_message_said():
    heard: list = []
    ssdp = SSDPScanner(on_message=lambda ip, detail: heard.append(("ssdp", ip, detail)))
    ssdp._process_response(
        b"NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nNT: upnp:rootdevice\r\n"
        b"NTS: ssdp:byebye\r\nUSN: uuid:acme-1::upnp:rootdevice\r\n\r\n",
        "10.0.0.5",
    )
    amx = AMXDDPScanner(on_message=lambda ip, detail: heard.append(("amx_ddp", ip, detail)))
    amx._handle_datagram(b"AMXB<-UUID=AW3-0042><-Make=Acme><-Model=Widget3000>", "10.0.0.5")
    mdns = MDNSScanner(on_message=lambda ip, detail: heard.append(("mdns", ip, detail)))
    target = encode_dns_name("Widget._acme._tcp.local.")
    packet = (
        struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        + encode_dns_name("_acme._tcp.local.")
        + struct.pack("!HHIH", DNS_TYPE_PTR, 1, 120, len(target)) + target
    )
    mdns._process_response(packet, "10.0.0.5")

    (s_kind, s_ip, s_detail), (a_kind, _, a_detail), (m_kind, _, m_detail) = heard
    # A byebye is passed on, though the scanner drops the device's record for it.
    assert (s_kind, s_ip) == ("ssdp", "10.0.0.5")
    assert s_detail["kind"] == "notify" and s_detail["nts"] == "ssdp:byebye"
    assert "10.0.0.5" not in ssdp.results
    assert a_kind == "amx_ddp" and a_detail["make"] == "Acme" and a_detail["model"] == "Widget3000"
    assert m_kind == "mdns" and m_detail["records"][0]["type"] == "PTR"


def test_a_failing_callback_never_stops_a_listener():
    def boom(_ip, _detail):
        raise RuntimeError("the caller's problem")

    amx = AMXDDPScanner(on_message=boom)
    amx._handle_datagram(b"AMXB<-Make=Acme><-Model=W>", "10.0.0.5")
    assert amx.results["10.0.0.5"].model == "W"


def test_the_check_keeps_what_the_audited_device_said_and_when():
    from openavc.audit.footprint import NetworkCheck
    from openavc.discovery.engine import DiscoveryEngine

    check = NetworkCheck(DiscoveryEngine(), "10.0.0.5", "10.0.0.5", source_ip="")
    check._hear("ssdp")("10.0.0.5", {"kind": "notify"})
    check._hear("ssdp")("10.0.0.9", {"kind": "notify"})  # another device: not kept
    assert [m["protocol"] for m in check.heard] == ["ssdp"]
    t = check.heard[0]["t"]
    assert check.heard_since(t - 1) == check.heard
    assert check.heard_since(t + 1) == []
    assert check.source_ip == ""


# ---------------------------------------------------------------------------
# The power cycle
# ---------------------------------------------------------------------------


async def test_a_power_cycle_is_measured_through_production(driver):
    unit = Unit()
    await unit.start()
    heard: list = []
    session, run = await _connected(unit, heard)

    async def pinger() -> bool:
        return unit.on

    try:
        test = start_outage(session, run, POWER_CYCLE, pinger=pinger, **QUICK)
        assert test.before == {"power": True, "volume": 30}
        assert not test.watch["liveness_probe"] and test.watch["poll_interval"] == 0.2
        test.mark("off")
        await unit.power_off()
        await _until(lambda: test.noticed_at is not None and test.unreachable_at is not None)
        import time as _time

        heard.append({"t": _time.time(), "protocol": "ssdp", "detail": {"nts": "ssdp:alive"}})
        await asyncio.sleep(0.3)
        test.mark("on")
        await unit.power_on()
        await _until(lambda: test.status == DONE, timeout=20.0)

        record = test.to_dict()
        assert record["reconnected_at"] and record["reachable_at"]
        assert record["reason"]["code"]  # the manager's own offline reason
        measured = record["measured"]
        assert measured["noticed_after"] is not None
        assert measured["answered_after_on"] is not None and measured["answered_after_on"] >= 0
        assert measured["reconnected_after_back"] is not None
        # The same values again after the reconnect still count as reported.
        assert record["repopulated"] == {
            "reported_again": ["power", "volume"], "not_reported_again": [],
        }
        assert [m["protocol"] for m in record["announcements"]] == ["ssdp"]
        sentence = outage_sentence(record)
        assert sentence.startswith("OpenAVC noticed the device was gone")
        assert "the driver reconnected" in sentence
        assert "2 of 2 status values were reported again" in sentence
        assert "the device announced itself 1 time meanwhile" in sentence
        kinds = [e.kind for e in session.timeline]
        for kind in ("outage.started", "outage.off", "outage.unreachable", "outage.noticed",
                     "outage.on", "outage.reachable", "outage.reconnected", "outage.done"):
            assert kind in kinds, kind
        assert "outage" in session.steps
    finally:
        await run.stop()
        await unit.close()
    report = build_report(session)
    (kept,) = report["drivers"][0]["outages"]
    assert kept["kind"] == "power_cycle" and kept["status"] == DONE
    assert kept["summary"].startswith("OpenAVC noticed the device was gone")
    assert "Power and cable" in render_summary(report)


async def test_what_a_test_needs_before_it_starts(driver):
    unit = Unit()
    await unit.start()
    session, run = await _connected(unit)

    async def pinger() -> bool:
        return True

    try:
        commands = commands_for(session, run, window_seconds=30.0, flush_seconds=0.05)
        trial = await commands.send("ping")
        with pytest.raises(AuditError, match="Wait for Ping to finish first"):
            start_outage(session, run, POWER_CYCLE, pinger=pinger)
        await _until(lambda: trial.status == "watching")
        commands.end_now()
        await _until(lambda: trial.status == "done")

        test = start_outage(session, run, POWER_CYCLE, pinger=pinger, **QUICK)
        with pytest.raises(AuditError, match="Finish the test that is running first"):
            start_outage(session, run, POWER_CYCLE, pinger=pinger)
        # "Back on" before anything went off says what to do instead.
        with pytest.raises(AuditError, match="went off before"):
            test.mark("on")
    finally:
        await run.stop()
        await unit.close()
    # Stopping the driver ends a test that is running, and keeps what it saw.
    assert test.status == STOPPED and test.end_reason == "The driver was stopped."


async def test_nothing_to_drop_without_a_connected_driver(driver):
    session = AuditSession("out2", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(driver_id="acme_outage"))
    with pytest.raises(AuditError, match="nothing to see drop"):
        start_outage(session, run, POWER_CYCLE)


async def test_a_device_that_does_not_answer_ping_is_timed_by_the_marks(driver):
    unit = Unit()
    await unit.start()
    session, run = await _connected(unit)
    from openavc.audit.footprint import Footprint

    session.footprint = Footprint(address="127.0.0.1", ip="127.0.0.1")
    session.footprint.ping = {"result": "timeout", "method": "exec", "attempts": []}
    try:
        test = start_outage(session, run, POWER_CYCLE, **QUICK)
        assert not test.ping["used"] and "did not answer ping" in test.ping["why"]
        await test.stop()
    finally:
        await run.stop()
        await unit.close()
    limits = [x["id"] for x in build_report(session)["limits"]]
    assert "outage_by_marks" in limits


def test_the_mark_request_declares_every_field():
    assert set(AuditOutageMarkRequest.model_fields) == {"mark"}
    with pytest.raises(ValidationError):
        AuditOutageMarkRequest(mark="sideways")


async def test_the_routes(wired, driver):  # noqa: F811
    unit = Unit()
    await unit.start()
    try:
        started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
        session_id = started["session"]["session_id"]
        await routes.run_network_check(session_id)
        await _until(lambda: wired.manager.current().footprint is not None)
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_outage"))
        wired.engine.state.set("device.lobby.paused", True)
        await routes.set_session_connection(session_id, AuditConnectionRequest(
            config={"host": "127.0.0.1", "port": unit.port, "poll_interval": 0.2},
        ))
        await routes.connect_and_listen(session_id)
        session = wired.manager.current()
        run = session.runs[0]
        await _until(lambda: run.listen.sandbox.connected())
        with pytest.raises(HTTPException) as exc:
            await routes.mark_outage(session_id, AuditOutageMarkRequest(mark="off"))
        assert exc.value.status_code == 409

        result = await routes.start_power_cycle(session_id)
        assert result["session"]["runs"][0]["outages"][0]["kind"] == "power_cycle"
        await routes.mark_outage(session_id, AuditOutageMarkRequest(mark="off"))
        assert run.outages[0].off_at is not None
        await routes.stop_outage(session_id)
        assert run.outages[0].status == STOPPED
        with pytest.raises(HTTPException) as exc:
            await routes.stop_outage(session_id)
        assert exc.value.status_code == 409
        await routes.end_session(session_id, cancel=True)
    finally:
        await unit.close()
        await wired.manager.shutdown()

