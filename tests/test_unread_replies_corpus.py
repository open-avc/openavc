"""No shipped YAML driver trips the unread-replies check against its own
simulator.

The poll loop reports a device whose replies arrive while no response rule
reads any of them (test_unread_replies.py). That is a finding only if a
healthy device never looks like that. A driver whose poll draws nothing but
replies it deliberately leaves unread (an arming send, an acknowledgement no
rule needs) would be reported on every healthy unit, so every polling YAML
driver on a transport the check watches runs here against the simulator
built from its own definition, for several judged poll cycles, and must stay
quiet.

The same carve-out as tests/test_driver_round_trip_parity.py: a corpus-wide
sweep of a platform property that names no product. It cannot live in
openavc-drivers, whose CI carries no ``openavc`` package.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from tests import gates

DRIVERS_ROOT = Path(
    os.environ.get("OPENAVC_DRIVERS_ROOT")
    or Path(__file__).resolve().parent.parent.parent / "openavc-drivers"
)
_CATEGORIES = (
    "projectors", "displays", "switchers", "audio", "cameras", "video",
    "lighting", "power", "streaming", "utility",
)
# The transports whose connections keep the counts the check reads.
_WATCHED = ("tcp", "udp", "osc")
_JUDGED_CYCLES = 2
_INTERVAL = 0.2


def _polling_definitions() -> list[Path]:
    if not DRIVERS_ROOT.exists():
        return []
    found = []
    for category in _CATEGORIES:
        for path in sorted((DRIVERS_ROOT / category).glob("*.avcdriver")):
            definition = yaml.safe_load(path.read_text(encoding="utf-8"))
            if definition.get("transport") not in _WATCHED:
                continue
            polling = definition.get("polling") or {}
            if not polling.get("queries"):
                continue
            if not (definition.get("responses") or definition.get("osc_responses")):
                continue
            found.append(path)
    return found


DEFINITIONS = _polling_definitions()

# Each case starts its own simulator on a free port, and the slowest take half
# a minute of real poll cycles: a parallel run spreads them (tests/conftest.py).
XDIST_SPREAD = True


@gates.skipif_missing(
    gates.DRIVER_CORPUS,
    None if DEFINITIONS else f"no community drivers found at {DRIVERS_ROOT}",
)
@pytest.mark.parametrize("path", DEFINITIONS, ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_a_healthy_simulated_device_is_never_reported(path: Path) -> None:
    definition: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    sim_config = dict(definition.get("default_config") or {})
    sim = YAMLAutoSimulator(definition["id"], config=sim_config, driver_def=definition)
    await sim.start(0)
    cls = create_configurable_driver_class(definition)
    config = dict(definition.get("default_config") or {})
    config.update({
        "host": "127.0.0.1",
        "port": sim.port,
        "poll_interval": _INTERVAL,
        "max_missed_polls": 1,
    })
    drv = cls("acme1", config, StateStore(), EventBus())

    judged: list[bool] = []
    judge = drv._judge_reading

    def counting(parse_pending, cycles, limit):
        result = judge(parse_pending, cycles, limit)
        judged.append(drv._unread_replies_hold)
        return result

    drv._judge_reading = counting
    try:
        await drv.connect()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 60
        while len(judged) < _JUDGED_CYCLES and loop.time() < deadline:
            await asyncio.sleep(0.05)
        assert len(judged) >= _JUDGED_CYCLES, (
            f"only {len(judged)} poll cycles judged in 60 s"
        )
        assert not any(judged), (
            f"{path.name}: its own simulator's replies matched no response rule "
            f"in a poll cycle (last: {drv._unread_sample()!r})"
        )
    finally:
        await drv.disconnect()
        await sim.stop()
