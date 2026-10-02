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

**Its state store notes every write** (:class:`AuditStateStore`): a state
store tells its listeners only about changes, and a device that reports the
same value again after a reconnect has still reported it. The store is the
sandbox's own, so production's is untouched.

**Nothing is written on connect**: the device carries no pending settings.

**Network devices only**: a connection over a serial port is refused.

**Strict state is left off**, although the plan asked for it. Strict mode
raises on a write to undeclared state before the write happens, so the value
is lost and the reply handler (or a poll) fails, and the audit would report a
fault it caused. The contract observer records every such write instead
(``undeclared_state``), which is the finding strict mode was meant to give.
"""

from __future__ import annotations

import time
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
# ``last_error`` writes kept for the commands step to place in time.
ERROR_WRITES_KEPT = 5000

SERIAL_REFUSED = (
    "The audit tests devices on the network, and this connection is a serial port. "
    "Choose a driver that connects over the network."
)
NOT_INSTALLED = "The driver {driver} is not installed. Install it, then try again."


class AuditStateStore(StateStore):
    """The sandbox's state store: production's, noting when each key was
    last written, a write of the value it already had included, and every
    ``last_error`` write with its time (a poll that is refused again writes
    the same text, which changes nothing a subscriber would hear)."""

    def __init__(self) -> None:
        super().__init__()
        self.written: dict[str, float] = {}
        # Every write numbered in order, and each key's latest number: what
        # came after a moment, exactly, where a clock that ticks every 15.6 ms
        # gives two writes one timestamp.
        self.seq = 0
        self.written_seq: dict[str, int] = {}
        # (time, key, value, position), oldest first, at most
        # ERROR_WRITES_KEPT. ``position`` is how long the device's traffic
        # list was at the write, which places it against the requests sent
        # around it exactly, where a clock that ticks every 15.6 ms cannot.
        self.error_writes: list[tuple[float, str, Any, int]] = []
        self.position: Callable[[], int] = lambda: 0

    def _note(self, key: str, value: Any, now: float) -> None:
        self.written[key] = now
        self.seq += 1
        self.written_seq[key] = self.seq
        if key.endswith(".last_error") and len(self.error_writes) < ERROR_WRITES_KEPT:
            self.error_writes.append((now, key, value, self.position()))

    def set(self, key: str, value: Any, source: str = "system") -> None:
        self._note(key, value, time.time())
        super().set(key, value, source)

    def set_batch(self, updates: dict[str, Any], source: str = "system") -> None:
        now = time.time()
        for key, value in updates.items():
            self._note(key, value, now)
        super().set_batch(updates, source)


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


async def target_names(project: Any, address: str, ip: str) -> list[str]:
    """Every way the project names the audited device: what was typed, the
    address it resolved to, and each project device's host name that
    resolves to that address (a device set up as ``amp.local`` is the device
    at the audited IP). Matched by :func:`devices_at_host`."""
    import asyncio
    import ipaddress

    from openavc.audit.footprint import resolve_address
    from openavc.core.device_config import device_connections

    names = [address] + ([ip] if ip and ip != address else [])
    if project is None or not ip:
        return names
    known = {n.strip().rstrip(".").lower() for n in names}
    hosts = []
    for conn in device_connections(project):
        host = (conn.host or "").strip()
        if conn.transport == "serial" or not host or host.rstrip(".").lower() in known:
            continue
        try:
            ipaddress.ip_address(host)
            continue  # an address that is not this one
        except ValueError:
            pass
        if host not in hosts:
            hosts.append(host)
    resolved = await asyncio.gather(*(resolve_address(h) for h in hosts))
    return names + [host for host, found in zip(hosts, resolved) if found == ip]


def unpaused_devices_at(project: Any, state: Any, names: list[str]) -> list[str]:
    """Names of project devices that connect to ``names`` and are not paused.

    The audit pauses these when it starts; one found running later (added to
    the project, or resumed by hand) would share the device with the audit.
    """
    if project is None:
        return []
    out = []
    for conn in devices_at_host(project, names):
        prefix = f"device.{conn.device_id}."
        # A device whose driver is not installed holds no connection.
        if not state.get(prefix + "paused") and not state.get(prefix + "orphaned"):
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
        self.state = AuditStateStore()
        self.events = EventBus()
        self.state.set_event_bus(self.events)
        self.manager = DeviceManager(self.state, self.events)
        self.observer = AuditObserver(device_id, on_entry=on_entry, on_event=on_event)
        self.state.position = lambda: len(self.observer.traffic)
        # The config the device manager was handed, after the resolver.
        self.resolved: dict[str, Any] = {}
        self.transport = ""
        self.started = False
        # Where the driver asked the device to send its events, kept as the
        # device is removed (which drops the subscriptions).
        self._push_callbacks: list[str] = []

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
            raise AuditError(SERIAL_REFUSED)
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
        self._push_callbacks = self.push_callbacks()
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

    def push_callbacks(self) -> list[str]:
        """The URLs the driver asked the device to send its events to (each
        callback it registered with OpenAVC's HTTP listener)."""
        if not self.started:
            return list(self._push_callbacks)
        from openavc.transport import http_listener

        host = str((self.resolved.get("config") or {}).get("host") or "")
        return http_listener.callback_urls_for(self.device_id, host)

    def written_after(self, seq: int) -> set[str]:
        """The device's state keys (without the ``device.<id>.`` prefix)
        written after the store's write number ``seq`` (``state.seq`` taken
        earlier), whether or not the value changed."""
        prefix = f"device.{self.device_id}."
        return {
            key[len(prefix):] for key, n in self.state.written_seq.items()
            if n > seq and key.startswith(prefix)
        }

    def written_since(self, since: float) -> set[str]:
        """The device's state keys (without the ``device.<id>.`` prefix)
        written at or after ``since``, whether or not the value changed."""
        prefix = f"device.{self.device_id}."
        return {
            key[len(prefix):] for key, t in self.state.written.items()
            if t >= since and key.startswith(prefix)
        }
