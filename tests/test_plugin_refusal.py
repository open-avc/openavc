"""What a plugin's error says, and to whom.

The Programmer's plugin card shows whatever ``start()`` raised. The AI
assistant's answers are kept in the cloud chat history, so they carry a
plugin's own text only when the plugin marked it for the reader by raising
``PluginRefusal``; anything else is the platform's sentence, which sends the
reader to the Programmer's Log, where the text is.
"""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.event_bus import EventBus
from openavc.core.plugin_api import PluginRefusal
from openavc.core.plugin_loader import (
    _PLUGIN_CLASS_REGISTRY,
    _REGISTRY_LOCK,
    PluginLoader,
    register_plugin_class,
)
from openavc.core.project_loader import PluginConfig
from openavc.core.state_store import StateStore
from tests.test_ai_tool_handler_surgical import _make_project

# Text the plugin did not mark: an exception's own words, with a local path in
# them. It must never appear in anything the cloud is told.
UNMARKED = "UNMARKED-SENTINEL could not open /home/acme/.config/widget.key"
MARKED = "Enter the Acme Widget API key in this plugin's settings."


class AcmeWidgetPlugin:
    """Starts, or fails the way its config says to."""

    PLUGIN_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "version": "1.0.0",
        "author": "Acme",
        "description": "A plugin whose start() fails on request.",
        "category": "utility",
        "license": "MIT",
        "capabilities": [],
    }
    CONFIG_SCHEMA = {"fail": {"type": "string", "label": "Fail", "default": ""}}

    async def start(self, api):
        fail = api.config.get("fail", "")
        if fail == "marked":
            raise PluginRefusal(MARKED)
        if fail == "unmarked":
            raise RuntimeError(UNMARKED)

    async def stop(self):
        pass


@pytest.fixture(autouse=True)
def clean_plugin_registry():
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
    register_plugin_class(AcmeWidgetPlugin)
    yield
    with _REGISTRY_LOCK:
        _PLUGIN_CLASS_REGISTRY.clear()
        _PLUGIN_CLASS_REGISTRY.update(saved)


@pytest.fixture
def loader():
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    macros = MagicMock()
    macros.unregister_plugin_actions = MagicMock()
    return PluginLoader(state, events, macros, MagicMock())


@pytest.fixture
def engine(loader):
    """An engine stub with a real project and the real loader, applying edits
    the way the seam does (copy, mutate, swap)."""
    eng = MagicMock()
    eng.project = _make_project()
    eng.plugin_loader = loader

    async def _apply_edit(mutate):
        new_project = eng.project.model_copy(deep=True)
        mutate(new_project)
        eng.project = new_project

    eng.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    return eng


@pytest.fixture
def handler(engine):
    agent = MagicMock()
    agent.send_message = AsyncMock()
    tools = AIToolHandler(agent, MagicMock(), MagicMock())
    with patch.object(tools, "_get_engine", return_value=engine):
        yield tools


def _card(loader):
    return next(p for p in loader.list_plugins() if p["plugin_id"] == "acme_widget")


def _cloud_entry(loader):
    return next(
        p for p in loader.list_plugins(for_cloud=True) if p["plugin_id"] == "acme_widget"
    )


# --- The loader ---


@pytest.mark.asyncio
async def test_a_refusal_reaches_the_card_and_the_cloud_as_written(loader):
    assert await loader.start_plugin("acme_widget", {"fail": "marked"}) is False
    assert _card(loader)["error"] == MARKED
    assert loader.error_for_cloud("acme_widget") == MARKED
    assert _cloud_entry(loader)["error"] == MARKED


@pytest.mark.asyncio
async def test_anything_else_stays_on_the_card_and_in_the_log(loader, caplog):
    with caplog.at_level(logging.ERROR):
        assert await loader.start_plugin("acme_widget", {"fail": "unmarked"}) is False
    # The card keeps what it showed before.
    assert _card(loader)["error"] == UNMARKED
    # The cloud is told the plugin couldn't start, and where to look.
    assert loader.error_for_cloud("acme_widget") is None
    assert _cloud_entry(loader)["error"] == (
        "Acme Widget couldn't start. The Programmer's Log has the details."
    )
    # Where that sentence sends the reader: the record's own message, which is
    # what the Log view shows, carries the type and the text.
    assert any(
        f"RuntimeError: {UNMARKED}" in r.getMessage() for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_the_platforms_own_sentences_still_reach_the_cloud(loader):
    with patch("openavc.core.plugin_loader.PLUGIN_START_TIMEOUT", 0.01):
        class SlowWidget(AcmeWidgetPlugin):
            PLUGIN_INFO = {**AcmeWidgetPlugin.PLUGIN_INFO, "id": "slow_widget"}

            async def start(self, api):
                import asyncio
                await asyncio.sleep(1)

        register_plugin_class(SlowWidget)
        assert await loader.start_plugin("slow_widget", {}) is False
    assert "timed out" in (loader.error_for_cloud("slow_widget") or "")

    class NoLicenseWidget(AcmeWidgetPlugin):
        PLUGIN_INFO = {
            k: v for k, v in AcmeWidgetPlugin.PLUGIN_INFO.items() if k != "license"
        } | {"id": "no_license_widget"}

    register_plugin_class(NoLicenseWidget)
    assert await loader.start_plugin("no_license_widget", {}) is False
    assert (loader.error_for_cloud("no_license_widget") or "").startswith("Invalid manifest")


@pytest.mark.asyncio
async def test_the_mark_follows_the_latest_failure_and_clears_on_success(loader):
    await loader.start_plugin("acme_widget", {"fail": "unmarked"})
    assert loader.error_for_cloud("acme_widget") is None
    await loader.start_plugin("acme_widget", {"fail": "marked"})
    assert loader.error_for_cloud("acme_widget") == MARKED
    await loader.start_plugin("acme_widget", {"fail": "unmarked"})
    assert loader.error_for_cloud("acme_widget") is None
    assert await loader.start_plugin("acme_widget", {}) is True
    assert loader.error_for_cloud("acme_widget") is None
    assert "acme_widget" not in loader._unmarked_errors


# --- The AI assistant's tools ---


@pytest.mark.asyncio
async def test_enable_answers_a_refusal_as_written(handler, engine):
    engine.project.plugins["acme_widget"] = PluginConfig(enabled=False, config={"fail": "marked"})
    result = await handler._enable_plugin({"plugin_id": "acme_widget"})
    assert result["status"] == "error"
    assert result["error"] == (
        f"Plugin 'acme_widget' failed to start: {MARKED} "
        "The enable was rolled back; its config is preserved."
    )
    assert result["config"] == {"fail": "marked"}
    assert engine.project.plugins["acme_widget"].enabled is False


@pytest.mark.asyncio
async def test_enable_words_anything_else(handler, engine):
    engine.project.plugins["acme_widget"] = PluginConfig(enabled=False, config={"fail": "unmarked"})
    result = await handler._enable_plugin({"plugin_id": "acme_widget"})
    assert result["error"] == (
        "Plugin 'acme_widget' couldn't start. The Programmer's Log has the details. "
        "The enable was rolled back; its config is preserved."
    )


@pytest.mark.asyncio
async def test_a_config_update_that_stops_the_plugin_says_its_refusal(handler, engine, loader):
    engine.project.plugins["acme_widget"] = PluginConfig(enabled=True, config={"fail": ""})
    assert await loader.start_plugin("acme_widget", {"fail": ""}) is True

    result = await handler._update_plugin_config(
        {"plugin_id": "acme_widget", "config": {"fail": "marked"}}
    )
    assert result["applied"] == "start_failed"
    assert result["warning"] == (
        "Config saved, but plugin 'acme_widget' failed to restart with it and is "
        f"stopped: {MARKED}"
    )


@pytest.mark.asyncio
async def test_a_config_update_that_stops_the_plugin_otherwise_points_at_the_logs(
    handler, engine, loader,
):
    engine.project.plugins["acme_widget"] = PluginConfig(enabled=True, config={"fail": ""})
    assert await loader.start_plugin("acme_widget", {"fail": ""}) is True

    result = await handler._update_plugin_config(
        {"plugin_id": "acme_widget", "config": {"fail": "unmarked"}}
    )
    assert result["warning"] == (
        "Config saved, but plugin 'acme_widget' failed to restart with it and is "
        "stopped. Check the config values and plugin logs."
    )


@pytest.mark.asyncio
async def test_an_install_that_does_not_load_is_worded_by_the_platform(handler):
    loaded_wrong = {
        "status": "load_failed",
        "plugin_id": "acme_widget",
        "error": f"acme_widget_plugin.py: RuntimeError: {UNMARKED}",
    }
    with patch(
        "openavc.core.plugin_installer.install_plugin", AsyncMock(return_value=loaded_wrong),
    ):
        result = await handler._install_plugin(
            {"plugin_id": "acme_widget", "file_url": "https://example.invalid/x.py"}
        )
    assert result == {
        "status": "load_failed",
        "plugin_id": "acme_widget",
        "error": (
            "Plugin 'acme_widget' was installed but couldn't load. "
            "The Programmer's Log has the details."
        ),
    }


# --- The rule, across every answer that carries a plugin's error ---


async def _every_answer(handler, engine, loader, fail: str) -> list:
    """Each plugin tool answer that can carry this plugin's error, after its
    start() failed with ``fail``."""
    engine.project.plugins["acme_widget"] = PluginConfig(enabled=False, config={"fail": fail})
    answers = [await handler._enable_plugin({"plugin_id": "acme_widget"})]
    answers.append(await handler._list_plugins({}))
    answers.append(await handler._get_project_summary({}))

    engine.project.plugins["acme_widget"] = PluginConfig(enabled=True, config={"fail": ""})
    assert await loader.start_plugin("acme_widget", {"fail": ""}) is True
    answers.append(await handler._update_plugin_config(
        {"plugin_id": "acme_widget", "config": {"fail": fail}}
    ))
    return answers


@pytest.mark.asyncio
async def test_no_answer_carries_text_the_plugin_did_not_mark(handler, engine, loader):
    answers = await _every_answer(handler, engine, loader, "unmarked")
    for answer in answers:
        assert "UNMARKED-SENTINEL" not in json.dumps(answer, default=str), answer
    # The plugin card still shows it.
    assert _card(loader)["error"] == UNMARKED


@pytest.mark.asyncio
async def test_every_answer_carries_a_refusal(handler, engine, loader):
    answers = await _every_answer(handler, engine, loader, "marked")
    for answer in answers:
        assert MARKED in json.dumps(answer, default=str), answer


def test_a_plugin_on_an_older_release_falls_back_to_a_plain_exception():
    """The import the docs give plugin authors: on a release without
    PluginRefusal it is a RuntimeError, whose text the card shows as before."""
    namespace: dict = {}
    source = (
        "try:\n"
        "    from openavc.core.plugin_api import PluginRefusal\n"
        "except ImportError:\n"
        "    PluginRefusal = RuntimeError\n"
    )
    exec(source, namespace)
    assert namespace["PluginRefusal"] is PluginRefusal

    stub = SimpleNamespace()
    with patch.dict("sys.modules", {"openavc.core.plugin_api": stub}):
        namespace = {}
        exec(source, namespace)
    assert namespace["PluginRefusal"] is RuntimeError
