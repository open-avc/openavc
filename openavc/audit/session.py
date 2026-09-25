"""One device audit at a time: its target, its lifetime, and how it ends.

An audit points OpenAVC at one device and records what that device does. The
session is the thing every part of it hangs off, and its rules are the ones
that keep an audit from leaving anything behind:

- **One per server.** A second start is refused in words. A Discovery scan and
  an audit never run together either, in both directions: both open the
  multicast listeners and both send probes, so each would read the other's
  traffic as the device's. The manager hands the discovery engine a
  ``scan_blocker`` that says why a scan cannot start, and asks the engine
  before it starts a session.
- **Pauses it owns.** Project devices that use the audited address are paused
  before any traffic goes to it (a device that allows one control connection
  would drop the project's, and the project's traffic would land in the
  audit). The pause has a backstop in the device manager (``PAUSE_TTL``) so a
  lost session cannot strand a device offline; the session keeps it alive by
  re-pausing on a timer, from the server, because the browser that asked may
  be closed. A device that was already paused when the audit started (by the
  Driver Builder's test panel, say) is not the audit's to resume.
- **Every way out is the same teardown.** Finish, Cancel, the idle timeout and
  server shutdown all run ``_teardown``: stop what the session started, then
  resume what it paused. A server crash leaves nothing to tear down in
  process, and the pause backstop brings paused devices back on its own.
- **Idle means nobody asked.** The session ends when no request has touched it
  for ``IDLE_TIMEOUT`` seconds. An open wizard sends nothing while it sits on a
  finished report, so walking away from one ends the session and resumes the
  project's devices.

Everything else a session holds (the network check, the listeners, the report)
is attached by the modules that build it: ``track_task`` for work to cancel,
``on_teardown`` for things to stop. What the browser sees comes through
``subscribe``, one callback per WebSocket client that asked, never a
broadcast.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

log = logging.getLogger("audit.session")

# Thirty minutes without a request ends a session.
IDLE_TIMEOUT = 1800.0
# How often the idle watch looks. Short enough that a timeout lands within a
# minute of its due time, long enough to cost nothing.
_IDLE_CHECK_SECONDS = 30.0
# How often the session re-arms the pauses it owns. Well inside the device
# manager's backstop (600 s), so one missed tick does not resume a device.
KEEPALIVE_SECONDS = 120.0

# Session statuses. "active" until one of the others ends it.
ACTIVE = "active"
FINISHED = "finished"
CANCELLED = "cancelled"
EXPIRED = "expired"
SHUTDOWN = "shutdown"

ENDED_STATUSES = (FINISHED, CANCELLED, EXPIRED, SHUTDOWN)

BUSY_AUDIT = "An audit is already running. Finish it first."
BUSY_SCAN = (
    "A Discovery scan is running. Wait for it to finish, or stop it, "
    "then start the audit."
)
SCAN_BLOCKED = (
    "A device audit is running. Finish the audit before starting a "
    "Discovery scan."
)


class AuditError(Exception):
    """A refusal whose message is the sentence the person reads."""


class AuditBusy(AuditError):
    """Something already holds what the request needs (an audit, a scan)."""


class AuditNotFound(AuditError):
    """No active session has that id."""


class PauseControl(Protocol):
    """The device manager's pause surface, which is all a session touches."""

    async def pause_device(self, device_id: str, ttl: float | None = None) -> None: ...
    async def resume_device(self, device_id: str) -> None: ...
    def is_paused(self, device_id: str) -> bool: ...


class ScanState(Protocol):
    """What a session asks of the discovery engine."""

    scan_blocker: Callable[[], str | None] | None

    def is_scanning(self) -> bool: ...


@dataclass
class AuditTarget:
    """The device under audit.

    ``address`` is what the person typed (an IP address or a host name) and
    ``ip`` what it resolved to; both are kept, because a report has to say
    which name was used and which address answered.
    """

    address: str
    ip: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"address": self.address, "ip": self.ip}


@dataclass
class AuditOptions:
    """The choices made on the first step.

    ``extended``: the slower check (ports 1 to 1024 on top of the standard
    list, and a bounded walk of every SNMP value). ``snmp_communities``: read
    communities to try after ``public``. Kept as typed, since a report records
    which one answered and redaction covers them.
    """

    extended: bool = False
    snmp_communities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "extended": self.extended,
            "snmp_communities": len(self.snmp_communities),
        }


@dataclass
class PausedDevice:
    """A project device the session holds paused, and whether it owns the pause."""

    device_id: str
    name: str
    owned: bool

    def to_dict(self) -> dict[str, Any]:
        return {"device_id": self.device_id, "name": self.name, "owned": self.owned}


@dataclass
class TimelineEntry:
    """One thing that happened, in order. ``t`` is epoch seconds."""

    t: float
    kind: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"t": self.t, "kind": self.kind, "text": self.text}
        if self.data:
            out["data"] = self.data
        return out


Subscriber = Callable[[dict[str, Any]], None]


class AuditSession:
    """One audit: what it is looking at, what it holds, and what happened."""

    def __init__(
        self,
        session_id: str,
        target: AuditTarget,
        options: AuditOptions,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.id = session_id
        self.target = target
        self.options = options
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.status = ACTIVE
        # The steps the person has been through, in order, for the report.
        self.steps: list[str] = []
        self.paused: list[PausedDevice] = []
        self.timeline: list[TimelineEntry] = []
        # Set by the network check and the report builder.
        self.footprint: Any = None
        self.report_name: str | None = None
        self._clock = clock
        self._last_activity = clock()
        self._subscribers: dict[int, Subscriber] = {}
        self._next_subscriber = 0
        self._tasks: set[asyncio.Task] = set()
        self._teardown_hooks: list[Callable[[], Awaitable[None]]] = []

    # -- activity -----------------------------------------------------------

    def touch(self) -> None:
        """A request arrived for this session; the idle clock restarts."""
        self._last_activity = self._clock()

    def idle_seconds(self) -> float:
        return self._clock() - self._last_activity

    @property
    def active(self) -> bool:
        return self.status == ACTIVE

    def enter_step(self, step: str) -> None:
        """Record that the person reached ``step`` (the first time only)."""
        if step not in self.steps:
            self.steps.append(step)

    # -- what the browser sees ----------------------------------------------

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Deliver this session's messages to ``callback`` until unsubscribed.

        The callback must not block: it is called inline, and the WebSocket
        hub's per-client queue is what absorbs a slow client.
        """
        key = self._next_subscriber
        self._next_subscriber += 1
        self._subscribers[key] = callback
        self.touch()

        def unsubscribe() -> None:
            self._subscribers.pop(key, None)

        return unsubscribe

    def publish(self, message: dict[str, Any]) -> None:
        """Send ``message`` to every subscriber, tagged with this session."""
        message = {**message, "session_id": self.id}
        for callback in list(self._subscribers.values()):
            try:
                callback(message)
            except Exception:  # a subscriber's failure is its own
                log.debug("Audit subscriber failed", exc_info=True)

    def add_timeline(self, kind: str, text: str, **data: Any) -> TimelineEntry:
        """Append one entry to the timeline and send it to subscribers."""
        entry = TimelineEntry(t=time.time(), kind=kind, text=text, data=data)
        self.timeline.append(entry)
        self.publish({"type": "audit.timeline", "entry": entry.to_dict()})
        return entry

    def publish_state(self) -> None:
        self.publish({"type": "audit.state", "state": self.to_dict()})

    # -- things to stop -----------------------------------------------------

    def track_task(self, task: asyncio.Task) -> asyncio.Task:
        """Cancel ``task`` at teardown if it is still running."""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def on_teardown(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Await ``hook`` at teardown, after tracked tasks are cancelled."""
        self._teardown_hooks.append(hook)

    async def stop_everything(self, *, spare: asyncio.Task | None = None) -> None:
        """Cancel tracked tasks (all but ``spare``), then run teardown hooks."""
        tasks = [t for t in self._tasks if not t.done() and t is not spare]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for hook in reversed(self._teardown_hooks):
            try:
                await hook()
            except Exception:
                log.warning("Audit teardown step failed", exc_info=True)
        self._teardown_hooks.clear()

    # -- the record ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The session's state, as the wizard needs it to (re)draw."""
        return {
            "session_id": self.id,
            "status": self.status,
            "target": self.target.to_dict(),
            "options": self.options.to_dict(),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "steps": list(self.steps),
            "paused": [p.to_dict() for p in self.paused],
            "report_name": self.report_name,
        }


class AuditManager:
    """Owns the one audit session a server may have."""

    def __init__(
        self,
        devices: PauseControl | None,
        discovery: ScanState | None = None,
        *,
        idle_timeout: float = IDLE_TIMEOUT,
        idle_check_seconds: float = _IDLE_CHECK_SECONDS,
        keepalive_seconds: float = KEEPALIVE_SECONDS,
        pause_ttl: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``pause_ttl`` is the backstop each held pause gets (None: the
        device manager's own); the keepalive re-arms it well before it runs
        out."""
        self._devices = devices
        self._pause_ttl = pause_ttl
        self._discovery = discovery
        self._idle_timeout = idle_timeout
        self._idle_check = idle_check_seconds
        self._keepalive = keepalive_seconds
        self._clock = clock
        self._current: AuditSession | None = None
        self._lock = asyncio.Lock()
        # Called with a session as it ends, before its pauses are released.
        self._end_hooks: list[Callable[[AuditSession], Awaitable[None]]] = []
        if discovery is not None:
            discovery.scan_blocker = self.scan_blocked_reason

    # -- queries ------------------------------------------------------------

    def current(self) -> AuditSession | None:
        """The active session, if there is one."""
        if self._current is not None and self._current.active:
            return self._current
        return None

    def get(self, session_id: str) -> AuditSession:
        """The active session with this id, or AuditNotFound."""
        session = self.current()
        if session is None or session.id != session_id:
            raise AuditNotFound("That audit has ended. Start a new one.")
        return session

    def scan_blocked_reason(self) -> str | None:
        """Why a Discovery scan cannot start now, or None when it can."""
        return SCAN_BLOCKED if self.current() is not None else None

    def add_end_hook(self, hook: Callable[[AuditSession], Awaitable[None]]) -> None:
        """Await ``hook(session)`` whenever a session ends, however it ends."""
        self._end_hooks.append(hook)

    # -- lifecycle ----------------------------------------------------------

    async def start(
        self,
        target: AuditTarget,
        options: AuditOptions | None = None,
        pause: list[tuple[str, str]] | None = None,
    ) -> AuditSession:
        """Open a session on ``target``, pausing the ``(device_id, name)`` pairs.

        Refused while another session is active or a Discovery scan runs. A
        pause that fails undoes the ones before it, so a refused start holds
        nothing.
        """
        async with self._lock:
            if self.current() is not None:
                raise AuditBusy(BUSY_AUDIT)
            if self._discovery is not None and self._discovery.is_scanning():
                raise AuditBusy(BUSY_SCAN)
            session = AuditSession(
                secrets.token_hex(6), target, options or AuditOptions(),
                clock=self._clock,
            )
            try:
                await self._pause_all(session, pause or [])
            except Exception:
                await self._resume_owned(session)
                raise
            self._current = session
            session.enter_step("target")
            session.add_timeline(
                "session.started", f"Audit started on {target.address}.",
                address=target.address, ip=target.ip,
            )
            session.track_task(asyncio.create_task(self._watch(session)))
            log.info("Device audit %s started on %s", session.id, target.address)
            return session

    async def finish(self, session_id: str, status: str = FINISHED) -> AuditSession:
        """End the session: stop everything it started, resume what it paused."""
        session = self.get(session_id)
        await self._teardown(session, status)
        return session

    async def shutdown(self) -> None:
        """End the active session, if any, because the server is stopping."""
        session = self.current()
        if session is not None:
            await self._teardown(session, SHUTDOWN)

    async def _teardown(self, session: AuditSession, status: str) -> None:
        if not session.active:
            return
        session.status = status
        session.ended_at = time.time()
        # The session's own watch task is among the tracked tasks; teardown
        # can run from inside it (the idle timeout), so it is not cancelled
        # from under itself.
        await session.stop_everything(spare=asyncio.current_task())
        for hook in self._end_hooks:
            try:
                await hook(session)
            except Exception:
                log.warning("Audit end hook failed", exc_info=True)
        await self._resume_owned(session)
        session.add_timeline("session.ended", self._end_text(status), status=status)
        session.publish_state()
        log.info("Device audit %s ended (%s)", session.id, status)

    def _end_text(self, status: str) -> str:
        if status == EXPIRED:
            minutes = max(1, round(self._idle_timeout / 60))
            return f"Audit ended after {minutes} minutes without activity."
        return _END_TEXT.get(status, "Audit ended.")

    # -- pauses -------------------------------------------------------------

    async def _pause_all(
        self, session: AuditSession, pause: list[tuple[str, str]],
    ) -> None:
        if not pause:
            return
        if self._devices is None:
            raise AuditError("Project devices cannot be paused right now.")
        for device_id, name in pause:
            if any(p.device_id == device_id for p in session.paused):
                continue
            owned = not self._devices.is_paused(device_id)
            await self._devices.pause_device(device_id, ttl=self._pause_ttl)
            session.paused.append(PausedDevice(device_id, name, owned))
            session.add_timeline(
                "device.paused", f"Paused {name} for the audit.", device_id=device_id,
            )

    async def _rearm_pauses(self, session: AuditSession) -> None:
        """Reset each held pause's backstop. A removed device is let go."""
        if self._devices is None:
            return
        for held in list(session.paused):
            try:
                await self._devices.pause_device(held.device_id, ttl=self._pause_ttl)
            except Exception as exc:
                log.info(
                    "Audit %s let go of %s: %s", session.id, held.device_id, exc,
                )
                session.paused.remove(held)
                session.add_timeline(
                    "device.released",
                    f"{held.name} is no longer in the project, so the audit "
                    "stopped holding it.",
                    device_id=held.device_id,
                )

    async def _resume_owned(self, session: AuditSession) -> None:
        if self._devices is None:
            return
        for held in session.paused:
            if not held.owned:
                continue
            try:
                await self._devices.resume_device(held.device_id)
            except Exception as exc:
                log.info("Audit %s could not resume %s: %s", session.id, held.device_id, exc)
                continue
            session.add_timeline(
                "device.resumed", f"Reconnected {held.name}.", device_id=held.device_id,
            )

    # -- the watch ----------------------------------------------------------

    async def _watch(self, session: AuditSession) -> None:
        """Keep the pauses alive and end the session when it goes idle."""
        tick = max(0.01, min(self._idle_check, self._keepalive))
        loop = asyncio.get_running_loop()
        next_rearm = loop.time() + self._keepalive
        while session.active:
            await asyncio.sleep(tick)
            if not session.active:
                return
            if session.idle_seconds() >= self._idle_timeout:
                log.info("Device audit %s idle; ending it", session.id)
                await self._teardown(session, EXPIRED)
                return
            if loop.time() >= next_rearm:
                next_rearm = loop.time() + self._keepalive
                await self._rearm_pauses(session)


_END_TEXT = {
    FINISHED: "Audit finished.",
    CANCELLED: "Audit cancelled.",
    SHUTDOWN: "Audit ended because OpenAVC stopped.",
}
