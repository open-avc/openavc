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
    AV_SERVICE_TYPES,
    _parse_txt_rdata,
    _extract_instance_name,
    _service_type_to_protocol,
    _service_type_to_category,
)
from server.discovery.ssdp_scanner import (
    SSDPScanner,
    SSDPResult,
    parse_ssdp_response,
    _extract_port_from_url,
    _parse_upnp_xml,
    _st_to_category,
    M_SEARCH_TEMPLATE,
    SEARCH_TARGETS,
)
from server.discovery.result import (
    DiscoveredDevice,
    merge_device_info,
    compute_confidence,
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
        result = MDNSResult(
            ip="192.168.1.50",
            hostname="projector",
            port=4352,
            service_type="_pjlink._tcp.local.",
            instance_name="NEC PA1004UL",
        )
        info = result.to_device_info()
        assert info["hostname"] == "projector"
        assert info["device_name"] == "NEC PA1004UL"
        assert "_pjlink._tcp.local." in info["mdns_services"]
        assert "pjlink" in info["protocols"]
        assert info["category"] == "projector"

    def test_to_device_info_with_txt_records(self):
        result = MDNSResult(
            ip="192.168.1.60",
            service_type="_http._tcp.local.",
            txt_records={
                "manufacturer": "Samsung",
                "model": "UN55NU8000",
                "fw": "T-KTM2AKUC-1400.5",
                "sn": "SER12345",
            },
        )
        info = result.to_device_info()
        assert info["manufacturer"] == "Samsung"
        assert info["model"] == "UN55NU8000"
        assert info["firmware"] == "T-KTM2AKUC-1400.5"
        assert info["serial_number"] == "SER12345"

    def test_to_device_info_short_txt_keys(self):
        """Test shorthand TXT keys (mf, md, fw, sn)."""
        result = MDNSResult(
            ip="192.168.1.70",
            txt_records={"mf": "LG", "md": "OLED55"},
        )
        info = result.to_device_info()
        assert info["manufacturer"] == "LG"
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


class TestServiceTypeMapping:
    def test_pjlink_protocol(self):
        assert _service_type_to_protocol("_pjlink._tcp.local.") == "pjlink"

    def test_pjlink_protocol_no_trailing_dot(self):
        """DNS-decoded names don't have trailing dots."""
        assert _service_type_to_protocol("_pjlink._tcp.local") == "pjlink"

    def test_qsc_protocol(self):
        assert _service_type_to_protocol("_qsc._tcp.local.") == "qsc"

    def test_shure_protocol(self):
        assert _service_type_to_protocol("_shure._tcp.local.") == "shure_dcs"

    def test_http_no_protocol(self):
        assert _service_type_to_protocol("_http._tcp.local.") is None

    def test_none_input(self):
        assert _service_type_to_protocol(None) is None

    def test_pjlink_category(self):
        assert _service_type_to_category("_pjlink._tcp.local.") == "projector"

    def test_pjlink_category_no_trailing_dot(self):
        assert _service_type_to_category("_pjlink._tcp.local") == "projector"

    def test_airplay_category(self):
        assert _service_type_to_category("_airplay._tcp.local.") == "display"

    def test_qsc_category(self):
        assert _service_type_to_category("_qsc._tcp.local.") == "audio"

    def test_unknown_category(self):
        assert _service_type_to_category("_http._tcp.local.") is None


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

    def test_av_service_types_defined(self):
        """Verify we have AV-relevant service types."""
        assert len(AV_SERVICE_TYPES) > 5
        assert "_pjlink._tcp.local." in AV_SERVICE_TYPES
        assert "_airplay._tcp.local." in AV_SERVICE_TYPES
        assert "_http._tcp.local." in AV_SERVICE_TYPES

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

    @pytest.mark.asyncio
    async def test_collect_passive_results_mdns(self):
        """Test that mDNS results are merged into engine results."""
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
        ssdp_results = {}

        mdns_future = asyncio.get_event_loop().create_future()
        mdns_future.set_result(mdns_results)
        ssdp_future = asyncio.get_event_loop().create_future()
        ssdp_future.set_result(ssdp_results)

        await self.engine._collect_passive_results(mdns_future, ssdp_future)

        assert "192.168.1.72" in self.engine.results
        device = self.engine.results["192.168.1.72"]
        assert device.alive is True
        assert "mdns_advertised" in device.sources
        assert "NEC PA1004UL" == device.device_name
        assert "_pjlink._tcp.local." in device.mdns_services

    @pytest.mark.asyncio
    async def test_collect_passive_results_ssdp(self):
        """Test that SSDP results are merged into engine results."""
        mdns_results = {}
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
        mdns_future.set_result(mdns_results)
        ssdp_future = asyncio.get_event_loop().create_future()
        ssdp_future.set_result(ssdp_results)

        await self.engine._collect_passive_results(mdns_future, ssdp_future)

        assert "192.168.1.50" in self.engine.results
        device = self.engine.results["192.168.1.50"]
        assert device.alive is True
        assert "ssdp_identified" in device.sources
        assert device.device_name == "Living Room TV"
        assert device.manufacturer == "Samsung"

    @pytest.mark.asyncio
    async def test_collect_passive_results_merge_with_active(self):
        """Passive results should merge with existing active scan results."""
        # Pre-populate from active scan
        self.engine.results["192.168.1.50"] = DiscoveredDevice(
            ip="192.168.1.50",
            mac="8c:71:f8:11:22:33",
            manufacturer="Samsung",
            sources=["alive", "mac_known", "oui_av_mfg"],
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

        await self.engine._collect_passive_results(mdns_future, ssdp_future)

        device = self.engine.results["192.168.1.50"]
        assert device.mac == "8c:71:f8:11:22:33"  # Preserved from active
        assert device.device_name == "Conference Room Display"  # From SSDP
        assert device.model == "QM55R"  # From SSDP
        # Longer manufacturer wins (merge_device_info behavior)
        assert device.manufacturer == "Samsung Electronics"
        assert "ssdp_identified" in device.sources
        assert "alive" in device.sources  # Preserved

    @pytest.mark.asyncio
    async def test_collect_passive_handles_failed_tasks(self):
        """Should handle tasks that raised exceptions."""
        mdns_future = asyncio.get_event_loop().create_future()
        mdns_future.set_exception(OSError("Socket error"))
        ssdp_future = asyncio.get_event_loop().create_future()
        ssdp_future.set_result({})

        # Should not raise
        await self.engine._collect_passive_results(mdns_future, ssdp_future)
        assert len(self.engine.results) == 0

    @pytest.mark.asyncio
    async def test_collect_passive_both_contribute(self):
        """Both mDNS and SSDP can contribute info about the same device."""
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

        await self.engine._collect_passive_results(mdns_future, ssdp_future)

        device = self.engine.results["192.168.1.50"]
        assert device.hostname == "tv-livingroom"  # From mDNS
        assert device.manufacturer == "Samsung"  # From SSDP
        assert device.model == "QM55R"  # From SSDP
        assert device.serial_number == "SER999"  # From SSDP
        assert "mdns_advertised" in device.sources
        assert "ssdp_identified" in device.sources


# ============================================================
# Confidence Scoring Tests (passive sources)
# ============================================================


class TestPassiveConfidenceScoring:
    def test_mdns_advertised_weight(self):
        score = compute_confidence(["alive", "mdns_advertised"])
        assert score == pytest.approx(0.15, abs=0.01)

    def test_ssdp_identified_weight(self):
        score = compute_confidence(["alive", "ssdp_identified"])
        assert score == pytest.approx(0.15, abs=0.01)

    def test_combined_passive_and_active(self):
        """Active + passive sources should combine."""
        score = compute_confidence([
            "alive", "mac_known", "oui_av_mfg",
            "av_port_open", "mdns_advertised", "ssdp_identified",
        ])
        expected = 0.05 + 0.05 + 0.15 + 0.10 + 0.10 + 0.10
        assert score == pytest.approx(expected, abs=0.01)

    def test_passive_only_device(self):
        """A device found only via passive means should have a score."""
        score = compute_confidence(["mdns_advertised"])
        assert score == 0.10

    def test_passive_capped_at_one(self):
        """Score should never exceed 1.0."""
        # Just use all known sources
        score = compute_confidence([
            "alive", "mac_known", "oui_av_mfg", "av_port_open",
            "banner_matched", "probe_confirmed", "snmp_identified",
            "mdns_advertised", "ssdp_identified", "model_known",
            "driver_matched", "hint_matched",
        ])
        assert score <= 1.0


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
            sources=["alive", "mac_known", "oui_av_mfg", "av_port_open"],
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
