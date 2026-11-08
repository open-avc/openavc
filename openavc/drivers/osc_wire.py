"""A YAML driver's OSC: the typed args a command sends, and the
address-pattern reply rules.

An OSC command is one with an ``address`` (``/ch/{ch}/mix/fader``) and a list
of typed ``args``; an OSC reply is matched by its address against each rule's
pattern (``fnmatch``: ``/ch/*/mix/fader``), first match wins, and its
mappings read positional args. The rules worth knowing before changing
anything here:

* An arg's type tag is one of ``f i s h d T F N``. A numeric tag whose value
  will not read as a number raises one clear error naming the tag rather
  than a bare ValueError. An unsupported tag is dropped with a warning: the
  loader rejects it, and a message one arg short is worse than a missing one.
* An OSC command never goes out on a socket that is not OSC: a command
  declared with an ``address`` on another transport is refused and logged.
* A ``child_set:`` entry names its child by an address segment or a literal,
  and a rule routing to children throttles per concrete address, since the
  wildcard pattern serves every child.

``OscWire`` is a mixin ``ConfigurableDriver`` inherits, so every name stays
on the driver object. From its host it needs ``device_id``, ``config``,
``transport``, the compiled ``_osc_responses`` table, the message counters
(``_framed_messages``, ``_parsed_messages``, ``_last_unread``),
``_rule_applies``, ``_throttle_hold``, ``_apply_writes``,
``_substitute_for_wire``, ``_observe`` and what ``ReplyValues`` provides.
"""

from __future__ import annotations

import fnmatch
import struct
from typing import Any, Callable

from openavc.core.state_store import is_flat_primitive
from openavc.drivers import compiled_protocol
from openavc.drivers.contract_events import UNMATCHED_RESPONSE
from openavc.drivers.json_replies import JSON_PATH_MISSING, extract_json_path
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")

# Numeric tags: what the value is read as.
_NUMERIC_TAGS: dict[str, Any] = {"f": float, "h": int, "d": float}
# Tags that carry no value of their own.
_CONSTANT_TAGS: dict[str, Any] = {"T": True, "F": False, "N": None}

# An arg a mapping names that the message does not carry.
_NO_ARG = JSON_PATH_MISSING


def osc_num(tag: str, value: str, converter: Any) -> Any:
    """Coerce an OSC arg value to a number, raising a clear error.

    A missing/unresolved value (e.g. ``float("")`` or an unmatched
    ``{placeholder}``) would otherwise surface as a bare ValueError with no
    context. Turn it into an actionable message naming the OSC type tag.
    """
    try:
        return converter(value)
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"OSC arg of type '{tag}' requires a numeric value, got {value!r}"
        ) from e


def _typed_arg(tag: str, resolved: str) -> tuple[str, Any] | None:
    """One ``(tag, value)`` arg, or None for a tag OSC here does not send."""
    if tag in _NUMERIC_TAGS:
        return (tag, osc_num(tag, resolved, _NUMERIC_TAGS[tag]))
    if tag == "i":
        return ("i", int(osc_num("i", resolved, float)))
    if tag == "s":
        return ("s", resolved)
    if tag in _CONSTANT_TAGS:
        return (tag, _CONSTANT_TAGS[tag])
    return None


def build_osc_args(
    arg_defs: list[dict[str, Any]], params: dict[str, Any]
) -> list[tuple[str, Any]]:
    """Build a list of typed OSC args from definition, substituting params."""
    args: list[tuple[str, Any]] = []
    for arg_def in arg_defs:
        tag = arg_def.get("type", "f")
        raw_value = str(arg_def.get("value", ""))
        resolved = (
            compiled_protocol.safe_substitute(raw_value, params)
            if "{" in raw_value else raw_value
        )
        arg = _typed_arg(tag, resolved)
        if arg is None:
            log.warning(
                "OSC arg type %r is not supported (expected one of "
                "f/i/s/h/d/T/F/N); arg dropped",
                tag,
            )
            continue
        args.append(arg)
    return args


def _arg_value(mapping: dict[str, Any], args: list[tuple[str, Any]]) -> Any:
    """The positional arg a flat mapping reads (through its ``json_path``
    when it has one), or ``_NO_ARG``."""
    arg_index = mapping.get("arg", 0)
    if arg_index >= len(args):
        return _NO_ARG
    _, raw_value = args[arg_index]
    json_path = mapping.get("json_path")
    if json_path is not None:
        # The arg is a JSON string (QLab /reply ...): the value at json_path.
        return extract_json_path(raw_value, json_path)
    return raw_value


def _child_wire_id(segments: list[str], id_spec: tuple[str, Any]) -> Any:
    """The wire id a child_set entry names: an address segment or a literal."""
    id_kind, id_val = id_spec
    if id_kind != "segment":
        return id_val
    return segments[id_val] if 0 <= id_val < len(segments) else _NO_ARG


def _unread_args(args: list[tuple[str, Any]]) -> list[Any]:
    return [value if is_flat_primitive(value) else repr(value) for _tag, value in args]


class OscWire:
    """OSC commands and reply rules, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver / ReplyValues; declared here
    # for the methods.
    device_id: str
    config: dict[str, Any]
    transport: Any
    _osc_responses: list[Any]
    _framed_messages: int
    _parsed_messages: int
    _last_unread: str
    _rule_applies: Callable[[Any], bool]
    _throttle_hold: Callable[..., bool]
    _apply_writes: Callable[..., None]
    _substitute_for_wire: Callable[[str, str, dict[str, Any]], str]
    _observe: Callable[..., None]
    _place_child: Callable[..., Any]
    _convert_declared: Callable[[Any, str], Any]
    _mapped_reading: Callable[..., Any]
    _convert_osc_value: Callable[[Any, str], Any]

    _osc_num = staticmethod(osc_num)
    _build_osc_args = staticmethod(build_osc_args)

    def _is_osc_command(self, cmd_def: dict[str, Any]) -> bool:
        """Check if a command definition uses OSC-style fields."""
        return "address" in cmd_def

    async def _send_osc_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any]
    ) -> Any:
        """Send an OSC command: encode address + typed args and send."""
        from openavc.transport.osc import OSCTransport
        from openavc.transport.osc_codec import osc_encode_message

        # Mirrors the HTTP path's isinstance check: no OSC bytes on a
        # non-OSC socket when a command's declared shape doesn't match.
        if not isinstance(self.transport, OSCTransport):
            log.error(
                f"[{self.device_id}] Command '{command}' uses OSC fields "
                f"but transport is not OSC"
            )
            return None

        all_params = {**self.config, **params}
        address = self._substitute_for_wire(
            f"commands.{command}.address", cmd_def.get("address", ""), all_params
        )
        args = self._build_osc_args(cmd_def.get("args", []), all_params)
        data = osc_encode_message(address, args)
        await self.transport.send(data)
        log.debug(f"[{self.device_id}] Sent OSC command '{command}': {address}")
        return True

    async def _handle_osc_response(self, data: bytes) -> None:
        """Decode incoming OSC data and match against address-based responses."""
        from openavc.transport.osc_codec import osc_decode_bundle

        try:
            messages = osc_decode_bundle(data)
        except (ValueError, struct.error) as e:
            log.warning(f"[{self.device_id}] Failed to decode OSC message: {e}")
            return

        for address, args in messages:
            self._framed_messages += 1
            if self._apply_osc_message(address, args):
                continue
            log.debug(f"[{self.device_id}] Unmatched OSC: {address}")
            self._last_unread = address[:80]
            self._observe(
                UNMATCHED_RESPONSE, address=address, args=_unread_args(args),
            )

    def _apply_osc_message(self, address: str, args: list[tuple[str, Any]]) -> bool:
        """Apply the first rule whose pattern matches and whose ``only_when``
        holds. False when none does."""
        for rule in self._osc_responses:
            addr_pattern, condition = rule[0], rule[4]
            if fnmatch.fnmatch(address, addr_pattern) and self._rule_applies(condition):
                self._parsed_messages += 1
                self._apply_osc_rule(rule, address, args)
                return True
        return False

    def _apply_osc_rule(
        self, rule: Any, address: str, args: list[tuple[str, Any]]
    ) -> None:
        addr_pattern, mappings, child_mappings, tstate, _condition = rule
        flat = self._osc_flat_reads(mappings, args)
        children = (
            self._resolve_osc_child_mappings(address, args, child_mappings)
            if child_mappings else []
        )
        # An OSC child_set rule matches a wildcard address pattern
        # (/ch/*/mix/fader), so the concrete address IS the per-child scope.
        if self._throttle_hold(
            tstate, address if child_mappings else "", flat, children,
        ):
            log.debug(
                f"[{self.device_id}] OSC response throttled: {addr_pattern}"
            )
            return
        self._apply_writes(flat, children)
        log.debug(f"[{self.device_id}] OSC matched: {addr_pattern}")

    def _osc_flat_reads(
        self, mappings: list[dict[str, Any]], args: list[tuple[str, Any]]
    ) -> list[tuple[str, Any]]:
        flat: list[tuple[str, Any]] = []
        for mapping in mappings:
            state_key = mapping.get("state")
            if not state_key:
                continue
            raw_value = _arg_value(mapping, args)
            if raw_value is _NO_ARG:
                continue
            flat.append(
                (state_key, self._mapped_reading(raw_value, mapping, self._convert_osc_value))
            )
        return flat

    def _resolve_osc_child_mappings(
        self,
        address: str,
        args: list[tuple[str, Any]],
        child_mappings: list[dict[str, Any]],
    ) -> list[tuple[str, Any, dict[str, Any]]]:
        """Read an address-matched OSC message for child-entity state
        (``child_set:``): one ``(child type, local id, updates)`` per child.
        The child id comes from an address segment or a literal; prop values
        from positional args or literals. The id map, the coercion and the
        unregistered-id guard are the regex path's (``_place_child``)."""
        segments = address.strip("/").split("/")
        writes: list[tuple[str, Any, dict[str, Any]]] = []
        for cm in child_mappings:
            write = self._osc_child_write(segments, args, cm)
            if write is not None:
                writes.append(write)
        return writes

    def _osc_child_write(
        self, segments: list[str], args: list[tuple[str, Any]], cm: dict[str, Any]
    ) -> tuple[str, Any, dict[str, Any]] | None:
        ctype = cm["type"]
        raw_id = _child_wire_id(segments, cm["id"])
        if raw_id is _NO_ARG:
            return None
        local_id = self._place_child(ctype, raw_id, cm, "child_set")
        if local_id is None:
            return None
        updates: dict[str, Any] = {}
        for pm in cm["props"]:
            value = self._osc_prop(pm, args)
            if value is not _NO_ARG:
                updates[pm["prop"]] = value
        return (ctype, local_id, updates) if updates else None

    def _osc_prop(self, pm: dict[str, Any], args: list[tuple[str, Any]]) -> Any:
        """One child prop's value: its literal, else its positional arg."""
        if "value" in pm:
            return self._convert_declared(pm["value"], pm.get("type", "string"))
        arg_index = pm.get("arg", 0)
        if not 0 <= arg_index < len(args):
            return _NO_ARG
        _, raw_value = args[arg_index]
        return self._mapped_reading(raw_value, pm, self._convert_osc_value)
