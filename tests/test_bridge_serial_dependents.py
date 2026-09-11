"""A bridge and the serial device bound through it: fault truth, and line settings.

Two gaps met at the same rig. A serial downstream has its connection rewritten
to the bridge's transparent pass-through, so it is nobody's mirror: it dials the
bridge itself and keeps its own reconnect loop. That left it describing the
pass-through socket when its bridge died ("Connection refused on 127.0.0.1:4999",
an address nobody typed), and left its line settings pushed only at add time, so
a bridge that healed later served it at whatever its NVRAM held.

Invented devices only (core-test rule): an Acme bridge advertising one serial and
one IR port, and a widget bound to the serial one.
"""

from __future__ import annotations

import asyncio
from typing import Any

from openavc.core.connection_fault import BRIDGE_OFFLINE, ConnectionFaultError
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver

PASSTHROUGH = 4999


class _AcmeBridge(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_bridge",
        "name": "Acme Bridge",
        "category": "utility",
        "transport": "tcp",
        "bridge": {
            "ports": [
                {
                    "id": "serial:1",
                    "kind": "serial",
                    "label": "Serial 1",
                    "passthrough_port": PASSTHROUGH,
                },
                {"id": "ir:1", "kind": "ir", "label": "IR 1"},
            ]
        },
        "state_variables": {},
        "commands": {},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.prepared: list[tuple[str, int | None]] = []
        self.prepare_raises = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def prepare_bridge_port(self, port_id: str, params: dict) -> None:
        if self.prepare_raises:
            raise RuntimeError("bridge said no")
        self.prepared.append((port_id, params.get("baudrate")))


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "category": "utility",
        "transport": "serial",
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _rig(*, bridge_online: bool = True, register_bridge: bool = True):
    """A manager holding an Acme bridge and a widget bound to its serial port.

    The widget's config is the RESOLVED one: `resolve_bridge_binding` has already
    rewritten transport/host/port to the bridge's pass-through and left the
    `bridge` / `bridge_port` markers behind, which is the shape the manager sees.
    """
    dm = DeviceManager(StateStore(), EventBus())
    bridge = _AcmeBridge("b1", {"host": "192.0.2.9"}, dm.state, dm.events)
    if register_bridge:
        dm._devices["b1"] = bridge
        dm._device_configs["b1"] = {
            "id": "b1", "driver": "acme_bridge", "config": {"host": "192.0.2.9"},
        }
        bridge.set_state("connected", bridge_online)
        bridge._connected = bridge_online
    dm.state.set("device.b1.name", "Acme Bridge")

    resolved = {
        "transport": "tcp",
        "host": "127.0.0.1",
        "port": PASSTHROUGH,
        "bridge": "b1",
        "bridge_port": "serial:1",
        "baudrate": 9600,
    }
    widget = _AcmeWidget("w1", dict(resolved), dm.state, dm.events)
    dm._devices["w1"] = widget
    dm._device_configs["w1"] = {"id": "w1", "driver": "acme_widget", "config": resolved}
    return dm, bridge, widget


def _refused() -> OSError:
    return ConnectionRefusedError(61, "Connection refused")


# --- fault truth: a dead bridge is named, not its pass-through socket ---


def test_dead_bridge_is_named_instead_of_the_passthrough_address():
    dm, _bridge, widget = _rig(bridge_online=False)

    code = dm._set_offline_reason("w1", widget, exc=_refused())

    assert code == BRIDGE_OFFLINE
    assert dm.state.get("device.w1.offline_reason") == BRIDGE_OFFLINE
    detail = dm.state.get("device.w1.offline_detail")
    assert detail and "Acme Bridge" in detail
    # The address the integrator never typed must not be what they are shown.
    assert str(PASSTHROUGH) not in detail


def test_a_bridge_whose_driver_is_not_installed_yet_still_names_the_bridge():
    # The window before the bridge driver exists: nothing is registered under
    # its id, so the downstream used to fall back to raw-serial wording that is
    # wrong about both the transport and the box at fault.
    dm, _bridge, widget = _rig(register_bridge=False)

    assert dm._set_offline_reason("w1", widget, exc=_refused()) == BRIDGE_OFFLINE
    assert dm.state.get("device.w1.offline_reason") == BRIDGE_OFFLINE


def test_a_live_bridge_leaves_the_failure_with_the_device():
    # Guard the guard: blaming the bridge whenever one is bound would point the
    # integrator at the wrong box for an unplugged device.
    dm, _bridge, widget = _rig(bridge_online=True)

    code = dm._set_offline_reason("w1", widget, exc=_refused())

    assert code != BRIDGE_OFFLINE
    assert dm.state.get("device.w1.offline_reason") != BRIDGE_OFFLINE


def test_a_binding_with_no_port_was_never_rewritten_so_the_bridge_is_not_blamed():
    # `resolve_bridge_binding` needs both markers to rewrite anything. With only
    # `bridge`, the device is dialling its own address and the bridge is not in
    # the path at all.
    dm, _bridge, widget = _rig(bridge_online=False)
    dm._device_configs["w1"]["config"].pop("bridge_port")

    assert dm._set_offline_reason("w1", widget, exc=_refused()) != BRIDGE_OFFLINE


def test_a_device_that_answered_keeps_its_own_fault_over_the_bridge():
    # Precedence: a typed fault means the device itself replied, which pins the
    # failure to the device -- and auth_failed steers the reconnect policy, so
    # masking it with the carrier would silently resume hammering a bad login.
    dm, _bridge, widget = _rig(bridge_online=False)

    code = dm._set_offline_reason(
        "w1", widget, exc=ConnectionFaultError("Login rejected", code="auth_failed"),
    )

    assert code == "auth_failed"
    assert dm.state.get("device.w1.offline_reason") == "auth_failed"


# --- line settings: a bridge that heals re-prepares what it carries ---


def test_a_bridge_coming_online_re_pushes_its_dependents_line_settings():
    dm, bridge, _widget = _rig(bridge_online=True)
    assert bridge.prepared == []

    asyncio.run(dm._on_device_connected("device.connected.b1", {}))

    assert bridge.prepared == [("serial:1", 9600)]


def test_an_ordinary_device_connecting_prepares_nothing():
    # This runs on the connect path EVERY device takes, so it has to stay a
    # no-op for anything that is not a bridge with dependents.
    dm, bridge, _widget = _rig(bridge_online=True)

    asyncio.run(dm._on_device_connected("device.connected.w1", {}))

    assert bridge.prepared == []


def test_a_bridge_side_failure_does_not_break_the_connect_handler():
    # Same contract the add-time path has: preparing is best effort and must not
    # strand anything. The IR mirror below it still has to run.
    dm, bridge, _widget = _rig(bridge_online=True)
    bridge.prepare_raises = True
    ir = _AcmeWidget(
        "tv1", {"transport": "bridge", "bridge": "b1", "bridge_port": "ir:1"},
        dm.state, dm.events,
    )
    ir._bridge_routed = True
    ir.set_state("connected", False)
    dm._devices["tv1"] = ir
    dm._device_configs["tv1"] = {
        "id": "tv1", "driver": "acme_widget",
        "config": {"transport": "bridge", "bridge": "b1", "bridge_port": "ir:1"},
    }

    asyncio.run(dm._on_device_connected("device.connected.b1", {}))

    assert ir.get_state("connected") is True


def test_every_bound_port_kind_is_prepared_not_just_the_mirrored_ones():
    # The mirrored-dependents lookup is IR-only by design; preparing ports is
    # not, and reusing that lookup here would have skipped the serial device
    # this whole path exists for.
    dm, _bridge, _widget = _rig(bridge_online=True)
    dm._devices["tv1"] = _AcmeWidget(
        "tv1", {"transport": "bridge", "bridge": "b1", "bridge_port": "ir:1"},
        dm.state, dm.events,
    )
    dm._device_configs["tv1"] = {
        "id": "tv1", "driver": "acme_widget",
        "config": {"transport": "bridge", "bridge": "b1", "bridge_port": "ir:1"},
    }

    assert sorted(dm._bridge_bound_dependents("b1")) == ["tv1", "w1"]
    assert dm._bridge_routed_dependents("b1") == ["tv1"]
