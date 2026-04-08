"""Tests for protocol probes (Chunk 2)."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from server.discovery.protocol_prober import (
    probe_banner,
    probe_pjlink,
    probe_samsung_mdc,
    probe_visca,
    probe_http,
    probe_crestron_cip,
    probe_device,
    probe_device as run_protocol_probes,
    _probe_banner_extron,
    _probe_banner_biamp,
    _probe_banner_qsc,
    _probe_banner_kramer,
    _probe_banner_shure,
    ProbeResult,
)


# ===== Banner Probe Tests =====


class TestExtronBannerProbe:
    def test_matches_standard_banner(self):
        banner = "(c) 2020 Extron Electronics DTP CrossPoint 84 4K IPCP SA V1.02.0000"
        result = _probe_banner_extron(banner)
        assert result is not None
        assert result.protocol == "extron_sis"
        assert result.manufacturer == "Extron"
        assert result.category == "switcher"

    def test_extracts_model(self):
        banner = "(c) 2020 Extron Electronics DTP CrossPoint 84 4K IPCP SA V1.02.0000"
        result = _probe_banner_extron(banner)
        assert result is not None
        assert result.model is not None
        assert "DTP" in result.model

    def test_extracts_firmware(self):
        banner = "(c) 2020 Extron Electronics DTP CrossPoint 84 V1.02.0000"
        result = _probe_banner_extron(banner)
        assert result is not None
        assert result.firmware is not None
        assert "1.02" in result.firmware

    def test_matches_copyright_symbol(self):
        banner = "\u00a9 2019 Extron Electronics IN1608"
        result = _probe_banner_extron(banner)
        assert result is not None
        assert result.manufacturer == "Extron"

    def test_no_match_on_other_banner(self):
        banner = "Welcome to Biamp Tesira"
        result = _probe_banner_extron(banner)
        assert result is None


class TestBiampBannerProbe:
    def test_matches_tesira_welcome(self):
        result = _probe_banner_biamp("Welcome to the Tesira Text Protocol 1.20")
        assert result is not None
        assert result.protocol == "biamp_tesira"
        assert result.manufacturer == "Biamp"
        assert result.category == "audio"

    def test_matches_tesira_hash(self):
        result = _probe_banner_biamp("#Tesira Text Protocol 1.20")
        assert result is not None
        assert result.manufacturer == "Biamp"

    def test_extracts_version(self):
        result = _probe_banner_biamp("Welcome to the Tesira Text Protocol 1.20")
        assert result is not None
        assert result.firmware == "1.20"

    def test_no_match(self):
        assert _probe_banner_biamp("Some other banner") is None


class TestQSCBannerProbe:
    def test_matches_qsc(self):
        result = _probe_banner_qsc("QSC Q-SYS Core 110f")
        assert result is not None
        assert result.manufacturer == "QSC"
        assert result.category == "audio"

    def test_extracts_model(self):
        result = _probe_banner_qsc("QSC Q-SYS Core 110f version 9.5")
        assert result is not None
        assert result.model is not None
        assert "Core 110f" in result.model

    def test_no_match(self):
        assert _probe_banner_qsc("Extron Electronics") is None


class TestKramerBannerProbe:
    def test_matches_welcome(self):
        result = _probe_banner_kramer("Welcome to Kramer P3K-1234")
        assert result is not None
        assert result.manufacturer == "Kramer"
        assert result.protocol == "kramer_p3000"

    def test_no_match(self):
        assert _probe_banner_kramer("Extron") is None


class TestShureBannerProbe:
    def test_matches_rep(self):
        result = _probe_banner_shure("< REP DEVICE_NAME MyMicrophone >")
        assert result is not None
        assert result.manufacturer == "Shure"
        assert result.category == "audio"

    def test_no_match(self):
        assert _probe_banner_shure("Biamp") is None


class TestProbeBannerDispatcher:
    def test_dispatches_extron(self):
        results = probe_banner("(c) 2020 Extron Electronics DTP CrossPoint 84 V1.02")
        assert len(results) >= 1
        assert results[0].manufacturer == "Extron"

    def test_dispatches_biamp(self):
        results = probe_banner("#Tesira Text Protocol 1.20")
        assert len(results) >= 1
        assert results[0].manufacturer == "Biamp"

    def test_returns_empty_for_unknown(self):
        assert probe_banner("Unknown device banner text") == []

    def test_returns_all_matches(self):
        # Extron should match
        results = probe_banner("(c) 2020 Extron Electronics IN1608 V1.00")
        assert len(results) >= 1
        assert results[0].manufacturer == "Extron"


# ===== PJLink Probe Tests =====


class TestPJLinkProbe:
    @pytest.mark.asyncio
    async def test_successful_probe(self):
        """Test PJLink probe with mocked TCP responses."""
        responses = [
            b"PJLINK 0\r",         # Greeting
            b"%1CLSS=1\r",         # Class 1
            b"%1INF1=NEC\r",       # Manufacturer
            b"%1INF2=PA1004UL\r",  # Product
            b"%1NAME=Room101\r",   # Name
            b"%1LAMP=12345 1\r",   # Lamp hours
        ]
        with patch(
            "server.discovery.protocol_prober._tcp_multi_exchange",
            new_callable=AsyncMock,
            return_value=responses,
        ):
            result = await probe_pjlink("192.168.1.72")

        assert result is not None
        assert result.protocol == "pjlink"
        assert result.manufacturer == "NEC"
        assert result.model == "PA1004UL"
        assert result.device_name == "Room101"
        assert result.category == "projector"
        assert result.extra.get("pjlink_class") == "1"
        assert result.extra.get("lamp_hours") == 12345

    @pytest.mark.asyncio
    async def test_auth_required_still_identifies(self):
        """PJLink with auth still gets identified as PJLink."""
        responses = [
            b"PJLINK 1 abcdef\r",  # Auth required
            b"%1CLSS=ERRA\r",      # Error (no auth)
            b"%1INF1=ERRA\r",
            b"%1INF2=ERRA\r",
            b"%1NAME=ERRA\r",
            b"%1LAMP=ERRA\r",
        ]
        with patch(
            "server.discovery.protocol_prober._tcp_multi_exchange",
            new_callable=AsyncMock,
            return_value=responses,
        ):
            result = await probe_pjlink("192.168.1.72")

        assert result is not None
        assert result.protocol == "pjlink"
        assert result.category == "projector"
        # Fields may be None since auth was required
        assert result.manufacturer is None  # ERRA is filtered

    @pytest.mark.asyncio
    async def test_no_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_multi_exchange",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await probe_pjlink("192.168.1.72")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_pjlink_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_multi_exchange",
            new_callable=AsyncMock,
            return_value=[b"Something else\r"],
        ):
            result = await probe_pjlink("192.168.1.72")
        assert result is None


# ===== Samsung MDC Probe Tests =====


class TestSamsungMDCProbe:
    @pytest.mark.asyncio
    async def test_successful_probe(self):
        """Samsung MDC ACK response identifies the device."""
        # Simulated ACK response: AA FF <id> <len> <ack> <cmd>
        response = bytes([0xAA, 0xFF, 0x01, 0x03, 0x41, 0x0B, 0x50])
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_samsung_mdc("192.168.1.80")

        assert result is not None
        assert result.protocol == "samsung_mdc"
        assert result.manufacturer == "Samsung"
        assert result.category == "display"

    @pytest.mark.asyncio
    async def test_no_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await probe_samsung_mdc("192.168.1.80")
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_header(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=b"\x00\x00\x00\x00",
        ):
            result = await probe_samsung_mdc("192.168.1.80")
        assert result is None


# ===== VISCA Probe Tests =====


class TestVISCAProbe:
    @pytest.mark.asyncio
    async def test_sony_camera(self):
        """VISCA version response with Sony vendor code."""
        # Response: 90 50 00 20 01 23 FF (vendor=0x0020=Sony, model=0x0123)
        response = bytes([0x90, 0x50, 0x00, 0x20, 0x01, 0x23, 0xFF])
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_visca("192.168.1.90")

        assert result is not None
        assert result.protocol == "visca"
        assert result.manufacturer == "Sony"
        assert result.category == "camera"

    @pytest.mark.asyncio
    async def test_no_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await probe_visca("192.168.1.90")
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_header(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=b"\x90\x60\x00\x00",
        ):
            result = await probe_visca("192.168.1.90")
        assert result is None


# ===== HTTP Probe Tests =====


class TestHTTPProbe:
    @pytest.mark.asyncio
    async def test_crestron_server_header(self):
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Server: Crestron/2.0\r\n"
            b"Content-Type: text/html\r\n\r\n"
            b"<html><title>Crestron</title></html>"
        )
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_http("192.168.1.60", 80)

        assert result is not None
        assert result.manufacturer == "Crestron"
        assert result.category == "control"

    @pytest.mark.asyncio
    async def test_panasonic_ptz_path(self):
        response = (
            b"HTTP/1.1 200 OK\r\n\r\n"
            b"<html><body>/cgi-bin/aw_ptz supported</body></html>"
        )
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_http("192.168.1.70", 80)

        assert result is not None
        assert result.manufacturer == "Panasonic"
        assert result.category == "camera"

    @pytest.mark.asyncio
    async def test_nec_projector_title(self):
        response = (
            b"HTTP/1.1 200 OK\r\n\r\n"
            b"<html><title>NEC PA1004UL Projector</title></html>"
        )
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_http("192.168.1.72", 80)

        assert result is not None
        assert result.manufacturer == "NEC"
        assert result.category == "projector"

    @pytest.mark.asyncio
    async def test_samsung_title(self):
        response = (
            b"HTTP/1.1 200 OK\r\n\r\n"
            b"<html><title>Samsung QM85R Display</title></html>"
        )
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_http("192.168.1.80", 80)

        assert result is not None
        assert result.manufacturer == "Samsung"
        assert result.category == "display"

    @pytest.mark.asyncio
    async def test_extracts_title_as_model(self):
        response = (
            b"HTTP/1.1 200 OK\r\n\r\n"
            b"<html><title>Barco ClickShare CSE-200</title></html>"
        )
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_http("192.168.1.85", 80)

        assert result is not None
        assert result.manufacturer == "Barco"
        assert result.extra.get("http_title") == "Barco ClickShare CSE-200"

    @pytest.mark.asyncio
    async def test_no_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await probe_http("192.168.1.1", 80)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_http_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=b"Not an HTTP response",
        ):
            result = await probe_http("192.168.1.1", 80)
        assert result is None

    @pytest.mark.asyncio
    async def test_generic_web_page_no_match(self):
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Server: nginx\r\n\r\n"
            b"<html><title>My Router Config</title></html>"
        )
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_http("192.168.1.1", 80)
        assert result is None


# ===== Crestron CIP Probe Tests =====


class TestCrestronCIPProbe:
    @pytest.mark.asyncio
    async def test_responds(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=b"\x01\x00\x00\x00",
        ):
            result = await probe_crestron_cip("192.168.1.60")

        assert result is not None
        assert result.manufacturer == "Crestron"
        assert result.protocol == "crestron_cip"

    @pytest.mark.asyncio
    async def test_no_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await probe_crestron_cip("192.168.1.60")
        assert result is None


# ===== Device Probe Dispatcher Tests =====


class TestProbeDevice:
    @pytest.mark.asyncio
    async def test_banner_only(self):
        """Probe with only banners, no open probe ports."""
        results = await probe_device(
            "192.168.1.50",
            open_ports=[23],
            banners={23: "(c) 2020 Extron Electronics DTP CrossPoint 84 V1.02"},
        )
        assert len(results) >= 1
        extron = next((r for r in results if r.manufacturer == "Extron"), None)
        assert extron is not None
        assert extron.protocol == "extron_sis"

    @pytest.mark.asyncio
    async def test_pjlink_port_triggers_probe(self):
        """Port 4352 should trigger a PJLink probe."""
        import server.discovery.protocol_prober as prober_mod
        mock_fn = AsyncMock(return_value=ProbeResult(
            protocol="pjlink", manufacturer="NEC", model="PA1004UL", category="projector",
        ))
        orig = prober_mod._PORT_PROBES[4352]
        prober_mod._PORT_PROBES[4352] = [mock_fn]
        try:
            results = await probe_device("192.168.1.72", open_ports=[4352])
        finally:
            prober_mod._PORT_PROBES[4352] = orig

        assert len(results) == 1
        assert results[0].protocol == "pjlink"
        assert results[0].manufacturer == "NEC"

    @pytest.mark.asyncio
    async def test_samsung_port_triggers_probe(self):
        """Port 1515 should trigger a Samsung MDC probe."""
        import server.discovery.protocol_prober as prober_mod
        mock_fn = AsyncMock(return_value=ProbeResult(
            protocol="samsung_mdc", manufacturer="Samsung", category="display",
        ))
        orig = prober_mod._PORT_PROBES[1515]
        prober_mod._PORT_PROBES[1515] = [mock_fn]
        try:
            results = await probe_device("192.168.1.80", open_ports=[1515])
        finally:
            prober_mod._PORT_PROBES[1515] = orig

        assert len(results) == 1
        assert results[0].protocol == "samsung_mdc"

    @pytest.mark.asyncio
    async def test_http_port_triggers_probe(self):
        """Port 80 should trigger an HTTP probe."""
        import server.discovery.protocol_prober as prober_mod
        mock_fn = AsyncMock(return_value=ProbeResult(
            protocol="nec_http", manufacturer="NEC", category="projector",
        ))
        orig = prober_mod._PORT_PROBES[80]
        prober_mod._PORT_PROBES[80] = [mock_fn]
        try:
            results = await probe_device("192.168.1.72", open_ports=[80])
        finally:
            prober_mod._PORT_PROBES[80] = orig

        assert len(results) == 1
        assert results[0].manufacturer == "NEC"

    @pytest.mark.asyncio
    async def test_multiple_ports_multiple_results(self):
        """Device with both PJLink and HTTP ports gets both probed."""
        import server.discovery.protocol_prober as prober_mod
        mock_pjlink = AsyncMock(return_value=ProbeResult(
            protocol="pjlink", manufacturer="NEC", category="projector",
        ))
        mock_http = AsyncMock(return_value=ProbeResult(
            protocol="nec_http", manufacturer="NEC", category="projector",
        ))
        orig_4352 = prober_mod._PORT_PROBES[4352]
        orig_80 = prober_mod._PORT_PROBES[80]
        prober_mod._PORT_PROBES[4352] = [mock_pjlink]
        prober_mod._PORT_PROBES[80] = [mock_http]
        try:
            results = await probe_device("192.168.1.72", open_ports=[4352, 80])
        finally:
            prober_mod._PORT_PROBES[4352] = orig_4352
            prober_mod._PORT_PROBES[80] = orig_80

        assert len(results) == 2
        protocols = {r.protocol for r in results}
        assert "pjlink" in protocols
        assert "nec_http" in protocols

    @pytest.mark.asyncio
    async def test_no_ports_no_banners(self):
        results = await probe_device("192.168.1.1", open_ports=[], banners=None)
        assert results == []

    @pytest.mark.asyncio
    async def test_probe_failure_is_handled(self):
        """Probe that throws an exception should not crash the dispatcher."""
        import server.discovery.protocol_prober as prober_mod
        mock_fn = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        orig = prober_mod._PORT_PROBES[4352]
        prober_mod._PORT_PROBES[4352] = [mock_fn]
        try:
            results = await probe_device("192.168.1.72", open_ports=[4352])
        finally:
            prober_mod._PORT_PROBES[4352] = orig
        # Should not crash, just return empty or skip the failed probe
        assert isinstance(results, list)


# ===== Integration Test: Engine with Protocol Probes =====


class TestEngineWithProbes:
    """Pipeline tests that mock passive scanners to avoid 10s listener timeouts."""

    def _mock_passive_scanners(self):
        """Context managers for mDNS, SSDP, SNMP, and hostname resolution."""
        mock_mdns_cls = patch("server.discovery.engine.MDNSScanner")
        mock_ssdp_cls = patch("server.discovery.engine.SSDPScanner")
        mock_snmp_cls = patch("server.discovery.engine.SNMPScanner")
        mock_hostnames = patch(
            "server.discovery.engine._resolve_hostnames",
            new_callable=AsyncMock, return_value={},
        )
        return mock_mdns_cls, mock_ssdp_cls, mock_snmp_cls, mock_hostnames

    def _configure_passive_mocks(self, mdns_cls, ssdp_cls, snmp_cls):
        mock_mdns = MagicMock()
        mock_mdns.start = AsyncMock(return_value={})
        mdns_cls.return_value = mock_mdns
        mock_ssdp = MagicMock()
        mock_ssdp.scan = AsyncMock(return_value={})
        ssdp_cls.return_value = mock_ssdp
        mock_snmp = MagicMock()
        mock_snmp.scan_devices = AsyncMock(return_value={})
        snmp_cls.return_value = mock_snmp

    @pytest.mark.asyncio
    async def test_pipeline_runs_protocol_probes(self):
        """Full pipeline test verifying protocol probes are executed."""
        from server.discovery.engine import DiscoveryEngine

        engine = DiscoveryEngine()

        with patch("server.discovery.engine.ping_sweep", new_callable=AsyncMock) as mock_ping, \
             patch("server.discovery.engine.harvest_arp_table", new_callable=AsyncMock) as mock_arp, \
             patch("server.discovery.engine.scan_host_ports", new_callable=AsyncMock) as mock_ports, \
             patch("server.discovery.engine.grab_banners", new_callable=AsyncMock) as mock_banners, \
             patch("server.discovery.engine.run_protocol_probes", new_callable=AsyncMock) as mock_probes, \
             patch("server.discovery.engine.MDNSScanner") as mdns_cls, \
             patch("server.discovery.engine.SSDPScanner") as ssdp_cls, \
             patch("server.discovery.engine.SNMPScanner") as snmp_cls, \
             patch("server.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}):

            self._configure_passive_mocks(mdns_cls, ssdp_cls, snmp_cls)
            mock_ping.return_value = ["192.168.1.72"]
            mock_arp.return_value = {"192.168.1.72": "04:fe:31:aa:bb:cc"}
            mock_ports.return_value = [4352, 80]
            mock_banners.return_value = {}
            mock_probes.return_value = [
                ProbeResult(
                    protocol="pjlink",
                    manufacturer="NEC",
                    model="PA1004UL",
                    device_name="Room101 Projector",
                    category="projector",
                ),
            ]

            await engine._scan_pipeline(["192.168.1.0/24"])

        device = engine.results.get("192.168.1.72")
        assert device is not None
        assert device.manufacturer == "NEC"
        assert device.model == "PA1004UL"
        assert device.device_name == "Room101 Projector"
        assert "pjlink" in device.protocols
        assert "probe_confirmed" in device.sources
        assert "model_known" in device.sources
        assert device.confidence > 0.3

    @pytest.mark.asyncio
    async def test_pipeline_banner_enriches_device(self):
        """Banners from port scan are passed to protocol probes."""
        from server.discovery.engine import DiscoveryEngine

        engine = DiscoveryEngine()

        with patch("server.discovery.engine.ping_sweep", new_callable=AsyncMock) as mock_ping, \
             patch("server.discovery.engine.harvest_arp_table", new_callable=AsyncMock) as mock_arp, \
             patch("server.discovery.engine.scan_host_ports", new_callable=AsyncMock) as mock_ports, \
             patch("server.discovery.engine.grab_banners", new_callable=AsyncMock) as mock_banners, \
             patch("server.discovery.engine.run_protocol_probes", wraps=run_protocol_probes), \
             patch("server.discovery.engine.MDNSScanner") as mdns_cls, \
             patch("server.discovery.engine.SSDPScanner") as ssdp_cls, \
             patch("server.discovery.engine.SNMPScanner") as snmp_cls, \
             patch("server.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}):

            self._configure_passive_mocks(mdns_cls, ssdp_cls, snmp_cls)
            mock_ping.return_value = ["192.168.1.50"]
            mock_arp.return_value = {"192.168.1.50": "00:05:a6:12:34:56"}
            mock_ports.return_value = [23]
            mock_banners.return_value = {
                23: "(c) 2020 Extron Electronics DTP CrossPoint 84 V1.02"
            }

            await engine._scan_pipeline(["192.168.1.0/24"])

        device = engine.results.get("192.168.1.50")
        assert device is not None
        assert device.manufacturer == "Extron"
        assert "extron_sis" in device.protocols
