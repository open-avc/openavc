"""A reading nobody has taken is not a reading, on every surface that reads it.

A driver used to publish a *value* for every state variable it declared, from
the moment it was constructed and before a single byte had been exchanged with
the device: a numeric started at its declared minimum, an enum at its first
value, a string at ``""``. Nothing downstream could tell that from something the
device said. A projector nobody had reached published ``lamp_hours = 0`` against
a true 450 and ``power = "off"`` for a projector that might well be on; the cloud
relay shipped both up as fresh readings; a monitor declaring a minimum above zero
fired an alert on the invented zero; and the honest tile beside it, on a key that
only appears at runtime and so read "--", looked like the broken one.

The keys still exist from construction -- a binding picker, the IDE's live state
list and a ``$device.…`` reference all need to know the reading is on offer. What
changed is that they hold ``None``, which is the word the rest of the platform
already had for this and was simply never being given:

* ``core/monitors.py``      -- ``NO_VALUE``: draws "--", never 0.
* ``cloud/alert_monitor.py``-- matches no threshold, so nothing fires on it.
* ``core/condition_eval.py``-- "no decision", so a guard cannot act on it.

These tests run the whole chain on an invented device, because the point is not
any one consumer: it is that the invented value is gone at the source and every
consumer therefore agrees.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.cloud.alert_monitor import AlertMonitor
from openavc.core import monitors
from openavc.core.condition_eval import eval_operator
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


class _AcmeProjector(BaseDriver):
    """Every declared shape that used to produce an invented starting value."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_projector",
        "name": "Acme Projector",
        "transport": "tcp",
        "commands": {},
        "state_variables": {
            # The one that was photographed: a real projector reading 450.
            "lamp_hours": {"type": "integer", "label": "Lamp Hours", "min": 0},
            # A numeric whose declared minimum is a long way from zero, so a
            # seeded value would have been loudly wrong rather than quietly.
            "level_db": {"type": "number", "label": "Level", "min": -80.0},
            "power": {"type": "enum", "label": "Power", "values": ["off", "on"]},
            "muted": {"type": "boolean", "label": "Muted"},
            "model": {"type": "string", "label": "Model"},
        },
        "child_entity_types": {
            "lamp": {
                "label": "Lamp",
                "id_format": {"type": "integer", "min": 1, "max": 4},
                "state_variables": {
                    "hours": {"type": "integer", "label": "Hours", "min": 0},
                    "mode": {
                        "type": "enum", "label": "Mode", "values": ["eco", "full"],
                    },
                },
            },
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _driver(device_id: str = "proj1") -> _AcmeProjector:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return _AcmeProjector(device_id, {}, state, events)


# ── At the source ───────────────────────────────────────────────────────────


def test_a_declared_variable_has_a_key_and_no_value():
    """Both halves matter and they are different facts.

    The key has to be there, or the Builder's picker cannot offer the reading
    and a ``$device.…`` reference warns about a variable the driver plainly
    declares. The value has to be absent, or everything downstream is reading a
    number the platform made up.
    """
    drv = _driver()
    for prop in ("lamp_hours", "level_db", "power", "muted", "model"):
        key = f"device.proj1.{prop}"
        assert drv.state.has(key), f"{prop}: declared, so the key must exist"
        assert drv.state.get(key) is None, f"{prop}: nothing reported it"


def test_the_shapes_that_used_to_invent_the_loudest_values():
    """Named one at a time, because each was a different flavour of wrong."""
    drv = _driver()
    # -80.0 on a fader is fully attenuated, drawn as a real position.
    assert drv.get_state("level_db") is None
    # "off" for a projector that might be on -- and the panel would have drawn
    # its off look with complete confidence.
    assert drv.get_state("power") is None
    # 0 hours on a lamp with 450 on it, which is what fired the alert.
    assert drv.get_state("lamp_hours") is None
    # False is a claim too: an amplifier reading not-muted while it is muted.
    assert drv.get_state("muted") is None


def test_connected_is_still_false_because_that_one_is_known():
    """The platform is not guessing here: a driver being constructed is not
    connected. ``connected`` is a statement about the platform's own doing,
    not a reading off the hardware, which is why it keeps a real value."""
    drv = _driver()
    assert drv.get_state("connected") is False


def test_a_registered_child_reports_nothing_until_it_does():
    drv = _driver()
    drv.register_child("lamp", 1)
    for prop in ("hours", "mode"):
        key = f"device.proj1.lamp.1.{prop}"
        assert drv.state.has(key), prop
        assert drv.state.get(key) is None, prop


def test_the_four_platform_child_keys_keep_their_meaning():
    """These describe the child rather than report from it, so they are the
    platform speaking and they still say something. An unknown ``online``
    would blank the dot on every Child Entities row, the "N down" count and
    the device banner at once."""
    drv = _driver()
    drv.register_child("lamp", 1)
    assert drv.state.get("device.proj1.lamp.1.online") is True
    assert drv.state.get("device.proj1.lamp.1.offline_reason") == ""
    assert drv.state.get("device.proj1.lamp.1.offline_detail") == ""
    assert drv.state.get("device.proj1.lamp.1.label") == ""


def test_a_driver_supplied_initial_state_still_wins():
    """A driver that already knows a child's value at registration -- because
    it read the roster off the device -- is reporting, not guessing."""
    drv = _driver()
    drv.register_child("lamp", 2, initial_state={"hours": 450})
    assert drv.state.get("device.proj1.lamp.2.hours") == 450
    assert drv.state.get("device.proj1.lamp.2.mode") is None


def test_the_first_real_reading_is_a_change_even_when_it_is_zero():
    """The old seeding hid this: a lamp genuinely reading 0 wrote 0 over a
    seeded 0, the store saw no change, and nothing downstream ever learned the
    reading had been taken."""
    drv = _driver()
    seen: list[tuple[str, Any, Any]] = []
    drv.state.subscribe("device.proj1.lamp_hours", lambda k, o, n, s: seen.append((k, o, n)))
    drv.set_state("lamp_hours", 0)
    assert seen == [("device.proj1.lamp_hours", None, 0)]


# ── The store's own absent/None distinction ─────────────────────────────────


def test_setting_none_on_an_absent_key_creates_it():
    """``has()`` has always promised this distinction; ``set()`` could not
    produce it. ``None == None`` short-circuited change detection, so the key
    was never created and a driver declaring an unread variable published
    nothing at all."""
    state = StateStore()
    assert not state.has("device.x.reading")
    state.set("device.x.reading", None)
    assert state.has("device.x.reading")
    assert state.get("device.x.reading") is None
    # And it is genuinely a change, so the relay and the WS clients hear it.
    seen: list[Any] = []
    state.subscribe("device.y.reading", lambda k, o, n, s: seen.append(n))
    state.set("device.y.reading", None)
    assert seen == [None]
    # Writing None again over a None is still no change.
    state.set("device.y.reading", None)
    assert seen == [None]


def test_a_delete_still_reads_as_a_delete_not_a_none():
    """The relay tells the two apart by probing the store, so creating a key
    with None must not look like removing one."""
    state = StateStore()
    state.set("device.x.reading", 5)
    state.delete("device.x.reading")
    assert not state.has("device.x.reading")


# ── What every consumer makes of it ─────────────────────────────────────────


LAMP_MONITOR = {
    "key": "device.proj1.lamp_hours",
    "label": "Lamp Hours",
    "unit": "h",
    "normal_min": 1,
    "normal_max": 2000,
}


def test_the_tile_says_no_reading_rather_than_zero():
    drv = _driver()
    value = drv.state.get(LAMP_MONITOR["key"])
    assert monitors.monitor_status(LAMP_MONITOR, value) == monitors.NO_VALUE
    assert monitors.monitor_reading(LAMP_MONITOR, value) == "—"
    # And once the projector answers, the same monitor judges normally.
    drv.set_state("lamp_hours", 450)
    reported = drv.state.get(LAMP_MONITOR["key"])
    assert monitors.monitor_status(LAMP_MONITOR, reported) == monitors.NORMAL
    assert monitors.monitor_reading(LAMP_MONITOR, reported) == "450 h"


def test_a_guard_cannot_act_on_a_reading_nobody_took():
    """`power != "on"` used to be true against a seeded "off", which is how a
    triggered shutdown macro could run against a projector that had never been
    reached."""
    drv = _driver()
    power = drv.get_state("power")
    assert eval_operator("ne", power, "on") is False
    assert eval_operator("eq", power, "off") is False


@pytest.mark.asyncio
async def test_the_compiled_monitor_rule_does_not_fire_on_an_unreported_key():
    """The finding's own headline: a monitor declaring ``normal_min`` above
    zero fired on the invented zero, and the alert reached somebody's phone.

    Driven through the real ``compile_alert_rules`` and the real AlertMonitor,
    because the claim is about the two agreeing, not about either alone.
    """
    drv = _driver()
    agent = _RecordingAgent(drv.state)
    monitor = AlertMonitor(agent, drv.state, drv.events)
    await monitor.start()
    try:
        rules = monitors.compile_alert_rules([LAMP_MONITOR])
        assert [r["condition"]["operator"] for r in rules] == ["<", ">"]
        monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": rules})

        # Re-writing the declaration is what the seeding used to look like from
        # the alert monitor's side: a value arriving on the key with nobody
        # having reported one.
        drv.set_state("lamp_hours", None)
        assert await _drain(monitor, agent) == []

        # A genuine reading below the floor still fires -- otherwise this test
        # would pass against a build whose alerting is simply broken.
        drv.set_state("lamp_hours", 0)
        fired = await _drain(monitor, agent)
        assert [t for t, _ in fired] == ["alert"]
    finally:
        await monitor.stop()


def test_the_relay_would_ship_it_as_no_reading_rather_than_a_number():
    """What the cloud receives is the same absence, so the health card's tile
    and the local Dashboard cannot disagree. Asserted on the snapshot the relay
    reads rather than through a live connection."""
    drv = _driver()
    snapshot = drv.state.snapshot()
    assert "device.proj1.lamp_hours" in snapshot
    assert snapshot["device.proj1.lamp_hours"] is None


# ── Helpers ─────────────────────────────────────────────────────────────────


class _RecordingAgent:
    """Just enough CloudAgent for the AlertMonitor to run against."""

    def __init__(self, state: StateStore) -> None:
        self.state = state
        self.sent_messages: list[tuple[str, dict]] = []
        self._config = {"features": {"alerts": True}}

    async def send_message(self, msg_type: str, payload: dict) -> None:
        self.sent_messages.append((msg_type, payload))

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    @property
    def connected(self) -> bool:
        return True


async def _drain(monitor: AlertMonitor, agent: _RecordingAgent) -> list:
    """Flush the monitor's pending sends and return the alert traffic."""
    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)
    out = [
        (t, p) for t, p in agent.sent_messages
        if t in ("alert", "alert_resolved")
    ]
    agent.sent_messages.clear()
    return out
