"""Tests for passive discovery: mDNS + SSDP (Chunk 4)."""

import asyncio
import socket
import struct
import pytest
from unittest.mock import patch, AsyncMock

from server.discovery.mdns_scanner import (
    MDNSScanner,
    MDNSResult,
    encode_dns_name,
    decode_dns_name,
    build_dns_query,
    parse_dns_packet,
    DNS_TYPE_A,
    DNS_TYPE_PTR,
    DNS_TYPE_SRV,
    DNS_TYPE_TXT,
    BASELINE_SERVICE_TYPES,
    DNS_SD_META_QUERY,
    MAX_MDNS_SOURCES,
    MAX_PENDING_ENTRIES,
    MAX_HOSTNAME_ENTRIES,
    MAX_UNKNOWN_SERVICE_TYPES,
    _parse_txt_rdata,
    _extract_instance_name,
)
from server.discovery.amx_ddp_scanner import AMXDDPScanner
from server.discovery.ssdp_scanner import (
    SSDPScanner,
    SSDPResult,
    parse_ssdp_response,
    _extract_port_from_url,
    _location_host_is_sender,
    _parse_upnp_xml,
    _st_to_category,
    M_SEARCH_TEMPLATE,
    MAX_DESCRIPTION_FETCHES,
    MAX_SSDP_SOURCES,
    SEARCH_TARGETS,
    SSDP_ADDR,
    SSDP_PORT,
)
from server.discovery.result import (
    DiscoveredDevice,
    merge_device_info,
)
from server.discovery.engine import DiscoveryEngine


# ============================================================
# DNS Wire Format Tests
# ============================================================


class TestEncodeDNSName:
    def test_simple_name(self):
        result = encode_dns_name("example.local.")
        assert result == b"\x07example\x05local\x00"

    def test_strips_trailing_dot(self):
        result = encode_dns_name("test.local.")
        result2 = encode_dns_name("test.local")
        assert result == result2

    def test_service_type(self):
        result = encode_dns_name("_http._tcp.local.")
        # \x05_http\x04_tcp\x05local\x00
        assert result[0] == 5  # length of "_http"
        assert result[1:6] == b"_http"
        assert result[6] == 4  # length of "_tcp"
        assert result[7:11] == b"_tcp"
        assert result[-1] == 0  # root label

    def test_single_label(self):
        result = encode_dns_name("localhost")
        assert result == b"\x09localhost\x00"

    def test_empty_string(self):
        result = encode_dns_name("")
        # Empty label (0 byte for empty split) + root label
        assert result == b"\x00\x00"


class TestDecodeDNSName:
    def test_simple_name(self):
        data = b"\x07example\x05local\x00"
        name, offset = decode_dns_name(data, 0)
        assert name == "example.local"
        assert offset == len(data)

    def test_with_offset(self):
        # Some prefix bytes, then the name
        data = b"\xff\xff\x07example\x05local\x00"
        name, offset = decode_dns_name(data, 2)
        assert name == "example.local"

    def test_compression_pointer(self):
        # Name at offset 0: "local"
        # Then a compressed name at offset 7 pointing back to 0
        data = b"\x05local\x00\x04test\xc0\x00"
        name, offset = decode_dns_name(data, 7)
        assert name == "test.local"
        assert offset == 14  # past the compression pointer

    def test_empty_name(self):
        data = b"\x00"
        name, offset = decode_dns_name(data, 0)
        assert name == ""

    def test_roundtrip(self):
        original = "mydevice._http._tcp.local."
        encoded = encode_dns_name(original)
        decoded, _ = decode_dns_name(encoded, 0)
        # Decoded won't have trailing dot
        assert decoded == original.rstrip(".")


class TestBuildDNSQuery:
    def test_builds_valid_packet(self):
        packet = build_dns_query("_http._tcp.local.", DNS_TYPE_PTR)
        # Header: 12 bytes
        assert len(packet) >= 12

        # Parse header
        tx_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            "!HHHHHH", packet[:12]
        )
        assert tx_id == 0  # mDNS uses 0
        assert flags == 0  # Standard query
        assert qdcount == 1  # One question
        assert ancount == 0
        assert nscount == 0
        assert arcount == 0

    def test_contains_query_name(self):
        packet = build_dns_query("_http._tcp.local.", DNS_TYPE_PTR)
        # The query name should be in the packet
        encoded_name = encode_dns_name("_http._tcp.local.")
        assert encoded_name in packet

    def test_query_type_in_packet(self):
        packet = build_dns_query("_http._tcp.local.", DNS_TYPE_PTR)
        # Last 4 bytes should be QTYPE + QCLASS
        qtype, qclass = struct.unpack("!HH", packet[-4:])
        assert qtype == DNS_TYPE_PTR
        assert qclass == 1  # IN class

    def test_a_record_query(self):
        packet = build_dns_query("myhost.local.", DNS_TYPE_A)
        qtype, qclass = struct.unpack("!HH", packet[-4:])
        assert qtype == DNS_TYPE_A

    def test_srv_record_query(self):
        packet = build_dns_query("_http._tcp.local.", DNS_TYPE_SRV)
        qtype, qclass = struct.unpack("!HH", packet[-4:])
        assert qtype == DNS_TYPE_SRV


class TestParseDNSPacket:
    def _build_response_with_a_record(self, name: str, ip: str) -> bytes:
        """Build a minimal DNS response with one A record."""
        # Header: ID=0, flags=0x8400 (response, authoritative), 0 questions, 1 answer
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        # Answer: name + type A + class IN + TTL 120 + rdlength 4 + IP
        name_bytes = encode_dns_name(name)
        ip_bytes = socket.inet_aton(ip)
        answer = name_bytes + struct.pack("!HHIH", DNS_TYPE_A, 1, 120, 4) + ip_bytes
        return header + answer

    def _build_response_with_ptr(self, name: str, target: str) -> bytes:
        """Build a DNS response with one PTR record."""
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        name_bytes = encode_dns_name(name)
        target_bytes = encode_dns_name(target)
        answer = (
            name_bytes
            + struct.pack("!HHIH", DNS_TYPE_PTR, 1, 120, len(target_bytes))
            + target_bytes
        )
        return header + answer

    def test_parse_a_record(self):
        data = self._build_response_with_a_record("myhost.local.", "192.168.1.50")
        _, records = parse_dns_packet(data)
        assert len(records) == 1
        assert records[0].rtype == DNS_TYPE_A
        assert records[0].ip == "192.168.1.50"
        assert records[0].name == "myhost.local"

    def test_parse_ptr_record(self):
        data = self._build_response_with_ptr(
            "_http._tcp.local.",
            "My Device._http._tcp.local."
        )
        _, records = parse_dns_packet(data)
        assert len(records) == 1
        assert records[0].rtype == DNS_TYPE_PTR
        assert records[0].target == "My Device._http._tcp.local"

    def test_parse_srv_record(self):
        """Build and parse a SRV record."""
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        name = encode_dns_name("My Device._http._tcp.local.")
        target_name = encode_dns_name("mydevice.local.")
        # SRV rdata: priority(2) + weight(2) + port(2) + target
        srv_rdata = struct.pack("!HHH", 0, 0, 8080) + target_name
        answer = (
            name
            + struct.pack("!HHIH", DNS_TYPE_SRV, 1, 120, len(srv_rdata))
            + srv_rdata
        )
        data = header + answer
        _, records = parse_dns_packet(data)
        assert len(records) == 1
        assert records[0].rtype == DNS_TYPE_SRV
        assert records[0].port == 8080
        assert records[0].priority == 0
        assert records[0].weight == 0
        assert "mydevice" in records[0].target

    def test_parse_txt_record(self):
        """Build and parse a TXT record with key=value pairs."""
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        name = encode_dns_name("My Device._http._tcp.local.")

        # TXT rdata: length-prefixed strings
        txt1 = b"manufacturer=Samsung"
        txt2 = b"model=UE55"
        txt_rdata = bytes([len(txt1)]) + txt1 + bytes([len(txt2)]) + txt2

        answer = (
            name
            + struct.pack("!HHIH", DNS_TYPE_TXT, 1, 120, len(txt_rdata))
            + txt_rdata
        )
        data = header + answer
        _, records = parse_dns_packet(data)
        assert len(records) == 1
        assert records[0].rtype == DNS_TYPE_TXT
        assert records[0].txt["manufacturer"] == "Samsung"
        assert records[0].txt["model"] == "UE55"

    def test_parse_empty_packet(self):
        _, records = parse_dns_packet(b"")
        assert records == []

    def test_parse_too_short(self):
        _, records = parse_dns_packet(b"\x00\x00")
        assert records == []

    def test_parse_multiple_records(self):
        """Response with A + PTR records."""
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 2, 0, 0)

        # A record
        a_name = encode_dns_name("myhost.local.")
        a_rdata = socket.inet_aton("192.168.1.50")
        a_answer = a_name + struct.pack("!HHIH", DNS_TYPE_A, 1, 120, 4) + a_rdata

        # PTR record
        ptr_name = encode_dns_name("_http._tcp.local.")
        ptr_target = encode_dns_name("My Device._http._tcp.local.")
        ptr_answer = (
            ptr_name
            + struct.pack("!HHIH", DNS_TYPE_PTR, 1, 120, len(ptr_target))
            + ptr_target
        )

        data = header + a_answer + ptr_answer
        _, records = parse_dns_packet(data)
        assert len(records) == 2


class TestRdataNameBoundary:
    """PTR/SRV target names must stay inside the record's rdata window.

    A short or forged rdlength must not let the target decode bleed into the
    following record's name (memory-safe but wrong-result: a spoofed hostname
    flows into pending state, the dedup key, and the catalog-growth UI).
    """

    def test_srv_rdlength_six_yields_no_target(self):
        # SRV with rdlength == 6 carries priority/weight/port but NO target.
        # The next record's owner name must not be attributed as the target.
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 2, 0, 0)
        srv_name = encode_dns_name("dev._svc._tcp.local.")
        srv_rdata = struct.pack("!HHH", 0, 0, 8080)  # exactly 6 bytes, no target
        srv_answer = (
            srv_name
            + struct.pack("!HHIH", DNS_TYPE_SRV, 1, 120, len(srv_rdata))
            + srv_rdata
        )
        # A record immediately after — its name is the bleed target.
        a_name = encode_dns_name("secret.attacker.local.")
        a_answer = (
            a_name
            + struct.pack("!HHIH", DNS_TYPE_A, 1, 120, 4)
            + socket.inet_aton("10.0.0.9")
        )
        data = header + srv_answer + a_answer
        _, records = parse_dns_packet(data)
        srv = records[0]
        assert srv.rtype == DNS_TYPE_SRV
        assert srv.port == 8080
        assert not srv.target  # no bleed into "secret.attacker.local"

    def test_ptr_target_clamped_to_rdlength(self):
        # PTR rdata declares only a partial label (no terminating null inside
        # the window); trailing bytes continue the name. Pre-clamp the decoder
        # would read past rdlength and pick up "attack".
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        ptr_name = encode_dns_name("_svc._tcp.local.")
        in_window = b"\x04host"       # label "host", no null terminator
        trailer = b"\x06attack\x00"   # would extend the name if unclamped
        ptr_answer = (
            ptr_name
            + struct.pack("!HHIH", DNS_TYPE_PTR, 1, 120, len(in_window))
            + in_window
            + trailer
        )
        data = header + ptr_answer
        _, records = parse_dns_packet(data)
        assert records[0].rtype == DNS_TYPE_PTR
        assert "attack" not in (records[0].target or "")

    def test_compressed_target_inside_rdata_still_resolves(self):
        # A legitimate compression pointer inside the rdata window must still
        # follow to the earlier name — the clamp only bounds the name's own
        # bytes, not pointer targets.
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        # Owner name for the PTR record, placed at offset 12 (right after the
        # header) so a pointer can reference the service-type suffix.
        ptr_name = encode_dns_name("_svc._tcp.local.")  # starts at offset 12
        # rdata: "Acme Widget" label + pointer to "_svc._tcp.local." at 12.
        rdata = b"\x0bAcme Widget" + struct.pack("!H", 0xC000 | 12)
        ptr_answer = (
            ptr_name
            + struct.pack("!HHIH", DNS_TYPE_PTR, 1, 120, len(rdata))
            + rdata
        )
        data = header + ptr_answer
        _, records = parse_dns_packet(data)
        assert records[0].target == "Acme Widget._svc._tcp.local"


def _scanner_factories():
    """Every scanner whose listen loop parks in a blocking recvfrom.

    All three run `recvfrom` in the default executor under a 0.5 s socket
    timeout, so all three need shutdown-before-close for the same reason.
    Parametrized together so a fourth scanner added later has an obvious
    place to land — and so the trio can't drift apart again (mdns was fixed
    alone first, leaving these two behind for months).
    """
    return [
        pytest.param(MDNSScanner, id="mdns"),
        pytest.param(SSDPScanner, id="ssdp"),
        pytest.param(AMXDDPScanner, id="amx_ddp"),
    ]


class TestCloseSocketShutdown:
    """_close_socket shuts the socket down before closing so a peer thread
    blocked in recvfrom is unblocked promptly (close alone does not wake it)."""

    @pytest.mark.parametrize("scanner_cls", _scanner_factories())
    def test_shutdown_precedes_close(self, scanner_cls):
        calls: list[tuple] = []

        class _RecordingSocket:
            def shutdown(self, how):
                calls.append(("shutdown", how))

            def close(self):
                calls.append(("close",))

        scanner = scanner_cls()
        scanner._sock = _RecordingSocket()
        scanner._close_socket()
        assert calls == [("shutdown", socket.SHUT_RDWR), ("close",)]
        assert scanner._sock is None

    @pytest.mark.parametrize("scanner_cls", _scanner_factories())
    def test_shutdown_error_on_unconnected_is_ignored(self, scanner_cls):
        closed: list[bool] = []

        class _UnconnectedSocket:
            def shutdown(self, how):
                raise OSError("not connected")

            def close(self):
                closed.append(True)

        scanner = scanner_cls()
        scanner._sock = _UnconnectedSocket()
        scanner._close_socket()  # must not raise
        assert closed == [True]
        assert scanner._sock is None


class TestParseTxtRdata:
    def test_key_value_pairs(self):
        rdata = bytes([11]) + b"model=UE55" + b"\x00" + bytes([7]) + b"fw=1.23"
        # Wait — the format is length prefix for each string. Let me rebuild:
        s1 = b"model=UE55"
        s2 = b"fw=1.23"
        rdata = bytes([len(s1)]) + s1 + bytes([len(s2)]) + s2
        result = _parse_txt_rdata(rdata)
        assert result["model"] == "UE55"
        assert result["fw"] == "1.23"

    def test_key_without_value(self):
        s = b"flagonly"
        rdata = bytes([len(s)]) + s
        result = _parse_txt_rdata(rdata)
        assert "flagonly" in result
        assert result["flagonly"] == ""

    def test_empty_rdata(self):
        result = _parse_txt_rdata(b"")
        assert result == {}

    def test_single_entry(self):
        s = b"manufacturer=Shure"
        rdata = bytes([len(s)]) + s
        result = _parse_txt_rdata(rdata)
        assert result["manufacturer"] == "Shure"


# ============================================================
# mDNS Result Tests
# ============================================================


class TestMDNSResult:
    def test_to_device_info_basic(self):
        # Core records the raw service type as observed and the
        # instance name as device_name. Protocol/category labels come
        # from the matched driver later in the pipeline, not from a
        # core dispatch table.
        result = MDNSResult(
            ip="192.168.1.50",
            hostname="projector",
            port=4352,
            service_type="_example._tcp.local.",
            instance_name="Acme Foo Box",
        )
        info = result.to_device_info()
        assert info["hostname"] == "projector"
        assert info["device_name"] == "Acme Foo Box"
        assert "_example._tcp.local." in info["mdns_services"]
        # Core no longer derives protocol or category from the service
        # type; those are populated by the driver match in finalize.
        assert "protocols" not in info
        assert "category" not in info

    def test_to_device_info_with_txt_records(self):
        result = MDNSResult(
            ip="192.168.1.60",
            service_type="_http._tcp.local.",
            txt_records={
                "manufacturer": "Acme Display Co",
                "model": "Foo-55X",
                "fw": "1.4.0.5",
                "sn": "SER12345",
            },
        )
        info = result.to_device_info()
        assert info["manufacturer"] == "Acme Display Co"
        assert info["model"] == "Foo-55X"
        assert info["firmware"] == "1.4.0.5"
        assert info["serial_number"] == "SER12345"

    def test_to_device_info_short_txt_keys(self):
        """Test shorthand TXT keys (mf, md, fw, sn)."""
        result = MDNSResult(
            ip="192.168.1.70",
            txt_records={"mf": "Acme Display Co", "md": "OLED55"},
        )
        info = result.to_device_info()
        assert info["manufacturer"] == "Acme Display Co"
        assert info["model"] == "OLED55"

    def test_to_device_info_minimal(self):
        result = MDNSResult(ip="192.168.1.80")
        info = result.to_device_info()
        assert info == {}

    def test_port_in_open_ports(self):
        result = MDNSResult(ip="192.168.1.90", port=4352)
        info = result.to_device_info()
        assert 4352 in info.get("open_ports", [])

    def test_standard_port_not_in_open_ports(self):
        """Port 80 and 443 are not added to open_ports (too generic)."""
        result = MDNSResult(ip="192.168.1.91", port=80)
        info = result.to_device_info()
        assert "open_ports" not in info


class TestMdnsScannerBaseline:
    """Drivers contribute their own mDNS service types. Core only ships
    a small generic baseline plus the DNS-SD meta-query."""

    def test_baseline_is_protocol_class_only(self):
        # Baseline carries only generic / consumer protocol service
        # types — none of the AV-vendor service types are hard-coded.
        for s in BASELINE_SERVICE_TYPES:
            assert s.endswith(".local.")
            # Bare protocol-class names — no vendor-specific drivers.
            assert "." in s

    def test_driver_supplied_service_types_passed_through(self):
        # Drivers contribute service types via discovery.mdns; the
        # scanner queries whatever it receives.
        scanner = MDNSScanner(service_types=[
            "_example._udp.local.",
            "_other._tcp.local.",
        ])
        assert "_example._udp.local." in scanner._service_types
        assert "_other._tcp.local." in scanner._service_types

    def test_dns_sd_meta_query_always_included(self):
        # Always added so unknown service types surface for catalog
        # growth, regardless of what drivers declare.
        scanner = MDNSScanner(service_types=[])
        assert DNS_SD_META_QUERY in scanner._service_types
        scanner2 = MDNSScanner(service_types=["_foo._tcp.local."])
        assert DNS_SD_META_QUERY in scanner2._service_types


class TestExtractInstanceName:
    def test_standard_extraction(self):
        result = _extract_instance_name(
            "NEC PA1004UL._pjlink._tcp.local.",
            "_pjlink._tcp.local."
        )
        assert result == "NEC PA1004UL"

    def test_with_dots_in_name(self):
        result = _extract_instance_name(
            "My.Device._http._tcp.local.",
            "_http._tcp.local."
        )
        assert result == "My.Device"

    def test_same_as_service_type(self):
        result = _extract_instance_name(
            "_http._tcp.local.",
            "_http._tcp.local."
        )
        assert result is None

    def test_no_match(self):
        result = _extract_instance_name(
            "something_else.local.",
            "_http._tcp.local."
        )
        assert result == "something_else.local"


# ============================================================
# mDNS Scanner Tests
# ============================================================


class TestMDNSScanner:
    def test_init(self):
        scanner = MDNSScanner()
        assert scanner._running is False
        assert scanner._results == {}

    def test_baseline_service_types_defined(self):
        """The baseline still carries the consumer / generic protocols
        the engine includes alongside driver-declared types."""
        assert len(BASELINE_SERVICE_TYPES) >= 4
        assert "_airplay._tcp.local." in BASELINE_SERVICE_TYPES
        assert "_http._tcp.local." in BASELINE_SERVICE_TYPES

    @pytest.mark.asyncio
    async def test_start_handles_socket_error(self):
        """Should return empty results if socket creation fails."""
        scanner = MDNSScanner()
        with patch(
            "server.discovery.mdns_scanner._create_mdns_socket",
            side_effect=OSError("Permission denied"),
        ):
            results = await scanner.start(duration=0.1)
        assert results == {}

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self):
        scanner = MDNSScanner()
        scanner._running = True
        await scanner.stop()
        assert scanner._running is False

    def test_process_response_a_record(self):
        """Process a packet with an A record."""
        scanner = MDNSScanner()

        # Build a response with A record
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        name = encode_dns_name("mydevice.local.")
        ip_bytes = socket.inet_aton("192.168.1.50")
        answer = name + struct.pack("!HHIH", DNS_TYPE_A, 1, 120, 4) + ip_bytes
        data = header + answer

        scanner._process_response(data, "192.168.1.50")
        assert "mydevice.local" in scanner._hostname_to_ip
        assert scanner._hostname_to_ip["mydevice.local"] == "192.168.1.50"

    def test_process_response_full_service(self):
        """Process a realistic mDNS response with PTR + SRV + A + TXT."""
        scanner = MDNSScanner()

        # Build a multi-record response
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 4, 0, 0)

        # 1. PTR: _pjlink._tcp.local -> NEC PA1004UL._pjlink._tcp.local
        ptr_name = encode_dns_name("_pjlink._tcp.local.")
        ptr_target = encode_dns_name("NEC PA1004UL._pjlink._tcp.local.")
        ptr_answer = (
            ptr_name
            + struct.pack("!HHIH", DNS_TYPE_PTR, 1, 120, len(ptr_target))
            + ptr_target
        )

        # 2. SRV: NEC PA1004UL._pjlink._tcp.local -> projector.local:4352
        srv_name = encode_dns_name("NEC PA1004UL._pjlink._tcp.local.")
        srv_target = encode_dns_name("projector.local.")
        srv_rdata = struct.pack("!HHH", 0, 0, 4352) + srv_target
        srv_answer = (
            srv_name
            + struct.pack("!HHIH", DNS_TYPE_SRV, 1, 120, len(srv_rdata))
            + srv_rdata
        )

        # 3. A: projector.local -> 192.168.1.72
        a_name = encode_dns_name("projector.local.")
        a_rdata = socket.inet_aton("192.168.1.72")
        a_answer = a_name + struct.pack("!HHIH", DNS_TYPE_A, 1, 120, 4) + a_rdata

        # 4. TXT: manufacturer=NEC, model=PA1004UL
        txt_name = encode_dns_name("NEC PA1004UL._pjlink._tcp.local.")
        txt_s1 = b"manufacturer=NEC"
        txt_s2 = b"model=PA1004UL"
        txt_rdata = bytes([len(txt_s1)]) + txt_s1 + bytes([len(txt_s2)]) + txt_s2
        txt_answer = (
            txt_name
            + struct.pack("!HHIH", DNS_TYPE_TXT, 1, 120, len(txt_rdata))
            + txt_rdata
        )

        data = header + ptr_answer + srv_answer + a_answer + txt_answer
        scanner._process_response(data, "192.168.1.72")

        # Should have resolved to an MDNSResult
        assert "192.168.1.72" in scanner._results
        result = scanner._results["192.168.1.72"]
        assert result.ip == "192.168.1.72"
        assert result.port == 4352
        assert result.instance_name == "NEC PA1004UL"
        # DNS name decoder strips trailing dots
        assert result.service_type == "_pjlink._tcp.local"
        assert result.txt_records.get("manufacturer") == "NEC"
        assert result.txt_records.get("model") == "PA1004UL"

    def test_process_response_skips_malformed(self):
        """Should not crash on malformed packets."""
        scanner = MDNSScanner()
        scanner._process_response(b"\x00\x00", "1.2.3.4")
        assert len(scanner._results) == 0


class TestMDNSFloodGuards:
    """The listener caps every accumulator hostile multicast can grow.

    Mirrors the AMX/SSDP distinct-source caps, with one difference: a
    SINGLE mDNS source can inflate most structures without source
    spoofing — fabricated A records mint result IPs and hostname
    mappings, fabricated instance names mint pending entries.
    """

    def _ptr_packet(self, n: int) -> bytes:
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        ptr_name = encode_dns_name("_pjlink._tcp.local.")
        ptr_target = encode_dns_name(f"Dev-{n}._pjlink._tcp.local.")
        return (
            header
            + ptr_name
            + struct.pack("!HHIH", DNS_TYPE_PTR, 1, 120, len(ptr_target))
            + ptr_target
        )

    def _a_packet(self, hostname: str, ip: str) -> bytes:
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        name = encode_dns_name(hostname)
        return (
            header
            + name
            + struct.pack("!HHIH", DNS_TYPE_A, 1, 120, 4)
            + socket.inet_aton(ip)
        )

    def _source_ip(self, n: int) -> str:
        return f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"

    def test_distinct_result_sources_capped(self):
        scanner = MDNSScanner()
        for n in range(MAX_MDNS_SOURCES + 50):
            scanner._process_response(self._ptr_packet(n), self._source_ip(n))
        assert len(scanner._results) == MAX_MDNS_SOURCES

    def test_known_source_still_updates_past_cap(self):
        scanner = MDNSScanner()
        for n in range(MAX_MDNS_SOURCES + 50):
            scanner._process_response(self._ptr_packet(n), self._source_ip(n))
        # 10.0.0.0 was the first source in; a fresh advertisement from it
        # must still be merged even though the cap has tripped.
        scanner._process_response(self._ptr_packet(9999), "10.0.0.0")
        assert scanner._results["10.0.0.0"].instance_name == "Dev-9999"

    def test_result_cap_logs_once_per_window(self, caplog):
        scanner = MDNSScanner()
        with caplog.at_level("WARNING"):
            for n in range(MAX_MDNS_SOURCES + 10):
                scanner._process_response(self._ptr_packet(n), self._source_ip(n))
        hits = [r for r in caplog.records if "distinct-source cap" in r.message]
        assert len(hits) == 1

    def test_capped_pending_entry_is_dropped_not_left_to_accumulate(self):
        scanner = MDNSScanner()
        for n in range(MAX_MDNS_SOURCES + 50):
            scanner._process_response(self._ptr_packet(n), self._source_ip(n))
        # Entries that couldn't land in results must not pile up in the
        # pending staging dict either.
        assert len(scanner._pending) == 0

    def test_pending_entries_capped(self, caplog):
        scanner = MDNSScanner()
        for i in range(MAX_PENDING_ENTRIES):
            scanner._pending[f"instance-{i}"] = {}
        with caplog.at_level("WARNING"):
            assert scanner._pending_entry("one-more") is None
            assert scanner._pending_entry("another") is None
        assert "one-more" not in scanner._pending
        # Existing keys keep updating past the cap.
        assert scanner._pending_entry("instance-0") is scanner._pending["instance-0"]
        hits = [r for r in caplog.records if "pending-record cap" in r.message]
        assert len(hits) == 1

    def test_hostname_entries_capped(self):
        scanner = MDNSScanner()
        sender = "10.0.0.1"
        for n in range(MAX_HOSTNAME_ENTRIES + 10):
            scanner._process_response(
                self._a_packet(f"host-{n}.local.", self._source_ip(n)), sender,
            )
        assert len(scanner._hostname_to_ip) == MAX_HOSTNAME_ENTRIES
        # A known hostname still updates past the cap.
        scanner._process_response(
            self._a_packet("host-0.local.", "192.168.9.9"), sender,
        )
        assert scanner._hostname_to_ip["host-0.local"] == "192.168.9.9"

    def test_unknown_service_types_capped(self):
        scanner = MDNSScanner()
        for n in range(MAX_UNKNOWN_SERVICE_TYPES + 20):
            scanner._track_unknown_service_type(f"_fake{n}._tcp.local.")
        assert len(scanner.unknown_service_types) == MAX_UNKNOWN_SERVICE_TYPES

    @pytest.mark.asyncio
    async def test_caps_reset_on_start(self):
        scanner = MDNSScanner()
        scanner._cap_warned = True
        scanner._pending_cap_warned = True
        with patch(
            "server.discovery.mdns_scanner._create_mdns_socket",
            side_effect=OSError("no socket"),
        ):
            await scanner.start(duration=0.1)
        assert scanner._cap_warned is False
        assert scanner._pending_cap_warned is False


# ============================================================
# SSDP Response Parsing Tests
# ============================================================


class TestParseSSDPResponse:
    def test_standard_response(self):
        text = (
            "HTTP/1.1 200 OK\r\n"
            "CACHE-CONTROL: max-age=1800\r\n"
            "LOCATION: http://192.168.1.50:49152/description.xml\r\n"
            "SERVER: Linux/3.0, UPnP/1.0, Samsung/1.0\r\n"
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "USN: uuid:abc123::urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "\r\n"
        )
        headers = parse_ssdp_response(text)
        assert headers is not None
        assert headers["location"] == "http://192.168.1.50:49152/description.xml"
        assert "Samsung" in headers["server"]
        assert "MediaRenderer" in headers["st"]
        assert "uuid:abc123" in headers["usn"]

    def test_notify_response(self):
        text = (
            "NOTIFY * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            "NT: upnp:rootdevice\r\n"
            "NTS: ssdp:alive\r\n"
            "LOCATION: http://192.168.1.60:80/desc.xml\r\n"
            "SERVER: Microsoft-Windows/10.0 UPnP/1.0\r\n"
            "USN: uuid:def456\r\n"
            "\r\n"
        )
        headers = parse_ssdp_response(text)
        assert headers is not None
        assert headers["location"] == "http://192.168.1.60:80/desc.xml"

    def test_rejects_m_search(self):
        """M-SEARCH requests should not be parsed as responses."""
        text = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "ST: ssdp:all\r\n"
            "MX: 3\r\n"
            "\r\n"
        )
        # parse_ssdp_response will return headers (M-SEARCH starts not with HTTP/ or NOTIFY)
        headers = parse_ssdp_response(text)
        assert headers is None

    def test_empty_input(self):
        assert parse_ssdp_response("") is None

    def test_no_headers(self):
        assert parse_ssdp_response("HTTP/1.1 200 OK\r\n") is None

    def test_malformed_input(self):
        assert parse_ssdp_response("garbage data") is None


class TestExtractPortFromUrl:
    def test_with_port(self):
        assert _extract_port_from_url("http://192.168.1.50:8080/desc.xml") == 8080

    def test_without_port(self):
        assert _extract_port_from_url("http://192.168.1.50/desc.xml") is None

    def test_https_with_port(self):
        assert _extract_port_from_url("https://192.168.1.50:443/desc.xml") == 443

    def test_high_port(self):
        assert _extract_port_from_url("http://10.0.0.1:49152/desc.xml") == 49152


class TestSTToCategory:
    def test_media_renderer(self):
        # MediaRenderer is too broad to categorize (could be display or speaker)
        assert _st_to_category("urn:schemas-upnp-org:device:MediaRenderer:1") is None

    def test_media_server(self):
        assert _st_to_category("urn:schemas-upnp-org:device:MediaServer:1") is None

    def test_basic_device(self):
        assert _st_to_category("urn:schemas-upnp-org:device:Basic:1") is None

    def test_none(self):
        assert _st_to_category(None) is None


# ============================================================
# SSDP Result Tests
# ============================================================


class TestSSDPResult:
    def test_to_device_info_full(self):
        result = SSDPResult(
            ip="192.168.1.50",
            usn="uuid:abc123",
            st="urn:schemas-upnp-org:device:MediaRenderer:1",
            location="http://192.168.1.50:49152/desc.xml",
            server="Linux/3.0, UPnP/1.0, Samsung/1.0",
            friendly_name="Living Room TV",
            manufacturer="Samsung",
            model_name="UE55",
            model_number="UN55NU8000",
            serial_number="SER123",
            udn="uuid:abc123",
        )
        info = result.to_device_info()
        assert info["device_name"] == "Living Room TV"
        assert info["manufacturer"] == "Samsung"
        assert info["model"] == "UE55 UN55NU8000"
        assert info["serial_number"] == "SER123"
        assert info["ssdp_info"]["usn"] == "uuid:abc123"
        assert "category" not in info  # ST is too broad to infer category

    def test_to_device_info_model_number_in_name(self):
        """Don't duplicate model_number if it's already in model_name."""
        result = SSDPResult(
            ip="192.168.1.60",
            model_name="UN55NU8000",
            model_number="UN55NU8000",
        )
        info = result.to_device_info()
        assert info["model"] == "UN55NU8000"  # Not duplicated

    def test_to_device_info_model_number_only(self):
        result = SSDPResult(ip="192.168.1.70", model_number="XYZ123")
        info = result.to_device_info()
        assert info["model"] == "XYZ123"

    def test_to_device_info_minimal(self):
        result = SSDPResult(ip="192.168.1.80")
        info = result.to_device_info()
        # Should have no device info fields, but ssdp_info is empty too
        assert "device_name" not in info
        assert "manufacturer" not in info


# ============================================================
# UPnP XML Parsing Tests
# ============================================================


class TestParseUpnpXml:
    def test_device_type_extracted_from_description(self):
        # The description's deviceType is the definitive URN — present
        # even when the M-SEARCH response carrying it was lost or arrived
        # under a generic ST.
        xml = """<?xml version="1.0"?>
        <root xmlns="urn:schemas-upnp-org:device-1-0">
          <device>
            <deviceType>urn:acme:device:WidgetFamily:1</deviceType>
            <friendlyName>Widget 6a</friendlyName>
            <modelName>Widget-6a</modelName>
          </device>
        </root>"""
        result = SSDPResult(ip="192.168.1.50")
        _parse_upnp_xml(result, xml)
        assert result.device_types == ["urn:acme:device:WidgetFamily:1"]
        assert result.model_name == "Widget-6a"

    def test_standard_xml(self):
        xml = """<?xml version="1.0"?>
        <root xmlns="urn:schemas-upnp-org:device-1-0">
          <device>
            <friendlyName>Living Room TV</friendlyName>
            <manufacturer>Samsung Electronics</manufacturer>
            <modelName>UE55</modelName>
            <modelNumber>UN55NU8000</modelNumber>
            <serialNumber>ABC12345</serialNumber>
            <UDN>uuid:12345678-1234-1234-1234-123456789012</UDN>
          </device>
        </root>"""
        result = SSDPResult(ip="192.168.1.50")
        _parse_upnp_xml(result, xml)
        assert result.friendly_name == "Living Room TV"
        assert result.manufacturer == "Samsung Electronics"
        assert result.model_name == "UE55"
        assert result.model_number == "UN55NU8000"
        assert result.serial_number == "ABC12345"
        assert result.udn == "uuid:12345678-1234-1234-1234-123456789012"

    def test_xml_without_namespace(self):
        xml = """<?xml version="1.0"?>
        <root>
          <device>
            <friendlyName>Projector</friendlyName>
            <manufacturer>NEC</manufacturer>
            <modelName>PA1004UL</modelName>
          </device>
        </root>"""
        result = SSDPResult(ip="192.168.1.60")
        _parse_upnp_xml(result, xml)
        assert result.friendly_name == "Projector"
        assert result.manufacturer == "NEC"
        assert result.model_name == "PA1004UL"

    def test_partial_xml(self):
        """Only some fields present."""
        xml = """<?xml version="1.0"?>
        <root xmlns="urn:schemas-upnp-org:device-1-0">
          <device>
            <friendlyName>Speaker</friendlyName>
            <manufacturer>QSC</manufacturer>
          </device>
        </root>"""
        result = SSDPResult(ip="192.168.1.70")
        _parse_upnp_xml(result, xml)
        assert result.friendly_name == "Speaker"
        assert result.manufacturer == "QSC"
        assert result.model_name is None
        assert result.serial_number is None

    def test_invalid_xml(self):
        """Should handle malformed XML gracefully."""
        result = SSDPResult(ip="192.168.1.80")
        _parse_upnp_xml(result, "<not valid xml")
        assert result.friendly_name is None

    def test_empty_xml(self):
        result = SSDPResult(ip="192.168.1.90")
        _parse_upnp_xml(result, "")
        assert result.friendly_name is None

    def test_xml_no_device_element(self):
        xml = """<?xml version="1.0"?>
        <root xmlns="urn:schemas-upnp-org:device-1-0">
          <specVersion><major>1</major><minor>0</minor></specVersion>
        </root>"""
        result = SSDPResult(ip="192.168.1.91")
        _parse_upnp_xml(result, xml)
        assert result.friendly_name is None


# ============================================================
# SSDP Scanner Tests
# ============================================================


class TestSSDPScanner:
    def test_init(self):
        scanner = SSDPScanner()
        assert scanner._running is False
        assert scanner._results == {}

    def test_search_targets_defined(self):
        assert len(SEARCH_TARGETS) > 0
        assert "ssdp:all" in SEARCH_TARGETS

    def test_m_search_template_format(self):
        """M-SEARCH template should be formattable."""
        msg = M_SEARCH_TEMPLATE.format(search_target="ssdp:all")
        assert "M-SEARCH" in msg
        assert "ssdp:all" in msg
        assert "239.255.255.250:1900" in msg

    @pytest.mark.asyncio
    async def test_scan_handles_socket_error(self):
        """Should return empty results if socket creation fails."""
        scanner = SSDPScanner()
        with patch(
            "server.discovery.ssdp_scanner._create_ssdp_socket",
            side_effect=OSError("Permission denied"),
        ):
            results = await scanner.scan(timeout=0.1)
        assert results == {}

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self):
        scanner = SSDPScanner()
        scanner._running = True
        await scanner.stop()
        assert scanner._running is False

    def test_process_response_standard(self):
        """Process a standard SSDP response."""
        scanner = SSDPScanner()
        text = (
            "HTTP/1.1 200 OK\r\n"
            "CACHE-CONTROL: max-age=1800\r\n"
            "LOCATION: http://192.168.1.50:49152/description.xml\r\n"
            "SERVER: Linux/3.0, UPnP/1.0, Samsung/1.0\r\n"
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "USN: uuid:abc123\r\n"
            "\r\n"
        )
        scanner._process_response(text.encode("utf-8"), "192.168.1.50")
        assert "192.168.1.50" in scanner._results
        result = scanner._results["192.168.1.50"]
        assert result.location == "http://192.168.1.50:49152/description.xml"
        assert result.server == "Linux/3.0, UPnP/1.0, Samsung/1.0"
        assert result.port == 49152

    def test_process_response_ignores_m_search(self):
        """Should ignore M-SEARCH echoes."""
        scanner = SSDPScanner()
        text = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            "\r\n"
        )
        scanner._process_response(text.encode("utf-8"), "192.168.1.1")
        assert len(scanner._results) == 0

    def test_process_response_updates_existing(self):
        """Multiple responses from same IP should update the record."""
        scanner = SSDPScanner()

        resp1 = (
            "HTTP/1.1 200 OK\r\n"
            "ST: ssdp:all\r\n"
            "USN: uuid:first\r\n"
            "\r\n"
        )
        scanner._process_response(resp1.encode("utf-8"), "192.168.1.50")

        resp2 = (
            "HTTP/1.1 200 OK\r\n"
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "SERVER: Samsung UPnP SDK\r\n"
            "\r\n"
        )
        scanner._process_response(resp2.encode("utf-8"), "192.168.1.50")

        assert len(scanner._results) == 1
        result = scanner._results["192.168.1.50"]
        assert result.st == "urn:schemas-upnp-org:device:MediaRenderer:1"
        assert result.server == "Samsung UPnP SDK"

    def test_process_response_accumulates_all_observed_types(self):
        """An ssdp:all responder answers once per advertised type; a later
        generic response (upnp:rootdevice) must not clobber the family
        device-type URN a driver fingerprints."""
        scanner = SSDPScanner()

        resp1 = (
            "HTTP/1.1 200 OK\r\n"
            "ST: urn:acme:device:WidgetFamily:1\r\n"
            "USN: uuid:abc::urn:acme:device:WidgetFamily:1\r\n"
            "\r\n"
        )
        scanner._process_response(resp1.encode("utf-8"), "192.168.1.50")

        resp2 = (
            "HTTP/1.1 200 OK\r\n"
            "ST: upnp:rootdevice\r\n"
            "USN: uuid:abc::upnp:rootdevice\r\n"
            "\r\n"
        )
        scanner._process_response(resp2.encode("utf-8"), "192.168.1.50")

        result = scanner._results["192.168.1.50"]
        # Most-recent ST is the generic one...
        assert result.st == "upnp:rootdevice"
        # ...but the URN stays observed, so the fingerprint can still match.
        assert result.device_types == [
            "urn:acme:device:WidgetFamily:1",
            "upnp:rootdevice",
        ]

    def test_process_response_mines_usn_suffix(self):
        """A response whose ST is a bare uuid still names its type in the
        USN suffix (uuid:X::<type>)."""
        scanner = SSDPScanner()
        resp = (
            "HTTP/1.1 200 OK\r\n"
            "ST: uuid:abc123\r\n"
            "USN: uuid:abc123::urn:acme:device:WidgetFamily:1\r\n"
            "\r\n"
        )
        scanner._process_response(resp.encode("utf-8"), "192.168.1.50")
        result = scanner._results["192.168.1.50"]
        assert result.device_types == ["urn:acme:device:WidgetFamily:1"]


# ============================================================
# SSDP HTTP Fetch Tests
# ============================================================


class TestSSDPHttpGet:
    @pytest.mark.asyncio
    async def test_fetch_description_integration(self):
        """Test that _fetch_single_description calls _http_get and parses XML."""
        scanner = SSDPScanner()
        result = SSDPResult(
            ip="192.168.1.50",
            location="http://192.168.1.50:49152/desc.xml"
        )
        xml_body = """<?xml version="1.0"?>
        <root xmlns="urn:schemas-upnp-org:device-1-0">
          <device>
            <friendlyName>Test TV</friendlyName>
            <manufacturer>TestMfg</manufacturer>
          </device>
        </root>"""

        with patch(
            "server.discovery.ssdp_scanner._http_get",
            new_callable=AsyncMock,
            return_value=xml_body,
        ):
            await scanner._fetch_single_description(result)

        assert result.friendly_name == "Test TV"
        assert result.manufacturer == "TestMfg"

    @pytest.mark.asyncio
    async def test_fetch_description_handles_failure(self):
        """Should not raise on fetch failure."""
        scanner = SSDPScanner()
        result = SSDPResult(
            ip="192.168.1.50",
            location="http://192.168.1.50:49152/desc.xml"
        )
        with patch(
            "server.discovery.ssdp_scanner._http_get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await scanner._fetch_single_description(result)
        assert result.friendly_name is None

    @pytest.mark.asyncio
    async def test_fetch_description_no_location(self):
        """No-op if location is None."""
        scanner = SSDPScanner()
        result = SSDPResult(ip="192.168.1.50", location=None)
        await scanner._fetch_single_description(result)
        assert result.friendly_name is None


# ============================================================
# SSDP Hardening: source cap, SSRF-guarded fetch, NOTIFY, multicast join
# ============================================================


class TestSSDPSourceCap:
    """A spoofed responder must not be able to grow ``_results`` without limit."""

    def _response(self, n: int) -> bytes:
        return (
            "HTTP/1.1 200 OK\r\n"
            f"ST: urn:schemas-upnp-org:device:Widget-{n}:1\r\n"
            f"USN: uuid:dev-{n}\r\n"
            "\r\n"
        ).encode("utf-8")

    def test_distinct_sources_capped(self):
        scanner = SSDPScanner()
        for n in range(MAX_SSDP_SOURCES + 50):
            scanner._process_response(
                self._response(n),
                f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}",
            )
        assert len(scanner._results) == MAX_SSDP_SOURCES

    def test_known_source_still_updates_past_cap(self):
        scanner = SSDPScanner()
        for n in range(MAX_SSDP_SOURCES + 50):
            scanner._process_response(
                self._response(n),
                f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}",
            )
        # 10.0.0.0 was the first source in; a fresh response from it must still
        # be recorded even after the cap trips.
        scanner._process_response(
            (
                "HTTP/1.1 200 OK\r\n"
                "SERVER: Updated UPnP SDK\r\n"
                "\r\n"
            ).encode("utf-8"),
            "10.0.0.0",
        )
        assert scanner._results["10.0.0.0"].server == "Updated UPnP SDK"

    def test_cap_logs_once_per_window(self, caplog):
        scanner = SSDPScanner()
        with caplog.at_level("WARNING"):
            for n in range(MAX_SSDP_SOURCES + 10):
                scanner._process_response(
                    self._response(n),
                    f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}",
                )
        hits = [r for r in caplog.records if "distinct-source cap" in r.message]
        assert len(hits) == 1


class TestSSDPLocationSSRFGuard:
    """The description fetch must only ever hit the responder's own LOCATION."""

    def test_location_host_is_sender_matches_ip(self):
        assert _location_host_is_sender(
            "http://192.168.1.50:49152/desc.xml", "192.168.1.50"
        )

    def test_location_host_is_sender_rejects_foreign_ip(self):
        # LOCATION points at an internal service on a different host.
        assert not _location_host_is_sender(
            "http://10.0.0.5:6379/desc.xml", "192.168.1.50"
        )

    def test_location_host_is_sender_rejects_hostname(self):
        # A hostname would require resolution — a DNS-rebinding vector.
        assert not _location_host_is_sender(
            "http://internal.corp.example/desc.xml", "192.168.1.50"
        )

    def test_location_host_is_sender_rejects_metadata_endpoint(self):
        assert not _location_host_is_sender(
            "http://169.254.169.254/latest/meta-data/", "192.168.1.50"
        )

    @pytest.mark.asyncio
    async def test_fetch_skips_foreign_location(self):
        """A LOCATION that isn't the sender's own IP must not be fetched."""
        scanner = SSDPScanner()
        result = SSDPResult(
            ip="192.168.1.50",
            location="http://10.0.0.5:6379/desc.xml",
        )
        with patch(
            "server.discovery.ssdp_scanner._http_get",
            new_callable=AsyncMock,
        ) as mock_get:
            await scanner._fetch_single_description(result)
        mock_get.assert_not_called()
        assert result.friendly_name is None

    @pytest.mark.asyncio
    async def test_fetch_allows_own_location(self):
        """A LOCATION on the responder's own IP is still fetched."""
        scanner = SSDPScanner()
        result = SSDPResult(
            ip="192.168.1.50",
            location="http://192.168.1.50:49152/desc.xml",
        )
        xml_body = (
            '<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
            "<friendlyName>Own Device</friendlyName></device></root>"
        )
        with patch(
            "server.discovery.ssdp_scanner._http_get",
            new_callable=AsyncMock,
            return_value=xml_body,
        ) as mock_get:
            await scanner._fetch_single_description(result)
        mock_get.assert_called_once()
        assert result.friendly_name == "Own Device"

    @pytest.mark.asyncio
    async def test_fetch_descriptions_concurrency_capped(self):
        """Concurrent description fetches must not exceed MAX_DESCRIPTION_FETCHES."""
        scanner = SSDPScanner()
        # Many responders, each with a valid same-IP LOCATION.
        for n in range(MAX_DESCRIPTION_FETCHES * 4):
            ip = f"192.168.5.{n}"
            scanner._results[ip] = SSDPResult(
                ip=ip, location=f"http://{ip}:49152/desc.xml"
            )

        in_flight = 0
        peak = 0

        async def fake_get(url, timeout=3.0):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return None

        with patch(
            "server.discovery.ssdp_scanner._http_get",
            side_effect=fake_get,
        ):
            await scanner._fetch_descriptions()

        assert peak <= MAX_DESCRIPTION_FETCHES


class TestSSDPNotify:
    """NOTIFY beacons carry the type in NT and announce departures via NTS."""

    def test_notify_alive_uses_nt(self):
        scanner = SSDPScanner()
        notify = (
            "NOTIFY * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            "NT: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "NTS: ssdp:alive\r\n"
            "USN: uuid:beacon-1::urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "SERVER: Acme UPnP/1.0\r\n"
            "\r\n"
        )
        scanner._process_response(notify.encode("utf-8"), "192.168.1.77")
        assert "192.168.1.77" in scanner._results
        result = scanner._results["192.168.1.77"]
        # NT populates the type even though there is no ST header.
        assert result.st == "urn:schemas-upnp-org:device:MediaRenderer:1"
        assert "urn:schemas-upnp-org:device:MediaRenderer:1" in result.device_types

    def test_notify_byebye_removes_existing(self):
        scanner = SSDPScanner()
        # First an M-SEARCH reply records the device...
        reply = (
            "HTTP/1.1 200 OK\r\n"
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "USN: uuid:beacon-1\r\n"
            "\r\n"
        )
        scanner._process_response(reply.encode("utf-8"), "192.168.1.77")
        assert "192.168.1.77" in scanner._results

        # ...then a byebye departure drops it.
        byebye = (
            "NOTIFY * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            "NT: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "NTS: ssdp:byebye\r\n"
            "USN: uuid:beacon-1\r\n"
            "\r\n"
        )
        scanner._process_response(byebye.encode("utf-8"), "192.168.1.77")
        assert "192.168.1.77" not in scanner._results

    def test_notify_byebye_no_record_is_noop(self):
        scanner = SSDPScanner()
        byebye = (
            "NOTIFY * HTTP/1.1\r\n"
            "NTS: ssdp:byebye\r\n"
            "USN: uuid:ghost\r\n"
            "\r\n"
        )
        scanner._process_response(byebye.encode("utf-8"), "192.168.1.99")
        assert scanner._results == {}


class TestSSDPSocketJoinsGroup:
    """The listening socket must join the SSDP group, not just bind ephemeral."""

    def test_socket_binds_port_and_joins_group(self):
        from server.discovery import ssdp_scanner as mod

        created = {}

        class FakeSocket:
            def __init__(self, *a, **k):
                created["sock"] = self
                self.bound = None
                self.closed = False

            def setsockopt(self, *a):
                pass

            def bind(self, addr):
                self.bound = addr

            def setblocking(self, flag):
                pass

            def close(self):
                self.closed = True

        with patch.object(mod.socket, "socket", FakeSocket), \
             patch.object(mod, "set_shared_port_reuse"), \
             patch.object(
                 mod, "join_group_on_interfaces", return_value=["192.168.1.10"]
             ) as mock_join:
            sock = mod._create_ssdp_socket()

        assert sock.bound == ("", SSDP_PORT)
        mock_join.assert_called_once()
        # Joined the SSDP multicast group.
        assert mock_join.call_args.args[1] == SSDP_ADDR

    def test_socket_raises_when_no_interface_joins(self):
        from server.discovery import ssdp_scanner as mod

        class FakeSocket:
            def __init__(self, *a, **k):
                self.closed = False

            def setsockopt(self, *a):
                pass

            def bind(self, addr):
                pass

            def setblocking(self, flag):
                pass

            def close(self):
                self.closed = True

        with patch.object(mod.socket, "socket", FakeSocket), \
             patch.object(mod, "set_shared_port_reuse"), \
             patch.object(mod, "join_group_on_interfaces", return_value=[]):
            with pytest.raises(OSError):
                mod._create_ssdp_socket()


# ============================================================
# Engine Integration Tests
# ============================================================


class TestEnginePassiveIntegration:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    def test_total_phases_is_eight(self):
        """Engine should now have 8 phases."""
        assert self.engine.scan_status.total_phases == 8

    def test_scan_status_total_phases(self):
        status = self.engine.get_status()
        assert status["total_phases"] == 8

    def _amx_future(self, result=None):
        f = asyncio.get_event_loop().create_future()
        f.set_result(result or {})
        return f

    @pytest.mark.asyncio
    async def test_collect_passive_results_mdns(self):
        """mDNS results merge into engine results + emit passive_listener evidence."""
        mdns_results = {
            "192.168.1.72": MDNSResult(
                ip="192.168.1.72",
                hostname="projector",
                port=4352,
                service_type="_pjlink._tcp.local.",
                instance_name="NEC PA1004UL",
                txt_records={"manufacturer": "NEC", "model": "PA1004UL"},
            ),
        }

        mdns_future = asyncio.get_event_loop().create_future()
        mdns_future.set_result(mdns_results)
        ssdp_future = asyncio.get_event_loop().create_future()
        ssdp_future.set_result({})

        await self.engine._collect_passive_results(
            mdns_future, ssdp_future, self._amx_future(),
        )

        assert "192.168.1.72" in self.engine.results
        device = self.engine.results["192.168.1.72"]
        assert device.alive is True
        assert any(e.source == "mdns:_pjlink._tcp.local." for e in device.evidence_log)
        assert device.device_name == "NEC PA1004UL"
        assert "_pjlink._tcp.local." in device.mdns_services

    @pytest.mark.asyncio
    async def test_collect_passive_results_ssdp(self):
        """SSDP results merge into engine results + emit passive_listener evidence."""
        ssdp_results = {
            "192.168.1.50": SSDPResult(
                ip="192.168.1.50",
                friendly_name="Living Room TV",
                manufacturer="Samsung",
                model_name="UE55",
                serial_number="SER123",
                usn="uuid:abc123",
                st="urn:schemas-upnp-org:device:MediaRenderer:1",
            ),
        }

        mdns_future = asyncio.get_event_loop().create_future()
        mdns_future.set_result({})
        ssdp_future = asyncio.get_event_loop().create_future()
        ssdp_future.set_result(ssdp_results)

        await self.engine._collect_passive_results(
            mdns_future, ssdp_future, self._amx_future(),
        )

        assert "192.168.1.50" in self.engine.results
        device = self.engine.results["192.168.1.50"]
        assert device.alive is True
        assert any(
            e.data.get("source_id") == "urn:schemas-upnp-org:device:MediaRenderer:1"
            for e in device.evidence_log
        )
        assert device.device_name == "Living Room TV"
        assert device.manufacturer == "Samsung"

    @pytest.mark.asyncio
    async def test_collect_passive_results_merge_with_active(self):
        """Passive results merge with existing active-scan device records."""
        self.engine.results["192.168.1.50"] = DiscoveredDevice(
            ip="192.168.1.50",
            mac="8c:71:f8:11:22:33",
            manufacturer="Samsung",
        )

        ssdp_results = {
            "192.168.1.50": SSDPResult(
                ip="192.168.1.50",
                friendly_name="Conference Room Display",
                manufacturer="Samsung Electronics",
                model_name="QM55R",
            ),
        }

        mdns_future = asyncio.get_event_loop().create_future()
        mdns_future.set_result({})
        ssdp_future = asyncio.get_event_loop().create_future()
        ssdp_future.set_result(ssdp_results)

        await self.engine._collect_passive_results(
            mdns_future, ssdp_future, self._amx_future(),
        )

        device = self.engine.results["192.168.1.50"]
        assert device.mac == "8c:71:f8:11:22:33"
        assert device.device_name == "Conference Room Display"
        assert device.model == "QM55R"
        assert device.manufacturer == "Samsung Electronics"

    @pytest.mark.asyncio
    async def test_collect_passive_handles_failed_tasks(self):
        """Failed mDNS/SSDP/AMX-DDP tasks don't crash the collector."""
        mdns_future = asyncio.get_event_loop().create_future()
        mdns_future.set_exception(OSError("Socket error"))
        ssdp_future = asyncio.get_event_loop().create_future()
        ssdp_future.set_result({})

        await self.engine._collect_passive_results(
            mdns_future, ssdp_future, self._amx_future(),
        )
        assert len(self.engine.results) == 0

    @pytest.mark.asyncio
    async def test_collect_passive_both_contribute(self):
        """mDNS + SSDP can both contribute info about the same device."""
        mdns_results = {
            "192.168.1.50": MDNSResult(
                ip="192.168.1.50",
                hostname="tv-livingroom",
                service_type="_airplay._tcp.local.",
                instance_name="Living Room TV",
            ),
        }
        ssdp_results = {
            "192.168.1.50": SSDPResult(
                ip="192.168.1.50",
                manufacturer="Samsung",
                model_name="QM55R",
                serial_number="SER999",
            ),
        }

        mdns_future = asyncio.get_event_loop().create_future()
        mdns_future.set_result(mdns_results)
        ssdp_future = asyncio.get_event_loop().create_future()
        ssdp_future.set_result(ssdp_results)

        await self.engine._collect_passive_results(
            mdns_future, ssdp_future, self._amx_future(),
        )

        device = self.engine.results["192.168.1.50"]
        assert device.hostname == "tv-livingroom"
        assert device.manufacturer == "Samsung"
        assert device.model == "QM55R"
        assert device.serial_number == "SER999"
        assert any(e.source.startswith("mdns:") for e in device.evidence_log)


# ============================================================
# Merge Behavior Tests (passive data)
# ============================================================


class TestPassiveMerge:
    def test_merge_mdns_services(self):
        device = DiscoveredDevice(ip="192.168.1.50")
        merge_device_info(device, {
            "mdns_services": ["_pjlink._tcp.local."],
        }, "mdns")
        assert "_pjlink._tcp.local." in device.mdns_services

    def test_merge_mdns_services_dedup(self):
        device = DiscoveredDevice(
            ip="192.168.1.50",
            mdns_services=["_pjlink._tcp.local."],
        )
        merge_device_info(device, {
            "mdns_services": ["_pjlink._tcp.local.", "_http._tcp.local."],
        }, "mdns")
        assert len(device.mdns_services) == 2
        assert "_http._tcp.local." in device.mdns_services

    def test_merge_ssdp_info(self):
        device = DiscoveredDevice(ip="192.168.1.50")
        merge_device_info(device, {
            "ssdp_info": {"usn": "uuid:abc", "st": "ssdp:all"},
        }, "ssdp")
        assert device.ssdp_info is not None
        assert device.ssdp_info["usn"] == "uuid:abc"

    def test_merge_ssdp_doesnt_overwrite(self):
        device = DiscoveredDevice(
            ip="192.168.1.50",
            ssdp_info={"usn": "uuid:first"},
        )
        merge_device_info(device, {
            "ssdp_info": {"usn": "uuid:second"},
        }, "ssdp")
        # Should NOT overwrite existing ssdp_info
        assert device.ssdp_info["usn"] == "uuid:first"

    def test_passive_enriches_active_device(self):
        """Passive scan adds to an actively discovered device."""
        device = DiscoveredDevice(
            ip="192.168.1.50",
            mac="8c:71:f8:11:22:33",
            manufacturer="Samsung",
            open_ports=[80, 1515],
        )
        # mDNS gives us the hostname
        merge_device_info(device, {
            "hostname": "samsung-display",
            "mdns_services": ["_http._tcp.local."],
        }, "mdns")
        assert device.hostname == "samsung-display"
        assert device.mac == "8c:71:f8:11:22:33"  # Preserved
        assert 80 in device.open_ports  # Preserved

        # SSDP gives us the model
        merge_device_info(device, {
            "device_name": "Conference Room Display",
            "model": "QM55R",
        }, "ssdp")
        assert device.device_name == "Conference Room Display"
        assert device.model == "QM55R"
