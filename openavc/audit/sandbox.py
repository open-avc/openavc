"""The driver sandbox: the chosen driver against the audited device, as production runs it.

**Same code as a project device.** A private ``DeviceManager(StateStore(),
EventBus())`` runs the installed driver class from the same registry, with a
config from the same resolver (``core.device_config.resolve_device_config``)
given an audit-scoped project view, registered with ``add_device`` and dialed
with ``bring_up`` exactly as the engine's reconcile does. So connect, sign-in,
the start-up steps, polling, reconnect with its backoff and the typed offline
reasons are production's own, and so is every gate ``send_command`` applies.
The resolver takes any object with ``connections`` and ``devices``, so no
second resolver was needed: ``AuditProjectView`` is an empty connection table
and no devices (a bridge joins it for a bridged device).

**It reaches nothing else.** The state store and event bus are the sandbox's
own, so no WebSocket client, cloud relay, alert monitor, trigger, script or
plugin hears the device. It is not in the project, so a project save cannot
remove it, and simulation redirects the engine's devices only.

**The globals it does share**, and how each is handled:

- The secret registry and the traffic recorder are keyed by device id
  (``audit-<session>``); removing the device forgets both, after the audit's
  observer has taken what it needs (``AuditObserver.snapshot_secrets``).
- The HTTP push registry is keyed by device id as well, and the push route
  reaches whatever subscribed, not only the engine's devices.
- Shared push listener ports separate subscribers by source address, and the
  audit pauses every project device that uses the audited address before the
  driver starts. A project device at that address that is not paused (added
  or resumed since) is refused in words (:func:`unpaused_devices_at`).
- Its log lines carry the ``[audit-...]`` prefix; its web-UI probe runs, as
  in production.

**Nothing is written on connect**: the device carries no pending settings.

**A serial port that would simulate is refused** (a ``SIM:`` path, or serial
support missing), since a report about a simulated port describes nothing.

**Strict state is left off**, although the plan asked for it. Strict mode
raises on a write to undeclared state before the write happens, so the value
is lost and the reply handler (or a poll) fails, and the audit would report a
fault it caused. The contract observer records every such write instead
(``undeclared_state``), which is the finding strict mode was meant to give.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from openavc.audit.observe import AuditObserver, ContractEvent
from openavc.audit.session import AuditError
from openavc.core.device_config import devices_at_host, resolve_device_config
from openavc.core.device_manager import DeviceManager
from openavc.core.device_traffic import TrafficEntry
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.registry import get_driver_class, get_driver_transport
from openavc.utils.logger import get_logger

log = get_logger(__name__)

AUDIT_DEVICE_PREFIX = "audit-"

SIMULATED_PORT = (
    "The serial port {port} is a simulated port (its name starts with SIM:). "
    "Choose the port the device is plugged into."
)
NO_SERIAL_SUPPORT = (
    "This copy of OpenAVC cannot open serial ports, because its serial support "
    "is not installed. Reinstall OpenAVC, then run the audit again."
)
NOT_INSTALLED = "The driver {driver} is not installed. Install it, then try again."


def audit_device_id(session_id: str) -> str:
    """The temporary device's id: never a project device's (their ids are the
    person's), and the prefix every log line of it carries."""
    return f"{AUDIT_DEVICE_PREFIX}{session_id}"


@dataclass
class AuditProjectView:
    """What ``resolve_device_config`` reads from a project: the connection
    table and the devices a binding can name. Empty for a direct device."""

    connections: dict[str, dict[str, Any]] = field(default_factory=dict)
    devices: list[Any] = field(default_factory=list)


def serial_refusal(config: dict[str, Any]) -> str | None:
    """Why a serial connection with this config would not be the real port."""
    from openavc.transport import serial_transport

    port = str(config.get("port") or "")
    if port.upper().startswith("SIM:"):
        return SIMULATED_PORT.format(port=port)
    if not serial_transport.HAS_SERIAL:
        return NO_SERIAL_SUPPORT
    return None


def unpaused_devices_at(project: Any, state: Any, names: list[str]) -> list[str]:
    """Names of project devices that connect to ``names`` and are not paused.

    The audit pauses these when it starts; one found running later (added to
    the project, or resumed by hand) would share the device with the audit.
    """
    if project is None:
        return []
    out = []
    for conn in devices_at_host(project, names):
        if not state.get(f"device.{conn.device_id}.paused"):
            out.append(conn.name)
    return out


class DriverSandbox:
    """One driver running against the audited device, and nothing else."""

    def __init__(
        self,
        device_id: str,
        driver_id: str,
        config: dict[str, Any],
        *,
        name: str = "Audited device",
        project_view: AuditProjectView | None = None,
        on_entry: Callable[[TrafficEntry], None] | None = None,
        on_event: Callable[[ContractEvent], None] | None = None,
    ) -> None:
        self.device_id = device_id
        self.driver_id = driver_id
        self.name = name
        self.config = dict(config)
        self.project_view = project_view or AuditProjectView()
        self.state = StateStore()
        self.events = EventBus()
        self.state.set_event_bus(self.events)
        self.manager = DeviceManager(self.state, self.events)
        self.observer = AuditObserver(device_id, on_entry=on_entry, on_event=on_event)
        # The config the device manager was handed, after the resolver.
        self.resolved: dict[str, Any] = {}
        self.transport = ""
        self.started = False

    # -- lifecycle ------------------------------------------------------------

    def prepare(self) -> dict[str, Any]:
        """Resolve the config and refuse what would not be a real test.

        Returns the resolved device record. Raises ``AuditError`` with the
        sentence the person reads.
        """
        if get_driver_class(self.driver_id) is None:
            raise AuditError(NOT_INSTALLED.format(driver=self.driver_id))
        record = {
            "id": self.device_id,
            "driver": self.driver_id,
            "name": self.name,
            "config": dict(self.config),
        }
        resolved = resolve_device_config(record, self.project_view)
        transport = str(
            resolved["config"].get("transport") or get_driver_transport(self.driver_id) or "tcp"
        )
        if transport == "serial":
            refusal = serial_refusal(resolved["config"])
            if refusal:
                raise AuditError(refusal)
        self.resolved = resolved
        self.transport = transport
        return resolved

    async def start(self) -> None:
        """Register the device (not yet dialed) and start observing it.

        The observer subscribes before the driver exists and is attached as
        the driver's contract observer before it connects, so the first byte
        of the connection and every reply to a start-up step are caught.
        """
        if self.started:
            return
        resolved = self.resolved or self.prepare()
        self.observer.start()
        try:
            await self.manager.add_device(resolved, defer_connect=True)
        except Exception:
            self.observer.stop()
            raise
        driver = self.manager.get_driver(self.device_id)
        if driver is not None:
            self.observer.attach(driver)
        self.observer.snapshot_secrets()
        self.started = True
        log.info(
            "[%s] Audit sandbox holds driver '%s' for %s",
            self.device_id, self.driver_id, resolved["config"].get("host")
            or resolved["config"].get("port"),
        )

    async def connect(self) -> None:
        """Dial the device the way the engine's reconcile does. Never raises
        for a failed connect: that is reported as the offline reason, and the
        reconnect loop carries on, as in production."""
        await self.manager.bring_up()

    async def stop(self) -> None:
        """Remove the device: its connection, its loops, its secrets and its
        recorded ring go, and the observer keeps what the report needs."""
        if not self.started:
            return
        self.started = False
        self.observer.snapshot_secrets()
        try:
            await self.manager.remove_device(self.device_id)
        finally:
            self.observer.stop()

    # -- reading ----------------------------------------------------------------

    @property
    def driver(self) -> Any:
        return self.manager.get_driver(self.device_id)

    def device_state(self) -> dict[str, Any]:
        """Every state key the device has, without the ``device.<id>.`` prefix."""
        return self.state.get_namespace(f"device.{self.device_id}.")

    def connected(self) -> bool:
        return bool(self.state.get(f"device.{self.device_id}.connected"))
