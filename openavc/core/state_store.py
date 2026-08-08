"""
OpenAVC StateStore — centralized reactive key-value store.

The single source of truth for all system state. Every device property,
user variable, and UI element value lives here.

Keys follow a namespace convention:
    device.<device_id>.<property>    — e.g., "device.projector1.power"
    var.<variable_id>                — e.g., "var.room_active"
    ui.<element_id>.<property>       — e.g., "ui.vol_slider.value"
    system.<property>                — e.g., "system.version"
    plugin.<plugin_id>.<property>    — e.g., "plugin.streamdeck.connected"
    isc.<instance_id>.<key>          — remote instance state

All values are Python primitives: str, int, float, bool, None.
No nested objects — flat key-value only.

Subscription dispatch uses a prefix-index so wildcard listeners (cloud
relay, ISC, alert monitor) do not pay an fnmatch cost per key. Patterns
are bucketed at subscribe time:

    "*"                  -> universal bucket
    "device.proj1.*"     -> prefix bucket keyed by "device.proj1."
    "var.room_active"    -> exact bucket
    "device.*.power"     -> glob fallback (fnmatch)

High-volume listeners that only need the aggregate delta can register
via ``subscribe_bulk(pattern, callback)``; the callback receives a list
of (key, old_value, new_value, source) tuples once per set/set_batch/
delete transaction, instead of one call per key.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from fnmatch import fnmatch
from time import time
from typing import TYPE_CHECKING, Any, Callable

from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.event_bus import EventBus

log = get_logger(__name__)


@dataclass
class HistoryEntry:
    """Record of a single state change."""

    key: str
    old_value: Any
    new_value: Any
    source: str
    timestamp: float = field(default_factory=time)


@dataclass
class _Sub:
    """Internal subscription record."""

    sub_id: str
    pattern: str
    callback: Callable
    bulk: bool


_GLOB_CHARS = "*?["


def is_flat_primitive(value: Any) -> bool:
    """True if ``value`` satisfies the store's flat-primitive invariant.

    THE definition of the invariant — every entry door imports this one rather
    than re-typing the isinstance tuple, so a change here reaches all of them.

    The store holds only ``str``/``int``/``float``/``bool``/``None``. A nested
    ``dict``/``list`` (or any other object) breaks every downstream consumer:
    change detection (``old == value and type matches`` passes when the same
    mutable object is mutated in place, silently dropping the notification),
    condition evaluation, persistence, and ISC re-propagation all assume
    scalars. ``bool`` is intentionally accepted (it's an ``int`` subclass).
    """
    return value is None or isinstance(value, (str, int, float, bool))


def coerce_flat_primitive(value: Any) -> tuple[Any, bool]:
    """Coerce a value to the flat-primitive state invariant.

    A few write paths take author- or runtime-supplied values (variable
    ``source_map`` results, static ``state.set`` binding values) that the
    project schema types as ``Any``, so a list/dict could otherwise reach the
    store and break downstream consumers that assume primitives. Primitives
    pass through unchanged; anything else is flattened to a JSON string so it
    stays representable.

    Lives beside ``is_flat_primitive`` because it enforces the same invariant —
    a change to what counts as flat has to reach both.

    Returns ``(coerced_value, was_coerced)`` so callers can log with context.
    """
    if is_flat_primitive(value):
        return value, False
    try:
        return json.dumps(value, ensure_ascii=False), True
    except (TypeError, ValueError):
        return str(value), True


# The namespaces a state key may live under. A key outside them still *works*
# mechanically, but nothing routes it: the panel binds by namespace, the cloud
# relay and ISC filter by it, and persistence only knows about var.*. So an
# off-namespace key is always a typo, and the doors below reject it.
VALID_KEY_PREFIXES = ("device.", "var.", "ui.", "system.", "isc.", "plugin.")

# The subset an *unauthenticated* caller may write. Panel state.set exists for
# plugin iframes and panel-driven user variables. Writes to device/system/isc/ui
# keys (which would let a panel defeat trigger cooldowns, fool skip_if_offline
# guards, or pollute ISC mesh state) are rejected.
PANEL_WRITABLE_PREFIXES = ("var.", "plugin.")


def check_state_write(key: str, value: Any, *, panel: bool = False) -> str | None:
    """One policy for every remote door into the state store.

    Returns a user-facing reason string, or None when the write is allowed.
    REST turns it into a 422, the WebSocket into an error frame, the cloud AI
    tools into an ``{"error": ...}`` result — but they all ask the same
    question here, so the same write can't succeed through one door and fail
    through another (it used to: REST had no prefix gate at all).

    ``panel=True`` marks the one unauthenticated door (panel WebSocket
    clients), which is held to ``PANEL_WRITABLE_PREFIXES``.

    This is a pre-flight check, not the enforcement point. ``set()`` still
    drops a non-primitive on its own — that backstop covers in-process writers
    (drivers, scripts, macros) that have no door to report through. What the
    doors need on top of it is a *reason* to hand back: without one they'd
    answer "success" for a write the store silently discarded.
    """
    if not key:
        return "State key cannot be empty"

    allowed = PANEL_WRITABLE_PREFIXES if panel else VALID_KEY_PREFIXES
    if not key.startswith(allowed):
        names = ", ".join(p + "*" for p in allowed)
        if panel:
            return f"Panel clients can only set keys under: {names}"
        return f"State key '{key}' must start with one of: {names}"

    if not is_flat_primitive(value):
        return (
            f"State values must be a flat primitive (string, number, boolean, "
            f"or null) — got {type(value).__name__}. Store structured data "
            f"under multiple keys, or as a JSON string."
        )
    return None


class StateStore:
    """Centralized reactive key-value state store with change notification."""

    def __init__(self):
        self._store: dict[str, Any] = {}
        # Subscription indices — populated at subscribe() time.
        self._universal_subs: list[_Sub] = []                 # pattern == "*"
        self._exact_subs: dict[str, list[_Sub]] = {}          # no glob chars
        self._prefix_subs: dict[str, list[_Sub]] = {}         # "<prefix>." -> subs (pattern ends in ".*")
        self._glob_subs: list[_Sub] = []                      # other glob patterns
        self._all_subs: dict[str, _Sub] = {}                  # sub_id -> sub (for unsubscribe)
        self._history: deque[HistoryEntry] = deque(maxlen=1000)
        self._event_bus: EventBus | None = None
        self._pending_event_tasks: set[asyncio.Task] = set()

    def set_event_bus(self, event_bus: EventBus) -> None:
        """Wire up the EventBus after construction (avoids circular dependency)."""
        self._event_bus = event_bus

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        return self._store.get(key, default)

    def has(self, key: str) -> bool:
        """Return True if the key is present — even when its value is None.

        Lets a caller distinguish "key absent" from "key set to None/False" (a
        ``get()`` returning None can't), so a $-reference resolver only warns on
        a genuinely missing key, not on a legitimately falsy one.
        """
        return key in self._store

    def get_namespace(self, prefix: str) -> dict[str, Any]:
        """
        Get all key-value pairs under a namespace prefix.

        Example: get_namespace("device.projector1") returns
                 {"power": "on", "input": "hdmi1", "connected": True}
        """
        prefix_dot = prefix if prefix.endswith(".") else prefix + "."
        result = {}
        for key, value in self._store.items():
            if key.startswith(prefix_dot):
                short_key = key[len(prefix_dot):]
                result[short_key] = value
        return result

    def get_matching(self, pattern: str) -> dict[str, Any]:
        """Get all key-value pairs where the key matches a glob pattern."""
        return {k: v for k, v in self._store.items() if fnmatch(k, pattern)}

    def snapshot(self) -> dict[str, Any]:
        """Return a complete copy of the state store."""
        return dict(self._store)

    def get_history(self, count: int = 50) -> list[dict]:
        """Return the ``count`` most recent state changes.

        A count of 0 (or negative) returns an empty list. The naive
        ``[-count:]`` slice turns 0 into "everything" (``[-0:]`` is the whole
        list) and a negative count into a wrong window, so a caller asking
        for none would otherwise get the entire 1000-entry buffer.
        """
        if count <= 0:
            return []
        entries = list(self._history)[-count:]
        return [
            {
                "key": e.key,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "source": e.source,
                "timestamp": e.timestamp,
            }
            for e in entries
        ]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def set(self, key: str, value: Any, source: str = "system") -> None:
        """
        Set a state value. If the value actually changed:
        1. Update internal store
        2. Record in history
        3. Notify matching listeners
        4. Emit events on the EventBus

        Values must be flat primitives (str, int, float, bool, None). A
        non-primitive is rejected here — the central enforcement point for
        the invariant — so automation write paths (macro/script state.set,
        driver polls) can't smuggle a nested object past the caller-side
        guards and corrupt change detection. It's dropped, not coerced: we
        never persist an arbitrary blob, even stringified.
        """
        if not is_flat_primitive(value):
            log.warning(
                "Rejected non-primitive state write to '%s' (%s) from source=%s — "
                "state values must be flat primitives (str, int, float, bool, None)",
                key, type(value).__name__, source,
            )
            return

        if not key.startswith(VALID_KEY_PREFIXES):
            log.debug("State key '%s' has unknown namespace prefix (source=%s)", key, source)

        old_value = self._store.get(key)
        if old_value == value and type(old_value) is type(value):
            return  # No change, skip notifications

        self._store[key] = value
        self._history.append(HistoryEntry(key, old_value, value, source))

        if log.isEnabledFor(10):  # DEBUG = 10
            log.debug(f"State: {key} = {value!r} (was {old_value!r}, source={source})")

        change = (key, old_value, value, source)
        self._notify([change])
        self._emit_events([change])

    def set_batch(self, updates: dict[str, Any], source: str = "system") -> None:
        """
        Atomically set multiple values — all state is updated before any
        notifications fire. Listeners and triggers see the complete batch,
        not partial intermediate states.
        """
        # Phase 1: apply all changes, collect what actually changed.
        changes: list[tuple[str, Any, Any, str]] = []
        store = self._store
        history = self._history
        for key, value in updates.items():
            if not is_flat_primitive(value):
                log.warning(
                    "Rejected non-primitive state write to '%s' (%s) from source=%s — "
                    "state values must be flat primitives (str, int, float, bool, None)",
                    key, type(value).__name__, source,
                )
                continue
            old_value = store.get(key)
            if old_value == value and type(old_value) is type(value):
                continue
            store[key] = value
            history.append(HistoryEntry(key, old_value, value, source))
            changes.append((key, old_value, value, source))

        if not changes:
            return

        if log.isEnabledFor(10):
            for key, old_value, new_value, _ in changes:
                log.debug(f"State: {key} = {new_value!r} (was {old_value!r}, source={source})")

        # Phase 2: notify listeners (state is fully updated at this point).
        self._notify(changes)

        # Phase 3: emit events.
        self._emit_events(changes)

    def delete(self, key: str, source: str = "system") -> None:
        """Remove a key from the store entirely (not just set to None).

        Unlike set(key, None), this removes the key from the store so
        get(key) returns the default.  Fires the same listener and EventBus
        notifications as set() so that downstream consumers (state relay,
        triggers, WebSocket broadcast) learn about the removal.
        """
        if key not in self._store:
            return  # Key doesn't exist — nothing to notify

        old_value = self._store.pop(key)
        self._history.append(HistoryEntry(key, old_value, None, source))

        if log.isEnabledFor(10):
            log.debug(f"State: {key} deleted (was {old_value!r}, source={source})")

        # Notify listeners. The key is already removed from _store at this
        # point, which lets consumers distinguish delete from set-to-None
        # by probing whether the key still exists in the store.
        change = (key, old_value, None, source)
        self._notify([change])
        self._emit_events([change])

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, pattern: str, callback: Callable) -> str:
        """
        Subscribe to state changes matching a glob pattern.

        Examples:
            "device.projector1.*"  — all projector1 changes
            "device.*.power"       — power state of any device
            "var.*"                — all user variables
            "*"                    — everything

        Args:
            pattern: Glob pattern to match state keys against.
            callback: Called with (key, old_value, new_value, source).
                      Can be sync or async.

        Returns:
            Subscription ID (use to unsubscribe).
        """
        sub_id = str(uuid.uuid4())
        self._add_sub(_Sub(sub_id=sub_id, pattern=pattern, callback=callback, bulk=False))
        log.debug(f"State subscription {sub_id[:8]}... on pattern '{pattern}'")
        return sub_id

    def subscribe_bulk(self, pattern: str, callback: Callable) -> str:
        """
        Subscribe for delta-batched notifications.

        The callback is invoked at most once per ``set`` / ``set_batch`` /
        ``delete`` transaction with a ``list[tuple[key, old, new, source]]``
        of every change in that transaction that matched the pattern.

        Prefer this over ``subscribe`` for high-volume wildcard listeners
        (cloud relay, ISC bridge, alert monitor) that only need the
        aggregate delta rather than N individual callbacks. For a 40k-key
        ``set_batch`` with a ``"*"`` bulk subscriber, the callback fires
        once with a 40k-item list instead of 40k times.

        The pattern grammar is identical to ``subscribe``.
        """
        sub_id = str(uuid.uuid4())
        self._add_sub(_Sub(sub_id=sub_id, pattern=pattern, callback=callback, bulk=True))
        log.debug(f"State bulk subscription {sub_id[:8]}... on pattern '{pattern}'")
        return sub_id

    def subscribe_children(
        self, parent_id: str, child_type: str, callback: Callable
    ) -> str:
        """Subscribe to every state change on any child of ``parent_id`` of
        type ``child_type``.

        Equivalent to ``subscribe(f"device.{parent_id}.{child_type}.*", cb)``
        but encodes the platform's child-entity key shape so callers don't
        assemble it themselves. Callback signature is the standard
        per-key form: ``(key, old_value, new_value, source)``.
        """
        return self.subscribe(f"device.{parent_id}.{child_type}.*", callback)

    def unsubscribe(self, sub_id: str) -> None:
        """Remove a subscription by ID."""
        sub = self._all_subs.pop(sub_id, None)
        if sub is None:
            return
        self._remove_sub_from_index(sub)

    @property
    def _listeners(self) -> dict[str, list[tuple[str, Callable]]]:
        """Read-only view of subscriptions grouped by pattern.

        Reconstructed from the internal indices for backward-compatible
        inspection (used by tests that assert a pattern is no longer
        subscribed after cleanup). Don't mutate this; use subscribe /
        unsubscribe.
        """
        out: dict[str, list[tuple[str, Callable]]] = {}
        for sub in self._all_subs.values():
            out.setdefault(sub.pattern, []).append((sub.sub_id, sub.callback))
        return out

    # ------------------------------------------------------------------
    # Async housekeeping
    # ------------------------------------------------------------------

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        """Log exceptions from completed async callback tasks."""
        if not task.cancelled() and task.exception() is not None:
            log.error(f"State callback error: {task.exception()}")

    async def flush_pending_events(self) -> None:
        """Wait for all pending event emission tasks to complete."""
        if self._pending_event_tasks:
            await asyncio.gather(*self._pending_event_tasks, return_exceptions=True)
            self._pending_event_tasks.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_pattern(pattern: str) -> tuple[str, str]:
        """Bucket a subscription pattern.

        Returns ``(bucket, key)`` where ``bucket`` is one of:

        - ``"universal"`` — pattern is literally ``"*"``; key is ``""``.
        - ``"exact"``     — pattern has no glob chars; key is the pattern.
        - ``"prefix"``    — pattern is ``"<literal>.*"`` with no other glob
                            chars; key is ``"<literal>."`` (trailing dot
                            retained so candidate-prefix lookup matches it
                            exactly).
        - ``"glob"``      — anything else; falls back to fnmatch.
        """
        if pattern == "*":
            return ("universal", "")
        has_glob = any(c in pattern for c in _GLOB_CHARS)
        if not has_glob:
            return ("exact", pattern)
        if pattern.endswith(".*") and not any(c in pattern[:-2] for c in _GLOB_CHARS):
            return ("prefix", pattern[:-1])  # keep the trailing dot
        return ("glob", pattern)

    def _add_sub(self, sub: _Sub) -> None:
        bucket, key = self._classify_pattern(sub.pattern)
        if bucket == "universal":
            self._universal_subs.append(sub)
        elif bucket == "exact":
            self._exact_subs.setdefault(key, []).append(sub)
        elif bucket == "prefix":
            self._prefix_subs.setdefault(key, []).append(sub)
        else:
            self._glob_subs.append(sub)
        self._all_subs[sub.sub_id] = sub

    def _remove_sub_from_index(self, sub: _Sub) -> None:
        bucket, key = self._classify_pattern(sub.pattern)
        if bucket == "universal":
            self._universal_subs = [s for s in self._universal_subs if s.sub_id != sub.sub_id]
        elif bucket == "exact":
            lst = self._exact_subs.get(key)
            if lst is not None:
                remaining = [s for s in lst if s.sub_id != sub.sub_id]
                if remaining:
                    self._exact_subs[key] = remaining
                else:
                    del self._exact_subs[key]
        elif bucket == "prefix":
            lst = self._prefix_subs.get(key)
            if lst is not None:
                remaining = [s for s in lst if s.sub_id != sub.sub_id]
                if remaining:
                    self._prefix_subs[key] = remaining
                else:
                    del self._prefix_subs[key]
        else:
            self._glob_subs = [s for s in self._glob_subs if s.sub_id != sub.sub_id]

    def _matching_non_universal_subs(self, key: str) -> Iterator[_Sub]:
        """Yield subs whose pattern matches ``key``, excluding universal subs.

        Universal subs ('*') are dispatched once per transaction outside the
        per-key loop, so they're omitted here.
        """
        # Exact subs are an O(1) dict lookup.
        exact = self._exact_subs.get(key)
        if exact:
            yield from exact

        # Prefix subs: walk every "."-bounded prefix of the key. A pattern
        # "p.*" was registered under "p." so we look up each "p." substring
        # of the key. Cost is O(D) where D is the number of dots in the
        # key (≤ ~6 for OpenAVC keys), not O(P) where P is the number of
        # prefix subscriptions.
        if self._prefix_subs:
            prefix_subs = self._prefix_subs
            for i, ch in enumerate(key):
                if ch == ".":
                    bucket = prefix_subs.get(key[: i + 1])
                    if bucket:
                        yield from bucket

        # Glob fallback: arbitrary-position wildcards still pay fnmatch.
        # Expected to be rare in practice.
        if self._glob_subs:
            for sub in self._glob_subs:
                if fnmatch(key, sub.pattern):
                    yield sub

    def _notify(self, changes: list[tuple[str, Any, Any, str]]) -> None:
        """Deliver a transaction's worth of changes to subscribers.

        Per-key subscribers fire once per matching change. Bulk subscribers
        fire once with a list of every change in this transaction that
        matched their pattern. Universal subs ('*') are handled outside the
        per-change loop so wildcard listeners don't pay an O(N) index walk
        per change.
        """
        if not changes:
            return

        # 1) Universal subs match every change unconditionally. Fire them in
        #    their natural mode and skip them in the per-change loop below.
        for sub in self._universal_subs:
            if sub.bulk:
                self._invoke_bulk(sub, changes)
            else:
                for key, old, new, source in changes:
                    self._invoke_per_key(sub, key, old, new, source)

        # 2) Patterned subs (exact / prefix / glob). Per-key subs fire
        #    immediately; bulk subs accumulate into per-sub buckets, then
        #    fire once with the full list at the end of the transaction.
        bulk_buckets: dict[str, list[tuple[str, Any, Any, str]]] | None = None
        bulk_subs_by_id: dict[str, _Sub] | None = None

        for change in changes:
            key, old, new, source = change
            for sub in self._matching_non_universal_subs(key):
                if sub.bulk:
                    if bulk_buckets is None:
                        bulk_buckets = {}
                        bulk_subs_by_id = {}
                    existing = bulk_buckets.get(sub.sub_id)
                    if existing is None:
                        bulk_buckets[sub.sub_id] = [change]
                        bulk_subs_by_id[sub.sub_id] = sub  # type: ignore[index]
                    else:
                        existing.append(change)
                else:
                    self._invoke_per_key(sub, key, old, new, source)

        if bulk_buckets:
            assert bulk_subs_by_id is not None
            for sub_id, bucket_changes in bulk_buckets.items():
                self._invoke_bulk(bulk_subs_by_id[sub_id], bucket_changes)

    def _invoke_per_key(
        self, sub: _Sub, key: str, old: Any, new: Any, source: str
    ) -> None:
        try:
            result = sub.callback(key, old, new, source)
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(result)
                    task.add_done_callback(self._log_task_exception)
                except RuntimeError:
                    pass  # No event loop — skip
        except Exception:  # Catch-all: isolates listener callback errors
            log.exception(f"Error in state listener for pattern '{sub.pattern}'")

    def _invoke_bulk(
        self, sub: _Sub, changes: list[tuple[str, Any, Any, str]]
    ) -> None:
        try:
            result = sub.callback(changes)
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(result)
                    task.add_done_callback(self._log_task_exception)
                except RuntimeError:
                    pass
        except Exception:
            log.exception(f"Error in state bulk listener for pattern '{sub.pattern}'")

    def _emit_events(self, changes: list[tuple[str, Any, Any, str]]) -> None:
        """Emit ``state.changed`` events for a whole transaction.

        Schedules emission as a single asyncio task; if there's no running
        loop (sync tests), the events are silently skipped.

        The whole transaction is dispatched by ONE task that awaits the
        per-key emits in sequence, rather than fanning out two tasks per key.
        A bulk ``set_batch`` (device/child poll, project reload) can carry
        tens of thousands of changes; the old per-key fan-out scheduled 2N
        tasks at once and pinned a strong reference to every one of them in
        ``_pending_event_tasks`` until they completed — a memory/scheduler
        spike on a common path. Collapsing to one task bounds that to a
        single live reference per transaction. Each ``emit`` still dispatches
        its own matching handlers concurrently, and ordering the emits by
        change order is deterministic (a minor improvement over the old
        racy fan-out).
        """
        if self._event_bus is None or not changes:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop — skip async events (sync tests)

        task = loop.create_task(self._dispatch_change_events(changes))
        self._pending_event_tasks.add(task)
        task.add_done_callback(self._pending_event_tasks.discard)

    async def _dispatch_change_events(
        self, changes: list[tuple[str, Any, Any, str]]
    ) -> None:
        """Emit the generic and per-key ``state.changed`` events for a batch."""
        bus = self._event_bus
        if bus is None:
            return
        for key, old_value, new_value, source in changes:
            payload = {
                "key": key,
                "old_value": old_value,
                "new_value": new_value,
                "source": source,
            }
            await bus.emit("state.changed", payload)
            await bus.emit(f"state.changed.{key}", payload)
