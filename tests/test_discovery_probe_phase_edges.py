"""The edges of a scan's driver probe phase: the UDP and TCP probes drivers
declare and their Python companions.

test_discovery_engine_hardening.py, test_discovery_budget_engine.py and
test_discovery_udp_reply_port.py drive the phase's main paths. These pin the
rest: probe evidence that names the device, UDP probes with nowhere to go, a
probe or a companion runner that fails, the budget's cap on TCP probes, the
stagger between ports, and what a companion is handed. All network I/O is
faked; invented addresses and devices only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

import pytest

from openavc.discovery import probe_phase as probes_mod
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.hints import parse_driver_discovery
from openavc.discovery.probe_runner import ReplyPortUnavailable
from openavc.discovery.result import Evidence, SignalTier

_LOGGER = "discovery"


def _hint(driver_id: str, **discovery: Any) -> Any:
    return parse_driver_discovery({
        "id": driver_id, "name": f"Acme {driver_id}", "manufacturer": "Acme",
        "category": "audio", "transport": "tcp", "discovery": discovery,
    })


def _tcp(port: int = 4321) -> dict[str, Any]:
    return {"port": port, "send_ascii": "id\r\n", "expect": "Acme", "timeout_ms": 500}


def _udp(port: int = 4400) -> dict[str, Any]:
    return {"port": port, "send_ascii": "who?", "expect": "Acme", "timeout_ms": 700}


def _engine(*hints: Any, hosts: tuple[str, ...] = (), port: int = 4321) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.discovery_hints = list(hints)
    for ip in hosts:
        dev = engine._get_or_create(ip)
        dev.alive = True
        dev.open_ports = [port]
    return engine


def _evidence(**response: Any) -> Evidence:
    return Evidence(tier=SignalTier.ACTIVE_PROBE, source="probe:acme", data={"response": response})


class _Updates:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    async def __call__(self, device: Any, phase: str) -> None:
        self.seen.append((device.ip, phase))


# --- Keeping what answered ---


async def test_probe_evidence_fills_only_the_fields_still_empty() -> None:
    engine = _engine()
    device = engine._get_or_create("10.77.0.5")
    device.model = "Passive Model"
    ev = _evidence(model="Probe Model With A Much Longer Name", firmware="2.1")
    engine._probe_finished[0] = [("10.77.0.5", ev, "protocol_probe")]
    updates = _Updates()
    with patch.object(engine, "_emit_device_update", updates):
        assert await engine.merge_probe_results() == 1
    assert device.alive is True
    assert device.evidence_log == [ev]
    assert device.model == "Passive Model"
    assert device.firmware == "2.1"
    assert updates.seen == [("10.77.0.5", "protocol_probe")]


async def test_probes_merge_in_the_order_they_started_and_once() -> None:
    engine = _engine()
    late, early, middle = (
        _evidence(model="Late"), _evidence(model="Early"), _evidence(model="Middle"),
    )
    engine._probe_finished[5] = [("10.77.0.5", late, "protocol_probe")]
    engine._probe_finished[1] = [("10.77.0.5", early, "broadcast_probe")]
    engine._probe_finished[3] = [("10.77.0.5", middle, "companion")]
    updates = _Updates()
    with patch.object(engine, "_emit_device_update", updates):
        assert await engine.merge_probe_results() == 3
        assert await engine.merge_probe_results() == 0
    device = engine.results["10.77.0.5"]
    assert device.evidence_log == [early, middle, late]
    assert device.model == "Early"
    assert updates.seen == [
        ("10.77.0.5", "broadcast_probe"), ("10.77.0.5", "companion"),
        ("10.77.0.5", "protocol_probe"),
    ]


# --- UDP probes ---


async def test_udp_probes_with_nowhere_to_go_start_nothing_and_tcp_numbers_from_zero() -> None:
    engine = _engine(_hint("acme_udp", udp_probe=_udp()), _hint("acme_ctl", tcp_probe=_tcp()),
                     hosts=("fd00::5",))
    sent: list[Any] = []

    async def fake_udp(spec, **kwargs):
        sent.append(spec)
        return {}

    async def fake_port_probes(host, port, specs, *, on_observation, **kwargs):
        on_observation(0, type("Obs", (), {"evidence": _evidence(model="X")})())

    with patch.object(probes_mod, "run_udp_broadcast_probe", fake_udp), \
            patch.object(probes_mod, "observe_port_probes", fake_port_probes):
        await engine._run_custom_probes(["fd00::/64"], "")
    assert sent == []
    assert list(engine._probe_finished) == [0]


async def test_udp_probes_are_numbered_ahead_of_the_tcp_probes() -> None:
    engine = _engine(_hint("acme_udp", udp_probe=_udp()), _hint("acme_ctl", tcp_probe=_tcp()),
                     hosts=("10.77.0.5",))

    async def fake_udp(spec, **kwargs):
        return {"10.77.0.9": _evidence(model="Heard")}

    async def fake_port_probes(host, port, specs, *, on_observation, **kwargs):
        on_observation(0, type("Obs", (), {"evidence": _evidence(model="X")})())

    with patch.object(probes_mod, "run_udp_broadcast_probe", fake_udp), \
            patch.object(probes_mod, "observe_port_probes", fake_port_probes):
        await engine._run_custom_probes(["10.77.0.0/24"], "10.77.0.1")
    assert sorted(engine._probe_finished) == [0, 1]
    assert engine._probe_finished[0][0][0] == "10.77.0.9"
    assert engine._probe_finished[0][0][2] == "broadcast_probe"
    assert engine._probe_finished[1][0][:1] == ("10.77.0.5",)
    assert engine._probe_finished[1][0][2] == "protocol_probe"


async def test_a_udp_probe_that_hears_nothing_keeps_nothing() -> None:
    engine = _engine(_hint("acme_udp", udp_probe=_udp()))

    async def fake_udp(spec, **kwargs):
        return {}

    with patch.object(probes_mod, "run_udp_broadcast_probe", fake_udp):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert engine._probe_finished == {}


async def test_a_failed_udp_probe_is_a_debug_line_and_a_busy_port_a_warning(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    engine = _engine(
        _hint("acme_b", udp_probe=_udp(4400)), _hint("acme_a", udp_probe=_udp(4400)),
        _hint("acme_c", udp_probe=_udp(4300)), _hint("acme_x", udp_probe=_udp(4500)),
    )

    async def fake_udp(spec, **kwargs):
        if spec.driver_id == "acme_x":
            raise RuntimeError("acme socket trouble")
        raise ReplyPortUnavailable(4400 if spec.driver_id != "acme_c" else 4300, "in use")

    with patch.object(probes_mod, "run_udp_broadcast_probe", fake_udp):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert engine.scan_status.warnings == [
        "Could not listen on UDP port 4300 for the Acme acme_c identification "
        "check (in use). Another program on this computer may be using it.",
        "Could not listen on UDP port 4400 for the Acme acme_b, Acme acme_a "
        "identification check (in use). Another program on this computer may be using it.",
    ]
    failed = [r for r in caplog.records if r.getMessage().startswith("Custom UDP probe")]
    assert [r.levelno for r in failed] == [logging.DEBUG]
    assert "acme_x" in failed[0].getMessage()


# --- TCP probes ---


async def test_the_budget_cap_keeps_the_first_tcp_probes_in_order() -> None:
    engine = _engine(_hint("acme_ctl", tcp_probe=_tcp()),
                     hosts=("10.77.0.5", "10.77.0.6", "10.77.0.7"))
    probed: list[str] = []

    async def fake_port_probes(host, port, specs, **kwargs):
        probed.append(host)

    assert engine._count_probe_jobs(["10.77.0.0/24"])[0] == 3
    with patch.object(probes_mod, "observe_port_probes", fake_port_probes):
        await engine._run_custom_probes(["10.77.0.0/24"], "", max_tcp_jobs=2)
    assert sorted(probed) == ["10.77.0.5", "10.77.0.6"]


async def test_a_port_two_drivers_probe_runs_their_checks_together() -> None:
    engine = _engine(_hint("acme_a", tcp_probe=_tcp()), _hint("acme_b", tcp_probe=_tcp()),
                     hosts=("10.77.0.5",))
    calls: list[tuple[str, int, list[str]]] = []

    async def fake_port_probes(host, port, specs, *, on_observation, **kwargs):
        calls.append((host, port, [s.driver_id for s in specs]))
        on_observation(1, type("Obs", (), {"evidence": _evidence(model="B")})())
        on_observation(0, type("Obs", (), {"evidence": None})())

    with patch.object(probes_mod, "observe_port_probes", fake_port_probes):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert calls == [("10.77.0.5", 4321, ["acme_a", "acme_b"])]
    assert list(engine._probe_finished) == [1]
    assert engine.results["10.77.0.5"].model == "B"


async def test_the_ports_start_twenty_milliseconds_apart() -> None:
    engine = _engine(_hint("acme_ctl", tcp_probe=_tcp()),
                     hosts=("10.77.0.5", "10.77.0.6", "10.77.0.7"))
    pauses: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds, *args, **kwargs):
        pauses.append(seconds)
        await real_sleep(0)

    async def fake_port_probes(host, port, specs, **kwargs):
        return None

    with patch.object(probes_mod, "observe_port_probes", fake_port_probes), \
            patch("asyncio.sleep", recording_sleep):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert sorted(pauses) == pytest.approx([0.020, 0.040])


async def test_a_failed_tcp_probe_is_a_debug_line_and_the_rest_still_merge(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    engine = _engine(_hint("acme_ctl", tcp_probe=_tcp()), hosts=("10.77.0.5", "10.77.0.6"))

    async def fake_port_probes(host, port, specs, *, on_observation, **kwargs):
        if host == "10.77.0.5":
            raise OSError("acme refused")
        on_observation(0, type("Obs", (), {"evidence": _evidence(model="Six")})())

    with patch.object(probes_mod, "observe_port_probes", fake_port_probes):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert engine.results["10.77.0.6"].model == "Six"
    assert engine._probe_merged == {1}
    failed = [r for r in caplog.records if r.getMessage() == "Custom TCP probe failed"]
    assert [r.levelno for r in failed] == [logging.DEBUG]


async def test_no_host_with_the_port_open_probes_and_merges_nothing() -> None:
    engine = _engine(_hint("acme_ctl", tcp_probe=_tcp()), hosts=("10.77.0.5",), port=80)
    engine._probe_finished[7] = [("10.77.0.5", _evidence(model="Earlier"), "protocol_probe")]
    with patch.object(probes_mod, "observe_port_probes", side_effect=AssertionError):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert engine._probe_merged == set()


# --- Python companions ---


async def test_a_companion_is_handed_the_scan_and_its_open_ports() -> None:
    engine = _engine(hosts=("10.77.0.5", "10.77.0.6"), port=4321)
    engine.results["10.77.0.6"].open_ports = [4321, 80]
    seen: dict[str, Any] = {}

    async def probe(ctx):
        seen.update(
            driver_id=ctx.driver_id, source_ip=ctx.source_ip,
            target_subnets=ctx.target_subnets, hosts=dict(ctx.hosts_by_open_port),
        )

    engine._discovery_companions = {"acme_probe": probe}
    await engine._run_custom_probes(["10.77.0.0/24"], "10.77.0.1")
    assert seen == {
        "driver_id": "acme_probe", "source_ip": "10.77.0.1",
        "target_subnets": ("10.77.0.0/24",),
        "hosts": {4321: ("10.77.0.5", "10.77.0.6"), 80: ("10.77.0.6",)},
    }


async def test_a_companion_record_with_nothing_to_lift_still_lands() -> None:
    engine = _engine()
    ev = Evidence(tier=SignalTier.BROADCAST_PROBE, source="broadcast:acme", data={"response": {}})

    async def probe(ctx):
        await ctx._emit_for_host("10.77.0.50", ev)

    engine._discovery_companions = {"acme_probe": probe}
    updates = _Updates()
    with patch.object(engine, "_emit_device_update", updates):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    device = engine.results["10.77.0.50"]
    assert device.alive is True
    assert device.evidence_log == [ev]
    assert device.model in (None, "")
    assert updates.seen == [("10.77.0.50", "broadcast_probe")]


async def test_a_companion_record_lifts_what_it_names_without_replacing() -> None:
    engine = _engine()
    device = engine._get_or_create("10.77.0.50")
    device.manufacturer = "Acme Passive"
    ev = Evidence(tier=SignalTier.BROADCAST_PROBE, source="broadcast:acme",
                  data={"response": {"manufacturer": "Acme Probe", "model": "W-1"}})

    async def probe(ctx):
        await ctx._emit_for_host("10.77.0.50", ev)

    engine._discovery_companions = {"acme_probe": probe}
    await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert (device.manufacturer, device.model) == ("Acme Passive", "W-1")


async def test_a_companion_runner_that_fails_is_a_warning(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    engine = _engine()

    async def probe(ctx):
        return None

    engine._discovery_companions = {"acme_bad": probe, "acme_cut": probe, "acme_ok": probe}

    async def fake_run_companion(driver_id, probe_fn, ctx):
        if driver_id == "acme_bad":
            raise RuntimeError("worker thread")
        if driver_id == "acme_cut":
            raise asyncio.CancelledError()
        return None

    with patch.object(probes_mod, "run_companion", fake_run_companion):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    warned = [r.getMessage() for r in caplog.records
              if r.levelno == logging.WARNING and "runner failed" in r.getMessage()]
    assert warned == ["Discovery companion acme_bad runner failed: RuntimeError('worker thread')"]


async def test_nothing_declared_runs_nothing() -> None:
    engine = _engine(hosts=("10.77.0.5",))
    with patch.object(probes_mod, "observe_port_probes", side_effect=AssertionError), \
            patch.object(probes_mod, "run_udp_broadcast_probe", side_effect=AssertionError):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert engine._count_probe_jobs(["10.77.0.0/24"]) == (0, 0, 0, 0.0, 0.0)


def test_the_count_projects_the_udp_sends_and_the_slowest_waits() -> None:
    engine = _engine(
        _hint("acme_u1", udp_probe=_udp()),
        _hint("acme_u2", udp_probe={**_udp(), "timeout_ms": 1500}),
        _hint("acme_ctl", tcp_probe=_tcp()),
        hosts=("10.77.0.5", "10.77.0.6"),
    )
    engine._discovery_companions = {"acme_a": None, "acme_b": None}
    tcp_jobs, udp_sends, companions, udp_listen, tcp_wait = engine._count_probe_jobs(
        ["10.77.0.0/24", "10.77.1.8/31"],
    )
    assert (tcp_jobs, udp_sends, companions) == (2, 6, 2)
    assert udp_listen == 1.5
    assert tcp_wait > 0


async def test_the_tcp_probes_are_the_drivers_installed_when_the_phase_began() -> None:
    # A driver installed or removed while the UDP probes wait for answers
    # changes nothing about the TCP probes this phase runs.
    engine = _engine(_hint("acme_a", udp_probe=_udp()), _hint("acme_b", tcp_probe=_tcp(23)),
                     hosts=("10.77.0.5",), port=23)
    probed: list[str] = []

    async def fake_udp(spec, **kwargs):
        engine.discovery_hints = [_hint("acme_a", udp_probe=_udp()), _hint("acme_c", tcp_probe=_tcp(23))]
        return {}

    async def fake_port_probes(host, port, specs, **kwargs):
        probed.extend(s.driver_id for s in specs)

    with patch.object(probes_mod, "run_udp_broadcast_probe", fake_udp), \
            patch.object(probes_mod, "observe_port_probes", fake_port_probes):
        await engine._run_custom_probes(["10.77.0.0/24"], "")
    assert probed == ["acme_b"]
