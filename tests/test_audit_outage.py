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
    CABLE_PULL,
    DONE,
    POWER_CYCLE,
    STOPPED,
    outage_sentence,
    span_text,
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
    """A device on loopback that can be switched off and on at its port, or
    unplugged: its connections stay open and it simply stops answering."""

    def __init__(self) -> None:
        self.on = True
        self.unplugged = False
        # Reset the next connection after its first reply, as a device does
        # that accepts one while it is still booting.
        self.reset_once = False
        self.port = 0
        self._server: asyncio.base_events.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []
        # Connections a reboot left dead: they stay open and answer nothing,
        # as a unit that lost power sends no reset for them.
        self._dead: set[int] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer) -> None:
        self._writers.append(writer)
        resetting, self.reset_once = self.reset_once, False
        try:
            while True:
                line = (await reader.readuntil(b"\r")).decode().strip()
                if self.unplugged or id(writer) in self._dead:
                    continue  # nothing gets through, and nothing closes
                if line == "PWR?":
                    writer.write(b"PWR=on\r")
                elif line == "VOL?":
                    writer.write(b"VOL=30\r")
                await writer.drain()
                if resetting:
                    return
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

    def reboot_silently(self) -> None:
        """Every open connection goes dead without closing; new ones work."""
        self._dead |= {id(w) for w in self._writers}

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


async def _connected(unit: Unit, heard: list | None = None, driver_id: str = "acme_outage"):
    session = AuditSession("out1", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    session.check = SimpleNamespace(
        source_ip="",
        heard_since=lambda since, until=None: [
            m for m in (heard or []) if m["t"] >= since and (until is None or m["t"] <= until)
        ],
    )
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id=driver_id, identity={"name": "Acme Outage", "version": "1.0.0"},
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


async def test_a_stray_ping_reply_after_power_off_is_not_the_device_back(driver):
    """A unit powering down answered one more ping a second after it went
    dark, and the test took that reply as the unit back: every time after it
    was measured from it. One reply is not the device back, and a device that
    answers for a while and goes quiet again before the driver returns was
    not back either."""
    import time as _time

    unit = Unit()
    await unit.start()
    session, run = await _connected(unit, [])
    # Answering, then a miss, one stray reply and quiet; then three replies
    # and quiet again (a boot that brings the network up and down); then
    # whatever the unit says.
    script = [True, True, True, False, True, False, False, False, False,
              True, True, True, False, False, False, False]

    async def pinger() -> bool:
        return script.pop(0) if script else unit.on

    try:
        test = start_outage(session, run, POWER_CYCLE, pinger=pinger, **QUICK)
        test.mark("off")
        await unit.power_off()
        await _until(lambda: not script and test.noticed_at is not None, timeout=10.0)
        assert test.unreachable_at is not None
        # Neither the stray reply nor the short run that went quiet again.
        assert test.reachable_at is None
        kinds = [e.kind for e in session.timeline]
        assert kinds.count("outage.unreachable") == 1
        assert "outage.unreachable_again" in kinds
        on_at = _time.time()
        test.mark("on")
        await unit.power_on()
        await _until(lambda: test.status == DONE, timeout=20.0)
        assert test.reachable_at is not None and test.reachable_at >= on_at
        assert test.to_dict()["measured"]["answered_after_on"] >= 0
    finally:
        await run.stop()
        await unit.close()


async def test_a_connection_that_drops_again_is_waited_out(driver):
    """A device that accepts the reconnect as it boots and then resets it (an
    amplifier did, 2.7 s in): the test records the second drop, waits for the
    driver to come back again, and says both; before, it said the driver had
    reconnected and ended, while the device was down again."""
    unit = Unit()
    await unit.start()
    session, run = await _connected(unit)

    async def pinger() -> bool:
        return unit.on

    try:
        test = start_outage(session, run, POWER_CYCLE, pinger=pinger, **QUICK)
        test.mark("off")
        await unit.power_off()
        await _until(lambda: test.noticed_at is not None)
        unit.reset_once = True
        test.mark("on")
        await unit.power_on()
        await _until(lambda: test.status == DONE, timeout=30.0)

        record = test.to_dict()
        assert record["reconnected_at"] < record["dropped_again_at"] < record["reconnected_again_at"]
        assert record["measured"]["dropped_again_after"] is not None
        assert record["measured"]["reconnected_again_after"] is not None
        # What came back is counted from the reconnect that held.
        assert record["repopulated"]["not_reported_again"] == []
        sentence = outage_sentence(record)
        assert "the driver reconnected" in sentence
        assert "the connection dropped again" in sentence
        assert ", and the driver reconnected" in sentence
        kinds = [e.kind for e in session.timeline]
        assert "outage.dropped_again" in kinds and "outage.reconnected_again" in kinds
    finally:
        await run.stop()
        await unit.close()

    # Still down when the test ended: said so.
    down = dict(record, reconnected_again_at=None)
    down["measured"] = dict(record["measured"], reconnected_again_after=None)
    assert "and the driver had not reconnected when the test ended" in outage_sentence(down)


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
        with pytest.raises(AuditError, match="Press I turned it off first"):
            test.mark("on")
    finally:
        await run.stop()
        await unit.close()
    # Stopping the driver ends a test that is running, and keeps what it saw.
    assert test.status == STOPPED and test.end_reason == "The driver was stopped."


async def test_nothing_to_drop_without_a_connected_driver(driver):
    session = AuditSession("out2", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(driver_id="acme_outage"))
    with pytest.raises(AuditError, match="these tests cannot run"):
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


async def test_a_pulled_cable_the_driver_never_notices_is_said_plainly(driver):
    """No liveness probe: polls go out, nothing comes back, nothing fails."""
    unit = Unit()
    await unit.start()
    session, run = await _connected(unit)

    async def pinger() -> bool:
        return not unit.unplugged

    try:
        test = start_outage(
            session, run, CABLE_PULL, pinger=pinger, notice_ceiling_seconds=1.0,
            back_unnoticed_seconds=0.3, **QUICK,
        )
        assert not test.watch["liveness_probe"]
        test.mark("off")
        unit.unplugged = True
        await _until(lambda: test.not_noticed_at is not None)
        assert test.noticed_at is None and run.listen.sandbox.connected()
        unit.unplugged = False
        test.mark("on")
        await _until(lambda: test.status == DONE)
        record = test.record()
        assert record["unreachable_at"] and record["reachable_at"]
        assert record["summary"].startswith(
            "OpenAVC did not notice the device was gone within 1 second. This driver does not "
            "check on its own whether the device is still there, so it notices only when "
            "something it sends fails"
        )
        assert "outage.not_noticed" in [e.kind for e in session.timeline]
    finally:
        await run.stop()
        await unit.close()


async def test_a_driver_with_a_liveness_probe_notices_a_pulled_cable(driver):
    watched = dict(DRIVER, id="acme_watched", liveness={
        "send": "PWR?\\r", "interval": 1, "timeout": 0.3, "max_failures": 1,
    })
    _DRIVER_REGISTRY["acme_watched"] = create_configurable_driver_class(watched)
    unit = Unit()
    await unit.start()
    session, run = await _connected(unit, driver_id="acme_watched")

    async def pinger() -> bool:
        return not unit.unplugged

    try:
        test = start_outage(session, run, CABLE_PULL, pinger=pinger, **QUICK)
        assert test.watch["liveness_probe"] and test.watch["probe_every"] == 1.0
        test.mark("off")
        unit.unplugged = True
        await _until(lambda: test.noticed_at is not None, timeout=20.0)
        assert test.reason["code"] == "no_response"
        unit.unplugged = False
        test.mark("on")
        await _until(lambda: test.status == DONE, timeout=30.0)
        record = test.record()
        assert record["reconnected_at"] is not None
        assert "OpenAVC noticed the device was gone" in record["summary"]
        # Plugged back in before the next ping, so the ping clock follows the mark.
        assert "answered again" in record["summary"]
        assert "after the cable was plugged back in" in record["summary"]
    finally:
        await run.stop()
        await unit.close()
        _DRIVER_REGISTRY.pop("acme_watched", None)


async def test_a_probe_that_notices_after_the_device_is_back_is_waited_for(driver):
    """The unit was back before the liveness probe gave up on the connection
    the reboot left dead; the probe notices after it, and the test waits for
    the driver to reconnect rather than ending as "back, never noticed"."""
    watched = dict(DRIVER, id="acme_watched", liveness={
        "send": "PWR?\r", "interval": 1, "timeout": 0.3, "max_failures": 2,
    })
    _DRIVER_REGISTRY["acme_watched"] = create_configurable_driver_class(watched)
    unit = Unit()
    await unit.start()
    session, run = await _connected(unit, driver_id="acme_watched")
    answering = {"yes": True}

    async def pinger() -> bool:
        return answering["yes"]

    try:
        test = start_outage(
            session, run, POWER_CYCLE, pinger=pinger, back_unnoticed_seconds=0.3, **QUICK,
        )
        assert test.watch["notice_within"] == pytest.approx(2.6)
        test.mark("off")
        unit.reboot_silently()
        answering["yes"] = False
        await _until(lambda: test.unreachable_at is not None)
        answering["yes"] = True
        test.mark("on")
        await _until(lambda: test.reachable_at is not None)
        assert test.noticed_at is None  # back before the probe gave up
        await _until(lambda: test.status == DONE, timeout=30.0)
        record = test.record()
        assert record["noticed_at"] > record["reachable_at"]
        assert record["reconnected_at"] is not None
        assert "the driver reconnected" in record["summary"]
    finally:
        await run.stop()
        await unit.close()
        _DRIVER_REGISTRY.pop("acme_watched", None)


def test_how_the_test_ended_is_said_once():
    base = {"kind": POWER_CYCLE, "noticed_at": 5.0, "reason": {"code": "no_response"},
            "measured": {"noticed_after": 3.0}}
    ceiling = outage_sentence({**base, "status": STOPPED, "end_code": "ceiling",
                               "end_reason": "The test stopped after 15 minutes."})
    assert ceiling.endswith(
        "; the driver had not reconnected when the test stopped after 15 minutes."
    )
    assert "; The" not in ceiling
    stopped = outage_sentence({**base, "status": STOPPED, "end_code": "person",
                               "end_reason": "The test was stopped."})
    assert stopped.endswith("; the driver had not reconnected when the test was stopped.")


def test_the_ceiling_reads_right_at_any_length():
    assert span_text(300.0) == "5 minutes"
    assert span_text(60.0) == "1 minute"
    assert span_text(1.0) == "1 second"
    assert span_text(40.0) == "40 seconds"


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
        with pytest.raises(HTTPException) as exc:
            await routes.start_cable_pull(session_id)
        assert exc.value.status_code == 409
        await routes.stop_outage(session_id)
        assert run.outages[0].status == STOPPED
        result = await routes.start_cable_pull(session_id)
        assert result["session"]["runs"][0]["outages"][1]["kind"] == "cable_pull"
        await routes.stop_outage(session_id)
        with pytest.raises(HTTPException) as exc:
            await routes.stop_outage(session_id)
        assert exc.value.status_code == 409
        await routes.end_session(session_id, cancel=True)
    finally:
        await unit.close()
        await wired.manager.shutdown()



async def test_both_tests_against_the_simulator(driver):
    """The platform's own simulator: its no_response error mode is a pulled
    cable (the connection stays, nothing answers), and stopping and starting
    it is a power cycle (the connection closes, the port refuses)."""
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    definition = dict(
        DRIVER, id="acme_simulated",
        polling={"queries": [
            {"send": "PWR?\r", "query_for": "power"},
            {"send": "VOL?\r", "query_for": "volume"},
        ]},
        liveness={"send": "PWR?\r", "interval": 1, "timeout": 0.3, "max_failures": 1},
        simulator={
            "initial_state": {"power": True, "volume": 30},
            "error_modes": {"unplugged": {"behavior": "no_response", "description": "No network"}},
        },
    )
    _DRIVER_REGISTRY["acme_simulated"] = create_configurable_driver_class(definition)
    sim = YAMLAutoSimulator("sim1", {}, driver_def=definition)
    await sim.start(0)
    port = sim.port
    session = AuditSession("out3", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    session.check = SimpleNamespace(source_ip="", heard_since=lambda since, until=None: [])
    run = DriverRun(index=0, choice=DriverChoice(driver_id="acme_simulated"))
    run.config = {"host": "127.0.0.1", "port": port}
    session.runs.append(run)
    powered = {"on": True}

    async def pinger() -> bool:
        return powered["on"] and not sim.has_error_behavior("no_response")

    try:
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.sandbox.device_state().get("volume") == 30)

        pull = start_outage(session, run, CABLE_PULL, pinger=pinger, **QUICK)
        pull.mark("off")
        sim.inject_error("unplugged")
        await _until(lambda: pull.noticed_at is not None, timeout=20.0)
        assert pull.reason["code"] == "no_response"
        sim.clear_error("unplugged")
        pull.mark("on")
        await _until(lambda: pull.status == DONE, timeout=30.0)
        assert pull.reconnected_at is not None
        assert pull.record()["repopulated"]["reported_again"] == ["power", "volume"]

        cycle = start_outage(session, run, POWER_CYCLE, pinger=pinger, **QUICK)
        cycle.mark("off")
        powered["on"] = False
        await sim.stop()
        await _until(lambda: cycle.noticed_at is not None)
        powered["on"] = True
        cycle.mark("on")
        await sim.start(port)
        await _until(lambda: cycle.status == DONE, timeout=30.0)
        assert cycle.reconnected_at is not None and cycle.reason is not None
        assert [o.kind for o in run.outages] == ["cable_pull", "power_cycle"]
    finally:
        await run.stop()
        await sim.stop()
        _DRIVER_REGISTRY.pop("acme_simulated", None)
