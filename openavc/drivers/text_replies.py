"""A YAML driver's text reply rules: a regex per rule over each framed line.

The rules worth knowing before changing anything here:

* A reply is offered to the JSON-body rules first (``JsonReplies``). A body
  one of them read is finished with: the json rules are the ones that
  understand this device's replies, and letting the regex table loose on the
  same text would double-write. ``after_json`` is the opt-in for a body
  carrying values AND something no json key reads (an error field beside the
  readings): then only the rules declaring it are offered the text.
* First match wins. A rule whose ``only_when:`` does not hold has not
  consumed the line, so a later rule may still take it; one whose throttle
  window is open has consumed it, and its writes are held for the window's
  end. The window is per routed child, so one rule can serve N children.
* A ``child_set:`` entry names its child by a capture or a literal; a
  ``child_list:`` entry spreads one reply's list across children, each item
  by its own id capture or by its place counted from ``start``. Either way
  the id goes through the id map, the id coercion and the registered check
  (``ReplyValues._place_child``) before anything is written.
* A line no rule read is counted as unread for the poll loop's
  unread-replies check and told to a contract observer.

``TextReplies`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. From its host it needs ``device_id``, the
compiled ``_compiled_responses``, ``_json_responses`` and
``_after_json_responses`` tables, the message counters, ``_rule_applies``,
``_throttle_hold``, ``_apply_writes``, ``_follow_up``, ``_observe``, what
``JsonReplies`` and what ``ReplyValues`` provide.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from openavc.drivers.contract_events import UNMATCHED_RESPONSE
from openavc.drivers.json_replies import JSON_PATH_MISSING, extract_json_path
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")

# A value a mapping names that the line does not carry.
_UNREAD = JSON_PATH_MISSING


def _group(match: re.Match[str], group: Any) -> Any:
    """A capture, or None when the rule names one the match does not have."""
    try:
        return match.group(group)
    except (IndexError, re.error):
        return None


def list_page(spec: dict[str, Any], match: re.Match[str]) -> str:
    """Where a list reply's page begins: the start capture, else the first
    item's id, else the fixed start."""
    kind, value = spec["start"]
    try:
        if spec["id_group"] is not None:
            text = match.group(spec["group"]) or ""
            first = spec["element"].search(text)
            return str(first.group(spec["id_group"])) if first else ""
        if kind == "group":
            return str(match.group(value))
    except (IndexError, re.error):
        return ""
    return str(value)


def _scope_part(cm: dict[str, Any], match: re.Match[str]) -> str:
    if "list" in cm:
        # One reply writes a whole list, so a list is one scope; a device
        # that answers a page at a time gets one per page.
        return f"{cm['type']}:list:{list_page(cm['list'], match)}"
    id_kind, id_val = cm["id"]
    raw: Any = _group(match, id_val) if id_kind == "group" else id_val
    return f"{cm['type']}:{raw}"


def throttle_scope(child_mappings: list[dict[str, Any]], match: re.Match[str]) -> str:
    """Throttle bucket for a regex rule: the child ids it routes to, so a
    rule serving N children throttles each independently. Flat rules (no
    child_set) return "" — one shared bucket."""
    if not child_mappings:
        return ""
    return "|".join(_scope_part(cm, match) for cm in child_mappings)


def _list_items(spec: dict[str, Any], text: str) -> list[re.Match[str] | None]:
    """A list's items: split on the separator, each trimmed item matched by
    the element whole (one that does not still holds its place); without a
    separator, the element found along the list one after another."""
    element: re.Pattern[str] = spec["element"]
    if spec["separator"] is not None:
        return [element.fullmatch(part.strip()) for part in text.split(spec["separator"])]
    # An empty match places nothing and is not a place in the list.
    return [m for m in element.finditer(text) if m.end() > m.start()]


def _list_start(start_spec: tuple[str, Any], match: re.Match[str]) -> Any:
    """The id the list's first place names, or ``_UNREAD`` when the reply
    does not say where its list begins: placing it anyway could write one
    page over another."""
    kind, value = start_spec
    if kind != "group":
        return value
    try:
        return int(str(match.group(value)).strip())
    except (IndexError, re.error, TypeError, ValueError):
        return _UNREAD


def _item_id(item: re.Match[str] | None, place: int, start: Any, id_group: Any) -> Any:
    """The wire id one list item names: its own capture, else its place."""
    if item is None:
        return _UNREAD
    if id_group is None:
        return start + place
    raw_id = _group(item, id_group)
    return _UNREAD if raw_id is None else raw_id


class TextReplies:
    """Regex reply rules, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver / JsonReplies / ReplyValues;
    # declared here for the methods.
    device_id: str
    _compiled_responses: list[Any]
    _json_responses: list[Any]
    _after_json_responses: list[Any]
    _parsed_messages: int
    _last_unread: str
    _rule_applies: Callable[[Any], bool]
    _throttle_hold: Callable[..., bool]
    _apply_writes: Callable[..., None]
    _follow_up: Callable[..., None]
    _observe: Callable[..., None]
    _apply_json_responses: Callable[[str], bool]
    _place_child: Callable[..., Any]
    _convert_declared: Callable[[Any, str], Any]
    _mapped_reading: Callable[..., Any]
    _convert_value: Callable[[Any, str], Any]

    _throttle_scope = staticmethod(throttle_scope)
    _list_page = staticmethod(list_page)

    def _read_text_reply(self, text: str) -> None:
        """Offer one framed, non-empty line to the json rules, then the
        regex rules."""
        read_by_json = bool(self._json_responses) and self._apply_json_responses(text)
        if read_by_json:
            self._parsed_messages += 1
        # A body a json rule read was read, whether or not an after_json rule
        # also wanted it.
        if self._apply_first_text_rule(text, read_by_json) or read_by_json:
            return
        log.debug(f"[{self.device_id}] Unmatched response: {text!r}")
        self._last_unread = text[:80]
        self._observe(UNMATCHED_RESPONSE, text=text)

    def _apply_first_text_rule(self, text: str, read_by_json: bool) -> bool:
        """Apply the first rule that matches and applies; False when none
        does. After the json rules read the body, only the after_json rules
        are offered it."""
        eligible = self._after_json_responses if read_by_json else self._compiled_responses
        for rule in eligible:
            match = rule[0].search(text)
            # Before anything is written, and before the throttle: a rule
            # that does not apply right now has not consumed this frame.
            if match and self._rule_applies(rule[4]):
                self._apply_text_rule(rule, match, read_by_json)
                return True
        return False

    def _apply_text_rule(
        self, rule: Any, match: re.Match[str], read_by_json: bool
    ) -> None:
        pattern, mappings, child_mappings, tstate, _condition, follow_ups = rule
        flat = self._text_flat_reads(match, mappings)
        children = (
            self._resolve_child_mappings(match, child_mappings)
            if child_mappings else []
        )
        if not read_by_json:
            self._parsed_messages += 1
        scope = throttle_scope(child_mappings, match)
        if self._throttle_hold(tstate, scope, flat, children):
            log.debug(f"[{self.device_id}] Response throttled: {pattern.pattern}")
            return
        self._apply_writes(flat, children)
        log.debug(f"[{self.device_id}] Response matched: {pattern.pattern}")
        if follow_ups:
            self._follow_up(rule, follow_ups, scope, children)

    def _text_flat_reads(
        self, match: re.Match[str], mappings: list[dict[str, Any]]
    ) -> list[tuple[str, Any]]:
        flat: list[tuple[str, Any]] = []
        for mapping in mappings:
            state_key = mapping.get("state")
            if not state_key:
                continue
            value = self._text_reading(match, mapping)
            if value is not _UNREAD:
                flat.append((state_key, value))
        return flat

    def _text_reading(self, match: re.Match[str], mapping: dict[str, Any]) -> Any:
        """One flat mapping's value: its literal ``value:``, else its capture
        (through its ``json_path`` when it has one, which benefits HTTP/TCP
        JSON replies as it does OSC's), mapped and converted."""
        if "value" in mapping:
            return self._convert_declared(mapping["value"], mapping.get("type", "string"))
        raw_value = _group(match, mapping.get("group", 0))
        if raw_value is None:
            return _UNREAD
        json_path = mapping.get("json_path")
        if json_path is not None:
            extracted = extract_json_path(raw_value, json_path)
            if extracted is JSON_PATH_MISSING:
                return _UNREAD
            raw_value = str(extracted)
        return self._mapped_reading(raw_value, mapping, self._convert_value)

    def _resolve_child_mappings(
        self, match: re.Match[str], child_mappings: list[dict[str, Any]]
    ) -> list[tuple[str, Any, dict[str, Any]]]:
        """Read a matched response's captures for child-entity state
        (``child_set:``, ``child_list:``): one ``(child type, local id,
        updates)`` per child, written by the caller with
        ``set_child_state_batch``."""
        writes: list[tuple[str, Any, dict[str, Any]]] = []
        for cm in child_mappings:
            if "list" in cm:
                writes.extend(self._resolve_child_list(match, cm))
                continue
            id_kind, id_val = cm["id"]
            raw_id = _group(match, id_val) if id_kind == "group" else id_val
            if id_kind == "group" and raw_id is None:
                continue
            write = self._child_write(cm["type"], raw_id, cm, match, "child_set")
            if write is not None:
                writes.append(write)
        return writes

    def _child_write(
        self,
        ctype: str,
        raw_id: Any,
        cm: dict[str, Any],
        match: re.Match[str],
        form: str,
    ) -> tuple[str, Any, dict[str, Any]] | None:
        """One child's ``(child type, local id, updates)`` from a wire id and
        the match its props read their captures from (the rule's match for
        ``child_set``, one item's for ``child_list``). None when the id cannot
        be placed or nothing was read."""
        local_id = self._place_child(ctype, raw_id, cm, form)
        if local_id is None:
            return None
        updates: dict[str, Any] = {}
        for pm in cm["props"]:
            value = self._text_prop(pm, match)
            if value is not _UNREAD:
                updates[pm["prop"]] = value
        return (ctype, local_id, updates) if updates else None

    def _text_prop(self, pm: dict[str, Any], match: re.Match[str]) -> Any:
        if "value" in pm:
            return self._convert_declared(pm["value"], pm.get("type", "string"))
        raw_value = _group(match, pm.get("group", 0))
        if raw_value is None:
            return _UNREAD
        return self._mapped_reading(raw_value, pm, self._convert_value)

    def _resolve_child_list(
        self, match: re.Match[str], cm: dict[str, Any]
    ) -> list[tuple[str, Any, dict[str, Any]]]:
        """Spread one reply's list across children (``child_list:``).

        With a separator the list is split on it and every item, trimmed, has
        to match the element whole; an item that does not still holds its
        place, so the items after it keep their children. Without one the
        element is found along the list one after another. An item names its
        child by its own id capture, or by its place counted from ``start``
        (a number, or the rule's capture when the reply says where its page
        begins). Each item is then written exactly as a ``child_set`` entry
        would be: id map, id coercion, unregistered ids skipped."""
        spec = cm["list"]
        text = _group(match, spec["group"])
        if not text:
            return []
        items = _list_items(spec, text)
        id_group = spec["id_group"]
        start = _list_start(spec["start"], match) if id_group is None else 0
        if start is _UNREAD:
            return []
        placed = [
            (item, raw_id)
            for place, item in enumerate(items)
            if (raw_id := _item_id(item, place, start, id_group)) is not _UNREAD
        ]
        writes = [
            self._child_write(cm["type"], raw_id, cm, item, "child_list")
            for item, raw_id in placed
        ]
        return [write for write in writes if write is not None]
