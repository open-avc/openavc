"""Tests for the AMX DDP / AMX Beacon listener.

DDP is a passive multicast protocol — the listener never sends. These
tests cover the parser (which is the load-bearing logic) plus a smoke
test of the scanner lifecycle with a mocked socket.
"""

from unittest.mock import patch

import pytest

from server.discovery.amx_ddp_scanner import (
    AMXDDPScanner,
    DDPBeacon,
    parse_ddp_beacon,
)
from server.discovery.result import SignalTier


# ===== Parser =====


class TestParseDDPBeacon:
    def test_polycom_soundstructure(self):
        data = (
            b"AMXB<-UUID=001122334455>"
            b"<-SDKClass=AudioConferencer>"
            b"<-Make=Polycom>"
            b"<-Model=SoundStructureC16>"
            b"<-Revision=1.0.0>"
        )
        b = parse_ddp_beacon(data, "192.168.1.50")

        assert b is not None
        assert b.make == "Polycom"
        assert b.model == "SoundStructureC16"
        assert b.revision == "1.0.0"
        assert b.uuid == "001122334455"
        assert b.sdk_class == "AudioConferencer"
        assert b.ip == "192.168.1.50"

    def test_epson_projector(self):
        # Epson PowerLite projectors emit a DDP beacon when AMX Device Discovery is enabled.
        data = (
            b"AMXB<-UUID=00abcd112233>"
            b"<-SDKClass=VideoProjector>"
            b"<-Make=Epson>"
            b"<-Model=EB-1485Fi>"
            b"<-Revision=1.20>"
            b"<Config-Name=Conf Room A>"
            b"<Config-URL=http://192.168.1.72/>"
        )
        b = parse_ddp_beacon(data, "192.168.1.72")

        assert b is not None
        assert b.make == "Epson"
        assert b.model == "EB-1485Fi"
        assert b.config_name == "Conf Room A"
        assert b.config_url == "http://192.168.1.72/"

    def test_optional_dash_prefix(self):
        # Some firmware sends tags without the leading dash; parser must accept both.
        data = (
            b"AMXB<UUID=AAAA>"
            b"<Make=Sony>"
            b"<Model=BRAVIA>"
        )
        b = parse_ddp_beacon(data, "10.0.0.5")
        assert b is not None
        assert b.make == "Sony"
        assert b.model == "BRAVIA"
        assert b.uuid == "AAAA"

    def test_unicode_in_config_name(self):
        # Config-Name is user-set and may be UTF-8.
        data = (
            "AMXB<-Make=Sharp><-Model=4K-NEC><-Config-Name=Salle de Réunion>"
        ).encode("utf-8")
        b = parse_ddp_beacon(data, "10.0.0.6")
        assert b is not None
        assert b.config_name == "Salle de Réunion"

    def test_returns_none_without_magic(self):
        # Random multicast traffic must not match.
        assert parse_ddp_beacon(b"AAAA<Make=Foo>", "10.0.0.1") is None
        assert parse_ddp_beacon(b"", "10.0.0.1") is None
        assert parse_ddp_beacon(b"hello world", "10.0.0.1") is None

    def test_magic_only_returns_none(self):
        # Magic with no tags is not actionable; return None so the caller
        # can ignore rather than emit a useless evidence record.
        assert parse_ddp_beacon(b"AMXB", "10.0.0.1") is None
        assert parse_ddp_beacon(b"AMXB<>", "10.0.0.1") is None

    def test_partial_garbage_after_valid_tags(self):
        # If the device emits valid tags followed by garbage, we still parse the tags.
        data = b"AMXB<-Make=Epson><-Model=PL725>\x00\xff\xfe"
        b = parse_ddp_beacon(data, "10.0.0.7")
        assert b is not None
        assert b.make == "Epson"
        assert b.model == "PL725"


class TestDDPBeaconAccessors:
    def test_to_device_info_full(self):
        b = DDPBeacon(
            ip="10.0.0.5",
            raw="...",
            fields={
                "Make": "Polycom",
                "Model": "SoundStructureC16",
                "Revision": "1.0.0",
                "UUID": "001122334455",
                "Config-Name": "Boardroom",
            },
        )
        info = b.to_device_info()
        assert info["manufacturer"] == "Polycom"
        assert info["model"] == "SoundStructureC16"
        assert info["firmware"] == "1.0.0"
        assert info["serial_number"] == "001122334455"
        assert info["device_name"] == "Boardroom"

    def test_to_device_info_partial(self):
        b = DDPBeacon(
            ip="10.0.0.5",
            raw="...",
            fields={"Make": "Epson", "Model": "PL725"},
        )
        info = b.to_device_info()
        assert info == {"manufacturer": "Epson", "model": "PL725"}

    def test_to_evidence_emits_tier1(self):
        b = DDPBeacon(
            ip="10.0.0.5",
            raw="AMXB<-Make=Polycom><-Model=SSC16>",
            fields={"Make": "Polycom", "Model": "SSC16"},
        )
        ev = b.to_evidence()
        assert ev.tier == SignalTier.PASSIVE_LISTENER
        assert ev.source == "amx_ddp:Polycom/SSC16"
        assert ev.data["make"] == "Polycom"
        assert ev.data["model"] == "SSC16"
        assert ev.data["fields"]["Make"] == "Polycom"


# ===== Scanner lifecycle =====


class TestAMXDDPScannerLifecycle:
    @pytest.mark.asyncio
    async def test_socket_creation_failure_is_handled(self):
        scanner = AMXDDPScanner()
        with patch(
            "server.discovery.amx_ddp_scanner._create_ddp_socket",
            side_effect=OSError("permission denied"),
        ):
            results = await scanner.start(duration=0.1)
        assert results == {}

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self):
        scanner = AMXDDPScanner()
        scanner._running = True
        await scanner.stop()
        assert scanner._running is False

    @pytest.mark.asyncio
    async def test_results_property_returns_copy(self):
        scanner = AMXDDPScanner()
        scanner._results["10.0.0.1"] = DDPBeacon(
            ip="10.0.0.1", raw="x", fields={"Make": "X"},
        )
        results = scanner.results
        results.clear()
        # The internal dict should be untouched.
        assert "10.0.0.1" in scanner._results

    def test_constructor_accepts_control_ip(self):
        scanner = AMXDDPScanner(control_ip="192.168.1.50")
        assert scanner._control_ip == "192.168.1.50"

    def test_constructor_default_no_control_ip(self):
        scanner = AMXDDPScanner()
        assert scanner._control_ip == ""


class TestParserRobustness:
    def test_handles_empty_value(self):
        b = parse_ddp_beacon(b"AMXB<-Make=><-Model=X>", "10.0.0.1")
        assert b is not None
        # Empty value is preserved as empty string; falsy check filters it
        # in to_device_info.
        assert b.fields["Make"] == ""
        assert b.model == "X"
        info = b.to_device_info()
        # Empty Make should not produce a manufacturer field.
        assert "manufacturer" not in info

    def test_ignores_malformed_tag(self):
        # Tag without '=' is ignored by the regex.
        b = parse_ddp_beacon(b"AMXB<NoEquals><Make=Sony>", "10.0.0.1")
        assert b is not None
        assert b.make == "Sony"
        assert "NoEquals" not in b.fields

    def test_does_not_trip_on_long_payload(self):
        # Some firmware emits very long Config-URL with embedded > chars
        # would break things; verify our regex stops at first '>'.
        data = (
            b"AMXB<-Make=Test>"
            b"<-Model=ModelA>"
            b"<-Config-URL=http://example.com/path?x=1>"
            b"<-Revision=1.0>"
        )
        b = parse_ddp_beacon(data, "10.0.0.1")
        assert b is not None
        assert b.config_url == "http://example.com/path?x=1"
        assert b.revision == "1.0"
