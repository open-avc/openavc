"""Every driver probe exchange is kept as an observation, matched or not.

A scan keeps only matching replies, as evidence, and that is unchanged. The
``observe_*`` runners also return what each probe sent and read, the
certificate on a TLS probe, the ``then:`` step, the timing, and why nothing
came back, so a single-device check can show the near misses that correct a
driver's fingerprint. UDP reply bytes are kept, even on a match.
"""

from __future__ import annotations

import asyncio
import socket
import ssl

from openavc.discovery.hints import parse_driver_discovery
from openavc.discovery.probe_runner import (
    MISS_CONNECT,
    MISS_FOLLOW_UP,
    MISS_NO_REPLY,
    MISS_REPLY,
    RateLimiter,
    observe_tcp_active_probe,
    observe_udp_probe,
    run_tcp_active_probe,
)


def _spec(kind: str, **block):
    hint = parse_driver_discovery({
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "tcp",
        "discovery": {f"{kind}_probe": {"timeout_ms": 1500, **block}},
    })
    return hint.tcp_probe if kind == "tcp" else hint.udp_probe


async def _server(replies: list[bytes], ssl_ctx=None):
    """Loopback TCP server: after each read, send the next reply."""
    async def handle(reader, writer):
        for reply in replies:
            try:
                await asyncio.wait_for(reader.read(1024), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            writer.write(reply)
            await writer.drain()
        await asyncio.sleep(0.3)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ssl_ctx)
    return server, server.sockets[0].getsockname()[1]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestTcpObservations:
    async def test_a_miss_keeps_what_the_device_said(self):
        server, port = await _server([b"HELLO FROM SOMETHING ELSE\r\n"])
        async with server:
            spec = _spec("tcp", port=port, send_ascii="ID?\r", expect_regex="^ACME")
            obs = await observe_tcp_active_probe(spec, target="127.0.0.1", source_ip="")
        assert obs.matched is False and obs.evidence is None
        assert obs.sent == b"ID?\r"
        assert obs.reply == b"HELLO FROM SOMETHING ELSE\r\n"
        assert obs.error == "" and obs.miss == MISS_REPLY
        assert obs.connect_ms is not None and obs.first_reply_ms is not None
        view = obs.to_dict()
        assert view["reply"]["hex"] == b"HELLO FROM SOMETHING ELSE\r\n".hex()
        assert view["reply"]["text"].startswith("HELLO")

    async def test_a_match_carries_the_scan_evidence(self):
        server, port = await _server([b"ACME WIDGET 3000\r\n"])
        async with server:
            spec = _spec("tcp", port=port, send_ascii="ID?\r", expect_regex="^ACME")
            obs = await observe_tcp_active_probe(spec, target="127.0.0.1", source_ip="")
        assert obs.matched is True and obs.miss == ""
        assert obs.evidence is not None
        assert obs.evidence.source == "probe:custom_acme_widget_tcp"
        assert obs.evidence.data["response"]["text"] == "ACME WIDGET 3000\r\n"

    async def test_run_returns_only_the_evidence(self):
        server, port = await _server([b"NOT IT\r\n"])
        async with server:
            spec = _spec("tcp", port=port, send_ascii="ID?\r", expect_regex="^ACME")
            assert await run_tcp_active_probe(spec, target="127.0.0.1", source_ip="") is None

    async def test_refused_is_named(self):
        # Windows retries a SYN to a closed loopback port for about 2 s before
        # it reports the refusal, so this probe gets a longer timeout.
        spec = _spec("tcp", port=_free_port(), expect_regex="^ACME", timeout_ms=5000)
        obs = await observe_tcp_active_probe(spec, target="127.0.0.1", source_ip="")
        assert obs.error == "refused" and obs.miss == MISS_CONNECT
        assert obs.reply == b"" and obs.connect_ms is None

    async def test_then_step_is_recorded(self):
        server, port = await _server([b"ACME READY\r\n", b"UNEXPECTED\r\n"])
        async with server:
            spec = _spec(
                "tcp", port=port, send_ascii="ID?\r", expect_regex="^ACME",
                then={"send_ascii": "VER?\r", "expect_regex": "^V[0-9]", "timeout_ms": 800},
            )
            obs = await observe_tcp_active_probe(spec, target="127.0.0.1", source_ip="")
        assert obs.reply == b"ACME READY\r\n"
        assert obs.follow_up_sent == b"VER?\r"
        assert obs.follow_up_reply == b"UNEXPECTED\r\n"
        assert obs.follow_up_ok is False and obs.miss == MISS_FOLLOW_UP
        assert obs.matched is False and obs.evidence is None
        assert obs.to_dict()["follow_up"]["ok"] is False

    async def test_tls_cert_is_recorded_but_evidence_is_unchanged(self, tmp_path):
        from openavc.tls import generate_self_signed

        certs = generate_self_signed(tmp_path, hostnames=["widget-3000"], ips=["127.0.0.1"])
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certs.cert_path, certs.key_path)
        server, port = await _server([b"HTTP/1.0 200 OK\r\n\r\n<title>Acme Widget</title>"], ssl_ctx=ctx)
        async with server:
            spec = _spec("tcp", port=port, tls=True,
                         send_ascii="GET / HTTP/1.0\r\n\r\n", expect="Acme Widget")
            obs = await observe_tcp_active_probe(spec, target="127.0.0.1", source_ip="")
        assert obs.tls is True
        assert "widget-3000" in obs.cert_subject
        assert obs.matched is True
        # No cert rule and no extract: the evidence carries no cert, as before.
        assert "cert_subject" not in obs.evidence.data["response"]


class TestUdpObservations:
    async def _responder(self, reply: bytes | None):
        loop = asyncio.get_running_loop()

        class Proto(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                if reply is not None:
                    self.transport.sendto(reply, addr)

        transport, _ = await loop.create_datagram_endpoint(Proto, local_addr=("127.0.0.1", 0))
        return transport, transport.get_extra_info("sockname")[1]

    async def test_matching_reply_bytes_are_kept(self):
        transport, port = await self._responder(b"ACME-W3000 fw=2.1")
        try:
            spec = _spec("udp", port=port, send_hex="00010203", expect_regex="^ACME")
            [obs] = await observe_udp_probe(
                spec, targets=["127.0.0.1"], source_ip="127.0.0.1",
                rate_limiter=RateLimiter(100.0),
            )
        finally:
            transport.close()
        assert obs.target == "127.0.0.1"
        assert obs.sent == bytes.fromhex("00010203")
        assert obs.reply == b"ACME-W3000 fw=2.1"
        assert obs.matched is True and obs.evidence is not None
        assert obs.first_reply_ms is not None

    async def test_non_matching_reply_is_kept_without_evidence(self):
        transport, port = await self._responder(b"SOMEONE ELSE")
        try:
            spec = _spec("udp", port=port, send_hex="00", expect_regex="^ACME")
            [obs] = await observe_udp_probe(
                spec, targets=["127.0.0.1"], source_ip="127.0.0.1",
                rate_limiter=RateLimiter(100.0),
            )
        finally:
            transport.close()
        assert obs.reply == b"SOMEONE ELSE" and obs.miss == MISS_REPLY
        assert obs.matched is False and obs.evidence is None

    async def test_no_reply_is_one_observation(self):
        transport, port = await self._responder(None)
        try:
            spec = _spec("udp", port=port, send_hex="00", expect_regex="^ACME",
                         timeout_ms=300)
            [obs] = await observe_udp_probe(
                spec, targets=["127.0.0.1"], source_ip="127.0.0.1",
                rate_limiter=RateLimiter(100.0),
            )
        finally:
            transport.close()
        assert obs.target == "" and obs.error == "no reply" and obs.miss == MISS_NO_REPLY
        assert obs.sent == bytes.fromhex("00")
        assert obs.sent_to == ("127.0.0.1",)
