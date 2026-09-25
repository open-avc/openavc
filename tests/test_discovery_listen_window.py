"""The passive listeners listen through the collect window.

Phase 7 used to stop the mDNS, SSDP and AMX DDP listeners first and then
"wait" for announcements nothing could hear any more, so the window the
budget grants went unlistened and a beacon sent every 30 to 60 seconds was
caught only if it landed during phases 3 to 6. The listeners now keep
running for the whole window and are stopped at its end.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from unittest.mock import AsyncMock, MagicMock, patch

from openavc.discovery import engine as engine_mod
from openavc.discovery.amx_ddp_scanner import AMXDDPScanner
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.scan_budget import ScanBudget, resolve_policy

BEACON_IP = "10.77.0.40"
BEACON = b"AMXB<-UUID=0011aabb><-SDKClass=Widget><-Make=Acme><-Model=Widget 3000>"


class LateBeaconListener(AMXDDPScanner):
    """An AMX DDP listener whose one device beacons only after phase 7 began."""

    def __init__(self, engine: DiscoveryEngine, control_ip: str = "") -> None:
        super().__init__(control_ip=control_ip)
        self.engine = engine
        self.stopped_at_phase: str | None = None

    async def start(self, duration: float = 30.0):
        self._results.clear()
        self._running = True
        collect_began = None
        while self._running:
            await asyncio.sleep(0.01)
            if self.engine.scan_status.phase != "passive_collect":
                continue
            collect_began = collect_began or time.monotonic()
            if time.monotonic() - collect_began >= 0.05 and BEACON_IP not in self._results:
                self._handle_datagram(BEACON, BEACON_IP)
        return dict(self._results)

    async def stop(self) -> None:
        self.stopped_at_phase = self.engine.scan_status.phase
        await super().stop()


def _instant_listener(method: str):
    cls, inst = MagicMock(), MagicMock()
    setattr(inst, method, AsyncMock(return_value={}))
    inst.results = {}
    inst.env_error = None
    cls.return_value = inst
    return cls


class TestListenWindow:
    def setup_method(self):
        self._tick = engine_mod._PASSIVE_COLLECT_TICK_SECONDS
        engine_mod._PASSIVE_COLLECT_TICK_SECONDS = 0.01

    def teardown_method(self):
        engine_mod._PASSIVE_COLLECT_TICK_SECONDS = self._tick

    async def test_announcement_sent_during_the_window_reaches_the_evidence(self):
        engine = DiscoveryEngine()
        policy = dataclasses.replace(resolve_policy("quick"), passive_collect_seconds=0.3)
        engine._budget = ScanBudget(policy, 60.0)
        listener = LateBeaconListener(engine)

        with patch("openavc.discovery.engine.ping_sweep", new_callable=AsyncMock, return_value=[]), \
             patch("openavc.discovery.engine.harvest_arp_table", new_callable=AsyncMock, return_value={}), \
             patch("openavc.discovery.engine.scan_host_ports", new_callable=AsyncMock, return_value=[]), \
             patch.object(engine.community_index, "get_drivers", new_callable=AsyncMock, return_value=[]), \
             patch("openavc.discovery.engine.MDNSScanner", _instant_listener("start")), \
             patch("openavc.discovery.engine.SSDPScanner", _instant_listener("scan")), \
             patch("openavc.discovery.engine.AMXDDPScanner", lambda control_ip="": listener):
            engine.scan_status.subnets = ["10.77.0.0/24"]
            started = time.monotonic()
            await engine._scan_pipeline_inner(["10.77.0.0/24"])
            took = time.monotonic() - started

        device = engine.results.get(BEACON_IP)
        assert device is not None, "a beacon sent inside the window was never heard"
        assert any(ev.source == "amx_ddp:Acme/Widget 3000" for ev in device.evidence_log)
        # Stopped at the end of the collect window, and the window was spent.
        assert listener.stopped_at_phase == "passive_collect"
        assert took >= 0.3

    async def test_window_ends_early_when_no_listener_is_running(self):
        """Nothing left to hear anything: the window does not wait for nothing."""
        engine = DiscoveryEngine()
        done = [asyncio.create_task(asyncio.sleep(0)) for _ in range(3)]
        await asyncio.sleep(0)
        started = time.monotonic()
        await engine._collect_passive_results(*done, wait_seconds=5.0)
        assert time.monotonic() - started < 1.0
