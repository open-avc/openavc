"""Phase 6 and 7's timings and words: the entity MIB setting SNMP is asked
with, how long the SNMP scan is waited for (the figure the phase budget
counts), and the sentence a collect window that overran its budget adds."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from openavc.discovery import scan_budget as budgeting
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.passive_phase import BackgroundScans
from openavc.discovery.port_phase import PortScanSettings
from openavc.discovery.scan_budget import PassiveCollectPlan, ScanBudget, resolve_policy

pytestmark = pytest.mark.asyncio


def _engine(depth: str) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.config["scan_depth"] = depth
    engine._budget = ScanBudget(resolve_policy(depth, False), 300.0)
    return engine


@pytest.mark.parametrize("depth, entity_mib", [("quick", False), ("thorough", True)])
async def test_snmp_reads_the_entity_mib_only_when_the_depth_says_to(depth, entity_mib):
    engine = _engine(depth)
    settings = engine._snmp_launch_settings("10.77.0.9", gentle=False)
    assert settings.entity_mib is entity_mib

    scanner = MagicMock()
    scanner.scan_devices = MagicMock(return_value=asyncio.sleep(0, result={}))
    background = BackgroundScans()
    with patch("openavc.discovery.passive_phase.SNMPScanner", return_value=scanner):
        await engine._launch_snmp(background, ["10.77.0.5"], settings)
        await background.snmp
    assert scanner.scan_devices.call_args.kwargs["entity_mib"] is entity_mib


async def test_the_snmp_scan_is_waited_for_the_seconds_the_budget_counts():
    engine = DiscoveryEngine()
    task = asyncio.create_task(asyncio.sleep(30))
    seen: list[float | None] = []
    real_wait = asyncio.wait

    async def recording_wait(tasks, timeout=None, **kwargs):
        seen.append(timeout)
        return await real_wait(tasks, timeout=0)

    with patch("openavc.discovery.passive_phase.asyncio.wait", side_effect=recording_wait):
        await engine._collect_snmp_results(task, None)

    assert seen == [5.0]
    assert seen == [budgeting.SNMP_COLLECT_SECONDS]
    assert task.cancelled()


async def test_a_collect_window_that_overran_says_so_and_narrows_the_scan():
    engine = _engine("standard")

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    with patch.object(budgeting, "plan_passive_collect",
                      return_value=PassiveCollectPlan(0.05, 0.1)), \
         patch.object(engine, "_collect_phase", side_effect=slow):
        await engine._passive_collect_phase(
            BackgroundScans(), ["10.77.0.5"], PortScanSettings([], 1.0, 4),
        )

    assert engine.scan_status.warnings == [
        "Collecting device announcements ran out of its share of the "
        "scan time. Some announcements may be missing."
    ]
    assert engine._narrowed is True
    assert engine.scan_status.phase == "passive_collect"
    assert engine.scan_status.phase_number == 7


async def test_the_collect_window_gets_the_wait_its_plan_granted():
    engine = _engine("standard")
    seen: list[tuple] = []

    async def record(background, wait, alive_ips, followup):
        seen.append((wait, list(alive_ips), followup))

    followup = PortScanSettings([80], 1.0, 4)
    with patch.object(budgeting, "plan_passive_collect",
                      return_value=PassiveCollectPlan(3.0, 20.0, ["Listened 3s."])), \
         patch.object(engine, "_collect_phase", side_effect=record):
        await engine._passive_collect_phase(BackgroundScans(), ["10.77.0.5"], followup)

    assert seen == [(3.0, ["10.77.0.5"], followup)]
    assert engine.scan_status.warnings == ["Listened 3s."]
    assert engine._narrowed is True
