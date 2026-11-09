"""What a driver definition declares, for the load-time checks that follow a name.

Many of the contract's checks are cross-references: a ``{config_field}``
template in a push block, a ``when:`` gate on a query or a registration, an
``instances:`` roster sized from a setting, a child property a response rule
writes. Each asks the same question of the definition (is this name declared?),
so the answer is read here once and every check gets the same one. A name that
is not declared gets the nearest one that is (``did_you_mean``), and a mapping
whose keys a Python driver builds at runtime is marked (``UNEVALUATED_KEY``),
so a check does not report a name it could not see.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling ``spec`` table only. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

import difflib
from typing import Any

from openavc.drivers.spec import CHILD_RESERVED_PROPS, CONFIG_FIELD_SOURCES

# Placeholder key standing in for a mapping whose KEYS were built at runtime
# rather than written out. A YAML definition can never contain one: the reader
# that produces it (``python_info``) only ever substitutes it for a Python
# driver's computed block. The checks that follow a name are shared by both
# surfaces, so the marker is defined with them rather than in the reader that
# emits it: a name missing from a mapping holding it may still be declared.
UNEVALUATED_KEY = "<unevaluated>"

# Names the platform substitutes without the driver declaring them:
# ``child_id`` in a per-child send template, and the three tokens the push
# machinery injects before a registration command goes out (``base.py`` sets
# ``listener_port``; ``_push_params`` supplies ``push_callback_url`` and, for
# an sse session, ``push_session``).
PLATFORM_SUBSTITUTIONS = frozenset(
    {"child_id", "push_callback_url", "listener_port", "push_session"}
)


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


def substitution_names(driver_def: dict[str, Any]) -> set[str]:
    """Every name a template may fill from outside a command's own params.

    The runtime builds a command's substitution map as ``{**self.config,
    **self._push_params(), **params}``, and ``self.config`` is the driver's
    declared defaults merged with the device's own settings, so a name
    resolves exactly when it is a config field or the platform supplies it.
    Every shipped YAML driver declares every name it interpolates, so nothing
    beyond the platform's tokens is assumed. A field name that is not text is
    left out: no template can name it, and a did-you-mean sorts these names.
    """
    fields = {name for name in config_field_names(driver_def) if isinstance(name, str)}
    return fields | PLATFORM_SUBSTITUTIONS


def closest_name(name: str, candidates: set[str]) -> str | None:
    """The nearest declared name, for a did-you-mean suggestion."""
    close = difflib.get_close_matches(name, sorted(candidates), n=1, cutoff=0.7)
    return close[0] if close else None


def did_you_mean(name: str, candidates: set[str]) -> str:
    """`` (did you mean 'x'?)`` naming the nearest declared name, or nothing."""
    closest = closest_name(name, candidates)
    return f" (did you mean '{closest}'?)" if closest else ""


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
