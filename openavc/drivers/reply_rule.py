"""A response rule, read: THE reading of what one rule of a driver's
``responses:`` writes to its flat state variables, and of the parts a reader
takes as written.

``read_reply_rule(rule, state_variables)`` reads one rule into a
``ReplyRule``:

* ``form``: which reader takes it, in the runtime's order: an ``address:``
  makes an OSC rule, ``json: true`` a JSON-body rule, anything else a regex
  (text) rule.
* ``mappings``: the flat mappings it writes, with both of a rule's forms read
  once. A regex rule keeps its ``mappings:`` entries, then its ``set:``
  shorthand's items, a variable named in both taking the ``mappings:``
  entry's reading, each typed by its state variable unless it says
  otherwise, with each capture reference read as a number (``capture_ref``).
  A json rule reads its ``mappings:`` entries, or its ``set:`` items when it
  has none, as value specs (``json_value_spec``). An OSC rule's
  ``mappings:`` are as written: each names a message argument, not a capture.
* ``skipped``: what a regex rule names that no reader writes, a reference
  that names no capture, with the words a reader that logs says.
* ``require``, ``condition``, ``follow_ups``, ``after_json``: a json rule's
  ``require:`` keys, the ``only_when:`` gate, a regex rule's ``then_query:``
  list and its ``after_json:`` flag. Nothing is evaluated here: a condition
  needs the state store and a follow-up the transport, so the runtime asks.

Who asks it: the driver model's ``wire.replies`` (``driver_model``), which
the runtime's compile reads (``compiled_protocol.compile_driver``), and the
simulator's reply tables (``simulator/reply_rules``), the validator's json
keys (``simulator/reply_reading``) and a Generic device's JSON fields
(``simulator/inline_device``), which read one rule at a time.

The rules worth knowing before changing anything here:

* **Config-free.** A pattern and an address stay templates, read from
  ``written``; the compile fills them with one device's config.
* **It never logs and never raises.** A part that is not the shape a reader
  takes reads as absent (the shape walk is what names it), and a reference a
  reader skips is on ``skipped`` for the logging reader to say.
* **Never edited.** Each mapping is a new dict, or the rule's own entry where
  it is already whole; a reader that adds to one copies it first, since the
  definition is shared by every device on the driver.

Pure: the standard library, ``capture_ref`` and ``json_value_spec``, with
absolute ``openavc.`` imports (``tests/test_avcdriver_semantic_purity.py``).
Vendored with the driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.drivers.capture_ref import capture_group, names_a_capture
from openavc.drivers.json_value_spec import FLAT, LISTED, Place, json_value_mapping

# A rule's form: which reader takes it.
REGEX = "regex"
OSC = "osc"
JSON = "json"


@dataclass(frozen=True)
class SkippedMapping:
    """A flat mapping a regex rule names that no reader writes: its state
    variable, and the words a reader that logs says, naming no device."""

    state: str
    words: str


@dataclass(frozen=True)
class ReplyRule:
    """One response rule, read. ``written`` is the rule as the file wrote it
    (a part of the definition, never edited), for what a reader takes as
    written: the pattern or address, the throttle, the child routes."""

    written: Mapping[str, Any]
    form: str
    mappings: tuple[Any, ...]
    skipped: tuple[SkippedMapping, ...] = ()
    require: tuple[str, ...] = ()
    condition: Mapping[str, Any] | None = None
    follow_ups: tuple[Any, ...] | None = None
    after_json: bool = False


def read_reply_rule(rule: Mapping[str, Any], state_variables: Any = None) -> ReplyRule:
    """One rule of ``responses:``, read, its mappings typed by the driver's
    ``state_variables`` (none when that is not a mapping)."""
    state_vars = state_variables if isinstance(state_variables, Mapping) else {}
    form = rule_form(rule)
    skipped: tuple[SkippedMapping, ...] = ()
    if form == OSC:
        mappings = tuple(listed_mappings(rule))
    elif form == JSON:
        mappings = tuple(_json_rule_mappings(rule, state_vars))
    else:
        mappings, skipped = _regex_rule_mappings(rule, state_vars)
    return ReplyRule(
        written=rule,
        form=form,
        mappings=mappings,
        skipped=skipped,
        require=_required_keys(rule),
        condition=_condition(rule),
        follow_ups=_follow_ups(rule),
        after_json=bool(rule.get("after_json")),
    )


def rule_form(rule: Mapping[str, Any]) -> str:
    """Which reader takes a rule: OSC for an ``address:``, JSON for
    ``json: true``, else regex."""
    if "address" in rule:
        return OSC
    return JSON if rule.get("json") else REGEX


def listed_mappings(rule: Mapping[str, Any]) -> list[Any]:
    """A rule's ``mappings:`` list, or none when it is not a list: YAML reads
    a ``mappings:`` key with nothing under it as null, which the loader names
    and loads, so the rule is read on its ``set:`` instead of raising."""
    listed = rule.get("mappings")
    return listed if isinstance(listed, list) else []


def _condition(rule: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """A rule's optional ``only_when:`` gate, as written; anything that is not
    a non-empty object reads as none, as the rest of a hand-installed file's
    misshapen parts do (the loader is where a malformed driver is named)."""
    raw = rule.get("only_when")
    return raw if isinstance(raw, dict) and raw else None


def _follow_ups(rule: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """A regex rule's optional ``then_query:`` list, as written: the runtime
    expands and sends it the way it does a poll entry. Anything that is not a
    non-empty list reads as none, as with ``only_when``."""
    raw = rule.get("then_query")
    return tuple(raw) if isinstance(raw, list) and raw else None


def _required_keys(rule: Mapping[str, Any]) -> tuple[str, ...]:
    """A json rule's optional ``require:`` scope, the key or keys a body must
    carry for the rule to read it. Endpoints of one REST device often reuse a
    field name (``status``, ``id``) with different meanings, and an unscoped
    rule would misapply across them."""
    raw_require = rule.get("require")
    if isinstance(raw_require, str):
        return (raw_require,)
    if isinstance(raw_require, list):
        return tuple(str(k) for k in raw_require if k)
    return ()


def _variable(state_vars: Mapping[str, Any], name: Any) -> Any:
    """The declaration a mapping's state variable has; none for a name that
    is not text."""
    return state_vars.get(name) if isinstance(name, str) else None


def _declared_type(state_vars: Mapping[str, Any], name: Any) -> Any:
    """The type a state variable declares, ``string`` when it declares none."""
    var_def = _variable(state_vars, name)
    return var_def.get("type", "string") if isinstance(var_def, dict) else "string"


# ── json rules ──


def _json_rule_mappings(rule: Mapping[str, Any], state_vars: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A ``json: true`` rule's {state, key, type, map} mappings.

    It reads the detailed ``mappings:`` list or the friendly ``set:`` map,
    the ``set:`` only when there are no ``mappings:`` (unlike a regex rule,
    which keeps both). Either way each item is a value spec (a ``set:`` item
    as ``FLAT`` reads it, a ``mappings:`` entry as ``LISTED`` does), the JSON
    path to read, not a capture reference, typed by its state variable unless
    it says otherwise. A ``mappings:`` entry naming no state variable reads
    nothing."""
    listed = [entry for entry in listed_mappings(rule) if _names_a_state(entry)]
    if listed:
        return _json_mappings(((entry["state"], entry) for entry in listed), state_vars, LISTED)
    set_map = rule.get("set")
    if listed_mappings(rule) or not isinstance(set_map, dict):
        return []
    return _json_mappings(set_map.items(), state_vars, FLAT)


def _json_mappings(
    items: Iterable[tuple[Any, Any]], state_vars: Mapping[str, Any], place: Place
) -> list[dict[str, Any]]:
    """Each (state variable, value spec) as a mapping, typed by its state
    variable unless the spec says otherwise; one ``place`` leaves out is
    dropped (``FLAT`` and ``LISTED`` leave none out)."""
    compiled = (
        json_value_mapping(name, spec, _declared_type(state_vars, name), place)
        for name, spec in items
    )
    return [mapping for mapping in compiled if mapping is not None]


def _names_a_state(entry: Any) -> bool:
    """A ``mappings:`` entry a reader writes from, one naming its state
    variable. Any other entry reads nothing."""
    return isinstance(entry, dict) and isinstance(entry.get("state"), str) and bool(entry["state"])


# ── regex rules ──


def _regex_rule_mappings(
    rule: Mapping[str, Any], state_vars: Mapping[str, Any]
) -> tuple[tuple[Any, ...], tuple[SkippedMapping, ...]]:
    """A regex rule's flat mappings, and what it names that no reader writes.

    A rule may carry both ``mappings:`` (the detailed form, the only one that
    takes a value map) and the ``set:`` shorthand, and routinely needs to: the
    moment one captured field needs a map, the author writes ``mappings:`` for
    it and ``set:`` for the plain ones. Both are kept, ``mappings:`` first.
    A state variable a ``mappings:`` entry names is that entry's, as the
    driver guide says, so a ``set:`` item for it is left out, even when the
    entry names no capture."""
    listed = listed_mappings(rule)
    named = {mp["state"] for mp in listed if _names_a_state(mp)}
    read = [_listed_mapping(mp, state_vars) for mp in listed]
    set_map = rule.get("set")
    if isinstance(set_map, dict):
        read += [
            _set_mapping(name, value, state_vars)
            for name, value in set_map.items() if name not in named
        ]
    mappings = tuple(m for m in read if not isinstance(m, SkippedMapping))
    return mappings, tuple(m for m in read if isinstance(m, SkippedMapping))


def _set_mapping(name: Any, value_expr: Any, state_vars: Mapping[str, Any]) -> Any:
    """One ``set:`` shorthand item as a mapping: ``$N`` reads capture N, any
    other value is a literal; either way typed by the state variable, so a
    boolean fed ``set: {mute: "true"}`` stores True, not the string."""
    var_type = _declared_type(state_vars, name)
    if not names_a_capture(value_expr):
        return {"group": 0, "state": name, "value": value_expr, "type": var_type}
    group = capture_group(value_expr)
    if group is None:
        # A $-reference that names no capture is an author typo. Capturing
        # group 0 (the whole match) would write a wrong value with no warning,
        # so it is skipped, and said. "$0" is the whole match when that is meant.
        return SkippedMapping(
            str(name),
            f"set-shorthand reference {value_expr!r} for state '{name}' is not a "
            f"numeric group ($1, $2, ...); skipping this mapping",
        )
    return {"group": group, "state": name, "type": var_type}


def _listed_mapping(mp: Any, state_vars: Mapping[str, Any]) -> Any:
    """A ``mappings:`` entry typed by its state variable, with the capture its
    ``group`` names read as a number (``capture_ref``; none is the whole
    match). Skipped, said, for a group that names no capture; an entry that
    writes a literal ``value:`` or no state variable is kept as written."""
    mp = _typed_mapping(mp, state_vars)
    if not _reads_a_capture(mp):
        return mp
    group = capture_group(mp.get("group"))
    if group is None:
        return SkippedMapping(
            str(mp["state"]),
            f"mappings: group {mp.get('group')!r} for state '{mp['state']}' is not a "
            f"numeric capture ref ($1, $2, ...); skipping this mapping",
        )
    return {**mp, "group": group}


def _typed_mapping(mp: Any, state_vars: Mapping[str, Any]) -> Any:
    """A ``mappings:`` entry with no ``type:``, given its state variable's
    declared type; anything else as written.

    Without it an explicit entry took the string default even when the
    variable declares integer/boolean, so a trigger comparing it to a number
    never fired (``set:`` has always inferred the declared type). Copied rather
    than filled in place: the entry belongs to the loaded driver definition,
    shared with every other device on the driver."""
    if not isinstance(mp, dict) or "type" in mp or not mp.get("state"):
        return mp
    var_def = _variable(state_vars, mp["state"])
    if isinstance(var_def, dict) and var_def.get("type"):
        return {**mp, "type": var_def["type"]}
    return mp


def _reads_a_capture(mp: Any) -> bool:
    """A ``mappings:`` entry naming a state variable and no literal value."""
    if not isinstance(mp, dict) or "value" in mp:
        return False
    return bool(mp.get("state"))
