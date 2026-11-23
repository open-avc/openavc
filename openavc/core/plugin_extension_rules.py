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


def _is_whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_list_of(value: Any, kind: type) -> bool:
    return isinstance(value, list) and all(isinstance(item, kind) for item in value)


#: Each kind of layout field: the words for it and whether a value is one.
_KINDS: dict[str, tuple[str, Any]] = {
    "flag": ("true or false", lambda v: isinstance(v, bool)),
    "whole": ("a whole number", _is_whole),
    "text": ("text", lambda v: isinstance(v, str)),
    "texts": ("a list of text", lambda v: _is_list_of(v, str)),
    "controls": ("a list of dicts", lambda v: _is_list_of(v, dict)),
    "key pattern": ("a state key with *", lambda v: isinstance(v, str) and "*" in v),
    "row key": ("a state key with {row}", lambda v: isinstance(v, str) and "{row}" in v),
    "route match": (
        "one of " + ", ".join(repr(m) for m in sorted(SURFACE_ROUTE_MATCH_MODES)),
        lambda v: isinstance(v, str) and v in SURFACE_ROUTE_MATCH_MODES,
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
