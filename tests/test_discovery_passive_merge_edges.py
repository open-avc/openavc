"""The edges of merging what a scan heard: the passive listeners' results
(mDNS, SSDP, AMX DDP) and SNMP's.

test_discovery_passive.py and test_discovery_snmp.py drive the main paths.
These pin the rest: a source merged once per scan, a new device refused at
the passive cap, the mDNS name not recorded twice, and SNMP that overran its
window or failed. Every scanner is a stand-in; invented addresses only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

from openavc.discovery import passive_phase as passive_mod
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.result import Evidence, SignalTier
from openavc.discovery.tier_matcher import evidence_hostname

_LOGGER = "discovery"


class _Result:
    """A listener's or SNMP's result for one address."""

    def __init__(self, records: list[Evidence], info: dict[str, Any], evidence: Evidence | None = None):
        self._records = records
        self._info = info
        self._evidence = evidence

    def to_evidence_records(self) -> list[Evidence]:
        return list(self._records)

    def to_evidence(self) -> Evidence | None:
        return self._evidence

    def to_device_info(self) -> dict[str, Any]:
        return dict(self._info)


class _Scanner:
    def __init__(self, results: dict[str, _Result] | None = None) -> None:
        self.results = results or {}


def _ev(source: str) -> Evidence:
    return Evidence(tier=SignalTier.PASSIVE_LISTENER, source=source, data={})


class _Updates:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    async def __call__(self, device: Any, phase: str) -> None:
        self.seen.append((device.ip, phase))


def _engine(mdns=None, ssdp=None, amx=None) -> tuple[DiscoveryEngine, _Updates]:
    engine = DiscoveryEngine()
    engine._passive_scanners = (_Scanner(mdns), _Scanner(ssdp), _Scanner(amx))
    updates = _Updates()
    engine._emit_device_update = updates  # type: ignore[method-assign]
    return engine, updates


# --- The passive listeners ---


async def test_each_source_merges_its_evidence_and_info_once() -> None:
    mdns_ev, ssdp_ev, beacon = _ev("mdns:_acme._tcp."), _ev("ssdp:acme"), _ev("amx_ddp:acme")
    engine, updates = _engine(
        mdns={"10.77.0.5": _Result([mdns_ev], {"model": "M-1"})},
        ssdp={"10.77.0.6": _Result([ssdp_ev], {"manufacturer": "Acme"})},
        amx={"10.77.0.7": _Result([], {"model": "A-1"}, evidence=beacon)},
    )
    assert await engine.merge_passive_results() == 3
    assert await engine.merge_passive_results() == 0
    assert engine.results["10.77.0.5"].evidence_log == [mdns_ev]
    assert engine.results["10.77.0.5"].model == "M-1"
    assert engine.results["10.77.0.6"].evidence_log == [ssdp_ev]
    assert engine.results["10.77.0.6"].manufacturer == "Acme"
    assert engine.results["10.77.0.7"].evidence_log == [beacon]
    assert engine.results["10.77.0.7"].model == "A-1"
    assert all(engine.results[ip].alive for ip in ("10.77.0.5", "10.77.0.6", "10.77.0.7"))
    assert updates.seen == [
        ("10.77.0.5", "mdns"), ("10.77.0.6", "ssdp"), ("10.77.0.7", "amx_ddp"),
    ]


async def test_one_address_heard_by_every_listener_merges_each_once() -> None:
    engine, updates = _engine(
        mdns={"10.77.0.5": _Result([_ev("mdns:a")], {})},
        ssdp={"10.77.0.5": _Result([_ev("ssdp:a")], {})},
        amx={"10.77.0.5": _Result([], {}, evidence=_ev("amx_ddp:a"))},
    )
    assert await engine.merge_passive_results() == 3
    assert [ev.source for ev in engine.results["10.77.0.5"].evidence_log] == [
        "mdns:a", "ssdp:a", "amx_ddp:a",
    ]
    assert updates.seen == [("10.77.0.5", "mdns"), ("10.77.0.5", "ssdp"), ("10.77.0.5", "amx_ddp")]


async def test_a_new_address_past_the_passive_cap_is_dropped_by_every_source(caplog) -> None:
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    engine, updates = _engine(
        mdns={"10.77.0.5": _Result([_ev("mdns:a")], {})},
        ssdp={"10.77.0.6": _Result([_ev("ssdp:a")], {}), "10.77.0.9": _Result([_ev("ssdp:b")], {})},
        amx={"10.77.0.7": _Result([], {}, evidence=_ev("amx_ddp:a"))},
    )
    engine._get_or_create("10.77.0.9")
    with patch.object(passive_mod, "MAX_PASSIVE_RESULT_SOURCES", 0):
        assert await engine.merge_passive_results() == 1
    assert set(engine.results) == {"10.77.0.9"}
    assert updates.seen == [("10.77.0.9", "ssdp")]
    assert len(engine.scan_status.warnings) == 1
    # Each address was claimed even though it was dropped: it is not tried again.
    assert await engine.merge_passive_results() == 0


async def test_no_listeners_merges_nothing() -> None:
    engine = DiscoveryEngine()
    engine._passive_scanners = None
    assert await engine.merge_passive_results() == 0


async def test_the_announced_name_is_not_recorded_twice() -> None:
    named = evidence_hostname("acme-1")
    engine, _ = _engine(mdns={"10.77.0.5": _Result([named], {"hostname": "acme-1"})})
    await engine.merge_passive_results()
    hostname_records = [
        ev for ev in engine.results["10.77.0.5"].evidence_log if ev.data.get("kind") == "hostname"
    ]
    assert hostname_records == [named]


async def test_an_announced_name_with_no_record_yet_gets_one() -> None:
    engine, _ = _engine(mdns={"10.77.0.5": _Result([_ev("mdns:a")], {"hostname": "acme-1"})})
    await engine.merge_passive_results()
    log = engine.results["10.77.0.5"].evidence_log
    assert [ev.source for ev in log][0] == "mdns:a"
    assert [ev.data.get("value") for ev in log if ev.data.get("kind") == "hostname"] == ["acme-1"]


# --- SNMP ---


def _snmp(ip: str, *, with_evidence: bool = True) -> dict[str, _Result]:
    ev = Evidence(tier=SignalTier.ENRICHMENT, source=f"snmp:{ip}", data={})
    return {ip: _Result([], {"model": f"S-{ip[-1]}"}, evidence=ev if with_evidence else None)}


async def test_snmp_that_finished_merges_its_results(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    engine, updates = _engine()

    async def scan():
        return {**_snmp("10.77.0.5"), **_snmp("10.77.0.6", with_evidence=False)}

    task = asyncio.ensure_future(scan())
    await engine._collect_snmp_results(task, _Scanner())
    assert engine.results["10.77.0.5"].evidence_log[0].source == "snmp:10.77.0.5"
    assert engine.results["10.77.0.5"].model == "S-5"
    assert engine.results["10.77.0.6"].evidence_log == []
    assert engine.results["10.77.0.6"].alive is True
    assert updates.seen == [("10.77.0.5", "snmp"), ("10.77.0.6", "snmp")]
    assert "SNMP enriched 2 devices" in [r.getMessage() for r in caplog.records]


async def test_snmp_that_failed_is_a_debug_line_and_merges_nothing(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    engine, updates = _engine()

    async def scan():
        raise OSError("acme socket")

    task = asyncio.ensure_future(scan())
    await engine._collect_snmp_results(task, _Scanner(_snmp("10.77.0.5")))
    assert engine.results == {}
    assert updates.seen == []
    failed = [r for r in caplog.records if r.getMessage() == "SNMP task failed"]
    assert [r.levelno for r in failed] == [logging.DEBUG]


def _short_wait(real_wait=asyncio.wait):
    async def wait(tasks, *, timeout=None, **kwargs):
        return await real_wait(tasks, timeout=0.05 if timeout == 5.0 else timeout, **kwargs)
    return wait


async def test_snmp_that_overran_is_cancelled_and_its_partial_results_kept(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    engine, updates = _engine()
    task = asyncio.ensure_future(asyncio.sleep(30))
    with patch("asyncio.wait", _short_wait()):
        await engine._collect_snmp_results(task, _Scanner(_snmp("10.77.0.5")))
    assert task.cancelled()
    assert engine.results["10.77.0.5"].model == "S-5"
    assert updates.seen == [("10.77.0.5", "snmp")]
    messages = [r.getMessage() for r in caplog.records]
    assert "SNMP scan overran the collect window; merging 1 partial result(s)" in messages
    assert "SNMP enriched 1 devices" in messages


async def test_snmp_that_overran_with_nothing_found_says_nothing(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    engine, updates = _engine()
    task = asyncio.ensure_future(asyncio.sleep(30))
    with patch("asyncio.wait", _short_wait()):
        await engine._collect_snmp_results(task, _Scanner())
    assert task.cancelled()
    assert engine.results == {}
    assert [r for r in caplog.records if "SNMP" in r.getMessage()] == []


async def test_snmp_that_overran_without_its_scanner_merges_nothing() -> None:
    engine, updates = _engine()
    task = asyncio.ensure_future(asyncio.sleep(30))
    with patch("asyncio.wait", _short_wait()):
        await engine._collect_snmp_results(task, None)
    assert task.cancelled()
    assert engine.results == {}


async def test_no_snmp_task_merges_nothing() -> None:
    engine, updates = _engine()
    await engine._collect_snmp_results(None, _Scanner(_snmp("10.77.0.5")))
    assert engine.results == {}


# --- The end of the listening window ---


class _Listener:
    def __init__(self) -> None:
        self._running = True
        self.results: dict[str, Any] = {}

    async def stop(self) -> None:
        self._running = False


async def test_the_listeners_get_the_drain_to_finish_and_a_crash_is_logged(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    engine = DiscoveryEngine()
    mdns, ssdp, amx = _Listener(), _Listener(), _Listener()
    engine._passive_scanners = (mdns, ssdp, amx)

    async def mdns_crashed():
        raise RuntimeError("acme multicast")

    async def ssdp_fetching():
        while ssdp._running:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.05)  # its description fetches, after the stop
        return {}

    async def amx_listening():
        while amx._running:
            await asyncio.sleep(0.005)
        return {}

    tasks = (
        asyncio.ensure_future(mdns_crashed()),
        asyncio.ensure_future(ssdp_fetching()),
        asyncio.ensure_future(amx_listening()),
    )
    with patch.object(passive_mod, "_PASSIVE_COLLECT_TICK_SECONDS", 0.01), \
            patch.object(passive_mod, "_LISTENER_DRAIN_SECONDS", 2.0):
        await engine._collect_passive_results(*tasks, wait_seconds=0.03)
    assert tasks[1].done() and not tasks[1].cancelled()
    assert tasks[2].done() and not tasks[2].cancelled()
    failed = [r for r in caplog.records if r.getMessage().endswith("listener failed")]
    assert [(r.getMessage(), r.levelno) for r in failed] == [("mDNS listener failed", logging.DEBUG)]
    assert isinstance(failed[0].exc_info[1], RuntimeError)
