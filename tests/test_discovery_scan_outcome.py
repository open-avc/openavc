"""What a finished or running scan holds, as every exit reads it.

A host the ARP table only named is held back until it answers something for
itself. The live feed held it back while the scan's results, its count, the
assistant's answer and the re-match after a driver change did not, and a scan
that was stopped, or whose matcher failed, never ended the hold at all: the
host stayed in the results, counted, though no client was ever sent it. These
pin one answer across the exits: the complete event's count, the status's
count, the results and what the feed sent agree on every way a scan ends.

The scan's network phases are replaced by stubs that leave hand-built
devices behind; the hosts are invented addresses in 192.0.2.0/24.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from openavc.api import discovery as discovery_api
from openavc.cloud.tools.system_tools import SystemToolsMixin
from openavc.discovery.engine import DiscoveryEngine

pytestmark = pytest.mark.asyncio

PROVEN = "192.0.2.10"      # answered the sweep
QUIET = "192.0.2.40"       # named by the ARP table, answered nothing
EARNED = "192.0.2.41"      # named by the ARP table, then an open port answered


def _engine() -> tuple[DiscoveryEngine, list[dict]]:
    engine = DiscoveryEngine()
    engine.scan_status.started_at = time.time() - 1.0
    engine.scan_status.scan_id = "scan_outcome"
    engine.scan_status.status = "running"
    events: list[dict] = []

    async def on_update(message: dict) -> None:
        events.append(message)

    engine.on_update = on_update
    return engine, events


async def _mid_scan(engine: DiscoveryEngine) -> None:
    """A proven host, plus two ARP-named hosts on probation: one that has
    answered on a port and one that has answered nothing. Each is announced
    as the sweep announces a host, so only the proven one goes out."""
    engine._get_or_create(PROVEN).alive = True
    for ip in (QUIET, EARNED):
        engine._get_or_create(ip).alive = True
        engine._arp_provisional.add(ip)
    engine.results[EARNED].open_ports = [10001]
    for device in list(engine.results.values()):
        await engine._emit_device_update(device, "ping_sweep")


def _ips(devices: list[dict]) -> list[str]:
    return [d["ip"] for d in devices]


def _sent(events: list[dict]) -> set[str]:
    return {e["device"]["ip"] for e in events if e.get("type") == "discovery.update"}


def _complete(events: list[dict]) -> dict:
    found = [e for e in events if e.get("type") == "discovery.complete"]
    assert len(found) == 1, events
    return found[0]


def _agree(engine: DiscoveryEngine, events: list[dict]) -> int:
    """The count every exit reports, after checking they all report it."""
    results = engine.get_results()
    complete = _complete(events)
    assert complete["total_devices"] == len(results)
    assert engine.get_status()["devices_found"] == len(results)
    assert _sent(events) == set(_ips(results))
    return len(results)


async def test_the_results_hold_back_a_host_on_probation_as_the_feed_does():
    engine, _ = _engine()
    await _mid_scan(engine)

    assert _ips(engine.get_results()) == [PROVEN]
    assert engine.get_status()["devices_found"] == 1


async def test_a_finished_scan_reports_one_count_on_every_exit():
    engine, events = _engine()

    async def pipeline(subnets):
        await _mid_scan(engine)

    with patch.object(engine, "_scan_pipeline", side_effect=pipeline):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    assert _agree(engine, events) == 2
    assert QUIET not in engine.results


async def test_a_stopped_scan_ends_the_probation_like_a_finished_one():
    engine, events = _engine()

    async def pipeline(subnets):
        await _mid_scan(engine)
        raise asyncio.CancelledError()

    with patch.object(engine, "_scan_pipeline", side_effect=pipeline):
        with pytest.raises(asyncio.CancelledError):
            await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    assert _complete(events)["status"] == "cancelled"
    assert _agree(engine, events) == 2
    assert QUIET not in engine.results
    assert [e["phase"] for e in events if e.get("type") == "discovery.update"
            and e["device"]["ip"] == EARNED] == ["finalize"]


async def test_a_failed_matcher_still_ends_the_probation():
    engine, events = _engine()

    async def pipeline(subnets):
        await _mid_scan(engine)

    with patch.object(engine, "_scan_pipeline", side_effect=pipeline), \
         patch.object(engine, "_match_each_device", side_effect=RuntimeError("matcher")):
        await engine._run_scan(["192.0.2.0/24"], timeout=10.0)

    assert _agree(engine, events) == 2
    assert QUIET not in engine.results


async def test_a_scan_stopped_before_it_began_still_tells_every_client():
    engine, events = _engine()
    with patch.object(engine, "_scan_pipeline", new_callable=AsyncMock):
        engine._scan_task = asyncio.create_task(engine._run_scan(["192.0.2.0/24"], 10.0))
        await engine.stop_scan()

    assert engine.get_status()["status"] == "cancelled"
    assert _complete(events)["status"] == "cancelled"


async def test_a_rematch_after_a_driver_change_never_sends_a_host_on_probation(monkeypatch):
    engine, _ = _engine()
    await _mid_scan(engine)
    sent: list[dict] = []

    async def broadcast(message: dict) -> None:
        sent.append(message)

    async def no_catalog(force_catalog: bool = False) -> None:
        return None

    monkeypatch.setattr(engine, "refresh_signal_index_with_catalog", no_catalog)
    monkeypatch.setattr(discovery_api, "_engine", engine)
    monkeypatch.setattr(discovery_api, "_broadcast_fn", broadcast)

    await discovery_api.refresh_all_device_matches()

    assert _sent(sent) == {PROVEN}


class _Assistant(SystemToolsMixin):
    pass


async def test_the_assistants_answer_holds_back_a_host_on_probation(monkeypatch):
    engine, _ = _engine()
    await _mid_scan(engine)
    monkeypatch.setattr(discovery_api, "_engine", engine)

    answer = await _Assistant()._get_discovery_results({})

    assert _ips(answer["devices"]) == [PROVEN]
    assert answer["total_devices"] == 1
