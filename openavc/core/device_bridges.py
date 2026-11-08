"""Devices that reach their equipment through a bridge: the part of DeviceManager
that prepares a bridge's ports, carries a bridge-routed device's commands to the
bridge, and keeps the devices a bridge carries in step with it.

A device is bound to a bridge port when its resolved config names both
``bridge`` and ``bridge_port`` (core/device_config.py). There are two kinds. A
serial device has its connection rewritten to the bridge's pass-through and
dials it itself. An IR device on an emitter port (resolved transport ``bridge``)
has no connection at all: it emits through the live bridge instance, and is
online exactly while its bridge is.

The rule worth knowing before changing it: only the second kind MIRRORS the
bridge (its connected state, a lifecycle event on a real edge only, and the
``bridge_offline`` reason). Both kinds have their port prepared by the bridge
(a serial port's line settings), when they are added and again each time the
bridge comes back, because a bridge that was down when its device was added
still holds whatever settings it had. Preparing a port is best-effort and never
raises: a failure on the bridge's side must not strand the device downstream.

Mixed into DeviceManager (core/device_manager.py).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.event_bus import EventBus
    from openavc.drivers.base import BaseDriver

log = get_logger("openavc.core.device_manager")


def _binds_bridge_port(config: dict[str, Any]) -> bool:
    """True when a resolved config is bound to a port on a bridge: both markers
    set, which is also the precondition for its connection being rewritten."""
    return bool(config.get("bridge") and config.get("bridge_port"))


class DeviceBridgesMixin:
    """Bridge ports, bridge-routed commands, and the bridge's state mirrored
    onto the devices it carries.

    Holds no state of its own. From its host (DeviceManager) it reads
    ``events``, ``_devices`` and ``_device_configs``, and asks
    ``_set_bridge_offline_reason`` and ``_clear_offline_reason``
    (core/device_away.py).
    """

    events: EventBus
    _devices: dict[str, BaseDriver]
    _device_configs: dict[str, dict[str, Any]]

    def _attach_bridge_router(self, driver: BaseDriver, config: dict[str, Any]) -> None:
        """Hand a device bound to a bridge port the router that reaches its
        bridge at send time (an IR device emits through the live bridge)."""
        if _binds_bridge_port(config):
            driver._bridge_router = self._route_bridge_command

    async def _prepare_bridge_for(
        self, device_id: str, config: dict[str, Any]
    ) -> None:
        """Best-effort: ask the live bridge driver to prepare a port before a
        bridge-bound downstream device connects.

        The bridge driver instance owns the bridge's command socket; preparing
        the port (for serial, pushing baud/parity via the bridge protocol) makes
        the transparent pass-through carry bytes at the right line settings. A
        missing / not-yet-live bridge is logged and skipped — bridges are
        ordered ahead of their dependents on load, and serial config persists on
        the hardware, so a transient miss self-heals on the next add/edit. Never
        raises: a bridge-side failure must not strand the downstream offline.
        """
        if not _binds_bridge_port(config):
            return
        bridge_id = config.get("bridge")
        bridge_port = config.get("bridge_port")
        bridge = self._devices.get(bridge_id)
        if bridge is None or not getattr(bridge, "is_bridge", False):
            log.debug(
                "Device '%s' binds bridge '%s' but it isn't a live bridge "
                "instance yet — skipping port prep", device_id, bridge_id,
            )
            return
        try:
            await bridge.prepare_bridge_port(bridge_port, config)
        except Exception:
            log.warning(
                "Bridge '%s' failed to prepare port '%s' for device '%s' — "
                "connecting anyway", bridge_id, bridge_port, device_id,
                exc_info=True,
            )

    async def _route_bridge_command(
        self, bridge_id: str, port_id: str, kind: str, payload: dict[str, Any]
    ) -> Any:
        """Route a bridge-routed downstream device's command to its live bridge.

        Injected into bridge-routed drivers as their ``_bridge_router`` so a
        command (e.g. an IR device's code) reaches the bridge instance that owns
        the hardware socket. Raises ConnectionError with a clear message when the
        bridge is missing, not a bridge, or offline — surfaced to the caller as a
        command failure rather than a silent no-op.
        """
        bridge = self._devices.get(bridge_id)
        if bridge is None or not getattr(bridge, "is_bridge", False):
            raise ConnectionError(f"Bridge '{bridge_id}' is not available")
        if not getattr(bridge, "_connected", False):
            raise ConnectionError(f"Bridge '{bridge_id}' is offline")
        return await bridge.bridge_emit(port_id, kind, payload)

    def _note_bridge_offline_if_down(
        self, device_id: str, driver: BaseDriver, config: dict[str, Any]
    ) -> None:
        """After a connect that did not raise: a bridge-routed device opens no
        socket and comes up online only if its bridge already is, so one still
        offline says its bridge is why. The mirror clears it when the bridge
        comes up."""
        if config.get("transport") != "bridge" or driver.get_state("connected"):
            return
        bridge_id = config.get("bridge")
        if bridge_id:
            self._set_bridge_offline_reason(device_id, bridge_id)

    async def _bridge_came_up(self, bridge_id: str) -> None:
        """A device connected: when it is a bridge, its ports are prepared
        again, then the devices it carries are brought online with it.

        The ports first, so the line settings are on their way before anything
        is told the bridge is back. A no-op for any other device.
        """
        await self._reprepare_bridge_ports(bridge_id)
        deps = self._bridge_routed_dependents(bridge_id)
        if deps:
            await self._mirror_bridge_state(bridge_id, True, deps)

    async def _bridge_went_down(self, bridge_id: str) -> None:
        """A device dropped: when it is a bridge, the devices it carries go
        offline with it. A no-op for any other device."""
        deps = self._bridge_routed_dependents(bridge_id)
        if deps:
            await self._mirror_bridge_state(bridge_id, False, deps)

    async def _reprepare_bridge_ports(self, bridge_id: str) -> None:
        """Re-run the port preparation for every device bound to this bridge.

        A no-op unless ``bridge_id`` really is a live bridge with dependents,
        which is what makes it safe on the connect path every device takes.
        Each call is best-effort and never raises, exactly as at add time.
        """
        bridge = self._devices.get(bridge_id)
        if bridge is None or not getattr(bridge, "is_bridge", False):
            return
        for dev_id in self._bridge_bound_dependents(bridge_id):
            driver = self._devices.get(dev_id)
            if driver is None:
                continue
            await self._prepare_bridge_for(dev_id, driver.config or {})

    def _live_devices_where(
        self, wanted: Callable[[dict[str, Any]], Any]
    ) -> list[str]:
        """Ids of the devices with a live driver whose config ``wanted``
        accepts, in registration order."""
        return [
            dev_id for dev_id, dc in self._device_configs.items()
            if dev_id in self._devices and wanted(dc.get("config", {}))
        ]

    def _bridge_routed_dependents(self, bridge_id: str) -> list[str]:
        """Live device ids that route their commands through ``bridge_id`` and
        have no transport of their own (resolved ``transport == "bridge"``).

        These are the devices whose connected state mirrors the bridge (IR
        devices on emitter ports). A serial pass-through downstream is *not*
        here — it dials the bridge's TCP passthrough and tracks the bridge via
        its own socket, so it needs no mirroring.
        """
        return self._live_devices_where(
            lambda cfg: cfg.get("bridge") == bridge_id and cfg.get("transport") == "bridge"
        )

    def _bridge_bound_dependents(self, bridge_id: str) -> list[str]:
        """Live device ids bound to a port on ``bridge_id``, of every port kind.

        Wider than ``_bridge_routed_dependents``, which is only the devices
        whose connected state MIRRORS the bridge. This is everything the bridge
        has to configure a port for, including the serial pass-through devices
        that dial the bridge themselves and so are nobody's mirror.
        """
        return self._live_devices_where(
            lambda cfg: cfg.get("bridge") == bridge_id and cfg.get("bridge_port")
        )

    def _carrier_bridge_if_offline(self, device_id: str) -> str | None:
        """The id of the bridge carrying ``device_id``, when that bridge is down.

        A serial device bound through a bridge has its connection rewritten to
        the bridge's transparent pass-through (``resolve_bridge_binding``), which
        overwrites host, port and transport but keeps ``bridge`` / ``bridge_port``
        in the resolved config. Those markers are the only thing left tying the
        failing socket back to the bridge, because the address it failed against
        belongs to the bridge rather than to the device.

        Both markers are required, matching the rewrite's own precondition: a
        binding with no port was never rewritten, so that device is dialling its
        own address and the bridge has nothing to do with the failure. ``None``
        also when the bridge is UP -- a live bridge with an unreachable device
        downstream is the device's own fault, and saying otherwise would point
        the integrator at the wrong box.
        """
        cfg = self._device_configs.get(device_id, {}).get("config", {})
        if not _binds_bridge_port(cfg):
            return None
        bridge_id = cfg.get("bridge")
        bridge = self._devices.get(bridge_id)
        # A bridge that is not registered at all (its driver is not installed
        # yet) counts as down: the downstream is certainly not reaching anything
        # through it, and naming the bridge beats describing a socket.
        if bridge is not None and bridge.get_state("connected"):
            return None
        return str(bridge_id)

    async def _mirror_bridge_state(
        self, bridge_id: str, online: bool, deps: list[str]
    ) -> None:
        """Set each bridge-routed dependent's connected state to ``online``."""
        for dev_id in deps:
            driver = self._devices.get(dev_id)
            if driver is not None:
                await self._mirror_onto(dev_id, driver, bridge_id, online)

    async def _mirror_onto(
        self, dev_id: str, driver: BaseDriver, bridge_id: str, online: bool
    ) -> None:
        """One dependent follows its bridge: connected set to ``online``, the
        ``bridge_offline`` reason published going down and cleared coming up,
        then its lifecycle event, only on an actual transition, so triggers see
        one edge, not a stream."""
        was = bool(driver.get_state("connected"))
        driver._bridge_routed = True
        driver._connected = online
        driver.set_state("connected", online)
        if online:
            self._clear_offline_reason(dev_id)
        else:
            self._set_bridge_offline_reason(dev_id, bridge_id)
        if was != online:
            edge = "connected" if online else "disconnected"
            await self.events.emit(f"device.{edge}.{dev_id}")
