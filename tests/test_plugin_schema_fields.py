"""A CONFIG_SCHEMA field, or a group's ``fields``, that is not a dict.

The plugin is refused when it starts, with a sentence naming the field. The
readers that take a schema before the plugin runs (the default config an
enable builds, the setup fields the settings dialog and the AI read) pass over
such a field rather than raising, so the AI assistant is told why the plugin
cannot start instead of the fixed sentence for a server error.

Uses an invented plugin (acme_widget) throughout.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.ai_tool_handler import TOOL_FAILED, AIToolHandler
from openavc.core.event_bus import EventBus
from openavc.core.plugin_declarations import Declared, config_schema_problem, first_problem
from openavc.core.plugin_loader import (
    _PLUGIN_CLASS_REGISTRY,
    _REGISTRY_LOCK,
    PluginLoader,
    register_plugin_class,
)
from openavc.core.project_loader import (
    PluginConfig,
    build_default_plugin_config,
    get_plugin_setup_fields,
)
from openavc.core.state_store import StateStore
from tests.test_ai_tool_handler_surgical import _make_project

_INFO = {
    "id": "acme_widget", "name": "Acme Widget", "version": "1.0.0", "author": "Acme",
    "description": "A widget.", "category": "utility", "license": "MIT",
}

_FLAT = {"host": {"type": "string", "required": True}, "port": "9000"}
_GROUP_FIELDS = {
    "host": {"type": "string", "required": True},
    "advanced": {"type": "group", "fields": ["retries"]},
}
_IN_A_GROUP = {
    "advanced": {"type": "group", "fields": {"retries": {"type": "integer", "default": 3}, "mode": 5}},
}


# --- the rule ---------------------------------------------------------------------


@pytest.mark.parametrize("schema, message", [
    (_FLAT, "CONFIG_SCHEMA['port'] must be a dict, got str"),
    ({"port": None}, "CONFIG_SCHEMA['port'] must be a dict, got NoneType"),
    (_GROUP_FIELDS, "CONFIG_SCHEMA['advanced']['fields'] must be a dict, got list"),
    ({"advanced": {"type": "group", "fields": None}},
     "CONFIG_SCHEMA['advanced']['fields'] must be a dict, got NoneType"),
    (_IN_A_GROUP, "CONFIG_SCHEMA['advanced']['fields']['mode'] must be a dict, got int"),
    ({"outer": {"type": "group", "fields": {"inner": {"type": "group", "fields": {"x": [1]}}}}},
     "CONFIG_SCHEMA['outer']['fields']['inner']['fields']['x'] must be a dict, got list"),
    ([], "CONFIG_SCHEMA must be a dict"),
])
def test_a_field_that_is_not_a_dict_is_refused_by_its_place(schema, message):
    assert config_schema_problem(schema) == message
    assert first_problem(Declared(dict(_INFO), config_schema=schema)) == message


@pytest.mark.parametrize("schema", [
    None,
    {},
    {"host": {"type": "string"}},
    {"advanced": {"type": "group"}},
    {"advanced": {"type": "group", "fields": {}}},
    {"advanced": {"type": "group", "fields": {"retries": {"type": "integer"}}}},
    # Only a group's fields are read as fields.
    {"rows": {"type": "mapping_list", "fields": ["a"], "item_schema": {"a": {"type": "string"}}}},
])
def test_a_schema_of_dicts_passes(schema):
    assert config_schema_problem(schema) is None


# --- the readers before a start -------------------------------------------------------


def test_the_default_config_passes_over_a_field_that_is_not_a_dict():
    assert build_default_plugin_config({**_FLAT, "port": "9000", "mode": {"type": "string", "default": "a"}}) == {
        "mode": "a"}
    assert build_default_plugin_config(_GROUP_FIELDS) == {"advanced": {}}
    assert build_default_plugin_config(_IN_A_GROUP) == {"advanced": {"retries": 3}}


def test_the_setup_fields_pass_over_a_field_that_is_not_a_dict():
    assert get_plugin_setup_fields(_FLAT) == {"host": {"type": "string", "required": True}}
    assert get_plugin_setup_fields(_GROUP_FIELDS) == {"host": {"type": "string", "required": True}}


# --- what the AI assistant is told ------------------------------------------------------


def _plugin_class(schema):
    async def start(self, api):
        return None

    async def stop(self):
        return None

    return type("AcmeWidgetPlugin", (), {
        "PLUGIN_INFO": dict(_INFO), "CONFIG_SCHEMA": schema, "start": start, "stop": stop,
    })


@pytest.fixture
def registry():
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
    yield lambda schema: register_plugin_class(_plugin_class(schema))
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
        _PLUGIN_CLASS_REGISTRY.update(saved)


@pytest.fixture
def engine():
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    eng = MagicMock()
    eng.project = _make_project()
    eng.plugin_loader = PluginLoader(state, events, MagicMock(), MagicMock())

    async def _apply_edit(mutate):
        new_project = eng.project.model_copy(deep=True)
        mutate(new_project)
        eng.project = new_project

    eng.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    return eng


@pytest.fixture
def ask(engine):
    """Run one AI tool through the dispatch the cloud calls; what it sends back."""
    agent = MagicMock()
    agent.send_message = AsyncMock()
    tools = AIToolHandler(agent, MagicMock(), MagicMock())

    async def _ask(tool_name, tool_input):
        with patch.object(tools, "_get_engine", return_value=engine):
            await tools._execute_tool("req-1", tool_name, tools._tools[tool_name], tool_input)
        return agent.send_message.call_args[0][1]

    return _ask


@pytest.mark.parametrize("schema, field", [
    (_FLAT, "CONFIG_SCHEMA['port']"),
    (_GROUP_FIELDS, "CONFIG_SCHEMA['advanced']['fields']"),
])
async def test_enable_says_why_the_plugin_cannot_start(registry, engine, ask, schema, field):
    registry(schema)
    answer = await ask("enable_plugin", {"plugin_id": "acme_widget"})
    assert answer["success"] is False
    assert answer["error"] != TOOL_FAILED
    assert f"Invalid manifest: {field} must be a dict" in answer["error"]
    assert engine.project.plugins["acme_widget"].enabled is False


@pytest.mark.parametrize("schema", [_FLAT, _GROUP_FIELDS])
async def test_get_plugin_config_answers(registry, engine, ask, schema):
    registry(schema)
    engine.project.plugins["acme_widget"] = PluginConfig(enabled=False, config={"host": "10.0.0.9"})
    answer = await ask("get_plugin_config", {"plugin_id": "acme_widget"})
    assert answer["success"] is True, answer
    assert answer["result"]["required_fields"] == ["host"]


async def test_the_setup_fields_route_answers(registry):
    from openavc.api.plugins import get_setup_fields

    registry(_FLAT)
    assert await get_setup_fields("acme_widget") == {
        "plugin_id": "acme_widget",
        "setup_required": True,
        "fields": {"host": {"type": "string", "required": True}},
    }
