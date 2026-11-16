"""THE reader of a plugin's declarations from its source, without importing it.

Importing a plugin runs its code and needs its dependencies installed, so a
door that must judge a plugin first (a plugin repository's CI, the installer
before a download's dependencies) reads the module's text instead.
``read_source`` gives the same ``Declared`` the loader takes from the imported
class (``plugin_declarations.Declared.of_class``), for
``plugin_declarations.first_problem`` to judge.

Which class: the one the loader's ``plugin_class_in`` picks, the first
top-level class by name whose ``PLUGIN_INFO`` is a dict with an ``id``.

What is read: a value written as a literal, where a name stands for the value
an earlier assignment gives it, looked up where Python looks it up when the
class statement runs (the class body so far, then the module before the
class) and followed through as many names as it takes. That covers both
shapes the published plugins use: a dict literal in the class body, and a
module-level dict bound into the class (``PLUGIN_INFO = PLUGIN_INFO``).
``PLUGIN_INFO`` is read a key at a time, so one value built at runtime leaves
the others read; every other declaration is read whole.

A value that cannot be read (a call, an f-string, a subscript, an imported
name) is ``UNREAD`` and its place is listed in ``Declared.unread``. Not
followed: inheritance, a change made after the assignment, and an assignment
inside ``if``, ``try`` or a function.

Purity contract: standard library only, like ``plugin_declarations``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from typing import Any

from openavc.core.plugin_declarations import DECLARATION_NAMES, UNREAD, Declared


class _NotLiteral(Exception):
    """A value the reader cannot evaluate from the text."""


def read_source(text: str) -> Declared | None:
    """The declarations of the plugin class in a module's source, or None when
    no class there declares a ``PLUGIN_INFO`` dict with an ``id``.

    Raises ``SyntaxError`` (``ValueError`` for a null byte) when the source
    does not parse, which the caller words: a file the loader cannot import
    either.
    """
    tree = ast.parse(text)
    module = _Module(tree)
    classes = _classes_by_name(tree)
    for name in sorted(classes):
        found = _ClassReader(module, *classes[name]).declared()
        if found is not None:
            return found
    return None


def _classes_by_name(tree: ast.Module) -> dict[str, tuple[int, ast.ClassDef]]:
    """Each top-level class with its position; a later class of the same name
    replaces the earlier one, as the module attribute does."""
    return {
        statement.name: (index, statement)
        for index, statement in enumerate(tree.body)
        if isinstance(statement, ast.ClassDef)
    }


class _Module:
    """A module's top-level assignments, by position."""

    def __init__(self, tree: ast.Module):
        self._assigned: dict[str, list[tuple[int, ast.expr]]] = {}
        for index, statement in enumerate(tree.body):
            for name, value in _assignments(statement):
                self._assigned.setdefault(name, []).append((index, value))

    def value_before(self, name: str, before: int) -> tuple[int, ast.expr] | None:
        """The last top-level assignment to ``name`` before statement ``before``."""
        earlier = [found for found in self._assigned.get(name, ()) if found[0] < before]
        return earlier[-1] if earlier else None


@dataclass(frozen=True)
class _Scope:
    """Where the names in one expression are looked up: the class body's
    statements before ``position``, then the module's before ``before``. Each
    name followed moves strictly earlier, so a chain of names always ends."""

    module: _Module
    before: int
    #: A class body's assignments, as (statement, name, value).
    body: tuple[tuple[int, str, ast.expr], ...] = ()
    position: int = 0

    def resolve(self, name: str) -> tuple[ast.expr, _Scope] | None:
        for statement, assigned, value in reversed(self.body):
            if statement < self.position and assigned == name:
                return value, replace(self, position=statement)
        found = self.module.value_before(name, self.before)
        if found is None:
            return None
        return found[1], _Scope(self.module, before=found[0])

    def evaluate(self, node: ast.expr) -> Any:
        try:
            return self._evaluate(node)
        except TypeError as exc:  # an unhashable key or set member
            raise _NotLiteral(str(exc)) from exc

    def _evaluate(self, node: ast.expr) -> Any:
        reader = _READERS.get(type(node))
        if reader is None:
            raise _NotLiteral(type(node).__name__)
        return reader(self, node)

    def _name(self, node: ast.Name) -> Any:
        resolved = self.resolve(node.id)
        if resolved is None:
            raise _NotLiteral(node.id)
        value, scope = resolved
        return scope._evaluate(value)

    def _dict(self, node: ast.Dict) -> dict:
        result: dict[Any, Any] = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                result.update(self._merged(value))
            else:
                result[self._evaluate(key)] = self._evaluate(value)
        return result

    def _merged(self, node: ast.expr) -> dict:
        """What a ``**value`` adds to a dict."""
        merged = self._evaluate(node)
        if not isinstance(merged, dict):
            raise _NotLiteral("**")
        return merged

    def _items(self, node: ast.List | ast.Tuple | ast.Set) -> Any:
        items = [self._evaluate(item) for item in node.elts]
        return _COLLECTIONS[type(node)](items)

    def dict_node(self, node: ast.expr) -> tuple[ast.expr, _Scope]:
        """Follow names to the expression they stand for, so a dict can be
        read a key at a time."""
        scope = self
        while isinstance(node, ast.Name):
            resolved = scope.resolve(node.id)
            if resolved is None:
                break
            node, scope = resolved
        return node, scope


def _constant(_scope: _Scope, node: ast.Constant) -> Any:
    return node.value


def _signed(_scope: _Scope, node: ast.UnaryOp) -> Any:
    """A signed number literal (``-1``); any other unary expression is computed."""
    operand = node.operand
    if not _is_number(operand) or not isinstance(node.op, (ast.USub, ast.UAdd)):
        raise _NotLiteral("unary")
    return -operand.value if isinstance(node.op, ast.USub) else operand.value


def _is_number(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) in (int, float, complex)


_COLLECTIONS = {ast.List: list, ast.Tuple: tuple, ast.Set: set}

_READERS = {
    ast.Constant: _constant,
    ast.Name: _Scope._name,
    ast.Dict: _Scope._dict,
    ast.List: _Scope._items,
    ast.Tuple: _Scope._items,
    ast.Set: _Scope._items,
    ast.UnaryOp: _signed,
}


class _ClassReader:
    """One class body's declarations."""

    def __init__(self, module: _Module, index: int, cls: ast.ClassDef):
        self._scope = _Scope(module, before=index, body=tuple(
            (position, name, value)
            for position, statement in enumerate(cls.body)
            for name, value in _assignments(statement)
        ))
        self._name = cls.name
        self._unread: list[str] = []

    def declared(self) -> Declared | None:
        info = self._info()
        if not isinstance(info, dict) or "id" not in info:
            return None
        values = [self._declaration(name) for name in DECLARATION_NAMES[1:]]
        return Declared(info, *values, unread=tuple(self._unread), class_name=self._name)

    def _last(self, name: str) -> tuple[ast.expr, _Scope] | None:
        """The value of the class body's last assignment to ``name``, and
        where its names are looked up."""
        for position, assigned, value in reversed(self._scope.body):
            if assigned == name:
                return value, replace(self._scope, position=position)
        return None

    def _info(self) -> Any:
        found = self._last("PLUGIN_INFO")
        if found is None:
            return None
        node, scope = found[1].dict_node(found[0])
        if not isinstance(node, ast.Dict):
            return None
        info: dict[Any, Any] = {}
        for key, value in zip(node.keys, node.values):
            self._read_entry(info, key, value, scope)
        return info

    def _read_entry(self, info: dict, key: ast.expr | None, value: ast.expr, scope: _Scope) -> None:
        """One ``key: value`` (or ``**value``) of PLUGIN_INFO into ``info``. A
        key that cannot be read, or a ``**`` that cannot, leaves ``UNREAD`` as
        a key: what it holds is not known."""
        if key is None:
            self._read_merge(info, value, scope)
            return
        try:
            name = scope.evaluate(key)
            hash(name)
        except (_NotLiteral, TypeError):
            self._unread_key(info, "PLUGIN_INFO[<computed key>]")
            return
        try:
            info[name] = scope.evaluate(value)
        except _NotLiteral:
            info[name] = UNREAD
            self._unread.append(f"PLUGIN_INFO[{name!r}]")

    def _read_merge(self, info: dict, value: ast.expr, scope: _Scope) -> None:
        try:
            merged = scope.evaluate(value)
        except _NotLiteral:
            merged = None
        if isinstance(merged, dict):
            info.update(merged)
        else:
            self._unread_key(info, "PLUGIN_INFO[**]")

    def _unread_key(self, info: dict, where: str) -> None:
        info[UNREAD] = UNREAD
        self._unread.append(where)

    def _declaration(self, name: str) -> Any:
        found = self._last(name)
        if found is None:
            return None
        value, scope = found
        try:
            return scope.evaluate(value)
        except _NotLiteral:
            self._unread.append(name)
            return UNREAD


def _assignments(statement: ast.stmt) -> list[tuple[str, ast.expr]]:
    """``(name, value)`` for each plain name a statement assigns: every target
    of ``A = B = value``, and ``A: type = value``."""
    if isinstance(statement, ast.Assign):
        return [(t.id, statement.value) for t in statement.targets if isinstance(t, ast.Name)]
    if isinstance(statement, ast.AnnAssign):
        return _annotated(statement)
    return []


def _annotated(statement: ast.AnnAssign) -> list[tuple[str, ast.expr]]:
    if statement.value is None or not isinstance(statement.target, ast.Name):
        return []
    return [(statement.target.id, statement.value)]
