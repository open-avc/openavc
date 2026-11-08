"""What a scan's ending does on each way out: the status it reports, the
sentences it adds, the count and timing on the complete event, and what still
runs when the recovery or the matcher fails.

A scan's network phases are replaced by a stub here; these pin the outer
runner (``DiscoveryEngine._run_scan``), not any phase.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, patch

import pytest

from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.result import DiscoveredDevice

pytestmark = pytest.mark.asyncio


def _engine_with_events() -> tuple[DiscoveryEngine, list[dict]]:
    engine = DiscoveryEngine()
    engine.scan_status.started_at = time.time() - 2.0
    engine.scan_status.scan_id = "scan_pin"
    events: list[dict] = []

    async def on_update(message: dict) -> None:
        events.append(message)

    engine._on_update = on_update
    return engine, events


def _complete(events: list[dict]) -> dict:
    found = [e for e in events if e.get("type") == "discovery.complete"]
    assert len(found) == 1, events
    return found[0]


async def test_an_unexpected_error_ends_partial_with_its_sentence(caplog):
    engine, events = _engine_with_events()

    async def broken(subnets):
        raise RuntimeError("boom")

    with patch.object(engine, "_scan_pipeline", side_effect=broken), \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock) as finalize, \
         caplog.at_level(logging.ERROR, logger="discovery"):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    assert engine.scan_status.status == "partial"
    assert engine.scan_status.warnings == [
        "Scan ended early after an unexpected error. Results are incomplete "
        "— see the server log for details."
    ]
    assert "Scan failed" in caplog.messages
    finalize.assert_awaited_once()
    assert _complete(events)["status"] == "partial"


async def test_the_ceiling_sentence_names_the_limit_and_the_phase_message():
    engine, _ = _engine_with_events()
    engine.scan_status.phase = "port_scan"
    engine.scan_status.message = "Scanning ports..."

    async def slow(subnets):
        await asyncio.sleep(10)

    with patch.object(engine, "_scan_pipeline", side_effect=slow), \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock):
        await engine._run_scan(["192.0.2.0/24"], timeout=0.1)

    assert engine.scan_status.warnings == [
        "Scan stopped at its 0s limit during \"Scanning ports...\". Devices found "
        "after that point are missing. Scan a smaller subnet, or raise the time "
        "limit, for complete results."
    ]


async def test_the_complete_event_carries_the_count_and_the_duration():
    engine, events = _engine_with_events()
    engine.results["192.0.2.10"] = DiscoveredDevice(ip="192.0.2.10")
    engine.results["192.0.2.11"] = DiscoveredDevice(ip="192.0.2.11")

    with patch.object(engine, "_scan_pipeline", new_callable=AsyncMock), \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    event = _complete(events)
    assert event == {
        "type": "discovery.complete",
        "scan_id": "scan_pin",
        "status": "complete",
        "total_devices": 2,
        "duration_seconds": engine.scan_status.duration,
        "warnings": [],
    }
    assert engine.scan_status.duration >= 2.0
    assert engine.scan_status.devices_found == 2


async def test_a_failed_matcher_still_counts_and_says_so(caplog):
    engine, events = _engine_with_events()
    engine.results["192.0.2.10"] = DiscoveredDevice(ip="192.0.2.10")
    engine.scan_status.devices_found = 0

    with patch.object(engine, "_scan_pipeline", new_callable=AsyncMock), \
         patch.object(engine, "_finalize_scan", side_effect=RuntimeError("matcher")), \
         caplog.at_level(logging.ERROR, logger="discovery"):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    assert engine.scan_status.devices_found == 1
    assert engine.scan_status.status == "complete"
    assert engine.scan_status.warnings == [
        "Driver matching failed — devices were found but could not "
        "be identified. See the server log for details."
    ]
    assert "Driver matching failed" in caplog.messages
    assert _complete(events)["warnings"] == engine.scan_status.warnings


async def test_a_failed_probe_recovery_is_logged_and_the_matcher_still_runs(caplog):
    engine, _ = _engine_with_events()

    with patch.object(engine, "_scan_pipeline", new_callable=AsyncMock), \
         patch.object(engine, "merge_probe_results", side_effect=RuntimeError("probe")), \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock) as finalize, \
         caplog.at_level(logging.ERROR, logger="discovery"):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    assert "Probe result recovery failed" in caplog.messages
    finalize.assert_awaited_once()
    assert engine.scan_status.status == "complete"


async def test_a_failed_passive_recovery_is_logged_and_the_probes_still_merge(caplog):
    engine, _ = _engine_with_events()

    async def slow(subnets):
        await asyncio.sleep(10)

    with patch.object(engine, "_scan_pipeline", side_effect=slow), \
         patch.object(engine, "merge_passive_results", side_effect=RuntimeError("passive")), \
         patch.object(engine, "merge_probe_results", new_callable=AsyncMock,
                      return_value=0) as probes, \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock) as finalize, \
         caplog.at_level(logging.ERROR, logger="discovery"):
        await engine._run_scan(["192.0.2.0/24"], timeout=0.1)

    assert "Passive result recovery failed" in caplog.messages
    probes.assert_awaited_once()
    finalize.assert_awaited_once()


async def test_recovered_counts_are_logged(caplog):
    engine, _ = _engine_with_events()

    async def slow(subnets):
        await asyncio.sleep(10)

    with patch.object(engine, "_scan_pipeline", side_effect=slow), \
         patch.object(engine, "merge_passive_results", new_callable=AsyncMock, return_value=3), \
         patch.object(engine, "merge_probe_results", new_callable=AsyncMock, return_value=2), \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock), \
         caplog.at_level(logging.INFO, logger="discovery"):
        await engine._run_scan(["192.0.2.0/24"], timeout=0.1)

    assert "Recovered 3 passive result(s) from a truncated scan" in caplog.messages
    assert (
        "Kept 2 driver probe result(s) from a probe phase that ran out of time"
        in caplog.messages
    )


async def test_nothing_recovered_logs_nothing(caplog):
    engine, _ = _engine_with_events()

    async def slow(subnets):
        await asyncio.sleep(10)

    with patch.object(engine, "_scan_pipeline", side_effect=slow), \
         patch.object(engine, "merge_passive_results", new_callable=AsyncMock, return_value=0), \
         patch.object(engine, "merge_probe_results", new_callable=AsyncMock, return_value=0), \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock), \
         caplog.at_level(logging.INFO, logger="discovery"):
        await engine._run_scan(["192.0.2.0/24"], timeout=0.1)

    assert not any("Recovered" in m or "Kept" in m for m in caplog.messages)


async def test_a_completed_scan_never_merges_the_listeners_again():
    engine, _ = _engine_with_events()

    with patch.object(engine, "_scan_pipeline", new_callable=AsyncMock), \
         patch.object(engine, "merge_passive_results", new_callable=AsyncMock) as passive, \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    passive.assert_not_awaited()


async def test_a_cancelled_scan_still_reports_and_emits_then_raises():
    engine, events = _engine_with_events()
    engine.results["192.0.2.10"] = DiscoveredDevice(ip="192.0.2.10")

    async def cancelled(subnets):
        raise asyncio.CancelledError()

    with patch.object(engine, "_scan_pipeline", side_effect=cancelled), \
         patch.object(engine, "merge_probe_results", new_callable=AsyncMock) as probes, \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock) as finalize:
        with pytest.raises(asyncio.CancelledError):
            await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    probes.assert_not_awaited()
    finalize.assert_not_awaited()
    assert engine.scan_status.devices_found == 1
    event = _complete(events)
    assert event["status"] == "complete"
    assert event["total_devices"] == 1


async def test_a_narrowed_scan_that_finished_reports_partial():
    engine, events = _engine_with_events()
    engine._narrowed = True

    with patch.object(engine, "_scan_pipeline", new_callable=AsyncMock), \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    assert engine.scan_status.status == "partial"
    assert _complete(events)["status"] == "partial"


async def test_a_status_already_cancelled_stays_cancelled():
    engine, events = _engine_with_events()
    engine.scan_status.status = "cancelled"

    with patch.object(engine, "_scan_pipeline", new_callable=AsyncMock), \
         patch.object(engine, "_finalize_scan", new_callable=AsyncMock):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    assert _complete(events)["status"] == "cancelled"
