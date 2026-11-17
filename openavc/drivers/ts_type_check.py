"""THE rule that the Programmer's generated driver types promise no more than
the driver contract.

``contract_gen_ts`` renders the driver-definition interfaces the Driver
Builder edits from the registry in ``spec.py``, and its tables may spell a
member's type in TypeScript by hand: a string enum as ``string``, a block as
a named interface, a loose ``Record``. A spelling may be wider than the
contract, never narrower. Narrower is a type that says a value is always a
string where the contract takes a number too, or a member always present
where a driver may leave it out: code written against it reads a value that
is not there, and nothing fails until a driver that leaves it out is opened.

So every member the generator renders, hand-spelled or not, is checked here
against the registry node it presents, and so is every alias that presents a
registry definition:

* **Its type** must accept every value the node accepts (``covers``): each
  kind the node takes (string, number, boolean, null, array, object, every
  branch of a ``one_of`` / ``any_of``, one part of an ``all_of``) is taken by
  some member of the TypeScript union, objects member by member. Beside a
  kind, a combinator only narrows it further, so taking the kind is enough.
  A node with no ``extra`` takes keys it does not name, holding anything, as
  the published schema does; ``Record<string, string>`` over such a node is
  narrower.
* **Its requiredness** must be the node's own ``required`` or a member the
  caller says every door refuses a driver without (``guaranteed``); the
  generator proves each of those in its tests.

The TypeScript is read in the small set of forms the generator's tables
use: unions, parentheses, ``X[]``, ``Array<X>``, ``Record<string, X>``,
object literals with an optional ``[key: string]: X``, string and number
literals, the primitives, and the names of the interfaces and aliases the
same file renders. Anything else is refused (``TsTypeError``), so a new
spelling is taught here before it can ship unchecked.

Pure stdlib; no import of the generator, which hands everything in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

_ANY: dict[str, Any] = {"any": True}

_TOKEN = re.compile(
    r'\s*(?:(?P<str>"(?:[^"\\]|\\.)*")|(?P<num>-?\d+(?:\.\d+)?)'
    r"|(?P<id>[A-Za-z_$][\w$]*)|(?P<p>\[\]|[|{}()<>\[\]:;?,]))"
)


class TsTypeError(ValueError):
    """TypeScript text the checker cannot read, or a name it cannot find."""


@dataclass(frozen=True)
class Prim:
    name: str


@dataclass(frozen=True)
class Lit:
    value: Any


@dataclass(frozen=True)
class Arr:
    item: Any


@dataclass(frozen=True)
class Rec:
    value: Any


@dataclass(frozen=True)
class Named:
    name: str


@dataclass(frozen=True)
class Union:
    members: tuple[Any, ...]


@dataclass(frozen=True)
class Obj:
    """An object type: each member's (name, type, optional), an index
    signature's value type or None, and the interface or alias name it was
    read from (whose ``guaranteed`` members may be required)."""

    members: tuple[tuple[str, Any, bool], ...]
    index: Any = None
    name: str | None = None


_WORDS: dict[str, Any] = {
    "true": Lit(True),
    "false": Lit(False),
    **{word: Prim(word) for word in ("string", "number", "boolean", "null", "unknown", "undefined")},
}


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    text = text.rstrip()
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise TsTypeError(f"cannot read the TypeScript type {text!r} at {text[pos:]!r}")
        kind = match.lastgroup or ""
        out.append((kind, match.group(kind)))
        pos = match.end()
    return out


def _joined(parts: list[Any]) -> Any:
    """One type for a union's parts: nested unions opened, one part alone."""
    flat: list[Any] = []
    for part in parts:
        flat.extend(part.members if isinstance(part, Union) else (part,))
    return flat[0] if len(flat) == 1 else Union(tuple(flat))


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.toks = _tokens(text)
        self.i = 0

    def fail(self, why: str) -> TsTypeError:
        return TsTypeError(f"cannot read the TypeScript type {self.text!r}: {why}")

    def peek(self) -> str | None:
        return self.toks[self.i][1] if self.i < len(self.toks) else None

    def take(self, want: str | None = None) -> tuple[str, str]:
        if self.i >= len(self.toks):
            raise self.fail("it ends early")
        tok = self.toks[self.i]
        if want is not None and tok[1] != want:
            raise self.fail(f"expected {want!r}, found {tok[1]!r}")
        self.i += 1
        return tok

    def skip(self, tok: str) -> bool:
        """Take ``tok`` if it is next; whether it was."""
        if self.peek() != tok:
            return False
        self.take()
        return True

    def whole(self) -> Any:
        parsed = self.union()
        if self.i != len(self.toks):
            raise self.fail(f"unread text from {self.toks[self.i][1]!r}")
        return parsed

    def union(self) -> Any:
        self.skip("|")
        parts = [self.postfix()]
        while self.skip("|"):
            parts.append(self.postfix())
        return _joined(parts)

    def postfix(self) -> Any:
        parsed = self.primary()
        while self.skip("[]"):
            parsed = Arr(parsed)
        return parsed

    def primary(self) -> Any:
        kind, tok = self.take()
        reader = _BY_TOKEN.get(tok) or _BY_KIND.get(kind)
        if reader is None:
            raise self.fail(f"unexpected {tok!r}")
        return reader(self, tok)

    def parenthesized(self, _tok: str) -> Any:
        inner = self.union()
        self.take(")")
        return inner

    def named(self, name: str) -> Any:
        if name in _WORDS:
            return _WORDS[name]
        generic = _GENERICS.get(name)
        if generic is not None:
            return generic(self)
        if self.peek() == "<":
            raise self.fail(f"unknown generic {name!r}")
        return Named(name)

    def record(self) -> Rec:
        self.take("<")
        if self.union() != Prim("string"):
            raise self.fail("a Record's keys must be string")
        self.take(",")
        inner = self.union()
        self.take(">")
        return Rec(inner)

    def array(self) -> Arr:
        self.take("<")
        inner = self.union()
        self.take(">")
        return Arr(inner)

    def object_body(self, _tok: str) -> Obj:
        members: list[tuple[str, Any, bool]] = []
        index = None
        while self.peek() != "}":
            if self.peek() == "[":
                index = self.index_signature()
            else:
                members.append(self.member())
            self.skip(";") or self.skip(",")
        self.take("}")
        return Obj(tuple(members), index)

    def index_signature(self) -> Any:
        for want in ("[", None, ":"):
            self.take(want)
        if self.take()[1] != "string":
            raise self.fail("an index signature's keys must be string")
        self.take("]")
        self.take(":")
        return self.union()

    def member(self) -> tuple[str, Any, bool]:
        kind, name = self.take()
        if kind != "id":
            raise self.fail(f"expected a member name, found {name!r}")
        optional = self.skip("?")
        self.take(":")
        return (name, self.union(), optional)


_BY_TOKEN: dict[str, Callable[[_Parser, str], Any]] = {
    "(": _Parser.parenthesized,
    "{": _Parser.object_body,
}
_BY_KIND: dict[str, Callable[[_Parser, str], Any]] = {
    "str": lambda _p, tok: Lit(tok[1:-1]),
    "num": lambda _p, tok: Lit(float(tok) if "." in tok else int(tok)),
    "id": _Parser.named,
}
_GENERICS: dict[str, Callable[[_Parser], Any]] = {
    "Record": _Parser.record,
    "Array": _Parser.array,
}


def parse(text: str) -> Any:
    """The checker's reading of one TypeScript type, or TsTypeError."""
    return _Parser(text).whole()


def _with_name(parsed: Any, name: str) -> Any:
    """An alias's reading, an object one carrying the alias's name."""
    return Obj(parsed.members, parsed.index, name) if isinstance(parsed, Obj) else parsed


@dataclass
class TypeTable:
    """What the rendered file defines: each interface as its members
    (name -> (TypeScript text, required)), each alias as its text, and the
    members every door refuses a driver without, by interface or alias."""

    interfaces: Mapping[str, Mapping[str, tuple[str, bool]]]
    aliases: Mapping[str, str]
    defs: Mapping[str, dict[str, Any]]
    guaranteed: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _cache: dict[str, Any] = field(default_factory=dict)

    def resolve(self, name: str) -> Any:
        if name not in self._cache:
            self._cache[name] = self._read(name)
        return self._cache[name]

    def _read(self, name: str) -> Any:
        if name in self.interfaces:
            members = self.interfaces[name].items()
            return Obj(tuple((m, parse(text), not req) for m, (text, req) in members), None, name)
        if name in self.aliases:
            return _with_name(parse(self.aliases[name]), name)
        raise TsTypeError(f"the type {name!r} is not defined in the rendered file")

    def may_require(self, ts: Obj, node: dict[str, Any]) -> set[str]:
        """The members ``ts`` may mark required over ``node``."""
        return set(node.get("required", ())) | set(self.guaranteed.get(ts.name or "", ()))


def _others(node: dict[str, Any]) -> Any:
    """What the keys an object node does not name may hold."""
    extra = node.get("extra", True)
    return _ANY if extra is True else extra


def _plain(value: Any, kinds: Any) -> bool:
    return isinstance(value, kinds) and not isinstance(value, bool)


def _literal_enum(node: dict[str, Any], kinds: Any) -> tuple[Any, ...]:
    """The node's enum when every value is of ``kinds``, else ()."""
    enum = tuple(node.get("enum") or ())
    return enum if all(_plain(v, kinds) for v in enum) else ()


def _takes_literals(members: list[Any], node: dict[str, Any], kinds: Any) -> bool:
    enum = _literal_enum(node, kinds)
    return bool(enum) and all(Lit(v) in members for v in enum)


class _Coverage:
    """Whether a TypeScript type takes every value a registry node takes."""

    def __init__(self, table: TypeTable) -> None:
        self.table = table
        # (type, id of a registry node) pairs being checked: the parsed types
        # compare by value, the registry's nodes live as long as the module.
        self._open: set[tuple[Any, int]] = set()

    def _resolved(self, part: Any) -> Any:
        return self.table.resolve(part.name) if isinstance(part, Named) else part

    def _flat(self, ts: Any) -> list[Any]:
        """The union's members with every name resolved, nested unions
        opened."""
        out: list[Any] = []
        stack = list(ts.members) if isinstance(ts, Union) else [ts]
        while stack:
            part = self._resolved(stack.pop(0))
            if isinstance(part, Union):
                stack[:0] = part.members
            else:
                out.append(part)
        return out

    def covers(self, ts: Any, node: Any) -> bool:
        members = self._flat(ts)
        if Prim("unknown") in members:
            return True
        node = node if isinstance(node, dict) else _ANY
        key = (tuple(members), id(node))
        if key in self._open:  # a definition that holds itself: taken so far
            return True
        self._open.add(key)
        try:
            return self._covers_node(members, node)
        finally:
            self._open.discard(key)

    def _covers_node(self, members: list[Any], node: dict[str, Any]) -> bool:
        for key, rule in _NODE_RULES:
            if key in node:
                return rule(self, members, node)
        return False  # a node naming no kind takes anything

    def _ref(self, members: list[Any], node: dict[str, Any]) -> bool:
        return self.covers(Union(tuple(members)), self.table.defs[node["ref"]])

    def _kinds(self, members: list[Any], node: dict[str, Any]) -> bool:
        kinds = node["type"]
        kinds = (kinds,) if isinstance(kinds, str) else kinds
        return all(self._covers_kind(members, kind, node) for kind in kinds)

    def _one_part(self, members: list[Any], node: dict[str, Any]) -> bool:
        # The value meets every part, so taking any one part is enough.
        return any(self.covers(Union(tuple(members)), part) for part in node["all_of"])

    def _each_branch(self, members: list[Any], node: dict[str, Any], comb: str) -> bool:
        return all(self.covers(Union(tuple(members)), branch) for branch in node[comb])

    def _covers_kind(self, members: list[Any], kind: str, node: dict[str, Any]) -> bool:
        rule = _KIND_RULES.get(kind)
        if rule is None:
            raise TsTypeError(f"the registry kind {kind!r} has no TypeScript reading")
        return rule(self, members, node)

    def _takes_string(self, members: list[Any], node: dict[str, Any]) -> bool:
        return Prim("string") in members or _takes_literals(members, node, str)

    def _takes_number(self, members: list[Any], node: dict[str, Any]) -> bool:
        return Prim("number") in members or _takes_literals(members, node, (int, float))

    def _takes_boolean(self, members: list[Any], _node: dict[str, Any]) -> bool:
        return Prim("boolean") in members or {Lit(True), Lit(False)} <= set(members)

    def _takes_null(self, members: list[Any], _node: dict[str, Any]) -> bool:
        return Prim("null") in members

    def _takes_array(self, members: list[Any], node: dict[str, Any]) -> bool:
        item = node.get("items", _ANY)
        arrays = (m for m in members if isinstance(m, Arr))
        return any(self.covers(m.item, item) for m in arrays)

    def _takes_object(self, members: list[Any], node: dict[str, Any]) -> bool:
        return any(self._object_covers(m, node) for m in members)

    def _object_covers(self, ts: Any, node: dict[str, Any]) -> bool:
        if isinstance(ts, Rec):
            return self._record_covers(ts, node)
        if isinstance(ts, Obj):
            return self._members_cover(ts, node) and self._index_covers(ts, node)
        return False

    def _record_covers(self, ts: Rec, node: dict[str, Any]) -> bool:
        held = list((node.get("fields") or {}).values())
        if node.get("extra", True) is not False:
            held.append(_others(node))
        return all(self.covers(ts.value, sub) for sub in held)

    def _members_cover(self, ts: Obj, node: dict[str, Any]) -> bool:
        allowed = self.table.may_require(ts, node)
        return all(self._member_covers(member, node, allowed) for member in ts.members)

    def _member_covers(
        self, member: tuple[str, Any, bool], node: dict[str, Any], allowed: set[str],
    ) -> bool:
        name, member_ts, optional = member
        if not (optional or name in allowed):
            return False
        fields = node.get("fields") or {}
        if name in fields:
            return self.covers(member_ts, fields[name])
        # A key the node does not name: never there in a closed object, any
        # value the node lets its other keys hold in an open one.
        return node.get("extra", True) is False or self.covers(member_ts, _others(node))

    def _index_covers(self, ts: Obj, node: dict[str, Any]) -> bool:
        if ts.index is None:
            return True
        written = {name for name, _, _ in ts.members}
        fields = node.get("fields") or {}
        held = [sub for name, sub in fields.items() if name not in written]
        if node.get("extra", True) is not False:
            held.append(_others(node))
        return all(self.covers(ts.index, sub) for sub in held)


_NODE_RULES: tuple[tuple[str, Callable[[_Coverage, list[Any], dict[str, Any]], bool]], ...] = (
    ("any", lambda _c, _m, _n: False),
    ("ref", _Coverage._ref),
    ("type", _Coverage._kinds),
    ("all_of", _Coverage._one_part),
    ("one_of", lambda c, m, n: c._each_branch(m, n, "one_of")),
    ("any_of", lambda c, m, n: c._each_branch(m, n, "any_of")),
)
_KIND_RULES: dict[str, Callable[[_Coverage, list[Any], dict[str, Any]], bool]] = {
    "string": _Coverage._takes_string,
    "integer": _Coverage._takes_number,
    "number": _Coverage._takes_number,
    "boolean": _Coverage._takes_boolean,
    "null": _Coverage._takes_null,
    "array": _Coverage._takes_array,
    "object": _Coverage._takes_object,
}


def _type_narrowings(coverage: _Coverage, members: list[tuple[str, str, Any]]) -> list[str]:
    return [
        f"{where}: {text} does not take every value the contract allows"
        for where, text, node in members
        if not coverage.covers(parse(text), node)
    ]


def _required_narrowings(table: TypeTable, interfaces: list[tuple[str, dict[str, Any]]]) -> list[str]:
    found: list[str] = []
    for name, node in interfaces:
        resolved = table.resolve(name)
        allowed = table.may_require(resolved, node)
        found.extend(
            f"{name}.{member}: required, though the contract lets a driver leave it out"
            for member, _, optional in resolved.members
            if not (optional or member in allowed)
        )
    return found


def _alias_narrowings(coverage: _Coverage, aliases: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [
        f"{name}: {coverage.table.aliases[name]} does not take every value the contract allows"
        for name, node in aliases
        if not coverage.covers(Named(name), node)
    ]


def narrowings(
    table: TypeTable,
    members: list[tuple[str, str, Any]],
    interfaces: list[tuple[str, dict[str, Any]]],
    aliases: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    """Every place the rendered types promise more than the contract, as a
    sentence each; [] when they promise nothing more.

    ``members`` are (where, TypeScript text, registry node), one per
    rendered member; ``interfaces`` (name, node) pairs each interface with
    the node it presents, for its requiredness; ``aliases`` (name, node)
    each alias that presents a registry definition."""
    coverage = _Coverage(table)
    return [
        *_type_narrowings(coverage, members),
        *_required_narrowings(table, interfaces),
        *_alias_narrowings(coverage, aliases),
    ]
