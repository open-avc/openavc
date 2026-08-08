"""OpenAVC — an open-source AV room control platform.

This package wears two hats, and they do not overlap.

To a driver or plugin author it is an ordinary package: ``from openavc.drivers.base
import BaseDriver``, ``from openavc.transport.tcp import TCPTransport``. Those are
plain submodule imports and nothing here is involved in resolving them.

To someone writing a control script it is the scripting API: ``from openavc import
devices, state, log``. Those names live in :mod:`openavc.core.script_api` and are
served from here on demand by the module ``__getattr__`` below.

Both spellings have to keep working, so two rules govern this file.

**``__all__`` is not decoration.** A module-level ``__getattr__`` is not consulted
by ``from openavc import *`` — the star form reads ``__all__`` and looks each name
up explicitly. Without the list below, a star-import would bind *nothing*, quietly,
with no error to notice.

**Resolution stays lazy.** Importing ``script_api`` up here would drag the whole
core in behind any ``import openavc.utils.logger``, which costs startup time and
invites circular imports. Nothing is imported until a script actually asks for a
name.
"""

from typing import Any

#: The scripting API, as ``docs/scripting-guide.md`` and
#: ``docs/scripting-api-reference.md`` teach it. This list is the public contract:
#: adding a name here publishes it, removing one breaks every script that used it.
#: The rest of ``script_api`` is platform plumbing the ScriptEngine calls directly
#: and stays reachable at its real home, ``openavc.core.script_api``.
__all__ = [
    "Event",
    "after",
    "cancel_all_timers",
    "cancel_timer",
    "compare",
    "delay",
    "devices",
    "events",
    "every",
    "isc",
    "log",
    "macros",
    "on_event",
    "on_state_change",
    "plugins",
    "state",
]


def __getattr__(name: str) -> Any:
    """Resolve a scripting-API name off :mod:`openavc.core.script_api`, on demand.

    Only names in ``__all__`` are served. Anything else raises the ordinary
    ``AttributeError`` a caller expects, so a typo reads as a typo rather than as
    a missing subsystem.
    """
    if name in __all__:
        from openavc.core import script_api

        return getattr(script_api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Include the lazily-served names, so ``dir(openavc)`` and tab-completion agree."""
    return sorted({*globals(), *__all__})
