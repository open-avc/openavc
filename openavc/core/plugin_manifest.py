"""Whether an imported plugin class may be started: its declarations judged on
this machine.

``validate_manifest`` answers for a plugin class the way the loader refuses one
at start: ``(True, "")``, or ``(False, <the sentence the plugin's card
shows>)``. What makes each declaration valid, and the order the rules run in,
is ``core/plugin_declarations.py``'s; this module hands it the class's values
and the two checks only an imported class on a running platform can answer:

- the host: whether this platform is one the plugin runs on, and whether this
  OpenAVC is at least its ``min_openavc_version`` (the comparison needs
  ``packaging``, which the declaration rules may not import);
- the handlers: whether a ``MACRO_ACTIONS`` or ``SCRIPT_API`` entry names a
  method the class has, and whether that method is async.

The installer reads ``min_openavc_version`` with its own walk of the plugin's
files after the download (``plugin_installer._extract_min_openavc_version``),
so a change to the version rule is made there too.

The declaration tables stay importable from here for openavc-plugins' tests,
which compare its ``validate.py`` with them.

Logs as ``openavc.core.plugin_loader``: the Programmer's plugin page shows
that logger's lines beside the plugin's own.
"""

import inspect
from typing import Any

from openavc.core import plugin_secrets
from openavc.core.plugin_declarations import (
    GUEST_ALIAS_RE as GUEST_ALIAS_RE,
    MIT_COMPATIBLE_LICENSES as MIT_COMPATIBLE_LICENSES,
    REQUIRED_MANIFEST_FIELDS as REQUIRED_MANIFEST_FIELDS,
    RESERVED_GUEST_ALIASES as RESERVED_GUEST_ALIASES,
    VALID_CAPABILITIES as VALID_CAPABILITIES,
    VALID_CATEGORIES as VALID_CATEGORIES,
    Declared,
    first_problem,
    macro_actions_problem,
    platform_problem,
    script_api_problem,
)
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.plugin_loader")


def validate_manifest(plugin_class: type, platform_id: str) -> tuple[bool, str]:
    """Check a plugin class's manifest and declarations. Returns (valid, error_message)."""
    problem = first_problem(
        Declared.of_class(plugin_class),
        host=lambda info: platform_problem(info, platform_id) or _min_openavc_version(info),
        handlers=_handlers_of(plugin_class),
    )
    if problem is not None:
        return False, problem
    _warn_row_secrets(plugin_class)
    return True, ""


def _min_openavc_version(info: dict) -> str | None:
    """Passes when the version cannot be compared: a version that is not text
    or does not parse, or a deployment missing ``packaging`` (a missing
    dependency must never turn enabling a plugin into a server error)."""
    min_version = info.get("min_openavc_version")
    if not min_version or not isinstance(min_version, str):
        return None
    from openavc.version import __version__
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return None
    try:
        if Version(__version__) < Version(min_version):
            return f"Plugin requires OpenAVC v{min_version} or later (current: v{__version__})"
    except InvalidVersion:
        pass
    return None


def _handlers_of(plugin_class: Any):
    """The handler probe for ``plugin_class``: None for a name the class has
    no attribute for, else whether that attribute is a coroutine function."""

    def probe(name: str) -> bool | None:
        handler = getattr(plugin_class, name, None)
        if handler is None:
            return None
        return inspect.iscoroutinefunction(handler)

    return probe


def _warn_row_secrets(plugin_class: type) -> None:
    """A ``mapping_list`` row field marked secret is not refused: its rows have
    no id to save a login under, so the login stays in the project."""
    for where in plugin_secrets.unsupported_row_secrets(getattr(plugin_class, "CONFIG_SCHEMA", None)):
        log.warning(
            f"Plugin '{plugin_class.PLUGIN_INFO['id']}': {where} is marked secret, but a "
            f"mapping_list row has no id to save a login under, so it stays in the project"
        )


def validate_macro_actions(
    macro_actions: Any, plugin_id: str, plugin_class: type
) -> tuple[bool, str]:
    """Validate a plugin's MACRO_ACTIONS declaration. Returns (valid, error_message)."""
    problem = macro_actions_problem(macro_actions, plugin_id, _handlers_of(plugin_class))
    return (False, problem) if problem else (True, "")


def validate_script_api(
    script_api: Any, plugin_id: str, plugin_class: type
) -> tuple[bool, str]:
    """Validate a plugin's SCRIPT_API declaration. Returns (valid, error_message)."""
    problem = script_api_problem(script_api, plugin_id, _handlers_of(plugin_class))
    return (False, problem) if problem else (True, "")
