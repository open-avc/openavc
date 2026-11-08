"""A YAML driver's JSON-body reply rules, and the ``json_path`` walk.

A ``json:`` response rule reads named keys out of a reply body that is one
JSON object, as HTTP and many TCP devices answer. The rules worth knowing
before changing anything here:

* Rules are additive: every rule whose keys the body carries applies, and a
  rule whose keys are absent sets nothing and does not stamp its throttle
  window. A body a json rule read is finished with, unless a rule declares
  ``after_json`` (``TextReplies`` decides that).
* ``require:`` scopes a rule to bodies carrying all of the named keys, so
  endpoints that reuse a field name (a paired peripheral's ``status`` beside
  the main unit's) do not cross-write each other's state.
* A single-element top-level array (``[{...}]``) is its one object: several
  protocols wrap every reply that way. A longer array is ambiguous and falls
  through to the regex rules.
* A path walks dot-separated keys and list indices (``data.0.name``). A path
  that lands on a list or object yields its length, which keeps the state
  store flat and makes an array usable as "anything?" or a count. A body that
  is not JSON, or a path that does not resolve, is ``JSON_PATH_MISSING``, so
  the mapping is skipped rather than written wrong. The regex and OSC rules
  walk a captured JSON string the same way (``json_path:``).

``JsonReplies`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. From its host it needs ``device_id``, the
compiled ``_json_responses`` table, ``_rule_applies``, ``_throttle_hold``,
``_apply_writes``, ``is_child_registered``, ``_observe`` and what
``ReplyValues`` provides.
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.drivers import compiled_protocol
from openavc.drivers.contract_events import CHILD_UNREGISTERED
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")

# A JSON string that does not parse, or a path that does not resolve: distinct
# from a legitimately-extracted None, so the caller skips the mapping instead
# of writing a wrong value.
JSON_PATH_MISSING = object()

# What one json rule did with a body.
_APPLIED = "applied"
_HELD = "held"


def _parsed(raw_value: Any) -> Any:
    if isinstance(raw_value, (dict, list)):
        return raw_value
    try:
        return compiled_protocol.parse_json_reply(raw_value)
    except (ValueError, TypeError):
        return JSON_PATH_MISSING


def _step(node: Any, seg: str) -> Any:
    """One path segment down: a key of an object, an index of a list."""
    if isinstance(node, dict):
        return node[seg] if seg in node else JSON_PATH_MISSING
    if isinstance(node, list):
        try:
            return node[int(seg)]
        except (ValueError, IndexError):
            return JSON_PATH_MISSING
    return JSON_PATH_MISSING


def _segments(path: Any) -> list[str]:
    """A dotted path's keys and indices (empty segments dropped); none for
    an empty path."""
    if not path:
        return []
    return [seg for seg in str(path).split(".") if seg]


def extract_json_path(raw_value: Any, path: Any, collapse: bool = True) -> Any:
    """Parse a JSON string (or take a parsed node) and walk a dotted path.

    Used by response mappings that declare ``json_path`` -- common for OSC
    devices (QLab's ``/reply/...`` carries a single string arg holding JSON
    like ``{"status":"ok","data":"Intro Music"}``; ``json_path: data``
    extracts the useful value) -- and by every json rule's ``key``.

    An empty path returns the whole parsed value. ``collapse=False`` returns
    the node itself (list or object included) for a caller that asks a
    question of it rather than storing it -- a ``contains`` mapping.
    """
    node = _parsed(raw_value)
    for seg in _segments(path):
        if node is JSON_PATH_MISSING:
            break
        node = _step(node, seg)
    if collapse and isinstance(node, (list, dict)):
        return len(node)
    return node


def _holds(node: Any, needle: Any) -> bool:
    """``contains``: membership for a list, a key for an object, a substring
    for a string, equality otherwise."""
    if isinstance(node, list):
        return needle in node or str(needle) in [str(v) for v in node]
    if isinstance(node, (dict, str)):
        return str(needle) in node
    return node == needle or str(node) == str(needle)


def read_json_mapping(obj: Any, mapping: dict[str, Any]) -> Any:
    """Resolve one json mapping against a parsed body: the value at its
    ``key``, or with ``contains`` the answer to "does the value at key hold
    this?". That is how a resource that reports flags as an array
    (``["NoLink", "LowBattery"]``) becomes one boolean per flag. Missing key
    -> ``JSON_PATH_MISSING``."""
    key = mapping.get("key")
    if "contains" not in mapping:
        return extract_json_path(obj, key)
    node = extract_json_path(obj, key, collapse=False)
    if node is JSON_PATH_MISSING:
        return node
    return _holds(node, mapping["contains"])


def json_throttle_scope(child_mappings: list[dict[str, Any]]) -> str:
    """Throttle bucket for a json rule, the regex path's rule read against
    literal ids: the children this rule routes to. A json rule's ids are
    fixed, so the bucket is constant -- but a rule that routes to children
    and one that only writes flat state are then not sharing a window,
    which is what the scope is for."""
    if not child_mappings:
        return ""
    return "|".join(
        f"{cm['type']}:{cm['id'][1]}" for cm in child_mappings
    )


def _reply_object(text: str) -> dict[str, Any] | None:
    """The one JSON object a reply body is, or None."""
    try:
        obj = compiled_protocol.parse_json_reply(text)
    except (ValueError, TypeError):
        return None
    if _is_one_wrapped_object(obj):
        obj = obj[0]
    return obj if isinstance(obj, dict) else None


def _is_one_wrapped_object(obj: Any) -> bool:
    return isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict)


def _carries(obj: dict[str, Any], require: Any) -> bool:
    """Whether a body carries every key a rule's ``require:`` names."""
    return not require or all(
        extract_json_path(obj, key) is not JSON_PATH_MISSING for key in require
    )


def _flat_reads(
    obj: dict[str, Any], mappings: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], Any]]:
    """Each flat mapping whose key the body carries, with what it read."""
    resolved: list[tuple[dict[str, Any], Any]] = []
    for mapping in mappings:
        if not mapping.get("state") or not mapping.get("key"):
            continue
        value = read_json_mapping(obj, mapping)
        if value is not JSON_PATH_MISSING:
            resolved.append((mapping, value))
    return resolved


class JsonReplies:
    """JSON-body reply rules, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver / ReplyValues; declared here
    # for the methods.
    device_id: str
    _json_responses: list[Any]
    _rule_applies: Callable[[Any], bool]
    _throttle_hold: Callable[..., bool]
    _apply_writes: Callable[..., None]
    is_child_registered: Callable[[str, Any], bool]
    _observe: Callable[..., None]
    _coerce_child_local_id: Callable[[str, Any], Any]
    _mapped_reading: Callable[..., Any]
    _convert_json_value: Callable[[Any, str], Any]

    _extract_json_path = staticmethod(extract_json_path)
    _read_json_mapping = staticmethod(read_json_mapping)
    _json_throttle_scope = staticmethod(json_throttle_scope)

    def _apply_json_responses(self, text: str) -> bool:
        """Apply every JSON-body response rule to one response/message body.

        Returns True if at least one rule applied or was held by its throttle
        (so the caller stops before regex matching, unless a rule declares
        ``after_json``). An object that repeats a name keeps the values under
        every copy (``compiled_protocol.parse_json_reply``): a device that
        bundles several addresses under one name loses none of them.
        """
        obj = _reply_object(text)
        if obj is None:
            return False
        outcomes = [self._apply_json_rule(obj, rule) for rule in self._json_responses]
        applied = _APPLIED in outcomes
        if applied:
            log.debug(
                f"[{self.device_id}] JSON response applied "
                f"({len(self._json_responses)} rule(s))"
            )
        return applied or _HELD in outcomes

    def _apply_json_rule(self, obj: dict[str, Any], rule: Any) -> str | None:
        """One rule against one body: applied, held for its throttle window,
        or None when it does not apply or the body carries none of its keys
        (resolved first, so such a body does not stamp the window)."""
        mappings, tstate, require, condition, child_mappings = rule
        if not self._rule_applies(condition) or not _carries(obj, require):
            return None
        resolved = _flat_reads(obj, mappings)
        child_writes = self._resolve_json_child_mappings(obj, child_mappings)
        if not resolved and not child_writes:
            return None
        flat = [
            (mapping["state"], self._mapped_reading(value, mapping, self._convert_json_value))
            for mapping, value in resolved
        ]
        if self._throttle_hold(
            tstate, json_throttle_scope(child_mappings), flat, child_writes,
        ):
            return _HELD
        self._apply_writes(flat, child_writes)
        return _APPLIED

    def _resolve_json_child_mappings(
        self, obj: Any, child_mappings: list[dict[str, Any]]
    ) -> list[tuple[str, Any, dict[str, Any]]]:
        """Read a json rule's ``child_set:`` entries out of one parsed body.

        Resolved, not written: a body that carries none of a rule's paths must
        neither apply nor stamp the rule's throttle window, so the caller
        decides after seeing what came back."""
        writes: list[tuple[str, Any, dict[str, Any]]] = []
        for cm in child_mappings:
            write = self._json_child_write(obj, cm)
            if write is not None:
                writes.append(write)
        return writes

    def _json_child_write(
        self, obj: Any, cm: dict[str, Any]
    ) -> tuple[str, Any, dict[str, Any]] | None:
        """One ``child_set:`` entry's ``(child type, local id, updates)``, or
        None. Unregistered ids are skipped -- a device legitimately answers
        for units beyond a user-configured roster -- and told to a contract
        observer only when the body carried a value for one: a smaller unit's
        reply has nothing at the paths of the ids it does not have."""
        ctype = cm["type"]
        local_id = self._coerce_child_local_id(ctype, cm["id"][1])
        if local_id is None:
            return None
        if not self.is_child_registered(ctype, local_id):
            self._json_child_unregistered(obj, cm, local_id)
            return None
        updates: dict[str, Any] = {}
        for pm in cm["props"]:
            value = read_json_mapping(obj, pm)
            if value is not JSON_PATH_MISSING:
                updates[pm["prop"]] = self._mapped_reading(
                    value, pm, self._convert_json_value
                )
        return (ctype, local_id, updates) if updates else None

    def _json_child_unregistered(
        self, obj: Any, cm: dict[str, Any], local_id: Any
    ) -> None:
        """Tell an observer about a child the roster lacks, when the body
        carried a value for it."""
        if not any(
            read_json_mapping(obj, pm) is not JSON_PATH_MISSING for pm in cm["props"]
        ):
            return
        ctype = cm["type"]
        log.debug(
            f"[{self.device_id}] child_set: {ctype} {local_id!r} "
            f"not registered — skipping"
        )
        self._observe(CHILD_UNREGISTERED, child_type=ctype, local_id=local_id)
