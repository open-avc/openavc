"""A YAML driver's response throttle: one write per window, trailing edge.

A response rule may declare ``throttle_ms:``. A reply inside the window does
not write; its writes are held, and the newest hold per scope is written
when the window lapses. The rules worth knowing before changing anything
here:

* **Nothing is dropped.** A value that stops changing inside a window (a
  talker's final position, a fader that came to rest) still lands when the
  window ends, at one write per window.
* **The window is per scope.** A per-channel meter is one ``child_set:``
  rule matching every channel, so a rule-wide window would let whichever
  channel arrived first consume it and starve the rest. The reply rules
  pass the routed child as the scope; flat rules share the single ``""``
  scope.
* **A newer write wins.** A hold remembers what each key held when it was
  taken, and the flush writes only the keys nothing else wrote since (a
  polled reply, another rule), so an older held value never overwrites a
  newer one.
* **No loop, no timer.** A hold taken outside a running event loop (a
  synchronous caller) has nothing to flush on and is dropped.

The tables the holds live in are the compiled reply rules'
(``compiled_protocol.compile_driver``): each rule's throttle state is a dict
with its ``window`` (seconds) and ``last`` (scope -> monotonic time of its
last fire); the hold adds ``pending`` and ``timers``.

``ReplyThrottle`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. From its host it needs ``device_id``, the
compiled ``_compiled_responses``, ``_osc_responses`` and ``_json_responses``
tables, and the state and child-state accessors ``BaseDriver`` provides.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# is where a device's log view files them.
log = get_logger("openavc.drivers.configurable")

# Where each compiled table keeps a rule's throttle state: the regex and OSC
# rule tuples carry it fourth, the JSON-body rule tuples second.
_THROTTLE_SLOT = {"_compiled_responses": 3, "_osc_responses": 3, "_json_responses": 1}


class ReplyThrottle:
    """Hold, flush and drop a response rule's throttled writes."""

    def _throttle_hold(
        self,
        tstate: dict[str, Any] | None,
        scope: str,
        flat: list[tuple[str, Any]],
        children: list[tuple[str, Any, dict[str, Any]]],
    ) -> bool:
        """False when the rule may apply its writes now (outside its window:
        now is stamped as the scope's last fire). True when it fired inside
        the window: the writes are held for the window's end and the caller
        applies nothing."""
        if not tstate:
            return False
        now = time.monotonic()
        window = tstate["window"]
        last: dict[str, float] = tstate["last"]
        if now - last.get(scope, float("-inf")) >= window:
            # A hold still waiting for its timer is older than this frame.
            self._drop_held(tstate, scope)
            last[scope] = now
            return False
        pending = tstate.setdefault("pending", {})
        timers = tstate.setdefault("timers", {})
        pending[scope] = (
            flat, children, self._flat_before(flat), self._children_before(children),
        )
        if scope not in timers:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No loop to flush on (a synchronous caller): drop, as a
                # throttle without a timer has to.
                pending.pop(scope, None)
                return True
            delay = window - (now - last[scope])
            timers[scope] = loop.call_later(
                delay, self._flush_held, tstate, scope
            )
        return True

    def _flat_before(self, flat: list[tuple[str, Any]]) -> dict[str, Any]:
        """What each flat key a hold writes holds now."""
        return {key: self.get_state(key) for key, _ in flat}

    def _children_before(
        self, children: list[tuple[str, Any, dict[str, Any]]],
    ) -> dict[tuple[str, Any], dict[str, Any]]:
        """What each child property a hold writes holds now."""
        before: dict[tuple[str, Any], dict[str, Any]] = {}
        for ctype, local_id, updates in children:
            current = self.get_child_state(ctype, local_id)
            before[(ctype, local_id)] = {prop: current.get(prop) for prop in updates}
        return before

    def _flush_held(self, tstate: dict[str, Any], scope: str) -> None:
        """Write a scope's held update when its window lapses. The flush counts
        as the scope's fire, so a frame inside the next window is held again.
        A key something else wrote since the hold keeps that newer value."""
        tstate.get("timers", {}).pop(scope, None)
        held = tstate.get("pending", {}).pop(scope, None)
        if held is None:
            return
        flat, children, flat_before, child_before = held
        tstate["last"][scope] = time.monotonic()
        try:
            self._write_held_flat(flat, flat_before)
            for ctype, local_id, updates in children:
                self._write_held_child(
                    ctype, local_id, updates, child_before.get((ctype, local_id), {}),
                )
        except Exception:
            log.exception(f"[{self.device_id}] Writing a throttled response failed")

    def _write_held_flat(
        self, flat: list[tuple[str, Any]], before: dict[str, Any],
    ) -> None:
        for key, value in flat:
            if self.get_state(key) == before.get(key):
                self.set_state(key, value)

    def _write_held_child(
        self,
        ctype: str,
        local_id: Any,
        updates: dict[str, Any],
        before: dict[str, Any],
    ) -> None:
        """A held child write, minus what changed since and a child now gone."""
        if not self.is_child_registered(ctype, local_id):
            return
        current = self.get_child_state(ctype, local_id)
        kept = {
            prop: value for prop, value in updates.items()
            if current.get(prop) == before.get(prop)
        }
        if kept:
            self.set_child_state_batch(ctype, local_id, kept)

    @staticmethod
    def _drop_held(tstate: dict[str, Any], scope: str) -> None:
        timer = tstate.get("timers", {}).pop(scope, None)
        if timer is not None:
            timer.cancel()
        tstate.get("pending", {}).pop(scope, None)

    def _drop_all_held(self) -> None:
        """Cancel every held throttled write: the connection they came in on
        is going away, and a timer must not write into a device that may have
        been removed."""
        for table, slot in _THROTTLE_SLOT.items():
            for entry in getattr(self, table, None) or []:
                tstate = entry[slot]
                if tstate:
                    self._drop_scopes(tstate)

    def _drop_scopes(self, tstate: dict[str, Any]) -> None:
        for scope in list(tstate.get("timers", {})):
            self._drop_held(tstate, scope)
        tstate.get("pending", {}).clear()

    def _apply_writes(
        self,
        flat: list[tuple[str, Any]],
        children: list[tuple[str, Any, dict[str, Any]]],
    ) -> None:
        """A reply rule's writes, applied now (no throttle, or outside its
        window)."""
        for key, value in flat:
            self.set_state(key, value)
        for ctype, local_id, updates in children:
            self.set_child_state_batch(ctype, local_id, updates)
