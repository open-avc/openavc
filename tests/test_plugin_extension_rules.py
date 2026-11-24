"""The rules for a plugin's EXTENSIONS and its SURFACE_LAYOUT
(``core/plugin_extension_rules.py``), and the sides that ask them: the start,
the serve, the page review, the panel and the IDE."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openavc.core import plugin_declarations
from openavc.core.plugin_extension_rules import (
    CONTEXT_ACTION_PAGES,
    EXTENSION_FIELDS,
    EXTENSION_ID_FIELD,
    EXTENSION_RENDERERS,
    PANEL_ELEMENT_NAME_RE,
    SURFACE_LAYOUT_FIELDS,
    SURFACE_ROUTE_MATCH_DEFAULT,
    SURFACE_ROUTE_MATCH_MODES,
    extension_entries_problem,
    extensions_problem,
    in_plugin_namespace,
    is_panel_element_name,
    plugin_namespace,
    surface_layout_problem,
)
from openavc.core.plugin_extensions import collect_extensions
from openavc.ui.matrix_model import ROUTE_MATCH_MODES
from openavc.ui.page_references import plugin_element_finding

PANEL_JS = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "panel.js"


@pytest.mark.parametrize("name", ["meter", "video_stream", "Meter-2", "a", "acme_widget"])
def test_a_name_the_panel_draws(name):
    assert is_panel_element_name(name)


@pytest.mark.parametrize("name", [
    "", "my meter", "acme:meter", "meter\n", "\nmeter", "meter.html", "../meter", "mètre", None, 5,
])
def test_a_name_the_panel_does_not_draw(name):
    assert not is_panel_element_name(name)


def test_the_panel_holds_a_plugin_elements_names_to_the_same_characters():
    """``renderPluginElement`` tests the plugin id and the type against one
    regex literal, ``/^[<class>]+$/``, and draws the unconfigured placeholder
    when either fails. Its class must take exactly the characters the rule's
    does, however the ranges are written."""
    source = PANEL_JS.read_text(encoding="utf-8")
    body = source[source.index("    renderPluginElement(element) {"):]
    body = body[:body.index("\n    }\n")]
    literals = re.findall(r"const validIdPattern = /\^(\[[^\]]+\])\+\$/;", body)
    assert len(literals) == 1, "panel.js renderPluginElement's name check is not /^[...]+$/"
    assert "validIdPattern.test(pluginId)" in body and "validIdPattern.test(pluginType)" in body
    rule = PANEL_ELEMENT_NAME_RE.pattern
    assert rule.endswith("]+")
    panel_class, rule_class = re.compile(literals[0]), re.compile(rule[:-1])
    differ = [hex(code) for code in range(0x10000)
              if bool(panel_class.fullmatch(chr(code))) != bool(rule_class.fullmatch(chr(code)))]
    assert differ == [], (
        "panel.js renderPluginElement's name check takes different characters from "
        "plugin_extension_rules.PANEL_ELEMENT_NAME_RE"
    )


def _plugin_element(plugin_type):
    return {"id": "plg", "type": "plugin", "plugin_id": "acme_widget", "plugin_type": plugin_type}


def test_the_page_review_asks_the_same_rule_of_a_type():
    """A type with a line break after it is one the panel refuses; the review
    used to pass it, since a Python ``$`` also matches before a final newline."""
    finding = plugin_element_finding(_plugin_element("meter\n"), "plg", None)
    assert finding is not None and finding.key == ("plugin_element_unconfigured", "plg", "malformed")
    assert plugin_element_finding(_plugin_element("meter"), "plg", None) is None


def test_every_plugin_id_is_a_name_the_panel_draws():
    for plugin_id in ("a", "acme_widget", "x9", "_"):
        assert plugin_declarations.is_plugin_id(plugin_id)
        assert is_panel_element_name(plugin_id)


def test_the_declarations_judge_asks_this_rule():
    """The start's EXTENSIONS row is this module's rule, not a copy of it."""
    assert plugin_declarations.extensions_problem is extensions_problem
    assert plugin_declarations.EXTENSION_ID_FIELD is EXTENSION_ID_FIELD


HELPERS_TS = (
    Path(__file__).resolve().parents[1] / "openavc" / "web" / "programmer" / "src"
    / "components" / "plugins" / "pluginExtensionHelpers.ts"
)


def test_the_ide_draws_each_context_on_the_page_the_rule_names():
    """The IDE's ``CONTEXT_ACTION_PAGES`` is the rule's table, word for word:
    a context the IDE does not list would draw nowhere."""
    source = HELPERS_TS.read_text(encoding="utf-8")
    found = re.findall(r"export const CONTEXT_ACTION_PAGES[^=]*= \{([^}]*)\}", source)
    assert len(found) == 1, "pluginExtensionHelpers.ts declares no CONTEXT_ACTION_PAGES object"
    ide = dict(re.findall(r"(\w+): \"(\w+)\"", found[0]))
    assert ide == CONTEXT_ACTION_PAGES


def test_every_documented_context_draws_on_a_page():
    assert set(CONTEXT_ACTION_PAGES) == {"global", "device", "plugin"}
    assert set(CONTEXT_ACTION_PAGES.values()) == {"device", "plugin"}


def test_an_action_with_an_unknown_context_still_starts():
    """A context the IDE draws nowhere is not a reason to stop a plugin."""
    assert extensions_problem({"context_actions": [{"id": "scan", "context": "toolbar"}]}) is None


def test_the_ide_is_served_an_actions_confirm_sentence():
    class AcmeWidget:
        PLUGIN_INFO = {"id": "acme_widget", "name": "Acme Widget"}
        EXTENSIONS = {"context_actions": [{
            "id": "wipe", "label": "Wipe", "context": "plugin",
            "event": "action.wipe", "confirm": "Wipe every preset?",
        }]}

    served = collect_extensions({"acme_widget": AcmeWidget()})["context_actions"]
    assert served == [{
        "id": "wipe", "label": "Wipe", "context": "plugin", "event": "action.wipe",
        "confirm": "Wipe every preset?", "plugin_id": "acme_widget", "plugin_name": "Acme Widget",
    }]


def test_an_id_is_unique_within_its_plugin_and_type_only():
    assert extensions_problem({
        "views": [{"id": "main"}],
        "status_cards": [{"id": "main"}],
        "context_actions": [{"id": "main"}],
        "panel_elements": [{"type": "main"}],
    }) is None
    assert extensions_problem({"views": [{"id": "main"}, {"id": "main"}]}) == (
        "views has duplicate id 'main'"
    )


# --- a surface layout ---------------------------------------------------------

ACME_MATRIX = {
    "type": "matrix",
    "rows_label": "Outputs",
    "columns_label": "Inputs",
    "rows_state_pattern": "plugin.acme_router.rx.*",
    "columns_state_pattern": "plugin.acme_router.tx.*",
    "route_state_pattern": "plugin.acme_router.source.{row}",
    "route_match": "exact",
    "presets": True,
}

ACME_GRID = {
    "type": "grid", "rows": 2, "columns": 4, "key_size_px": 72, "key_spacing_px": 4,
    "supports_pages": True, "max_pages": 10, "requires_device": True,
    "device_label": "Acme Pad", "virtual_models": ["Acme Pad Mini"], "network": True,
}


@pytest.mark.parametrize("layout", [
    None,
    ACME_MATRIX,
    {k: v for k, v in ACME_MATRIX.items() if k != "route_match"},
    {**ACME_MATRIX, "route_match": "port"},
    ACME_GRID,
    {"type": "grid", "rows": 3, "columns": 5},
    {"type": "strip", "columns": 8, "controls": [{"index": 0, "type": "fader"}]},
    {"type": "custom", "width_px": 600, "height_px": 200, "controls": [{"id": "f1", "type": "fader"}]},
], ids=["none", "matrix", "matrix-default-mode", "matrix-port-mode", "grid", "static-grid", "strip", "custom"])
def test_a_layout_the_ide_draws(layout):
    assert surface_layout_problem(layout) is None


@pytest.mark.parametrize(("layout", "problem"), [
    ([], "SURFACE_LAYOUT must be a dict"),
    ({"rows": 2}, "SURFACE_LAYOUT type None is not one of 'custom', 'grid', 'matrix', 'strip'"),
    ({"type": "dial"}, "SURFACE_LAYOUT type 'dial' is not one of 'custom', 'grid', 'matrix', 'strip'"),
    (
        {**ACME_MATRIX, "cell_state_pattern": "plugin.acme_router.route.{row}.{col}"},
        "SURFACE_LAYOUT has 'cell_state_pattern', which a matrix layout does not have",
    ),
    (
        {k: v for k, v in ACME_MATRIX.items() if k != "route_state_pattern"},
        "SURFACE_LAYOUT (matrix) needs 'route_state_pattern': the state key, with {row}, "
        "whose value names the source routed to each row",
    ),
    (
        {**ACME_MATRIX, "route_state_pattern": "plugin.acme_router.source"},
        "SURFACE_LAYOUT 'route_state_pattern' must be a state key with {row}, "
        "not 'plugin.acme_router.source'",
    ),
    (
        {**ACME_MATRIX, "rows_state_pattern": "plugin.acme_router.rx"},
        "SURFACE_LAYOUT 'rows_state_pattern' must be a state key with *, not 'plugin.acme_router.rx'",
    ),
    (
        {**ACME_MATRIX, "route_match": "fuzzy"},
        "SURFACE_LAYOUT 'route_match' must be one of 'exact', 'port', not 'fuzzy'",
    ),
    ({**ACME_MATRIX, "presets": "yes"}, "SURFACE_LAYOUT 'presets' must be true or false, not 'yes'"),
    ({**ACME_GRID, "rows": True}, "SURFACE_LAYOUT 'rows' must be a whole number, not True"),
    ({**ACME_GRID, "rows": -1}, "SURFACE_LAYOUT 'rows' must be a whole number, not -1"),
    ({**ACME_GRID, "device_label": 5}, "SURFACE_LAYOUT 'device_label' must be text, not 5"),
    ({**ACME_GRID, "virtual_models": "Acme Pad"}, "SURFACE_LAYOUT 'virtual_models' must be a list of text, not 'Acme Pad'"),
    ({"type": "strip", "controls": [5]}, "SURFACE_LAYOUT 'controls' must be a list of dicts, not [5]"),
    ({"type": "grid", "controls": []}, "SURFACE_LAYOUT has 'controls', which a grid layout does not have"),
])
def test_a_layout_the_ide_cannot_draw_as_written(layout, problem):
    assert surface_layout_problem(layout) == problem


def test_the_declarations_judge_hands_on_the_layout_rule():
    """validate.py asks it through plugin_declarations, as it asks the rest."""
    assert plugin_declarations.surface_layout_problem is surface_layout_problem


def test_the_start_does_not_ask_the_layout_rule():
    """A layout the IDE cannot draw is not a reason to stop a plugin: one
    installed before a layout field changed still starts."""
    info = {
        "id": "acme_router", "name": "Acme Router", "version": "1.0.0", "author": "Acme",
        "description": "Routes.", "category": "integration", "license": "MIT",
    }

    class AcmeRouter:
        PLUGIN_INFO = info
        SURFACE_LAYOUT = {"type": "matrix", "cell_state_pattern": "plugin.acme_router.route.{row}.{col}"}

    assert plugin_declarations.first_problem(plugin_declarations.Declared.of_class(AcmeRouter)) is None


def test_the_layout_names_the_matrix_route_rules_modes():
    assert list(SURFACE_ROUTE_MATCH_MODES) == list(ROUTE_MATCH_MODES)
    assert SURFACE_ROUTE_MATCH_DEFAULT in ROUTE_MATCH_MODES
    # The default never lights a crosspoint from a number two names share.
    assert ROUTE_MATCH_MODES[SURFACE_ROUTE_MATCH_DEFAULT] is False


SURFACE_TYPES_TS = (
    Path(__file__).resolve().parents[1] / "openavc" / "web" / "programmer" / "src"
    / "components" / "plugins" / "surface" / "types.ts"
)
ROUTING_HELPERS_TS = HELPERS_TS.with_name("routingMatrixHelpers.ts")


def test_the_ides_layout_names_every_field_the_table_does():
    """The IDE's ``SurfaceLayout`` holds the table's fields and no other."""
    source = SURFACE_TYPES_TS.read_text(encoding="utf-8")
    body = re.search(r"export interface SurfaceLayout \{(.*?)\n\}", source, re.S)
    assert body, "types.ts declares no SurfaceLayout interface"
    ide = set(re.findall(r"^\s+(\w+)\??:", body.group(1), re.M))
    table = {"type"} | {field for fields in SURFACE_LAYOUT_FIELDS.values() for field in fields}
    assert ide == table
    kinds = re.search(r'type: ([^;]+);', body.group(1))
    assert kinds and set(re.findall(r'"(\w+)"', kinds.group(1))) == set(SURFACE_LAYOUT_FIELDS)


def test_the_ide_compares_in_the_tables_default_mode():
    source = ROUTING_HELPERS_TS.read_text(encoding="utf-8")
    found = re.findall(r'export const SURFACE_ROUTE_MATCH_DEFAULT[^=]*= "(\w+)";', source)
    assert found == [SURFACE_ROUTE_MATCH_DEFAULT]


# --- The plugin.<id>. namespace -------------------------------------------------

OPENAVC_PACKAGE = Path(__file__).resolve().parents[1] / "openavc"
FRAMES_JS = PANEL_JS.parent / "modules" / "frames.js"
PROGRAMMER_SRC = OPENAVC_PACKAGE / "web" / "programmer" / "src"


def test_a_plugins_namespace():
    assert plugin_namespace("acme_widget") == "plugin.acme_widget."
    assert in_plugin_namespace("plugin.acme_widget.power", "acme_widget")
    assert in_plugin_namespace("plugin.acme_widget.action.go", "acme_widget")
    # The dot ends the id: another plugin whose id begins the same is not it.
    assert not in_plugin_namespace("plugin.acme_widgets.power", "acme_widget")
    assert not in_plugin_namespace("plugin.acme_widget", "acme_widget")
    assert not in_plugin_namespace("device.proj.power", "acme_widget")
    assert not in_plugin_namespace(None, "acme_widget")


_JS_NAMESPACE = re.compile(
    r"export function pluginNamespace\(pluginId[^)]*\)[^{]*\{\s*return `([^`]*)`;\s*\}"
)


@pytest.mark.parametrize("path", [FRAMES_JS, HELPERS_TS], ids=["panel", "ide"])
def test_the_panel_and_the_ide_build_the_namespace_as_the_rule_does(path):
    found = _JS_NAMESPACE.findall(path.read_text(encoding="utf-8"))
    assert len(found) == 1, f"{path.name} declares no pluginNamespace(pluginId)"
    assert found[0].replace("${pluginId}", "acme_widget") == plugin_namespace("acme_widget")


def _web_sources():
    for root, suffixes in ((PANEL_JS.parent, {".js"}), (PROGRAMMER_SRC, {".ts", ".tsx"})):
        for path in root.rglob("*"):
            if path.suffix in suffixes and ".test." not in path.name and path not in (FRAMES_JS, HELPERS_TS):
                yield path


def test_nothing_else_builds_a_plugins_namespace():
    """Every builder asks the one rule (or the panel's or the IDE's copy of
    it); a second copy could drift from it unseen."""
    python_builder = re.compile(r"""f["']plugin\.\{[^}]+\}\.""")
    web_builder = re.compile(r"plugin\.\$\{")
    rules = OPENAVC_PACKAGE / "core" / "plugin_extension_rules.py"
    found = [
        f"{path.relative_to(OPENAVC_PACKAGE)}:{n}"
        for path in OPENAVC_PACKAGE.rglob("*.py") if path != rules
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if python_builder.search(line)
    ] + [
        f"{path.relative_to(OPENAVC_PACKAGE)}:{n}"
        for path in _web_sources()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if web_builder.search(line)
    ]
    assert found == []


# --- The fields of an entry ----------------------------------------------------

ACME_ELEMENT = {
    "type": "acme_meter", "label": "Acme Meter", "renderer": "iframe",
    "default_size": {"w": 25, "h": 20}, "sandbox_permissions": ["allow-same-origin"],
    "allow_features": ["fullscreen"], "ext_auth": True,
    "config_schema": [
        {"key": "title", "label": "Title", "type": "string", "default": ""},
        {"key": "feed", "label": "Feed", "type": "select", "options_source": "plugin.acme_widget.feeds"},
    ],
}


@pytest.mark.parametrize("extensions", [
    {"views": [{"id": "monitor", "label": "Monitor", "icon": "activity", "renderer": "state_table",
                "state_pattern": "plugin.acme_widget.*"}]},
    {"views": [{"id": "surface", "label": "Surface", "renderer": "surface"}]},
    {"device_panels": [{"id": "channels", "label": "Channels", "match": {"driver_id": "acme_*"},
                        "renderer": "state_table",
                        "state_pattern": "plugin.acme_widget.device.{device_id}.*"}]},
    {"status_cards": [{"id": "status", "label": "Acme", "icon": "activity", "metrics": [
        {"key": "plugin.acme_widget.connected", "label": "Connected", "format": "boolean"}]}]},
    {"context_actions": [{"id": "scan", "label": "Scan", "context": "global",
                          "event": "action.scan", "confirm": "Scan now?"}]},
    {"context_actions": [{"id": "ping", "label": "Ping", "context": "device",
                          "event": "action.ping", "match": {"driver_id": "acme_*"}}]},
    {"panel_elements": [ACME_ELEMENT]},
    # What the start refuses is the start's to say.
    {"views": "notalist"},
], ids=["view", "surface-view", "device-panel", "status-card", "global-action", "device-action",
        "panel-element", "refused-at-start"])
def test_an_entry_drawn_as_written(extensions):
    assert extension_entries_problem(extensions, "acme_widget") is None


@pytest.mark.parametrize(("extensions", "problem"), [
    ({"views": [{"id": "v", "label": "V"}]}, "views[0] needs 'renderer'"),
    ({"views": [{"id": "v", "renderer": "chart"}]},
     "views[0] 'renderer' must be one of 'surface', 'state_table', 'log', 'video_streams'"),
    ({"device_panels": [{"id": "d", "renderer": "surface"}]},
     "device_panels[0] 'renderer' must be one of 'state_table', 'log'"),
    ({"views": [{"id": "v", "renderer": "state_table", "state_pattern": "device.*"}]},
     "views[0] 'state_pattern' must name the plugin's own state (plugin.acme_widget.*)"),
    ({"views": [{"id": "v", "renderer": "log", "state_pattern": "plugin.acme_widgets.*"}]},
     "views[0] 'state_pattern' must name the plugin's own state (plugin.acme_widget.*)"),
    ({"status_cards": [{"id": "c", "metrics": [{"key": "device.proj.power", "label": "Power"}]}]},
     "status_cards[0] metrics[0] key 'device.proj.power' is not one of the plugin's own state "
     "keys (plugin.acme_widget.*)"),
    ({"status_cards": [{"id": "c", "metrics": {"key": "plugin.acme_widget.x"}}]},
     "status_cards[0] 'metrics' must be a list of dicts"),
    ({"panel_elements": [{**ACME_ELEMENT, "config_schema": [
        {"key": "feed", "type": "select", "options_source": "var.feeds"}]}]},
     "panel_elements[0] config_schema[0] options_source 'var.feeds' is not one of the plugin's "
     "own state keys (plugin.acme_widget.*)"),
    ({"panel_elements": [{**ACME_ELEMENT, "config_schema": [{"label": "No key"}]}]},
     "panel_elements[0] config_schema[0] needs a 'key' (string)"),
    ({"panel_elements": [{**ACME_ELEMENT, "type": "acme:meter"}]},
     "panel_elements[0] 'type' must be letters, digits, _ and - only"),
    ({"panel_elements": [{**ACME_ELEMENT, "capabilities": ["state_read"]}]},
     "panel_elements[0] has 'capabilities', which a panel_elements entry does not have"),
    ({"panel_elements": [{**ACME_ELEMENT, "renderer_url": "/x.html"}]},
     "panel_elements[0] has 'renderer_url', which a panel_elements entry does not have"),
    ({"panel_elements": [{**ACME_ELEMENT, "default_size": {"w": 25}}]},
     """panel_elements[0] 'default_size' must be {"w": N, "h": N}, each above 0"""),
    ({"panel_elements": [{**ACME_ELEMENT, "ext_auth": "yes"}]},
     "panel_elements[0] 'ext_auth' must be true or false"),
    ({"context_actions": [{"id": "a", "context": "room"}]},
     "context_actions[0] 'context' must be one of 'global', 'device', 'plugin'"),
], ids=["view-no-renderer", "view-renderer", "panel-renderer", "pattern-outside", "pattern-other-plugin",
        "metric-outside", "metrics-not-list", "options-source-outside", "setting-no-key",
        "element-type", "capabilities", "renderer-url", "default-size", "ext-auth", "context"])
def test_an_entry_drawn_otherwise_than_written(extensions, problem):
    assert extension_entries_problem(extensions, "acme_widget") == problem


def test_the_start_does_not_ask_the_entry_rule():
    """An entry drawn otherwise than written is not a reason to stop a
    plugin: one installed before a field changed still starts."""
    info = {
        "id": "acme_widget", "name": "Acme Widget", "version": "1.0.0", "author": "Acme",
        "description": "Widgets.", "category": "integration", "license": "MIT",
    }

    class AcmeWidget:
        PLUGIN_INFO = info
        EXTENSIONS = {"panel_elements": [{"type": "acme_meter", "capabilities": ["state_read"]}]}

    assert extension_entries_problem(AcmeWidget.EXTENSIONS, "acme_widget") is not None
    assert plugin_declarations.first_problem(plugin_declarations.Declared.of_class(AcmeWidget)) is None
    assert plugin_declarations.extension_entries_problem is extension_entries_problem


def test_every_type_has_its_fields_and_its_naming_field_among_them():
    assert set(EXTENSION_FIELDS) == set(EXTENSION_ID_FIELD)
    for ext_type, id_field in EXTENSION_ID_FIELD.items():
        assert id_field in EXTENSION_FIELDS[ext_type]
    assert set(EXTENSION_RENDERERS) <= set(EXTENSION_FIELDS)


PLUGIN_CLIENT_TS = PROGRAMMER_SRC / "api" / "pluginClient.ts"
PLUGIN_EXTENSIONS_TSX = HELPERS_TS.with_name("PluginExtensions.tsx")
#: What the serve adds to each entry it hands on.
SERVED_FIELDS = {"plugin_id", "plugin_name"}
#: The fields only the panel reads, which the IDE's type leaves out.
PANEL_ONLY_FIELDS = {"sandbox_permissions", "allow_features", "ext_auth"}


def test_the_ides_extension_type_names_only_fields_a_plugin_may_send():
    """A field the IDE's ``PluginExtension`` reads that no entry may hold is
    one nothing writes; one the table has that it leaves out is the panel's."""
    source = PLUGIN_CLIENT_TS.read_text(encoding="utf-8")
    body = re.search(r"export interface PluginExtension \{(.*?)\n\}", source, re.S)
    assert body, "pluginClient.ts declares no PluginExtension interface"
    ide = set(re.findall(r"^  (\w+)\??:", body.group(1), re.M))
    table = {field for fields in EXTENSION_FIELDS.values() for field in fields}
    assert ide - SERVED_FIELDS - table == set()
    assert table - ide == PANEL_ONLY_FIELDS
    assert SERVED_FIELDS <= ide


def _slot(source: str, start: str) -> str:
    body = source[source.index(start):]
    return body[:body.index("\n}\n")]


def test_the_ide_draws_the_renderers_the_table_names():
    source = PLUGIN_EXTENSIONS_TSX.read_text(encoding="utf-8")
    views = re.findall(r'case "(\w+)":', _slot(source, "export function PluginViewRenderer("))
    panels = re.findall(r'panel\.renderer === "(\w+)"', _slot(source, "export function DevicePanelSlot("))
    assert sorted(views) == sorted(EXTENSION_RENDERERS["views"])
    assert sorted(panels) == sorted(EXTENSION_RENDERERS["device_panels"])


def _same_characters(js_class: str) -> list[str]:
    rule = PANEL_ELEMENT_NAME_RE.pattern
    assert rule.endswith("]+")
    js, ours = re.compile(js_class), re.compile(rule[:-1])
    return [hex(code) for code in range(0x10000)
            if bool(js.fullmatch(chr(code))) != bool(ours.fullmatch(chr(code)))]


def test_the_palette_offers_only_names_the_panel_draws():
    """The UI Builder's palette leaves out a plugin element the panel would
    only ever draw as a placeholder: its copy of the rule takes the same
    characters, and it tests both names."""
    source = HELPERS_TS.read_text(encoding="utf-8")
    literals = re.findall(r"const PANEL_ELEMENT_NAME = /\^(\[[^\]]+\])\+\$/;", source)
    assert len(literals) == 1, "pluginExtensionHelpers.ts declares no PANEL_ELEMENT_NAME /^[...]+$/"
    assert _same_characters(literals[0]) == []
    body = source[source.index("export function drawablePanelElements("):]
    body = body[:body.index("\n}\n")]
    assert "PANEL_ELEMENT_NAME.test(e.plugin_id)" in body and "PANEL_ELEMENT_NAME.test(e.type" in body
    palette = (PROGRAMMER_SRC / "components" / "ui-builder" / "ElementPalette.tsx").read_text(encoding="utf-8")
    assert "drawablePanelElements(" in palette
