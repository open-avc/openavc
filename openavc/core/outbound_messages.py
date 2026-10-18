"""Recipient projections for the live control WebSocket.

An internal event is not a public payload. This registry selects fields before
serialization, and the immutable envelope retains the recipient and authority
needed at the eventual send. Queued and immediate send adapters must call
``for_send`` after their last wait and before writing to the socket.

Room state, effective UI and deliberately authored custom events are public
interfaces. Their values are not secret-detected. Diagnostic data and unknown
internal message types never acquire room visibility from a producer hint.
ISC and the simulator keep their separately authenticated protocols.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import math
import time
from types import MappingProxyType
from typing import Any

from openavc.utils.logger import get_logger

log = get_logger(__name__)


class InvalidMessage(ValueError):
    """An internal producer supplied an unsupported payload shape."""


@dataclass(frozen=True)
class RoomText:
    """Guidance explicitly constructed for a room by a core producer.

    Raw exception/device/protocol text must not be wrapped. This is a provenance
    distinction at the producer, not a string sanitizer or an extension sandbox.
    """

    text: str


class MessageScope:
    """An opaque server-owned subscription scope; a public ID is not a grant."""


def _no_scope(_scope: MessageScope) -> bool:
    return False


@dataclass(frozen=True)
class Recipient:
    """Server-established audience and the existing admission's live checks."""

    connection: object
    client_type: str
    current: Callable[[], bool]
    has_scope: Callable[[MessageScope], bool] = _no_scope

    @property
    def programmer(self) -> bool:
        # An authenticated preview is still a renderer, not an admin feed.
        return self.client_type == "programmer"


@dataclass(frozen=True)
class Envelope:
    """Frozen recipient bytes. Revalidate after queue/lock waits, before send."""

    _wire: str
    _original: Recipient
    _programmer: bool
    _scope: MessageScope | None

    def for_send(self, recipient: Recipient) -> str | None:
        if recipient.connection is not self._original.connection:
            return None
        if self._programmer and not recipient.programmer:
            return None
        try:
            for admission in (self._original,) if recipient is self._original else (self._original, recipient):
                if admission.current() is not True:
                    return None
                if self._scope is not None and admission.has_scope(self._scope) is not True:
                    return None
        except Exception:
            # A broken verifier is not authority. Never log its exception text.
            return None
        return self._wire


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidMessage("string required")
    return value


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise InvalidMessage("boolean required")
    return value


def _number(value: Any) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise InvalidMessage("finite number required")
    return value


def _primitive(value: Any) -> Any:
    if value is None or type(value) in (str, bool):
        return value
    return _number(value)


def _json_value(value: Any) -> Any:
    # Conditions may compare structured custom-event values. The final strict
    # JSON encoder validates their nested values and freezes the snapshot.
    if type(value) in (dict, list):
        return value
    return _primitive(value)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise InvalidMessage("string list required")
    return [_text(item) for item in value]


def _path(value: Any) -> list[str | int]:
    if not isinstance(value, (list, tuple)):
        raise InvalidMessage("step path required")
    if any(type(item) not in (str, int) for item in value):
        raise InvalidMessage("invalid step segment")
    return list(value)


def _object(value: Any) -> dict:
    if not isinstance(value, dict):
        raise InvalidMessage("object required")
    return value


def _list(value: Any) -> list:
    if not isinstance(value, list):
        raise InvalidMessage("list required")
    return value


def _state(value: Any) -> dict[str, Any]:
    return {_text(key): _primitive(item) for key, item in _object(value).items()}


def _ui(value: Any) -> dict:
    # Effective UI is produced by Engine.panel_ui; retain authored controls and
    # bindings verbatim. Refuse a stored definition that bypassed that owner.
    ui = _object(value)
    if "lock_code" in _object(ui.get("settings", {})):
        raise InvalidMessage("effective UI required")
    return ui


Validator = Callable[[Any], Any]


def _fields(data: Mapping, schema: Mapping[str, Validator]) -> dict:
    return {name: validate(data[name]) for name, validate in schema.items() if name in data}


@dataclass(frozen=True)
class Rule:
    room: Mapping[str, Validator] | None
    protected: Mapping[str, Validator]
    required: tuple[str, ...] = ()
    targeted: bool = False
    scoped: bool = False
    guidance: str | None = None


def _rule(room=None, protected=None, *, required=(), targeted=False, scoped=False, guidance=None):
    return Rule(
        None if room is None else MappingProxyType(room),
        MappingProxyType(protected or {}), tuple(required), targeted, scoped, guidance,
    )


_MACRO_ROOM = {
    "macro_id": _text, "macro_name": _text, "run_id": _text,
    "step_index": _number, "total_steps": _number, "step_path": _path,
    "action": _text, "description": _text, "status": _text,
    "device": _text, "group": _text, "command": _text,
    "call_chain": _strings, "outcome": _text, "failed_steps": _number,
}
_MACRO_DETAIL = {
    "error": _text, "message": _text, "reason": _text,
    "condition_result": _bool, "branch": _text, "condition_key": _text,
    "condition_operator": _text, "condition_value": _json_value,
    "actual_value": _json_value, "timeout": _number, "waited_seconds": _number,
    "on_timeout": _text, "device_results": _list,
}
_TRIGGER = {
    "trigger_id": _text, "macro_id": _text, "macro_name": _text,
    "trigger_type": _text, "reason": _text, "outcome": _text, "error": _text,
    "delay_ms": _number, "delay": _number, "debounce_ms": _number,
    "pending_until": _number, "queued_at": _number,
    "wait_seconds": _number, "queue_position": _number,
}
_LOG = {
    "timestamp": _number, "level": _text, "source": _text,
    "category": _text, "message": _text, "device": _text,
}

# No fallback rule. New message names require an intentional audience and shape.
_registry = {
    "ping": _rule({}),
    "state.snapshot": _rule({"state": _state}, required=("state",)),
    "state.update": _rule({"changes": _state}, required=("changes",)),
    "state.delete": _rule({"keys": _strings}, required=("keys",)),
    "ui.definition": _rule({"ui": _ui}, required=("ui",)),
    "ui.navigate": _rule({"page_id": _text}, required=("page_id",)),
    "ui.files": _rule({"version": _text}, {"path": _text}, required=("version",)),
    "project.reloaded": _rule({}, {"revision": _text, "origin_client": _text}),
    "project.replaced": _rule({}, {"project_name": _text, "source": _text}),
    "command.ack": _rule(
        {"device_id": _text, "command": _text, "success": _bool},
        {"error": _text}, required=("device_id", "command", "success"),
        targeted=True, guidance="error",
    ),
    "state.set.ack": _rule(
        {"key": _text, "success": _bool}, {"error": _text},
        required=("key", "success"), targeted=True, guidance="error",
    ),
    "macro.execute.ack": _rule({"macro_id": _text}, required=("macro_id",), targeted=True),
    "macro.cancel.ack": _rule(
        {"macro_id": _text, "cancelled": _bool}, required=("macro_id",), targeted=True,
    ),
    "event.subscribed": _rule({"patterns": _strings}, required=("patterns",), targeted=True),
    "event.emit.ack": _rule({"event": _text}, required=("event",), targeted=True),
    "error": _rule(
        {"source_type": _text, "element_id": _text, "code": _text},
        {"message": _text}, targeted=True, guidance="message",
    ),
    "script.error": _rule(protected={
        "script_id": _text, "script": _text, "error": _text, "traceback": _text,
        "message": _text, "event": _text, "function": _text, "handler": _text, "line": _number,
    }),
    "drivers.changed": _rule(protected={"driver_id": _text}),
    "panel.devices.changed": _rule(protected={}),
    "log.entry": _rule(protected=_LOG, targeted=True),
    "log.history": _rule(protected={"entries": _list}, targeted=True),
    "audit.state": _rule(
        protected={"session_id": _text, "seq": _number, "state": _object},
        required=("session_id", "state"), targeted=True, scoped=True,
    ),
    "discovery.phase": _rule(protected={
        "phase": _text, "phase_number": _number, "total_phases": _number,
        "message": _text, "progress": _number, "warnings": _strings,
    }),
    "discovery.update": _rule(protected={
        "device": _object, "phase": _text, "progress": _number,
    }),
    "discovery.complete": _rule(protected={
        "scan_id": _text, "status": _text, "total_devices": _number,
        "duration_seconds": _number, "warnings": _strings,
    }),
}
for _name in ("started", "progress", "completed", "cancelled", "error", "step_error", "skipped"):
    _registry[f"macro.{_name}"] = _rule(
        _MACRO_ROOM, _MACRO_DETAIL, required=("macro_id",),
        guidance="message" if _name in ("error", "step_error") else None,
    )
for _name in ("fired", "skipped", "pending", "queued", "completed"):
    _registry[f"trigger.{_name}"] = _rule(protected=_TRIGGER, required=("trigger_id",))
for _name in ("started", "stopped", "error", "missing"):
    _registry[f"plugin.{_name}"] = _rule(
        protected={"plugin_id": _text, "error": _text, "message": _text},
        required=("plugin_id",),
    )
for _name, _field in (("commands", "commands"), ("listen", "listen"),
                      ("outage", "outage"), ("settings", "settings"),
                      ("progress", "activity"), ("timeline", "entry")):
    _registry[f"audit.{_name}"] = _rule(
        protected={"session_id": _text, "seq": _number, "run": _number, _field: _object},
        required=("session_id", _field), targeted=True, scoped=True,
    )
_registry["audit.traffic"] = _rule(
    protected={"session_id": _text, "seq": _number, "run": _number,
               "entries": _list, "skipped": _number},
    required=("session_id", "entries"), targeted=True, scoped=True,
)
REGISTRY: Mapping[str, Rule] = MappingProxyType(_registry)
del _registry, _name, _field

# Event-only producers share the projections above wherever their contract is
# also sent directly. A namespace prefix alone does not grant room visibility.
_EVENT_EXACT = MappingProxyType({
    "system.started": _rule({}),
    "system.stopping": _rule({}),
    "system.project.reloaded": _rule({}),
    "device.orphaned": _rule(protected={"device_id": _text, "driver": _text}),
    "device.pending_settings_applied": _rule(protected={
        "device_id": _text, "applied": _object, "remaining": _object,
    }),
})
_EVENT_SUFFIX = MappingProxyType({
    "device.connected": _rule({"device_id": _text}),
    "device.disconnected": _rule({"device_id": _text}),
    "device.error": _rule({"device_id": _text}, {"error": _text, "source": _text}),
    **{f"ui.{name}": _rule({
        "element_id": _text, "value": _primitive, "input": _primitive,
        "output": _primitive, "audio": _bool,
    }) for name in ("press", "release", "hold", "change", "submit", "select",
                   "toggle_off", "route", "page")},
})


class OutboundPolicy:
    """Closed projections and bounded, payload-free refusal diagnostics."""

    def __init__(self) -> None:
        self.refused = {"unknown": 0, "shape": 0}
        self._next_log = 0.0

    def _reject(self, reason: str) -> None:
        self.refused[reason] += 1
        now = time.monotonic()
        if now >= self._next_log:
            log.warning("Outbound message refused (%s); unknown=%d shape=%d",
                        reason, self.refused["unknown"], self.refused["shape"])
            self._next_log = now + 60

    def project(
        self, message: dict, recipient: Recipient, *, targeted: bool = False,
        scope: MessageScope | None = None,
    ) -> Envelope | None:
        """Project now; preserve authority for a second check at actual send."""
        try:
            if recipient.client_type not in ("panel", "preview", "programmer"):
                return None
            kind = _text(message["type"])
            if kind == "event":
                if not targeted:
                    return None
                payload = self._event(message, recipient, scope)
                if payload is None:
                    return None
                return self._envelope(kind, payload, recipient, scope)
            rule = REGISTRY.get(kind)
            if rule is None:
                self._reject("unknown")
                return None
            payload = self._values(kind, message, rule, recipient, targeted, scope)
            if payload is None:
                return None
            return self._envelope(kind, payload, recipient, scope if rule.scoped else None)
        except (InvalidMessage, KeyError, TypeError, ValueError, RecursionError, OverflowError):
            self._reject("shape")
            return None

    @staticmethod
    def _envelope(kind, payload, recipient, scope):
        # Serialization makes nested authored/diagnostic values immutable.
        # No default=str: an arbitrary object's repr is not a wire schema.
        wire = json.dumps({"type": kind, **payload}, allow_nan=False)
        envelope = Envelope(wire, recipient, recipient.programmer, scope)
        return envelope if envelope.for_send(recipient) is not None else None

    @staticmethod
    def _values(kind, message, rule, recipient, targeted, scope):
        if rule.targeted and not targeted:
            return None
        if rule.scoped and not isinstance(scope, MessageScope):
            return None
        if rule.room is None and not recipient.programmer:
            return None
        if any(key not in message for key in rule.required):
            raise InvalidMessage("required field absent")
        payload = _fields(message, rule.room or {})
        if recipient.programmer:
            payload.update(_fields(message, rule.protected))
        if rule.guidance is not None:
            guidance = message.get("room_guidance")
            failed = kind == "error" or kind.startswith("macro.") or message.get("success") is False
            if failed and not recipient.programmer:
                payload[rule.guidance] = (
                    _text(guidance.text) if isinstance(guidance, RoomText)
                    else "The action could not be completed. Contact support."
                )
        return payload

    def _event(self, message, recipient, scope):
        event = _text(message["event"])
        data = _object(message["payload"])
        if event.startswith("state."):
            # State has its own coalesced delivery path.
            return None
        if event.startswith("custom.") and event[7:].strip():
            projected = data  # Deliberately authored public event contract.
        else:
            rule = _EVENT_EXACT.get(event) or REGISTRY.get(event)
            kind = event
            if rule is None:
                parts = event.split(".", 2)
                if len(parts) == 3 and parts[2]:
                    kind = ".".join(parts[:2])
                    rule = _EVENT_SUFFIX.get(kind)
                    if parts[0] == "macro":
                        rule = REGISTRY.get(kind)
            if rule is None:
                self._reject("unknown")
                return None
            projected = self._values(kind, data, rule, recipient, True, scope)
            if projected is None:
                return None
        result = {"event": event, "payload": projected}
        if "timestamp" in message:
            result["timestamp"] = _number(message["timestamp"])
        return result
