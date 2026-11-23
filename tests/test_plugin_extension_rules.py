"""The rules for a plugin's EXTENSIONS (``core/plugin_extension_rules.py``), and
the sides that ask them: the start, the page review and the panel."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openavc.core import plugin_declarations
from openavc.core.plugin_extension_rules import (
    EXTENSION_ID_FIELD,
    PANEL_ELEMENT_NAME_RE,
    extensions_problem,
    is_panel_element_name,
)
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
