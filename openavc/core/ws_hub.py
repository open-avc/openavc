"""
WebSocket push hub — the engine's fan-out path to connected panels and IDEs.

Two halves of one pipeline:

  * **Client fan-out.** Every client gets a bounded send queue drained by its
    own writer task, so a broadcast never awaits a client's TCP send and one
    wedged panel can't stall macros, triggers, or the state flush loop.
  * **State coalescing.** State changes arrive far faster than a panel needs
    them, so they batch into a 50 ms window and go out as one
    ``state.update`` / ``state.delete`` message.

Owned by the engine (``engine.ws``). ``Engine.broadcast_ws`` forwards here
and stays the platform-wide entry point for sending a message to panels.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openavc.core.state_store import StateStore
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Sentinel used to distinguish deleted keys (absent from the store) from
# keys that exist with a None value. Used by on_state_change.
_STATE_MISSING = object()

# Per-client WS send queue depth. At the state flush loop's 20 msg/s ceiling
# this is >10s of buffered updates — a client that far behind is wedged, not
# slow, and is dropped to reconnect with a fresh snapshot.
_WS_SEND_QUEUE_MAX = 256

# State flush cadence: 50ms = max 20 updates/sec to each client.
_STATE_FLUSH_INTERVAL = 0.05


def _log_task_exception(task: asyncio.Task) -> None:
    """Log an exception from a fire-and-forget task instead of swallowing it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"Background task failed: {exc}", exc_info=exc)


class WSHub:
    """Registry of connected WebSocket clients + the batched push loop."""

    def __init__(self, state: StateStore):
        self._state = state

        # WebSocket clients (set of WebSocket connections)
        self._clients: set = set()
        # Per-client namespace filters: id(ws) -> tuple of prefix strings
        self._ns_filters: dict[int, tuple[str, ...]] = {}
        # Per-client bounded send queues + their writer tasks: id(ws) -> ...
        # Broadcasts enqueue and return; each writer drains its own client,
        # so one wedged client can never stall the emit path (macros,
        # triggers, state flushes). Overflow drops the client (see broadcast).
        self._send_queues: dict[int, asyncio.Queue] = {}
        self._writers: dict[int, asyncio.Task] = {}
        # Per-client delivery gates: a writer sends nothing until its event
        # is set. Lets the connection handler register (and start buffering)
        # BEFORE taking the state snapshot, then release delivery once the
        # snapshot is on the wire — closing the gap where changes flushed
        # mid-handshake reached only already-registered clients.
        self._ready_events: dict[int, asyncio.Event] = {}
        # Strong refs for short-lived cleanup tasks (socket closes, cancelled
        # writers unwinding) — asyncio only weakly references tasks, so an
        # unreferenced one can be GC'd before it runs.
        self._cleanup_tasks: set[asyncio.Task] = set()

        # State batching for WebSocket push
        self._state_batch: dict[str, Any] = {}
        # Keys that were deleted (rather than set) since the last flush.
        # Tracked separately so the flush loop can emit a state.delete WS
        # message — clients can't tell delete from set-to-None otherwise.
        self._state_deleted_keys: set[str] = set()
        self._batch_task: asyncio.Task | None = None
        self._running = False

    # --- Client registry ---

    @property
    def client_count(self) -> int:
        """Connected clients — reported in system status and cloud heartbeats."""
        return len(self._clients)

    def add_client(self, ws, ns_prefixes: tuple[str, ...] | None = None,
                   *, defer_delivery: bool = False) -> None:
        """Register a WebSocket client with optional namespace filter.

        Each client gets a bounded send queue drained by its own writer
        task, so broadcast never awaits a client's TCP send directly.

        With ``defer_delivery=True`` broadcasts buffer into the queue but
        nothing is delivered until ``mark_client_ready()``. The connection
        handler registers before snapshotting so changes flushed
        mid-handshake buffer here instead of being missed, then releases
        delivery once the snapshot is on the wire.
        """
        self._clients.add(ws)
        if ns_prefixes:
            self._ns_filters[id(ws)] = ns_prefixes
        queue: asyncio.Queue = asyncio.Queue(maxsize=_WS_SEND_QUEUE_MAX)
        self._send_queues[id(ws)] = queue
        ready = asyncio.Event()
        if not defer_delivery:
            ready.set()
        self._ready_events[id(ws)] = ready
        writer = asyncio.create_task(self._send_loop(ws, queue, ready))
        writer.add_done_callback(_log_task_exception)
        self._writers[id(ws)] = writer
        log.info(f"WebSocket client connected ({len(self._clients)} total)")

    def mark_client_ready(self, ws) -> None:
        """Release a ``defer_delivery`` client's writer once its snapshot has
        been sent. Queued updates may partially predate the snapshot; replay
        is safe because state messages carry full per-key values (the client
        converges on the latest, never regresses past it)."""
        ready = self._ready_events.get(id(ws))
        if ready is not None:
            ready.set()

    def remove_client(self, ws) -> None:
        """Unregister a WebSocket client."""
        if ws not in self._clients and id(ws) not in self._writers:
            return  # Already dropped (send failure / overflow) — keep idempotent
        self._drop_client(ws, cancel_writer=True)
        log.info(f"WebSocket client disconnected ({len(self._clients)} total)")

    def _drop_client(self, ws, *, cancel_writer: bool) -> None:
        """Remove a client from every registry; optionally cancel its writer.

        ``cancel_writer=False`` is for the writer task removing its own
        client on a send failure — it is about to exit on its own.
        """
        self._clients.discard(ws)
        self._ns_filters.pop(id(ws), None)
        self._send_queues.pop(id(ws), None)
        self._ready_events.pop(id(ws), None)
        writer = self._writers.pop(id(ws), None)
        if writer is not None and cancel_writer and not writer.done():
            writer.cancel()
            # Keep a strong ref while the cancelled task unwinds.
            self._cleanup_tasks.add(writer)
            writer.add_done_callback(self._cleanup_tasks.discard)

    async def _send_loop(
        self, ws, queue: asyncio.Queue, ready: asyncio.Event
    ) -> None:
        """Drain one client's send queue for the life of its connection.

        A send failure means the peer is gone or the transport broke: drop
        the client here; the connection handler's own remove_client on
        unwind is a no-op by then.
        """
        try:
            await ready.wait()
            while True:
                text = await queue.get()
                try:
                    await ws.send_text(text)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._drop_client(ws, cancel_writer=False)
            log.info(
                f"WebSocket client dropped on send failure "
                f"({len(self._clients)} total)"
            )
        finally:
            # Mark anything left undelivered as done so flush_sends()
            # joiners can't hang on a dead client's queue.
            while True:
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

    def _close_client(self, ws) -> None:
        """Best-effort close of an overflowed client's socket (fire-and-forget)."""

        async def _close() -> None:
            try:
                await ws.close(code=1013)  # 1013 = try again later
            except Exception:
                pass  # Peer already gone — nothing to close

        task = asyncio.create_task(_close())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def flush_sends(self) -> None:
        """Wait until every currently-queued WS message has been sent.

        Used by the shutdown flush (best-effort, with a timeout) and by
        tests that need delivery to have happened before asserting.
        """
        queues = list(self._send_queues.values())
        if queues:
            await asyncio.gather(*(q.join() for q in queues))

    # --- Broadcast ---

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Queue a JSON message for delivery to all connected WS clients.

        Never awaits a client send: messages go onto per-client bounded
        queues, so a slow or wedged client can't stall the caller (macro
        and trigger event emits, the 50ms state flush loop). A client
        whose queue overflows is dropped and its socket closed — panels
        auto-reconnect and resnapshot, which is cheaper than letting one
        sick client apply backpressure engine-wide.
        """
        if not self._clients:
            return

        msg_type = message.get("type")
        # state.update and state.delete carry per-key payloads; a client with
        # ns_prefixes should only receive keys under those namespaces.
        is_filterable = msg_type in ("state.update", "state.delete")

        full_text: str | None = None
        for ws in list(self._clients):
            ns = self._ns_filters.get(id(ws)) if is_filterable else None
            if not ns:
                if full_text is None:
                    full_text = json.dumps(message)
                text = full_text
            elif msg_type == "state.update":
                changes = message.get("changes", {})
                filtered = {k: v for k, v in changes.items()
                            if k.startswith(ns)}
                if not filtered:
                    continue
                text = json.dumps({"type": "state.update", "changes": filtered})
            else:  # state.delete
                keys = message.get("keys", [])
                filtered_keys = [k for k in keys if k.startswith(ns)]
                if not filtered_keys:
                    continue
                text = json.dumps({"type": "state.delete", "keys": filtered_keys})

            queue = self._send_queues.get(id(ws))
            if queue is None:
                continue
            try:
                queue.put_nowait(text)
            except asyncio.QueueFull:
                log.warning(
                    "WebSocket client send queue overflowed "
                    f"({_WS_SEND_QUEUE_MAX} messages) — dropping client so it "
                    "reconnects with a fresh snapshot"
                )
                self._drop_client(ws, cancel_writer=True)
                self._close_client(ws)

    # --- State batching ---

    def on_state_change(
        self, key: str, old_value: Any, new_value: Any, source: str
    ) -> None:
        """Collect state changes into a batch for WebSocket push.

        Safe in single-threaded asyncio: this sync callback runs atomically
        between awaits of the flush loop. The lock is acquired in the flush
        loop to guard the read-clear operation.

        Distinguishes deletion from set-to-None by probing the store: when
        StateStore.delete() fires the listener, the key has already been
        removed. Deletes go to _state_deleted_keys; sets go to _state_batch.
        Either action clears the key from the other bucket so a delete-then-set
        (or set-then-delete) within one window resolves to the latest action.
        """
        is_deleted = new_value is None and self._state.get(key, _STATE_MISSING) is _STATE_MISSING
        if is_deleted:
            self._state_batch.pop(key, None)
            self._state_deleted_keys.add(key)
        else:
            self._state_deleted_keys.discard(key)
            self._state_batch[key] = new_value

    def start_state_flush(self) -> None:
        """Start the periodic state flush loop (engine startup)."""
        self._running = True
        self._batch_task = asyncio.create_task(self._flush_state_batch_loop())
        self._batch_task.add_done_callback(_log_task_exception)

    async def stop_state_flush(self) -> None:
        """Stop the flush loop; its unwind does a final best-effort flush."""
        self._running = False
        task = self._batch_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._batch_task = None

    async def _flush_state_batch_loop(self) -> None:
        """Periodically flush batched state changes to WebSocket clients."""
        try:
            while self._running:
                await asyncio.sleep(_STATE_FLUSH_INTERVAL)
                if not self._state_batch and not self._state_deleted_keys:
                    continue
                await self.flush_state_batch()
        except asyncio.CancelledError:
            pass
        finally:
            # Best-effort flush of any remaining batch during shutdown.
            if self._state_batch or self._state_deleted_keys:
                try:
                    await self.flush_state_batch()
                    # The flush only enqueues; give the per-client writers a
                    # moment to actually deliver before the process exits.
                    await asyncio.wait_for(self.flush_sends(), timeout=1.0)
                except Exception:  # Errors are non-critical at shutdown
                    pass

    async def flush_state_batch(self) -> None:
        """Drain _state_batch and _state_deleted_keys into WS messages.

        Emits a state.update for set keys and a state.delete for deleted
        keys. Atomic swap of the buffers ensures on_state_change calls
        between the two broadcasts land in the next flush window.
        """
        # Swap out the buffers atomically — sync on_state_change can't
        # interleave with us between awaits, but this pattern is safe if
        # callers ever change.
        batch = self._state_batch
        self._state_batch = {}
        deleted = self._state_deleted_keys
        self._state_deleted_keys = set()

        if batch:
            await self.broadcast({
                "type": "state.update",
                "changes": batch,
            })
        if deleted:
            await self.broadcast({
                "type": "state.delete",
                "keys": sorted(deleted),
            })
