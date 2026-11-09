"""The query entries' load-time checks: what a poll, a start-up and a then_query send.

A query entry is one line of ``polling.queries``, of ``on_connect`` or of a
text rule's ``then_query``: a bare string, ``{send}`` with an optional
``when:`` / ``when_state:`` gate and ``query_for:``, a per-child
``{each_child, send}`` with an optional ``page_size:``, or, on ``on_connect``
only, an OSC ``{address, args}``. ``validate_driver_definition`` walks each
list and tags what comes back with the entry's place; the order the lists are
reported in is the walk's.

What an entry may name is read from the definition once, into
``QueryEntries``: the config fields a ``when:`` gate names, the device's own
state variables (what ``when_state:`` and a plain ``query_for:`` name), the
child types and their variables, and ``ListReads``, what the ``child_list:``
rules fill. A query sent once reports a child variable only through one of
those rules, and a paged per-child query only through a rule that can place
each page.

A then_query entry is checked in two passes over the rule's list: first
against the rule it follows (``FollowUp``: ``{child_id}`` is the child the
rule's ``child_set:`` wrote, and reading again what the rule has just written
asks the device what it has just said), then as any entry is. The runtime's
twins are ``query_entries.py`` (the gates) and ``rereads.py`` (a then_query
round); the simulator answers the same entries through
``simulator/reread_queries.py``. A new template position in an entry is also
an edit to ``validate_substitutions`` (``avcdriver_semantic``), which checks
every entry's placeholders.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling contract modules only. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

import re
from typing import Any

from openavc.drivers.declared_names import config_field_names, declared_enum_values

# A {child_id} / {child_id:spec} token in a query template.
_CHILD_ID_TOKEN = re.compile(r"\{child_id(?::[^{}]*)?\}")

# The shapes a mapping entry may take, for the message that names them.
_ENTRY_SHAPES = (
    "{each_child, send} or {send, when} or {send, when_state} or "
    "{send, query_for}"
)

_FIXED_START = "Take the start from the reply (start: $2) or name each item (id: $1)"


def then_query_errors(index: int, rule: dict[str, Any]) -> list[str]:
    """Whether a response rule may carry a ``then_query:`` list at all.

    Empty when its entries are to be checked: a text rule's non-empty list.
    """
    if rule.get("json") or "address" in rule:
        return [f"Response {index}: then_query works on text rules (match:) only"]
    follow = rule.get("then_query")
    if not isinstance(follow, list) or not follow:
        return [f"Response {index}: then_query must list at least one query"]
    return []


class FollowUp:
    """What a text rule writes, for the then_query entries that follow it.

    ``child_of`` is the one child type its ``child_set:`` entries route to
    (None when they route to none or to more than one), which is the child
    ``{child_id}`` names in an entry sent once.
    """

    def __init__(self, rule: dict[str, Any]) -> None:
        routes = rule.get("child_set")
        routes = [e for e in routes if isinstance(e, dict)] if isinstance(routes, list) else []
        self._routed_types = sorted({str(e.get("type")) for e in routes})
        self.child_of = self._routed_types[0] if len(self._routed_types) == 1 else None
        written = rule.get("set")
        self._written = set(written) if isinstance(written, dict) else set()
        self._written |= {
            m.get("state") for m in (rule.get("mappings") or []) if isinstance(m, dict)
        }
        self._written_child = {
            prop for e in routes if isinstance(e.get("state"), dict) for prop in e["state"]
        }

    def reread_errors(self, where: str, entry: Any) -> list[str]:
        """An entry against the rule: whom ``{child_id}`` names, and whether
        it reads again what the rule has just written."""
        if isinstance(entry, dict) and "each_child" in entry:
            return []
        send = entry.get("send") if isinstance(entry, dict) else entry
        read = entry.get("query_for") if isinstance(entry, dict) else None
        if isinstance(send, str) and _CHILD_ID_TOKEN.search(send):
            return self._routed_errors(where, read)
        if isinstance(read, str) and read in self._written:
            return [
                f"{where}: reads '{read}', which this rule has just written. "
                f"then_query reads what the report did not say"
            ]
        return []

    def _routed_errors(self, where: str, read: Any) -> list[str]:
        if not self._routed_types:
            return [
                f"{where}: {{child_id}} names the child this rule writes, and the "
                f"rule has no child_set. Route the report to its child with "
                f"child_set, or send the query to every child with each_child"
            ]
        if self.child_of is None:
            return [
                f"{where}: {{child_id}} names the child this rule writes, and the "
                f"rule writes children of more than one type "
                f"({', '.join(self._routed_types)}). Send the query to every "
                f"child of one type with each_child"
            ]
        if isinstance(read, str) and read in self._written_child:
            return [
                f"{where}: reads '{read}', which this rule has just written for "
                f"that child. then_query reads what the report did not say"
            ]
        return []


class ListReads:
    """What the definition's ``child_list:`` rules fill.

    ``filled`` holds each (child type, variable) a rule writes from a list,
    ``paged`` the ones some rule can place a page of: it names each item
    (``id``) or takes its start from the reply (``start: $N``). A rule
    counting from a fixed start writes every page onto the same first
    children. A rule whose ``child_list`` or its ``state`` is not a mapping
    fills nothing (the response rule's own check reports it).
    """

    def __init__(self, responses: list[Any]) -> None:
        self.filled: set[tuple[Any, Any]] = set()
        self.paged: set[tuple[Any, Any]] = set()
        self._type_paged: dict[Any, bool] = {}
        for rule in responses:
            spec = rule.get("child_list") if isinstance(rule, dict) else None
            if isinstance(spec, dict) and isinstance(spec.get("state"), dict):
                self._add(spec)

    def _add(self, spec: dict[str, Any]) -> None:
        start = spec.get("start")
        places_a_page = "id" in spec or (isinstance(start, str) and start.startswith("$"))
        child_type = spec.get("type")
        self._type_paged[child_type] = (
            self._type_paged.get(child_type, False) or places_a_page
        )
        for prop in spec["state"]:
            self.filled.add((child_type, prop))
            if places_a_page:
                self.paged.add((child_type, prop))

    def type_paged(self, child_type: Any) -> bool | None:
        """Whether some rule filling this type can place a page; None when
        no rule fills the type at all."""
        return self._type_paged.get(child_type)


class QueryEntries:
    """The checks of one query entry, against what the definition declares.

    ``responses`` is the rule list as the walk read it (empty when it is not
    a list); the declared maps that are not mappings read as empty.
    """

    def __init__(self, driver_def: dict[str, Any], responses: list[Any]) -> None:
        self._config_fields = config_field_names(driver_def)
        raw_vars = driver_def.get("state_variables")
        self._device_vars: dict[Any, Any] = raw_vars if isinstance(raw_vars, dict) else {}
        raw_types = driver_def.get("child_entity_types")
        self._child_types: dict[Any, Any] = raw_types if isinstance(raw_types, dict) else {}
        self._lists = ListReads(responses)

    # --- what the definition declares ---------------------------------------

    def _variables_of(self, child_type: Any) -> Any:
        type_def = self._child_types.get(child_type) if isinstance(child_type, str) else None
        return type_def.get("state_variables") if isinstance(type_def, dict) else None

    def _declares(self, child_type: Any, var: Any) -> bool:
        child_vars = self._variables_of(child_type)
        return isinstance(child_vars, dict) and var in child_vars

    def _types_declaring(self, var: Any) -> list[Any]:
        """Every child type with a state variable of this name, sorted."""
        return sorted(
            t for t, d in self._child_types.items()
            if isinstance(d, dict)
            and isinstance(d.get("state_variables"), dict)
            and var in d["state_variables"]
        )

    def _names_a_child_variable(self, var: Any) -> bool:
        """A child type declares it, or it is written ``<type>.<variable>``."""
        return bool(self._types_declaring(var)) or (isinstance(var, str) and "." in var)

    # --- one entry ----------------------------------------------------------

    def entry_errors(
        self, where: str, entry: Any, *, osc_form: bool = False,
        follow_up: FollowUp | None = None,
    ) -> list[str]:
        """Every error in one entry, in the order the author reads them.

        ``osc_form``: the list takes an OSC ``{address, args}`` entry too
        (``on_connect``). ``follow_up``: the rule a then_query entry follows;
        ``{child_id}`` in an entry sent once is the child it writes. An entry
        that is not a mapping (a bare string) has nothing to check here.
        """
        if not isinstance(entry, dict):
            return []
        routed = (
            follow_up is not None
            and "each_child" not in entry
            and isinstance(entry.get("send"), str)
            and _CHILD_ID_TOKEN.search(entry["send"]) is not None
        )
        return [
            *self._when_errors(where, entry),
            *self._gate_errors(where, entry),
            *self._query_for_errors(
                where, entry, follow_up.child_of if routed and follow_up else None, routed
            ),
            *self._page_size_errors(where, entry),
            *self._shape_errors(where, entry, osc_form),
        ]

    def _when_errors(self, where: str, entry: dict[str, Any]) -> list[str]:
        # `when: <config_field>` gates the entry on a truthy config value; a
        # field name that doesn't exist would disable the entry forever.
        if "when" not in entry:
            return []
        when = entry.get("when")
        if not isinstance(when, str) or not when:
            return [f"{where}: 'when' must name a config field"]
        if when not in self._config_fields:
            return [
                f"{where}: 'when' field '{when}' is not a declared config field "
                f"(config_schema / default_config / config_derived)"
            ]
        return []

    def _gate_errors(self, where: str, entry: dict[str, Any]) -> list[str]:
        if "when_state" not in entry:
            return []
        return self.when_state_errors(where, entry.get("when_state"))

    # --- query_for: which variable the reply reports ------------------------

    def _query_for_errors(
        self, where: str, entry: dict[str, Any], child_of: Any, routed: bool,
    ) -> list[str]:
        """``query_for:`` names the variable the reply reports (the
        auto-generated simulator answers it): a device variable on an entry
        sent once, ``<child type>.<variable>`` for a list read, one of the
        child type's variables on a per-child entry, or of the child
        ``{child_id}`` names on a then_query entry. A dangling name would
        silently declare nothing."""
        if "query_for" not in entry:
            return []
        read = entry.get("query_for")
        if "address" in entry:
            return [
                f"{where}: 'query_for' is only valid on a plain {{send}} or "
                f"each_child query entry"
            ]
        if not isinstance(read, str) or not read:
            return [f"{where}: 'query_for' must name a state variable"]
        if routed:
            return self._routed_read_errors(where, read, child_of)
        if "each_child" in entry:
            return self._per_child_read_errors(where, entry, read)
        if "." in read:
            return self._list_read_errors(where, read)
        return self._device_read_errors(where, read)

    def _routed_read_errors(self, where: str, read: str, child_of: Any) -> list[str]:
        # No single child type to name: the then_query checks report that.
        if child_of and not self._declares(child_of, read):
            return [
                f"{where}: query_for '{read}' is not a state variable of child "
                f"type {child_of!r}, the child {{child_id}} names here"
            ]
        return []

    def _per_child_read_errors(
        self, where: str, entry: dict[str, Any], read: str,
    ) -> list[str]:
        child_type = entry.get("each_child")
        if not self._declares(child_type, read):
            return [
                f"{where}: query_for '{read}' is not a state variable of child "
                f"type {child_type!r}"
            ]
        if "page_size" not in entry:
            return []
        if (child_type, read) not in self._lists.filled:
            return [
                f"{where}: a paged query reads a list, and no child_list rule "
                f"fills '{read}' on {child_type!r}"
            ]
        if (child_type, read) not in self._lists.paged:
            return [
                f"{where}: every page of this paged query would land on the same "
                f"children of {child_type!r}: the child_list rule that fills "
                f"'{read}' counts from a fixed start. {_FIXED_START}"
            ]
        return []

    def _list_read_errors(self, where: str, read: str) -> list[str]:
        # <child type>.<variable>: a list read sent once, whose reply a
        # child_list rule spreads across every child.
        child_type, _, var = read.partition(".")
        if not isinstance(self._child_types.get(child_type), dict):
            return [
                f"{where}: query_for '{read}': '{child_type}' is not a declared "
                f"child_entity_type"
            ]
        if not self._declares(child_type, var):
            return [
                f"{where}: query_for '{read}': '{var}' is not a state variable "
                f"of child type '{child_type}'"
            ]
        if (child_type, var) not in self._lists.filled:
            return [
                f"{where}: query_for '{read}' names a child variable, and no "
                f"child_list rule fills it; a query sent once reports a child "
                f"only through a list"
            ]
        return []

    def _device_read_errors(self, where: str, read: str) -> list[str]:
        if read in self._device_vars:
            return []
        # Two child types can share a variable name, so a bare child variable
        # on a query sent once says nothing about which children it reports.
        owners = self._types_declaring(read)
        if owners:
            return [
                f"{where}: query_for '{read}' is a child variable; on a query "
                f"sent once, name its child type too: '{owners[0]}.{read}'"
            ]
        return [f"{where}: query_for '{read}' is not a declared state variable"]

    # --- page_size: and the entry's shape ------------------------------------

    def _page_size_errors(self, where: str, entry: dict[str, Any]) -> list[str]:
        # `page_size: N` sends a per-child query once per N children.
        if "page_size" not in entry:
            return []
        page = entry.get("page_size")
        if "each_child" not in entry:
            return [f"{where}: page_size applies to a per-child query (each_child)"]
        if not (_whole(page) and page >= 2):
            return [
                f"{where}: page_size must be a whole number, 2 or more (how "
                f"many children one reply covers)"
            ]
        child_type = entry.get("each_child")
        if "query_for" not in entry and self._lists.type_paged(child_type) is False:
            return [
                f"{where}: every page of this paged query would land on the same "
                f"children of {child_type!r}: its child_list rules count from a "
                f"fixed start. {_FIXED_START}"
            ]
        return []

    def _shape_errors(
        self, where: str, entry: dict[str, Any], osc_form: bool,
    ) -> list[str]:
        if "each_child" in entry:
            return self._per_child_errors(where, entry)
        if osc_form and "address" in entry:
            return []
        send = entry.get("send")
        if isinstance(send, str) and send:
            return []
        return [
            f"{where}: mapping entries must be {_ENTRY_SHAPES}"
            + (" or {address, args}" if osc_form else "")
        ]

    def _per_child_errors(self, where: str, entry: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        child_type = entry.get("each_child")
        type_def = self._child_types.get(child_type) if isinstance(child_type, str) else None
        if not isinstance(child_type, str) or child_type not in self._child_types:
            errors.append(
                f"{where}: each_child type {child_type!r} is not a declared "
                f"child_entity_type"
            )
        elif not isinstance(
            type_def.get("instances") if isinstance(type_def, dict) else None, dict
        ):
            errors.append(
                f"{where}: each_child type '{child_type}' declares no instances: "
                f"block, so nothing would ever be polled"
            )
        send = entry.get("send")
        if not isinstance(send, str) or not send:
            errors.append(f"{where}: missing 'send' template")
        elif not _CHILD_ID_TOKEN.search(send):
            errors.append(
                f"{where}: 'send' must contain {{child_id}} (a format spec like "
                f"{{child_id:02d}} works too)"
            )
        return errors

    # --- when_state: a gate on the device's own state ------------------------

    def when_state_errors(self, where: str, gate: Any) -> list[str]:
        """A ``when_state:`` gate, on an entry or on the whole poll:
        device-level state variables, each with a value (or a list of values)
        its declared type and enum allow.

        The gate reads the device's own state at runtime; a name that is not
        a device-level variable never holds a value, so the entry would run as
        if ungated forever, and a value the variable can never take shuts it
        for good. Both are refused here, where the author can see them. So is
        empty text: a condition left blank reads as none, but it shuts the
        gate whenever the device reports any text at all.
        """
        if not isinstance(gate, dict) or not gate:
            return [
                f"{where}: when_state must map a state variable to the value it "
                f"must hold, or to a list of values"
            ]
        errors: list[str] = []
        for var, wanted in gate.items():
            errors += self._gate_condition_errors(where, var, wanted)
        return errors

    def _gate_condition_errors(self, where: str, var: Any, wanted: Any) -> list[str]:
        var_def = self._device_vars.get(var)
        if not isinstance(var_def, dict):
            if self._names_a_child_variable(var):
                return [
                    f"{where}: when_state '{var}' is a child variable; the gate "
                    f"reads the device's own state variables only"
                ]
            return [f"{where}: when_state '{var}' is not a declared state variable"]
        values = wanted if isinstance(wanted, list) else [wanted]
        if not values:
            return [f"{where}: when_state.{var} lists no values"]
        vtype = var_def.get("type", "string")
        allowed = declared_enum_values(var_def) if vtype == "enum" else None
        found = (
            _gate_value_error(f"{where}: when_state.{var}", vtype, allowed, value)
            for value in values
        )
        return [error for error in found if error]


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# What a gate on a variable of each typed kind waits for, and how a value it
# can never hold is named. The declared type is compared with each name, never
# looked up by hash: a `type:` written as a list is reported, not raised on.
_TYPED_VALUES: tuple[tuple[str, Any, str], ...] = (
    ("boolean", lambda v: isinstance(v, bool),
     "is a boolean; write true or false without quotes, not {!r}"),
    ("integer", _whole, "is a whole number; {!r} is not one"),
    ("number", _number, "is a number; {!r} is not one"),
    ("float", _number, "is a number; {!r} is not one"),
)


def _gate_value_error(
    where: str, vtype: Any, allowed: list[str] | None, value: Any,
) -> str | None:
    """What is wrong with one value a gate waits for, or None."""
    if isinstance(value, str) and not value.strip():
        return (
            f"{where} has no value. Type the value the device reports, or "
            f"remove the condition"
        )
    for name, holds, problem in _TYPED_VALUES:
        if vtype == name:
            return None if holds(value) else f"{where} {problem.format(value)}"
    return _gate_text_error(where, allowed, value)


def _gate_text_error(where: str, allowed: list[str] | None, value: Any) -> str | None:
    if isinstance(value, bool):
        return (
            f"{where} value {value!r} is not text; YAML reads a bare true, false, "
            f"on, off, yes or no as a boolean. Put it in quotes."
        )
    if not isinstance(value, str):
        return f"{where} holds text; put {value!r} in quotes"
    if allowed is not None and value not in allowed:
        return f"{where} value '{value}' is not one of its values ({', '.join(allowed)})"
    return None
