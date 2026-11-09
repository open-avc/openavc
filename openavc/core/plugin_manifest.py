"""What a plugin class must declare to be started: the manifest rules.

``validate_manifest`` answers for a plugin class the way the loader refuses one
at start: ``(True, "")``, or ``(False, <the sentence the plugin's card
shows>)``. Every rule about ``PLUGIN_INFO`` and the class-level declarations
beside it (``CONFIG_SCHEMA``, ``CONFIG_SECRETS``, ``MACRO_ACTIONS``,
``SCRIPT_API``, ``EXTENSIONS``) is one check in ``_MANIFEST_CHECKS``, run in
that order; the first refusal is the answer. A new ``PLUGIN_INFO`` rule is one
check function and one row.

The rules for ``EXTENSIONS`` live with what the panel and the IDE are served
from them, in ``core/plugin_extensions.py``; ``network_ports`` is
``core/plugin_ports.py``'s and ``CONFIG_SECRETS`` is
``core/plugin_secrets.py``'s. openavc-plugins' ``validate.py`` repeats several
of the tables here by hand for contributors, and the installer reads
``min_openavc_version`` before it downloads anything
(``plugin_installer._extract_min_openavc_version``), so a change to one of
those tables or to the version rule is made there too.

Logs as ``openavc.core.plugin_loader``: the Programmer's plugin page shows
that logger's lines beside the plugin's own.
"""

import inspect
import re
from typing import Any, Callable

from openavc.core import plugin_secrets
from openavc.core.plugin_extensions import validate_extensions
from openavc.core.plugin_ports import validate_declaration
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.plugin_loader")

REQUIRED_MANIFEST_FIELDS = {"id", "name", "version", "author", "description", "category", "license"}

VALID_CAPABILITIES = {
    "state_read", "state_write", "variable_write",
    "event_emit", "event_subscribe",
    "macro_execute", "device_command", "network_listen", "usb_access",
    "http_endpoints", "guest_endpoints",
}

# PLUGIN_INFO["guest_alias"]: an optional short top-level route for a plugin's
# guest router ("present" also mounts it at /present/*, a URL a guest can
# type). One lowercase URL segment; requires guest_endpoints. Uniqueness
# against other running plugins and the app's live routes is checked when the
# router mounts (api/plugin_ext.py).
GUEST_ALIAS_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
# Top-level path segments of the platform's own that an alias must never shadow.
RESERVED_GUEST_ALIASES = {
    "api", "panel", "programmer", "pair", "setup", "ws", "isc",
    "docs", "redoc", "openapi.json", "assets", "themes", "health",
    "favicon.ico", "static", "simulator",
}

VALID_CATEGORIES = {"control_surface", "integration", "sensor", "utility"}

# Compared lowercased and trimmed.
MIT_COMPATIBLE_LICENSES = {
    "mit", "bsd-2-clause", "bsd-3-clause", "apache-2.0", "isc",
    "psf", "unlicense", "0bsd", "cc0-1.0",
}

# The param field types the macro builder can render.
VALID_MACRO_ACTION_PARAM_TYPES = {
    "text", "integer", "float", "boolean", "select",
    "state_key", "device_ref", "macro_ref",
}

# The action name after "<plugin_id>.": lowercase letters, digits, underscores.
_MACRO_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# A SCRIPT_API method name, and the plugin id it is exposed under
# (openavc.plugins.<id>.<method>): an identifier with no leading underscore.
_SCRIPT_API_METHOD_RE = re.compile(r"^[a-z][a-zA-Z0-9_]*$")

# Names the openavc.plugins proxy uses itself.
_SCRIPT_API_RESERVED_NAMES = frozenset({"register", "unregister", "clear"})


def platform_supported(info: dict, platform_id: str) -> bool:
    """Whether a manifest's ``platforms`` (default ``["all"]``) includes ``platform_id``."""
    platforms = info.get("platforms", ["all"])
    return "all" in platforms or platform_id in platforms


def validate_manifest(plugin_class: type, platform_id: str) -> tuple[bool, str]:
    """Check a plugin class's manifest and declarations. Returns (valid, error_message)."""
    info = getattr(plugin_class, "PLUGIN_INFO", None)
    if not isinstance(info, dict):
        return False, "Missing or invalid PLUGIN_INFO dict"
    for check in _MANIFEST_CHECKS:
        problem = check(info, plugin_class, platform_id)
        if problem is not None:
            return False, problem
    return True, ""


# Each check takes (info, plugin_class, platform_id) and returns the refusal,
# or None when the plugin passes it.


def _required_fields(info: dict, _cls: type, _platform_id: str) -> str | None:
    missing = REQUIRED_MANIFEST_FIELDS - set(info.keys())
    if missing:
        return f"Missing required fields: {sorted(missing)}"
    return None


def _license(info: dict, _cls: type, _platform_id: str) -> str | None:
    if info.get("license", "").lower().strip() not in MIT_COMPATIBLE_LICENSES:
        return f"License '{info.get('license')}' is not MIT-compatible"
    return None


def _category(info: dict, _cls: type, _platform_id: str) -> str | None:
    category = info.get("category", "")
    if category not in VALID_CATEGORIES:
        return f"Invalid category '{category}', must be one of {sorted(VALID_CATEGORIES)}"
    return None


def _capabilities(info: dict, _cls: type, _platform_id: str) -> str | None:
    invalid = set(info.get("capabilities", [])) - VALID_CAPABILITIES
    if invalid:
        return f"Unknown capabilities: {sorted(invalid)}"
    return None


def _guest_alias(info: dict, _cls: type, _platform_id: str) -> str | None:
    alias = info.get("guest_alias")
    if alias is None:
        return None
    if not isinstance(alias, str) or not GUEST_ALIAS_RE.fullmatch(alias):
        return (
            f"Invalid guest_alias '{alias}': must be one lowercase URL "
            "segment (letters, digits, hyphens, underscores; max 32 chars)"
        )
    if alias in RESERVED_GUEST_ALIASES:
        return f"guest_alias '{alias}' is a reserved platform path"
    if "guest_endpoints" not in info.get("capabilities", []):
        return "guest_alias requires the guest_endpoints capability"
    return None


def _network_ports(info: dict, _cls: type, _platform_id: str) -> str | None:
    """A port the plugin needs open in the HOST firewall: a request about the
    machine, so a bad declaration is refused outright rather than trimmed, and
    it requires the capability that says the plugin listens at all."""
    if "network_ports" not in info:
        return None
    ok, problem = validate_declaration(info["network_ports"])
    if not ok:
        return problem
    if info["network_ports"] and "network_listen" not in info.get("capabilities", []):
        return "network_ports requires the network_listen capability"
    return None


def _supported_platform(info: dict, _cls: type, platform_id: str) -> str | None:
    if not platform_supported(info, platform_id):
        return (
            f"Plugin not compatible with current platform '{platform_id}'. "
            f"Supported: {info.get('platforms', ['all'])}"
        )
    return None


def _min_openavc_version(info: dict, _cls: type, _platform_id: str) -> str | None:
    """Passes when the version cannot be compared: a version string that does
    not parse, or a deployment missing ``packaging`` (a missing dependency must
    never turn enabling a plugin into a server error)."""
    min_version = info.get("min_openavc_version")
    if not min_version:
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


def _config_schema(_info: dict, plugin_class: type, _platform_id: str) -> str | None:
    schema = getattr(plugin_class, "CONFIG_SCHEMA", None)
    if schema is not None and not isinstance(schema, dict):
        return "CONFIG_SCHEMA must be a dict"
    return None


def _config_secrets(info: dict, plugin_class: type, _platform_id: str) -> str | None:
    """The logins a plugin declares. A malformed CONFIG_SECRETS is refused:
    started anyway, its logins would stay in the project, which is what
    declaring them was meant to stop."""
    schema = getattr(plugin_class, "CONFIG_SCHEMA", None)
    problem = plugin_secrets.config_secrets_problem(
        getattr(plugin_class, "CONFIG_SECRETS", None), schema,
    )
    if problem:
        return problem
    for where in plugin_secrets.unsupported_row_secrets(schema):
        log.warning(
            f"Plugin '{info['id']}': {where} is marked secret, but a mapping_list row "
            f"has no id to save a login under, so it stays in the project"
        )
    return None


def _declaration(attr: str, validate: Callable[[Any, str, type], tuple[bool, str]]):
    """A check for a class-level declaration that has its own validator."""

    def check(info: dict, plugin_class: type, _platform_id: str) -> str | None:
        declared = getattr(plugin_class, attr, None)
        if declared is None:
            return None
        valid, error = validate(declared, info["id"], plugin_class)
        return None if valid else f"{attr} invalid: {error}"

    return check


def validate_macro_actions(
    macro_actions: Any, plugin_id: str, plugin_class: type
) -> tuple[bool, str]:
    """Validate a plugin's MACRO_ACTIONS declaration. Returns (valid, error_message)."""
    if not isinstance(macro_actions, dict):
        return False, "MACRO_ACTIONS must be a dict"
    for action_type, spec in macro_actions.items():
        problem = _macro_action_problem(action_type, spec, plugin_id, plugin_class)
        if problem:
            return False, problem
    return True, ""


def _macro_action_problem(action_type: Any, spec: Any, plugin_id: str, plugin_class: type) -> str | None:
    if not isinstance(action_type, str):
        return f"action key {action_type!r} must be a string"
    prefix = f"{plugin_id}."
    if not action_type.startswith(prefix):
        return f"action '{action_type}' must be prefixed with the plugin id ('{prefix}')"
    suffix = action_type[len(prefix):]
    if not _MACRO_ACTION_NAME_RE.match(suffix):
        return (
            f"action '{action_type}' suffix must be lowercase letters, digits, "
            f"or underscores (got '{suffix}')"
        )
    if not isinstance(spec, dict):
        return f"action '{action_type}' spec must be a dict"
    subject = f"action '{action_type}'"
    handler, problem = _handler(subject, spec, plugin_class)
    if problem:
        return problem
    if not inspect.iscoroutinefunction(handler):
        return f"{subject} handler '{spec['handler']}' must be an async method"
    return _macro_params_problem(action_type, spec.get("params", []))


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


def validate_script_api(
    script_api: Any, plugin_id: str, plugin_class: type
) -> tuple[bool, str]:
    """Validate a plugin's SCRIPT_API declaration. Returns (valid, error_message)."""
    if not isinstance(script_api, dict):
        return False, "SCRIPT_API must be a dict"
    if not _SCRIPT_API_METHOD_RE.match(plugin_id):
        return False, (
            f"plugin id '{plugin_id}' is not a valid Python identifier — "
            f"SCRIPT_API can't be exposed under openavc.plugins"
        )
    for method_name, spec in script_api.items():
        problem = _script_method_problem(method_name, spec, plugin_class)
        if problem:
            return False, problem
    return True, ""


def _script_method_problem(method_name: Any, spec: Any, plugin_class: type) -> str | None:
    problem = _script_method_name_problem(method_name)
    if problem:
        return problem
    if not isinstance(spec, dict):
        return f"method '{method_name}' spec must be a dict"
    handler, problem = _handler(f"method '{method_name}'", spec, plugin_class)
    if problem:
        return problem
    return _sync_problem(method_name, spec, inspect.iscoroutinefunction(handler))


def _script_method_name_problem(method_name: Any) -> str | None:
    if not isinstance(method_name, str):
        return f"method key {method_name!r} must be a string"
    if not _SCRIPT_API_METHOD_RE.match(method_name):
        return (
            f"method '{method_name}' is not a valid identifier "
            f"(lowercase start, letters/digits/underscores only)"
        )
    if method_name in _SCRIPT_API_RESERVED_NAMES:
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


def _handler(subject: str, spec: dict, plugin_class: type) -> tuple[Any, str | None]:
    """The method a MACRO_ACTIONS or SCRIPT_API entry names, or the refusal."""
    handler_name = spec.get("handler")
    if not handler_name or not isinstance(handler_name, str):
        return None, f"{subject} missing 'handler' (method name)"
    handler = getattr(plugin_class, handler_name, None)
    if handler is None:
        return None, f"{subject} handler '{handler_name}' not found on plugin class"
    return handler, None


_MANIFEST_CHECKS = (
    _required_fields,
    _license,
    _category,
    _capabilities,
    _guest_alias,
    _network_ports,
    _supported_platform,
    _min_openavc_version,
    _config_schema,
    _config_secrets,
    _declaration("MACRO_ACTIONS", validate_macro_actions),
    _declaration("SCRIPT_API", validate_script_api),
    _declaration("EXTENSIONS", validate_extensions),
)
