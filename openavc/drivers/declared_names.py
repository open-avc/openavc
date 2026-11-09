"""What a driver definition declares, for the load-time checks that follow a name.

Many of the contract's checks are cross-references: a ``{config_field}``
template in a push block, a ``when:`` gate on a query or a registration, an
``instances:`` roster sized from a setting, a child property a response rule
writes. Each asks the same question of the definition (is this name declared?),
so the answer is read here once and every check gets the same one.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling ``spec`` table only. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.spec import CHILD_RESERVED_PROPS, CONFIG_FIELD_SOURCES


def config_field_names(driver_def: dict[str, Any]) -> set[str]:
    """Every config field a template, a gate or a roster may name.

    The keys of ``config_schema``, ``default_config`` and ``config_derived``:
    a derived key resolves into config at runtime, so it counts like a
    declared field. A source that is not a mapping contributes nothing.
    """
    names: set[str] = set()
    for source in CONFIG_FIELD_SOURCES:
        block = driver_def.get(source)
        if isinstance(block, dict):
            names.update(block)
    return names


def declared_id_kind(type_def: dict[str, Any]) -> Any:
    """A child type's ``id_format.type`` as written, ``integer`` when absent.

    The checks compare it with ``integer`` or ``string``, so a value that is
    neither (a typo, a number) asks for no id check here; the child-type
    check reports the value itself. ``child_ids.child_id_kind``, the runtime's
    reading, takes a non-text kind as ``integer`` instead.
    """
    id_format = type_def.get("id_format")
    id_format = id_format if isinstance(id_format, dict) else {}
    return id_format.get("type", "integer")


def child_writable_props(type_def: Any) -> set[str]:
    """Every property a driver may write on a child of this type.

    The declared ``state_variables`` PLUS the reserved keys the platform
    injects at runtime (``online``, ``label``, ``offline_reason``,
    ``offline_detail``). The checks that need this used to compare against the
    raw declaration alone, so a YAML driver reporting presence, which is the
    single commonest thing a child roster does, was told the key it was
    writing did not exist. The runtime always allowed it
    (``BaseDriver._effective_child_schema`` injects the same names), so this
    was the static half refusing what the live half accepts: the driver
    worked, and ``python -m openavc.drivers.check``, the Driver Builder and
    catalog CI all said it was broken.
    """
    declared = type_def.get("state_variables") if isinstance(type_def, dict) else None
    names = set(declared) if isinstance(declared, dict) else set()
    return names | set(CHILD_RESERVED_PROPS)


def declared_enum_values(decl: dict[str, Any]) -> list[str] | None:
    """The values an enum state variable or setting declares, as text.

    Each entry of ``values:`` is a value or a ``{value, label}`` mapping; a
    mapping without ``value`` names none. None when ``values`` is not a list,
    which the checks read as "any value".
    """
    values = decl.get("values")
    if not isinstance(values, list):
        return None
    return [
        str(v.get("value")) if isinstance(v, dict) else str(v)
        for v in values
        if not isinstance(v, dict) or "value" in v
    ]
