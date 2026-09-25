"""Scanner capture mode and the one certificate reader.

What a scanner parses and then drops is what a single-device check needs:
every NetBIOS name and the MAC, the SNMP hardware revision, uptime and
interface MACs, how each port answered (open, refused, filtered), every SSDP
header set and the whole description document, and a TLS certificate's
subject, issuer, SANs, validity, serial and fingerprint. What comes free with
a normal scan is always kept; what costs extra traffic or memory is behind a
``capture`` option that normal scans leave off.
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
import struct
from unittest.mock import patch

from openavc.discovery import port_scanner
from openavc.discovery.certificates import certificate_details, certificate_match_text
from openavc.discovery.hints import parse_driver_discovery
from openavc.discovery.network_scanner import _parse_nbstat_response
from openavc.discovery.probe_runner import observe_tcp_active_probe
from openavc.discovery.snmp_scanner import (
    IF_PHYS_ADDRESS,
    OIDS,
    SYS_UPTIME,
    SNMPInfo,
    SNMPScanner,
)
from openavc.discovery.ssdp_scanner import SSDPResult, SSDPScanner, _http_fetch
from openavc.transport.snmp_codec import build_snmp_response, oid_sort_key, parse_snmp_request


# ---------------------------------------------------------------- certificates

class TestCertificateReader:
    def _der(self, tmp_path):
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding
        from openavc.tls import generate_self_signed

        certs = generate_self_signed(tmp_path, hostnames=["widget-3000"], ips=["127.0.0.1"])
        with open(certs.cert_path, "rb") as fh:
            cert = x509.load_pem_x509_certificate(fh.read())
        return cert.public_bytes(Encoding.DER), certs

    def test_details(self, tmp_path):
        der, _ = self._der(tmp_path)
        info = certificate_details(der)
        assert "widget-3000" in info["subject"]
        assert info["self_signed"] is (info["subject"] == info["issuer"])
        assert info["san_dns"] == ["widget-3000"]
        assert info["san_ip"] == ["127.0.0.1"]
        assert info["sha256"] == hashlib.sha256(der).hexdigest()
        assert info["serial"] and info["not_before"] < info["not_after"]

    def test_match_text_keeps_the_driver_contract_format(self, tmp_path):
        der, _ = self._der(tmp_path)
        info = certificate_details(der)
        assert certificate_match_text(info) == f"{info['subject']} SAN:widget-3000"
        assert certificate_match_text(None) == ""

    def test_unparseable_is_none(self):
        assert certificate_details(b"not a certificate") is None

    async def test_tls_probe_observation_carries_the_details(self, tmp_path):
        _, certs = self._der(tmp_path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certs.cert_path, certs.key_path)

        async def handle(reader, writer):
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
        port = server.sockets[0].getsockname()[1]
        spec = parse_driver_discovery({
            "id": "acme_widget", "name": "Acme Widget", "manufacturer": "Acme",
            "category": "audio", "transport": "tcp",
            "discovery": {"tcp_probe": {"port": port, "tls": True,
                                        "cert_subject": "widget-3000", "timeout_ms": 2000}},
        }).tcp_probe
        async with server:
            obs = await observe_tcp_active_probe(spec, target="127.0.0.1", source_ip="")
        assert obs.matched is True
        assert obs.certificate["san_ip"] == ["127.0.0.1"]
        assert obs.cert_subject == certificate_match_text(obs.certificate)


# ---------------------------------------------------------------- NetBIOS

def _nbstat(names: list[tuple[str, int, bool]], mac: bytes) -> bytes:
    body = bytes([len(names)])
    for name, suffix, group in names:
        body += name.encode().ljust(15) + bytes([suffix]) + struct.pack(">H", 0x8400 if group else 0x0400)
    return b"\x00" * 56 + body + mac + b"\x00" * 40


class TestNetbiosNames:
    def test_every_name_and_the_mac(self):
        parsed = _parse_nbstat_response(_nbstat(
            [("WIDGET", 0x00, False), ("ACMEGROUP", 0x00, True), ("WIDGET", 0x20, False)],
            bytes.fromhex("0011223344ab"),
        ))
        assert parsed["hostname"] == "WIDGET"
        assert parsed["workgroup"] == "ACMEGROUP"
        assert parsed["names"] == [
            {"name": "WIDGET", "suffix": "00", "group": False},
            {"name": "ACMEGROUP", "suffix": "00", "group": True},
            {"name": "WIDGET", "suffix": "20", "group": False},
        ]
        assert parsed["mac"] == "00:11:22:33:44:ab"

    def test_all_zero_mac_is_left_out(self):
        parsed = _parse_nbstat_response(_nbstat([("WIDGET", 0x00, False)], b"\x00" * 6))
        assert "mac" not in parsed


# ---------------------------------------------------------------- ports

class TestPortStates:
    async def test_open_refused_filtered_error(self):
        async def fake_open(ip, port, local_addr=None):
            if port == 1:
                raise ConnectionRefusedError()
            if port == 2:
                await asyncio.sleep(5)
            if port == 3:
                raise OSError("network unreachable")

            class W:
                def close(self):
                    pass

                async def wait_closed(self):
                    pass
            return None, W()

        with patch.object(port_scanner.asyncio, "open_connection", fake_open):
            states = await port_scanner.scan_host_port_states(
                "10.77.0.9", [4, 1, 2, 3], timeout=0.2, stagger_ms=0,
            )
            open_only = await port_scanner.scan_host_ports(
                "10.77.0.9", [4, 1, 2, 3], timeout=0.2, stagger_ms=0,
            )
        assert states == {1: "refused", 2: "filtered", 3: "error", 4: "open"}
        assert open_only == [4]


# ---------------------------------------------------------------- SNMP

MIB = {
    OIDS["sysDescr"]: ("string", "Acme Widget 3000"),
    OIDS["sysName"]: ("string", "widget"),
    SYS_UPTIME: ("timeticks", 123456),
    f"{IF_PHYS_ADDRESS}.1": ("string", b""),                            # loopback
    f"{IF_PHYS_ADDRESS}.2": ("string", bytes.fromhex("0011223344ab")),
    f"{IF_PHYS_ADDRESS}.3": ("string", bytes.fromhex("00112233fffe")),  # bytes past 0x7f survive
    "1.3.6.1.2.1.2.2.1.7.1": ("integer", 1),                            # the next column
}


def _fake_agent(sent: list[bytes]):
    ordered = sorted(MIB, key=oid_sort_key)

    async def query(self, ip, packet, timeout, request_id):
        sent.append(packet)
        req = parse_snmp_request(packet)
        varbinds = []
        for vb in req.varbinds:
            if req.is_get:
                type_name, value = MIB.get(vb.oid, ("noSuchObject", None))
                varbinds.append((vb.oid, type_name, value))
            else:
                later = [o for o in ordered if oid_sort_key(o) > oid_sort_key(vb.oid)]
                if not later:
                    varbinds.append((vb.oid, "endOfMibView", None))
                else:
                    varbinds.append((later[0], *MIB[later[0]]))
        return build_snmp_response(req.request_id, req.community, varbinds)

    return query


class TestSnmpCapture:
    async def test_capture_reads_uptime_and_interface_macs(self):
        sent: list[bytes] = []
        with patch.object(SNMPScanner, "_udp_query", _fake_agent(sent)):
            info = await SNMPScanner(capture=True).query_device("10.77.0.9")
        assert info.sys_uptime == "123456"
        assert info.if_phys_addresses == {"2": "00:11:22:33:44:ab", "3": "00:11:22:33:ff:fe"}
        assert info.to_dict()["sysUpTime"] == "123456"

    async def test_normal_scan_sends_one_get_and_no_walk(self):
        sent: list[bytes] = []
        with patch.object(SNMPScanner, "_udp_query", _fake_agent(sent)):
            info = await SNMPScanner().query_device("10.77.0.9")
        assert len(sent) == 1
        assert SYS_UPTIME not in [vb.oid for vb in parse_snmp_request(sent[0]).varbinds]
        assert info.sys_uptime == "" and info.if_phys_addresses is None

    def test_hardware_and_firmware_revision_are_kept(self):
        info = SNMPInfo(sys_descr="x", entity_hardware_rev="B", entity_firmware_rev="2.1")
        assert info.to_dict()["entPhysicalHardwareRev"] == "B"
        assert info.to_dict()["entPhysicalFirmwareRev"] == "2.1"


# ---------------------------------------------------------------- SSDP

NOTIFY = (
    "NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nNT: urn:acme-com:device:Widget:1\r\n"
    "NTS: ssdp:alive\r\nUSN: uuid:abc::urn:acme-com:device:Widget:1\r\n"
    "LOCATION: http://10.77.0.40:49152/desc.xml\r\nX-ACME-BOOTID: 7\r\n\r\n"
).encode()


class TestSsdpCapture:
    def test_header_sets_kept_only_in_capture(self):
        normal, capture = SSDPScanner(), SSDPScanner(capture=True)
        for scanner in (normal, capture):
            scanner._process_response(NOTIFY, "10.77.0.40")
            scanner._process_response(NOTIFY, "10.77.0.40")
        assert normal.results["10.77.0.40"].raw_headers == []
        kept = capture.results["10.77.0.40"].raw_headers
        assert len(kept) == 2 and kept[0]["x-acme-bootid"] == "7"

    async def test_capture_fetches_the_whole_document(self):
        body = "<root>" + "x" * 40000 + "</root>"

        async def handle(reader, writer):
            await reader.read(1024)
            writer.write(b"HTTP/1.0 200 OK\r\nServer: Acme/1.0\r\n\r\n")
            await writer.drain()
            for i in range(0, len(body), 8000):
                writer.write(body[i:i + 8000].encode())
                await writer.drain()
                await asyncio.sleep(0.02)
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            head, got = await _http_fetch(
                f"http://127.0.0.1:{port}/desc.xml", max_bytes=256 * 1024, read_to_end=True,
            )
            scanner = SSDPScanner(capture=True)
            result = SSDPResult(ip="127.0.0.1", location=f"http://127.0.0.1:{port}/desc.xml")
            await scanner._fetch_single_description(result)
        assert got == body
        assert head.startswith("HTTP/1.0 200 OK") and "Server: Acme/1.0" in head
        assert result.description_xml == body
        assert "Server: Acme/1.0" in result.description_head
