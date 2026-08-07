"""
Panel UI-event runtime — turns a touch-panel interaction into system effects.

A peer of the macro engine: where MacroEngine runs an authored step list,
this runs the action list a UI element's ``do.<interaction>`` binding
declares, plus the two-way ``show.value.write_back`` link that lets a
control drive the state key it reflects.

Owned by the engine (``engine.ui_events``), which forwards every panel
event through ``Engine.handle_ui_event``. Reads the live project through
the engine because a reload replaces it wholesale.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from server.core.state_store import coerce_flat_primitive
from server.core.value_resolver import resolve_ref
from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.engine import Engine

log = get_logger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Log an exception from a fire-and-forget task instead of swallowing it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"Background task failed: {exc}", exc_info=exc)


class UIEventRuntime:
    """Executes the bindings behind panel interactions."""

    def __init__(self, engine: Engine):
        self._engine = engine

    async def handle(
        self, event_type: str, element_id: str, data: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Handle a UI event from a connected panel.

        Looks up the element's bindings and dispatches the appropriate action.

        Returns what it dispatched, one record per action, in the order they
        ran. The panel ignores it -- a touch has nowhere to put a receipt. It
        exists for ``simulate_ui_action``, which had no way to tell a button
        that fired from a button that did nothing: a ``device.command`` writes
        no state key directly (it goes out the wire and comes back on a poll or
        a push), so watching the state store reported "success, no changes" for
        a working control, and the caller reasonably believed it.
        """
        engine = self._engine
        data = data or {}
        dispatched: list[dict[str, Any]] = []

        # Emit the raw UI event
        event_name = f"ui.{event_type}.{element_id}"
        await engine.events.emit(event_name, {"element_id": element_id, **data})

        # Find the element and its bindings
        element = self._find_element(element_id)
        if not element:
            return dispatched

        bindings = element.bindings
        show = bindings.get("show") if isinstance(bindings.get("show"), dict) else {}
        do = bindings.get("do") if isinstance(bindings.get("do"), dict) else {}

        # Two-way LINK: a control whose value is bound with write_back drives the
        # state key it reflects. Only writable keys round-trip this way; a
        # device.* value is read-only and must be driven by a do.<interaction>
        # device.command with $value, never written to the state mirror directly
        # (a state.set to device.* no-ops, overwritten on the next poll). The
        # value source for both a slider/select/text_input ("change") and a list
        # row ("select") is show.value; the device guard here is defensive
        # against a hand-edited / AI-authored write_back on a device key.
        value_binding = show.get("value") if isinstance(show.get("value"), dict) else None
        if value_binding and value_binding.get("write_back"):
            link_key = value_binding.get("key", "")
            if link_key and not link_key.startswith("device."):
                # change → scale the display value to the element's output range;
                # select → write the tapped item's value as-is (a list has no
                # output range). Value is already a flat primitive (validated at
                # the WS boundary). The panel reads this same key to reflect the
                # control, so the write closes the two-way loop and lets
                # bindings/triggers/macros react to it.
                if event_type == "change":
                    engine.state.set(
                        link_key,
                        self.scale_value_forward(element, data.get("value")),
                        source="ui",
                    )
                elif event_type == "select":
                    engine.state.set(link_key, data.get("value"), source="ui")

        # Look up the action list for this interaction (always a list of actions)
        binding = do.get(event_type)

        # Toggle off: look for off_action inside the first press action that has one
        if not binding and event_type == "toggle_off":
            press_actions = do.get("press")
            if isinstance(press_actions, dict) and "off_action" in press_actions:
                binding = [press_actions["off_action"]]
            elif isinstance(press_actions, list):
                for act in press_actions:
                    if isinstance(act, dict) and "off_action" in act:
                        binding = [act["off_action"]]
                        break

        # Hold: look for hold_action inside the first press action that has one
        if not binding and event_type == "hold":
            press_actions = do.get("press")
            if isinstance(press_actions, dict) and "hold_action" in press_actions:
                binding = [press_actions["hold_action"]]
            elif isinstance(press_actions, list):
                for act in press_actions:
                    if isinstance(act, dict) and "hold_action" in act:
                        binding = [act["hold_action"]]
                        break

        if not binding:
            return dispatched

        # Binding is a list of actions — execute sequentially
        if not isinstance(binding, list):
            binding = [binding]
        for action_item in binding:
            if isinstance(action_item, dict):
                await self.execute_action(action_item, data, element, dispatched)
        return dispatched

    async def execute_action(
        self, action_def: dict[str, Any], data: dict[str, Any],
        element: Any = None, dispatched: list[dict[str, Any]] | None = None,
    ) -> None:
        """Execute a single UI binding action.

        ``dispatched`` collects a record per action for a caller that needs to
        know what happened rather than only what changed. Optional because the
        panel does not want one, and None here must stay as cheap as it looks.
        """
        engine = self._engine
        action = action_def.get("action", "")

        def record(**fields: Any) -> None:
            if dispatched is not None:
                dispatched.append({"action": action, **fields})

        # The UI-event tokens a binding can reference. Built once so the
        # device.command and state.set branches resolve them identically: $value
        # is scaled to the element's output range; $input/$output come from
        # matrix route bindings; $mute comes from mute_route / audio_mute_route
        # bindings. Always all four keys so they resolve from the event, never
        # from the state store. Any other $var/$device/$system ref falls through
        # to the state store (the same shared resolver the macro engine uses).
        event_ctx = {
            "value": self.scale_value_forward(element, data.get("value")),
            "input": data.get("input"),
            "output": data.get("output"),
            "mute": data.get("mute"),
        }

        if action == "value_map":
            # Per-option action map (used by select elements).
            element_value = str(data.get("value", ""))
            action_map = action_def.get("map", {})
            mapped_action = action_map.get(element_value)
            # A value that matches no entry is the quiet failure this whole
            # binding shape invites, so it is recorded either way.
            record(value=element_value, matched=bool(mapped_action))
            if mapped_action:
                await self.execute_action(mapped_action, data, element, dispatched)

        elif action == "macro":
            macro_id = action_def.get("macro", "")
            if macro_id:
                # Run macro in background so UI doesn't block
                task = asyncio.create_task(engine.macros.execute(macro_id))
                task.add_done_callback(_log_task_exception)
                record(macro=macro_id, started=True)

        elif action == "device.command":
            device_id = action_def.get("device", "")
            command = action_def.get("command", "")
            params = dict(action_def.get("params", {}))
            # Resolve $-references in each param: the UI-event tokens above
            # ($value scaled, $input/$output/$mute), then any $var/$device/
            # $system ref from the state store.
            for k, v in params.items():
                params[k] = resolve_ref(v, state=engine.state, event_ctx=event_ctx)
            try:
                await engine.devices.send_command(device_id, command, params)
                record(device=device_id, command=command, params=params, sent=True)
            except Exception as exc:  # Catch-all: driver send_command may raise arbitrary errors
                log.exception(f"Binding command failed: {device_id}.{command}")
                record(
                    device=device_id, command=command, params=params,
                    sent=False, error=str(exc),
                )

        elif action == "state.set":
            key = action_def.get("key", "")
            # Support "value_from": "element" to use the element's current value
            if action_def.get("value_from") == "element":
                value = data.get("value")
            else:
                # Resolve a $-reference in the literal value, with the same
                # event context as device.command — so $value works in a
                # state.set value and $var/$device/$system refs resolve like the
                # macro state.set, not pass through as a literal "$..." string.
                value = resolve_ref(
                    action_def.get("value"), state=engine.state, event_ctx=event_ctx
                )
            # A hand-edited / AI-authored binding may carry a nested literal;
            # keep the store's flat-primitive invariant.
            value, coerced = coerce_flat_primitive(value)
            if coerced:
                log.warning(
                    "state.set binding for key '%s' had a non-primitive value; "
                    "coerced to a JSON string", key,
                )
            engine.state.set(key, value, source="ui")
            record(key=key, value=value)

        elif action == "ui.navigate":
            # Page navigation — broadcast to all panels so they can switch.
            # One spelling on purpose: the macro step, the WS frame below and
            # this binding action are all "ui.navigate", so the same move is
            # written the same way wherever you author it. A control surface's
            # deck-page action is a different thing living in plugin config —
            # it moves the deck's own pages by index, never a panel page — and
            # keeps its own "navigate" name.
            page_id = action_def.get("page", "")
            if page_id:
                await engine.events.emit(f"ui.page.{page_id}")
                await engine.broadcast_ws({
                    "type": "ui.navigate",
                    "page_id": page_id,
                })
                record(page=page_id)

        elif action == "script.call":
            func_name = action_def.get("function", "")
            if func_name:
                await engine.events.emit(f"script.call.{func_name}", data)
                record(function=func_name)

    @staticmethod
    def scale_value_forward(element: Any, raw_value: Any) -> Any:
        """Scale a display value to a device value using output_min/output_max."""
        if raw_value is None or element is None:
            return raw_value
        output_min = getattr(element, "output_min", None)
        output_max = getattr(element, "output_max", None)
        if output_min is None or output_max is None:
            return raw_value

        val = float(raw_value)
        if getattr(element, "scale_to_full", None) is False:
            result = max(output_min, min(output_max, val))
        else:
            display_min = getattr(element, "min", None)
            display_max = getattr(element, "max", None)
            if display_min is None or display_max is None:
                return raw_value
            display_range = display_max - display_min
            if display_range == 0:
                return output_min
            frac = (val - display_min) / display_range
            result = output_min + frac * (output_max - output_min)

        # Kill floating-point noise from the division so an identity/whole-number
        # scale returns 26.0, not 25.9999996. Then, if the control steps in whole
        # numbers over a whole-number output range and the result is whole, hand
        # back an int — so an untyped command param renders "26", not "26.0".
        # (A param declared type: integer coerces regardless, but this keeps the
        # value clean for drivers that declare nothing.)
        result = round(result, 9)
        step = getattr(element, "step", None)
        whole_control = (
            (step is None or (isinstance(step, (int, float)) and float(step).is_integer()))
            and float(output_min).is_integer()
            and float(output_max).is_integer()
        )
        if whole_control and result == int(result):
            return int(result)
        return result

    def _find_element(self, element_id: str) -> Any | None:
        """Find a UI element by ID across all pages."""
        project = self._engine.project
        if not project:
            return None
        for page in project.ui.pages:
            for element in page.elements:
                if element.id == element_id:
                    return element
        return None
