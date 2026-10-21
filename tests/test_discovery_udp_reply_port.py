"""A UDP probe whose answer comes back on a fixed port (``reply_port``).

Some devices answer a discovery broadcast by broadcasting to a fixed port
instead of replying to the port the query came from. These tests stage that
on loopback: a responder that sends its answer to the reply port, never to
the sender. Every exchange is real UDP on 127.0.0.1; nothing leaves the host.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from unittest.mock import patch

import pytest

from openavc.discovery import engine as engine_mod
from openavc.discovery import probe_runner as probe_runner_mod
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.explain import _declared_probe
from openavc.discovery.hints import DiscoveryHintError, parse_driver_discovery
from openavc.discovery.probe_runner import (
    MISS_NOT_SENT,
    RateLimiter,
    ReplyPortUnavailable,
    observe_udp_probe,
    run_udp_broadcast_probe,
)


def _hint(driver_id: str, **discovery):
    return parse_driver_discovery({
        "id": driver_id, "name": f"Acme {driver_id}", "manufacturer": "Acme",
        "category": "power", "transport": "tcp", "discovery": discovery,
    })


def _free_udp_port() -> int:
    # The listener under test has to bind the port itself, so it cannot be
    # held here; the gap between release and bind is the only race.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _held_port() -> socket.socket:
    """A port another program holds without sharing it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows shares under SO_REUSEADDR otherwise
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind(("", 0))
    return s


class _AnswersElsewhere:
    """A device that answers a query by sending to ``reply_port``, not the sender."""

    def __init__(self, query: bytes, reply: bytes, reply_port: int) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(3.0)
        self.port = self.sock.getsockname()[1]
        self.queried = threading.Event()
        self.sent_from_port: int | None = None

        def serve() -> None:
            try:
                data, addr = self.sock.recvfrom(2048)
                self.queried.set()
                self.sent_from_port = addr[1]
                if query in data:
                    self.sock.sendto(reply, ("127.0.0.1", reply_port))
            except OSError:
                pass
            finally:
                self.sock.close()

        threading.Thread(target=serve, daemon=True).start()


def _probe(driver_id: str, port: int, reply_port: int | None, *, expect: str, model: str = r"model=(\S+)"):
    block = {
        "port": port, "send_hex": "01 EC 00", "expect": expect,
        "timeout_ms": 1500, "extract": {"model": {"regex": model}},
    }
    if reply_port is not None:
        block["reply_port"] = reply_port
    return _hint(driver_id, udp_probe=block).udp_probe


# --- The declaration ----------------------------------------------------------


def test_reply_port_is_read_on_a_udp_probe():
    spec = _probe("acme_pdu", 62387, 62386, expect="ACME")
    assert spec.reply_port == 62386
    assert _probe("acme_pdu", 62387, None, expect="ACME").reply_port is None
    assert _declared_probe(spec).endswith("answered on UDP port 62386")


def test_reply_port_is_refused_on_a_tcp_probe():
    with pytest.raises(DiscoveryHintError, match=r"reply_port is only valid on a udp_probe"):
        _hint("acme_pdu", tcp_probe={"port": 80, "reply_port": 62386})


@pytest.mark.parametrize("bad", [0, 65536, "62386", True])
def test_reply_port_must_be_a_port(bad):
    with pytest.raises(DiscoveryHintError, match=r"reply_port"):
        _hint("acme_pdu", udp_probe={
            "port": 62387, "send_hex": "01", "expect": "x", "reply_port": bad,
        })


# --- The runner ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_answer_on_the_reply_port_identifies_the_device():
    reply_port = _free_udp_port()
    device = _AnswersElsewhere(b"\x01\xec\x00", b"ACME-PDU model=4KB", reply_port)
    results = await run_udp_broadcast_probe(
        _probe("acme_pdu", device.port, reply_port, expect="ACME-PDU"),
        targets=["127.0.0.1"], source_ip="127.0.0.1",
        rate_limiter=RateLimiter(20), accept_from=["127.0.0.0/8"],
    )
    ev = results["127.0.0.1"]
    assert ev.data["txt"]["model"] == "4KB"
    # The evidence names the port the probe went to, the one the matcher keys on.
    assert ev.data["port"] == device.port
    # Sent from an ordinary socket of its own, not from the reply port.
    assert device.sent_from_port not in (None, reply_port)
    assert probe_runner_mod._reply_listeners == {}  # closed with its last probe


@pytest.mark.asyncio
async def test_without_a_reply_port_the_same_answer_is_never_heard():
    # What the field exists for: the answer goes to a port nobody listens on.
    reply_port = _free_udp_port()
    device = _AnswersElsewhere(b"\x01\xec\x00", b"ACME-PDU model=4KB", reply_port)
    results = await run_udp_broadcast_probe(
        _probe("acme_pdu", device.port, None, expect="ACME-PDU"),
        targets=["127.0.0.1"], source_ip="127.0.0.1", rate_limiter=RateLimiter(20),
    )
    assert device.queried.is_set()
    assert results == {}


@pytest.mark.asyncio
async def test_an_answer_from_outside_the_scanned_networks_is_dropped():
    reply_port = _free_udp_port()
    device = _AnswersElsewhere(b"\x01\xec\x00", b"ACME-PDU model=4KB", reply_port)
    observed = await observe_udp_probe(
        _probe("acme_pdu", device.port, reply_port, expect="ACME-PDU"),
        targets=["127.0.0.1"], source_ip="127.0.0.1",
        rate_limiter=RateLimiter(20), accept_from=["10.77.0.0/24"],
    )
    assert device.queried.is_set()
    # Not even recorded: the wildcard socket hears every network this
    # computer is on, and an answer from elsewhere is not this scan's.
    [nothing] = observed
    assert nothing.target == "" and nothing.evidence is None and nothing.error == "no reply"


@pytest.mark.asyncio
async def test_two_drivers_naming_one_reply_port_each_hear_every_answer():
    reply_port = _free_udp_port()
    first = _AnswersElsewhere(b"\x01\xec\x00", b"ACME-A model=A1", reply_port)
    second = _AnswersElsewhere(b"\x01\xec\x00", b"ACME-B model=B2", reply_port)
    a, b = await asyncio.gather(
        run_udp_broadcast_probe(
            _probe("acme_a", first.port, reply_port, expect="ACME-A"),
            targets=["127.0.0.1"], source_ip="127.0.0.1",
            rate_limiter=RateLimiter(50), accept_from=["127.0.0.0/8"],
        ),
        run_udp_broadcast_probe(
            _probe("acme_b", second.port, reply_port, expect="ACME-B"),
            targets=["127.0.0.1"], source_ip="127.0.0.1",
            rate_limiter=RateLimiter(50), accept_from=["127.0.0.0/8"],
        ),
    )
    # One socket for both, so neither answer went to the other driver alone
    # (two binds of one port split unicast between them on Linux and Windows).
    assert a["127.0.0.1"].data["txt"]["model"] == "A1"
    assert b["127.0.0.1"].data["txt"]["model"] == "B2"
    assert probe_runner_mod._reply_listeners == {}


@pytest.mark.asyncio
async def test_a_reply_port_another_program_holds_is_said_and_nothing_is_sent():
    with _held_port() as held:
        port = held.getsockname()[1]
        device = _AnswersElsewhere(b"\x01\xec\x00", b"ACME-PDU model=4KB", port)
        spec = _probe("acme_pdu", device.port, port, expect="ACME-PDU")
        [obs] = await observe_udp_probe(
            spec, targets=["127.0.0.1"], source_ip="127.0.0.1", rate_limiter=RateLimiter(20),
        )
        assert obs.miss == MISS_NOT_SENT and obs.reply_port_error
        assert obs.error.startswith(f"could not listen on UDP port {port} for the answer: ")
        with pytest.raises(ReplyPortUnavailable) as raised:
            await run_udp_broadcast_probe(
                spec, targets=["127.0.0.1"], source_ip="127.0.0.1", rate_limiter=RateLimiter(20),
            )
        assert raised.value.port == port
        assert not device.queried.wait(0.3)
    assert probe_runner_mod._reply_listeners == {}


# --- The scan -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_scan_says_which_port_it_could_not_listen_on_and_is_not_partial():
    # The driver beside it answers the sender, the ordinary way, and still runs.
    neighbour = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    neighbour.bind(("127.0.0.1", 0))
    neighbour.settimeout(3.0)

    def answer() -> None:
        try:
            _, addr = neighbour.recvfrom(2048)
            neighbour.sendto(b"ACME-B model=B2", addr)
        except OSError:
            pass
        finally:
            neighbour.close()

    threading.Thread(target=answer, daemon=True).start()
    with _held_port() as held:
        port = held.getsockname()[1]
        engine = DiscoveryEngine()
        engine.discovery_hints = [
            _hint("acme_a", udp_probe={
                "port": 9, "send_hex": "01 EC 00", "expect": "ACME-A",
                "timeout_ms": 300, "reply_port": port,
            }),
            _hint("acme_b", udp_probe={
                "port": neighbour.getsockname()[1], "send_hex": "01 EC 00",
                "expect": "ACME-B", "timeout_ms": 1500,
            }),
        ]
        await engine._run_custom_probes(["127.0.0.1/32"], "127.0.0.1")
    [warning] = engine.scan_status.warnings
    assert warning.startswith(
        f"Could not listen on UDP port {port} for the Acme acme_a identification check ("
    )
    assert not engine._narrowed
    sources = [ev.data.get("source_id") for ev in engine.results["127.0.0.1"].evidence_log]
    assert sources == ["custom_acme_b_udp"]


@pytest.mark.asyncio
async def test_the_scan_keeps_only_answers_from_the_networks_it_scanned():
    engine = DiscoveryEngine()
    engine.discovery_hints = [_hint("acme_a", udp_probe={
        "port": 62387, "send_hex": "01 EC 00", "expect": "ACME", "reply_port": 62386,
    })]
    seen: dict = {}

    async def fake_runner(spec, **kwargs):
        seen.update(kwargs)
        return {}

    with patch.object(engine_mod, "run_udp_broadcast_probe", fake_runner):
        await engine._run_custom_probes(["10.77.0.0/24"], "10.77.0.1")
    assert seen["accept_from"] == ["10.77.0.0/24"]
