"""A bridge's ports, and a device that sends through one.

A bridge is a device that exposes typed ports (serial, IR, relay) which
other devices connect through: the pro-AV port-binding model. A bridge
driver declares its ports in ``DRIVER_INFO["bridge"]["ports"]`` and
overrides the methods here that it supports; the defaults support none.

The rules worth knowing before changing anything here:

* The platform resolves a downstream device's connection to the bridge's
  pass-through endpoint (``engine.resolved_device_config``) and calls
  ``prepare_bridge_port()`` on the live bridge before that device connects,
  and again whenever the bridge itself reconnects, so a bridge's override
  must be idempotent. A raise there never blocks the downstream connect.
* A bridge that emits commands for downstream devices (IR) owns its command
  socket. The platform hands ``bridge_emit()`` a vendor-neutral payload and
  the bridge turns it into its own wire format. A bridge-routed device
  reaches it through ``emit_via_bridge()``, by the router the device manager
  injects (core/device_bridges.py).
* IR codes are stored as Pronto hex: ``bridge_import_code()`` and the learner
  turn a bridge's native format into it.

``BridgePorts`` is a mixin ``BaseDriver`` inherits, so every name stays on
the driver object, where a bridge driver overrides it. From its host this
needs ``device_id``, ``config``, ``DRIVER_INFO`` and the ``_bridge_router``
its constructor sets to None.
"""

from __future__ import annotations

from typing import Any


class BridgePorts:
    """A bridge's ports, for ``BaseDriver`` (see the module docstring)."""

    # Supplied by BaseDriver; declared here for the methods.
    DRIVER_INFO: dict[str, Any]
    device_id: str
    config: dict[str, Any]
    _bridge_router: Any

    @property
    def is_bridge(self) -> bool:
        """True if this driver advertises bridge ports."""
        return bool((self.DRIVER_INFO.get("bridge") or {}).get("ports"))

    async def prepare_bridge_port(
        self, port_id: str, params: dict[str, Any]
    ) -> None:
        """Configure one of this bridge's ports for a device bound to it.

        Called by the platform on the *bridge* driver. For a serial port a
        bridge pushes the downstream's baud/parity to the hardware here, so the
        transparent pass-through carries bytes at the right line settings;
        ``params`` is the downstream's resolved connection config (baudrate,
        parity, bytesize, stopbits, ...). Default: no-op — a bridge that needs
        no per-port setup, or a port kind that routes commands at send time
        (IR / relay), does nothing. Raising does NOT block the downstream
        connect (the platform logs and proceeds), so a transient bridge-side
        failure can't strand the downstream device offline.

        **Make it idempotent.** It runs before a downstream connects, and again
        for every bound port whenever the bridge itself reconnects — otherwise
        a bridge that lost its settings to a power cut would carry a device that
        was added while it was down at whatever its memory held. That second
        call can therefore arrive while the downstream is already connected
        through the pass-through, so do the work on the bridge's own command
        connection rather than anything that would disturb the data path.
        """

    # A bridge that emits commands for downstream devices (IR, and any future
    # non-pass-through kind) owns its command socket and multiplexes it. The
    # platform speaks a vendor-neutral payload; the bridge driver translates to
    # its own wire format. For IR: kind == "ir", payload == {"pronto": <hex>,
    # "repeat": <int>}. Default: not an emitting bridge.
    async def bridge_emit(
        self, port_id: str, kind: str, payload: dict[str, Any]
    ) -> Any:
        """Emit a downstream device's command through one of this bridge's ports.

        Called on the *bridge* driver by the DeviceManager router when a
        bridge-routed downstream device sends a command. Override in an emitting
        bridge (e.g. IR) to convert ``payload`` to the hardware wire format and
        send it on the bridge's command socket. Default raises.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not emit through bridge ports"
        )

    async def bridge_import_code(self, wire: str) -> str:
        """Convert a bridge-native wire code (as an integrator might paste from a
        manual, e.g. a Global Cache ``sendir`` string) to vendor-neutral Pronto
        hex for storage in an IR device's code-set.

        Called on the *bridge* driver so the (vendor-specific) wire format is
        parsed by the code that owns it; the platform stores only Pronto. The
        default has no wire format to import from. Raise ValueError for an
        unparseable code.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot import a native IR code"
        )

    @property
    def can_learn(self) -> bool:
        """True if this bridge can capture codes from a remote (IR learner).

        Override to return True on a bridge that implements bridge_learn_*.
        """
        return False

    async def bridge_learn_start(self) -> None:
        """Begin a learn session (e.g. enable the IR learner on a dedicated
        socket and pause polling). Override on a learning bridge. Default raises.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support learning"
        )

    async def bridge_learn_poll(self, timeout: float) -> str | None:
        """Wait up to ``timeout`` seconds for the next captured code.

        Returns the captured code as vendor-neutral Pronto hex, or None on
        timeout (so the caller can loop for continuous auto-capture). Override
        on a learning bridge.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support learning"
        )

    async def bridge_learn_stop(self) -> None:
        """End the learn session (disable the learner, close the socket, resume
        polling). Override on a learning bridge. Must be safe to call twice.
        """

    async def emit_via_bridge(self, kind: str, payload: dict[str, Any]) -> Any:
        """Emit ``payload`` through the bridge this device is bound to.

        For a bridge-routed device (its config carries ``bridge`` +
        ``bridge_port``): looks up the live bridge via the injected router and
        calls its bridge_emit. Raises ConnectionError if the device is not
        bridge-bound or the bridge is unavailable.
        """
        bridge_id = self.config.get("bridge")
        port_id = self.config.get("bridge_port")
        if not bridge_id or not port_id:
            raise ConnectionError(f"[{self.device_id}] not bound to a bridge port")
        if self._bridge_router is None:
            raise ConnectionError(
                f"[{self.device_id}] bridge routing unavailable"
            )
        return await self._bridge_router(bridge_id, port_id, kind, payload)
