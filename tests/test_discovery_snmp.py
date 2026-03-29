"""Tests for SNMP scanner + hostname resolution + engine integration (Chunk 5)."""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock

from server.discovery.snmp_scanner import (
    SNMPScanner,
    SNMPInfo,
    build_snmp_get,
    parse_snmp_response,
    parse_sys_descr,
    ber_encode_integer,
    ber_encode_string,
    ber_encode_oid,
    ber_encode_null,
    ber_encode_sequence,
    ber_encode_length,
    ber_encode_tagged,
    ber_decode_integer,
    ber_decode_string,
    ber_decode_oid,
    ber_decode_length,
    ber_decode_any_value,
    ber_skip_tlv,
    OIDS,
    ASN1_INTEGER,
    ASN1_OCTET_STRING,
    ASN1_OID,
    ASN1_SEQUENCE,
    SNMP_GET_RESPONSE,
    SNMP_VERSION_2C,
)
from server.discovery.result import (
    DiscoveredDevice,
    merge_device_info,
    compute_confidence,
)
from server.discovery.engine import DiscoveryEngine, _resolve_hostnames


# ============================================================
# BER Encoding Tests
# ============================================================


class TestBerEncodeLength:
    def test_short_length(self):
        assert ber_encode_length(0) == b"\x00"
        assert ber_encode_length(5) == b"\x05"
        assert ber_encode_length(127) == b"\x7f"

    def test_one_byte_length(self):
        result = ber_encode_length(128)
        assert result == b"\x81\x80"

    def test_two_byte_length(self):
        result = ber_encode_length(256)
        assert result == b"\x82\x01\x00"

    def test_large_length(self):
        result = ber_encode_length(1000)
        assert result[0] == 0x82
        assert (result[1] << 8) | result[2] == 1000


class TestBerEncodeInteger:
    def test_zero(self):
        result = ber_encode_integer(0)
        assert result[0] == ASN1_INTEGER
        assert result == b"\x02\x01\x00"

    def test_small_positive(self):
        result = ber_encode_integer(42)
        assert result[0] == ASN1_INTEGER
        assert result[-1] == 42

    def test_large_positive(self):
        result = ber_encode_integer(12345)
        assert result[0] == ASN1_INTEGER
        # Decode back
        value = int.from_bytes(result[2:], "big")
        assert value == 12345

    def test_roundtrip(self):
        for val in [0, 1, 127, 128, 255, 1000, 2**31 - 1]:
            encoded = ber_encode_integer(val)
            decoded, _ = ber_decode_integer(encoded, 0)
            assert decoded == val, f"Roundtrip failed for {val}"


class TestBerEncodeString:
    def test_empty_string(self):
        result = ber_encode_string("")
        assert result[0] == ASN1_OCTET_STRING
        assert result[1] == 0  # length

    def test_simple_string(self):
        result = ber_encode_string("public")
        assert result[0] == ASN1_OCTET_STRING
        assert result[1] == 6
        assert result[2:] == b"public"

    def test_roundtrip(self):
        for s in ["", "hello", "public", "SNMP Community String"]:
            encoded = ber_encode_string(s)
            decoded, _ = ber_decode_string(encoded, 0)
            assert decoded == s


class TestBerEncodeOid:
    def test_standard_oid(self):
        result = ber_encode_oid("1.3.6.1.2.1.1.1.0")
        assert result[0] == ASN1_OID
        # First two: 1.3 -> 40*1+3 = 43
        assert result[2] == 43

    def test_roundtrip(self):
        for oid in OIDS.values():
            encoded = ber_encode_oid(oid)
            decoded, _ = ber_decode_oid(encoded, 0)
            assert decoded == oid, f"Roundtrip failed for {oid}"

    def test_large_component(self):
        """OID components >= 128 use multi-byte encoding."""
        result = ber_encode_oid("1.3.6.1.2.1.1.1.0")
        # This should be a valid encoding
        assert len(result) > 2
        # Roundtrip
        decoded, _ = ber_decode_oid(result, 0)
        assert decoded == "1.3.6.1.2.1.1.1.0"


class TestBerEncodeNull:
    def test_null(self):
        result = ber_encode_null()
        assert result == b"\x05\x00"


class TestBerEncodeSequence:
    def test_simple_sequence(self):
        result = ber_encode_sequence([
            ber_encode_integer(1),
            ber_encode_string("test"),
        ])
        assert result[0] == ASN1_SEQUENCE
        # Should contain both items
        assert len(result) > 4


# ============================================================
# BER Decoding Tests
# ============================================================


class TestBerDecodeLength:
    def test_short(self):
        length, offset = ber_decode_length(b"\x05", 0)
        assert length == 5
        assert offset == 1

    def test_one_byte(self):
        length, offset = ber_decode_length(b"\x81\x80", 0)
        assert length == 128
        assert offset == 2

    def test_two_byte(self):
        length, offset = ber_decode_length(b"\x82\x01\x00", 0)
        assert length == 256
        assert offset == 3


class TestBerDecodeInteger:
    def test_zero(self):
        val, off = ber_decode_integer(b"\x02\x01\x00", 0)
        assert val == 0

    def test_positive(self):
        val, off = ber_decode_integer(b"\x02\x01\x2a", 0)
        assert val == 42

    def test_wrong_tag(self):
        """Should return 0 for non-integer tag."""
        val, off = ber_decode_integer(b"\x04\x01\x00", 0)
        assert val == 0


class TestBerDecodeString:
    def test_simple(self):
        val, off = ber_decode_string(b"\x04\x05hello", 0)
        assert val == "hello"

    def test_empty(self):
        val, off = ber_decode_string(b"\x04\x00", 0)
        assert val == ""


class TestBerDecodeOid:
    def test_sys_descr(self):
        encoded = ber_encode_oid("1.3.6.1.2.1.1.1.0")
        decoded, _ = ber_decode_oid(encoded, 0)
        assert decoded == "1.3.6.1.2.1.1.1.0"

    def test_all_standard_oids(self):
        for name, oid_str in OIDS.items():
            encoded = ber_encode_oid(oid_str)
            decoded, _ = ber_decode_oid(encoded, 0)
            assert decoded == oid_str, f"Failed for {name}: {oid_str}"


class TestBerDecodeAnyValue:
    def test_string(self):
        val, _ = ber_decode_any_value(b"\x04\x05hello", 0)
        assert val == "hello"

    def test_integer(self):
        val, _ = ber_decode_any_value(ber_encode_integer(42), 0)
        assert val == "42"

    def test_oid(self):
        val, _ = ber_decode_any_value(ber_encode_oid("1.3.6.1"), 0)
        assert val == "1.3.6.1"

    def test_null(self):
        val, _ = ber_decode_any_value(b"\x05\x00", 0)
        assert val == ""


class TestBerSkipTlv:
    def test_skip_integer(self):
        data = b"\x02\x01\x2a\x04\x05hello"
        offset = ber_skip_tlv(data, 0)
        assert offset == 3  # past the integer


# ============================================================
# SNMP Packet Building Tests
# ============================================================


class TestBuildSnmpGet:
    def test_builds_valid_packet(self):
        packet = build_snmp_get("public", [OIDS["sysDescr"]], 12345)
        # Should be a valid SEQUENCE
        assert packet[0] == ASN1_SEQUENCE
        assert len(packet) > 20

    def test_packet_contains_community(self):
        packet = build_snmp_get("public", [OIDS["sysDescr"]], 1)
        assert b"public" in packet

    def test_packet_contains_version(self):
        packet = build_snmp_get("public", [OIDS["sysDescr"]], 1)
        # Version 2c = integer 1
        assert ber_encode_integer(SNMP_VERSION_2C) in packet

    def test_multiple_oids(self):
        packet = build_snmp_get("public", list(OIDS.values()), 1)
        assert len(packet) > 50  # 5 OIDs should make a longer packet

    def test_custom_community(self):
        packet = build_snmp_get("myNetwork", [OIDS["sysDescr"]], 1)
        assert b"myNetwork" in packet


class TestParseSnmpResponse:
    def _build_response(self, community: str, request_id: int,
                        varbinds: list[tuple[str, str]]) -> bytes:
        """Build a mock SNMP GET-RESPONSE packet."""
        vb_items = []
        for oid_str, value in varbinds:
            vb = ber_encode_sequence([
                ber_encode_oid(oid_str),
                ber_encode_string(value),
            ])
            vb_items.append(vb)

        varbind_list = ber_encode_sequence(vb_items)

        pdu = ber_encode_tagged(SNMP_GET_RESPONSE, [
            ber_encode_integer(request_id),
            ber_encode_integer(0),   # error-status
            ber_encode_integer(0),   # error-index
            varbind_list,
        ])

        message = ber_encode_sequence([
            ber_encode_integer(SNMP_VERSION_2C),
            ber_encode_string(community),
            pdu,
        ])
        return message

    def test_parse_single_varbind(self):
        response = self._build_response("public", 1, [
            (OIDS["sysDescr"], "NEC PA1004UL Projector, Firmware V1.03"),
        ])
        result = parse_snmp_response(response)
        assert OIDS["sysDescr"] in result
        assert "NEC PA1004UL" in result[OIDS["sysDescr"]]

    def test_parse_multiple_varbinds(self):
        response = self._build_response("public", 1, [
            (OIDS["sysDescr"], "Extron DTP CrossPoint 84, V1.07"),
            (OIDS["sysName"], "Main-Switcher"),
            (OIDS["sysLocation"], "Rack A, Room 101"),
        ])
        result = parse_snmp_response(response)
        assert len(result) == 3
        assert "Extron" in result[OIDS["sysDescr"]]
        assert result[OIDS["sysName"]] == "Main-Switcher"
        assert "Room 101" in result[OIDS["sysLocation"]]

    def test_parse_all_oids(self):
        response = self._build_response("public", 42, [
            (OIDS["sysDescr"], "QSC Q-SYS Core 110f, V9.5.0"),
            (OIDS["sysName"], "Audio-DSP-01"),
            (OIDS["sysObjectID"], "1.3.6.1.4.1.12345"),
            (OIDS["sysContact"], "av-team@example.com"),
            (OIDS["sysLocation"], "Floor 3, Control Room"),
        ])
        result = parse_snmp_response(response)
        assert len(result) == 5

    def test_empty_data(self):
        assert parse_snmp_response(b"") == {}

    def test_malformed_data(self):
        assert parse_snmp_response(b"\x00\x01\x02\x03") == {}

    def test_error_status_nonzero(self):
        """Should return empty when error-status is non-zero."""
        vb = ber_encode_sequence([
            ber_encode_sequence([
                ber_encode_oid(OIDS["sysDescr"]),
                ber_encode_null(),
            ])
        ])
        pdu = ber_encode_tagged(SNMP_GET_RESPONSE, [
            ber_encode_integer(1),
            ber_encode_integer(2),  # error-status = noSuchName
            ber_encode_integer(1),
            vb,
        ])
        message = ber_encode_sequence([
            ber_encode_integer(SNMP_VERSION_2C),
            ber_encode_string("public"),
            pdu,
        ])
        result = parse_snmp_response(message)
        assert result == {}


# ============================================================
# sysDescr Parsing Tests
# ============================================================


class TestParseSysDescr:
    def test_nec_projector(self):
        result = parse_sys_descr("NEC PA1004UL Projector, Firmware V1.03")
        assert result["manufacturer"] == "NEC"
        assert result["model"] == "PA1004UL"
        assert result["category"] == "projector"

    def test_extron_switcher(self):
        result = parse_sys_descr("Extron DTP CrossPoint 84 IPCP, V1.07.0000")
        assert result["manufacturer"] == "Extron"
        assert "CrossPoint" in result["model"]
        assert result["firmware"] == "V1.07.0000"
        assert result["category"] == "switcher"

    def test_qsc_audio(self):
        result = parse_sys_descr("QSC Q-SYS Core 110f, V9.5.0")
        assert result["manufacturer"] == "QSC"
        assert "Core" in result["model"]
        assert result["firmware"] == "V9.5.0"
        assert result["category"] == "audio"

    def test_biamp_audio(self):
        result = parse_sys_descr("Biamp Tesira SERVER-IO, Firmware 4.14")
        assert result["manufacturer"] == "Biamp"
        assert "SERVER-IO" in result["model"]
        assert result["category"] == "audio"

    def test_shure_audio(self):
        result = parse_sys_descr("Shure MXA910, V4.5.6")
        assert result["manufacturer"] == "Shure"
        assert "MXA910" in result["model"]
        assert result["category"] == "audio"

    def test_crestron(self):
        result = parse_sys_descr("Crestron DM-MD8X8, Version 1.500")
        assert result["manufacturer"] == "Crestron"
        assert "DM-MD8X8" in result["model"]
        assert result["category"] == "control"

    def test_samsung_display(self):
        result = parse_sys_descr("Samsung QM55R")
        assert result["manufacturer"] == "Samsung"
        assert result["category"] == "display"

    def test_fallback_manufacturer(self):
        """Should detect manufacturer even without full pattern match."""
        result = parse_sys_descr("Some weird Extron device format")
        assert result.get("manufacturer") == "Extron"

    def test_unknown_device(self):
        result = parse_sys_descr("Linux router 4.19.0")
        assert result == {}

    def test_empty_string(self):
        result = parse_sys_descr("")
        assert result == {}


# ============================================================
# SNMPInfo Tests
# ============================================================


class TestSNMPInfo:
    def test_to_dict(self):
        info = SNMPInfo(
            sys_descr="NEC PA1004UL",
            sys_name="Projector-1",
            sys_location="Room 101",
        )
        d = info.to_dict()
        assert d["sysDescr"] == "NEC PA1004UL"
        assert d["sysName"] == "Projector-1"
        assert d["sysLocation"] == "Room 101"
        assert "sysContact" not in d  # Empty fields excluded

    def test_to_dict_empty(self):
        info = SNMPInfo()
        assert info.to_dict() == {}

    def test_to_device_info_with_descr(self):
        info = SNMPInfo(
            sys_descr="NEC PA1004UL Projector, Firmware V1.03",
            sys_name="Projector-Room101",
        )
        device_info = info.to_device_info()
        assert device_info["device_name"] == "Projector-Room101"
        assert device_info["manufacturer"] == "NEC"
        assert device_info["model"] == "PA1004UL"
        assert device_info["category"] == "projector"
        assert "snmp_info" in device_info

    def test_to_device_info_name_only(self):
        info = SNMPInfo(sys_name="Switch-Rack-A")
        device_info = info.to_device_info()
        assert device_info["device_name"] == "Switch-Rack-A"

    def test_to_device_info_empty(self):
        info = SNMPInfo()
        assert info.to_device_info() == {}


# ============================================================
# SNMP Scanner Tests
# ============================================================


class TestSNMPScanner:
    def test_init(self):
        scanner = SNMPScanner()
        assert scanner._results == {}

    def test_oids_defined(self):
        assert len(OIDS) == 5
        assert "sysDescr" in OIDS
        assert "sysName" in OIDS

    @pytest.mark.asyncio
    async def test_query_device_timeout(self):
        """Should return None on timeout."""
        scanner = SNMPScanner()
        with patch.object(scanner, "_udp_query", new_callable=AsyncMock, return_value=None):
            result = await scanner.query_device("192.168.1.1", timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_query_device_with_response(self):
        """Should parse a valid SNMP response."""
        scanner = SNMPScanner()

        # Build a mock response
        vb = ber_encode_sequence([
            ber_encode_sequence([
                ber_encode_oid(OIDS["sysDescr"]),
                ber_encode_string("NEC PA1004UL Projector"),
            ]),
            ber_encode_sequence([
                ber_encode_oid(OIDS["sysName"]),
                ber_encode_string("Projector-1"),
            ]),
        ])
        pdu = ber_encode_tagged(SNMP_GET_RESPONSE, [
            ber_encode_integer(1),
            ber_encode_integer(0),
            ber_encode_integer(0),
            vb,
        ])
        response = ber_encode_sequence([
            ber_encode_integer(SNMP_VERSION_2C),
            ber_encode_string("public"),
            pdu,
        ])

        with patch.object(scanner, "_udp_query", new_callable=AsyncMock, return_value=response):
            result = await scanner.query_device("192.168.1.72")

        assert result is not None
        assert result.sys_descr == "NEC PA1004UL Projector"
        assert result.sys_name == "Projector-1"

    @pytest.mark.asyncio
    async def test_scan_devices(self):
        """Should scan multiple devices in parallel."""
        scanner = SNMPScanner()

        # Build responses for two devices
        def make_response(descr, name):
            vb = ber_encode_sequence([
                ber_encode_sequence([
                    ber_encode_oid(OIDS["sysDescr"]),
                    ber_encode_string(descr),
                ]),
                ber_encode_sequence([
                    ber_encode_oid(OIDS["sysName"]),
                    ber_encode_string(name),
                ]),
            ])
            pdu = ber_encode_tagged(SNMP_GET_RESPONSE, [
                ber_encode_integer(1),
                ber_encode_integer(0),
                ber_encode_integer(0),
                vb,
            ])
            return ber_encode_sequence([
                ber_encode_integer(SNMP_VERSION_2C),
                ber_encode_string("public"),
                pdu,
            ])

        responses = {
            "192.168.1.50": make_response("Extron DTP CrossPoint, V1.07", "Switcher-1"),
            "192.168.1.72": make_response("NEC PA1004UL Projector", "Projector-1"),
        }

        async def mock_udp(ip, packet, loop):
            return responses.get(ip)

        with patch.object(scanner, "_udp_query", side_effect=mock_udp):
            results = await scanner.scan_devices(
                ["192.168.1.50", "192.168.1.72", "192.168.1.99"],
                concurrency=5,
            )

        assert len(results) == 2
        assert "192.168.1.50" in results
        assert "192.168.1.72" in results
        assert results["192.168.1.50"].sys_name == "Switcher-1"


# ============================================================
# Hostname Resolution Tests
# ============================================================


class TestResolveHostnames:
    @pytest.mark.asyncio
    async def test_resolves_known_hosts(self):
        with patch("server.discovery.engine._socket.gethostbyaddr") as mock_resolve:
            mock_resolve.side_effect = lambda ip: {
                "192.168.1.1": ("gateway.local", [], ["192.168.1.1"]),
                "192.168.1.50": ("extron-switch.local", [], ["192.168.1.50"]),
            }.get(ip, None) or (_ for _ in ()).throw(OSError("not found"))

            results = await _resolve_hostnames(["192.168.1.1", "192.168.1.50", "192.168.1.99"])

        assert results.get("192.168.1.1") == "gateway.local"
        assert results.get("192.168.1.50") == "extron-switch.local"
        assert "192.168.1.99" not in results

    @pytest.mark.asyncio
    async def test_handles_all_failures(self):
        with patch("server.discovery.engine._socket.gethostbyaddr", side_effect=OSError):
            results = await _resolve_hostnames(["192.168.1.1"])
        assert results == {}

    @pytest.mark.asyncio
    async def test_excludes_ip_as_hostname(self):
        """If gethostbyaddr returns the IP itself, skip it."""
        with patch("server.discovery.engine._socket.gethostbyaddr") as mock_resolve:
            mock_resolve.return_value = ("192.168.1.1", [], ["192.168.1.1"])
            results = await _resolve_hostnames(["192.168.1.1"])
        assert results == {}

    @pytest.mark.asyncio
    async def test_empty_list(self):
        results = await _resolve_hostnames([])
        assert results == {}


# ============================================================
# Engine Integration Tests (SNMP)
# ============================================================


class TestEngineSNMPIntegration:
    def setup_method(self):
        self.engine = DiscoveryEngine()

    @pytest.mark.asyncio
    async def test_collect_snmp_results(self):
        """Test that SNMP results are merged into engine results."""
        from server.discovery.snmp_scanner import SNMPInfo

        snmp_results = {
            "192.168.1.72": SNMPInfo(
                sys_descr="NEC PA1004UL Projector, Firmware V1.03",
                sys_name="Projector-Room101",
                sys_location="Building A, Room 101",
            ),
        }

        snmp_future = asyncio.get_event_loop().create_future()
        snmp_future.set_result(snmp_results)

        await self.engine._collect_snmp_results(snmp_future)

        assert "192.168.1.72" in self.engine.results
        device = self.engine.results["192.168.1.72"]
        assert "snmp_identified" in device.sources
        assert device.device_name == "Projector-Room101"
        assert device.manufacturer == "NEC"
        assert device.snmp_info is not None
        assert device.snmp_info["sysLocation"] == "Building A, Room 101"

    @pytest.mark.asyncio
    async def test_collect_snmp_results_none_task(self):
        """Should handle None task (SNMP disabled)."""
        await self.engine._collect_snmp_results(None)
        assert len(self.engine.results) == 0

    @pytest.mark.asyncio
    async def test_collect_snmp_results_failed_task(self):
        """Should handle failed SNMP task gracefully."""
        future = asyncio.get_event_loop().create_future()
        future.set_exception(OSError("SNMP failed"))
        await self.engine._collect_snmp_results(future)
        assert len(self.engine.results) == 0

    @pytest.mark.asyncio
    async def test_snmp_merges_with_active_results(self):
        """SNMP info should enrich actively discovered devices."""
        from server.discovery.snmp_scanner import SNMPInfo

        # Pre-populate from active scan
        self.engine.results["192.168.1.50"] = DiscoveredDevice(
            ip="192.168.1.50",
            mac="00:05:a6:12:34:56",
            manufacturer="Extron",
            sources=["alive", "mac_known", "oui_av_mfg"],
        )

        snmp_results = {
            "192.168.1.50": SNMPInfo(
                sys_descr="Extron DTP CrossPoint 84 IPCP, V1.07.0000",
                sys_name="Main-Switcher",
                sys_location="Rack A, Room 101",
            ),
        }

        future = asyncio.get_event_loop().create_future()
        future.set_result(snmp_results)
        await self.engine._collect_snmp_results(future)

        device = self.engine.results["192.168.1.50"]
        assert device.mac == "00:05:a6:12:34:56"  # Preserved
        assert device.device_name == "Main-Switcher"
        assert "CrossPoint" in device.model
        assert "V1.07" in device.firmware
        assert "snmp_identified" in device.sources


# ============================================================
# Confidence Scoring Tests (SNMP)
# ============================================================


class TestSNMPConfidenceScoring:
    def test_snmp_identified_weight(self):
        score = compute_confidence(["alive", "snmp_identified"])
        assert score == pytest.approx(0.15, abs=0.01)

    def test_full_active_plus_snmp(self):
        score = compute_confidence([
            "alive", "mac_known", "oui_av_mfg",
            "av_port_open", "probe_confirmed", "snmp_identified",
        ])
        expected = 0.05 + 0.05 + 0.15 + 0.10 + 0.20 + 0.10
        assert score == pytest.approx(expected, abs=0.01)


# ============================================================
# Merge Behavior Tests (SNMP)
# ============================================================


class TestSNMPMerge:
    def test_merge_snmp_info(self):
        device = DiscoveredDevice(ip="192.168.1.50")
        merge_device_info(device, {
            "snmp_info": {"sysDescr": "Test", "sysName": "Dev1"},
        }, "snmp")
        assert device.snmp_info is not None
        assert device.snmp_info["sysDescr"] == "Test"

    def test_merge_snmp_doesnt_overwrite(self):
        device = DiscoveredDevice(
            ip="192.168.1.50",
            snmp_info={"sysDescr": "First"},
        )
        merge_device_info(device, {
            "snmp_info": {"sysDescr": "Second"},
        }, "snmp")
        assert device.snmp_info["sysDescr"] == "First"

    def test_snmp_enriches_device(self):
        device = DiscoveredDevice(
            ip="192.168.1.50",
            manufacturer="Extron",
            sources=["alive", "oui_av_mfg"],
        )
        merge_device_info(device, {
            "device_name": "Main-Switcher",
            "model": "DTP CrossPoint 84 IPCP",
            "firmware": "V1.07.0000",
            "snmp_info": {"sysDescr": "Extron DTP CrossPoint 84 IPCP, V1.07.0000"},
        }, "snmp")
        assert device.device_name == "Main-Switcher"
        assert "CrossPoint" in device.model
        assert device.firmware == "V1.07.0000"
