"""A scan's connections follow one set of rules (``discovery/port_guard.py``).

- A port a connected project device holds is never opened: not by the port
  scan, not by a driver's identification check, not by a companion. Many
  displays and projectors serve one control connection at a time, and a new
  one takes the port from the driver's.
- A port's greeting is read first, and a port that asks for a login is sent
  only the checks that expect that prompt.
- Two connections never reach one address and port at the same moment.
- Each discovered device says which checks each of its ports received.

The device is an invented Acme Widget on loopback servers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import pytest

from openavc.discovery.companion import ProbeContext, run_companion
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.hints import parse_driver_discovery
from openavc.discovery.port_guard import (
    GREETING_LOGIN,
    GREETING_SILENT,
    GREETING_SPOKE,
    HOW_COMPANION,
    HOW_CONNECTED,
    HOW_GREETING,
    HOW_IN_USE,
    HOW_LOGIN_PROMPT,
    PortGuard,
    PortInUse,
    is_login_prompt,
)
from openavc.discovery.port_scanner import PORT_IN_USE, PORT_OPEN, scan_host_port_states
from openavc.discovery.probe_runner import (
    MISS_IN_USE,
    MISS_LOGIN_PROMPT,
    observe_port_probes,
    observe_tcp_active_probe,
    port_probe_seconds,
)

HOST = "127.0.0.1"
LOGIN_GREETING = b"Acme Widget 3000\r\nUser-name: "


class _Port:
    """A loopback TCP port that writes ``greeting`` on every connection and
    keeps what each connection sent, how many were open at once, and how many
    there were. ``reply`` maps a line it receives to what it answers."""

    def __init__(self, greeting: bytes = b"", reply: dict[bytes, bytes] | None = None,
                 hold_open: float = 0.0) -> None:
        self.greeting = greeting
        self.reply = reply or {}
        self.hold_open = hold_open
        self.accepted = 0
        self.open_now = 0
        self.most_open = 0
        self.received: list[bytes] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def __aenter__(self) -> "_Port":
        self.server = await asyncio.start_server(self._handle, HOST, 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.accepted += 1
        self.open_now += 1
        self.most_open = max(self.most_open, self.open_now)
        got = bytearray()
        try:
            if self.greeting:
                writer.write(self.greeting)
                await writer.drain()
            if self.hold_open:
                await asyncio.sleep(self.hold_open)
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=3.0)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                got += chunk
                for line, answer in self.reply.items():
                    if line in got:
                        writer.write(answer)
                        await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.open_now -= 1
            self.received.append(bytes(got))
            writer.close()


def _spec(driver_id: str, port: int, **probe):
    return parse_driver_discovery({
        "id": driver_id, "name": driver_id, "manufacturer": "Acme",
        "category": "utility", "transport": "tcp",
        "discovery": {"tcp_probe": {"port": port, **probe}},
    }).tcp_probe


# ---------------------------------------------------------------------------
# What counts as a login prompt
# ---------------------------------------------------------------------------


class TestLoginPrompt:
    @pytest.mark.parametrize("greeting", [
        LOGIN_GREETING,
        b"login: ",
        b"\xff\xfd\x18\xff\xfb\x01\xff\xfb\x03\r\nAcme Widget\r\nlogin: ",
        b"Password:",
        b"Acme Widget\r\nEnter password: ",
        b"Username:",
        b"login as: ",
    ])
    def test_a_prompt_for_a_user_name_or_password_is_one(self, greeting):
        assert is_login_prompt(greeting)

    @pytest.mark.parametrize("greeting", [
        b"",
        b"Acme Widget 3000 ready\r\n",
        b"Acme Widget 3000\r\n> ",
        b"Last login: Tue Oct 27 09:15:02 2026\r\nacme$ ",
        b"Welcome to the Acme Widget. Type HELP for commands.\r\n",
        b"\xff\xfb\x01\xff\xfb\x03",
    ])
    def test_a_banner_or_a_command_prompt_is_not(self, greeting):
        assert not is_login_prompt(greeting)


# ---------------------------------------------------------------------------
# A port a connected device holds is never opened
# ---------------------------------------------------------------------------


class TestPortsInUse:
    async def test_the_port_scan_skips_a_held_port_and_scans_the_rest(self):
        async with _Port() as held, _Port() as free:
            guard = PortGuard({(HOST, held.port): "Lobby Widget"})
            states = await scan_host_port_states(
                HOST, [held.port, free.port], timeout=1.0, stagger_ms=0, guard=guard,
            )
            await asyncio.sleep(0.1)
        assert states == {held.port: PORT_IN_USE, free.port: PORT_OPEN}
        assert held.accepted == 0
        assert free.accepted == 1
        assert guard.record(HOST)[held.port] == {"in_use_by": "Lobby Widget"}

    async def test_the_identification_checks_skip_a_held_port(self):
        async with _Port(greeting=b"ACME-WIDGET 3000\r\n") as held:
            guard = PortGuard({(HOST, held.port): "Lobby Widget"})
            specs = [
                _spec("acme_widget", held.port, expect="ACME-WIDGET"),
                _spec("acme_gadget", held.port, send_ascii="ID?\r", expect="GADGET"),
            ]
            observed = await observe_port_probes(
                HOST, held.port, specs, source_ip="", guard=guard,
            )
            single = await observe_tcp_active_probe(
                specs[1], target=HOST, source_ip="", guard=guard,
            )
        assert held.accepted == 0
        assert [o.miss for o in observed] == [MISS_IN_USE, MISS_IN_USE]
        assert single.miss == MISS_IN_USE and not single.sent
        record = guard.record(HOST)[held.port]
        assert record["in_use_by"] == "Lobby Widget"
        assert [p["how"] for p in record["probes"]] == [HOW_IN_USE, HOW_IN_USE]


class _Connection:
    def __init__(self, host, port, name):
        self.host, self.port, self.name = host, port, name


class TestTheScanLeavesConnectedDevicesAlone:
    """The engine reads the project's connected devices when a scan starts
    and none of the scan's connections reach their ports."""

    def _engine(self, connections) -> DiscoveryEngine:
        engine = DiscoveryEngine()
        engine.held_connections = lambda: list(connections)
        return engine

    async def test_a_held_port_is_neither_scanned_nor_probed(self):
        async with _Port(greeting=b"ACME-WIDGET 3000\r\n") as held, \
                _Port(greeting=b"ACME-WIDGET 3000\r\n") as free:
            engine = self._engine([_Connection(HOST, held.port, "Lobby Widget")])
            engine.discovery_hints = [parse_driver_discovery({
                "id": "acme_widget", "name": "Acme Widget", "manufacturer": "Acme",
                "category": "utility", "transport": "tcp",
                "discovery": {"tcp_probe": {"port": port, "expect": "ACME-WIDGET",
                                            "timeout_ms": 500}},
            }) for port in (held.port, free.port)]
            engine._guard = PortGuard(await engine._held_ports())

            await engine._port_scan_hosts([HOST], [held.port, free.port], 1, False)
            device = engine.results[HOST]
            assert device.open_ports == [free.port]
            # An announcement can name a port the scan never opened.
            device.open_ports = [held.port, free.port]
            await engine._run_custom_probes([f"{HOST}/32"], "")
            await engine._finalize_scan()
            await asyncio.sleep(0.1)

        assert held.accepted == 0
        assert free.accepted >= 1
        ports = device.to_dict()["port_probes"]
        assert ports[str(held.port)]["in_use_by"] == "Lobby Widget"
        assert [p["how"] for p in ports[str(held.port)]["probes"]] == [HOW_IN_USE]
        assert "greeting" not in ports[str(held.port)]
        assert [p["how"] for p in ports[str(free.port)]["probes"]] == [HOW_GREETING]

    async def test_a_device_set_up_by_host_name_is_found_by_its_address(self):
        engine = self._engine([_Connection("localhost", 4321, "Lobby Widget")])
        held = await engine._held_ports()
        assert held == {(HOST, 4321): "Lobby Widget"}

    async def test_a_host_name_that_does_not_resolve_is_named_in_the_warnings(self):
        engine = self._engine([_Connection("nowhere.invalid", 4321, "Lobby Widget")])
        assert await engine._held_ports() == {}
        [warning] = engine.scan_status.warnings
        assert "nowhere.invalid" in warning and "Lobby Widget" in warning

    async def test_no_project_means_nothing_is_held(self):
        assert await DiscoveryEngine()._held_ports() == {}

    async def test_a_companion_is_not_offered_a_held_port(self):
        engine = self._engine([_Connection(HOST, 4321, "Lobby Widget")])
        engine._guard = PortGuard(await engine._held_ports())
        device = engine._get_or_create(HOST)
        device.alive = True
        device.open_ports = [80, 4321]
        offered: dict = {}

        async def probe(ctx: ProbeContext) -> None:
            offered.update(ctx.hosts_by_open_port)

        engine._discovery_companions = {"acme_widget": probe}
        await engine._run_custom_probes([f"{HOST}/32"], "")
        assert offered == {80: (HOST,)}


class TestLiveConnections:
    """What the engine asks: where each connected device's driver connects."""

    def test_only_connected_network_devices_are_listed_at_their_own_address(self):
        from openavc.core.device_manager import DeviceManager
        from openavc.core.event_bus import EventBus
        from openavc.core.state_store import StateStore

        manager = DeviceManager(StateStore(), EventBus())

        class _Driver:
            def __init__(self, connected: bool) -> None:
                self.connected = connected
                self.config: dict = {}

            def get_state(self, key: str):
                return self.connected if key == "connected" else None

        def add(device_id: str, name: str, connected: bool, **config) -> None:
            manager._devices[device_id] = _Driver(connected)
            manager._device_configs[device_id] = {
                "id": device_id, "name": name, "driver": "acme_widget", "config": config,
            }

        add("lobby_widget", "Lobby Widget", True, host="10.77.0.5", port=4321)
        add("spare_widget", "Spare Widget", False, host="10.77.0.6", port=4321)
        add("rack_widget", "Rack Widget", True, transport="serial", port="/dev/ttyUSB0")
        # A running simulation points the live driver at loopback; the record
        # still names the device's own address.
        manager._devices["lobby_widget"].config = {"host": "127.0.0.1", "port": 19001}

        assert [(c.name, c.host, c.port) for c in manager.live_connections()] == [
            ("Lobby Widget", "10.77.0.5", 4321),
        ]


# ---------------------------------------------------------------------------
# A login prompt gets only the checks that expect it
# ---------------------------------------------------------------------------


class TestLoginPromptGetsOnlyLoginChecks:
    async def test_a_query_is_never_typed_into_a_login_prompt(self):
        async with _Port(greeting=LOGIN_GREETING, reply={b"\r": b"Password: "}) as dev:
            specs = [
                # Listens only: judged on the greeting, no connection of its own.
                _spec("acme_widget", dev.port, expect="Acme Widget 3000"),
                # Another driver's query: not sent.
                _spec("acme_gadget", dev.port, send_ascii="GET STATUS\r", expect="OK"),
                # Written for this prompt: it looks for it, so it is sent.
                _spec("acme_widget_pro", dev.port, send_ascii="\r", expect_regex="User-name:"),
            ]
            guard = PortGuard()
            observed = await observe_port_probes(HOST, dev.port, specs, source_ip="", guard=guard)
            await asyncio.sleep(0.1)

        widget, gadget, pro = observed
        assert widget.matched and widget.from_greeting
        assert gadget.miss == MISS_LOGIN_PROMPT and gadget.sent == b""
        assert pro.sent == b"\r"
        # The greeting read sent nothing; the one check written for the
        # prompt sent its line; nobody sent the query.
        assert sorted(dev.received) == [b"", b"\r"]
        assert not any(b"GET STATUS" in r for r in dev.received)
        record = guard.record(HOST)[dev.port]
        assert record["greeting"] == GREETING_LOGIN
        assert [(p["driver_id"], p["how"]) for p in record["probes"]] == [
            ("acme_widget", HOW_GREETING),
            ("acme_gadget", HOW_LOGIN_PROMPT),
            ("acme_widget_pro", HOW_CONNECTED),
        ]

    async def test_a_port_with_no_login_gets_every_check(self):
        async with _Port(greeting=b"ACME-WIDGET 3000\r\n", reply={b"ID?\r": b"OK GADGET\r\n"}) as dev:
            specs = [
                _spec("acme_widget", dev.port, expect="ACME-WIDGET"),
                _spec("acme_gadget", dev.port, send_ascii="ID?\r", expect="GADGET"),
            ]
            guard = PortGuard()
            widget, gadget = await observe_port_probes(
                HOST, dev.port, specs, source_ip="", guard=guard,
            )
        assert widget.matched and widget.from_greeting
        assert gadget.matched and gadget.sent == b"ID?\r"
        assert guard.record(HOST)[dev.port]["greeting"] == GREETING_SPOKE

    async def test_a_port_that_says_nothing_is_listened_to_twice_and_still_probed(self):
        async with _Port(reply={b"ID?\r": b"OK GADGET\r\n"}) as dev:
            specs = [
                _spec("acme_widget", dev.port, expect="ACME-WIDGET", timeout_ms=300),
                _spec("acme_gadget", dev.port, send_ascii="ID?\r", expect="GADGET"),
            ]
            guard = PortGuard()
            widget, gadget = await observe_port_probes(
                HOST, dev.port, specs, source_ip="", guard=guard,
            )
        assert not widget.matched
        assert gadget.matched
        # Two greeting reads (the second for the check that only listens),
        # then the one check that sends.
        assert dev.accepted == 3
        assert guard.record(HOST)[dev.port]["greeting"] == GREETING_SILENT


# ---------------------------------------------------------------------------
# Never two connections to one port at once
# ---------------------------------------------------------------------------


class TestOneConnectionPerPort:
    async def test_a_ports_checks_run_one_at_a_time(self):
        async with _Port(greeting=b"ACME-WIDGET 3000\r\n", hold_open=0.1) as dev:
            specs = [
                _spec(f"acme_{n}", dev.port, send_ascii=f"Q{n}\r", expect="NEVER",
                      timeout_ms=400)
                for n in range(4)
            ]
            await observe_port_probes(HOST, dev.port, specs, source_ip="", guard=PortGuard())
        assert dev.accepted == 5  # the greeting read, then each check
        assert dev.most_open == 1

    async def test_the_scan_never_opens_one_port_twice_at_once(self):
        """Many drivers probing the same port, run the way a scan runs them."""
        async with _Port(greeting=b"ACME-WIDGET 3000\r\n", hold_open=0.05) as dev:
            engine = DiscoveryEngine()
            engine.discovery_hints = [parse_driver_discovery({
                "id": f"acme_{n}", "name": f"Acme {n}", "manufacturer": "Acme",
                "category": "utility", "transport": "tcp",
                "discovery": {"tcp_probe": {
                    "port": dev.port, "send_ascii": f"Q{n}\r", "expect": "NEVER",
                    "timeout_ms": 300,
                }},
            }) for n in range(5)]
            device = engine._get_or_create(HOST)
            device.alive = True
            device.open_ports = [dev.port]
            await engine._run_custom_probes([f"{HOST}/32"], "")
        assert dev.accepted == 6
        assert dev.most_open == 1

    async def test_companions_and_checks_share_the_one_connection(self):
        async with _Port(greeting=b"ACME-WIDGET 3000\r\n", hold_open=0.15) as dev:
            guard = PortGuard()

            async def companion(ctx: ProbeContext) -> None:
                for _ in range(2):
                    async with ctx.hold_port(HOST, dev.port):
                        _, writer = await asyncio.open_connection(HOST, dev.port)
                        await asyncio.sleep(0.15)
                        writer.close()

            def context(driver_id: str) -> ProbeContext:
                async def emit(host, ev):
                    pass
                return ProbeContext(
                    driver_id=driver_id, source_ip="", target_subnets=(f"{HOST}/32",),
                    timeout_seconds=5.0, log=logging.getLogger("test"),
                    _emit_for_host=emit, hosts_by_open_port={dev.port: (HOST,)},
                    _guard=guard,
                )

            specs = [_spec("acme_gadget", dev.port, send_ascii="ID?\r", expect="NEVER",
                           timeout_ms=300)]
            await asyncio.gather(
                run_companion("acme_widget", companion, context("acme_widget")),
                run_companion("acme_widget_pro", companion, context("acme_widget_pro")),
                observe_port_probes(HOST, dev.port, specs, source_ip="", guard=guard),
            )
        assert dev.most_open == 1
        assert dev.accepted == 6  # two companions twice each, the greeting, the check
        companion_turns = [
            p for p in guard.record(HOST)[dev.port]["probes"] if p["how"] == HOW_COMPANION
        ]
        assert len(companion_turns) == 4


# ---------------------------------------------------------------------------
# A companion's hold
# ---------------------------------------------------------------------------


@dataclass
class _Emit:
    async def __call__(self, host, ev) -> None:
        pass


def _companion_ctx(guard: PortGuard, port: int, timeout: float = 5.0) -> ProbeContext:
    return ProbeContext(
        driver_id="acme_widget", source_ip="", target_subnets=(f"{HOST}/32",),
        timeout_seconds=timeout, log=logging.getLogger("test"), _emit_for_host=_Emit(),
        hosts_by_open_port={port: (HOST,)}, _guard=guard,
    )


class TestCompanionHold:
    async def test_a_held_port_is_refused(self):
        guard = PortGuard({(HOST, 4321): "Lobby Widget"})
        raised: list[BaseException] = []

        async def companion(ctx: ProbeContext) -> None:
            try:
                async with ctx.hold_port(HOST, 4321):
                    raise AssertionError("held port granted")
            except PortInUse as exc:
                raised.append(exc)

        await run_companion("acme_widget", companion, _companion_ctx(guard, 4321))
        [exc] = raised
        assert isinstance(exc, ConnectionRefusedError)
        assert exc.holder == "Lobby Widget"

    async def test_a_companion_cut_off_mid_hold_gives_the_port_back(self):
        guard = PortGuard()

        async def companion(ctx: ProbeContext) -> None:
            async with ctx.hold_port(HOST, 4321):
                await asyncio.sleep(30)

        await run_companion("acme_widget", companion, _companion_ctx(guard, 4321, timeout=0.5))
        assert not guard.is_held_open(HOST, 4321)
        await asyncio.wait_for(guard.acquire(HOST, 4321), timeout=1.0)

    async def test_a_companion_wedged_mid_hold_gives_the_port_back(self, monkeypatch):
        from openavc.discovery import companion as companion_mod

        monkeypatch.setattr(companion_mod, "THREAD_ABANDON_GRACE_SECONDS", 0.2)
        monkeypatch.setattr(companion_mod, "_wedged_threads", {})
        guard = PortGuard()

        async def companion(ctx: ProbeContext) -> None:
            async with ctx.hold_port(HOST, 4321):
                time.sleep(1.5)  # blocking: no cancellation reaches it

        await run_companion("acme_widget", companion, _companion_ctx(guard, 4321, timeout=0.5))
        assert not guard.is_held_open(HOST, 4321)
        await asyncio.wait_for(guard.acquire(HOST, 4321), timeout=1.0)
        guard.release(HOST, 4321)
        await asyncio.sleep(1.5)  # let the abandoned thread finish and give back nothing
        assert not guard.is_held_open(HOST, 4321)

    async def test_without_a_guard_the_hold_is_a_no_op(self):
        ctx = _companion_ctx(PortGuard(), 4321)
        ctx._guard = None
        async with ctx.hold_port(HOST, 4321):
            pass


# ---------------------------------------------------------------------------
# The budget counts a port's checks one after another
# ---------------------------------------------------------------------------


class TestPortProbeSeconds:
    def test_a_port_of_senders_adds_them_up(self):
        specs = [
            _spec("acme_widget", 4321, send_ascii="A\r", expect="A", timeout_ms=1000),
            _spec("acme_gadget", 4321, send_ascii="B\r", expect="B", timeout_ms=500),
        ]
        # One connect allowance (2 s), the greeting read (1.5 s), then each
        # check's reply.
        assert port_probe_seconds(specs) == pytest.approx(2.0 + 1.5 + 1.0 + 0.5)

    def test_a_check_that_only_listens_costs_a_second_read_not_a_connection(self):
        specs = [
            _spec("acme_widget", 4321, expect="ACME", timeout_ms=4000),
            _spec("acme_gadget", 4321, expect="GADGET", timeout_ms=1000),
        ]
        # Two reads, each listening as long as the longest listener would.
        assert port_probe_seconds(specs) == pytest.approx(2.0 + 2 * 4.0)
