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

from server.utils.logger import get_logger

log = get_logger(__name__)


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
        """Load persisted values from disk. Returns dict of key -> value."""
        if not self._state_file.exists():
            log.debug("No state.json found, starting fresh")
            return {}
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                log.warning("state.json is not a dict, ignoring")
                return {}
            log.info(f"Loaded {len(data)} persisted value(s) from state.json")
            return data
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to read state.json: {e}")
            return {}

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
        """Update the set of watched keys (called on reload)."""
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

    def _on_state_change(self, key: str, old_value: Any, new_value: Any, source: str) -> None:
        """Called when a persistent variable changes."""
        self._dirty = True
        # Schedule a debounced flush
        if self._flush_task is None or self._flush_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._flush_task = loop.create_task(self._debounced_flush())
            except RuntimeError:
                pass  # No event loop (sync context)

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
            value = self._state_store.get(key)
            if value is not None:
                data[key] = value
            else:
                # Still persist explicit None/default
                data[key] = self._state_store.get(key)

        content = json.dumps(data, indent=2, ensure_ascii=False)

        # Atomic write: temp file then rename
        fd = None
        tmp_path = None
        try:
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
        except OSError:
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
