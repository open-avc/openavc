"""THE shape rule for a driver definition: whether each value the contract
declares is the kind of value it declares, read off ``spec.FIELDS`` and
``spec.DEFS`` and nothing else.

``ShapeWalk`` follows the definition through the registry the published
schema is generated from, and at each value it reaches it judges:

* its kind (``type``: a string, an integer, a number, a boolean, a mapping, a
  list), an integer written ``5.0`` passing as one, as the schema passes it;
* a fixed set of values (``enum``, ``const``), naming the set; a value only a
  Python driver may use (``python_enum``) is named as needing one;
* the keys a mapping must carry (``required``); the definition's own
  required fields are the definition walk's to name;
* the forms a value may take (``one_of`` / ``any_of``): a value one form
  takes passes, a value of a kind no form takes is told the kinds, and a
  value of a kind several forms take that none passes is told so in one
  sentence. A choice of keys alone (one of ``when``, ``query_for``,
  ``when_state``) is named as that choice.

What it leaves to the definition walk's own checks and to the catalog's
schema run: text patterns, number ranges, lengths and counts, the names of a
mapping's keys (``prop_names``), the negative and raw rules (``not_``,
``raw``), and a key the contract does not declare (``unknown_key_errors``).
The ``discovery:`` block is not walked at all (``LEFT_FIELDS``): the
discovery parser judges it, at the platform's doors through the hook the
loader hands the definition walk and in the catalog through its own copy, so
its sentences are that parser's.
``tests/test_shape_walk.py`` holds it to the published schema: it never
refuses a value the schema takes, and every refusal of the schema it does
not make is of one of those kinds.

It reads the definition as handed to it; the null-as-absent read
(``spec.without_null_sections``) comes first, in the caller. A refusal is a
place (``commands.power.params.level.type``, ``responses[2]``) and a
sentence that opens with that place, so the walk's tags and the published
schema's paths are one spelling.

Purity contract: standard library plus ``spec``, like every module the
definition walk imports; vendored into openavc-drivers and openavc-sdk with
it.
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.spec import DEFS, FIELDS

# How each JSON kind is named in a sentence, in the words the definition
# walk's own messages use.
_KIND_WORDS: dict[str, str] = {
    "string": "a string",
    "integer": "an integer",
    "number": "a number",
    "boolean": "a boolean (true or false)",
    "object": "a mapping",
    "array": "a list",
    "null": "empty",
}

_NODE_KINDS = ("one_of", "any_of")

# Top-level fields the walk leaves whole to another owner (the module
# docstring says which).
LEFT_FIELDS: tuple[str, ...] = ("discovery",)

# The registry keys the walk reads, and those it leaves to the definition
# walk's own checks, the catalog's schema run, the docs or the floors. Every
# key a registry node carries is in one of the two
# (``tests/test_shape_walk.py``), so a key the registry grows is placed here
# before it can go unjudged.
READ_KEYS = frozenset({
    "type", "enum", "python_enum", "const", "required", "fields", "extra",
    "items", "one_of", "any_of", "all_of", "ref", "any",
})
LEFT_KEYS = frozenset({
    "pattern", "format", "min", "max", "emin", "min_len", "min_items",
    "min_props", "prop_names", "not_", "raw", "doc", "req", "since",
    "since_values", "yaml_since_values", "since_with", "enhancement_since",
})


def kind_fits(kind: str, value: Any) -> bool:
    """Whether ``value`` is of the JSON kind ``kind``, as the published
    schema reads it (a boolean is never a number, ``5.0`` is an integer)."""
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "null":
        return value is None
    return True


def _kinds(node: dict[str, Any]) -> tuple[str, ...]:
    declared = node.get("type")
    if declared is None:
        return ()
    return (declared,) if isinstance(declared, str) else tuple(declared)


def _resolved(node: Any) -> dict[str, Any]:
    """The registry node a reference names, followed until it names none."""
    seen = 0
    while isinstance(node, dict) and "ref" in node and seen < 32:
        node = DEFS.get(node["ref"], {})
        seen += 1
    return node if isinstance(node, dict) else {}


def _join(words: list[str], last: str = "or") -> str:
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} {last} {words[-1]}"


def _shown(value: Any) -> str:
    return f"'{value}'" if isinstance(value, str) else repr(value)


def _at(loc: str) -> str:
    return f"{loc}: " if loc else ""


def _inside(loc: str, key: Any) -> str:
    return f"{loc}.{key}" if loc else str(key)


class ShapeWalk:
    """One definition's values held to the kinds the registry declares.

    ``refusals`` is every place refused, with its sentence, in the order the
    registry declares the places: a value refused for its kind is not
    descended, so one wrong value is one sentence. ``tier`` is the driver's
    form ("yaml" or "python"); it decides which value set a field with a
    wider Python set (``python_enum``) is held to.
    """

    def __init__(self, tier: str = "yaml") -> None:
        self.tier = tier
        self.refusals: list[tuple[str, str]] = []

    def walk_definition(self, definition: dict[str, Any]) -> list[tuple[str, str]]:
        """Each top-level field the definition carries, against its node."""
        for name, node in FIELDS.items():
            if name in definition and name not in LEFT_FIELDS:
                self.walk(node, definition[name], name)
        return self.refusals

    def walk(self, node: Any, value: Any, loc: str) -> None:
        node = _resolved(node)
        if not node or node.get("any") is True:
            return
        if not self._fits_kind(node, value, loc):
            return
        if not self._fits_values(node, value, loc):
            return
        for alternatives in _NODE_KINDS:
            if alternatives in node and not self._fits_a_form(node[alternatives], value, loc):
                return
        for part in node.get("all_of") or ():
            self.walk(part, value, loc)
        if isinstance(value, dict):
            self._mapping(node, value, loc)
        elif isinstance(value, list) and "items" in node:
            for i, entry in enumerate(value):
                self.walk(node["items"], entry, f"{loc}[{i}]")

    def _refuse(self, loc: str, sentence: str) -> None:
        self.refusals.append((loc, f"{_at(loc)}{sentence}"))

    def _fits_kind(self, node: dict[str, Any], value: Any, loc: str) -> bool:
        kinds = _kinds(node)
        if not kinds or any(kind_fits(kind, value) for kind in kinds):
            return True
        words = [_KIND_WORDS.get(k, k) for k in kinds]
        required = node.get("required") or ()
        if kinds == ("object",) and required:
            # The keys it has to carry are part of what it must be.
            words = [f"a mapping with {_join(list(required), 'and')}"]
        self._refuse(loc, f"must be {_join(words)}{_hint(kinds, value)}")
        return False

    def _fits_values(self, node: dict[str, Any], value: Any, loc: str) -> bool:
        if "const" in node and value != node["const"]:
            self._refuse(loc, f"must be {_shown(node['const'])}")
            return False
        allowed = node.get("enum")
        if allowed is None:
            return True
        if self.tier == "python" and "python_enum" in node:
            allowed = node["python_enum"]
        if _in_set(value, allowed):
            return True
        if _in_set(value, node.get("python_enum") or ()):
            self._refuse(
                loc,
                f"{_shown(value)} needs a Python driver; a YAML driver takes "
                f"{_join([_shown(v) for v in allowed])}",
            )
        else:
            self._refuse(
                loc, f"must be {_join([_shown(v) for v in allowed])}, not {_shown(value)}"
            )
        return False

    def _fits_a_form(self, forms: tuple[Any, ...], value: Any, loc: str) -> bool:
        """A value the node lets take one of several forms: it passes when one
        form takes it whole; otherwise the sentence that says the most."""
        resolved = [_resolved(form) for form in forms]
        if all(_keys_only(form) for form in resolved):
            return self._carries_one_of(resolved, value, loc)
        if any(not ShapeWalk(self.tier).refuses(form, value) for form in resolved):
            return True
        by_kind = [form for form in resolved if _form_takes_kind(form, value)]
        if len(by_kind) == 1:
            self.walk(by_kind[0], value, loc)
        elif not by_kind:
            words = [_KIND_WORDS.get(k, k) for k in _form_kinds(resolved)]
            self._refuse(loc, f"must be {_join(words)}{_hint(_form_kinds(resolved), value)}")
        else:
            self._refuse(loc, "is not in any of the forms it can take")
        return False

    def _carries_one_of(self, forms: list[dict[str, Any]], value: Any, loc: str) -> bool:
        if not isinstance(value, dict):
            return True
        choices = [key for form in forms for key in form["required"]]
        if any(key in value for key in choices):
            return True
        self._refuse(loc, f"needs one of {_join([repr(k) for k in choices])}")
        return False

    def refuses(self, node: Any, value: Any) -> bool:
        """Whether ``node`` refuses ``value`` anywhere (this walk's list is
        used for the answer)."""
        self.walk(node, value, "")
        return bool(self.refusals)

    def _mapping(self, node: dict[str, Any], value: dict[Any, Any], loc: str) -> None:
        # Never asked of the definition itself (``walk_definition`` reads its
        # fields), whose required fields the definition walk names.
        for key in node.get("required") or ():
            if key not in value:
                self._refuse(loc, f"missing required '{key}'")
        fields = node.get("fields") or {}
        for key, sub in fields.items():
            if key in value:
                self.walk(sub, value[key], _inside(loc, key))
        extra = node.get("extra")
        if isinstance(extra, dict):
            for key, sub in value.items():
                if key not in fields:
                    self.walk(extra, sub, _inside(loc, key))


def _in_set(value: Any, allowed: Any) -> bool:
    """Membership as the schema reads it: by value, and never a boolean for a
    number or a number for a boolean."""
    return any(v == value and isinstance(v, bool) == isinstance(value, bool) for v in allowed)


def _keys_only(node: dict[str, Any]) -> bool:
    """A form that only says which keys must be there."""
    return set(node) == {"required"}


def _form_kinds(forms: list[dict[str, Any]]) -> list[str]:
    kinds: list[str] = []
    for form in forms:
        for kind in _kinds(form) or _form_kinds(
            [_resolved(f) for k in _NODE_KINDS for f in form.get(k, ())]
        ):
            if kind not in kinds:
                kinds.append(kind)
    return kinds


def _form_takes_kind(form: dict[str, Any], value: Any) -> bool:
    kinds = _form_kinds([form])
    return not kinds or any(kind_fits(kind, value) for kind in kinds)


def _hint(kinds: tuple[str, ...] | list[str], value: Any) -> str:
    """What to do about the commonest wrong kinds: a single entry written
    where a list belongs, and a YAML word or number where text belongs."""
    if "array" in kinds and isinstance(value, (str, dict)):
        return "; write a single entry as a one-item list"
    if "string" in kinds and isinstance(value, (bool, int, float)) and "number" not in kinds:
        return "; put it in quotes"
    return ""


def shape_refusals(definition: dict[str, Any], tier: str = "yaml") -> list[tuple[str, str]]:
    """Every place in ``definition`` whose value is not the kind the contract
    declares, with its sentence (``ShapeWalk``)."""
    return ShapeWalk(tier).walk_definition(definition)


def shape_of(field: str, value: Any, tier: str = "yaml") -> list[tuple[str, str]]:
    """The refusals of one top-level field's ``value`` (none for a field the
    contract does not declare)."""
    walk = ShapeWalk(tier)
    if field in FIELDS:
        walk.walk(FIELDS[field], value, field)
    return walk.refusals
