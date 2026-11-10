"""A response rule's load-time checks: one entry of a YAML driver's ``responses:``.

``validate_driver_definition`` walks the list and asks ``response_rule_errors``
about each rule, tagging what comes back with the rule's place in the list.
What one rule says about itself is decided here, in a fixed order: first the
checks every rule gets (``throttle``, ``only_when``, ``require``,
``after_json``, where ``child_list`` may sit, a value map written into
``set:``), then the checks of the rule's form. An ``address:`` rule is OSC,
otherwise a ``json: true`` rule reads a JSON body, otherwise it is a text rule
whose ``match:`` regex the rest refers to.

Three checks of a rule read other parts of the definition and run later in
the walk: a rule's ``then_query:`` (its entries are query entries, checked
with the poll's) and whether a paged query can place the list a
``child_list:`` rule fills, both in ``query_entry_checks``, and the
placeholders in its ``match:``, in ``substitution_checks``.

A rule's ``child_set:`` is one check for all three forms. What an entry's id
and its values may be depends on what the form has to route on (a capture, an
address segment, a JSON path), so each form is a ``_Routes`` subclass that
answers those two questions; the entry's shape, its declared type, its id map
and the properties it writes are the base class's, the same for all three.
The runtime's twin is ``child_routes.py``, which compiles the same entries and
skips what it cannot read; refusing them here is what tells the author why.

Purity contract, as for ``avcdriver_semantic``: standard library, the sibling
contract modules and the stdlib-only regex-safety helper. The module is
vendored with the rest of the driver contract (openavc-drivers' catalog CI,
openavc-sdk).
"""

from __future__ import annotations

import re
from typing import Any

from openavc.drivers.condition_checks import condition_block_errors
from openavc.drivers.declared_names import child_writable_props, declared_id_kind
from openavc.utils.regex_safety import regex_safety_error

# The keys a json value spec ({key, type, map, contains}) may carry.
_JSON_SPEC_KEYS = frozenset({"key", "path", "type", "map", "contains"})


def response_rule_errors(
    index: int, rule: Any, child_types: dict[str, Any], any_json_rule: bool
) -> list[str]:
    """Every error in one response rule, in the order the author reads them.

    ``index`` is the rule's place in ``responses:``; ``child_types`` the
    driver's ``child_entity_types`` (empty when it declares none or declares
    them malformed); ``any_json_rule`` whether some rule in the list reads
    JSON, which is what gives ``after_json:`` an effect.
    """
    where = f"Response {index}"
    if not isinstance(rule, dict):
        return [f"{where}: must be a mapping"]
    errors = [
        *_throttle_errors(where, rule),
        *condition_block_errors(where, rule.get("only_when"), field="only_when"),
        *_require_errors(where, rule),
        *_after_json_errors(where, rule, any_json_rule),
        *_child_list_placement_errors(where, rule),
        *_capture_set_errors(where, rule),
    ]
    if "address" in rule:
        return errors + _osc_rule_errors(where, rule, child_types)
    if rule.get("json"):
        return errors + _json_rule_errors(where, rule, child_types)
    return errors + _text_rule_errors(where, rule, child_types)


# --- the checks every rule gets -------------------------------------------------


def _not_positive_seconds(value: Any) -> bool:
    # Written as the refusal, not as "value > 0": NaN passes, as it always has.
    return isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0


def _throttle_errors(where: str, rule: dict[str, Any]) -> list[str]:
    """A per-rule throttle is positive seconds. A zero, negative or
    non-numeric one would silently disable the rule or the throttle."""
    throttle = rule.get("throttle")
    if throttle is not None and _not_positive_seconds(throttle):
        return [f"{where}: throttle must be a positive number of seconds"]
    return []


def _require_errors(where: str, rule: dict[str, Any]) -> list[str]:
    """``require:`` scopes a json rule to bodies carrying a key. A misdeclared
    one would leave the rule never matching, or unscoped."""
    require = rule.get("require")
    if require is None:
        return []
    errors = []
    if not rule.get("json"):
        errors.append(f"{where}: require only applies to json: true responses")
    return errors + _require_shape_errors(where, require)


def _require_shape_errors(where: str, require: Any) -> list[str]:
    if isinstance(require, str):
        return [] if require.strip() else [f"{where}: require must name a JSON key"]
    if not isinstance(require, list):
        return [f"{where}: require must be a JSON key name or a list of them"]
    if require and all(_is_key_name(k) for k in require):
        return []
    return [f"{where}: require list entries must be non-empty JSON key names"]


def _is_key_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _after_json_errors(
    where: str, rule: dict[str, Any], any_json_rule: bool
) -> list[str]:
    """``after_json:`` keeps a regex rule eligible after a json rule has read
    the body. Everywhere else it is silently inert: the flag reads as a
    promise the runtime never made, and the value never appears."""
    if not rule.get("after_json"):
        return []
    if rule.get("json"):
        return [
            f"{where}: after_json applies to regex rules; a json: true rule "
            f"already reads the body"
        ]
    if "address" in rule:
        return [
            f"{where}: after_json applies to regex rules; an OSC address rule "
            f"never runs after a json rule"
        ]
    if not any_json_rule:
        return [
            f"{where}: after_json has no effect: no response rule in this driver "
            f"is json: true"
        ]
    return []


def _child_list_placement_errors(where: str, rule: dict[str, Any]) -> list[str]:
    """``child_list:`` spreads a list a regex capture holds. A json body and an
    OSC message have no capture to hold one, and a rule with child_set beside
    it would write the one-item form twice: child_list already reads a list
    of one."""
    if "child_list" not in rule:
        return []
    if rule.get("json"):
        return [
            f"{where}: child_list works on text rules (match:) only; a json: true "
            f"rule routes to children with child_set"
        ]
    if "address" in rule:
        return [
            f"{where}: child_list works on text rules (match:) only; an OSC rule "
            f"routes to children with child_set"
        ]
    if rule.get("child_set") is not None:
        return [
            f"{where}: has both child_set and child_list; keep child_list, which "
            f"also reads a list of one item"
        ]
    return []


def _capture_set_errors(where: str, rule: dict[str, Any]) -> list[str]:
    """A ``set:`` value on a regex or OSC rule is a capture reference ("$1") or
    a static, never a {group, map} spec. That spec IS valid in two
    neighbouring places (inside ``child_set: … state:``, and on a ``json:
    true`` rule), which makes writing it here an easy mistake, and nothing
    downstream catches it: the runtime stores ``str()`` of the mapping, so the
    variable holds the literal text "{'group': 1, ...}" with no warning. Value
    maps on these rules belong in the sibling ``mappings:`` list."""
    if rule.get("json"):
        return []
    set_map = rule.get("set")
    if not isinstance(set_map, dict):
        return []
    return [
        f'{where}: set.{state_key} must be a capture reference like "$1" or a '
        f"static value; move a value map into mappings:"
        for state_key, value in set_map.items()
        if isinstance(value, (dict, list))
    ]


# --- each form's own checks ---------------------------------------------------------


def _osc_rule_errors(
    where: str, rule: dict[str, Any], child_types: dict[str, Any]
) -> list[str]:
    address = rule["address"]
    errors = []
    if not isinstance(address, str) or not address.startswith("/"):
        errors.append(f"{where}: OSC address must start with '/'")
    child_set = rule.get("child_set")
    if child_set is not None:
        errors += _OscRoutes(address).child_set_errors(where, child_set, child_types)
    return errors


def _json_rule_errors(
    where: str, rule: dict[str, Any], child_types: dict[str, Any]
) -> list[str]:
    """A json rule carries no regex: it parses the whole reply and maps fields
    by key or path, so it needs a set map, a mappings list or a child_set to
    do anything."""
    child_set = rule.get("child_set")
    errors = []
    if child_set is None and _maps_nothing(rule):
        errors.append(
            f"{where}: json response needs a 'set' map, a 'mappings' list or a "
            f"'child_set'"
        )
    for label, spec in _flat_value_specs(where, rule):
        if isinstance(spec, dict):
            errors += _json_contains_errors(label, spec)
    if child_set is not None:
        errors += _JSON_ROUTES.child_set_errors(where, child_set, child_types)
    return errors


def _flat_value_specs(where: str, rule: dict[str, Any]) -> list[tuple[str, Any]]:
    """The rule's own value specs, its ``set:`` before its ``mappings:``,
    each with the place a message names it by."""
    json_set = rule.get("set")
    mappings = rule.get("mappings")
    named = json_set.items() if isinstance(json_set, dict) else ()
    listed = enumerate(mappings) if isinstance(mappings, list) else ()
    return [(f"{where}: set.{key}", spec) for key, spec in named] + [
        (f"{where}: mappings[{j}]", spec) for j, spec in listed
    ]


def _maps_nothing(rule: dict[str, Any]) -> bool:
    return not isinstance(rule.get("set"), dict) and not isinstance(
        rule.get("mappings"), list
    )


def _text_rule_errors(
    where: str, rule: dict[str, Any], child_types: dict[str, Any]
) -> list[str]:
    """A text rule's pattern is checked for catastrophic backtracking; its
    child_list and child_set count captures against it."""
    pattern = rule.get("match", "")
    errors = []
    if not pattern:
        errors.append(f"{where}: missing match or address")
    else:
        redos = regex_safety_error(where, pattern)
        if redos:
            errors.append(redos)
    if rule.get("child_list") is not None:
        errors += _child_list_errors(
            f"{where}: child_list", rule["child_list"], pattern, child_types
        )
    child_set = rule.get("child_set")
    if child_set is not None:
        routes = _TextRoutes(_group_count(pattern))
        errors += routes.child_set_errors(where, child_set, child_types)
    return errors


def _json_contains_errors(where: str, spec: dict[str, Any]) -> list[str]:
    """``contains`` on a json mapping spec asks a yes/no question of the value
    at ``key`` (membership, a key, a substring), so it takes a scalar to look
    for. One function so the set:, mappings: and child_set: readings share
    one wording."""
    if "contains" not in spec:
        return []
    needle = spec["contains"]
    if needle is None or isinstance(needle, (dict, list)):
        return [
            f"{where}: contains must be a scalar to look for (a flag name, "
            f"a number, true/false), not {type(needle).__name__}"
        ]
    return []


# --- child_set: one check, a row per form ------------------------------------------------


def _reads_as_int(value: Any) -> bool:
    try:
        int(str(value).strip())
    except ValueError:
        return False
    return True


def _is_capture_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("$")


def _scalar_id(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (str, int))


def _scalar_pair(wire: Any, local: Any) -> bool:
    return isinstance(wire, (str, int)) and _scalar_id(local)


def _id_map_errors(
    where: str, id_map: Any, child_type: str, id_kind: Any
) -> list[str]:
    """An id's ``map:`` (wire id -> local child id), on a text or OSC entry.
    The first bad pair is reported, then the map is left."""
    if id_map is None:
        return []
    if not isinstance(id_map, dict) or not id_map:
        return [f"{where}: id map must be a non-empty mapping of wire id -> local child id"]
    for wire, local in id_map.items():
        if not _scalar_pair(wire, local):
            return [f"{where}: id map entries must be scalar wire id -> local id pairs"]
        if id_kind == "integer" and not _reads_as_int(local):
            return [
                f"{where}: id map value {local!r} is not an integer ({child_type} "
                f"declares integer ids)"
            ]
    return []


class _Routes:
    """How one form's ``child_set:`` entries route; the base holds what all
    three forms share. A form names what its entries say when they omit an
    id or a state map, and answers what its ids and its values may be."""

    missing_id: str = ""
    missing_state: str = ""

    def child_set_errors(
        self, where: str, child_set: Any, child_types: dict[str, Any]
    ) -> list[str]:
        if not isinstance(child_set, list) or not child_set:
            return [f"{where}: child_set must be a non-empty list"]
        errors: list[str] = []
        for j, entry in enumerate(child_set):
            errors += self._entry_errors(f"{where}: child_set[{j}]", entry, child_types)
        return errors

    def _entry_errors(
        self, where: str, entry: Any, child_types: dict[str, Any]
    ) -> list[str]:
        if not isinstance(entry, dict):
            return [f"{where}: must be a mapping"]
        child_type = entry.get("type")
        if not isinstance(child_type, str) or child_type not in child_types:
            return [f"{where}: type {child_type!r} is not a declared child_entity_type"]
        type_def = child_types.get(child_type)
        type_def = type_def if isinstance(type_def, dict) else {}
        child_id = entry.get("id")
        if child_id is None:
            errors = [f"{where}: missing 'id' ({self.missing_id})"]
        else:
            errors = self.id_errors(where, child_id, child_type, declared_id_kind(type_def))
        return errors + self._state_errors(where, entry.get("state"), child_type, type_def)

    def _state_errors(
        self, where: str, state_map: Any, child_type: str, type_def: dict[str, Any]
    ) -> list[str]:
        if not isinstance(state_map, dict) or not state_map:
            return [f"{where}: missing 'state' mapping ({self.missing_state})"]
        writable = child_writable_props(type_def)
        errors: list[str] = []
        for prop, value in state_map.items():
            if prop not in writable:
                errors.append(
                    f"{where}: state prop '{prop}' is not declared in "
                    f"child_entity_types.{child_type}.state_variables"
                )
            errors += self.value_errors(where, prop, value)
        return errors

    def id_errors(
        self, where: str, child_id: Any, child_type: str, id_kind: Any
    ) -> list[str]:
        raise NotImplementedError

    def value_errors(self, where: str, prop: Any, value: Any) -> list[str]:
        raise NotImplementedError


class _TextRoutes(_Routes):
    """A text rule routes by its match's captures: ``$N``, or ``{group: N}``
    with an optional id map; a literal id is taken as written."""

    missing_id = "a capture ref like $1, a literal, or {group, map}"
    missing_state = "prop -> $N or literal"

    def __init__(self, groups: int | None) -> None:
        # The pattern's capture count, or None when it does not compile as
        # written (a {config} placeholder substituted at runtime): no bound.
        self.groups = groups

    def id_errors(
        self, where: str, child_id: Any, child_type: str, id_kind: Any
    ) -> list[str]:
        if isinstance(child_id, dict):
            return self._group_errors(where, child_id.get("group")) + _id_map_errors(
                where, child_id.get("map"), child_type, id_kind
            )
        if _is_capture_ref(child_id):
            return self._ref_errors(f"{where}: id", child_id)
        return []

    def _group_errors(self, where: str, group: Any) -> list[str]:
        if _is_capture_ref(group):
            return self._ref_errors(f"{where}: id group", group)
        if isinstance(group, int) and not isinstance(group, bool):
            return self._ref_errors(f"{where}: id group", f"${group}")
        return [f"{where}: id group must be a capture ref (1, 2, ... or '$1')"]

    def value_errors(self, where: str, prop: Any, value: Any) -> list[str]:
        if _is_capture_ref(value):
            return self._ref_errors(f"{where}: state '{prop}'", value)
        return []

    def _ref_errors(self, where: str, ref: str) -> list[str]:
        try:
            group = int(ref[1:])
        except ValueError:
            return [f"{where}: {ref!r} is not a numeric capture ref ($1, $2, ...)"]
        if group < 1:
            return [f"{where}: capture ref must be $1 or higher"]
        if self.groups is not None and group > self.groups:
            return [
                f"{where}: capture ref ${group} exceeds the pattern's "
                f"{self.groups} group(s)"
            ]
        return []


class _OscRoutes(_Routes):
    """An OSC rule has no captures: the id comes from an address segment
    (``{segment: N}``, with an optional id map) and each value from a
    positional argument (``{arg: N}``) or a literal."""

    missing_id = "{segment: N} for an address segment, or a literal"
    missing_state = "prop -> {arg: N} or literal"

    def __init__(self, address: Any) -> None:
        # Best-effort segment bound: fnmatch '*' can in theory span '/', but no
        # real OSC pattern does, so the pattern's own segment count is the
        # practical upper bound.
        self.segments = (
            len(address.strip("/").split("/"))
            if isinstance(address, str) and address.strip("/")
            else None
        )

    def id_errors(
        self, where: str, child_id: Any, child_type: str, id_kind: Any
    ) -> list[str]:
        if isinstance(child_id, dict):
            return self._segment_errors(where, child_id.get("segment")) + _id_map_errors(
                where, child_id.get("map"), child_type, id_kind
            )
        if _is_capture_ref(child_id):
            return [
                f"{where}: OSC rules have no capture groups; use {{segment: N}} "
                f"for the id, {{arg: N}} for values"
            ]
        return []

    def _segment_errors(self, where: str, segment: Any) -> list[str]:
        if isinstance(segment, bool) or not isinstance(segment, int):
            return [
                f"{where}: id needs an integer 'segment' (0-based index into the "
                f"/-split address; OSC rules have no capture groups)"
            ]
        if segment < 0:
            return [f"{where}: segment must be 0 or higher"]
        if self.segments is not None and segment >= self.segments:
            return [
                f"{where}: segment {segment} is past the end of the address "
                f"pattern ({self.segments} segment(s))"
            ]
        return []

    def value_errors(self, where: str, prop: Any, value: Any) -> list[str]:
        if _is_capture_ref(value):
            return [
                f"{where}: state '{prop}': OSC rules have no capture groups; use "
                f"{{arg: N}}"
            ]
        if isinstance(value, dict):
            return _arg_errors(where, prop, value)
        return []


def _arg_errors(where: str, prop: Any, spec: dict[str, Any]) -> list[str]:
    """An OSC value spec: ``{arg: N}``, a positional argument, or ``{value: ...}``."""
    arg = spec.get("arg")
    if arg is None:
        if "value" in spec:
            return []
        return [f"{where}: state '{prop}' needs {{arg: N}} or {{value: ...}}"]
    if _not_an_index(arg):
        return [f"{where}: state '{prop}' arg must be an integer >= 0 (got {arg!r})"]
    return []


def _not_an_index(value: Any) -> bool:
    return isinstance(value, bool) or not isinstance(value, int) or value < 0


class _JsonRoutes(_Routes):
    """A json body carries no capture and no address to route on: each entry
    names its child literally (one entry per child), and each value is a JSON
    path, plain or as ``{key, type, map, contains}``."""

    missing_id = (
        "a literal child id: a json body has no captures to route on, so write "
        "one entry per child"
    )
    missing_state = "prop -> JSON path"

    def id_errors(
        self, where: str, child_id: Any, child_type: str, id_kind: Any
    ) -> list[str]:
        if isinstance(child_id, dict) or _is_capture_ref(child_id):
            return [
                f"{where}: a json rule has no captures or address segments, so id "
                f"must be a literal child id (got {child_id!r})"
            ]
        if isinstance(child_id, bool) or not isinstance(child_id, (str, int)):
            return [f"{where}: id must be a literal child id (got {child_id!r})"]
        if id_kind == "integer" and not _reads_as_int(child_id):
            return [
                f"{where}: id {child_id!r} is not an integer ({child_type} declares "
                f"integer ids)"
            ]
        return []

    def value_errors(self, where: str, prop: Any, value: Any) -> list[str]:
        if isinstance(value, dict):
            return _json_spec_errors(where, prop, value)
        if _is_capture_ref(value):
            return [
                f"{where}: state '{prop}': a json rule has no capture groups; use a "
                f"JSON path"
            ]
        if not isinstance(value, str) or not value.strip():
            return [
                f"{where}: state '{prop}' must be a JSON path (a literal value has "
                f"nothing to read)"
            ]
        return []


_JSON_ROUTES = _JsonRoutes()


def _json_spec_errors(where: str, prop: Any, spec: dict[str, Any]) -> list[str]:
    errors = []
    key = spec.get("key", spec.get("path"))
    if not isinstance(key, str) or not key.strip():
        errors.append(f'{where}: state \'{prop}\' needs a JSON path: "a.b" or {{key: a.b}}')
    unknown = set(spec) - _JSON_SPEC_KEYS
    if unknown:
        errors.append(
            f"{where}: state '{prop}' has {sorted(unknown)}, which a json rule does "
            f"not read; the spec is {{key, type, map, contains}}"
        )
    return errors + _json_contains_errors(f"{where}: state '{prop}'", spec)


# --- child_list ----------------------------------------------------------------------------


def _capture_number(ref: Any) -> int | None:
    """``"$2"`` or ``2`` as 2; None for anything else, a bool included."""
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str) and re.fullmatch(r"\$\d+", ref):
        return int(ref[1:])
    return None


def _group_count(pattern: Any) -> int | None:
    """Capture groups in a pattern, or None when it does not compile as
    written (a {config} placeholder substituted at runtime, say)."""
    if not isinstance(pattern, str) or not pattern:
        return None
    try:
        return re.compile(pattern).groups
    except re.error:
        return None


def _child_list_errors(
    where: str, spec: Any, pattern: Any, child_types: dict[str, Any]
) -> list[str]:
    """The shape of a regex rule's ``child_list:`` block. A malformed one
    would never write a child, so every reference is checked against the
    pattern it counts: ``group`` and ``start`` the rule's own captures, ``id``
    and ``state`` the item's (``element``)."""
    if not isinstance(spec, dict):
        return [f"{where}: must be a mapping"]
    child_type = spec.get("type")
    type_def = child_types.get(child_type) if isinstance(child_type, str) else None
    if not isinstance(type_def, dict):
        return [f"{where}: type {child_type!r} is not a declared child_entity_type"]
    return _ListCheck(where, spec, type_def, _group_count(pattern)).errors()


def _maps_scalar_ids(id_map: Any) -> bool:
    return isinstance(id_map, dict) and bool(id_map) and all(
        _scalar_id(value) for value in id_map.values()
    )


class _ListCheck:
    """One ``child_list:`` block being checked: the counts its references are
    measured against, and what it found so far."""

    def __init__(
        self, where: str, spec: dict[str, Any], type_def: dict[str, Any],
        outer_groups: int | None,
    ) -> None:
        self.where = where
        self.spec = spec
        self.type_def = type_def
        self.outer_groups = outer_groups
        self.element_groups: int | None = None
        self.found: list[str] = []

    def errors(self) -> list[str]:
        self._ref("group", self.spec.get("group", 1), self.outer_groups, "the rule's match")
        self._element()
        self._separator()
        if self.spec.get("id") is not None:
            self._ids()
        else:
            self._start()
        self._state()
        return self.found

    def _ref(self, label: str, ref: Any, groups: int | None, of: str) -> None:
        number = _capture_number(ref)
        if number is None or number < 1:
            self.found.append(f"{self.where}: {label} must be a capture ref ($1, $2, ...)")
        elif groups is not None and number > groups:
            self.found.append(
                f"{self.where}: {label} ${number} exceeds the {groups} group(s) of {of}"
            )

    def _element(self) -> None:
        element = self.spec.get("element")
        if not isinstance(element, str) or not element:
            self.found.append(
                f"{self.where}: missing 'element' (the regex for one item of the list)"
            )
            return
        try:
            compiled = re.compile(element)
        except re.error as e:
            self.found.append(
                f"{self.where}: element is not a valid regular expression ({e})"
            )
            return
        self.element_groups = compiled.groups
        redos = regex_safety_error(f"{self.where}: element", element)
        if redos:
            self.found.append(redos)
        elif compiled.fullmatch(""):
            self.found.append(
                f"{self.where}: element matches empty text, so it cannot tell one "
                f"item from the next"
            )

    def _separator(self) -> None:
        separator = self.spec.get("separator")
        if separator is None:
            return
        if not isinstance(separator, str) or not separator:
            self.found.append(f"{self.where}: separator must be the text between items")

    def _ids(self) -> None:
        child_id = self.spec.get("id")
        if isinstance(child_id, dict):
            self._ref("id group", child_id.get("group"), self.element_groups, "the element")
            self._id_map(child_id.get("map"))
        else:
            self._ref("id", child_id, self.element_groups, "the element")
        if "start" in self.spec:
            self.found.append(
                f"{self.where}: start counts places in the list, and with id each "
                f"item names its own child; remove one of them"
            )

    def _id_map(self, id_map: Any) -> None:
        # Not the child_set id map's rule: a bad value is reported as a bad map,
        # and the wire ids (the keys) are not checked.
        if id_map is None:
            return
        if not _maps_scalar_ids(id_map):
            self.found.append(
                f"{self.where}: id map must be a non-empty mapping of wire id "
                f"-> local child id"
            )
            return
        if declared_id_kind(self.type_def) != "integer":
            return
        for value in id_map.values():
            if not _reads_as_int(value):
                self.found.append(
                    f"{self.where}: id map value {value!r} is not an integer "
                    f"({self.spec.get('type')} declares integer ids)"
                )
                return

    def _start(self) -> None:
        if declared_id_kind(self.type_def) == "string":
            self.found.append(
                f"{self.where}: {self.spec.get('type')} has string ids, so an item "
                f"cannot be named by its place in the list; give id: $N"
            )
        start = self.spec.get("start", 1)
        if isinstance(start, str):
            self._ref("start", start, self.outer_groups, "the rule's match")
        elif isinstance(start, bool) or not isinstance(start, int):
            self.found.append(
                f"{self.where}: start must be the first item's child id or a capture "
                f"ref of the rule's match"
            )

    def _state(self) -> None:
        state_map = self.spec.get("state")
        if not isinstance(state_map, dict) or not state_map:
            self.found.append(
                f"{self.where}: missing 'state' mapping (prop -> $N or literal)"
            )
            return
        writable = child_writable_props(self.type_def)
        for prop, value in state_map.items():
            if prop not in writable:
                self.found.append(
                    f"{self.where}: state prop '{prop}' is not declared in "
                    f"child_entity_types.{self.spec.get('type')}.state_variables"
                )
            if _is_capture_ref(value):
                self._ref(f"state '{prop}'", value, self.element_groups, "the element")
            elif isinstance(value, dict):
                self._state_spec(prop, value)

    def _state_spec(self, prop: Any, spec: dict[str, Any]) -> None:
        if "group" in spec:
            self._ref(f"state '{prop}' group", spec.get("group"), self.element_groups, "the element")
        elif "value" not in spec:
            self.found.append(
                f"{self.where}: state '{prop}' needs a group (the item's capture) "
                f"or a value"
            )
