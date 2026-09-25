"""The network check against loopback fakes.

Real servers on 127.0.0.1 (a telnet-style banner port, a web server that asks
for a password, a TLS server with a self-signed certificate, the SNMP
simulator) and real mDNS, SSDP and AMX DDP packets fed through the real
listeners' parsers. The device is invented (Acme Widget 3000); the catalog is
a fake index.json with two invented drivers, served through the real catalog
cache.

The listeners are handed in rather than started: joining multicast groups
and binding 5353/1900/9131 is not something every test machine allows, and
what matters here is what the check does with what they heard.
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import struct

import pytest

from openavc.audit import footprint as fpmod
from openavc.audit.footprint import ACTIVITIES, DONE, SKIPPED, NetworkCheck
from openavc.discovery import community_index as ci
from openavc.discovery import snmp_scanner
from openavc.discovery.amx_ddp_scanner import AMXDDPScanner
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.mdns_scanner import (
    DNS_SD_META_QUERY,
    DNS_TYPE_A,
    DNS_TYPE_PTR,
    DNS_TYPE_SRV,
    DNS_TYPE_TXT,
    MDNSScanner,
    encode_dns_name,
)
from openavc.discovery.port_scanner import PORT_OPEN, PORT_REFUSED
from openavc.discovery.ssdp_scanner import SSDPScanner
from openavc.simulator.self_signed_tls import _generate_self_signed, remove_cert_files
from openavc.simulator.snmp_simulator import SNMPSimulator

HOST = "127.0.0.1"
VENDOR_TYPE = "_acmewidget._tcp.local."
URN = "urn:acme-com:device:Widget:1"
BANNER = b"\xff\xfb\x01\xff\xfb\x03ACME-WIDGET 3000 v1.2\r\nlogin: "
DESCRIPTION = (
    '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
    f"<deviceType>{URN}</deviceType><friendlyName>Lobby Widget</friendlyName>"
    "<manufacturer>Acme</manufacturer><modelName>Widget 3000</modelName>"
    "<serialNumber>AW3-0042</serialNumber></device></root>"
)


# ---------------------------------------------------------------------------
# Loopback servers
# ---------------------------------------------------------------------------


async def _serve(handler, ssl_ctx=None):
    server = await asyncio.start_server(handler, HOST, 0, ssl=ssl_ctx)
    return server, server.sockets[0].getsockname()[1]


async def _banner(reader, writer):
    writer.write(BANNER)
    await writer.drain()
    try:
        await reader.read(1024)
    except (ConnectionError, OSError):
        pass
    writer.close()


async def _web(reader, writer):
    try:
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
        writer.close()
        return
    if b"GET /desc.xml" in request:
        body = DESCRIPTION.encode()
        writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: text/xml\r\n\r\n" + body)
    else:
        writer.write(
            b"HTTP/1.0 401 Unauthorized\r\nServer: AcmeHTTP/2\r\n"
            b'WWW-Authenticate: Basic realm="Widget 3000"\r\n'
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            b"<html><head><title>Widget 3000\n Login</title></head></html>"
        )
    await writer.drain()
    writer.close()


async def _secure(reader, writer):
    try:
        await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, ssl.SSLError):
        writer.close()
        return
    writer.write(
        b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
        b"<title>Secure Widget</title>"
    )
    await writer.drain()
    writer.close()


class WidgetAgent(SNMPSimulator):
    SIMULATOR_INFO = {
        "driver_id": "acme_widget", "name": "Acme Widget", "category": "utility",
        "transport": "snmp",
    }
    READ_COMMUNITY = "widgets"
    OIDS = {
        "1.3.6.1.2.1.1.1.0": ("string", "Acme Widget 3000"),
        "1.3.6.1.2.1.1.2.0": ("oid", "1.3.6.1.4.1.99999.1"),
        "1.3.6.1.2.1.1.5.0": ("string", "widget-3000"),
        "1.3.6.1.2.1.1.3.0": ("timeticks", 12345),
    }


@pytest.fixture
async def bench(monkeypatch):
    """Every server up, plus a port that refuses."""
    banner, banner_port = await _serve(_banner)
    web, web_port = await _serve(_web)
    cert, key = _generate_self_signed()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    secure, secure_port = await _serve(_secure, ctx)
    agent = WidgetAgent("widget-snmp")
    await agent.start(0)
    monkeypatch.setattr(
        snmp_scanner, "SNMP_PORT", agent._udp_transport.get_extra_info("sockname")[1],
    )
    closed = socket.socket()
    closed.bind((HOST, 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    try:
        yield {
            "banner": banner_port, "web": web_port, "secure": secure_port,
            "closed": closed_port,
        }
    finally:
        for server in (banner, web, secure):
            server.close()
            await server.wait_closed()
        await agent.stop()
        remove_cert_files((cert, key))


# ---------------------------------------------------------------------------
# The catalog and the listeners
# ---------------------------------------------------------------------------


def _catalog(bench) -> bytes:
    def entry(driver_id, name, discovery):
        return {
            "id": driver_id, "name": name, "manufacturer": "Acme", "category": "utility",
            "transport": "tcp", "version": "1.0.0", "discovery": discovery,
        }

    return json.dumps({"drivers": [
        entry("acme_widget", "Acme Widget", {
            "mdns": VENDOR_TYPE,
            "tcp_probe": {"port": bench["banner"], "expect_regex": "ACME-WIDGET"},
        }),
        entry("acme_gadget", "Acme Gadget", {
            "tcp_probe": {"port": bench["web"], "send_ascii": "HELLO\r\n", "expect_regex": "GADGET"},
            "snmp_pen": 99999,
        }),
    ]}).encode()


@pytest.fixture
def discovery(monkeypatch, bench):
    raw = _catalog(bench)

    async def fetch(_path):
        return raw, ""

    monkeypatch.setattr(ci, "_fetch_raw_with_retry", fetch)
    engine = DiscoveryEngine()
    engine.load_driver_hints_from_registry([])
    return engine


def _rr(name, rtype, rdata):
    return encode_dns_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def _response(*records):
    return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def _mdns_packet(ip: str, service_type: str, instance: str, port: int, *txt: str) -> bytes:
    full = f"{instance}.{service_type}"
    txt_raw = b"".join(bytes([len(t)]) + t.encode() for t in txt)
    return _response(
        _rr(service_type, DNS_TYPE_PTR, encode_dns_name(full)),
        _rr(full, DNS_TYPE_SRV, struct.pack("!HHH", 0, 0, port) + encode_dns_name("widget.local.")),
        _rr(full, DNS_TYPE_TXT, txt_raw),
        _rr("widget.local.", DNS_TYPE_A, socket.inet_aton(ip)),
    )


def _heard_listeners(bench, *, device_heard: bool = True):
    mdns = MDNSScanner(query_enumerated_types=True, enumerated_from=[HOST])
    ssdp = SSDPScanner(capture=True)
    amx = AMXDDPScanner()
    # Someone else on the network, so the listeners provably worked.
    mdns._process_response(_mdns_packet("10.77.0.10", "_http._tcp.local.", "Printer", 80), "10.77.0.10")
    if device_heard:
        mdns._process_response(
            _mdns_packet(HOST, VENDOR_TYPE, "Widget 3000", bench["banner"], "md=Widget 3000", "fw"),
            HOST,
        )
        mdns._process_response(
            _response(_rr(DNS_SD_META_QUERY, DNS_TYPE_PTR, encode_dns_name(VENDOR_TYPE))), HOST,
        )
        ssdp._process_response((
            "HTTP/1.1 200 OK\r\n"
            f"LOCATION: http://{HOST}:{bench['web']}/desc.xml\r\n"
            "SERVER: AcmeOS/1.0 UPnP/1.0 Widget/3000\r\n"
            f"ST: {URN}\r\n"
            f"USN: uuid:aw3-0042::{URN}\r\n\r\n"
        ).encode(), HOST)
        amx._handle_datagram(
            b"AMXB<-UUID=AW3-0042><-SDKClass=Utility><-Make=Acme><-Model=Widget3000>"
            b"<-Revision=1.2>",
            HOST,
        )
    return mdns, ssdp, amx


def _check(discovery, bench, **kwargs):
    progress: list = []
    defaults = dict(
        port_list=[bench["banner"], bench["web"], bench["secure"], bench["closed"]],
        web_ports={bench["web"]: "http", bench["secure"]: "https"},
        listeners=_heard_listeners(bench),
        listen_seconds=0.2,
        greeting_seconds=0.6,
        port_pacing_ms=1.0,
        snmp_communities=["widgets"],
        on_progress=lambda act: progress.append((act.key, act.status)),
        source_ip="",
    )
    defaults.update(kwargs)
    check = NetworkCheck(discovery, HOST, HOST, **defaults)
    return check, progress


# ---------------------------------------------------------------------------
# The whole check
# ---------------------------------------------------------------------------


async def test_the_check_records_everything_the_device_says(discovery, bench):
    check, progress = _check(discovery, bench)
    fp = await check.run()

    # Ports: refused kept apart from open.
    assert fp.port_states[bench["banner"]] == PORT_OPEN
    assert fp.port_states[bench["closed"]] == PORT_REFUSED

    # What each port says, every byte (the telnet negotiation included).
    greeting = fp.greetings[bench["banner"]]
    assert greeting.data.startswith(b"\xff\xfb\x01")
    assert b"ACME-WIDGET 3000" in greeting.data
    assert fp.greetings[bench["web"]].data == b""

    # The web page: status, realm, title (whitespace collapsed).
    page = fp.web[bench["web"]]
    assert page.status == 401
    assert 'realm="Widget 3000"' in page.header("www-authenticate")
    assert page.title() == "Widget 3000 Login"
    # The TLS page and its certificate.
    secure = fp.web[bench["secure"]]
    assert secure.title() == "Secure Widget"
    cert = fp.certificates[bench["secure"]]
    assert cert["subject"] == "CN=openavc-sim"
    assert cert["self_signed"] is True
    assert len(cert["sha256"]) == 64

    # Announcements, from the listeners' own parsers.
    assert [s["service_type"] for s in fp.mdns["services"]] == [VENDOR_TYPE.rstrip(".")]
    assert fp.mdns["services"][0]["txt"] == {"md": "Widget 3000", "fw": ""}
    assert fp.mdns["enumerated_types"] == [VENDOR_TYPE]
    assert URN in fp.ssdp["device_types"]
    assert "<modelName>Widget 3000</modelName>" in fp.ssdp["description_xml"]
    assert fp.amx_ddp["make"] == "Acme"
    assert fp.listeners["mdns"] == {"running": True, "error": "", "other_devices_heard": 1}

    # SNMP answered on the second community, and the report does not name it.
    assert fp.snmp["answered"] is True
    assert fp.snmp["community"] == "community 2 of 2"
    assert fp.snmp["values"]["sysDescr"] == "Acme Widget 3000"
    assert fp.snmp["pen"] == 99999
    assert "widgets" not in json.dumps(fp.to_dict())

    # Driver probes: the match and the miss both kept.
    by_id = {o.probe_id: o for o in fp.probes}
    widget = by_id["custom_acme_widget_tcp"]
    assert widget.matched and b"ACME-WIDGET" in widget.reply
    gadget = by_id["custom_acme_gadget_tcp"]
    assert not gadget.matched and gadget.sent == b"HELLO\r\n"

    # The verdict a scan would reach, with every driver each signal names.
    verdict = fp.verdict
    assert verdict["state"] == "identified"
    assert verdict["identification"]["driver_id"] == "acme_widget"
    assert "acme_widget" in verdict["explanation"]["strong_drivers"]
    assert "acme_gadget" in verdict["explanation"]["drivers"]
    assert verdict["drivers"]["acme_widget"]["name"] == "Acme Widget"
    statuses = {c["kind"]: c["status"] for c in verdict["checks"]["acme_widget"]}
    assert statuses == {"mdns": "matched", "probe": "matched"}
    assert verdict["catalog"]["used"] == "fresh"
    assert verdict["catalog"]["driver_count"] == 2

    # The device record a Discovery card would show.
    assert fp.device.manufacturer == "Acme"
    assert fp.device.serial_number == "AW3-0042"

    # Limits that always hold are said.
    ids = {limit.id for limit in fp.limits}
    assert {"udp_ports", "ipv6", "ports_standard"} <= ids

    # Every activity finished, and the record is JSON all the way down.
    finished = {key for key, status in progress if status in (DONE, SKIPPED)}
    assert finished == set(ACTIVITIES)
    json.dumps(fp.to_dict())


async def test_a_silent_network_is_a_limit_not_a_finding(discovery, bench):
    """No announcement from anyone means the listeners may have been blocked;
    the report must not say the device does not announce."""
    mdns, ssdp, amx = MDNSScanner(), SSDPScanner(capture=True), AMXDDPScanner()
    check, _ = _check(discovery, bench, listeners=(mdns, ssdp, amx))
    fp = await check.run()
    ids = {limit.id for limit in fp.limits}
    assert {"mdns_silent", "ssdp_silent", "amx_ddp_silent"} <= ids
    assert fp.mdns is None


async def test_a_listener_that_never_ran_says_so(discovery, bench):
    mdns = MDNSScanner()
    mdns.env_error = "mDNS listener unavailable: address in use"
    check, _ = _check(discovery, bench, listeners=(mdns, SSDPScanner(), AMXDDPScanner()))
    fp = await check.run()
    text = {limit.id: limit.text for limit in fp.limits}
    assert "address in use" in text["mdns_listener"]
    assert fp.listeners["mdns"]["running"] is False


async def test_an_unreachable_catalog_is_recorded(monkeypatch, discovery, bench):
    async def offline(_path):
        return None, "network unreachable"

    monkeypatch.setattr(ci, "_fetch_raw_with_retry", offline)
    check, _ = _check(discovery, bench)
    fp = await check.run()
    assert fp.verdict["catalog"]["used"] == "none"
    assert fp.verdict["catalog"]["error"] == "network unreachable"
    assert "catalog_unreachable" in {limit.id for limit in fp.limits}
    # With no catalog nothing identifies the device, but it still answered.
    assert fp.verdict["state"] == "unknown"


async def test_the_extended_check_walks_snmp(discovery, bench):
    check, _ = _check(discovery, bench, extended=True)
    fp = await check.run()
    oids = [row["oid"] for row in fp.snmp["walk"]]
    assert "1.3.6.1.2.1.1.1.0" in oids
    assert fp.snmp["walk_complete"] is True
    assert "ports_standard" not in {limit.id for limit in fp.limits}


async def test_nothing_at_the_address_says_so(monkeypatch, discovery):
    """No ping, no port answering even to refuse, no announcement, no SNMP:
    the verdict says nothing answered, not "unknown device". The network is
    stubbed, because an address guaranteed dead on every test machine does
    not exist (some networks answer even the documentation range)."""
    from openavc.discovery import icmp

    async def silent_ping(*_a, **_k):
        return icmp.RESULT_TIMEOUT

    async def filtered(_ip, ports, **_k):
        return {p: "filtered" for p in ports}

    async def no_netbios(*_a, **_k):
        return None

    monkeypatch.setattr(fpmod.icmp, "ping_host", silent_ping)
    monkeypatch.setattr(fpmod, "scan_host_port_states", filtered)
    monkeypatch.setattr(fpmod, "netbios_query", no_netbios)
    check = NetworkCheck(
        discovery, "192.0.2.1", "192.0.2.1",
        port_list=[9, 23], web_ports={},
        listeners=(MDNSScanner(), SSDPScanner(), AMXDDPScanner()),
        listen_seconds=0.0, port_pacing_ms=1.0, source_ip="",
    )
    fp = await check.run()
    assert fp.verdict["state"] == "nothing"
    assert not fp.answered()
    assert "ports_all_filtered" in {limit.id for limit in fp.limits}


async def test_the_listeners_live_as_long_as_the_session(monkeypatch):
    """They open when the session does and close at its teardown, whichever
    way it ends."""
    from openavc.audit.footprint import open_for_session
    from openavc.audit.session import AuditManager, AuditTarget

    opened: list[str] = []
    closed: list[str] = []

    class FakeListener:
        env_error = None
        results: dict = {}

        def __init__(self, *args, **kwargs):
            self.kind = type(self).__name__

        async def start(self, duration=0.0):
            opened.append(self.kind)
            await asyncio.sleep(3600)

        async def scan(self, timeout=0.0, fetch_descriptions=True):
            opened.append(self.kind)
            await asyncio.sleep(3600)

        async def stop(self):
            closed.append(self.kind)

    class FakeMDNS(FakeListener):
        pass

    class FakeSSDP(FakeListener):
        pass

    class FakeAMX(FakeListener):
        pass

    monkeypatch.setattr(fpmod, "MDNSScanner", FakeMDNS)
    monkeypatch.setattr(fpmod, "SSDPScanner", FakeSSDP)
    monkeypatch.setattr(fpmod, "AMXDDPScanner", FakeAMX)
    engine = DiscoveryEngine()
    manager = AuditManager(None, engine)
    session = await manager.start(AuditTarget(address=HOST, ip=HOST))
    check = await open_for_session(session, engine)
    await asyncio.sleep(0.05)
    assert session.check is check
    assert sorted(opened) == ["FakeAMX", "FakeMDNS", "FakeSSDP"]
    await manager.finish(session.id)
    assert sorted(closed) == ["FakeAMX", "FakeMDNS", "FakeSSDP"]
    assert all(t.done() for t in check._listener_tasks)
