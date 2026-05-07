"""Tests for the ONVIF WS-Discovery probe.

The probe itself requires multicast UDP, so the integration paths are
exercised in Phase 8 with the simulator. These tests cover the parser
(load-bearing logic), scope-URI extraction, the Probe envelope builder,
and Evidence emission.
"""

import pytest

from server.discovery.onvif_scanner import (
    ONVIFResult,
    WSD_GROUP,
    WSD_PORT,
    _build_probe_envelope,
    _parse_probe_match,
    probe_onvif,
)
from server.discovery.result import SignalTier
from server.discovery.tier_matcher import KIND_BROADCAST


# ===== Probe envelope =====


class TestProbeEnvelope:
    def test_contains_required_soap_action(self):
        env = _build_probe_envelope()
        assert b"http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe" in env

    def test_contains_message_id_uuid(self):
        env = _build_probe_envelope().decode("utf-8")
        assert "urn:uuid:" in env

    def test_each_call_has_unique_message_id(self):
        # Spec compliance: stricter ONVIF firmware rejects duplicates.
        a = _build_probe_envelope().decode("utf-8")
        b = _build_probe_envelope().decode("utf-8")
        assert a != b

    def test_filters_to_network_video_transmitter(self):
        # Avoid matching every WS-Discovery responder (printers, NAS).
        env = _build_probe_envelope()
        assert b"NetworkVideoTransmitter" in env

    def test_constants(self):
        assert WSD_GROUP == "239.255.255.250"
        assert WSD_PORT == 3702


# ===== ProbeMatch parser =====


_AXIS_PROBE_MATCH = b"""<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
              xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <env:Header>
    <wsa:MessageID>urn:uuid:11111111-1111-1111-1111-111111111111</wsa:MessageID>
    <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</wsa:Action>
  </env:Header>
  <env:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <wsa:EndpointReference>
          <wsa:Address>urn:uuid:cafebabe-1234-5678-9abc-001122334455</wsa:Address>
        </wsa:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter tds:Device</d:Types>
        <d:Scopes>onvif://www.onvif.org/type/Network_Video_Transmitter
          onvif://www.onvif.org/Profile/Streaming
          onvif://www.onvif.org/Profile/G
          onvif://www.onvif.org/manufacturer/AXIS
          onvif://www.onvif.org/hardware/M3045-V
          onvif://www.onvif.org/name/AXIS%20M3045-V
          onvif://www.onvif.org/location/Lobby</d:Scopes>
        <d:XAddrs>http://192.168.1.20/onvif/device_service</d:XAddrs>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </env:Body>
</env:Envelope>"""


_HIKVISION_PROBE_MATCH = b"""<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
              xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
              xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <env:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <d:Types>dn:NetworkVideoTransmitter</d:Types>
        <d:Scopes>onvif://www.onvif.org/manufacturer/HIKVISION
          onvif://www.onvif.org/hardware/DS-2CD2042WD-I
          onvif://www.onvif.org/Profile/Streaming</d:Scopes>
        <d:XAddrs>http://192.168.1.21/onvif/device_service</d:XAddrs>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </env:Body>
</env:Envelope>"""


class TestParseProbeMatch:
    def test_axis_camera(self):
        result = _parse_probe_match(_AXIS_PROBE_MATCH, "192.168.1.20")
        assert result is not None
        assert result.ip == "192.168.1.20"
        assert result.manufacturer == "AXIS"
        assert result.hardware == "M3045-V"
        assert result.location == "Lobby"
        assert result.endpoint_reference == "urn:uuid:cafebabe-1234-5678-9abc-001122334455"
        assert "http://192.168.1.20/onvif/device_service" in result.xaddrs

    def test_hikvision_minimal(self):
        # No EndpointReference, no name. Common on cheaper firmware.
        result = _parse_probe_match(_HIKVISION_PROBE_MATCH, "192.168.1.21")
        assert result is not None
        assert result.manufacturer == "HIKVISION"
        assert result.hardware == "DS-2CD2042WD-I"
        assert result.location is None
        assert result.endpoint_reference == ""

    def test_returns_none_for_invalid_xml(self):
        for bad in (b"", b"not xml", b"<incomplete>"):
            assert _parse_probe_match(bad, "10.0.0.1") is None

    def test_returns_none_for_non_envelope(self):
        bad = b"<?xml version='1.0'?><foo>bar</foo>"
        assert _parse_probe_match(bad, "10.0.0.1") is None

    def test_returns_none_for_empty_probe_match(self):
        # ProbeMatch with no scopes and no xaddrs is not actionable.
        empty = b"""<?xml version="1.0"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <e:Body>
    <d:ProbeMatches>
      <d:ProbeMatch/>
    </d:ProbeMatches>
  </e:Body>
</e:Envelope>"""
        assert _parse_probe_match(empty, "10.0.0.1") is None


class TestScopeExtraction:
    def test_url_decoded_values_left_alone(self):
        # %20 in name should NOT be auto-decoded - keeps the matcher
        # honest and lets driver hints decide normalization.
        result = _parse_probe_match(_AXIS_PROBE_MATCH, "192.168.1.20")
        assert result.name == "AXIS%20M3045-V"

    def test_unknown_category_returns_none(self):
        result = ONVIFResult(
            ip="10.0.0.1",
            scopes=["onvif://www.onvif.org/manufacturer/Test"],
        )
        assert result._scope_value("nonexistent") is None

    def test_case_insensitive_category(self):
        result = ONVIFResult(
            ip="10.0.0.1",
            scopes=["onvif://www.onvif.org/Manufacturer/Bosch"],
        )
        assert result.manufacturer == "Bosch"


class TestONVIFResultDeviceInfo:
    def test_axis_device_info(self):
        result = _parse_probe_match(_AXIS_PROBE_MATCH, "192.168.1.20")
        info = result.to_device_info()
        assert info["manufacturer"] == "AXIS"
        assert info["model"] == "M3045-V"
        assert info["category"] == "camera"
        assert info["protocols"] == ["onvif"]
        assert info["serial_number"].startswith("urn:uuid:")

    def test_hikvision_device_info_no_category_without_type(self):
        # Hikvision response has type but it is NetworkVideoTransmitter.
        result = _parse_probe_match(_HIKVISION_PROBE_MATCH, "192.168.1.21")
        info = result.to_device_info()
        assert info.get("category") == "camera"


class TestEvidence:
    def test_emits_tier2_broadcast_evidence(self):
        result = _parse_probe_match(_AXIS_PROBE_MATCH, "192.168.1.20")
        ev = result.to_evidence()
        assert ev.tier == SignalTier.BROADCAST_PROBE
        assert ev.source == "broadcast:onvif"
        assert ev.data["kind"] == KIND_BROADCAST
        assert ev.data["source_id"] == "onvif"
        assert ev.data["manufacturer"] == "AXIS"
        assert ev.data["hardware"] == "M3045-V"
        assert "scopes" in ev.data


# ===== Smoke =====


@pytest.mark.asyncio
class TestProbeSmoke:
    async def test_returns_empty_when_socket_fails(self, monkeypatch):
        # Force the socket factory to fail; probe should return {} not raise.
        from server.discovery import onvif_scanner

        monkeypatch.setattr(
            onvif_scanner, "_make_onvif_socket", lambda control_ip="": None,
        )
        result = await probe_onvif(duration=0.1)
        assert result == {}
