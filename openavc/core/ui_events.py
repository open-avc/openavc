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

from openavc.core.script_engine import ScriptCallError
from openavc.core.state_store import coerce_flat_primitive
from openavc.core.value_resolver import resolve_ref
from openavc.ui.matrix_model import destination_for
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.engine import Engine

log = get_logger(__name__)


#: Every action name a ``do.<interaction>`` binding can carry.
#:
#: This IS the chain in ``execute_action``, written down -- ``tests/
#: test_ui_events_actions.py`` re-derives it from that chain and fails if the
#: two part company. It is a set rather than a comment because two other things
#: need it: ``ui.page_review`` warns on an action outside it before a panel is
#: ever written, and the ``else`` at the end of the chain says it out loud for
#: the panels nobody reviewed.
#:
#: A name outside this set reaches no branch at all. The panel sends the
#: interaction, the runtime walks the list, and nothing happens -- which is
#: indistinguishable, from the room, from a dead device.
DISPATCHED_ACTIONS = frozenset({
    "device.command", "event.emit", "macro", "script.call", "state.set",
    "ui.navigate", "value_map",
})


def _log_task_exception(task: asyncio.Task) -> None:
    """Log an exception from a fire-and-forget task instead of swallowing it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"Background task failed: {exc}", exc_info=exc)


def press_mode(element: Any) -> tuple[str, dict[str, Any]]:
    """A button's interaction mode, and the action carrying it.

    Mirrors the panel: mode lives on the FIRST action of ``do.press``, and a
    toggle without a ``toggle_key`` has nothing to compare, so it degrades to
    tap rather than doing nothing.
    """
    bindings = getattr(element, "bindings", None)
    do_map = bindings.get("do") if isinstance(bindings, dict) else None
    actions = do_map.get("press") if isinstance(do_map, dict) else None
    first = actions[0] if isinstance(actions, list) and actions else actions
    first = first if isinstance(first, dict) else {}
    mode = first.get("mode") or "tap"
    if mode == "toggle" and not first.get("toggle_key"):
        mode = "tap"
    return mode, first


def resolve_press(element: Any, state: Any) -> tuple[str, str | None]:
    """Which event a real press on this element would send, and why.

    **The panel decides this, not the server.** A touch on a toggle button
    compares the bound key against ``toggle_value`` in the browser and sends
    either ``ui.press`` or ``ui.toggle_off``; the server only ever sees the
    verdict. That is correct for the panel and wrong for anything trying to
    *verify* a panel -- calling ``handle_ui_event("press", ...)`` directly fires
    the on-branch no matter what the device is currently doing, so a toggle
    checked twice reports the same command twice and there is no way to tell a
    broken toggle from a simulation that cannot see one.

    So this is a mirror, and it is a mirror of four lines. The comparison is
    string and case-insensitive, matching panel.js exactly -- that is what lets
    ``toggle_value: true`` match a driver's boolean ``True``, which is how
    nearly every toggle in a real project is written.
    ``tests/test_ui_page_review_mirrors.py`` pins the spelling.

    Returns the event type plus a sentence explaining the choice, or None when
    the element is not a toggle and there was no choice to make.
    """
    mode, action = press_mode(element)
    if mode != "toggle":
        return "press", None
    key = action.get("toggle_key")
    want = action.get("toggle_value")
    current = state.get(key) if state is not None else None
    active = (
        current is not None and want is not None
        and str(current).lower() == str(want).lower()
    )
    verdict = "==" if active else "!="
    why = (
        f"toggle_key '{key}' is {current!r} {verdict} toggle_value {want!r}, so a press "
        f"here {'leaves' if active else 'reaches'} the toggled-on state -- dispatching "
        f"{'off_action' if active else 'the press action'}."
    )
    return ("toggle_off" if active else "press"), why


def _destination_route(element: Any, output: Any) -> list[Any] | None:
    """The action list this matrix destination overrides ``do.route`` with.

    The panel sends the destination's own opaque value rather than a row number,
    so the destination is found by value -- through the same comparison the
    crosspoints light by, because a dropdown reads ``"2"`` out of the DOM where
    the project wrote ``2``.

    None means "no override", which is not the same as an empty list: an
    override authored as ``[]`` is a destination somebody deliberately made
    inert, and falling back to the element default would route it anyway.
    """
    if getattr(element, "type", "") != "matrix":
        return None
    destination = destination_for(getattr(element, "matrix_config", None), output)
    if destination is None:
        return None
    route = destination.get("route")
    return route if isinstance(route, list) else None


class UIEventRuntime:
    """Executes the bindings behind panel interactions."""

    def __init__(self, engine: Engine):
        self._engine = engine
        #: (element id, action) pairs already reported -- see _warn_undispatched.
        self._warned_actions: set[tuple[str, str]] = set()

    def forget_undispatched_actions(self) -> None:
        """Report a bad action again after the project is replaced.

        The dedupe below exists to keep one bad binding out of every log line,
        not to say it once for the life of the process. Somebody who reloads a
        project is trying to fix something, and a second silence reads as
        having fixed it.
        """
        self._warned_actions.clear()

    async def handle(
        self, event_type: str, element_id: str, data: dict[str, Any] | None = None,
        *, dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Handle a UI event from a connected panel.

        Looks up the element's bindings and dispatches the appropriate action.

        Returns what it dispatched, one record per action, in the order they
        ran. It exists for ``simulate_ui_action``, which had no way to tell a
        button that fired from a button that did nothing: a ``device.command``
        writes no state key directly (it goes out the wire and comes back on a
        poll or a push), so watching the state store reported "success, no
        changes" for a working control, and the caller reasonably believed it.

        The WebSocket door reads it too, for the one field a touch does have
        somewhere to put: an ``error`` on a record is a failure this method
        deliberately swallowed to keep the rest of the press running, and the
        panel turns it into a message on the glass.

        ``dry_run`` walks the same resolution and returns the same records
        without any of the effects -- no command on the wire, no state write, no
        macro, no navigation broadcast. It threads through the real path rather
        than re-deriving one, because a preview that resolves differently from
        the thing it previews is worse than no preview. It is off by default:
        the panel is the caller that matters and it always means it.
        """
        engine = self._engine
        data = data or {}
        dispatched: list[dict[str, Any]] = []

        # Emit the raw UI event. A dry run emits nothing: scripts and triggers
        # subscribe to these, so the "preview" would drive the room.
        if not dry_run:
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
        if value_binding and value_binding.get("write_back") and not dry_run:
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

        # A matrix destination may carry its own action list. The element's
        # do.route is the default -- what almost every row does -- and a single
        # row can override it, which is how one control covers a frame's eight
        # outputs plus a "Stream" destination that starts an encoder instead.
        if event_type == "route":
            override = _destination_route(element, data.get("output"))
            if override is not None:
                binding = override

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
                await self.execute_action(
                    action_item, data, element, dispatched, dry_run=dry_run,
                )
        return dispatched

    async def execute_action(
        self, action_def: dict[str, Any], data: dict[str, Any],
        element: Any = None, dispatched: list[dict[str, Any]] | None = None,
        *, dry_run: bool = False,
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
                if dry_run:
                    dispatched[-1]["would_run"] = True

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
                await self.execute_action(
                    mapped_action, data, element, dispatched, dry_run=dry_run,
                )

        elif action == "macro":
            macro_id = action_def.get("macro", "")
            if macro_id:
                if dry_run:
                    record(macro=macro_id, started=False)
                    return
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
            if dry_run:
                # The params are resolved above and reported, because that is
                # the half worth previewing: a $value that resolves to None is
                # the mistake, and it is invisible until the command is sent.
                record(device=device_id, command=command, params=params, sent=False)
                return
            try:
                await engine.devices.send_command(device_id, command, params)
                record(device=device_id, command=command, params=params, sent=True)
            except Exception as exc:  # Catch-all: driver send_command may raise arbitrary errors
                log.exception(f"Binding command failed: {device_id}.{command}")
                record(
                    device=device_id, command=command, params=params,
                    sent=False, error=self._command_error(device_id, exc),
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
            if not dry_run:
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
                if not dry_run:
                    await engine.events.emit(f"ui.page.{page_id}")
                    await engine.broadcast_ws({
                        "type": "ui.navigate",
                        "page_id": page_id,
                    })
                record(page=page_id)

        elif action == "script.call":
            # Calls the function. It used to emit `script.call.<name>` and
            # nothing subscribed it to the function of that name, so a control
            # naming one ran it only if the script had also written
            # `@on_event("script.call.<name>")` -- a spelling no document
            # mentions. The Builder offered every function in every script, so
            # picking one off that list produced a dead button.
            func_name = action_def.get("function", "")
            # Which script, where the name is not unique. Written by the
            # Builder, which knows it; absent on anything hand-authored, and
            # then the name has to stand alone.
            script_id = str(action_def.get("script", "") or "")
            params = dict(action_def.get("params", {}))
            for k, v in params.items():
                params[k] = resolve_ref(v, state=engine.state, event_ctx=event_ctx)
            if func_name:
                if dry_run:
                    # Same half worth previewing as device.command: what the
                    # arguments RESOLVE to. A $value that resolves to None is
                    # the mistake, and it is invisible until the press.
                    record(function=func_name, params=params, called=False)
                    return
                if engine.scripts is None:
                    record(
                        function=func_name, params=params, called=False,
                        error=f"No script function named '{func_name}'.",
                    )
                    return
                try:
                    await engine.scripts.call_function(func_name, params, script_id)
                    record(function=func_name, params=params, called=True)
                except ScriptCallError as exc:
                    # Caught, not raised: the rest of the press still runs, the
                    # same way a device.command that never reached its device
                    # does. The sentence rides the record to the glass.
                    log.warning(f"Binding script call failed: {func_name}: {exc}")
                    record(
                        function=func_name, params=params, called=False,
                        error=str(exc),
                    )

        elif action == "event.emit":
            # The macro step, reachable from a control. The event NAME comes
            # from the project rather than the panel, so this grants a panel
            # exactly the authority running a macro already did -- and it
            # reaches the two things a script call cannot: a trigger, and a
            # plugin.
            event_name = action_def.get("event", "")
            payload = dict(action_def.get("payload") or {})
            for k, v in payload.items():
                payload[k] = resolve_ref(v, state=engine.state, event_ctx=event_ctx)
            if event_name:
                if not dry_run:
                    await engine.events.emit(event_name, payload)
                record(event=event_name, payload=payload)

        else:
            # Everything above is a name this runtime knows. Anything else used
            # to fall off the end of the chain in silence -- a retired spelling,
            # a typo, or a macro step name written where a binding action goes.
            #
            # Recorded as well as logged, because `dispatched` is what
            # simulate_ui_action shows the AI when it verifies its own write,
            # and an empty list is what a control with NO binding returns --
            # the exact ambiguity that tool was built to remove. No `error`
            # field on purpose: the WS door turns one of those into a message
            # on the panel, and a room full of people cannot act on the name of
            # an action.
            record(ran=False)
            self._warn_undispatched(action, element)

    def _warn_undispatched(self, action: str, element: Any) -> None:
        """Say, once, that a binding names something nothing runs.

        The write door (``ui.page_review``) catches these when the AI or the
        Builder authors one. This catches the other population: a hand-edited
        ``.avc``, and a project written against a different version. Neither
        passes a door, and from the room both look like a broken device.

        Deduped per (element, action) rather than per press, because a bad
        action on a slider fires on every tick of a drag and a real signal
        buried in log spam is not a signal. Not deduped per action alone: the
        same typo on two elements is two things to go and fix.
        """
        el_id = str(getattr(element, "id", "") or "?")
        if (el_id, action) in self._warned_actions:
            return
        self._warned_actions.add((el_id, action))
        usable = ", ".join(sorted(DISPATCHED_ACTIONS))
        if not action:
            log.warning(
                f"UI element '{el_id}' has a binding entry with no action, so "
                f"nothing runs when it fires. Valid actions: {usable}"
            )
        else:
            log.warning(
                f"UI element '{el_id}' has the binding action '{action}', which "
                f"nothing dispatches, so nothing runs when it fires. Valid "
                f"actions: {usable}"
            )

    def _command_error(self, device_id: str, exc: Exception) -> str:
        """Why the command did not go out, in words somebody can act on.

        Translated here because here is the last place the exception exists.
        The action list carries on past a failure on purpose -- one dead device
        must not stop the rest of a press -- so what gets written down is all
        anyone downstream will ever have, and ``str(exc)`` is not enough for
        that: a bare ``TimeoutError`` stringifies to the empty string, and a
        panel would put nothing at all on the glass.

        Same translation, with the same device and host context, as the direct
        ``command`` message on the WebSocket, so the same failure reads the
        same way whichever door it came through. The import is deferred to keep
        the module free of API imports at load time, matching how the project
        loader reaches for its save-error twin.
        """
        from openavc.api.error_messages import friendly_error

        state = self._engine.state
        name = state.get(f"device.{device_id}.name") or device_id
        host = state.get(f"device.{device_id}.host") or ""
        return friendly_error(exc, device=str(name), host=str(host))

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
        """Find a UI element by ID, across every page and then the masters.

        Masters were missed here for as long as this existed, which made the
        whole cross-page layer -- nav bars, a home button, a header status LED
        -- silently inert to anything that went through this path, and
        untestable through ``simulate_ui_action``. The panel has always drawn
        and dispatched them; only the server-side lookup did not know they were
        elements too.

        Pages first, matching the panel's own precedence: a page element wins a
        duplicate id, because that is the one it drew last.
        """
        project = self._engine.project
        if not project:
            return None
        for page in project.ui.pages:
            for element in page.elements:
                if element.id == element_id:
                    return element
        for master in getattr(project.ui, "master_elements", None) or []:
            if master.id == element_id:
                return master
        return None
