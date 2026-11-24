"""THE rules for a plugin's ``EXTENSIONS``: the table of extension types, what
an entry must be to be accepted, the page each context action is drawn on, and
the characters a plugin element's names may hold. And THE rule for its
``SURFACE_LAYOUT``: the fields each type of layout may say, a routing matrix's
route key among them.

A plugin adds views, device panels, status cards, context actions and panel
elements to the IDE and the panel through ``EXTENSIONS``: a dict of those
types, each a list of entries. ``EXTENSION_ID_FIELD`` is the table, one row per
type in the order the types are served: the field whose value names an entry
(``id``, or a panel element's ``type``).

An entry is named by its plugin, its type and that value. The value must be
unique within the plugin's list of that type, and two plugins may use the same
one: the panel and the IDE find every entry by its plugin first (a panel
element by plugin id, then type). ``extensions_problem`` is the whole rule.
The start asks it, through ``plugin_declarations``' ``EXTENSIONS`` row, and
refuses a plugin it answers for; the serve (``core/plugin_extensions.py``)
asks the same question and serves every entry of a declaration it accepts and
none of one it refuses.

``CONTEXT_ACTION_PAGES``: the page the IDE draws a context action on, by its
``context``. A ``device`` action is drawn on the page of each device its
``match`` names, and sent that device's id; a ``global`` or ``plugin`` action
on its plugin's own page, sent nothing. The IDE's copy
(``pluginExtensionHelpers.ts``, ``CONTEXT_ACTION_PAGES``) is held to it by
``tests/test_plugin_extension_rules.py``. The start does not ask it: an action
with another context starts and is drawn nowhere.

``is_panel_element_name``: the characters the panel accepts in a plugin
element's two names, its plugin id and its type (letters, digits, underscore
and hyphen). The panel loads ``/api/plugins/<id>/panel/<type>.html`` and draws
its unconfigured placeholder when either name holds anything else. The page
review (``ui/page_references.py``) asks this rule to name such an element; the
panel's copy (``web/panel/panel.js``, ``renderPluginElement``) is held to it by
``tests/test_plugin_extension_rules.py``. Every plugin id passes it
(``plugin_declarations.is_plugin_id`` is narrower). The start does not ask it
of a type: a plugin that declares a type the panel cannot draw starts, and the
page review names each element of that type.

``SURFACE_LAYOUT_FIELDS``: what a plugin's ``SURFACE_LAYOUT`` may say, by its
``type``, each field and the kind of value it takes; ``surface_layout_problem``
is the whole rule. A ``matrix`` layout draws its rows and columns from the
state keys two patterns match, and names one route key per row
(``route_state_pattern``, with ``{row}``) whose value names the source routed
to it; a crosspoint is routed when that value names the source its column's
own value names, compared by the matrix route rule (``ui/matrix_model.py``'s
``route_matches``) in the layout's ``route_match`` mode
(``SURFACE_ROUTE_MATCH_MODES``, its words, held to ``ROUTE_MATCH_MODES``
there; ``SURFACE_ROUTE_MATCH_DEFAULT`` when it names none). The IDE draws the
layout (``components/plugins/surface/``): its ``SurfaceLayout`` type and its
default mode are held to the table by ``tests/test_plugin_extension_rules.py``.
openavc-plugins' ``validate.py`` asks the rule, through
``plugin_declarations``. The start does not: a plugin installed with a layout
written before a field changed still starts, and its surface draws what this
release can read of it.

``plugin_namespace``: THE prefix every name a plugin owns starts with,
``plugin.<id>.``: the state keys it may write, the events it emits, the tasks
it runs, and so the keys an entry's state fields may name. Every builder asks
it: ``plugin_api``, the loader, the serve, the context action route; the IDE's
copy (``pluginExtensionHelpers.ts``, ``pluginNamespace``) and the panel's
(``web/panel/modules/frames.js``, ``pluginNamespace``) are held to it, and no
other place builds it, by ``tests/test_plugin_extension_rules.py``.

``EXTENSION_FIELDS``: the fields an entry of each type may hold, the kind
each takes and who reads it; ``EXTENSION_RENDERERS``, the words a
``renderer`` takes. ``extension_entries_problem`` is the rule:
``validate.py`` refuses with it. The start does not ask it (a plugin installed
before a field changed still starts); the serve holds each state field to the
plugin's namespace (``state_pattern``, a status card's metric keys, a panel
element setting's ``options_source``). The IDE's ``PluginExtension`` and its
renderers are held to the table by ``tests/test_plugin_extension_rules.py``.

Purity contract: standard library only, like ``plugin_declarations``, which
imports it; openavc-plugins' ``validate.py`` runs the vendored copy
(``scripts/vendor_platform_rules.py`` there).
"""

from __future__ import annotations

import re
from typing import Any

#: Each extension type and the field that names an entry within it, in the
#: order the types are served (core/plugin_extensions.py).
EXTENSION_ID_FIELD = {
    "views": "id",
    "device_panels": "id",
    "status_cards": "id",
    "context_actions": "id",
    "panel_elements": "type",
}
VALID_EXTENSION_TYPES = set(EXTENSION_ID_FIELD)

def plugin_namespace(plugin_id: str) -> str:
    """The prefix of every state key, event and task name the plugin owns."""
    return f"plugin.{plugin_id}."


def in_plugin_namespace(key: Any, plugin_id: str) -> bool:
    """Whether a state key, event or task name is the plugin's own."""
    return isinstance(key, str) and key.startswith(plugin_namespace(plugin_id))


#: The page the IDE draws a context action on, by the action's ``context``.
CONTEXT_ACTION_PAGES = {
    "global": "plugin",
    "device": "device",
    "plugin": "plugin",
}

#: What the panel accepts in a plugin element's plugin id and type, matched
#: whole (``fullmatch``): ``panel.js`` writes the same class between ``^`` and
#: ``$``, which in JavaScript refuses a line break after the name.
PANEL_ELEMENT_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")


def is_panel_element_name(value: Any) -> bool:
    """Whether the panel draws a plugin element with this plugin id or type."""
    return isinstance(value, str) and PANEL_ELEMENT_NAME_RE.fullmatch(value) is not None


#: The words a matrix layout's ``route_match`` may say: the modes of the
#: matrix route rule (``ROUTE_MATCH_MODES`` in ``ui/matrix_model.py``), named
#: here rather than imported, since this module is vendored without it.
SURFACE_ROUTE_MATCH_MODES = ("port", "exact")
#: The mode a matrix layout that names none is compared in: the one that never
#: lights a crosspoint from a number two source names share.
SURFACE_ROUTE_MATCH_DEFAULT = "exact"

_PAGES = {"supports_pages": "flag", "max_pages": "whole"}

#: What a ``SURFACE_LAYOUT`` may say, by its ``type``: each field and its kind
#: (``_KINDS``). ``type`` itself is the table's key.
SURFACE_LAYOUT_FIELDS: dict[str, dict[str, str]] = {
    "grid": {
        "rows": "whole", "columns": "whole", "key_size_px": "whole", "key_spacing_px": "whole",
        **_PAGES,
        # A device-backed surface: the IDE draws only real units.
        "requires_device": "flag", "device_label": "text", "virtual_models": "texts",
        "network": "flag",
    },
    "strip": {"columns": "whole", "controls": "controls", **_PAGES},
    "custom": {"width_px": "whole", "height_px": "whole", "controls": "controls", **_PAGES},
    "matrix": {
        "rows_label": "text", "columns_label": "text",
        "rows_state_pattern": "key pattern", "columns_state_pattern": "key pattern",
        "route_state_pattern": "row key", "route_match": "route match",
        "presets": "flag",
    },
}

#: The fields a layout of a type cannot be drawn without, and what each is.
_REQUIRED_LAYOUT_FIELDS = {
    "matrix": {
        "rows_state_pattern": "the state keys, with *, the rows are drawn from",
        "columns_state_pattern": "the state keys, with *, the columns are drawn from",
        "route_state_pattern": "the state key, with {row}, whose value names the source routed to each row",
    },
}


def _is_flag(value: Any) -> bool:
    return isinstance(value, bool)


def _is_whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_text(value: Any) -> bool:
    return isinstance(value, str)


def _is_texts(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_controls(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _is_key_pattern(value: Any) -> bool:
    return isinstance(value, str) and "*" in value


def _is_row_key(value: Any) -> bool:
    return isinstance(value, str) and "{row}" in value


def _is_route_match(value: Any) -> bool:
    return isinstance(value, str) and value in SURFACE_ROUTE_MATCH_MODES


#: Each kind of layout field: the words for it and whether a value is one.
_KINDS: dict[str, tuple[str, Any]] = {
    "flag": ("true or false", _is_flag),
    "whole": ("a whole number", _is_whole),
    "text": ("text", _is_text),
    "texts": ("a list of text", _is_texts),
    "controls": ("a list of dicts", _is_controls),
    "key pattern": ("a state key with *", _is_key_pattern),
    "row key": ("a state key with {row}", _is_row_key),
    "route match": (
        "one of " + ", ".join(repr(m) for m in sorted(SURFACE_ROUTE_MATCH_MODES)),
        _is_route_match,
    ),
}


def surface_layout_problem(layout: Any) -> str | None:
    """What keeps the IDE from drawing a ``SURFACE_LAYOUT`` as written, or
    None. A plugin that declares none has nothing to draw."""
    if layout is None:
        return None
    if not isinstance(layout, dict):
        return "SURFACE_LAYOUT must be a dict"
    layout_type = layout.get("type")
    if layout_type not in SURFACE_LAYOUT_FIELDS:
        known = ", ".join(repr(t) for t in sorted(SURFACE_LAYOUT_FIELDS))
        return f"SURFACE_LAYOUT type {layout_type!r} is not one of {known}"
    return (
        _unknown_layout_field(layout, layout_type)
        or _missing_layout_field(layout, layout_type)
        or _layout_value_problem(layout, SURFACE_LAYOUT_FIELDS[layout_type])
    )


def _unknown_layout_field(layout: dict, layout_type: str) -> str | None:
    fields = SURFACE_LAYOUT_FIELDS[layout_type]
    stray = next((f for f in layout if f != "type" and f not in fields), None)
    if stray is None:
        return None
    return f"SURFACE_LAYOUT has {stray!r}, which a {layout_type} layout does not have"


def _missing_layout_field(layout: dict, layout_type: str) -> str | None:
    required = _REQUIRED_LAYOUT_FIELDS.get(layout_type, {})
    missing = next((f for f in required if f not in layout), None)
    if missing is None:
        return None
    return f"SURFACE_LAYOUT ({layout_type}) needs {missing!r}: {required[missing]}"


def _layout_value_problem(layout: dict, fields: dict[str, str]) -> str | None:
    for field, kind in fields.items():
        words, fits = _KINDS[kind]
        if field in layout and not fits(layout[field]):
            return f"SURFACE_LAYOUT {field!r} must be {words}, not {layout[field]!r}"
    return None


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


# --- The fields of an entry ------------------------------------------------

#: The words a ``renderer`` takes, by type: what the IDE draws a view or a
#: device panel as, and a panel element's frame.
EXTENSION_RENDERERS = {
    "views": ("surface", "state_table", "log", "video_streams"),
    "device_panels": ("state_table", "log"),
    "panel_elements": ("iframe",),
}

_SHOWN = {"label": "text", "icon": "text"}

#: The fields an entry of each type may hold, its naming field among them,
#: and the kind each takes (``_ENTRY_KINDS``). The serve adds ``plugin_id``
#: and ``plugin_name``. Who reads them:
#: - the IDE: every field of a view, a device panel, a status card and a
#:   context action, a panel element's ``label``, ``default_size`` and
#:   ``config_schema`` (the UI Builder);
#: - the panel: a panel element's ``sandbox_permissions``, ``allow_features``
#:   (cut down by the serve first) and ``ext_auth`` (a token for the plugin's
#:   own routes, handed to the frame);
#: - ``renderer`` on a panel element is always ``iframe``, and nothing reads it.
EXTENSION_FIELDS: dict[str, dict[str, str]] = {
    "views": {"id": "text", **_SHOWN, "renderer": "renderer", "state_pattern": "state pattern"},
    "device_panels": {
        "id": "text", **_SHOWN, "match": "match", "renderer": "renderer",
        "state_pattern": "state pattern",
    },
    "status_cards": {"id": "text", **_SHOWN, "metrics": "metrics"},
    "context_actions": {
        "id": "text", **_SHOWN, "context": "context", "event": "text", "confirm": "text",
        "match": "match",
    },
    "panel_elements": {
        "type": "element name", "label": "text", "renderer": "renderer",
        "default_size": "size", "config_schema": "element settings",
        "sandbox_permissions": "texts", "allow_features": "texts", "ext_auth": "flag",
    },
}

#: The fields an entry of a type is not drawn without.
_REQUIRED_ENTRY_FIELDS = {"views": ("renderer",), "device_panels": ("renderer",)}


def _metrics_problem(value: Any, plugin_id: str) -> str | None:
    if not isinstance(value, list) or not all(isinstance(m, dict) for m in value):
        return "must be a list of dicts"
    for i, metric in enumerate(value):
        if not in_plugin_namespace(metric.get("key"), plugin_id):
            return (f"[{i}] key {metric.get('key')!r} is not one of the plugin's own state "
                    f"keys ({plugin_namespace(plugin_id)}*)")
    return None


def _element_settings_problem(value: Any, plugin_id: str) -> str | None:
    if not isinstance(value, list) or not all(isinstance(f, dict) for f in value):
        return "must be a list of dicts"
    for i, setting in enumerate(value):
        if not isinstance(setting.get("key"), str) or not setting["key"]:
            return f"[{i}] needs a 'key' (string)"
        source = setting.get("options_source")
        if source is not None and not in_plugin_namespace(source, plugin_id):
            return (f"[{i}] options_source {source!r} is not one of the plugin's own state "
                    f"keys ({plugin_namespace(plugin_id)}*)")
    return None


def _is_size(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == {"w", "h"}
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
                for v in value.values())
    )


def _is_match(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(v, str) for v in value.values())


def _simple(words: str, fits: Any):
    return lambda value, _plugin_id: None if fits(value) else f"must be {words}"


#: Each kind of entry field: what is wrong with a value, or None. Each takes
#: the value and the plugin's id, since a state field names the plugin's keys.
_ENTRY_KINDS: dict[str, Any] = {
    "text": _simple("text", _is_text),
    "flag": _simple("true or false", _is_flag),
    "texts": _simple("a list of text", _is_texts),
    "size": _simple('{"w": N, "h": N}, each above 0', _is_size),
    "match": _simple("a dict of text", _is_match),
    "element name": _simple("letters, digits, _ and - only", is_panel_element_name),
    "context": _simple(
        "one of " + ", ".join(repr(c) for c in CONTEXT_ACTION_PAGES),
        lambda v: v in CONTEXT_ACTION_PAGES,
    ),
    "state pattern": lambda v, pid: None if in_plugin_namespace(v, pid) else (
        f"must name the plugin's own state ({plugin_namespace(pid)}*)"
    ),
    "metrics": _metrics_problem,
    "element settings": _element_settings_problem,
}


def extension_entries_problem(extensions: Any, plugin_id: str) -> str | None:
    """What in an ``EXTENSIONS`` the start accepts is drawn otherwise than
    written, or None: a field its type does not have, a value of the wrong
    kind, a state field outside the plugin's namespace. The start does not ask
    it (an installed plugin keeps starting); ``validate.py`` refuses with it,
    and the serve holds each state field to the namespace."""
    if extensions_problem(extensions) is not None:
        return None
    for ext_type, entries in extensions.items():
        for i, entry in enumerate(entries):
            problem = _entry_problem(ext_type, entry, plugin_id)
            if problem:
                return f"{ext_type}[{i}] {problem}"
    return None


def _entry_problem(ext_type: str, entry: dict, plugin_id: str) -> str | None:
    fields = EXTENSION_FIELDS[ext_type]
    stray = next((f for f in entry if f not in fields), None)
    if stray is not None:
        return f"has {stray!r}, which a {ext_type} entry does not have"
    missing = next((f for f in _REQUIRED_ENTRY_FIELDS.get(ext_type, ()) if f not in entry), None)
    if missing is not None:
        return f"needs {missing!r}"
    for field in (f for f in fields if f in entry):
        problem = _field_problem(ext_type, fields[field], entry[field], plugin_id)
        if problem:
            # A list's problem names its item: metrics[0] key ...
            return f"{field}{problem}" if problem.startswith("[") else f"{field!r} {problem}"
    return None


def _field_problem(ext_type: str, kind: str, value: Any, plugin_id: str) -> str | None:
    if kind != "renderer":
        return _ENTRY_KINDS[kind](value, plugin_id)
    words = EXTENSION_RENDERERS[ext_type]
    return None if value in words else "must be one of " + ", ".join(repr(w) for w in words)
