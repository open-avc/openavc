"""Discovery binds every probe to the control interface.

Ping, the driver probe runner and the multicast joins already did; the port
scan, banner grab, SNMP, NetBIOS and the SSDP description fetch did not, so on
a multi-homed host (a VPN adapter beside the AV network is the common case)
their replies could leave and return by the wrong adapter.

Each helper is checked both ways against a loopback server: bound to
127.0.0.1 it connects from there, and bound to 192.0.2.1 (TEST-NET-1, held by
no machine) it cannot connect at all, which proves the bind happened.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

from openavc.discovery import network_scanner, snmp_scanner
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.port_scanner import grab_banner, scan_host_ports
from openavc.discovery.scan_budget import ScanBudget, resolve_policy
from openavc.discovery.snmp_scanner import SNMPScanner
from openavc.discovery.ssdp_scanner import SSDPResult, SSDPScanner, _http_get

UNBINDABLE = "192.0.2.1"


async def _tcp_server(reply: bytes = b""):
    """A loopback TCP server that records each client's address."""
    peers: list[str] = []

    async def handle(reader, writer):
        peers.append(writer.get_extra_info("peername")[0])
        if reply:
            writer.write(reply)
            await writer.drain()
        await asyncio.sleep(0.05)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], peers


class TestPortScanAndBanner:
    async def test_scan_host_ports_binds(self):
        server, port, peers = await _tcp_server()
        async with server:
            assert await scan_host_ports("127.0.0.1", [port], source_ip="127.0.0.1") == [port]
            assert await scan_host_ports("127.0.0.1", [port], source_ip=UNBINDABLE) == []
        assert peers == ["127.0.0.1"]

    async def test_grab_banner_binds(self):
        server, port, peers = await _tcp_server(b"ACME WIDGET READY\r\n")
        async with server:
            assert await grab_banner("127.0.0.1", port, source_ip="127.0.0.1") == "ACME WIDGET READY"
            assert await grab_banner("127.0.0.1", port, timeout=1.0, source_ip=UNBINDABLE) is None
        assert peers == ["127.0.0.1"]


class TestSsdpDescriptionFetch:
    async def test_http_get_binds(self):
        body = b"<root><device><friendlyName>Widget</friendlyName></device></root>"
        server, port, peers = await _tcp_server(b"HTTP/1.0 200 OK\r\n\r\n" + body)
        url = f"http://127.0.0.1:{port}/desc.xml"
        async with server:
            assert await _http_get(url, timeout=2.0, source_ip="127.0.0.1") == body.decode()
            assert await _http_get(url, timeout=1.0, source_ip=UNBINDABLE) is None
        assert peers == ["127.0.0.1"]

    async def test_scanner_fetches_from_its_control_interface(self):
        scanner = SSDPScanner(control_ip="10.77.0.2")
        result = SSDPResult(ip="10.77.0.40", location="http://10.77.0.40:49152/desc.xml")
        with patch("openavc.discovery.ssdp_scanner._http_get",
                   new_callable=AsyncMock, return_value=None) as fetch:
            await scanner._fetch_single_description(result)
        assert fetch.await_args.kwargs["source_ip"] == "10.77.0.2"


class TestUdpQueries:
    async def test_snmp_binds(self):
        seen: list[str] = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        port = sock.getsockname()[1]
        loop = asyncio.get_running_loop()

        async def drain():
            while True:
                _, addr = await loop.sock_recvfrom(sock, 2048)
                seen.append(addr[0])

        reader = asyncio.create_task(drain())
        try:
            with patch.object(snmp_scanner, "SNMP_PORT", port):
                # No agent answers: only who sent the request matters.
                assert await SNMPScanner(source_ip="127.0.0.1").query_device(
                    "127.0.0.1", timeout=0.3) is None
                assert await SNMPScanner(source_ip=UNBINDABLE).query_device(
                    "127.0.0.1", timeout=0.3) is None
            await asyncio.sleep(0.05)
        finally:
            reader.cancel()
            sock.close()
        assert seen == ["127.0.0.1"]

    async def test_netbios_binds(self):
        binds: list[tuple] = []

        class SpySocket(socket.socket):
            def bind(self, address):
                binds.append(address)
                return super().bind(address)

        with patch.object(network_scanner.socket, "socket", SpySocket):
            await network_scanner.netbios_query("127.0.0.1", timeout=0.2, source_ip="127.0.0.1")
            await network_scanner.netbios_query("127.0.0.1", timeout=0.2)
        assert binds == [("127.0.0.1", 0)]


class TestEngineWiring:
    async def test_pinned_control_interface_reaches_every_probe(self):
        engine = DiscoveryEngine()
        engine._budget = ScanBudget(resolve_policy("standard"), 300.0)
        host = "10.77.0.9"

        async def sweep(subnets, **kwargs):
            await kwargs["on_found"](host)
            return [host]

        ports = AsyncMock(return_value=[23])
        banners = AsyncMock(return_value={})
        netbios = AsyncMock(return_value={})
        snmp_cls = MagicMock()
        snmp_cls.return_value.scan_devices = AsyncMock(return_value={})
        snmp_cls.return_value.results = {}

        def listener(method):
            cls = MagicMock()
            setattr(cls.return_value, method, AsyncMock(return_value={}))
            cls.return_value.stop = AsyncMock()
            cls.return_value.results = {}
            cls.return_value.env_error = None
            return cls

        with patch.object(engine, "_get_control_interface", return_value="10.77.0.2"), \
             patch("openavc.discovery.engine.ping_sweep", side_effect=sweep), \
             patch("openavc.discovery.engine.harvest_arp_table", new_callable=AsyncMock, return_value={}), \
             patch("openavc.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}), \
             patch("openavc.discovery.engine.netbios_sweep", netbios), \
             patch("openavc.discovery.engine.scan_host_ports", ports), \
             patch("openavc.discovery.engine.grab_banners", banners), \
             patch("openavc.discovery.engine.SNMPScanner", snmp_cls), \
             patch.object(engine.community_index, "get_drivers", new_callable=AsyncMock, return_value=[]), \
             patch("openavc.discovery.engine.MDNSScanner", listener("start")), \
             patch("openavc.discovery.engine.SSDPScanner", listener("scan")), \
             patch("openavc.discovery.engine.AMXDDPScanner", listener("start")):
            engine.scan_status.subnets = ["10.77.0.0/24"]
            await engine._scan_pipeline_inner(["10.77.0.0/24"])

        assert ports.await_args.kwargs["source_ip"] == "10.77.0.2"
        assert banners.await_args.kwargs["source_ip"] == "10.77.0.2"
        assert netbios.await_args.kwargs["source_ip"] == "10.77.0.2"
        assert snmp_cls.call_args.kwargs["source_ip"] == "10.77.0.2"
