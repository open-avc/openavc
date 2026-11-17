"""The one step that follows every driver change: ``driver_change.changed``.

Platform test: an invented driver (``acme_widget``) on a fake engine.

Each door that changes a driver (an install, an update, an import, an
uninstall, a Builder save, delete or reload) used to do its own part of what
follows: one rebuilt the devices, another refreshed Discovery, none told the
simulation. ``changed`` is all of it, in one order, and no part of it can fail
the door: the change is made by the time it runs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openavc.core.device_records import ORPHANED, RUNNING
from openavc.drivers import driver_change

DRIVER_ID = "acme_widget"


class _Engine:
    """The parts of the engine the step reads, each call an event."""

    def __init__(self, events: list, *, statuses=None, restarting=False):
        self.events = events
        self.statuses = statuses or {}
        self.fail: set[str] = set()
        self.restarting = restarting
        devices = SimpleNamespace(
            get_devices_using_driver=self._using,
            status_of=lambda device_id: self.statuses.get(device_id, RUNNING),
            reload_driver=self._reload,
            retry_all_orphans=self._retry,
            request_bringup=lambda: events.append(("round",)),
        )
        self.devices = devices
        self.simulation = SimpleNamespace(drivers_changed=self._simulation)

    def _raise(self, part: str) -> None:
        if part in self.fail:
            raise RuntimeError(f"{part} failed")

    async def _simulation(self, driver_ids):
        self.events.append(("simulation", tuple(driver_ids)))
        self._raise("simulation")
        return self.restarting

    def _using(self, driver_id):
        self._raise("using")
        return [d for d in self.statuses if d.startswith(driver_id)]

    async def _reload(self, driver_id):
        self.events.append(("reload", driver_id))
        self._raise("reload")
        return [d for d in self.statuses if d.startswith(driver_id) and d.endswith("_back")]

    async def _retry(self):
        self.events.append(("retry",))
        self._raise("retry")
        return ["other_waiting"]


@pytest.fixture
def world(monkeypatch):
    events: list = []
    engine = _Engine(events)
    monkeypatch.setattr(driver_change, "_running_engine", lambda: engine)

    async def _refresh():
        events.append(("refresh",))
        engine._raise("refresh")

    async def _announce(driver_id=None):
        events.append(("announce", driver_id))

    monkeypatch.setattr("openavc.api.discovery.refresh_all_device_matches", _refresh)
    monkeypatch.setattr(driver_change, "announce", _announce)
    return engine


async def test_a_changed_driver_s_devices_are_built_again_and_the_waiting_ones_named(world):
    world.statuses = {
        "acme_widget_1_back": RUNNING,
        "acme_widget_2_back": ORPHANED,
        "acme_widget_3": RUNNING,
    }
    outcome = await driver_change.changed(DRIVER_ID)
    assert outcome == driver_change.Changed(
        reconnected=["acme_widget_1_back", "acme_widget_2_back"],
        activated=["acme_widget_2_back", "other_waiting"],
        not_reconnected=["acme_widget_3"],
    )
    assert world.events == [
        ("simulation", (DRIVER_ID,)),
        ("reload", DRIVER_ID),
        ("retry",),
        ("refresh",),
        ("announce", DRIVER_ID),
    ]


async def test_simulated_devices_on_it_ask_for_a_round_once_the_devices_are_built(world):
    world.restarting = True
    await driver_change.changed(DRIVER_ID)
    assert world.events.index(("round",)) == world.events.index(("retry",)) + 1


async def test_a_driver_that_went_leaves_the_devices_as_they_are(world):
    world.statuses = {"acme_widget_1": RUNNING}
    outcome = await driver_change.changed(gone=[DRIVER_ID])
    assert outcome == driver_change.Changed()
    assert world.events == [("simulation", ()), ("refresh",), ("announce", DRIVER_ID)]


async def test_a_renamed_driver_builds_both_ids_and_names_the_new_one(world):
    await driver_change.changed("acme_widget_v2", DRIVER_ID)
    assert [e for e in world.events if e[0] in ("reload", "announce")] == [
        ("reload", "acme_widget_v2"),
        ("reload", DRIVER_ID),
        ("announce", "acme_widget_v2"),
    ]


@pytest.mark.parametrize("part", ["simulation", "using", "reload", "retry", "refresh"])
async def test_no_part_that_fails_stops_the_rest(world, part):
    world.statuses = {"acme_widget_1": RUNNING}
    world.fail = {part}
    await driver_change.changed(DRIVER_ID)
    assert ("announce", DRIVER_ID) == world.events[-1]
    assert ("refresh",) in world.events
    assert ("retry",) in world.events


async def test_before_the_engine_starts_only_discovery_and_the_announcement_run(monkeypatch, world):
    monkeypatch.setattr(driver_change, "_running_engine", lambda: None)
    assert await driver_change.changed(DRIVER_ID) == driver_change.Changed()
    assert world.events == [("refresh",), ("announce", DRIVER_ID)]
