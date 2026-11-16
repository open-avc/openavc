"""THE rules for what a plugin declares: what makes each declaration valid,
and the order a plugin is refused in.

A plugin declares itself in six class attributes: ``PLUGIN_INFO`` (with
``network_ports`` inside it), ``CONFIG_SCHEMA``, ``CONFIG_SECRETS``,
``MACRO_ACTIONS``, ``SCRIPT_API`` and ``EXTENSIONS``. ``first_problem`` judges
them in one pass, one row of ``_RULES`` at a time, in the order the loader has
always refused a plugin: the first refusal is the sentence the plugin's card
shows. A new rule is one function and one row. The ``MACRO_ACTIONS`` and
``SCRIPT_API`` rules are ``core/plugin_handler_rules.py``'s, a row each here.

Two ways in, one judge:

- ``Declared.of_class(plugin_class)``: the values an imported class holds. The
  loader asks this at start (``core/plugin_manifest.py``).
- ``plugin_source.read_source(text)``: the same values read from a plugin
  module's text, for a door that must not run the plugin's code.

Two checks need more than the values, so the judge takes each as an optional
argument, asked at the row where it has always run:

- ``host``: whether this machine can run the plugin (its platform, the
  OpenAVC version). Asked between ``network_ports`` and ``CONFIG_SCHEMA``. A
  source read in CI has no host to ask.
- ``handlers``: whether a ``MACRO_ACTIONS`` or ``SCRIPT_API`` entry's handler
  is a method on the class, and whether it is async. Only an imported class
  answers that.

Left out, the check is not made, and every other rule is judged as the loader
judges it. ``plugin_manifest`` supplies both from the class and the running
platform.

A value a source read could not evaluate is ``UNREAD``. No rule refuses one:
whether it is valid is not known from the text.

Purity contract: standard library only (with ``plugin_handler_rules`` and
``plugin_source``), so openavc-plugins can carry these modules and run the
rules in its CI without installing OpenAVC
(``tests/test_plugin_declarations_purity.py`` holds it). Rules return their
sentence; the caller logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from openavc.core.plugin_handler_rules import (
    HandlerProbe,
    macro_actions_problem,
    script_api_problem,
)

# --- the tables ---------------------------------------------------------------

#: The class attributes a plugin declares itself in, in the order they are judged.
DECLARATION_NAMES = (
    "PLUGIN_INFO", "CONFIG_SCHEMA", "CONFIG_SECRETS", "MACRO_ACTIONS", "SCRIPT_API", "EXTENSIONS",
)

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

#: The protocols a ``network_ports`` entry may ask for. A plugin does not get
#: to ask for a raw socket family.
PORT_PROTOCOLS = ("tcp", "udp")

#: Ports the platform itself owns or that no plugin has any business asking the
#: firewall to open. Refused in a declaration so a typo cannot open SSH, and
#: never recorded for a device's pushes either (core/push_ports.py).
HOST_PORTS = frozenset({22, 23, 25, 135, 137, 138, 139, 445, 3389, 5900})

#: The keys a ``CONFIG_SECRETS`` entry may have.
CONFIG_SECRETS_KEYS = frozenset({"id", "fields", "generated"})

# Each extension type, the field that identifies an entry within it, and the
# order the types are served in (core/plugin_extensions.py). Panel elements are
# keyed by their element type.
EXTENSION_ID_FIELD = {
    "views": "id",
    "device_panels": "id",
    "status_cards": "id",
    "context_actions": "id",
    "panel_elements": "type",
}
VALID_EXTENSION_TYPES = set(EXTENSION_ID_FIELD)


class _Unread:
    """Stands in for a declared value the source computes at runtime."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<unread>"


UNREAD = _Unread()


def _known(value: Any) -> bool:
    return value is not UNREAD


def _declares(value: Any) -> bool:
    """Whether a declaration is there to judge: given, and read."""
    return value is not None and value is not UNREAD


def info_value(info: dict, key: str, default: Any = None) -> Any:
    """``info[key]``, or ``default`` when it is plainly absent: ``UNREAD`` when
    keys the source computes (an ``UNREAD`` key) may hold it. THE read of one
    ``PLUGIN_INFO`` value, for the rules here and the installer alike."""
    if key in info:
        return info[key]
    return UNREAD if UNREAD in info else default


# --- the declarations ---------------------------------------------------------


@dataclass(frozen=True)
class Declared:
    """A plugin's six declarations: None where it declares none, ``UNREAD``
    where a source read could not evaluate one. In ``PLUGIN_INFO`` a value
    that could not be read keeps its key, with ``UNREAD`` as its value, and a
    computed key or ``**`` leaves ``UNREAD`` as a key."""

    info: Any
    config_schema: Any = None
    config_secrets: Any = None
    macro_actions: Any = None
    script_api: Any = None
    extensions: Any = None
    #: Each place a source read could not evaluate, as ``PLUGIN_INFO['version']``
    #: or ``CONFIG_SCHEMA``, in the order met.
    unread: tuple[str, ...] = ()
    #: The class the declarations were read from.
    class_name: str | None = None

    @classmethod
    def of_class(cls, plugin_class: Any) -> Declared:
        return cls(
            *(getattr(plugin_class, name, None) for name in DECLARATION_NAMES),
            class_name=getattr(plugin_class, "__name__", None),
        )


#: ``host(info)``: why this machine cannot run the plugin, or None.
HostCheck = Callable[[dict], "str | None"]


@dataclass(frozen=True)
class _Judging:
    """One pass of the judge: the declarations, their PLUGIN_INFO, and the
    checks the caller can answer."""

    declared: Declared
    info: dict
    host: HostCheck | None
    handlers: HandlerProbe | None

    @property
    def plugin_id(self) -> Any:
        return info_value(self.info, "id", UNREAD)


def first_problem(
    declared: Declared,
    *,
    host: HostCheck | None = None,
    handlers: HandlerProbe | None = None,
) -> str | None:
    """The refusal the plugin's card shows, or None when every rule passes."""
    if not isinstance(declared.info, dict):
        return "Missing or invalid PLUGIN_INFO dict"
    judging = _Judging(declared, declared.info, host, handlers)
    for rule in _RULES:
        problem = rule(judging)
        if problem is not None:
            return problem
    return None


# --- PLUGIN_INFO ----------------------------------------------------------------


def _required_fields(j: _Judging) -> str | None:
    if UNREAD in j.info:  # keys the source computes may be the missing ones
        return None
    missing = REQUIRED_MANIFEST_FIELDS - set(j.info.keys())
    if missing:
        return f"Missing required fields: {sorted(missing)}"
    return None


def _license(j: _Judging) -> str | None:
    license_name = info_value(j.info, "license", "")
    if not _known(license_name):
        return None
    if not _is_mit_compatible(license_name):
        return f"License '{j.info.get('license')}' is not MIT-compatible"
    return None


def _is_mit_compatible(license_name: Any) -> bool:
    return isinstance(license_name, str) and license_name.lower().strip() in MIT_COMPATIBLE_LICENSES


def _category(j: _Judging) -> str | None:
    category = info_value(j.info, "category", "")
    if not _known(category):
        return None
    if not (isinstance(category, str) and category in VALID_CATEGORIES):
        return f"Invalid category '{category}', must be one of {sorted(VALID_CATEGORIES)}"
    return None


def _capabilities(j: _Judging) -> str | None:
    capabilities = info_value(j.info, "capabilities", [])
    if not _known(capabilities):
        return None
    if not isinstance(capabilities, (list, tuple)):
        return "capabilities must be a list"
    invalid = {_as_text(c) for c in capabilities if not _is_capability(c)}
    if invalid:
        return f"Unknown capabilities: {sorted(invalid)}"
    return None


def _is_capability(value: Any) -> bool:
    return isinstance(value, str) and value in VALID_CAPABILITIES


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else repr(value)


def _lacks_capability(info: dict, capability: str) -> bool:
    """Whether the plugin plainly does not declare ``capability``: False when
    its capabilities could not be read."""
    capabilities = info_value(info, "capabilities", [])
    return _known(capabilities) and capability not in capabilities


def _guest_alias(j: _Judging) -> str | None:
    alias = info_value(j.info, "guest_alias")
    if not _declares(alias):
        return None
    if not _is_url_segment(alias):
        return (
            f"Invalid guest_alias '{alias}': must be one lowercase URL "
            "segment (letters, digits, hyphens, underscores; max 32 chars)"
        )
    if alias in RESERVED_GUEST_ALIASES:
        return f"guest_alias '{alias}' is a reserved platform path"
    if _lacks_capability(j.info, "guest_endpoints"):
        return "guest_alias requires the guest_endpoints capability"
    return None


def _is_url_segment(alias: Any) -> bool:
    return isinstance(alias, str) and GUEST_ALIAS_RE.fullmatch(alias) is not None


def _network_ports(j: _Judging) -> str | None:
    """A port the plugin needs open in the HOST firewall: a request about the
    machine, so a bad declaration is refused outright rather than trimmed, and
    it requires the capability that says the plugin listens at all."""
    if "network_ports" not in j.info:
        return None
    entries = j.info["network_ports"]
    if not _known(entries):
        return None
    problem = network_ports_problem(entries)
    if problem is not None:
        return problem
    if entries and _lacks_capability(j.info, "network_listen"):
        return "network_ports requires the network_listen capability"
    return None


def network_ports_problem(entries: Any) -> str | None:
    """What is wrong with a ``network_ports`` declaration, or None.

    Strict, and refuses rather than dropping a bad entry: a plugin that asked
    for a port and silently did not get one would fail in the field, on a
    network nobody here can see, with the symptom appearing somewhere else
    entirely.
    """
    if not isinstance(entries, (list, tuple)):
        return "network_ports must be a list"
    seen: set[tuple[int, str]] = set()
    for entry in entries:
        problem = _port_entry_problem(entry, seen)
        if problem is not None:
            return problem
    return None


def _port_entry_problem(entry: Any, seen: set[tuple[int, str]]) -> str | None:
    if not isinstance(entry, dict):
        return f"network_ports entry must be a dict, got {type(entry).__name__}"
    for check in _PORT_ENTRY_CHECKS:
        problem = check(entry)
        if problem is not None:
            return problem
    key = (entry["port"], entry.get("protocol", "tcp"))
    if key in seen:
        return f"network_ports declares {key[0]}/{key[1]} twice"
    seen.add(key)
    return None


def _is_port_number(port: Any) -> bool:
    return isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535


def _port_number(entry: dict) -> str | None:
    port = entry.get("port")
    if not _is_port_number(port):
        return f"network_ports entry has an invalid port: {port!r}"
    if port in HOST_PORTS:
        return (
            f"network_ports may not request port {port} -- it belongs to the "
            "host, not to a plugin"
        )
    return None


def _port_protocol(entry: dict) -> str | None:
    proto = entry.get("protocol", "tcp")
    if proto not in PORT_PROTOCOLS:
        return (
            f"network_ports entry for port {entry['port']} has protocol {proto!r}; "
            f"must be one of {', '.join(PORT_PROTOCOLS)}"
        )
    return None


def _port_reason(entry: dict) -> str | None:
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return (
            f"network_ports entry for port {entry['port']}/{entry.get('protocol', 'tcp')} "
            "needs a `reason` -- it is what an administrator reads when deciding "
            "whether the rule may be removed"
        )
    return None


_PORT_ENTRY_CHECKS = (_port_number, _port_protocol, _port_reason)


def platform_supported(info: dict, platform_id: str) -> bool:
    """Whether a manifest's ``platforms`` (default ``["all"]``) includes
    ``platform_id``: True when they could not be read, False when they are not
    a list (a string is not read as one)."""
    platforms = info_value(info, "platforms", ["all"])
    if not _known(platforms):
        return True
    if not isinstance(platforms, (list, tuple)):
        return False
    return "all" in platforms or platform_id in platforms


def platform_problem(info: dict, platform_id: str) -> str | None:
    """The refusal for a plugin that does not run on ``platform_id``, or None."""
    if not platform_supported(info, platform_id):
        return (
            f"Plugin not compatible with current platform '{platform_id}'. "
            f"Supported: {info.get('platforms', ['all'])}"
        )
    return None


def _host(j: _Judging) -> str | None:
    return None if j.host is None else j.host(j.info)


# --- CONFIG_SCHEMA and CONFIG_SECRETS -------------------------------------------


def config_schema_problem(schema: Any) -> str | None:
    if _declares(schema) and not isinstance(schema, dict):
        return "CONFIG_SCHEMA must be a dict"
    return None


def _config_schema(j: _Judging) -> str | None:
    return config_schema_problem(j.declared.config_schema)


def config_secrets_problem(value: Any, schema: Any = None) -> str | None:
    """What is wrong with a ``CONFIG_SECRETS`` declaration, or None.

    A malformed one is refused: started anyway, its logins would stay in the
    project, which is what declaring them was meant to stop.
    """
    if not _declares(value):
        return None
    if not isinstance(value, dict):
        return "CONFIG_SECRETS must be a dict"
    for key, entry in value.items():
        problem = _secrets_entry_problem(key, entry, schema)
        if problem is not None:
            return problem
    return None


def _config_secrets(j: _Judging) -> str | None:
    return config_secrets_problem(j.declared.config_secrets, j.declared.config_schema)


def _secrets_entry_problem(key: Any, entry: Any, schema: Any) -> str | None:
    if not _is_config_key(key):
        return f"CONFIG_SECRETS key {key!r} must be a config key"
    if isinstance(schema, dict) and key in schema:
        return f"CONFIG_SECRETS['{key}'] is also a CONFIG_SCHEMA field; declare its logins in one place"
    if not isinstance(entry, dict):
        return f"CONFIG_SECRETS['{key}'] must be a dict"
    for check in _SECRETS_ENTRY_CHECKS:
        problem = check(key, entry)
        if problem is not None:
            return problem
    return None


def _is_config_key(key: Any) -> bool:
    return isinstance(key, str) and key != "" and not {"[", "]"} & set(key)


def _is_id_key(id_key: Any) -> bool:
    return isinstance(id_key, str) and id_key != "" and not {"=", "[", "]"} & set(id_key)


def _are_field_names(fields: Any) -> bool:
    """A non-empty list of distinct non-empty strings."""
    if not isinstance(fields, list) or not fields:
        return False
    names = [f for f in fields if isinstance(f, str) and f]
    return len(names) == len(fields) == len(set(names))


def _secrets_keys(key: str, entry: dict) -> str | None:
    unknown = set(entry) - CONFIG_SECRETS_KEYS
    if unknown:
        return f"CONFIG_SECRETS['{key}'] has unknown keys: {sorted(unknown)}"
    return None


def _secrets_id(key: str, entry: dict) -> str | None:
    if not _is_id_key(entry.get("id")):
        return f"CONFIG_SECRETS['{key}']['id'] must name the key holding each item's id"
    return None


def _secrets_fields(key: str, entry: dict) -> str | None:
    fields = entry.get("fields")
    if not _are_field_names(fields):
        return f"CONFIG_SECRETS['{key}']['fields'] must be a list of distinct field names"
    if entry["id"] in fields:
        return f"CONFIG_SECRETS['{key}']: the id key '{entry['id']}' cannot also be a login"
    return None


def _secrets_generated(key: str, entry: dict) -> str | None:
    if "generated" in entry and not isinstance(entry["generated"], bool):
        return f"CONFIG_SECRETS['{key}']['generated'] must be true or false"
    return None


_SECRETS_ENTRY_CHECKS = (_secrets_keys, _secrets_id, _secrets_fields, _secrets_generated)


# --- MACRO_ACTIONS, SCRIPT_API and EXTENSIONS -------------------------------------


def _macro_actions(j: _Judging) -> str | None:
    value = j.declared.macro_actions
    if not _declares(value) or not _known(j.plugin_id):
        return None
    problem = macro_actions_problem(value, j.plugin_id, j.handlers)
    return None if problem is None else f"MACRO_ACTIONS invalid: {problem}"


def _script_api(j: _Judging) -> str | None:
    value = j.declared.script_api
    if not _declares(value) or not _known(j.plugin_id):
        return None
    problem = script_api_problem(value, j.plugin_id, j.handlers)
    return None if problem is None else f"SCRIPT_API invalid: {problem}"


def extensions_problem(value: Any) -> str | None:
    """What is wrong with an ``EXTENSIONS`` declaration, or None."""
    if not isinstance(value, dict):
        return "EXTENSIONS must be a dict"
    for ext_type, ext_list in value.items():
        problem = _extension_type_problem(ext_type, ext_list)
        if problem:
            return problem
    return None


def _extension_type_problem(ext_type: Any, ext_list: Any) -> str | None:
    if ext_type not in VALID_EXTENSION_TYPES:
        return f"unknown extension type '{ext_type}' (allowed: {sorted(VALID_EXTENSION_TYPES)})"
    if not isinstance(ext_list, list):
        return f"extension type '{ext_type}' must be a list"
    id_field = EXTENSION_ID_FIELD[ext_type]
    seen_ids: set[str] = set()
    for i, ext in enumerate(ext_list):
        if not isinstance(ext, dict):
            return f"{ext_type}[{i}] must be a dict"
        ext_id = ext.get(id_field)
        if not ext_id or not isinstance(ext_id, str):
            return f"{ext_type}[{i}] missing '{id_field}' (string)"
        if ext_id in seen_ids:
            return f"{ext_type} has duplicate {id_field} '{ext_id}'"
        seen_ids.add(ext_id)
    return None


def _extensions(j: _Judging) -> str | None:
    value = j.declared.extensions
    if not _declares(value):
        return None
    problem = extensions_problem(value)
    return None if problem is None else f"EXTENSIONS invalid: {problem}"


#: The judge's rows, in the order a plugin is refused.
_RULES: tuple[Callable[[_Judging], "str | None"], ...] = (
    _required_fields,
    _license,
    _category,
    _capabilities,
    _guest_alias,
    _network_ports,
    _host,
    _config_schema,
    _config_secrets,
    _macro_actions,
    _script_api,
    _extensions,
)
