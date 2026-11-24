"""What the panel and the IDE are served from the running plugins' EXTENSIONS.

A plugin adds views, device panels, status cards, context actions and panel
elements through ``EXTENSIONS``: a dict of those types, each a list of entries.

- What a declaration must be is ``core/plugin_extension_rules.py``'s
  (``extensions_problem``), which the start asks and refuses a malformed one
  with, so a bad plugin fails to enable rather than blanking the plugin UI.
  ``validate_extensions`` here is that rule under the loader's
  ``(valid, error)`` signature.
- ``collect_extensions`` builds the ``/api/plugins/extensions`` answer from the
  running plugins. It serves exactly what the start accepts: every entry of a
  declaration the rule accepts, none of one it refuses (logged), and two
  plugins' entries of the same id both, since every reader finds an entry by
  its plugin first. One plugin's declaration never takes the endpoint down for
  every other plugin. Each state field of an entry is held inside its plugin's
  namespace (``plugin_extension_rules.plugin_namespace``), so a plugin's
  sidebar, card or element settings cannot read unrelated state: a
  ``state_pattern`` outside it becomes the namespace's own, and a status
  card's metric or a panel element setting's ``options_source`` outside it
  is left off, logged. A panel element's iframe grant is cut down to
  ``ALLOWED_SANDBOX_PERMISSIONS`` and ``ALLOWED_ALLOW_FEATURES`` (the panel
  sets the iframe's ``sandbox`` and ``allow`` attributes from it). What a
  frame may reach is the element's own grant, never the plugin's
  capabilities, so those are not served.

``sandbox_permissions`` is that same cut-down sandbox grant, summed over the
panel elements a plugin would be served: what the plugin list shows as part of
the trust given at install.

Logs as ``openavc.core.plugin_loader``: the Programmer's plugin page shows
that logger's lines beside the plugin's own.
"""

from dataclasses import dataclass
from typing import Any

from openavc.core.plugin_extension_rules import (
    EXTENSION_ID_FIELD,
    extensions_problem,
    in_plugin_namespace,
    plugin_namespace,
)
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.plugin_loader")

# The iframe sandbox tokens a panel element may opt into. ``allow-scripts`` is
# always set and not listed. Tokens that would escape the sandbox
# (``allow-popups-to-escape-sandbox``, ``allow-top-navigation``,
# ``allow-pointer-lock``) are left out on purpose.
ALLOWED_SANDBOX_PERMISSIONS = frozenset({
    "allow-same-origin",
    "allow-forms",
    "allow-modals",
    "allow-popups",
})

# The Permissions-Policy features a panel element may opt into through the
# iframe ``allow`` attribute. Sensor access (camera, microphone, geolocation
# and the like) is left out: nothing needs it, and its privacy cost is not
# obvious to the person granting it.
ALLOWED_ALLOW_FEATURES = frozenset({
    "autoplay",
    "encrypted-media",
    "fullscreen",
    "picture-in-picture",
})


def validate_extensions(
    extensions: Any, plugin_id: str, plugin_class: type
) -> tuple[bool, str]:
    """Validate a plugin's EXTENSIONS declaration (the rule is
    ``plugin_extension_rules.extensions_problem``). Returns (valid, error_message)."""
    problem = extensions_problem(extensions)
    return (False, problem) if problem else (True, "")


@dataclass(frozen=True)
class _Source:
    """The running plugin an entry is served for."""

    plugin_id: str
    name: str

    @property
    def namespace(self) -> str:
        return plugin_namespace(self.plugin_id)


def collect_extensions(instances: dict[str, Any]) -> dict[str, list]:
    """Every running plugin's extensions by type, as the panel and the IDE receive them.

    ``instances`` is the loader's running plugins (plugin id -> instance), in
    the order they are served. A plugin whose collection raises is logged and
    skipped; what it had already contributed stays.
    """
    result: dict[str, list] = {ext_type: [] for ext_type in EXTENSION_ID_FIELD}
    for plugin_id, instance in instances.items():
        try:
            _collect_plugin(plugin_id, type(instance), result)
        except Exception:  # One bad plugin must not blank the whole endpoint
            log.exception("Failed to collect extensions for plugin '%s'", plugin_id)
    return result


def _collect_plugin(plugin_id: str, plugin_class: type, result: dict[str, list]) -> None:
    extensions = _accepted_extensions(plugin_id, plugin_class)
    if not extensions:
        return
    source = _Source(plugin_id, plugin_class.PLUGIN_INFO.get("name", plugin_id))
    for ext_type, entries in result.items():
        entries.extend(_served_entry(source, ext_type, ext) for ext in extensions.get(ext_type, []))


def _accepted_extensions(plugin_id: str, plugin_class: type) -> dict | None:
    """The plugin's EXTENSIONS when the start's rule accepts them, else None."""
    extensions = getattr(plugin_class, "EXTENSIONS", None)
    if extensions is None:
        return None
    problem = extensions_problem(extensions)
    if problem:
        log.warning(
            "Plugin '%s' EXTENSIONS invalid: %s; serving none of them", plugin_id, problem,
        )
        return None
    return extensions


def _served_entry(source: _Source, ext_type: str, ext: dict) -> dict:
    safe_ext = {**ext, "plugin_id": source.plugin_id, "plugin_name": source.name}
    pattern = safe_ext.get("state_pattern")
    if pattern is not None:
        safe_ext["state_pattern"] = sanitize_state_pattern(
            pattern, source.plugin_id, source.namespace
        )
    if "metrics" in safe_ext:
        safe_ext["metrics"] = _own_keys(source.plugin_id, safe_ext["metrics"], "key", "metric")
    if ext_type == "panel_elements":
        _serve_panel_element(source.plugin_id, safe_ext)
    return safe_ext


def _serve_panel_element(plugin_id: str, safe_ext: dict) -> None:
    safe_ext["sandbox_permissions"] = _sandbox_tokens(plugin_id, safe_ext)
    safe_ext["allow_features"] = sanitize_token_list(
        safe_ext.get("allow_features"), ALLOWED_ALLOW_FEATURES, plugin_id, "allow_features",
    )
    if "config_schema" in safe_ext:
        safe_ext["config_schema"] = _own_options(plugin_id, safe_ext["config_schema"])


def _dicts(plugin_id: str, items: Any, what: str) -> list[dict]:
    if not isinstance(items, list):
        log.warning("Plugin '%s' %s list is not a list (%r); serving none", plugin_id, what, items)
        return []
    return [item for item in items if isinstance(item, dict)]


def _own_keys(plugin_id: str, items: Any, field: str, what: str) -> list[dict]:
    """The items whose ``field`` is one of the plugin's own state keys."""
    kept = []
    for item in _dicts(plugin_id, items, what):
        if in_plugin_namespace(item.get(field), plugin_id):
            kept.append(item)
        else:
            log.warning(
                "Plugin '%s' %s %s %r is outside its namespace; leaving it off",
                plugin_id, what, field, item.get(field),
            )
    return kept


def _own_options(plugin_id: str, settings: Any) -> list[dict]:
    """A panel element's settings, each ``options_source`` outside the
    plugin's namespace left off (the setting stays, with no options read)."""
    served = []
    for setting in _dicts(plugin_id, settings, "config_schema"):
        source = setting.get("options_source")
        if source is not None and not in_plugin_namespace(source, plugin_id):
            log.warning(
                "Plugin '%s' setting %r options_source %r is outside its namespace; leaving it off",
                plugin_id, setting.get("key"), source,
            )
            setting = {k: v for k, v in setting.items() if k != "options_source"}
        served.append(setting)
    return served


def sandbox_permissions(plugin_id: str, plugin_class: type) -> list[str]:
    """The allowed sandbox tokens across a plugin's panel elements, each once, in
    the order first declared: what the panel would actually apply, not the raw
    declaration."""
    return list(dict.fromkeys(
        token
        for ext in _panel_elements(plugin_class)
        for token in _sandbox_tokens(plugin_id, ext)
    ))


def _panel_elements(plugin_class: type) -> list[dict]:
    """The panel elements the plugin would be served: none of a declaration
    the start's rule refuses."""
    extensions = getattr(plugin_class, "EXTENSIONS", None)
    if extensions is None or extensions_problem(extensions):
        return []
    return extensions.get("panel_elements", [])


def _sandbox_tokens(plugin_id: str, ext: dict) -> list[str]:
    return sanitize_token_list(
        ext.get("sandbox_permissions"), ALLOWED_SANDBOX_PERMISSIONS,
        plugin_id, "sandbox_permissions",
    )


def sanitize_token_list(
    raw: Any, allowed: frozenset[str], plugin_id: str, field_name: str
) -> list[str]:
    """Filter a string list against an allowed set, dropping anything not on it.

    Returns an empty list for missing, non-list, or non-string-element input.
    Each token dropped is logged, so a plugin author can find a typo and an
    attempt past the policy shows in the system log.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        log.warning(
            "Plugin '%s' %s is not a list (%r); ignoring",
            plugin_id, field_name, raw,
        )
        return []
    result: list[str] = []
    for token in raw:
        if _allowed_token(token, allowed, plugin_id, field_name) and token not in result:
            result.append(token)
    return result


def _allowed_token(token: Any, allowed: frozenset[str], plugin_id: str, field_name: str) -> bool:
    if not isinstance(token, str):
        log.warning(
            "Plugin '%s' %s contains non-string token %r; ignoring",
            plugin_id, field_name, token,
        )
        return False
    if token not in allowed:
        log.warning(
            "Plugin '%s' %s requests unsupported token %r; ignoring "
            "(allowed: %s)",
            plugin_id, field_name, token, sorted(allowed),
        )
        return False
    return True


def sanitize_state_pattern(pattern: Any, plugin_id: str, namespace_prefix: str) -> str:
    """Force a plugin's state_pattern into its own namespace.

    Returns the pattern, trimmed, when it is inside the namespace (placeholders
    such as ``{device_id}`` included); otherwise the default
    ``plugin.<id>.*``, with a warning when the pattern was not blank.
    """
    default_pattern = f"{namespace_prefix}*"
    if not isinstance(pattern, str):
        log.warning(
            "Plugin '%s' state_pattern is not a string (%r); using default %r",
            plugin_id, pattern, default_pattern,
        )
        return default_pattern
    trimmed = pattern.strip()
    if not trimmed:
        return default_pattern
    if not trimmed.startswith(namespace_prefix):
        log.warning(
            "Plugin '%s' state_pattern %r escapes its namespace; "
            "rewriting to %r",
            plugin_id, trimmed, default_pattern,
        )
        return default_pattern
    return trimmed
