"""A single-device scan (a /32) works like any other.

A /32's network and broadcast addresses are both the target, so the ARP
rescue used to skip it, and a target that dropped ping got no port scan, no
SNMP and no probes. A /31 or /32 also has no directed broadcast address, so
driver UDP probes were never sent. The rescue now leaves out network and
broadcast addresses only below /31, and UDP probes go unicast to each address
of a /31 or /32.
"""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

from openavc.discovery import icmp
from openavc.discovery.engine import (
    DiscoveryEngine,
    _host_count,
    _udp_probe_targets_for,
)
from openavc.discovery.result import Evidence, SignalTier
from openavc.discovery.scan_budget import ScanBudget, resolve_policy

TARGET = "10.77.0.9"


class TestHelpers:
    def test_udp_targets_broadcast_below_31_unicast_from_31(self):
        assert _udp_probe_targets_for(["10.77.0.0/24"]) == ["10.77.0.255"]
        assert _udp_probe_targets_for([f"{TARGET}/32"]) == [TARGET]
        assert _udp_probe_targets_for(["10.77.0.8/31"]) == ["10.77.0.8", "10.77.0.9"]
        assert _udp_probe_targets_for(["10.77.0.0/24", f"{TARGET}/32", "bogus"]) == [
            "10.77.0.255", TARGET,
        ]

    def test_host_count(self):
        assert _host_count(ipaddress.IPv4Network("10.77.0.0/24")) == 254
        assert _host_count(ipaddress.IPv4Network("10.77.0.8/31")) == 2
        assert _host_count(ipaddress.IPv4Network(f"{TARGET}/32")) == 1


def _instant(method: str):
    cls, inst = MagicMock(), MagicMock()
    setattr(inst, method, AsyncMock(return_value={}))
    inst.stop = AsyncMock()
    inst.results = {}
    inst.env_error = None
    cls.return_value = inst
    return cls


class TestSingleHostScan:
    async def test_a_32_scan_of_a_host_that_drops_ping_is_scanned_and_probed(self):
        engine = DiscoveryEngine()
        engine._budget = ScanBudget(resolve_policy("standard"), 300.0)
        engine.config["snmp_enabled"] = False
        engine.load_driver_hints_from_registry([{
            "id": "acme_widget",
            "name": "Acme Widget",
            "manufacturer": "Acme",
            "category": "audio",
            "transport": "tcp",
            "discovery": {
                "tcp_probe": {"port": 23, "expect_regex": "ACME"},
                "udp_probe": {"port": 6000, "send_hex": "00010203", "expect_regex": "^ACME"},
            },
        }])

        async def silent_sweep(subnets, **kwargs):
            # The sweep ran (it has a method) but the target never answered.
            kwargs["stats"].method = icmp.METHOD_EXEC
            return []

        scanned: list[str] = []

        async def fake_scan(ip, ports, timeout=1.0):
            scanned.append(ip)
            return [23]

        tcp_probed: list[str] = []

        async def fake_tcp(spec, target, **kwargs):
            tcp_probed.append(target)
            return Evidence(
                tier=SignalTier.ACTIVE_PROBE,
                source="probe:custom_acme_widget_tcp",
                data={"kind": "active_probe", "source_id": "custom_acme_widget_tcp"},
            )

        udp_targets: list[list[str]] = []

        async def fake_udp(spec, *, targets, **kwargs):
            udp_targets.append(list(targets))
            return {}

        subnets = [f"{TARGET}/32"]
        with patch("openavc.discovery.engine.ping_sweep", side_effect=silent_sweep), \
             patch("openavc.discovery.engine.harvest_arp_table", new_callable=AsyncMock,
                   return_value={TARGET: "00:11:22:33:44:55"}), \
             patch("openavc.discovery.engine.icmp.ping_host", new_callable=AsyncMock,
                   return_value=icmp.RESULT_TIMEOUT), \
             patch("openavc.discovery.engine._resolve_hostnames", new_callable=AsyncMock, return_value={}), \
             patch("openavc.discovery.engine.netbios_sweep", new_callable=AsyncMock, return_value={}), \
             patch("openavc.discovery.engine.scan_host_ports", side_effect=fake_scan), \
             patch("openavc.discovery.engine.grab_banners", new_callable=AsyncMock, return_value={}), \
             patch("openavc.discovery.engine.run_tcp_active_probe", side_effect=fake_tcp), \
             patch("openavc.discovery.engine.run_udp_broadcast_probe", side_effect=fake_udp), \
             patch.object(engine.community_index, "get_drivers", new_callable=AsyncMock, return_value=[]), \
             patch("openavc.discovery.engine.MDNSScanner", _instant("start")), \
             patch("openavc.discovery.engine.SSDPScanner", _instant("scan")), \
             patch("openavc.discovery.engine.AMXDDPScanner", _instant("start")):
            engine.scan_status.subnets = subnets
            await engine._scan_pipeline_inner(subnets)
            await engine._finalize_scan()

        assert engine.scan_status.total_hosts_scanned == 1
        assert scanned == [TARGET]
        assert tcp_probed == [TARGET]
        assert udp_targets == [[TARGET]]
        # It answered on a port, so it earned its place.
        assert TARGET in engine.results
        assert engine.results[TARGET].open_ports == [23]
