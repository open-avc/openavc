"""Tests for SNMP scanner + hostname resolution + engine integration (Chunk 5)."""

import asyncio
import contextlib
import pytest
from unittest.mock import patch, AsyncMock

from openavc.discovery.snmp_scanner import (
    SNMPScanner,
    SNMPInfo,
    _SNMPQueryProtocol,
    build_snmp_get,
    parse_snmp_response,
    parse_snmp_request_id,
    extract_pen,
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
    ENTITY_COLUMNS,
    ENT_PHYSICAL_CONTAINED_IN,
    ENTITY_WALK_LIMIT,
    ASN1_INTEGER,
    ASN1_OCTET_STRING,
    ASN1_OID,
    ASN1_SEQUENCE,
    SNMP_GET_REQUEST,
    SNMP_GETNEXT_REQUEST,
    SNMP_GET_RESPONSE,
    SNMP_VERSION_2C,
)
from openavc.discovery.result import (
    DiscoveredDevice,
    merge_device_info,
)
from openavc.discovery.engine import DiscoveryEngine, _resolve_hostnames


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


class TestBerOidFirstSubidentifier:
    """The first OID subidentifier is (40*arc0)+arc1 and is itself base-128
    encoded — under arc 2 it can be >= 128 (multi-byte) per X.690 8.19."""

    def test_x690_worked_example_encode(self):
        # X.690 8.19.5: OID 2.999.3 encodes to 88 37 03
        assert ber_encode_oid("2.999.3") == bytes([ASN1_OID, 3, 0x88, 0x37, 0x03])

    def test_x690_worked_example_decode(self):
        decoded, offset = ber_decode_oid(bytes([ASN1_OID, 3, 0x88, 0x37, 0x03]), 0)
        assert decoded == "2.999.3"
        assert offset == 5

    def test_arc2_single_byte_above_120(self):
        # 40*2+40 = 120: single byte, but a naive //40 split reads it as 3.0
        decoded, _ = ber_decode_oid(ber_encode_oid("2.40"), 0)
        assert decoded == "2.40"

    def test_roundtrip_all_arcs(self):
        for oid_str in ["0.39", "1.39.7", "2.16.840.1.101.3", "2.40.17", "2.999.3", "2.999.1234567"]:
            encoded = ber_encode_oid(oid_str)
            decoded, _ = ber_decode_oid(encoded, 0)
            assert decoded == oid_str, f"Roundtrip failed for {oid_str}"


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
# PEN extraction tests
# ============================================================


class TestExtractPen:
    """``extract_pen`` returns a plain integer; vendor binding is per-driver."""

    def test_standard_pen_oid(self):
        # 1.3.6.1.4.1.<PEN>.<rest>
        assert extract_pen("1.3.6.1.4.1.17049.1.2.3") == 17049

    def test_root_pen_only(self):
        assert extract_pen("1.3.6.1.4.1.42") == 42

    def test_oid_outside_pen_branch(self):
        # OIDs that aren't under 1.3.6.1.4.1.<PEN> have no PEN.
        assert extract_pen("1.3.6.1.2.1.1.1.0") is None

    def test_empty_string(self):
        assert extract_pen("") is None

    def test_non_numeric_pen(self):
        assert extract_pen("1.3.6.1.4.1.notanumber.1") is None


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

    def test_to_device_info_surfaces_self_reported_fields(self):
        # Core no longer parses vendor strings out of sysDescr — drivers
        # do that via manufacturer_alias hints. ``to_device_info`` just
        # surfaces the device's self-reported sysDescr / sysName.
        info = SNMPInfo(
            sys_descr="Acme Foo 1234, Firmware V1.03",
            sys_name="Device-Room101",
        )
        device_info = info.to_device_info()
        assert device_info["device_name"] == "Device-Room101"
        assert "snmp_info" in device_info
        # No manufacturer/model/category guessed from sysDescr.
        assert "manufacturer" not in device_info
        assert "model" not in device_info
        assert "category" not in device_info

    def test_to_device_info_uses_entity_mib_when_present(self):
        # Entity MIB is device self-report — authoritative when set.
        info = SNMPInfo(
            sys_descr="some descr",
            entity_manufacturer="Acme Co",
            entity_model="Model 9",
            entity_serial="SN12345",
            entity_firmware_rev="3.2.1",
        )
        device_info = info.to_device_info()
        assert device_info["manufacturer"] == "Acme Co"
        assert device_info["model"] == "Model 9"
        assert device_info["serial_number"] == "SN12345"
        assert device_info["firmware"] == "3.2.1"

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

        async def mock_udp(ip, packet, timeout, expected_request_id):
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
        with patch("openavc.discovery.engine._socket.gethostbyaddr") as mock_resolve:
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
        with patch("openavc.discovery.engine._socket.gethostbyaddr", side_effect=OSError):
            results = await _resolve_hostnames(["192.168.1.1"])
        assert results == {}

    @pytest.mark.asyncio
    async def test_excludes_ip_as_hostname(self):
        """If gethostbyaddr returns the IP itself, skip it."""
        with patch("openavc.discovery.engine._socket.gethostbyaddr") as mock_resolve:
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
        """SNMP results are merged into engine results.

        Manufacturer comes from Entity MIB self-report when present,
        not from sysDescr parsing — that's now driver hint territory.
        """
        from openavc.discovery.snmp_scanner import SNMPInfo

        snmp_results = {
            "192.168.1.72": SNMPInfo(
                sys_descr="some-descr Projector, Firmware V1.03",
                sys_name="Projector-Room101",
                sys_location="Building A, Room 101",
                entity_manufacturer="Acme Co",
                entity_model="Model 9",
            ),
        }

        snmp_future = asyncio.get_event_loop().create_future()
        snmp_future.set_result(snmp_results)

        await self.engine._collect_snmp_results(snmp_future)

        assert "192.168.1.72" in self.engine.results
        device = self.engine.results["192.168.1.72"]
        assert device.device_name == "Projector-Room101"
        assert device.manufacturer == "Acme Co"
        assert device.model == "Model 9"
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
        """SNMP enriches actively discovered devices.

        sysDescr text is preserved verbatim in snmp_info; model /
        firmware come from Entity MIB self-report rather than from
        sysDescr regex parsing.
        """
        from openavc.discovery.snmp_scanner import SNMPInfo

        # Pre-populate from an earlier active scan.
        self.engine.results["192.168.1.50"] = DiscoveredDevice(
            ip="192.168.1.50",
            mac="00:05:a6:12:34:56",
            manufacturer="Acme Switcher Co",
        )

        snmp_results = {
            "192.168.1.50": SNMPInfo(
                sys_descr="Acme DTP CrossPoint 84 IPCP, V1.07.0000",
                sys_name="Main-Switcher",
                sys_location="Rack A, Room 101",
                entity_model="DTP CrossPoint 84",
                entity_firmware_rev="1.07.0000",
            ),
        }

        future = asyncio.get_event_loop().create_future()
        future.set_result(snmp_results)
        await self.engine._collect_snmp_results(future)

        device = self.engine.results["192.168.1.50"]
        assert device.mac == "00:05:a6:12:34:56"  # Preserved
        assert device.device_name == "Main-Switcher"
        assert device.model == "DTP CrossPoint 84"
        assert device.firmware == "1.07.0000"


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


# ============================================================
# Transport security + Entity MIB index discovery
# ============================================================

# SNMP v2c exception values (context-specific tags, RFC 3416)
NO_SUCH_INSTANCE = bytes([0x81, 0x00])
END_OF_MIB_VIEW = bytes([0x82, 0x00])


def _parse_request(data: bytes):
    """Decode (pdu_type, request_id, oids) from an SNMP request packet."""
    offset = 1  # outer SEQUENCE tag
    _msg_len, offset = ber_decode_length(data, offset)
    _version, offset = ber_decode_integer(data, offset)
    _community, offset = ber_decode_string(data, offset)
    pdu_type = data[offset]
    offset += 1
    _pdu_len, offset = ber_decode_length(data, offset)
    request_id, offset = ber_decode_integer(data, offset)
    _error_status, offset = ber_decode_integer(data, offset)
    _error_index, offset = ber_decode_integer(data, offset)
    offset += 1  # varbind list SEQUENCE tag
    vbl_len, offset = ber_decode_length(data, offset)
    end = offset + vbl_len
    oids = []
    while offset < end:
        offset += 1  # varbind SEQUENCE tag
        _vb_len, offset = ber_decode_length(data, offset)
        oid_str, offset = ber_decode_oid(data, offset)
        offset = ber_skip_tlv(data, offset)  # NULL value
        oids.append(oid_str)
    return pdu_type, request_id, oids


def _build_raw_response(request_id: int, varbinds: list[tuple[str, bytes]]) -> bytes:
    """Build a GET-RESPONSE whose varbind values are pre-encoded bytes."""
    vb_items = [
        ber_encode_sequence([ber_encode_oid(oid_str), value])
        for oid_str, value in varbinds
    ]
    pdu = ber_encode_tagged(SNMP_GET_RESPONSE, [
        ber_encode_integer(request_id),
        ber_encode_integer(0),
        ber_encode_integer(0),
        ber_encode_sequence(vb_items),
    ])
    return ber_encode_sequence([
        ber_encode_integer(SNMP_VERSION_2C),
        ber_encode_string("public"),
        pdu,
    ])


class _FakeSnmpAgent(asyncio.DatagramProtocol):
    """Minimal in-test SNMP agent: MIB-II scalars + an entPhysicalTable.

    entity_rows maps entPhysicalIndex -> {column_prefix: encoded_value}.
    """

    def __init__(self, scalars=None, entity_rows=None, reply_delay=0.0,
                 wrong_id_first=False, silent=False):
        self.scalars = scalars or {}
        self.entity_rows = entity_rows or {}
        self.reply_delay = reply_delay
        self.wrong_id_first = wrong_id_first
        self.silent = silent
        self.requests: list[tuple[int, list[str]]] = []
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        pdu_type, request_id, oids = _parse_request(data)
        self.requests.append((pdu_type, oids))
        if self.silent:
            return
        if pdu_type == SNMP_GET_REQUEST:
            varbinds = [(oid, self._get_exact(oid)) for oid in oids]
        else:  # GETNEXT
            varbinds = [self._get_next(oid) for oid in oids]
        response = _build_raw_response(request_id, varbinds)
        if self.wrong_id_first:
            evil = _build_raw_response(request_id ^ 0x7FFF, [
                (OIDS["sysDescr"], ber_encode_string("EVIL Device")),
                (OIDS["sysName"], ber_encode_string("EVIL")),
            ])
            self.transport.sendto(evil, addr)
        if self.reply_delay:
            asyncio.get_running_loop().call_later(
                self.reply_delay, self.transport.sendto, response, addr)
        else:
            self.transport.sendto(response, addr)

    def _all_instances(self) -> list[tuple[list[int], str, bytes]]:
        instances = []
        for index, row in self.entity_rows.items():
            for prefix, value in row.items():
                oid_str = f"{prefix}.{index}"
                instances.append(([int(p) for p in oid_str.split(".")], oid_str, value))
        instances.sort(key=lambda item: item[0])
        return instances

    def _get_exact(self, oid_str: str) -> bytes:
        if oid_str in self.scalars:
            return self.scalars[oid_str]
        for _key, inst_oid, value in self._all_instances():
            if inst_oid == oid_str:
                return value
        return NO_SUCH_INSTANCE

    def _get_next(self, oid_str: str) -> tuple[str, bytes]:
        target = [int(p) for p in oid_str.split(".")]
        for key, inst_oid, value in self._all_instances():
            if key > target:
                return inst_oid, value
        return oid_str, END_OF_MIB_VIEW


@contextlib.asynccontextmanager
async def _run_agent(agent: _FakeSnmpAgent):
    """Serve the fake agent on an ephemeral loopback port; yield the port."""
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: agent, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    try:
        yield port
    finally:
        transport.close()


_MIB2_SCALARS = {
    OIDS["sysDescr"]: ber_encode_string("Acme Widget 9000, V2.0"),
    OIDS["sysName"]: ber_encode_string("Widget-Lab"),
}


class TestParseSnmpRequestId:
    def test_extracts_id(self):
        response = _build_raw_response(424242, [
            (OIDS["sysDescr"], ber_encode_string("x")),
        ])
        assert parse_snmp_request_id(response) == 424242

    def test_rejects_garbage(self):
        assert parse_snmp_request_id(b"") is None
        assert parse_snmp_request_id(b"\x00\x01\x02") is None

    def test_rejects_request_pdu(self):
        packet = build_snmp_get("public", [OIDS["sysDescr"]], 99)
        assert parse_snmp_request_id(packet) is None


class TestSnmpQueryProtocol:
    @pytest.mark.asyncio
    async def test_mismatched_request_id_keeps_waiting(self):
        loop = asyncio.get_running_loop()
        proto = _SNMPQueryProtocol(b"req", 42, loop)
        wrong = _build_raw_response(7, [(OIDS["sysName"], ber_encode_string("EVIL"))])
        proto.datagram_received(wrong, ("10.0.0.9", 161))
        assert not proto.response.done()

        good = _build_raw_response(42, [(OIDS["sysName"], ber_encode_string("Real"))])
        proto.datagram_received(good, ("10.0.0.9", 161))
        assert proto.response.result() == good


class TestSnmpTransportSecurity:
    @pytest.mark.asyncio
    async def test_query_device_end_to_end(self):
        """Full round trip over real UDP through the datagram endpoint."""
        agent = _FakeSnmpAgent(scalars=dict(_MIB2_SCALARS))
        async with _run_agent(agent) as port:
            with patch("openavc.discovery.snmp_scanner.SNMP_PORT", port):
                result = await SNMPScanner().query_device("127.0.0.1", timeout=2.0)
        assert result is not None
        assert result.sys_descr == "Acme Widget 9000, V2.0"
        assert result.sys_name == "Widget-Lab"

    @pytest.mark.asyncio
    async def test_wrong_request_id_datagram_ignored(self):
        """A datagram with a non-matching request-id must not be attributed
        to the query — the scanner keeps waiting for the real response."""
        agent = _FakeSnmpAgent(scalars=dict(_MIB2_SCALARS), wrong_id_first=True)
        async with _run_agent(agent) as port:
            with patch("openavc.discovery.snmp_scanner.SNMP_PORT", port):
                result = await SNMPScanner().query_device("127.0.0.1", timeout=2.0)
        assert result is not None
        assert result.sys_name == "Widget-Lab"
        assert "EVIL" not in result.sys_descr

    @pytest.mark.asyncio
    async def test_caller_timeout_honored(self):
        """The caller's timeout governs the wait — a reply after 2s (the old
        hard-coded socket timeout) still lands when the caller allows it."""
        agent = _FakeSnmpAgent(scalars=dict(_MIB2_SCALARS), reply_delay=2.6)
        async with _run_agent(agent) as port:
            with patch("openavc.discovery.snmp_scanner.SNMP_PORT", port):
                result = await SNMPScanner().query_device("127.0.0.1", timeout=4.0)
        assert result is not None
        assert result.sys_name == "Widget-Lab"

    @pytest.mark.asyncio
    async def test_silent_agent_times_out(self):
        agent = _FakeSnmpAgent(silent=True)
        async with _run_agent(agent) as port:
            with patch("openavc.discovery.snmp_scanner.SNMP_PORT", port):
                result = await SNMPScanner().query_device("127.0.0.1", timeout=0.3)
        assert result is None


class TestEntityMibIndexDiscovery:
    def _row(self, contained_in, mfg=None, model=None, serial=None):
        row = {ENT_PHYSICAL_CONTAINED_IN: ber_encode_integer(contained_in)}
        if mfg is not None:
            row[ENTITY_COLUMNS["entPhysicalMfgName"]] = ber_encode_string(mfg)
        if model is not None:
            row[ENTITY_COLUMNS["entPhysicalModelName"]] = ber_encode_string(model)
        if serial is not None:
            row[ENTITY_COLUMNS["entPhysicalSerialNum"]] = ber_encode_string(serial)
        return row

    async def _query(self, agent: _FakeSnmpAgent) -> SNMPInfo | None:
        async with _run_agent(agent) as port:
            with patch("openavc.discovery.snmp_scanner.SNMP_PORT", port):
                return await SNMPScanner().query_device(
                    "127.0.0.1", timeout=2.0, entity_mib=True)

    @pytest.mark.asyncio
    async def test_chassis_at_nonstandard_index(self):
        """Devices whose chassis entPhysicalIndex isn't 1 still yield
        manufacturer/model/serial."""
        agent = _FakeSnmpAgent(
            scalars=dict(_MIB2_SCALARS),
            entity_rows={1001: self._row(0, mfg="Acme Co", model="Widget 9000",
                                         serial="SN-99")},
        )
        result = await self._query(agent)
        assert result is not None
        assert result.entity_manufacturer == "Acme Co"
        assert result.entity_model == "Widget 9000"
        assert result.entity_serial == "SN-99"

    @pytest.mark.asyncio
    async def test_chassis_at_index_one(self):
        agent = _FakeSnmpAgent(
            scalars=dict(_MIB2_SCALARS),
            entity_rows={1: self._row(0, mfg="Acme Co", model="Widget 1")},
        )
        result = await self._query(agent)
        assert result is not None
        assert result.entity_model == "Widget 1"

    @pytest.mark.asyncio
    async def test_walks_past_contained_rows(self):
        """The chassis is the row with entPhysicalContainedIn == 0, not the
        first table row."""
        agent = _FakeSnmpAgent(
            scalars=dict(_MIB2_SCALARS),
            entity_rows={
                3: self._row(8, model="Module-X"),   # a module inside the chassis
                8: self._row(0, model="Chassis-Y"),  # the chassis itself
            },
        )
        result = await self._query(agent)
        assert result is not None
        assert result.entity_model == "Chassis-Y"

    @pytest.mark.asyncio
    async def test_no_entity_mib_graceful(self):
        """Agents without the Entity MIB still return MIB-II info; the
        scanner falls back to a single GET at index 1."""
        agent = _FakeSnmpAgent(scalars=dict(_MIB2_SCALARS))
        result = await self._query(agent)
        assert result is not None
        assert result.sys_name == "Widget-Lab"
        assert result.entity_model == ""
        # Fallback GET at index 1 was attempted
        get_oids = [oids for pdu, oids in agent.requests if pdu == SNMP_GET_REQUEST]
        assert any(f"{ENTITY_COLUMNS['entPhysicalModelName']}.1" in oids
                   for oids in get_oids)

    @pytest.mark.asyncio
    async def test_walk_is_bounded(self):
        """A huge entity table with no top-level row can't stall the scan."""
        rows = {i: self._row(1) for i in range(2, 102)}  # 100 rows, none top-level
        agent = _FakeSnmpAgent(scalars=dict(_MIB2_SCALARS), entity_rows=rows)
        result = await self._query(agent)
        assert result is not None
        assert result.entity_model == ""
        getnext_count = sum(1 for pdu, _ in agent.requests
                            if pdu == SNMP_GETNEXT_REQUEST)
        assert getnext_count <= ENTITY_WALK_LIMIT
