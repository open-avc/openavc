"""
OpenAVC Cloud — State change relay to the cloud.

Subscribes to the StateStore for state changes, batches them over a
configurable window, and sends state_batch messages to the cloud via
the CloudAgent.
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


class StateRelay:
    """
    Batches state changes and forwards them to the cloud.

    Subscribes to the StateStore's wildcard listener. Collects changes
    over a configurable window (default 2 seconds), then sends them as
    a single state_batch message.
    """

    def __init__(self, agent: CloudAgent, state: StateStore):
        """
        Args:
            agent: The CloudAgent to send messages through.
            state: The StateStore to subscribe to.
        """
        self._agent = agent
        self._state = state

        self._batch: list[dict[str, Any]] = []
        self._batch_lock = threading.Lock()
        self._flush_task: asyncio.Task | None = None
        self._sub_id: str | None = None
        self._running = False

    async def start(self) -> None:
        """Start listening for state changes and batching them."""
        if self._running:
            return

        self._running = True

        # Discard any batch entries left over from a previous connection.
        # They're stale — the snapshot we're about to send is authoritative.
        # Without this, old value-update entries could re-insert keys for
        # devices that were deleted while the agent was disconnected.
        with self._batch_lock:
            self._batch.clear()

        # Send full state snapshot so the cloud has current values for keys
        # that were set before the relay started (e.g. device.*.connected).
        # The snapshot includes a flag so the cloud clears stale state from
        # a previous session before applying the fresh values.
        await self._send_initial_snapshot()

        self._sub_id = self._state.subscribe("*", self._on_state_change)
        self._flush_task = asyncio.create_task(self._flush_loop())
        log.info("State relay: started")

    async def stop(self) -> None:
        """Stop the state relay."""
        self._running = False

        if self._sub_id:
            self._state.unsubscribe(self._sub_id)
            self._sub_id = None

        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        log.info("State relay: stopped")

    async def _send_initial_snapshot(self) -> None:
        """Send all current state values to the cloud as the first batch.

        This ensures the cloud has device.*.connected and other state keys
        that were set before the relay started listening for changes.

        The first chunk includes ``"snapshot": true`` so the cloud clears any
        stale state left over from a previous session before ingesting.  This
        handles the case where devices were deleted while the agent was
        disconnected — the cloud won't have those keys in the snapshot and
        the clear removes them.
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
        max_size = self._agent._config.get("state_batch_max_size", 500)
        if not changes:
            await self._agent.send_message(
                STATE_BATCH, {"changes": [], "snapshot": True}
            )
        else:
            for i in range(0, len(changes), max_size):
                chunk = changes[i:i + max_size]
                payload: dict[str, Any] = {"changes": chunk}
                if i == 0:
                    payload["snapshot"] = True
                await self._agent.send_message(STATE_BATCH, payload)

        log.info("State relay: sent initial snapshot (%d keys)", len(changes))

    def _on_state_change(
        self, key: str, old_value: Any, new_value: Any, source: str
    ) -> None:
        """
        Callback for state changes. Adds to the batch.

        This is called synchronously from StateStore._notify_listeners,
        so we don't await anything here.

        When a key is deleted (rather than set to None), the key will no
        longer exist in the store at callback time.  We detect this by
        probing the store and flag the entry so the cloud removes the key.
        """
        # Don't relay cloud-internal state changes
        if key.startswith("system.cloud."):
            return

        # Don't relay ISC remote state (would create echo loops)
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

        with self._batch_lock:
            self._batch.append(entry)

    async def _flush_loop(self) -> None:
        """Periodically flush the state batch to the cloud."""
        try:
            while self._running:
                interval = self._agent._config.get("state_batch_interval", 2)
                await asyncio.sleep(interval)

                if not self._batch:
                    continue

                # Grab the batch
                with self._batch_lock:
                    batch = self._batch[:]
                    self._batch.clear()

                if not batch:
                    continue

                # Enforce max batch size
                max_size = self._agent._config.get("state_batch_max_size", 500)
                if len(batch) > max_size:
                    # Keep oldest entries to maintain causality order
                    log.warning(
                        f"State relay: batch overflow — {len(batch)} changes, "
                        f"keeping oldest {max_size}"
                    )
                    batch = batch[:max_size]

                # Format timestamps as ISO strings
                changes = []
                for entry in batch:
                    change: dict[str, Any] = {
                        "key": entry["key"],
                        "value": entry["value"],
                        "ts": self._format_ts(entry["ts"]),
                    }
                    if entry.get("deleted"):
                        change["deleted"] = True
                    changes.append(change)

                # Send
                await self._agent.send_message(STATE_BATCH, {"changes": changes})
                log.debug(f"State relay: sent {len(changes)} change(s)")

        except asyncio.CancelledError:
            return

    @staticmethod
    def _format_ts(epoch: float) -> str:
        """Format an epoch timestamp as ISO 8601."""
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
