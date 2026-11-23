"""THE rules for a plugin's ``EXTENSIONS``: the table of extension types, what
an entry must be to be accepted, the page each context action is drawn on, and
the characters a plugin element's names may hold.

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
