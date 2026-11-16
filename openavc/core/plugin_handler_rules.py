"""THE rules for a plugin's ``MACRO_ACTIONS`` and ``SCRIPT_API``: the two
declarations whose entries each name a method of the plugin class for the
platform to call.

``core/plugin_declarations.py`` judges them as a row each, in its order. Each
rule takes a ``HandlerProbe``, the one thing only an imported class can say:
whether the class has the named method, and whether it is async. Without one
(a plugin read from its source) every other part of an entry is judged, the
handler's name included, and whether the class has it is not.

Purity contract: standard library only, like ``plugin_declarations``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

#: ``handlers(method_name)``: None when the class has no such method, else
#: whether it is async.
HandlerProbe = Callable[[str], "bool | None"]

# The param field types the macro builder can render.
VALID_MACRO_ACTION_PARAM_TYPES = {
    "text", "integer", "float", "boolean", "select",
    "state_key", "device_ref", "macro_ref",
}

# The action name after "<plugin_id>.": lowercase letters, digits, underscores.
MACRO_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# A SCRIPT_API method name, and the plugin id it is exposed under
# (openavc.plugins.<id>.<method>): an identifier with no leading underscore.
SCRIPT_API_METHOD_RE = re.compile(r"^[a-z][a-zA-Z0-9_]*$")

# Names the openavc.plugins proxy uses itself.
SCRIPT_API_RESERVED_NAMES = frozenset({"register", "unregister", "clear"})


def macro_actions_problem(value: Any, plugin_id: str, handlers: HandlerProbe | None = None) -> str | None:
    """What is wrong with a ``MACRO_ACTIONS`` declaration, or None."""
    if not isinstance(value, dict):
        return "MACRO_ACTIONS must be a dict"
    for action_type, spec in value.items():
        problem = _macro_action_problem(action_type, spec, plugin_id, handlers)
        if problem:
            return problem
    return None


def _macro_action_problem(
    action_type: Any, spec: Any, plugin_id: str, handlers: HandlerProbe | None,
) -> str | None:
    problem = _action_name_problem(action_type, plugin_id)
    if problem:
        return problem
    if not isinstance(spec, dict):
        return f"action '{action_type}' spec must be a dict"
    subject = f"action '{action_type}'"
    is_async, problem = _handler(subject, spec, handlers)
    if problem:
        return problem
    if is_async is False:
        return f"{subject} handler '{spec['handler']}' must be an async method"
    return _macro_params_problem(action_type, spec.get("params", []))


def _action_name_problem(action_type: Any, plugin_id: str) -> str | None:
    if not isinstance(action_type, str):
        return f"action key {action_type!r} must be a string"
    prefix = f"{plugin_id}."
    if not action_type.startswith(prefix):
        return f"action '{action_type}' must be prefixed with the plugin id ('{prefix}')"
    suffix = action_type[len(prefix):]
    if not MACRO_ACTION_NAME_RE.match(suffix):
        return (
            f"action '{action_type}' suffix must be lowercase letters, digits, "
            f"or underscores (got '{suffix}')"
        )
    return None


def _macro_params_problem(action_type: str, params: Any) -> str | None:
    if not isinstance(params, list):
        return f"action '{action_type}' params must be a list"
    seen_keys: set[str] = set()
    for i, param in enumerate(params):
        problem = _macro_param_problem(action_type, i, param, seen_keys)
        if problem:
            return problem
    return None


def _macro_param_problem(action_type: str, i: int, param: Any, seen_keys: set[str]) -> str | None:
    if not isinstance(param, dict):
        return f"action '{action_type}' param[{i}] must be a dict"
    key = param.get("key")
    if not key or not isinstance(key, str):
        return f"action '{action_type}' param[{i}] missing 'key'"
    if key in seen_keys:
        return f"action '{action_type}' has duplicate param key '{key}'"
    seen_keys.add(key)
    ptype = param.get("type")
    if ptype not in VALID_MACRO_ACTION_PARAM_TYPES:
        return (
            f"action '{action_type}' param '{key}' has invalid type "
            f"'{ptype}' (allowed: {sorted(VALID_MACRO_ACTION_PARAM_TYPES)})"
        )
    if ptype == "select" and not _has_options(param):
        return (
            f"action '{action_type}' select param '{key}' needs either "
            f"'options' (list) or 'options_source' (state key)"
        )
    return None


def _has_options(param: dict) -> bool:
    return isinstance(param.get("options"), list) or bool(param.get("options_source"))


def script_api_problem(value: Any, plugin_id: str, handlers: HandlerProbe | None = None) -> str | None:
    """What is wrong with a ``SCRIPT_API`` declaration, or None."""
    if not isinstance(value, dict):
        return "SCRIPT_API must be a dict"
    if not SCRIPT_API_METHOD_RE.match(plugin_id):
        return (
            f"plugin id '{plugin_id}' is not a valid Python identifier — "
            f"SCRIPT_API can't be exposed under openavc.plugins"
        )
    for method_name, spec in value.items():
        problem = _script_method_problem(method_name, spec, handlers)
        if problem:
            return problem
    return None


def _script_method_problem(method_name: Any, spec: Any, handlers: HandlerProbe | None) -> str | None:
    problem = _script_method_name_problem(method_name)
    if problem:
        return problem
    if not isinstance(spec, dict):
        return f"method '{method_name}' spec must be a dict"
    is_async, problem = _handler(f"method '{method_name}'", spec, handlers)
    if problem:
        return problem
    if is_async is None:
        return None
    return _sync_problem(method_name, spec, is_async)


def _script_method_name_problem(method_name: Any) -> str | None:
    if not isinstance(method_name, str):
        return f"method key {method_name!r} must be a string"
    if not SCRIPT_API_METHOD_RE.match(method_name):
        return (
            f"method '{method_name}' is not a valid identifier "
            f"(lowercase start, letters/digits/underscores only)"
        )
    if method_name in SCRIPT_API_RESERVED_NAMES:
        return f"method name '{method_name}' is reserved"
    return None


def _sync_problem(method_name: str, spec: dict, is_async: bool) -> str | None:
    """A handler is async unless its entry says ``sync``; a sync entry's is not."""
    if spec.get("sync"):
        if is_async:
            return f"method '{method_name}' marked sync=True but handler '{spec['handler']}' is async"
        return None
    if not is_async:
        return (
            f"method '{method_name}' handler '{spec['handler']}' is not async — "
            f"add 'sync': True to SCRIPT_API entry if intentional"
        )
    return None


def _handler(subject: str, spec: dict, handlers: HandlerProbe | None) -> tuple[bool | None, str | None]:
    """Whether the method a MACRO_ACTIONS or SCRIPT_API entry names is async
    (None when there is no class to ask), or the refusal."""
    handler_name = spec.get("handler")
    if not handler_name or not isinstance(handler_name, str):
        return None, f"{subject} missing 'handler' (method name)"
    if handlers is None:
        return None, None
    is_async = handlers(handler_name)
    if is_async is None:
        return None, f"{subject} handler '{handler_name}' not found on plugin class"
    return is_async, None
