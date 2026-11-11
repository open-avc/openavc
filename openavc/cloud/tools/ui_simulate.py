"""Pressing a control the way a panel would, without a panel: ``simulate_ui_action``.

The interaction goes through the engine's own door (``handle_ui_event``), so
what comes back is what a touch would have done: the actions it dispatched,
and the state changes heard while it ran. Three things a panel does that the
server never sees are done here instead. A navigate is broadcast as well as
emitted, since panels switch page only on the broadcast. A press on a toggle
is resolved against its bound key the way the browser resolves it
(``ui_events.resolve_press``), or a working toggle reports its on branch every
time. And a silence is explained, against the element the runtime reached
(``ui_events.find_element``).

The capture hears everything the state store hears while the interaction runs,
so it keeps only what the action could have caused (not system metrics,
another tool, a cloud push, an ISC peer or discovery) and only what the cloud
may be sent (``state_relay``: the excluded keys, an address's password). Device
polling shares a device's source with a command's real effect, so it cannot be
told apart here.
"""

from typing import Any

from openavc.cloud.state_relay import cloud_state_value, is_cloud_excluded_key
from openavc.core.ui_events import find_element, resolve_press

#: State-change sources a simulated interaction can never itself produce.
_IGNORED_SOURCES = frozenset({
    "heartbeat", "system", "cloud", "ai", "isc", "discovered",
})

#: The touches, sent as the event the press resolves to, with no data.
_TOUCHES = ("press", "release", "hold")
#: The interactions that carry a value.
_VALUE_EVENTS = ("change", "submit")


async def simulate_ui_action(engine: Any, state: Any, input: dict) -> dict:
    """One interaction with one control, or a page move, and what it did."""
    action = input.get("action", "")
    element_id = input.get("element_id", "")
    dry_run = bool(input.get("dry_run"))
    if action == "navigate":
        return await _navigate(engine, input.get("page_id", ""), dry_run)
    if not element_id:
        return {"error": "element_id is required for this action"}

    effective, toggle_note = _resolved(engine, state, action, element_id)
    with _Capture(state) as capture:
        event = _event_args(action, effective, element_id, input.get("value"))
        if event is None:
            return {"error": f"Unknown action: {action}"}
        try:
            dispatched = await engine.handle_ui_event(*event, dry_run=dry_run)
        except Exception as e:
            return {"error": f"Action failed: {e}", "state_changes": capture.changes}

    return {
        "success": True,
        "action": action,
        **({"dispatched_as": effective} if effective != action else {}),
        **({"toggle": toggle_note} if toggle_note else {}),
        **({"dry_run": True} if dry_run else {}),
        "element_id": element_id,
        # What the interaction actually DID. A device.command writes no state
        # key directly -- it goes out the wire and comes back on a poll or a
        # push -- so an empty capture is the expected result for a command
        # that fired, and the dispatched records are what tell it from a
        # control with nothing behind it.
        "dispatched": dispatched or [],
        "state_changes": capture.changes,
        **_nothing_ran_note(engine, element_id, action, dispatched),
    }


async def _navigate(engine: Any, page_id: Any, dry_run: bool) -> dict:
    """Every connected panel moved to a page, the way the runtime moves them:
    the page event emitted AND ``ui.navigate`` broadcast, since a panel
    switches page only on the broadcast."""
    if not page_id:
        return {"error": "page_id is required for navigate action"}
    if dry_run:
        return {
            "success": True, "action": "navigate", "page_id": page_id,
            "dry_run": True, "state_changes": [],
            "note": f"Would move every connected panel to '{page_id}'.",
        }
    await engine.events.emit(f"ui.page.{page_id}")
    await engine.broadcast_ws({"type": "ui.navigate", "page_id": page_id})
    return {"success": True, "action": "navigate", "page_id": page_id, "state_changes": []}


def _resolved(engine: Any, state: Any, action: str, element_id: str) -> tuple[str, str | None]:
    """The event a real touch would send, and why, when the panel would choose.

    A press on a toggle is resolved BY THE PANEL, which compares the bound key
    against toggle_value and sends press or toggle_off; firing "press" here
    regardless reported the on branch twice for a working toggle.
    """
    if action == "press":
        element = find_element(getattr(engine, "project", None), element_id)
        if element is not None:
            return resolve_press(element, state)
    return action, None


def _event_args(action: str, effective: str, element_id: str, value: Any) -> tuple | None:
    """What ``handle_ui_event`` is called with for this interaction, or None
    for one no panel sends."""
    if action in _TOUCHES:
        return (effective, element_id)
    if action in _VALUE_EVENTS:
        return (action, element_id, {"value": value})
    return None


class _Capture:
    """The state changes heard while an interaction runs, as the cloud may see them."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self.changes: list[dict] = []

    def __enter__(self) -> "_Capture":
        self._sub = self._state.subscribe("*", self._heard)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._state.unsubscribe(self._sub)

    def _heard(self, key: str, old_val: Any, new_val: Any, source: Any) -> None:
        if source in _IGNORED_SOURCES or is_cloud_excluded_key(key):
            return
        self.changes.append({
            "key": key,
            "old_value": cloud_state_value(old_val),
            "new_value": cloud_state_value(new_val),
        })


def _nothing_ran_note(engine: Any, element_id: str, action: str, dispatched: Any) -> dict:
    """Say why an interaction did nothing, when it did nothing.

    ``{"success": true, "state_changes": []}`` is what a working device command
    looks like AND what a button with no binding at all looks like. The
    dispatched records separate those two; the silence that is left is worth a
    sentence, about the element the event actually reached.
    """
    if dispatched:
        return {}
    element = find_element(getattr(engine, "project", None), element_id)
    if element is None:
        return {
            "note": f"No element '{element_id}' is on any page or in the master "
                    f"elements, so nothing ran."
        }
    bindings = element.bindings if isinstance(element.bindings, dict) else {}
    do_map = bindings.get("do") if isinstance(bindings.get("do"), dict) else {}
    if not do_map.get(action):
        slots = sorted(slot for slot, actions in do_map.items() if actions)
        has = f" It has: {', '.join(slots)}." if slots else " It has no do bindings at all."
        return {"note": f"'{element_id}' has nothing bound to {action}.{has}"}
    return {"note": f"'{element_id}' has a do.{action} binding, but no action in it ran."}
