"""The ``push:`` block's load-time checks (device-initiated notifications).

A push block declares frames arriving on a channel the platform must open,
not the established control connection. A misdeclared block would silently
never deliver a frame (the still-polling failure it exists to fix), so its
shape and addressing are refused at load. Each type has its own keys and its
own checks, run after the type and the keys:

* ``multicast`` joins a group:port;
* ``udp_listener`` opens a local UDP port the device was set to send packets
  to (never 0: a person types this port into the device);
* ``tcp_listener`` opens a local port the device dials back to after a
  registration command tells it where (``{listener_port}``; 0 lets the OS
  pick), with its own frame parser for the pushed frames;
* ``sse`` holds GET path(s) open on the driver's own HTTP session, with an
  optional device session the stream carries;
* ``http_listener`` accepts device POSTs on a platform-assigned callback path
  (no keys of its own: the URL is built at runtime and substitutes into
  commands as ``{push_callback_url}``).

``tcp_listener`` and ``sse`` (the types whose keys include ``register``) also name driver commands to run once the channel
is open (``register:``) and before it closes (``unregister:``). Addressing
values accept ``{config_field}`` templates, so a device whose notification
target is user-configurable resolves them per instance. Every message here
opens with ``push:``; the walk tags them all with the block.

Purity contract, as for ``avcdriver_semantic``: standard library, the sibling
contract modules and the stdlib-only regex-safety helper. The module is
vendored with the rest of the driver contract (openavc-drivers' catalog CI,
openavc-sdk).
"""

from __future__ import annotations

import re
from typing import Any, Callable

from openavc.drivers.declared_names import config_field_names
from openavc.drivers.spec import (
    LENGTH_ENDIANS,
    PUSH_FRAME_PARSER_TYPES,
    PUSH_REGISTER_ENTRY_KEYS,
    PUSH_SESSION_KEYS,
    PUSH_TYPE_KEYS,
    STRUCT_LENGTH_SIZES,
    is_multicast_group,
)
from openavc.utils.regex_safety import regex_safety_error

# The keys an unknown type is measured against, so its other keys are still
# named in the message.
_ANY_TYPE_KEYS = frozenset({
    "type", "group", "port", "path", "idle_timeout",
    "frame_parser", "register", "unregister",
})

# The session keys that must be non-empty text when present.
_SESSION_TEXT_KEYS = ("header", "event", "key", "close_event")

# A struct_frame parser's reserved byte counts.
_STRUCT_RESERVES = ("header_reserve", "mid_reserve", "trailer_reserve")


def push_block_errors(driver_def: dict[str, Any]) -> list[str]:
    """Every error in the driver's ``push:`` block; empty when it has none."""
    block = driver_def.get("push")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["push: must be a mapping"]
    check = _PushCheck(driver_def, block)
    push_type = block.get("type")
    check.type_and_keys(push_type)
    type_check = _TYPE_CHECKS.get(push_type) if isinstance(push_type, str) else None
    if type_check is not None:
        type_check(check)
    if _registers(push_type):
        _register_checks(check)
    return check.found


def _registers(push_type: Any) -> bool:
    """Whether the type runs driver commands when its channel opens and
    closes: the types whose keys in ``spec.PUSH_TYPE_KEYS`` include
    ``register`` (tcp_listener and sse)."""
    return "register" in PUSH_TYPE_KEYS.get(push_type, ())


class _PushCheck:
    """One push block being checked: the definition it belongs to, the config
    fields its templates may name, and what it found so far."""

    def __init__(self, driver_def: dict[str, Any], block: dict[str, Any]) -> None:
        self.driver_def = driver_def
        self.block = block
        # config_derived keys resolve into config at runtime, so a template
        # may name them like any declared field.
        self.config_fields = config_field_names(driver_def)
        self.found: list[str] = []

    def type_and_keys(self, push_type: Any) -> None:
        if push_type not in PUSH_TYPE_KEYS:
            self.found.append(
                "push: missing or unknown 'type' "
                f"(supported: {', '.join(PUSH_TYPE_KEYS)})"
            )
        known = PUSH_TYPE_KEYS.get(push_type, _ANY_TYPE_KEYS)
        unknown = set(self.block) - known
        if unknown:
            self.found.append(
                f"push: unknown key(s): {', '.join(sorted(unknown))} "
                f"(known keys for type {push_type!r}: {', '.join(sorted(known))})"
            )

    def template(self, where: str, value: str) -> None:
        """A ``{config_field}`` template: every token a declared field."""
        fields = re.findall(r"\{(\w+)\}", value)
        if not fields:
            self.found.append(
                f"push: {where} {value!r} has braces but no {{config_field}} token"
            )
        for field in fields:
            if field not in self.config_fields:
                self.found.append(
                    f"push: {where} references config field '{field}' that is not "
                    f"declared in config_schema, default_config, or config_derived"
                )

    def port(self, lowest: int, allowed: str) -> None:
        """The block's ``port``: a number from ``lowest`` up, or a template."""
        port = self.block.get("port")
        if port is None:
            self.found.append("push: missing 'port'")
        elif _is_template(port):
            self.template("port", port)
        elif not _is_port(port, lowest):
            self.found.append(
                f"push: port must be an integer {allowed} or a {{config_field}} template"
            )


def _is_template(value: Any) -> bool:
    return isinstance(value, str) and "{" in value


def _is_port(value: Any, lowest: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and lowest <= value < 65536


def _not_positive_seconds(value: Any) -> bool:
    # Written as the refusal, not as "value > 0": NaN passes, as it always has.
    return isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0


def _not_a_count(value: Any) -> bool:
    return isinstance(value, bool) or not isinstance(value, int) or value < 0


def _blank(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


# --- each type's own checks ------------------------------------------------------------


def _multicast_checks(check: _PushCheck) -> None:
    group = check.block.get("group")
    if group is None:
        check.found.append("push: missing 'group'")
    elif _is_template(group):
        check.template("group", group)
    elif not isinstance(group, str) or not is_multicast_group(group):
        check.found.append(
            f"push: group {group!r} must be an IPv4 multicast address "
            f"(224.0.0.0 - 239.255.255.255) or a {{config_field}} template"
        )
    check.port(1, "1-65535")


def _udp_listener_checks(check: _PushCheck) -> None:
    check.port(1, "1-65535")


def _tcp_listener_checks(check: _PushCheck) -> None:
    check.port(0, "0-65535 (0 = OS-assigned)")
    # The dial-back channel is its own byte stream, so the control transport's
    # framing does not apply to it.
    frame = check.block.get("frame_parser")
    if frame is None:
        return
    if not isinstance(frame, dict):
        check.found.append("push: frame_parser must be a mapping")
        return
    frame_type = frame.get("type")
    if frame_type not in PUSH_FRAME_PARSER_TYPES:
        check.found.append(
            f"push: frame_parser type {frame_type!r} must be struct_frame, "
            f"length_prefix, or fixed_length"
        )
    elif frame_type == "struct_frame":
        check.found.extend(_struct_frame_errors(frame))


def _struct_frame_errors(frame: dict[str, Any]) -> list[str]:
    errors = [
        f"push: frame_parser {key} must be a non-negative integer"
        for key in _STRUCT_RESERVES
        if _not_a_count(frame.get(key, 0))
    ]
    if frame.get("length_size", 2) not in STRUCT_LENGTH_SIZES:
        errors.append("push: frame_parser length_size must be 1, 2, or 4")
    adjust = frame.get("length_adjust", 0)
    if isinstance(adjust, bool) or not isinstance(adjust, int):
        errors.append("push: frame_parser length_adjust must be an integer")
    if frame.get("length_endian", "big") not in LENGTH_ENDIANS:
        errors.append("push: frame_parser length_endian must be 'big' or 'little'")
    return errors


def _sse_checks(check: _PushCheck) -> None:
    # SSE rides the driver's own HTTP session: a streaming mode of the control
    # transport, not a separate listener.
    transport = check.driver_def.get("transport", "")
    if transport and transport != "http":
        check.found.append(
            f"push: type 'sse' requires the http transport, not '{transport}'"
        )
    paths = _sse_paths(check)
    if _not_positive_seconds_if_set(check.block.get("idle_timeout")):
        check.found.append("push: idle_timeout must be a positive number of seconds")
    session = check.block.get("session")
    if session is not None:
        _session_checks(check, session, paths)


def _not_positive_seconds_if_set(value: Any) -> bool:
    return value is not None and _not_positive_seconds(value)


def _sse_paths(check: _PushCheck) -> list[Any] | None:
    """The stream's path(s): one string or a list of them. Returns the list
    read (None when the value is neither), for the session's one-path rule."""
    raw = check.block.get("path")
    paths = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else None
    if raw is None or paths == []:
        check.found.append(
            "push: missing 'path' (an event-stream URL path, or a list of them)"
        )
    elif paths is None:
        check.found.append("push: path must be a string or a list of strings")
    else:
        for path in paths:
            _sse_path_checks(check, path)
    return paths


def _sse_path_checks(check: _PushCheck, path: Any) -> None:
    if _blank(path):
        check.found.append(f"push: path entry {path!r} must be a non-empty string")
    elif "{" in path:
        check.template("path", path)
    elif not path.startswith("/"):
        check.found.append(
            f"push: path {path!r} must start with '/' (a URL path on the device) "
            f"or be a {{config_field}} template"
        )


def _session_checks(check: _PushCheck, session: Any, paths: list[Any] | None) -> None:
    """A device session the stream carries: where the id comes from and which
    event ends it. A misdeclared one would silently never arm the device."""
    if not isinstance(session, dict):
        check.found.append("push: session must be a mapping")
        return
    unknown = set(session) - set(PUSH_SESSION_KEYS)
    if unknown:
        check.found.append(
            f"push: session has unknown key(s): {', '.join(sorted(unknown))} "
            f"(known: {', '.join(PUSH_SESSION_KEYS)})"
        )
    for key in _SESSION_TEXT_KEYS:
        value = session.get(key)
        if value is not None and _blank(value):
            check.found.append(f"push: session {key} must be a non-empty string")
    check.found.extend(_session_source_errors(session))
    check.found.extend(_session_pattern_errors(session.get("pattern")))
    if isinstance(paths, list) and len(paths) > 1:
        check.found.append(
            "push: a session block needs a single event-stream path "
            "({push_session} would be ambiguous across several sessions)"
        )


def _session_source_errors(session: dict[str, Any]) -> list[str]:
    errors = []
    if session.get("key") and not session.get("event"):
        errors.append(
            "push: session key names a JSON key in the opening event's data, so "
            "it needs 'event' (the event type that carries it)"
        )
    if not session.get("header") and not _event_carries_id(session):
        errors.append(
            "push: session needs a source for the session id: 'header' (a "
            "response header), or 'event' + 'key' (a JSON key in the opening "
            "event's data), or both"
        )
    return errors


def _event_carries_id(session: dict[str, Any]) -> Any:
    return session.get("event") and session.get("key")


def _session_pattern_errors(pattern: Any) -> list[str]:
    if pattern is None:
        return []
    if _blank(pattern):
        return ["push: session pattern must be a non-empty regex"]
    redos = regex_safety_error("push: session pattern", pattern)
    return [redos] if redos else []


_TYPE_CHECKS: dict[str, Callable[[_PushCheck], None]] = {
    "multicast": _multicast_checks,
    "udp_listener": _udp_listener_checks,
    "sse": _sse_checks,
    "tcp_listener": _tcp_listener_checks,
}


# --- register / unregister --------------------------------------------------------------


class _Registration:
    """What a ``register:`` / ``unregister:`` entry may name: the driver's
    commands and its child types."""

    def __init__(self, check: _PushCheck) -> None:
        self.check = check
        commands = check.driver_def.get("commands")
        self.commands = commands if isinstance(commands, dict) else {}
        child_types = check.driver_def.get("child_entity_types")
        self.child_types = set(child_types) if isinstance(child_types, dict) else set()

    def command(self, where: str, name: Any) -> None:
        # A typo here would silently never arm the device.
        if _blank(name):
            self.check.found.append(f"push: {where} must be a command name")
        elif name not in self.commands:
            self.check.found.append(
                f"push: {where} command '{name}' is not declared in commands"
            )

    def entry(self, where: str, item: dict[str, Any]) -> None:
        """A register list entry in its dict form: {command, when, each_child}."""
        unknown = set(item) - set(PUSH_REGISTER_ENTRY_KEYS)
        if unknown:
            self.check.found.append(
                f"push: {where} has unknown key(s): {', '.join(sorted(unknown))} "
                f"(known: {', '.join(PUSH_REGISTER_ENTRY_KEYS)})"
            )
        self.command(where, item.get("command"))
        when = item.get("when")
        if when is not None and not self._names_config_field(when):
            self.check.found.append(
                f"push: {where} when {when!r} must name a config field declared in "
                f"config_schema, default_config, or config_derived"
            )
        each = item.get("each_child")
        if each is not None:
            self._each_child(where, item, each)

    def _names_config_field(self, value: Any) -> bool:
        return isinstance(value, str) and value in self.check.config_fields

    def _each_child(self, where: str, item: dict[str, Any], each: Any) -> None:
        if not isinstance(each, str) or each not in self.child_types:
            self.check.found.append(
                f"push: {where} each_child {each!r} is not a declared child entity type"
            )
        elif not self._takes_child(item.get("command"), each):
            self.check.found.append(
                f"push: {where} runs once per '{each}' child, so command "
                f"'{item.get('command')}' needs a child_id parameter of that type"
            )

    def _takes_child(self, command: Any, child_type: str) -> bool:
        definition = self.commands.get(command)
        params = definition.get("params") if isinstance(definition, dict) else None
        return isinstance(params, dict) and any(
            _is_child_param(param, child_type) for param in params.values()
        )


def _is_child_param(param: Any, child_type: str) -> bool:
    return (
        isinstance(param, dict)
        and param.get("type") == "child_id"
        and param.get("child_type") == child_type
    )


def _register_checks(check: _PushCheck) -> None:
    """``register:`` names the command(s) to run once the listener opens or the
    session is named, as a name, a list of names, or {command, when,
    each_child} entries; ``unregister:`` the one to run before it closes."""
    registration = _Registration(check)
    register = check.block.get("register")
    if register is not None:
        _register_entries(check, registration, register)
    unregister = check.block.get("unregister")
    if unregister is not None:
        registration.command("unregister", unregister)


def _register_entries(
    check: _PushCheck, registration: _Registration, register: Any
) -> None:
    listed = isinstance(register, list)
    if listed and not register:
        check.found.append("push: register list must not be empty")
    for j, item in enumerate(register if listed else [register]):
        where = f"register[{j}]" if listed else "register"
        if isinstance(item, dict):
            registration.entry(where, item)
        else:
            registration.command(where, item)
