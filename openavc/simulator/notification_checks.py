"""Whether a ``simulator.notifications:`` message reaches the driver and is
read: the simulator validator's ``notifications`` check.

A ``notifications:`` entry maps a state key to the message the simulator pushes
when that key changes: one template for any value, or one per value. This reads
the section's shape, each key and value key, and renders each template with the
simulator's own renderer (``YAMLAutoSimulator._render_notification``, imported
when a template is rendered) at a sample value, then asks the driver's response
rules whether they read the message as pushed data
(``reply_reading.ReplyRules.reads_pushed``). A template the renderer leaves a
placeholder in is passed over: its message cannot be known here.

Whether a message can reach the driver at all is read from the declarations:
the push types listed here, or a transport whose simulator pushes on its control
link. The simulator keeps no list of type names (each push type has its own
set-up in ``push_channels``, which sends only when the block's target
resolves), so a new push type is added there by hand, to
``_PUSH_ON_ANY_TRANSPORT``, and to the "has no effect" warning in
``check_notifications``, which names the types in words.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from openavc.drivers.compiled_protocol import state_var_default
from openavc.simulator.driver_sends import effective_config
from openavc.simulator.reply_reading import ReplyRules
from openavc.simulator.validation_result import ValidationResult


def _default_for_type(var_def: dict) -> Any:
    """The value a state variable the initial state leaves out starts at: the
    simulator's own seed, so a template is rendered with a value the
    simulator would produce."""
    return state_var_default(var_def)


def check_notifications(
    result: ValidationResult, sim: dict, driver_def: dict, known_keys: set[str],
) -> None:
    """Validate the notifications: section (unsolicited push messages).

    Catches notification keys that aren't state keys (never fires), boolean
    value keys that can't match (lookup uses lowercase 'true'/'false'),
    {value} templates on boolean variables (emit Python's 'True'/'False'),
    and templates whose output no driver response pattern parses.
    """
    notifications = sim.get("notifications")
    if notifications is None:
        return
    if not isinstance(notifications, dict):
        result.error("notifications", "simulator.notifications must be a mapping")
        return
    if not notifications:
        return

    transport = driver_def.get("transport", "tcp")
    if not _notifications_reach_driver(driver_def, transport):
        result.warning(
            "notifications",
            f"notifications: has no effect for transport '{transport}' — only "
            f"TCP, serial and UDP simulators push notification messages on "
            f"the control link (unless the driver declares a multicast, SSE, "
            f"TCP dial-back, or HTTP-listener push: block)"
        )

    check = _NotificationCheck(
        result=result,
        state_vars=driver_def.get("state_variables", {}),
        sim_initial=sim.get("initial_state", {}),
        known_keys=known_keys,
        rules=ReplyRules.of(
            driver_def.get("responses", []), effective_config(driver_def), driver_def
        ),
    )
    for key, value_map in notifications.items():
        check.entry(key, value_map)


# The push channels the simulator sends a notification on whatever the control
# transport (push_channels._PUSH_CHANNELS); sse is not among them, because its
# stream rides the control session and so needs HTTP.
_PUSH_ON_ANY_TRANSPORT = ("multicast", "tcp_listener", "http_listener", "udp_listener")

# Transports whose simulator pushes on the control link itself: TCP and serial
# to every connected client, a UDP device to the last peer it heard from. HTTP
# and OSC have no control-link push.
_CONTROL_LINK_PUSH = ("tcp", "serial", "udp")


def _push_carries_notifications(driver_def: dict, transport: Any) -> bool:
    """Whether the driver declares a push channel the simulator emits its
    notifications on instead of the control connection."""
    push_def = driver_def.get("push")
    if not isinstance(push_def, dict):
        return False
    kind = push_def.get("type")
    return kind in _PUSH_ON_ANY_TRANSPORT or (kind == "sse" and transport == "http")


def _notifications_reach_driver(driver_def: dict, transport: Any) -> bool:
    """Whether a ``notifications:`` message can reach the driver at all: on a
    declared push channel, or on a transport that pushes on its control link."""
    return _push_carries_notifications(driver_def, transport) or (
        transport in _CONTROL_LINK_PUSH
    )


def _is_boolean_word(value: Any) -> bool:
    """The lookup's 'true' / 'false', which a boolean renders as the runtime's bool."""
    return isinstance(value, str) and value in ("true", "false")


# A placeholder the renderer left in a message, which cannot be validated
# statically. It follows the placeholders the simulator's renderer
# (push_channels._render_notification) fills and leaves as written, so a new
# placeholder form there is one here too. A brace followed by a quote is JSON,
# not a placeholder, so a rendered JSON notification is still read below.
_UNRESOLVED_PLACEHOLDER = re.compile(r"\{[a-zA-Z_][\w.]*(:[^}]*)?\}")


@dataclasses.dataclass
class _NotificationCheck:
    """One ``notifications:`` section's entries: each key and its value map,
    each template's value key, and whether the driver reads the message a
    template renders."""

    result: ValidationResult
    state_vars: dict
    sim_initial: dict
    known_keys: set[str]
    rules: ReplyRules

    def entry(self, key: Any, value_map: Any) -> None:
        """One state key's notification: a template string (for any value)
        or a value -> template mapping."""
        if self.known_keys and key not in self.known_keys:
            self.result.warning(
                "notifications",
                f"notification key '{key}' is not a state variable or "
                f"initial_state key — it only fires if a handler writes that key"
            )

        var_type = self.state_vars.get(key, {}).get("type", "string")

        if isinstance(value_map, str):
            value_map = {"*": value_map}
        elif not isinstance(value_map, dict):
            self.result.error(
                "notifications",
                f"notification '{key}' must be a template string or a "
                f"value → template mapping, got: {value_map!r}"
            )
            return

        for trigger_value, template in value_map.items():
            self._template(key, var_type, str(trigger_value), str(template))

    def _template(self, key: Any, var_type: Any, trigger: str, template: str) -> None:
        """One template, under the value key that selects it (``*`` for any)."""
        self._value_key(key, var_type, trigger)
        if var_type == "boolean" and "{value}" in template:
            self.result.error(
                "notifications",
                f"notification '{key}' template uses {{value}} but '{key}' is "
                f"boolean — Python's str(True) produces 'True' not 'true'. Use "
                f"per-value templates ('true': ..., 'false': ...) instead."
            )
            return
        if self.rules.any:
            self._round_trip(key, var_type, trigger, template)

    def _value_key(self, key: Any, var_type: Any, trigger: str) -> None:
        """A value key the lookup can never select."""
        if var_type == "boolean" and trigger not in ("true", "false", "*"):
            self.result.error(
                "notifications",
                f"notification '{key}' value key {trigger!r} never matches — "
                f"boolean lookups use lowercase 'true'/'false' (quote the keys "
                f"in YAML so they stay strings)"
            )
        if var_type == "enum" and trigger != "*":
            valid = [str(v) for v in self.state_vars[key].get("values", [])]
            if valid and trigger not in valid:
                self.result.warning(
                    "notifications",
                    f"notification '{key}' value key {trigger!r} is not one of "
                    f"the enum values {valid}"
                )

    def _sample_value(self, key: Any, var_type: Any, trigger: str) -> Any:
        """The value a template is rendered with: under ``*``, the key's
        initial state (or the type's starting value); else the value key, a
        boolean's 'true'/'false' as the runtime's bool."""
        if trigger == "*":
            if key in self.sim_initial:
                sample_value = self.sim_initial[key]
            else:
                sample_value = _default_for_type(self.state_vars.get(key, {}))
        else:
            sample_value = trigger
        if var_type == "boolean" and _is_boolean_word(sample_value):
            return sample_value == "true"
        return sample_value

    def _round_trip(self, key: Any, var_type: Any, trigger: str, template: str) -> None:
        """Render the template with the runtime's own renderer, so format specs
        ({value:d} on a boolean) validate the way they will emit, and warn
        when no response rule reads the message."""
        sample_value = self._sample_value(key, var_type, trigger)
        from openavc.simulator.yaml_auto import YAMLAutoSimulator

        message = YAMLAutoSimulator._render_notification(
            template, key, sample_value, state=self.sim_initial
        )
        if _UNRESOLVED_PLACEHOLDER.search(message):
            return
        # A template wrapped in CR/LF (dial-back containers embed the payload
        # as "\r\n<response>\r\n") or ending in the driver's own ">" is
        # read the way the runtime dispatches pushed data.
        if not self.rules.reads_pushed(message):
            self.result.warning(
                "notifications",
                f"notification '{key}' emits {message!r} which matches no "
                f"driver response pattern — the driver will ignore it"
            )
