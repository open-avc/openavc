"""The state variables a reply rule writes and gates on, read against those
its driver declares.

THE rule: each flat target of a response rule (a ``set:`` key, a
``mappings:`` entry's ``state``) and each bare key its ``only_when:`` gates
on names a state variable the driver declares under ``state_variables``.
The runtime takes an undeclared write and only warns
(``BaseDriver._check_undeclared_state``), and a gate on a name nothing
writes never opens (``reply_gate``), so a misspelled name fails on the
device with nothing said. ``undeclared_name_errors`` names each one.

Only the author's doors refuse on it (Validate, the Builder's save,
``drivers.check``, the AI's driver tool, catalog CI); a door loading the
driver reports the same sentence as a notice and loads it
(``avcdriver_semantic.check_driver_definition``, through the walk's
``authors_parts``). A device's own protocol is not a driver file and is
never read here.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling contract modules. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.declared_names import DeclaredSections, did_you_mean
from openavc.drivers.spec import PLATFORM_DEVICE_KEYS, PLATFORM_STATE_PROPS


def undeclared_name_errors(index: int, rule: Any, declared: DeclaredSections) -> list[str]:
    """The state variables one rule writes or gates on that the driver does
    not declare. The author's doors refuse them; a door loading the driver
    names them and loads it (``avcdriver_semantic.check_driver_definition``).

    A flat target is a ``set:`` key or a ``mappings:`` entry's ``state``; an
    OSC rule's are its ``mappings:`` alone, as it reads no ``set:``. A key
    the runtime takes undeclared (``spec.PLATFORM_STATE_PROPS``) is not
    judged. A gate key is judged when it is a bare name, with no dot: the
    reply gate reads it as this device's own (``reply_gate._condition_key``),
    so it names a declared variable or a key the platform writes for every
    device (``spec.PLATFORM_DEVICE_KEYS``). A key with a namespace, or a
    child's dotted key under this device, is read as written and not judged.
    Only a name that is text is read; one of another kind is the shape
    walk's. Nothing is said when the state variables are not a mapping, as
    every name would read as undeclared.
    """
    if not isinstance(rule, dict) or "state_variables" in declared.misshapen:
        return []
    names = _DeclaredNames(declared.state_variables)
    where = f"Response {index}"
    return names.target_errors(where, rule) + names.gate_errors(where, rule.get("only_when"))


class _DeclaredNames:
    """The names a rule may write and gate on, and what it says of one that
    is neither."""

    def __init__(self, state_variables: dict[Any, Any]) -> None:
        self.declared = {name for name in state_variables if isinstance(name, str)}
        self.writable = self.declared | PLATFORM_STATE_PROPS
        self.readable = self.declared | PLATFORM_DEVICE_KEYS

    def target_errors(self, where: str, rule: dict[str, Any]) -> list[str]:
        errors = [
            f"{where}: set '{name}' writes a state variable this driver does not "
            f"declare{did_you_mean(name, self.declared)}; declare it under state_variables"
            for name in self._set_targets(rule)
        ]
        mappings = rule.get("mappings")
        for j, mapping in enumerate(mappings if isinstance(mappings, list) else ()):
            name = mapping.get("state") if isinstance(mapping, dict) else None
            if self._unwritable(name):
                errors.append(
                    f"{where}: mappings[{j}] writes '{name}', a state variable this "
                    f"driver does not declare{did_you_mean(name, self.declared)}; "
                    f"declare it under state_variables"
                )
        return errors

    def _set_targets(self, rule: dict[str, Any]) -> list[str]:
        set_map = rule.get("set")
        if "address" in rule or not isinstance(set_map, dict):
            return []
        return [name for name in set_map if self._unwritable(name)]

    def _unwritable(self, name: Any) -> bool:
        return isinstance(name, str) and name not in self.writable

    def gate_errors(self, where: str, block: Any) -> list[str]:
        """Each bare key of an ``only_when:`` block, in ``condition_checks``'
        order and with its places."""
        return [
            f"{where}: {place} key '{key}' is not a state variable this driver "
            f"declares{did_you_mean(key, self.readable)}; declare it under "
            f"state_variables, or write a full state key such as var.<name> or "
            f"device.$id.<name>"
            for place, key in _gate_keys(block)
            if isinstance(key, str) and key and "." not in key and key not in self.readable
        ]


def _gate_keys(block: Any) -> list[tuple[str, Any]]:
    """Each condition's key in an ``only_when:`` block, with its place: one
    condition, or an ``any:`` then an ``all:`` group of them."""
    if not isinstance(block, dict):
        return []
    if "any" not in block and "all" not in block:
        return [("only_when", block.get("key"))]
    keys: list[tuple[str, Any]] = []
    for group_key in ("any", "all"):
        group = block.get(group_key)
        for j, condition in enumerate(group if isinstance(group, list) else ()):
            if isinstance(condition, dict):
                keys.append((f"only_when.{group_key}[{j}]", condition.get("key")))
    return keys
