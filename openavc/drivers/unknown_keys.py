"""THE walk for a key the driver contract does not declare: ``unknown_key_errors``,
which the author's doors refuse on and a load only logs (its docstring says
why), driven off the FIELDS/DEFS registry so it and the published schema never
disagree. A key another check refuses in words of its own (a command
parameter's ``unknown``, ``conversion_checks.param_unknown_issues``) is left to
that check.

Purity contract, as for ``avcdriver_semantic``, which re-exports
``unknown_key_errors``: standard library and the sibling contract modules.
Vendored with the rest of the driver contract (openavc-drivers' catalog CI,
openavc-sdk).
"""

from __future__ import annotations

import difflib as _difflib
from typing import Any

from openavc.drivers.conversion_checks import param_unknown_issues
from openavc.drivers.shape_checks import LEFT_FIELDS
from openavc.drivers.spec import DEFS, FIELDS, block_has_fixed_keys
from openavc.drivers.tagged_errors import TaggedErrors as _TaggedErrors
from openavc.drivers.yaml_text import NOT_TEXT


def unknown_key_errors(driver_def: dict[str, Any]) -> list[str]:
    """Report keys the driver contract doesn't declare, with a spelling hint.

    Driven entirely off the FIELDS/DEFS registry, so it can never disagree
    with the published schema: a block is checked exactly when the registry
    marks it closed (``extra: False``), which is exactly when the generator
    emits ``additionalProperties: false`` for it. Adding a field to the
    registry is all it takes for this to accept it.

    Why this is separate from ``validate_driver_definition``'s default path:
    the runtime loader drops a driver entirely when validation returns any
    error, and nothing gates a driver on ``min_platform_version`` at load. So
    a driver written for a newer platform would vanish at load and take its
    devices offline — worse than the typo. The loader logs these as warnings;
    only the authoring gates treat them as errors.

    The ``discovery:`` block is not descended (``shape_checks.LEFT_FIELDS``):
    its reader names a key it does not read, and only that reader honours
    the block's ``requires:`` gate, which says whether this release reads
    the block at all.
    """
    walk = _KeyWalk({(place, "unknown") for place, _ in param_unknown_issues(driver_def)})
    walk.walk({"fields": _KEY_WALK_FIELDS, "extra": False}, driver_def, "")
    return walk.errors


# The top-level fields as the key walk reads them: a field left whole to its
# own reader is declared (so it is no unknown key) but not descended.
_KEY_WALK_FIELDS: dict[str, Any] = {
    name: ({} if name in LEFT_FIELDS else node) for name, node in FIELDS.items()
}


class _KeyWalk:
    """``unknown_key_errors``' descent: a definition's value read against the
    registry node that declares it, one method per kind of node. A reference
    is followed, a list's entries are read against its ``items``, a block of
    author-chosen names against its ``extra`` and a block of declared
    ``fields`` against those fields; anything else is not descended. Each
    message names its own place, the block it was found in (a name that is
    not text, a key the block does not declare), never the place an earlier
    entry's descent left. Both are added through the local name ``errors``,
    the form the section-tag scans read.
    """

    def __init__(self, said: set[tuple[str, str]] | None = None) -> None:
        self.errors = _TaggedErrors()
        # (place, key) another check refuses in words of its own.
        self.said = said or set()

    def walk(self, node: Any, value: Any, loc: str) -> None:
        if not isinstance(node, dict):
            return
        if "ref" in node:
            self.walk(DEFS.get(node["ref"]), value, loc)
        elif isinstance(value, list):
            self._entries(node.get("items"), value, loc)
        elif isinstance(value, dict):
            self._block(node, value, loc)

    def _entries(self, item: Any, value: list[Any], loc: str) -> None:
        if item is None:
            return
        for i, entry in enumerate(value):
            self.walk(item, entry, f"{loc}[{i}]")

    def _block(self, node: dict[str, Any], value: dict[Any, Any], loc: str) -> None:
        fields = node.get("fields")
        extra = node.get("extra")
        # A block whose keys are author-chosen names (commands, state
        # variables, a config map) declares its value shape in `extra`. The
        # names are data, not contract keys: descend, don't judge them.
        if isinstance(extra, dict) and not fields:
            self._names(extra, value, loc)
        elif isinstance(fields, dict):
            self._fields(node, fields, value, loc)

    def _names(self, extra: dict[str, Any], value: dict[Any, Any], loc: str) -> None:
        """A block of names the author chose. The one thing worth judging is
        whether a name is text at all: YAML reads a bare ``on``, ``off``,
        ``yes`` or ``no`` as a boolean and bare digits as a number, so a
        parameter an author reasonably calls ``on`` arrives as the key
        ``True``. Nothing downstream can substitute it, and the failure
        surfaces far from the cause."""
        errors = self.errors
        for key, sub in value.items():
            if not isinstance(key, str):
                errors.at(
                    loc,
                    f"{_at(loc)}name '{key}' is not text. {NOT_TEXT}",
                )
            self.walk(extra, sub, _inside(loc, key))

    def _fields(
        self, node: dict[str, Any], fields: dict[str, Any], value: dict[Any, Any], loc: str,
    ) -> None:
        """A block of declared fields: a key it does not declare is reported
        only where the registry says the key set is fixed, so this and the
        schema always agree; an open or type-conditional block is still
        descended, just never judged here."""
        if block_has_fixed_keys(node):
            self._undeclared(set(fields), value, loc)
        for key, sub_node in fields.items():
            if key in value:
                self.walk(sub_node, value[key], _inside(loc, key))

    def _undeclared(self, known: set[str], value: dict[Any, Any], loc: str) -> None:
        errors = self.errors
        for key in sorted(set(value) - known, key=str):
            if (loc, key) in self.said:
                continue
            close = _difflib.get_close_matches(
                str(key), sorted(known), n=1, cutoff=0.7
            )
            hint = f" (did you mean '{close[0]}'?)" if close else ""
            errors.at(loc, f"{_at(loc)}unknown key '{key}'{hint}")


def _at(loc: str) -> str:
    """A message's opening: the place it names, or nothing at the top."""
    return f"{loc}: " if loc else ""


def _inside(loc: str, key: Any) -> str:
    """The place of ``key`` inside ``loc``."""
    return f"{loc}.{key}" if loc else str(key)
