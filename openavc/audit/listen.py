"""Connect and listen: what adding the device to a space does, watched closely.

The pass brings the chosen driver up through the sandbox (``audit/sandbox.py``)
exactly as a project device is brought up: connect, sign in, the start-up
steps, then status polling at the driver's own cadence. It sends none of the
driver's commands. What it records:

- **A timeline**: starting, the first byte sent, the first reply, connected
  (each timed from the start), every declared status value's first report,
  each offline reason the manager classifies, every drop and reconnect, and
  the first contract fault of each kind.
- **A listening window** of at least ``MIN_SECONDS`` and ``MIN_CYCLES`` poll
  cycles from the moment the device connected; the person can extend it, up
  to ``MAX_SECONDS`` from the start. A device that does not connect is watched
  for the minimum window, because the manager keeps retrying as it would for
  a device in a space.
- **The status table**: every state variable the driver declares with its
  value or "not reported", the rules that would have set it, any type
  problem; the children the driver registered and their values; each device
  setting's read-back value.
- **The front-panel check**: the person changes something on the device and
  says whether OpenAVC showed it; the state changes seen are kept beside the
  answer.

Everything the driver moved is in the run's observer for the report
(:meth:`ListenPass.report_record`). The wizard gets it live: ``audit.traffic``
batches and ``audit.listen`` updates, each throttled, to the subscribing
client only.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from openavc.audit.observe import (
    CONTRACT_TEXT,
    REPLY_WINDOW_SECONDS,
    ContractEvent,
    replies_to_nobody,
)
from openavc.audit.sandbox import DriverSandbox, audit_device_id
from openavc.audit.session import AuditError
from openavc.core.connection_fault import is_permanent_fault
from openavc.core.device_traffic import RX, TX, TrafficEntry, serialize_entry
from openavc.core.state_store import is_flat_primitive
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.audit.passes import DriverRun
    from openavc.audit.session import AuditSession

log = get_logger(__name__)

MIN_SECONDS = 45.0
MIN_CYCLES = 3
MAX_SECONDS = 300.0
EXTEND_SECONDS = 60.0
# How often the wizard hears about progress while listening.
FLUSH_SECONDS = 0.5
# Traffic entries sent to the wizard per update (the report keeps them all).
LIVE_BATCH = 200
# State changes kept for the front-panel check and the report.
CHANGES_KEPT = 2000

CONNECTING = "connecting"
LISTENING = "listening"
NOT_CONNECTED = "not_connected"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"

NO_CONNECTION_SET = "Enter the connection settings first."

# Keys the platform writes for every device; not the driver's status values.
_PLATFORM_KEYS = frozenset({
    "connected", "name", "enabled", "offline_reason", "offline_detail", "paused",
    "restarting", "web_ui_url", "orphaned", "orphan_reason",
})

def _shown(value: Any) -> Any:
    """A state value as a record keeps it: as is when the store could hold
    it, else its repr."""
    return value if is_flat_primitive(value) else repr(value)


def next_step(code: str) -> str:
    """What to try after an offline reason, in the audit's terms."""
    if is_permanent_fault(code):
        return "Change the setting on the Connection step, then connect again."
    return (
        "OpenAVC keeps trying to connect, as it would for a device in a space. "
        "The report records every attempt."
    )


def response_sources(driver: Any) -> dict[str, list[str]]:
    """For a YAML driver, the response rules that set each status value."""
    out: dict[str, list[str]] = {}

    def add(state: Any, text: str) -> None:
        if isinstance(state, str) and state:
            out.setdefault(state, [])
            if text not in out[state]:
                out[state].append(text)

    for rule in getattr(driver, "_compiled_responses", None) or []:
        pattern, mappings = rule[0], rule[1]
        for mapping in mappings or []:
            add(mapping.get("state"), f"reply matching /{pattern.pattern}/")
    for rule in getattr(driver, "_osc_responses", None) or []:
        address, mappings = rule[0], rule[1]
        for mapping in mappings or []:
            add(mapping.get("state"), f"OSC message {address}")
    for rule in getattr(driver, "_json_responses", None) or []:
        for mapping in rule[0] or []:
            add(mapping.get("state"), f"JSON key {mapping.get('key')}")
    return out


class ListenPass:
    """One connect-and-listen for one driver run."""

    def __init__(
        self,
        session: "AuditSession",
        run: "DriverRun",
        *,
        min_seconds: float = MIN_SECONDS,
        min_cycles: int = MIN_CYCLES,
        max_seconds: float = MAX_SECONDS,
        flush_seconds: float = FLUSH_SECONDS,
    ) -> None:
        self.session = session
        self.run = run
        self.min_seconds = min_seconds
        self.min_cycles = min_cycles
        self.max_seconds = max_seconds
        self._flush = flush_seconds
        self.status = CONNECTING
        self.error = ""
        self.started_at = time.time()
        self.connected_at: float | None = None
        self.first_tx_at: float | None = None
        self.first_rx_at: float | None = None
        self.ends_at: float | None = None
        self.finished_at: float | None = None
        self.poll_interval = 0.0
        self.reconnects = 0
        self.drops = 0
        self.offline: dict[str, str] | None = None
        self.first_reported: dict[str, float] = {}
        self.changes: list[dict[str, Any]] = []
        self.front_panel: dict[str, Any] | None = None
        self.sources: dict[str, list[str]] = {}
        self._events_seen: set[str] = set()
        self._pending: list[TrafficEntry] = []
        self._dirty = True
        self._redactor = None
        # Set while the audit itself takes the driver down: its disconnect
        # is not a drop, and its state being removed is not a change.
        self._stopping = False
        # The status table as it stood when the driver stopped (its state
        # goes with it), for the report and the wizard afterwards.
        self._final_table: dict[str, Any] | None = None
        self.task: asyncio.Task | None = None
        self.sandbox = DriverSandbox(
            audit_device_id(session.id), run.choice.driver_id, dict(run.config or {}),
            name=run.choice.identity.get("name") or run.choice.driver_id,
            on_entry=self._on_entry, on_event=self._on_event,
        )
        self.sandbox.observer.add_secrets(run.secrets)

    # -- the pass -------------------------------------------------------------

    async def execute(self) -> None:
        sb = self.sandbox
        device_id = sb.device_id
        try:
            sb.prepare()
            await sb.start()
        except AuditError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:
            log.exception("The audit could not start the driver")
            self._fail(f"The driver could not start: {exc}")
            return
        self._redactor = sb.observer.redactor()
        self.sources = response_sources(sb.driver)
        self.poll_interval = float(sb.resolved.get("config", {}).get("poll_interval", 0) or 0)
        sb.events.on(f"device.connected.{device_id}", self._on_connected)
        sb.events.on(f"device.disconnected.{device_id}", self._on_disconnected)
        sb.state.subscribe(f"device.{device_id}.*", self._on_state)
        where = sb.resolved["config"].get("host") or sb.resolved["config"].get("port") or ""
        port = sb.resolved["config"].get("port") if sb.resolved["config"].get("host") else ""
        self._timeline(
            "listen.connecting",
            f"Connecting to {where}{':' + str(port) if port else ''} with "
            f"{self.run.choice.identity.get('name')} over {sb.transport}.",
        )
        self.run.started_at = self.started_at
        await sb.connect()
        if self.connected_at is None:
            self.status = NOT_CONNECTED
            self.ends_at = self.started_at + self.min_seconds
            self._note_offline()
        self._dirty = True
        try:
            await self._watch()
        finally:
            self._flush_now()

    async def _watch(self) -> None:
        """Hold the listening window, then keep the wizard's view current
        while the driver stays connected (the next step uses it)."""
        while self.sandbox.started:
            now = time.time()
            if self.status in (LISTENING, NOT_CONNECTED) and self.ends_at and now >= self.ends_at:
                self._end_window()
            self._flush_now()
            await asyncio.sleep(self._flush)

    def _end_window(self) -> None:
        self.finished_at = time.time()
        declared = self._declared()
        reported = [v for v in declared if v in self.first_reported]
        if self.status == LISTENING:
            self.status = DONE
            listened = self.finished_at - (self.connected_at or self.started_at)
            self._timeline(
                "listen.done",
                f"Listened for {round(listened)} seconds: {len(reported)} of "
                f"{len(declared)} status values reported.",
                reported=len(reported), declared=len(declared),
            )
        else:
            self.status = FAILED
            self._timeline(
                "listen.failed",
                "The driver did not connect while the audit was listening.",
            )
        hints = replies_to_nobody(self.sandbox.observer.frames())
        if hints:
            self._timeline(
                "listen.unprompted",
                f"{len(hints)} {'reply' if len(hints) == 1 else 'replies'} arrived with no "
                "request in the 2 seconds before (the device may announce changes on "
                "its own).",
                count=len(hints),
            )
        self._dirty = True

    def _window_end(self) -> float:
        base = self.connected_at or self.started_at
        span = max(self.min_seconds, self.min_cycles * self.poll_interval)
        return min(base + span, self.started_at + self.max_seconds)

    def extend(self, seconds: float = EXTEND_SECONDS) -> None:
        """Keep listening longer, never past ``max_seconds`` from the start."""
        cap = self.started_at + self.max_seconds
        now = time.time()
        if now >= cap:
            raise AuditError(
                f"The audit listens for {round(self.max_seconds / 60)} minutes at most."
            )
        self.ends_at = min(max(self.ends_at or now, now) + seconds, cap)
        if self.status == DONE:
            self.status = LISTENING
            self.finished_at = None
        elif self.status == FAILED:
            self.status = NOT_CONNECTED
            self.finished_at = None
        self._timeline(
            "listen.extended",
            f"Listening until {time.strftime('%H:%M:%S', time.localtime(self.ends_at))}.",
        )
        self._dirty = True

    def answer_front_panel(self, answer: str, note: str = "") -> None:
        """The person changed something on the device: did OpenAVC show it?"""
        since = self.connected_at or self.started_at
        declared = set(self._declared())
        seen = [c for c in self.changes if c["t"] >= since and c["key"] in declared]
        self.front_panel = {"answer": answer, "note": note, "at": time.time(), "changes": seen}
        self._timeline(
            "listen.front_panel",
            "The person changed something on the device, and OpenAVC showed it."
            if answer == "showed"
            else "The person changed something on the device, and OpenAVC did not show it.",
            answer=answer,
        )
        self._dirty = True

    async def stop(self) -> None:
        """End this attempt: its connection closes, what it recorded stays."""
        task = self.task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.sandbox.started:
            self._final_table = self.status_table()
            self._stopping = True
            await self.sandbox.stop()
        if self.status in (CONNECTING, LISTENING, NOT_CONNECTED):
            self.status = STOPPED
            self.finished_at = time.time()
        self._dirty = True
        self._flush_now()

    def _fail(self, message: str) -> None:
        self.status = FAILED
        self.error = message
        self.finished_at = time.time()
        self._timeline("listen.failed", message)
        self._flush_now()

    # -- what arrives -----------------------------------------------------------

    def _on_entry(self, entry: TrafficEntry) -> None:
        if not entry.chunk:
            if entry.direction == TX and self.first_tx_at is None:
                self.first_tx_at = entry.t
                self._timeline(
                    "listen.first_tx",
                    f"Sent the first bytes, {self._since(entry.t)} after starting.",
                )
            elif entry.direction == RX and self.first_rx_at is None:
                self.first_rx_at = entry.t
                self._timeline(
                    "listen.first_rx",
                    f"The device answered, {self._since(entry.t)} after starting.",
                )
        self._pending.append(entry)

    def _on_event(self, event: ContractEvent) -> None:
        if event.kind not in self._events_seen:
            self._events_seen.add(event.kind)
            detail = event.detail.get("text") or event.detail.get("state") or event.detail.get(
                "command"
            ) or event.detail.get("address") or ""
            self._timeline(
                f"contract.{event.kind}",
                f"{CONTRACT_TEXT.get(event.kind, event.kind)}"
                f"{': ' + str(detail)[:120] if detail else ''}.",
            )
        self._dirty = True

    async def _on_connected(self, _event: str, _payload: Any = None) -> None:
        now = time.time()
        if self.connected_at is None:
            self.connected_at = now
            self.status = LISTENING
            self.offline = None
            self.ends_at = self._window_end()
            self._timeline("listen.connected", f"Connected, {self._since(now)} after starting.")
        else:
            self.reconnects += 1
            if self.status == NOT_CONNECTED:
                self.status = LISTENING
            self._timeline("listen.reconnected", "Reconnected.")
        self._dirty = True

    async def _on_disconnected(self, _event: str, _payload: Any = None) -> None:
        if self.connected_at is not None and not self._stopping:
            self.drops += 1
            self._timeline("listen.dropped", "The connection dropped.")
        self._dirty = True

    def _on_state(self, key: str, old: Any, new: Any, _source: str = "") -> None:
        if self._stopping:
            return
        prefix = f"device.{self.sandbox.device_id}."
        prop = key[len(prefix):] if key.startswith(prefix) else key
        now = time.time()
        if len(self.changes) < CHANGES_KEPT:
            self.changes.append({
                "t": now, "key": prop,
                "old": _shown(old),
                "new": _shown(new),
            })
        if prop == "offline_reason" and new:
            self._note_offline()
        if (
            new is not None and prop not in _PLATFORM_KEYS and "." not in prop
            and prop not in self.first_reported
        ):
            self.first_reported[prop] = now
            if prop in self._declared():
                shown = ("true" if new else "false") if isinstance(new, bool) else str(new)
                self._timeline(
                    "listen.reported",
                    f"{prop} reported: {shown[:80]}, {self._since(now)} after starting.",
                    state=prop,
                )
        self._dirty = True

    def _note_offline(self) -> None:
        state = self.sandbox.device_state()
        code = str(state.get("offline_reason") or "")
        if not code:
            return
        detail = str(state.get("offline_detail") or "")
        offline = {"code": code, "detail": detail, "next_step": next_step(code)}
        if offline != self.offline:
            self.offline = offline
            self._timeline(
                "listen.offline", f"Not connected: {detail or code}", code=code,
            )

    # -- publishing ---------------------------------------------------------------

    def _timeline(self, kind: str, text: str, **data: Any) -> None:
        self.session.add_timeline(kind, text, run=self.run.index, **data)

    def _since(self, t: float) -> str:
        return f"{max(0.0, t - self.started_at):.1f} s"

    def _flush_now(self) -> None:
        if self._pending:
            batch, self._pending = self._pending, []
            redactor = self._redactor or self.sandbox.observer.redactor()
            shown = batch[-LIVE_BATCH:]
            self.session.publish({
                "type": "audit.traffic",
                "run": self.run.index,
                "entries": [serialize_entry(e, redactor) for e in shown],
                "skipped": len(batch) - len(shown),
            })
        if self._dirty:
            self._dirty = False
            self.session.publish({
                "type": "audit.listen", "run": self.run.index, "listen": self.to_dict(),
            })

    # -- reading ------------------------------------------------------------------

    def _driver_info(self) -> dict[str, Any]:
        driver = self.sandbox.driver
        info = getattr(driver, "DRIVER_INFO", None)
        if info is None:
            from openavc.drivers.registry import get_driver_class

            info = getattr(get_driver_class(self.run.choice.driver_id), "DRIVER_INFO", {})
        return info or {}

    def _declared(self) -> list[str]:
        return list((self._driver_info().get("state_variables") or {}).keys())

    def status_table(self) -> dict[str, Any]:
        if self._final_table is not None and not self.sandbox.started:
            return self._final_table
        info = self._driver_info()
        state = self.sandbox.device_state()
        mismatches: dict[str, str] = {}
        for event in self.sandbox.observer.events:
            if event.kind == "type_mismatch":
                mismatches[str(event.detail.get("state"))] = str(event.detail.get("problem", ""))
        variables = []
        for name, spec in (info.get("state_variables") or {}).items():
            spec = spec if isinstance(spec, dict) else {}
            value = state.get(name)
            variables.append({
                "name": name,
                "label": spec.get("label") or name,
                "type": spec.get("type", "string"),
                "value": _shown(value),
                "reported": name in self.first_reported,
                "first_reported_at": self.first_reported.get(name),
                "problem": mismatches.get(name, ""),
                "sources": self.sources.get(name, []),
            })
        children: dict[str, dict[str, dict[str, Any]]] = {}
        for ctype in (info.get("child_entity_types") or {}):
            prefix = f"{ctype}."
            for key, value in state.items():
                if not key.startswith(prefix):
                    continue
                rest = key[len(prefix):]
                local_id, _, prop = rest.partition(".")
                if prop:
                    children.setdefault(ctype, {}).setdefault(local_id, {})[prop] = _shown(value)
        settings = []
        for key, spec in (info.get("device_settings") or {}).items():
            spec = spec if isinstance(spec, dict) else {}
            state_key = spec.get("state_key")
            value = state.get(state_key) if state_key else None
            settings.append({
                "key": key,
                "label": spec.get("label") or key,
                "state_key": state_key or "",
                "value": _shown(value),
                "populated": bool(state_key) and value is not None,
            })
        return {"variables": variables, "children": children, "settings": settings}

    def to_dict(self) -> dict[str, Any]:
        observer = self.sandbox.observer
        declared = self._declared()
        frames = observer.frames()
        own_session = (
            not frames and any(v in self.first_reported for v in declared)
        )
        return {
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "connected_at": self.connected_at,
            "first_tx_at": self.first_tx_at,
            "first_rx_at": self.first_rx_at,
            "ends_at": self.ends_at,
            "finished_at": self.finished_at,
            "max_ends_at": self.started_at + self.max_seconds,
            "poll_interval": self.poll_interval,
            "reconnects": self.reconnects,
            "drops": self.drops,
            "offline": self.offline,
            "declared": len(declared),
            "reported": sum(1 for v in declared if v in self.first_reported),
            "traffic": {
                "entries": len(frames),
                "sent": sum(1 for e in frames if e.direction == TX),
                "received": sum(1 for e in frames if e.direction == RX),
                "bytes": sum(len(e.data) for e in frames),
                "truncated": observer.truncated_at is not None,
                "not_captured": own_session,
            },
            "contract": {
                "counts": dict(observer.event_counts),
                "recent": [e.to_dict() for e in observer.events[-10:]],
            },
            "status_table": self.status_table(),
            "front_panel": self.front_panel,
        }


    def report_record(self) -> dict[str, Any]:
        """This attempt as the report keeps it: the live view's fields, plus
        every contract event kept, every state change, the replies that came
        with no request before them, and every traffic entry (raw receive
        chunks included) with its bytes as hex and text, secrets masked."""
        observer = self.sandbox.observer
        redactor = observer.redactor()
        live = self.to_dict()
        frames = observer.frames()
        unprompted = replies_to_nobody(frames)
        return {
            **{k: v for k, v in live.items() if k not in ("contract", "traffic")},
            "contract": {
                "counts": dict(observer.event_counts),
                "events": [
                    {**e.to_dict(), "detail": redactor.value(dict(e.detail))}
                    for e in observer.events
                ],
            },
            "unprompted_replies": {
                "count": len(unprompted),
                "window_seconds": REPLY_WINDOW_SECONDS,
                "seq": [e.seq for e in unprompted],
            },
            "state_changes": [redactor.value(dict(c)) for c in self.changes],
            "traffic": {
                "count": len(frames),
                "sent": live["traffic"]["sent"],
                "received": live["traffic"]["received"],
                "bytes": live["traffic"]["bytes"],
                "truncated_at": observer.truncated_at,
                "dropped_entries": observer.dropped_entries,
                "not_captured": live["traffic"]["not_captured"],
                "entries": [serialize_entry(e, redactor) for e in observer.traffic],
            },
        }


async def start_listen(
    session: "AuditSession", run: "DriverRun", **timings: Any,
) -> ListenPass:
    """Start connect-and-listen for ``run`` in the background.

    Connecting again (after changing a setting, say) ends the previous
    attempt first; every attempt stays in the run for the report.
    """
    if run.config is None:
        raise AuditError(NO_CONNECTION_SET)
    if run.listen is not None:
        await run.listen.stop()
    listen = ListenPass(session, run, **timings)
    run.sandbox = listen.sandbox
    run.listen = listen
    run.listens.append(listen)
    run.extra["listen"] = listen.to_dict
    session.enter_step("listen")
    listen.task = session.track_task(asyncio.create_task(listen.execute()))
    return listen
