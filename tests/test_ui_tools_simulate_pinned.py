"""The AI's simulated press, pinned: what it sends the event runtime, what it
captures while the interaction runs, and the reply it builds.

Each test records what ``simulate_ui_action`` does today, including the parts
that look odd (the "nothing ran" note names the action that was asked for, not
the branch a toggle dispatched; navigate needs no element and ignores one).
The element an interaction reaches is pinned on both sides: the runtime's own
lookup and the one the simulation resolves a toggle and its note against.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.project_loader import ProjectConfig
from openavc.core.state_store import StateStore
from openavc.core.ui_events import UIEventRuntime

from tests.test_ui_tools_lookups_pinned import _Devices

TOGGLE_PRESS = [{
    "action": "device.command", "device": "amp", "command": "power",
    "mode": "toggle", "toggle_key": "device.amp.power", "toggle_value": "on",
    "off_action": {"action": "device.command", "device": "amp", "command": "off"},
}]


def _project() -> dict:
    return {
        "project": {"id": "acme_room", "name": "Acme Room"},
        "devices": [{"id": "amp", "driver": "acme_amp", "name": "Amplifier"}],
        "ui": {
            "master_elements": [
                {"id": "m_toggle", "type": "button", "pages": "*",
                 "bindings": {"do": {"press": TOGGLE_PRESS}}},
                {"id": "shadowed", "type": "button", "pages": "*",
                 "bindings": {"do": {"press": TOGGLE_PRESS}}},
                {"id": "m_plain", "type": "button", "pages": "*"},
            ],
            "pages": [
                {"id": "main", "name": "Main", "elements": [
                    {"id": "shadowed", "type": "button", "bindings": {"do": {
                        "press": [{"action": "ui.navigate", "page": "other"}],
                    }}},
                    {"id": "twice", "type": "button", "bindings": {"do": {
                        "release": [{"action": "ui.navigate", "page": "main"}],
                        "hold": [],
                    }}},
                    {"id": "bare", "type": "button"},
                    {"id": "toggle", "type": "button",
                     "bindings": {"do": {"press": TOGGLE_PRESS}}},
                ]},
                {"id": "other", "name": "Other", "elements": [
                    {"id": "twice", "type": "slider", "bindings": {"do": {
                        "change": [{"action": "ui.navigate", "page": "main"}],
                    }}},
                ]},
            ],
        },
    }


class _Recorder:
    """The engine calls a simulation makes, in the order it made them."""

    def __init__(self, store: StateStore, *, writes=(), returns=None, raises=None):
        self.calls: list[tuple] = []
        self.store = store
        self.writes = list(writes)
        self.returns = returns
        self.raises = raises

    async def emit(self, name, *args, **kwargs):
        self.calls.append(("emit", name, args, kwargs))

    async def broadcast_ws(self, message):
        self.calls.append(("broadcast_ws", message))

    async def handle_ui_event(self, *args, **kwargs):
        self.calls.append(("handle_ui_event", args, kwargs))
        for key, value, source in self.writes:
            self.store.set(key, value, source=source)
        if self.raises is not None:
            raise self.raises
        return self.returns


def _engine(recorder: _Recorder, project: dict | None = None):
    engine = MagicMock()
    engine.project = ProjectConfig.model_validate(project or _project())
    engine.events.emit = recorder.emit
    engine.broadcast_ws = recorder.broadcast_ws
    engine.handle_ui_event = recorder.handle_ui_event
    return engine


async def _simulate(payload: dict, *, store=None, engine=None, **recorder):
    store = store if store is not None else StateStore()
    rec = _Recorder(store, **recorder)
    engine = engine if engine is not None else _engine(rec)
    agent = MagicMock()
    agent.state = store
    handler = AIToolHandler(agent, _Devices(), MagicMock(emit=AsyncMock()))
    with patch.object(handler, "_get_engine", return_value=engine):
        result = await handler._simulate_ui_action(payload)
    return result, rec, store


def _listeners(store: StateStore) -> int:
    return len(store._all_subs)


# --- the guards, in the order they run -------------------------------------------


@pytest.mark.asyncio
async def test_no_engine_is_refused_before_anything_is_read():
    agent = MagicMock()
    handler = AIToolHandler(agent, _Devices(), MagicMock(emit=AsyncMock()))
    with patch.object(handler, "_get_engine", return_value=None):
        assert await handler._simulate_ui_action({"action": "press"}) == {
            "error": "Engine not available",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("page_id", [None, "", 0])
async def test_navigate_needs_a_page_and_ignores_an_element(page_id):
    payload = {"action": "navigate", "element_id": "bare"}
    if page_id is not None:
        payload["page_id"] = page_id
    result, rec, _ = await _simulate(payload)
    assert result == {"error": "page_id is required for navigate action"}
    assert rec.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, 1, "yes"])
async def test_a_dry_run_navigate_says_where_and_sends_nothing(dry_run):
    result, rec, _ = await _simulate(
        {"action": "navigate", "page_id": "other", "dry_run": dry_run},
    )
    assert result == {
        "success": True, "action": "navigate", "page_id": "other",
        "dry_run": True, "state_changes": [],
        "note": "Would move every connected panel to 'other'.",
    }
    assert list(result) == [
        "success", "action", "page_id", "dry_run", "state_changes", "note",
    ]
    assert rec.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, 0, "", None, []])
async def test_a_navigate_emits_the_page_event_then_broadcasts(dry_run):
    result, rec, _ = await _simulate(
        {"action": "navigate", "page_id": "other", "dry_run": dry_run},
    )
    assert result == {
        "success": True, "action": "navigate", "page_id": "other", "state_changes": [],
    }
    assert rec.calls == [
        ("emit", "ui.page.other", (), {}),
        ("broadcast_ws", {"type": "ui.navigate", "page_id": "other"}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["press", "change", "teleport", ""])
async def test_every_other_action_needs_an_element_first(action):
    result, rec, store = await _simulate({"action": action, "element_id": ""})
    assert result == {"error": "element_id is required for this action"}
    assert rec.calls == []
    assert _listeners(store) == 0


@pytest.mark.asyncio
async def test_an_unknown_action_is_refused_after_subscribing_and_unsubscribes():
    store = StateStore()
    subscribe = MagicMock(wraps=store.subscribe)
    unsubscribe = MagicMock(wraps=store.unsubscribe)
    store.subscribe = subscribe
    store.unsubscribe = unsubscribe
    result, rec, _ = await _simulate(
        {"action": "teleport", "element_id": "bare"}, store=store,
    )
    assert result == {"error": "Unknown action: teleport"}
    assert rec.calls == []
    assert subscribe.call_count == 1
    assert subscribe.call_args.args[0] == "*"
    assert unsubscribe.call_count == 1
    assert _listeners(store) == 0


# --- what the event runtime is asked ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["press", "release", "hold"])
@pytest.mark.parametrize("dry_run", [False, True])
async def test_a_touch_is_sent_as_its_event_with_no_data(action, dry_run):
    result, rec, _ = await _simulate(
        {"action": action, "element_id": "bare", "value": 5, "dry_run": dry_run},
        returns=[{"action": "x"}],
    )
    assert rec.calls == [("handle_ui_event", (action, "bare"), {"dry_run": dry_run})]
    assert result["dispatched"] == [{"action": "x"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["change", "submit"])
@pytest.mark.parametrize("value", [None, 0, "", "abc", [1]])
async def test_a_value_event_carries_its_value(action, value):
    payload = {"action": action, "element_id": "twice", "dry_run": "x"}
    if value is not None:
        payload["value"] = value
    result, rec, _ = await _simulate(payload, returns=[])
    assert rec.calls == [
        ("handle_ui_event", (action, "twice", {"value": value}), {"dry_run": True}),
    ]
    assert result["dry_run"] is True


@pytest.mark.asyncio
async def test_a_failure_returns_what_changed_before_it_and_unsubscribes():
    store = StateStore()
    result, rec, _ = await _simulate(
        {"action": "change", "element_id": "twice", "value": 3}, store=store,
        writes=[("var.level", 3, "ui"), ("var.mode", "a", "heartbeat")],
        raises=RuntimeError("boom"),
    )
    assert result == {
        "error": "Action failed: boom",
        "state_changes": [{"key": "var.level", "old_value": None, "new_value": 3}],
    }
    assert list(result) == ["error", "state_changes"]
    store.set("var.level", 4, source="ui")
    assert result["state_changes"] == [
        {"key": "var.level", "old_value": None, "new_value": 3},
    ]


# --- what it captured ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_capture_drops_background_sources_and_cloud_excluded_keys():
    store = StateStore()
    store.set("var.url", "rtsp://a:old@cam", source="ui")
    writes = [
        ("var.url", "rtsp://user:secret@cam/stream", "device.cam"),
        ("system.cloud.session", "s", "ui"),
        ("isc.peer.x", 1, "device.amp"),
        ("var.a", 1, "heartbeat"), ("var.b", 1, "system"), ("var.c", 1, "cloud"),
        ("var.d", 1, "ai"), ("var.e", 1, "isc"), ("var.f", 1, "discovered"),
        ("var.g", 1, "device.amp"), ("var.h", True, "macro"),
    ]
    result, _, _ = await _simulate(
        {"action": "press", "element_id": "bare"}, store=store, writes=writes, returns=[],
    )
    assert result["state_changes"] == [
        {"key": "var.url", "old_value": "rtsp://a:***@cam",
         "new_value": "rtsp://user:***@cam/stream"},
        {"key": "var.g", "old_value": None, "new_value": 1},
        {"key": "var.h", "old_value": None, "new_value": True},
    ]


# --- the reply -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_toggle_press_reply_in_full_and_in_order():
    store = StateStore()
    store.set("device.amp.power", "ON", source="device.amp")
    result, rec, _ = await _simulate(
        {"action": "press", "element_id": "toggle", "dry_run": True},
        store=store, returns=None,
    )
    assert rec.calls == [("handle_ui_event", ("toggle_off", "toggle"), {"dry_run": True})]
    assert list(result) == [
        "success", "action", "dispatched_as", "toggle", "dry_run", "element_id",
        "dispatched", "state_changes", "note",
    ]
    assert result["success"] is True
    assert result["action"] == "press"
    assert result["dispatched_as"] == "toggle_off"
    assert result["toggle"].startswith("toggle_key 'device.amp.power' is 'ON' == ")
    assert result["dry_run"] is True
    assert result["dispatched"] == []
    assert result["state_changes"] == []
    # The note names the action asked for, not the branch the toggle took.
    assert result["note"] == "'toggle' has a do.press binding, but no action in it ran."


@pytest.mark.asyncio
async def test_a_toggle_off_its_value_is_a_plain_press_with_a_reason():
    result, rec, _ = await _simulate(
        {"action": "press", "element_id": "toggle"}, returns=[{"action": "y"}],
    )
    assert rec.calls == [("handle_ui_event", ("press", "toggle"), {"dry_run": False})]
    assert list(result) == [
        "success", "action", "toggle", "element_id", "dispatched", "state_changes",
    ]
    assert "!=" in result["toggle"]


@pytest.mark.asyncio
async def test_a_master_toggle_is_resolved_like_a_page_one():
    store = StateStore()
    store.set("device.amp.power", "on", source="device.amp")
    result, rec, _ = await _simulate(
        {"action": "press", "element_id": "m_toggle"}, store=store, returns=[],
    )
    assert rec.calls == [("handle_ui_event", ("toggle_off", "m_toggle"), {"dry_run": False})]
    assert result["dispatched_as"] == "toggle_off"
    assert result["note"] == "'m_toggle' has a do.press binding, but no action in it ran."


@pytest.mark.asyncio
async def test_a_page_element_wins_an_id_a_master_also_has():
    store = StateStore()
    store.set("device.amp.power", "on", source="device.amp")
    result, rec, _ = await _simulate(
        {"action": "press", "element_id": "shadowed"}, store=store, returns=[],
    )
    assert rec.calls == [("handle_ui_event", ("press", "shadowed"), {"dry_run": False})]
    assert "dispatched_as" not in result and "toggle" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["release", "hold"])
async def test_only_a_press_is_resolved_as_a_toggle(action):
    store = StateStore()
    store.set("device.amp.power", "on", source="device.amp")
    result, rec, _ = await _simulate(
        {"action": action, "element_id": "toggle"}, store=store, returns=[],
    )
    assert rec.calls == [("handle_ui_event", (action, "toggle"), {"dry_run": False})]
    assert "dispatched_as" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("element_id, action, note", [
    ("nowhere", "press",
     "No element 'nowhere' is on any page or in the master elements, so nothing ran."),
    ("bare", "press", "'bare' has nothing bound to press. It has no do bindings at all."),
    ("twice", "change", "'twice' has nothing bound to change. It has: release."),
    ("twice", "release", "'twice' has a do.release binding, but no action in it ran."),
    ("twice", "hold", "'twice' has nothing bound to hold. It has: release."),
    ("m_plain", "hold", "'m_plain' has nothing bound to hold. It has no do bindings at all."),
])
@pytest.mark.parametrize("returned", [None, []])
async def test_when_nothing_ran_the_reply_says_why(element_id, action, note, returned):
    result, _, _ = await _simulate(
        {"action": action, "element_id": element_id}, returns=returned,
    )
    assert result["dispatched"] == []
    assert result["note"] == note
    assert list(result)[-1] == "note"


@pytest.mark.asyncio
async def test_an_element_on_two_pages_is_the_first_pages():
    result, _, _ = await _simulate({"action": "change", "element_id": "twice"}, returns=[])
    assert result["note"] == "'twice' has nothing bound to change. It has: release."


@pytest.mark.asyncio
async def test_a_project_with_no_ui_has_no_elements():
    rec_store = StateStore()
    rec = _Recorder(rec_store, returns=[])
    engine = MagicMock()
    engine.project = None
    engine.handle_ui_event = rec.handle_ui_event
    result, _, _ = await _simulate(
        {"action": "press", "element_id": "bare"}, store=rec_store, engine=engine,
    )
    assert result["note"] == (
        "No element 'bare' is on any page or in the master elements, so nothing ran."
    )


# --- the element the runtime reaches -----------------------------------------------


def _runtime(project: dict | None = None) -> UIEventRuntime:
    engine = MagicMock()
    engine.project = ProjectConfig.model_validate(project or _project())
    return UIEventRuntime(engine)


@pytest.mark.parametrize("element_id, found", [
    ("shadowed", ("main", "button")),
    ("m_toggle", ("master", "button")),
    ("twice", ("main", "button")),
    ("toggle", ("main", "button")),
])
def test_the_runtime_finds_pages_first_then_masters(element_id, found):
    runtime = _runtime()
    element = runtime._find_element(element_id)
    project = runtime._engine.project
    on_page = next((p.id for p in project.ui.pages if element in p.elements), "master")
    assert (on_page, element.type) == found
    assert element.id == element_id


def test_the_runtime_finds_nothing_for_an_unknown_id_or_no_project():
    runtime = _runtime()
    assert runtime._find_element("nowhere") is None
    runtime._engine.project = None
    assert runtime._find_element("bare") is None


def test_the_runtime_finds_the_first_page_holding_an_id():
    runtime = _runtime()
    element = runtime._find_element("twice")
    assert element is runtime._engine.project.ui.pages[0].elements[1]
