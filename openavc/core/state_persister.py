"""
StatePersister — saves persistent variable values to disk.

Watches for changes to variables marked with persist=True and writes
their current values to <project_dir>/state.json. On startup, loads
saved values so they survive server restarts.

The file is written on a debounced 1-second interval to avoid disk
thrashing when multiple variables change rapidly.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from openavc.core.state_store import is_flat_primitive
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Sentinel distinguishing "key absent from the store" from "key present and
# holding None". A persistent variable can legitimately hold None (a cleared
# selection, "no source") — that must survive a restart, not revert to its
# default — so _write() persists a present None but skips a key that was
# never set.
_MISSING = object()




class StatePersister:
    """Persists selected state keys to a JSON file with debounced writes."""

    def __init__(self, state_file: Path, state_store: Any) -> None:
        self._state_file = state_file
        self._state_store = state_store
        self._persistent_keys: set[str] = set()
        self._dirty = False
        self._sub_ids: list[str] = []
        self._flush_task: asyncio.Task | None = None
        self._stopped = False

    def load(self) -> dict[str, Any]:
        """Load persisted values from disk. Returns dict of key -> value.

        Non-primitive values are dropped and logged — the store holds flat
        primitives only, and the load path must not be a back door for nested
        objects. A corrupt or non-dict file is quarantined (moved to
        ``state.json.corrupt``) rather than silently overwritten, so it can be
        inspected/recovered instead of becoming silent total data loss.
        """
        if not self._state_file.exists():
            log.debug("No state.json found, starting fresh")
            return {}
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log.warning(f"state.json is corrupt, quarantining: {e}")
            self._quarantine("corrupt")
            return {}
        except OSError as e:
            # Transient I/O error (locked/permission) — leave the possibly-fine
            # file in place rather than quarantining it.
            log.warning(f"Failed to read state.json: {e}")
            return {}
        if not isinstance(data, dict):
            log.warning("state.json is not a dict, quarantining")
            self._quarantine("malformed")
            return {}
        clean: dict[str, Any] = {}
        for key, value in data.items():
            if is_flat_primitive(value):
                clean[key] = value
            else:
                log.warning(
                    "Dropping non-primitive persisted value for %r (%s)",
                    key,
                    type(value).__name__,
                )
        log.info(f"Loaded {len(clean)} persisted value(s) from state.json")
        return clean

    def _quarantine(self, reason: str) -> None:
        """Move an unusable state.json aside so it isn't silently overwritten."""
        quarantine = self._state_file.parent / (self._state_file.name + ".corrupt")
        try:
            os.replace(str(self._state_file), str(quarantine))
            log.warning("Moved %s state.json to %s", reason, quarantine.name)
        except OSError as e:
            log.warning("Could not quarantine state.json: %s", e)

    def start(self, persistent_keys: set[str]) -> None:
        """Subscribe to changes on the given state keys."""
        self._persistent_keys = persistent_keys
        self._stopped = False
        for key in persistent_keys:
            sub_id = self._state_store.subscribe(
                key, self._on_state_change
            )
            self._sub_ids.append(sub_id)
        if persistent_keys:
            log.info(f"Watching {len(persistent_keys)} persistent variable(s)")

    def update_keys(self, persistent_keys: set[str]) -> None:
        """Update the set of watched keys (called on reload).

        Flush any pending write against the *current* key set before swapping,
        so an in-flight debounced flush can't fire against the new set and lose
        or mis-write a value when a reload races a recent change.

        When variables are de-persisted (their keys drop out of the set),
        rewrite state.json immediately so their stale values don't survive to
        the next restart — ``_write()`` only emits the *current* persistent
        keys, which prunes the removed ones from disk.
        """
        # Commit the pending change under the OLD key set, then stop the
        # debounce so a queued flush can't land against the swapped set.
        self.flush()
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        removed = self._persistent_keys - persistent_keys
        # Unsubscribe old
        for sub_id in self._sub_ids:
            self._state_store.unsubscribe(sub_id)
        self._sub_ids.clear()
        # Re-subscribe
        self._persistent_keys = persistent_keys
        for key in persistent_keys:
            sub_id = self._state_store.subscribe(
                key, self._on_state_change
            )
            self._sub_ids.append(sub_id)
        # Prune de-persisted keys from disk so they aren't restored next start.
        if removed and self._state_file.exists():
            self._write()

    def _on_state_change(self, key: str, old_value: Any, new_value: Any, source: str) -> None:
        """Called when a persistent variable changes."""
        self._dirty = True
        # Schedule a debounced flush
        if self._flush_task is None or self._flush_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._flush_task = loop.create_task(self._debounced_flush())
                self._flush_task.add_done_callback(self._on_flush_done)
            except RuntimeError:
                pass  # No event loop (sync context)

    @staticmethod
    def _on_flush_done(task: asyncio.Task) -> None:
        """Surface a failure in the fire-and-forget debounced flush task.

        Without this, an exception raised inside ``_debounced_flush`` (other
        than the OSError ``_write`` already swallows) would be silently lost
        and persistence would quietly stop.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("State persister flush task failed: %s", exc, exc_info=exc)

    async def _debounced_flush(self) -> None:
        """Wait 1 second then flush if still dirty."""
        await asyncio.sleep(1.0)
        if self._dirty and not self._stopped:
            self._write()

    def flush(self) -> None:
        """Immediately write any pending changes to disk."""
        if self._dirty:
            self._write()

    def stop(self) -> None:
        """Flush pending writes and unsubscribe."""
        self._stopped = True
        self.flush()
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        for sub_id in self._sub_ids:
            self._state_store.unsubscribe(sub_id)
        self._sub_ids.clear()

    def _write(self) -> None:
        """Write current persistent values to state.json atomically."""
        data: dict[str, Any] = {}
        for key in self._persistent_keys:
            value = self._state_store.get(key, _MISSING)
            if value is _MISSING:
                continue  # key never set — nothing to persist
            data[key] = value  # None is a first-class, persisted value

        # Atomic write: temp file then rename. json.dumps is inside the try so
        # a non-serializable value is caught and logged here instead of being
        # raised into orderly shutdown or swallowed by the debounced flush
        # task (which would silently stop all persistence).
        fd = None
        tmp_path = None
        try:
            content = json.dumps(data, indent=2, ensure_ascii=False)
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_file.parent),
                suffix=".tmp",
                prefix=".state_",
            )
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            fd = None
            os.replace(tmp_path, str(self._state_file))
            tmp_path = None
            self._dirty = False
            log.debug(f"Persisted {len(data)} variable(s) to state.json")
        except (TypeError, ValueError, OSError):
            log.exception("Failed to write state.json")
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
