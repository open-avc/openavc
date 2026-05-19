"""
OpenAVC Cloud — State change relay to the cloud.

Subscribes to the StateStore for state changes, batches them over a
configurable window, and sends state_batch messages to the cloud via
the CloudAgent.

State updates are bucketed into three priority tiers so a controller that
owns thousands of child entities (encoders, decoders, presets, ...) does
not drown the cloud bandwidth budget:

* ``top`` — top-level device state (``device.<id>.<prop>``) plus var /
  system / ui / plugin state. Flushed at ``state_batch_interval`` (default
  2 s). A driver can opt a child property into this tier by tagging it
  ``cloud_priority: "high"`` so latency-sensitive state (video routing,
  mute) keeps the snappy cadence.
* ``child`` — child-entity properties without an explicit priority tag.
  Flushed at ``state_batch_interval_child`` (default 5 s). The bulk of
  per-child telemetry lives here.
* ``low`` — child-entity properties the driver tagged
  ``cloud_priority: "low"``. Flushed at ``state_batch_interval_low``
  (default 30 s). Verbose per-IO voltages, per-pin states, etc.

Large flushes (above ``state_batch_pace_threshold`` keys, default 2000)
sleep ``state_batch_pace_delay`` seconds (default 50 ms) between chunks so
a 40k-key snapshot does not dump 80+ WebSocket frames into a single
event-loop tick.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, TYPE_CHECKING

from server.cloud.protocol import STATE_BATCH
from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.cloud.agent import CloudAgent
    from server.core.state_store import StateStore

log = get_logger(__name__)

# Sentinel for detecting deleted keys vs keys set to None
_MISSING = object()

# Default cadence per tier (seconds). Each is overridable via the agent
# config: state_batch_interval / state_batch_interval_child /
# state_batch_interval_low.
_DEFAULT_INTERVAL_TOP = 2
_DEFAULT_INTERVAL_CHILD = 5
_DEFAULT_INTERVAL_LOW = 30

# Above this number of keys in one flush or snapshot, pace between chunks
# so we do not burst dozens of WebSocket frames in one event-loop tick.
_DEFAULT_PACE_THRESHOLD = 2000
_DEFAULT_PACE_DELAY = 0.05

_TIER_TOP = "top"
_TIER_CHILD = "child"
_TIER_LOW = "low"


class StateRelay:
    """
    Batches state changes by priority tier and forwards them to the cloud.

    Three tiers (see module docstring): top, child, low — each with its
    own flush task and cadence.
    """

    def __init__(self, agent: CloudAgent, state: StateStore):
        """
        Args:
            agent: The CloudAgent to send messages through.
            state: The StateStore to subscribe to.
        """
        self._agent = agent
        self._state = state

        # One batch per tier so the per-tier flush loops can drain
        # independently without locking each other out.
        self._batches: dict[str, list[dict[str, Any]]] = {
            _TIER_TOP: [],
            _TIER_CHILD: [],
            _TIER_LOW: [],
        }
        self._batch_lock = threading.Lock()

        # Cache of key -> tier so we do not walk DRIVER_INFO on every state
        # change. Cleared on each start() — connection lifecycle is a
        # natural invalidation point and at-most-one-cache-miss-per-key
        # amortizes the cost flatly. Stale entries are harmless (a deleted
        # device's keys just sit unused in the cache).
        self._tier_cache: dict[str, str] = {}

        self._flush_tasks: dict[str, asyncio.Task] = {}
        self._sub_id: str | None = None
        self._running = False

    async def start(self) -> None:
        """Start listening for state changes and batching them."""
        if self._running:
            return

        self._running = True

        # Discard any batch entries left over from a previous connection.
        # They are stale — the snapshot we are about to send is
        # authoritative. Without this, old value-update entries could
        # re-insert keys for devices that were deleted while the agent
        # was disconnected.
        with self._batch_lock:
            for tier in self._batches:
                self._batches[tier].clear()
        # Drop any cached tier classifications too — DRIVER_INFO may have
        # changed across the disconnect (driver hot-reload, device removed,
        # cloud_priority retagged in a driver update).
        self._tier_cache.clear()

        # Send full state snapshot so the cloud has current values for keys
        # that were set before the relay started (e.g. device.*.connected).
        # The snapshot includes a flag so the cloud clears stale state from
        # a previous session before applying the fresh values.
        await self._send_initial_snapshot()

        self._sub_id = self._state.subscribe("*", self._on_state_change)

        self._flush_tasks = {
            _TIER_TOP: asyncio.create_task(
                self._flush_bucket_loop(
                    _TIER_TOP, "state_batch_interval", _DEFAULT_INTERVAL_TOP
                )
            ),
            _TIER_CHILD: asyncio.create_task(
                self._flush_bucket_loop(
                    _TIER_CHILD,
                    "state_batch_interval_child",
                    _DEFAULT_INTERVAL_CHILD,
                )
            ),
            _TIER_LOW: asyncio.create_task(
                self._flush_bucket_loop(
                    _TIER_LOW, "state_batch_interval_low", _DEFAULT_INTERVAL_LOW
                )
            ),
        }
        log.info("State relay: started")

    async def stop(self) -> None:
        """Stop the state relay."""
        self._running = False

        if self._sub_id:
            self._state.unsubscribe(self._sub_id)
            self._sub_id = None

        tasks = list(self._flush_tasks.values())
        self._flush_tasks = {}
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        log.info("State relay: stopped")

    async def _send_initial_snapshot(self) -> None:
        """Send all current state values to the cloud as the first batch.

        Ensures the cloud has device.*.connected and other state keys that
        were set before the relay started listening for changes.

        The first chunk includes ``"snapshot": true`` so the cloud clears
        any stale state left over from a previous session before ingesting.
        This handles the case where devices were deleted while the agent
        was disconnected — the cloud will not have those keys in the
        snapshot, so the clear removes them.

        Snapshots ignore per-tier cadence: they ship as one paced burst so
        the cloud sees an up-to-date world view as quickly as possible
        without waiting up to 30 s for the low-priority tier to drain.
        """
        snapshot = self._state.snapshot()

        now = time.time()
        changes = []
        for key, value in snapshot.items():
            # Skip cloud-internal and ISC state
            if key.startswith("system.cloud.") or key.startswith("isc."):
                continue
            changes.append({
                "key": key,
                "value": value,
                "ts": self._format_ts(now),
            })

        # Always send the first message with the snapshot flag, even when
        # there are no state keys, so the cloud clears stale data.
        if not changes:
            await self._agent.send_message(
                STATE_BATCH, {"changes": [], "snapshot": True}
            )
        else:
            await self._send_changes_chunked(changes, snapshot=True)

        log.info("State relay: sent initial snapshot (%d keys)", len(changes))

    def _on_state_change(
        self, key: str, old_value: Any, new_value: Any, source: str
    ) -> None:
        """
        Callback for state changes. Adds to the priority-bucketed batch.

        Called synchronously from StateStore._notify_listeners, so we do
        not await anything here.

        When a key is deleted (rather than set to None), the key will no
        longer exist in the store at callback time. We detect this by
        probing the store and flag the entry so the cloud removes the key.
        """
        # Do not relay cloud-internal state changes
        if key.startswith("system.cloud."):
            return

        # Do not relay ISC remote state (would create echo loops)
        if key.startswith("isc."):
            return

        entry: dict[str, Any] = {
            "key": key,
            "value": new_value,
            "ts": time.time(),
        }

        # Detect deletion: StateStore.delete() removes the key from the
        # store *before* firing notifications, so if the key is absent the
        # change was a delete rather than a set-to-None.
        if new_value is None and self._state.get(key, _MISSING) is _MISSING:
            entry["deleted"] = True

        tier = self._key_tier(key)
        with self._batch_lock:
            self._batches[tier].append(entry)

    def _key_tier(self, key: str) -> str:
        """Return the relay tier (``"top"``, ``"child"``, or ``"low"``)
        for a state key.

        Top-level keys (``device.<id>.<prop>`` plus ``var.*``, ``system.*``,
        ``ui.*``, ``plugin.*``) and anything we cannot classify ride the
        fast tier so the cloud does not lag behind real device state.
        Five-segment ``device.<id>.<type>.<padded>.<prop>`` keys consult
        the parent device's driver for a declared ``cloud_priority``.
        """
        cached = self._tier_cache.get(key)
        if cached is not None:
            return cached

        tier = _TIER_TOP
        if key.startswith("device."):
            parts = key.split(".")
            # device.<id>.<child_type>.<padded>.<prop>
            if len(parts) == 5:
                device_id, child_type, _padded, prop = (
                    parts[1], parts[2], parts[3], parts[4]
                )
                tier = self._lookup_child_tier(device_id, child_type, prop)

        self._tier_cache[key] = tier
        return tier

    def _lookup_child_tier(
        self, device_id: str, child_type: str, prop: str
    ) -> str:
        """Resolve the priority tier for a candidate child-entity key by
        consulting the parent device's driver.

        Returns ``"top"`` if the device or schema does not claim the key
        as a child property — unknown keys default to fast cadence rather
        than being silently slow-walked to the cloud.

        Driver-declared ``cloud_priority`` values:
            ``"low"``  -> ``"low"`` tier (30 s cadence)
            ``"high"`` -> ``"top"`` tier (2 s cadence, snappier than
                          the default child cadence for routing / mute
                          state)
            anything else (incl. unset) -> ``"child"`` tier (5 s cadence)
        """
        devices = getattr(self._agent, "devices", None)
        if devices is None or not hasattr(devices, "get_driver"):
            return _TIER_TOP
        driver = devices.get_driver(device_id)
        if driver is None:
            return _TIER_TOP
        types = driver.DRIVER_INFO.get("child_entity_types", {})
        if not isinstance(types, dict):
            return _TIER_TOP
        type_def = types.get(child_type)
        if not isinstance(type_def, dict):
            return _TIER_TOP
        var_def = type_def.get("state_variables", {}).get(prop)
        if not isinstance(var_def, dict):
            return _TIER_CHILD
        priority = var_def.get("cloud_priority")
        if priority == "low":
            return _TIER_LOW
        if priority == "high":
            return _TIER_TOP
        return _TIER_CHILD

    async def _flush_bucket_loop(
        self, bucket: str, config_key: str, default_interval: float
    ) -> None:
        """Per-tier flush loop. Reads the cadence on every iteration so a
        cloud-pushed ``config_update`` is picked up without a restart."""
        try:
            while self._running:
                interval = self._agent._config.get(config_key, default_interval)
                await asyncio.sleep(interval)
                await self._flush_bucket(bucket)
        except asyncio.CancelledError:
            return

    async def _flush_bucket(self, bucket: str) -> None:
        """Drain one priority bucket, dedupe by key, send paced chunks."""
        with self._batch_lock:
            entries = self._batches[bucket]
            self._batches[bucket] = []

        if not entries:
            return

        # Deduplicate: keep only the last value per key (the cloud cares
        # about current state, not intermediate transitions).
        deduped: dict[str, dict[str, Any]] = {}
        for entry in entries:
            deduped[entry["key"]] = entry
        deduped_entries = list(deduped.values())

        # Format timestamps as ISO strings
        changes = []
        for entry in deduped_entries:
            change: dict[str, Any] = {
                "key": entry["key"],
                "value": entry["value"],
                "ts": self._format_ts(entry["ts"]),
            }
            if entry.get("deleted"):
                change["deleted"] = True
            changes.append(change)

        await self._send_changes_chunked(changes)
        log.debug(
            "State relay: flushed %d change(s) on '%s' bucket",
            len(changes), bucket,
        )

    async def _send_changes_chunked(
        self, changes: list[dict[str, Any]], snapshot: bool = False
    ) -> None:
        """Send ``changes`` over the agent in ``state_batch_max_size`` chunks.

        When ``snapshot=True``, the first chunk carries ``"snapshot": true``
        so the cloud clears stale state from the previous session.

        When the total exceeds ``state_batch_pace_threshold``, sleeps
        ``state_batch_pace_delay`` between chunks so a 40k-key burst does
        not push 80+ WebSocket frames out in a single event-loop tick.
        """
        if not changes:
            return

        max_size = self._agent._config.get("state_batch_max_size", 500)
        pace_threshold = self._agent._config.get(
            "state_batch_pace_threshold", _DEFAULT_PACE_THRESHOLD
        )
        pace_delay = self._agent._config.get(
            "state_batch_pace_delay", _DEFAULT_PACE_DELAY
        )

        if len(changes) > max_size:
            log.warning(
                f"State relay: batch overflow — {len(changes)} keys, "
                f"sending in chunks of {max_size}"
            )

        paced = len(changes) > pace_threshold
        # Pre-compute the start index of the last chunk so we do not sleep
        # after the final send (which would only add latency).
        last_chunk_start = ((len(changes) - 1) // max_size) * max_size

        for i in range(0, len(changes), max_size):
            chunk = changes[i:i + max_size]
            payload: dict[str, Any] = {"changes": chunk}
            if snapshot and i == 0:
                payload["snapshot"] = True
            await self._agent.send_message(STATE_BATCH, payload)
            if paced and i != last_chunk_start and pace_delay > 0:
                await asyncio.sleep(pace_delay)

    @staticmethod
    def _format_ts(epoch: float) -> str:
        """Format an epoch timestamp as ISO 8601."""
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
