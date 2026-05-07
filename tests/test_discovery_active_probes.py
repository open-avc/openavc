"""Tests for the new Tier 3 active probes added in the discovery redesign:

- Q-SYS QRC (TCP 1710, JSON-RPC EngineStatus)
- Biamp Tesira TTP (TCP 23, DEVICE get serialNumber)
- Yamaha RCP (TCP 49280, devstatus runmode)

Plus the ``probe_result_to_evidence`` bridge that converts legacy
ProbeResult records into Tier 3 Evidence records consumable by the
deterministic matcher.
"""

from unittest.mock import AsyncMock, patch

import pytest

from server.discovery.protocol_prober import (
    ProbeResult,
    probe_qsys_qrc,
    probe_result_to_evidence,
    probe_tesira_ttp,
    probe_yamaha_rcp,
    _PORT_PROBES,
)
from server.discovery.result import SignalTier
from server.discovery.tier_matcher import KIND_ACTIVE_PROBE


# ===== Q-SYS QRC =====


class TestQSYSQRCProbe:
    @pytest.mark.asyncio
    async def test_identifies_core_110f(self):
        response = (
            b'{"jsonrpc":"2.0","id":1,"result":{'
            b'"Platform":"Core 110f",'
            b'"State":"Active",'
            b'"DesignName":"Boardroom Audio",'
            b'"DesignCode":"abc123",'
            b'"IsRedundant":false,'
            b'"IsEmulator":false}}\x00'
        )
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_qsys_qrc("192.168.1.50")

        assert result is not None
        assert result.protocol == "qsc_qrc"
        assert result.manufacturer == "QSC"
        assert result.category == "audio"
        assert result.model == "Core 110f"
        assert result.device_name == "Boardroom Audio"
        assert result.extra["qrc_state"] == "Active"
        assert result.extra["qrc_redundant"] is False

    @pytest.mark.asyncio
    async def test_returns_none_on_no_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await probe_qsys_qrc("192.168.1.50")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=b"not json at all",
        ):
            result = await probe_qsys_qrc("192.168.1.50")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_jsonrpc_error(self):
        # JSON-RPC error response (no result object) means we got a
        # non-Q-SYS service speaking JSON on this port.
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=b'{"jsonrpc":"2.0","id":1,"error":{"code":-32601}}\x00',
        ):
            result = await probe_qsys_qrc("192.168.1.50")
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_missing_optional_fields(self):
        # Older QRC firmware may not include IsRedundant.
        response = (
            b'{"jsonrpc":"2.0","id":1,"result":{'
            b'"Platform":"Core 510i",'
            b'"State":"Active",'
            b'"DesignName":""}}\x00'
        )
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=response,
        ):
            result = await probe_qsys_qrc("192.168.1.50")
        assert result is not None
        assert result.model == "Core 510i"
        # Empty design_name should not be set.
        assert result.device_name is None
        assert "qrc_redundant" not in result.extra


class TestQSYSQRCDispatch:
    def test_qsys_registered_on_port_1710(self):
        assert 1710 in _PORT_PROBES
        assert probe_qsys_qrc in _PORT_PROBES[1710]


# ===== Biamp Tesira TTP =====


class TestTesiraTTPProbe:
    @pytest.mark.asyncio
    async def test_identifies_via_banner(self):
        # Tesira greets with a recognizable banner. probe is configured
        # with read_first=True so the banner shows up in responses[0].
        async def fake_multi(*args, **kwargs):
            return [
                b"Welcome to the Tesira Text Protocol Server, version 4.5.1.234\r\n",
                b'+OK "value:TS123-AB456"\r\n',
            ]

        with patch(
            "server.discovery.protocol_prober._tcp_multi_exchange",
            new=fake_multi,
        ):
            result = await probe_tesira_ttp("192.168.1.50")

        assert result is not None
        assert result.protocol == "biamp_tesira"
        assert result.manufacturer == "Biamp"
        assert result.category == "audio"
        assert result.serial_number == "TS123-AB456"
        assert result.firmware == "4.5.1.234"

    @pytest.mark.asyncio
    async def test_returns_none_when_banner_does_not_match(self):
        async def fake_multi(*args, **kwargs):
            return [b"some other telnet greeting\r\n", b"+OK \"value:xx\""]

        with patch(
            "server.discovery.protocol_prober._tcp_multi_exchange",
            new=fake_multi,
        ):
            result = await probe_tesira_ttp("192.168.1.50")
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_missing_serial(self):
        # Some firmware refuses unauthenticated DEVICE commands but still
        # greets with the Tesira banner. We accept the identification
        # without a serial.
        async def fake_multi(*args, **kwargs):
            return [
                b"Welcome to the Tesira Text Protocol Server\r\n",
                b"-ERR \"address: NOT_AVAILABLE\"\r\n",
            ]

        with patch(
            "server.discovery.protocol_prober._tcp_multi_exchange",
            new=fake_multi,
        ):
            result = await probe_tesira_ttp("192.168.1.50")
        assert result is not None
        assert result.manufacturer == "Biamp"
        assert result.serial_number is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_data(self):
        async def fake_multi(*args, **kwargs):
            return []

        with patch(
            "server.discovery.protocol_prober._tcp_multi_exchange",
            new=fake_multi,
        ):
            result = await probe_tesira_ttp("192.168.1.50")
        assert result is None


class TestTesiraTTPDispatch:
    def test_tesira_registered_on_port_23(self):
        assert 23 in _PORT_PROBES
        assert probe_tesira_ttp in _PORT_PROBES[23]


# ===== Yamaha RCP =====


class TestYamahaRCPProbe:
    @pytest.mark.asyncio
    async def test_identifies_dm3(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=b"OK devstatus runmode normal\r\n",
        ):
            result = await probe_yamaha_rcp("192.168.1.50")

        assert result is not None
        assert result.protocol == "yamaha_rcp"
        assert result.manufacturer == "Yamaha"
        assert result.category == "audio"
        assert result.extra["rcp_runmode"] == "normal"

    @pytest.mark.asyncio
    async def test_returns_none_on_no_response(self):
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await probe_yamaha_rcp("192.168.1.50")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_unrelated_response(self):
        # Some other service answering on 49280 - banner unrelated.
        with patch(
            "server.discovery.protocol_prober._tcp_exchange",
            new_callable=AsyncMock,
            return_value=b"HTTP/1.1 404 Not Found\r\n\r\n",
        ):
            result = await probe_yamaha_rcp("192.168.1.50")
        assert result is None


class TestYamahaRCPDispatch:
    def test_yamaha_registered_on_port_49280(self):
        assert 49280 in _PORT_PROBES
        assert probe_yamaha_rcp in _PORT_PROBES[49280]


# ===== ProbeResult -> Evidence bridge =====


class TestProbeResultToEvidence:
    def test_pjlink_emits_active_probe_evidence(self):
        pr = ProbeResult(
            protocol="pjlink",
            manufacturer="NEC",
            model="PA1004UL",
            device_name="Room 101",
            firmware="1.02",
            category="projector",
        )
        ev = probe_result_to_evidence(pr)
        assert ev.tier == SignalTier.ACTIVE_PROBE
        assert ev.source == "probe:pjlink_class1"
        assert ev.data["kind"] == KIND_ACTIVE_PROBE
        assert ev.data["source_id"] == "pjlink_class1"
        assert ev.data["response"]["manufacturer"] == "NEC"
        assert ev.data["response"]["model"] == "PA1004UL"

    def test_qsc_qrc_uses_qrc_probe_id(self):
        pr = ProbeResult(protocol="qsc_qrc", manufacturer="QSC", model="Core 110f")
        ev = probe_result_to_evidence(pr)
        assert ev.source == "probe:qrc"
        assert ev.data["source_id"] == "qrc"

    def test_tesira_uses_tesira_ttp_probe_id(self):
        pr = ProbeResult(protocol="biamp_tesira", manufacturer="Biamp")
        ev = probe_result_to_evidence(pr)
        assert ev.source == "probe:tesira_ttp"

    def test_yamaha_rcp_probe_id(self):
        pr = ProbeResult(protocol="yamaha_rcp", manufacturer="Yamaha")
        ev = probe_result_to_evidence(pr)
        assert ev.source == "probe:yamaha_rcp"

    def test_unknown_protocol_uses_protocol_as_id(self):
        # Falls back to using the protocol string as the probe_id when
        # the protocol isn't in our hardcoded map.
        pr = ProbeResult(protocol="some_new_proto", manufacturer="Foo")
        ev = probe_result_to_evidence(pr)
        assert ev.source == "probe:some_new_proto"

    def test_extra_carries_through(self):
        pr = ProbeResult(
            protocol="pjlink",
            manufacturer="NEC",
            extra={"lamp_hours": 12345, "pjlink_class": "2"},
        )
        ev = probe_result_to_evidence(pr)
        assert ev.data["response"]["extra"]["lamp_hours"] == 12345
        assert ev.data["response"]["extra"]["pjlink_class"] == "2"


# ===== Sanity: existing probes still registered =====


class TestExistingProbesPreserved:
    """Adding new Tier 3 probes must not break the existing dispatch table."""

    def test_pjlink_still_on_4352(self):
        from server.discovery.protocol_prober import probe_pjlink
        assert 4352 in _PORT_PROBES
        assert probe_pjlink in _PORT_PROBES[4352]

    def test_samsung_mdc_still_on_1515(self):
        from server.discovery.protocol_prober import probe_samsung_mdc
        assert 1515 in _PORT_PROBES
        assert probe_samsung_mdc in _PORT_PROBES[1515]

    def test_visca_still_on_10500(self):
        from server.discovery.protocol_prober import probe_visca
        assert 10500 in _PORT_PROBES
        assert probe_visca in _PORT_PROBES[10500]

    def test_crestron_cip_still_on_1688(self):
        from server.discovery.protocol_prober import probe_crestron_cip
        assert 1688 in _PORT_PROBES
        assert probe_crestron_cip in _PORT_PROBES[1688]

    def test_shure_active_still_on_23(self):
        from server.discovery.protocol_prober import probe_shure_active
        assert 23 in _PORT_PROBES
        assert probe_shure_active in _PORT_PROBES[23]
