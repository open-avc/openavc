"""What a plugin's EXTENSIONS may declare, and what the panel and the IDE are served from them.

A plugin adds views, device panels, status cards, context actions and panel
elements through ``EXTENSIONS``: a dict of those types (``EXTENSION_ID_FIELD``,
which also gives each type's identifying field and the order they are served
in), each a list of entries. Both halves of the rule are here:

- ``validate_extensions`` refuses a malformed declaration when the plugin
  starts, so a bad plugin fails to enable rather than blanking the plugin UI.
- ``collect_extensions`` builds the ``/api/plugins/extensions`` answer from the
  running plugins and checks every entry's shape again, skipping a bad one with
  a warning: one plugin's entry must never take the endpoint down for every
  other plugin. The first plugin to claim an id keeps it. Each entry's
  ``state_pattern`` is held inside its plugin's ``plugin.<id>.`` namespace, so a
  plugin's sidebar cannot read unrelated state. A panel element's iframe grant
  is cut down to ``ALLOWED_SANDBOX_PERMISSIONS`` and ``ALLOWED_ALLOW_FEATURES``
  (the panel sets the iframe's ``sandbox`` and ``allow`` attributes from it)
  and carries the plugin's capabilities, which the panel's iframe bridge checks
  a request against.

``sandbox_permissions`` is that same cut-down sandbox grant, summed over a
plugin's panel elements: what the plugin list shows as part of the trust given
at install. openavc-plugins' ``validate.py`` repeats the type table by hand.

Logs as ``openavc.core.plugin_loader``: the Programmer's plugin page shows
that logger's lines beside the plugin's own.
"""

from dataclasses import dataclass
from typing import Any

from openavc.utils.logger import get_logger

log = get_logger("openavc.core.plugin_loader")

# Each extension type, the field that identifies an entry within it, and the
# order the types are served in. Panel elements are keyed by their element type.
EXTENSION_ID_FIELD = {
    "views": "id",
    "device_panels": "id",
    "status_cards": "id",
    "context_actions": "id",
    "panel_elements": "type",
}
VALID_EXTENSION_TYPES = set(EXTENSION_ID_FIELD)

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
    """Validate a plugin's EXTENSIONS declaration. Returns (valid, error_message)."""
    if not isinstance(extensions, dict):
        return False, "EXTENSIONS must be a dict"
    for ext_type, ext_list in extensions.items():
        problem = _declared_type_problem(ext_type, ext_list)
        if problem:
            return False, problem
    return True, ""


def _declared_type_problem(ext_type: Any, ext_list: Any) -> str | None:
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


@dataclass(frozen=True)
class _Source:
    """The running plugin an entry is served for."""

    plugin_id: str
    name: str
    info: dict

    @property
    def namespace(self) -> str:
        return f"plugin.{self.plugin_id}."


def collect_extensions(instances: dict[str, Any]) -> dict[str, list]:
    """Every running plugin's extensions by type, as the panel and the IDE receive them.

    ``instances`` is the loader's running plugins (plugin id -> instance), in
    the order they are served. A plugin whose collection raises is logged and
    skipped; what it had already contributed stays.
    """
    result: dict[str, list] = {ext_type: [] for ext_type in EXTENSION_ID_FIELD}
    # (ext_type, id) pairs already served: the first plugin to claim an id wins.
    seen_ids: set[tuple[str, str]] = set()
    for plugin_id, instance in instances.items():
        try:
            _collect_plugin(plugin_id, type(instance), result, seen_ids)
        except Exception:  # One bad plugin must not blank the whole endpoint
            log.exception("Failed to collect extensions for plugin '%s'", plugin_id)
    return result


def _collect_plugin(
    plugin_id: str, plugin_class: type, result: dict[str, list], seen_ids: set
) -> None:
    extensions = _declared_extensions(plugin_id, plugin_class)
    if extensions is None:
        return
    info = plugin_class.PLUGIN_INFO
    source = _Source(plugin_id, info.get("name", plugin_id), info)
    for ext_type in result:
        for ext in _declared_entries(plugin_id, extensions, ext_type):
            if _servable(source, ext_type, ext, seen_ids):
                result[ext_type].append(_served_entry(source, ext_type, ext))


def _declared_extensions(plugin_id: str, plugin_class: type) -> dict | None:
    extensions = getattr(plugin_class, "EXTENSIONS", None)
    if extensions is None:
        return None
    if not isinstance(extensions, dict):
        log.warning(
            "Plugin '%s' EXTENSIONS is not a dict (%s); skipping",
            plugin_id, type(extensions).__name__,
        )
        return None
    return extensions


def _declared_entries(plugin_id: str, extensions: dict, ext_type: str) -> list:
    ext_list = extensions.get(ext_type, [])
    if not isinstance(ext_list, list):
        log.warning(
            "Plugin '%s' EXTENSIONS['%s'] is not a list; skipping",
            plugin_id, ext_type,
        )
        return []
    return ext_list


def _servable(source: _Source, ext_type: str, ext: Any, seen_ids: set) -> bool:
    """Whether an entry is served: a dict, and no earlier plugin's id. Claims its id."""
    if not isinstance(ext, dict):
        log.warning(
            "Plugin '%s' %s entry is not a dict; skipping", source.plugin_id, ext_type,
        )
        return False
    ext_id = ext.get(EXTENSION_ID_FIELD[ext_type])
    if not (isinstance(ext_id, str) and ext_id):
        return True
    key = (ext_type, ext_id)
    if key in seen_ids:
        log.warning(
            "Plugin '%s' %s '%s' duplicates an already-registered id; skipping",
            source.plugin_id, ext_type, ext_id,
        )
        return False
    seen_ids.add(key)
    return True


def _served_entry(source: _Source, ext_type: str, ext: dict) -> dict:
    safe_ext = {**ext, "plugin_id": source.plugin_id, "plugin_name": source.name}
    pattern = safe_ext.get("state_pattern")
    if pattern is not None:
        safe_ext["state_pattern"] = sanitize_state_pattern(
            pattern, source.plugin_id, source.namespace
        )
    if ext_type == "panel_elements":
        safe_ext["sandbox_permissions"] = _sandbox_tokens(source.plugin_id, safe_ext)
        safe_ext["allow_features"] = sanitize_token_list(
            safe_ext.get("allow_features"), ALLOWED_ALLOW_FEATURES,
            source.plugin_id, "allow_features",
        )
        safe_ext["capabilities"] = list(source.info.get("capabilities", []))
    return safe_ext


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
    extensions = getattr(plugin_class, "EXTENSIONS", None)
    if not isinstance(extensions, dict):
        return []
    ext_list = extensions.get("panel_elements", [])
    if not isinstance(ext_list, list):
        return []
    return [ext for ext in ext_list if isinstance(ext, dict)]


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
