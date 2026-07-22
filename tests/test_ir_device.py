"""Tests for the IR code-set device model and bridge-state mirroring.

Exercises the platform capability, not any real device: an invented ``acme_ir``
IR driver (built on ConfigurableDriver via the ``ir_codes`` opt-in) and an
invented ``acme_bridge`` emitting bridge. Covers:

  * an IR code-set (device config + a community driver's default_config)
    surfacing as device commands, with the UI meta stripped of the raw code;
  * send_command routing an IR command through the bound bridge;
  * the DeviceManager mirroring a bridge's online state onto its bridge-routed
    dependents (online seed, disconnect, reconnect, reconnect-exemption).

No real hardware, no vendor protocol.
"""

from __future__ import annotations

import asyncio
from typing import Any

from server.core.connection_fault import BRIDGE_OFFLINE
from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import BaseDriver
from server.drivers.configurable import create_configurable_driver_class


def _acme_ir_class(default_codes: dict[str, Any] | None = None):
    """An invented IR driver: ConfigurableDriver + the ir_codes opt-in.

    ``default_codes`` stands in for a community IR driver's shipped code-set in
    default_config.ir_codes.
    """
    return create_configurable_driver_class(
        {
            "id": "acme_ir",
            "name": "Acme IR",
            "manufacturer": "Acme",
            "category": "display",
            "transport": "bridge",
            "ir_codes": True,
            "default_config": {"ir_codes": default_codes or {}},
            "commands": {},
            "state_variables": {},
        }
    )


def _mk(cls, config, device_id="tv1"):
    return cls(device_id, config, StateStore(), EventBus())


# --- code-set → commands ----------------------------------------------------


def test_ir_codes_surface_as_commands_ui_meta_stripped():
    cls = _acme_ir_class()
    dev = _mk(
        cls,
        {
            "transport": "bridge",
            "bridge": "b1",
            "bridge_port": "ir:1",
            "ir_codes": {
                "power_on": {"label": "Power On", "pronto": "0000 006D 0000 0001 0060 0018", "repeat": 1},
                "vol_up": {"label": "Volume Up", "pronto": "0000 006D 0000 0001 0030 0018", "repeat": 2},
            },
        },
    )
    cmds = dev.DRIVER_INFO["commands"]
    assert set(cmds) == {"power_on", "vol_up"}
    assert cmds["power_on"]["label"] == "Power On"
    # UI meta must not carry the raw code; the runtime definition must.
    assert "ir" not in cmds["power_on"]
    assert dev._definition["commands"]["vol_up"]["ir"]["repeat"] == 2
    assert dev._definition["commands"]["power_on"]["ir"]["pronto"].startswith("0000 006D")


def test_device_config_codes_override_driver_default_codes():
    # A community driver ships codes in default_config; the resolver layers them
    # into config (defaults < device config). A device-authored code of the same
    # name wins; codes only in the default still appear.
    cls = _acme_ir_class(
        default_codes={
            "power_on": {"label": "Power", "pronto": "0000 006D 0000 0001 0060 0018", "repeat": 1},
            "mute": {"label": "Mute", "pronto": "0000 006D 0000 0001 0040 0018", "repeat": 1},
        }
    )
    # This test covers command-surfacing from an already-resolved ir_codes map;
    # the per-code overlay itself (device codes layered onto the driver default,
    # not replacing it) is exercised against the real merge in
    # test_resolved_device_config.py::test_device_ir_codes_overlay_driver_defaults.
    default_codes = cls.DRIVER_INFO["default_config"]["ir_codes"]
    device_codes = {"power_on": {"label": "Power (custom)", "pronto": "0000 006D 0000 0001 0099 0018", "repeat": 3}}
    merged = {**default_codes, **device_codes}
    dev = _mk(cls, {"transport": "bridge", "bridge": "b1", "bridge_port": "ir:1", "ir_codes": merged})
    cmds = dev.DRIVER_INFO["commands"]
    assert set(cmds) == {"power_on", "mute"}
    assert cmds["power_on"]["label"] == "Power (custom)"
    assert dev._definition["commands"]["power_on"]["ir"]["repeat"] == 3
    assert dev._definition["commands"]["mute"]["ir"]["repeat"] == 1


def test_ir_send_routes_through_the_bridge():
    cls = _acme_ir_class()
    dev = _mk(
        cls,
        {
            "transport": "bridge",
            "bridge": "b1",
            "bridge_port": "ir:2",
            "ir_codes": {"power_on": {"pronto": "0000 006D 0000 0001 0060 0018", "repeat": 4}},
        },
    )
    calls: list[tuple] = []

    async def router(bridge_id, port_id, kind, payload):
        calls.append((bridge_id, port_id, kind, payload))
        return "ok"

    dev._bridge_router = router
    assert asyncio.run(dev.send_command("power_on")) == "ok"
    assert calls == [
        ("b1", "ir:2", "ir", {"pronto": "0000 006D 0000 0001 0060 0018", "repeat": 4})
    ]


def test_bad_ir_code_is_skipped_not_fatal():
    cls = _acme_ir_class()
    dev = _mk(
        cls,
        {
            "transport": "bridge",
            "bridge": "b1",
            "bridge_port": "ir:1",
            "ir_codes": {
                "good": {"pronto": "0000 006D 0000 0001 0060 0018"},
                "bad": {"label": "no pronto here"},
            },
        },
    )
    assert set(dev.DRIVER_INFO["commands"]) == {"good"}
    # A code with no repeat defaults to 1.
    assert dev._definition["commands"]["good"]["ir"]["repeat"] == 1


# --- DeviceManager bridge-state mirroring -----------------------------------


class _FakeBridge(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_bridge",
        "name": "Acme Bridge",
        "category": "utility",
        "transport": "tcp",
        "bridge": {"ports": [{"id": "ir:1", "kind": "ir", "label": "IR 1"}]},
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def bridge_emit(self, port_id: str, kind: str, payload: dict) -> Any:
        return {"status": "ok"}


def _dm_with_bridge_and_ir():
    """A DeviceManager holding a live bridge and a bridge-routed IR device."""
    dm = DeviceManager(StateStore(), EventBus())
    bridge = _FakeBridge("b1", {"host": "192.0.2.9"}, dm.state, dm.events)
    dm._devices["b1"] = bridge
    dm._device_configs["b1"] = {"id": "b1", "driver": "acme_bridge", "config": {"host": "192.0.2.9"}}
    dm.state.set("device.b1.name", "Acme Bridge")

    ir_cls = _acme_ir_class()
    ir = ir_cls("tv1", {"transport": "bridge", "bridge": "b1", "bridge_port": "ir:1"}, dm.state, dm.events)
    ir._bridge_routed = True
    dm._devices["tv1"] = ir
    dm._device_configs["tv1"] = {
        "id": "tv1",
        "driver": "acme_ir",
        "config": {"transport": "bridge", "bridge": "b1", "bridge_port": "ir:1"},
    }
    return dm, bridge, ir


def test_dependents_lookup_only_finds_bridge_routed_devices():
    dm, _bridge, _ir = _dm_with_bridge_and_ir()
    assert dm._bridge_routed_dependents("b1") == ["tv1"]
    # A serial pass-through downstream (transport tcp) is NOT a mirrored dependent.
    dm._device_configs["ser1"] = {
        "id": "ser1", "driver": "x",
        "config": {"transport": "tcp", "bridge": "b1", "bridge_port": "serial:1"},
    }
    dm._devices["ser1"] = object()
    assert dm._bridge_routed_dependents("b1") == ["tv1"]


def test_bridge_connect_brings_dependents_online():
    dm, _bridge, ir = _dm_with_bridge_and_ir()
    ir.set_state("connected", False)
    ir._connected = False
    dm.state.set("device.tv1.offline_reason", BRIDGE_OFFLINE)

    asyncio.run(dm._on_device_connected("device.connected.b1", {}))

    assert ir.get_state("connected") is True
    assert ir._connected is True
    assert dm.state.get("device.tv1.offline_reason") is None


def test_bridge_disconnect_takes_dependents_offline_with_reason():
    dm, _bridge, ir = _dm_with_bridge_and_ir()
    ir.set_state("connected", True)
    ir._connected = True

    asyncio.run(dm._on_device_disconnected("device.disconnected.b1", {}))

    assert ir.get_state("connected") is False
    assert ir._connected is False
    assert dm.state.get("device.tv1.offline_reason") == BRIDGE_OFFLINE
    detail = dm.state.get("device.tv1.offline_detail")
    assert detail and "Acme Bridge" in detail


def test_bridge_routed_device_is_exempt_from_auto_reconnect():
    dm, _bridge, ir = _dm_with_bridge_and_ir()
    ir.set_state("connected", False)
    ir._connected = False
    # A disconnect event for the IR device itself must not spin up a reconnect
    # loop — it has no transport to reconnect.
    asyncio.run(dm._on_device_disconnected("device.disconnected.tv1", {}))
    assert "tv1" not in dm._reconnect_tasks


def test_mirror_emits_lifecycle_events_only_on_transition():
    dm, _bridge, ir = _dm_with_bridge_and_ir()
    events: list[str] = []
    dm.events.on("device.connected.tv1", lambda e, p: events.append(e))
    dm.events.on("device.disconnected.tv1", lambda e, p: events.append(e))

    ir.set_state("connected", False)
    ir._connected = False
    # First connect transition -> one connected event.
    asyncio.run(dm._on_device_connected("device.connected.b1", {}))
    # A second connect with no state change -> no duplicate event.
    asyncio.run(dm._on_device_connected("device.connected.b1", {}))
    assert events == ["device.connected.tv1"]
