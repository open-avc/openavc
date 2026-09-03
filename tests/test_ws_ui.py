"""Tests for WebSocket UI message handling.

Tests verify:
- Connection and initial message delivery (snapshot, ui.definition)
- UI events (press, submit, route, change) dispatch correctly
- State changes from UI bindings are applied
- Page navigation broadcasts to clients
- Error handling for invalid messages
"""

import json
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openavc.core.engine import Engine
from openavc.main import app
from openavc.api import rest, ws


TEST_PROJECT = {
    "project": {"id": "ws_ui_test", "name": "WS UI Test"},
    "devices": [],
    "variables": [
        {"id": "channel", "type": "string", "default": "", "label": "Channel"},
        {"id": "slider_val", "type": "number", "default": 50, "label": "Slider"},
    ],
    "macros": [],
    "ui": {
        "pages": [
            {
                "id": "main",
                "name": "Main",
                "grid": {"columns": 12, "rows": 8},
                "elements": [
                    {
                        "id": "btn1",
                        "type": "button",
                        "label": "Test",
                        "grid_area": {"col": 1, "row": 1, "col_span": 3, "row_span": 2},
                        "style": {},
                        "bindings": {
                            "press": {
                                "action": "state.set",
                                "key": "var.channel",
                                "value": "pressed",
                            }
                        },
                    },
                    {
                        "id": "btn_dead",
                        "type": "button",
                        "label": "Power",
                        "grid_area": {"col": 10, "row": 1, "col_span": 3, "row_span": 2},
                        "style": {},
                        "bindings": {
                            "press": {
                                "action": "device.command",
                                "device": "ghost_projector",
                                "command": "power_on",
                            }
                        },
                    },
                    {
                        "id": "kp1",
                        "type": "keypad",
                        "label": "Keypad",
                        "digits": 3,
                        "grid_area": {"col": 4, "row": 1, "col_span": 3, "row_span": 5},
                        "style": {},
                        "bindings": {
                            "submit": {
                                "action": "state.set",
                                "key": "var.channel",
                                "value": "$value",
                            }
                        },
                    },
                    {
                        "id": "matrix1",
                        "type": "matrix",
                        "label": "Matrix",
                        "matrix_config": {
                            "input_count": 4,
                            "output_count": 2,
                            "input_labels": ["In 1", "In 2", "In 3", "In 4"],
                            "output_labels": ["Out 1", "Out 2"],
                            "route_key_pattern": "device.sw.output_*_source",
                        },
                        "grid_area": {"col": 1, "row": 4, "col_span": 6, "row_span": 4},
                        "style": {},
                        "bindings": {
                            "route": {
                                "action": "state.set",
                                "key": "device.sw.output_$output_source",
                                "value": "$input",
                            },
                            "audio_route": {
                                "action": "state.set",
                                "key": "var.audio_route_called",
                                "value": "yes",
                            },
                            "mute_route": {
                                "action": "state.set",
                                "key": "var.mute_route_called",
                                "value": "yes",
                            },
                            "audio_mute_route": {
                                "action": "state.set",
                                "key": "var.audio_mute_route_called",
                                "value": "yes",
                            },
                        },
                    },
                    {
                        "id": "slider1",
                        "type": "slider",
                        "label": "Volume",
                        "grid_area": {"col": 7, "row": 1, "col_span": 2, "row_span": 4},
                        "style": {},
                        "bindings": {
                            "variable": {"key": "var.slider_val"},
                        },
                    },
                    {
                        "id": "list1",
                        "type": "list",
                        "label": "Sources",
                        "list_style": "selectable",
                        "items": [
                            {"label": "HDMI 1", "value": "hdmi_1"},
                            {"label": "HDMI 2", "value": "hdmi_2"},
                        ],
                        "grid_area": {"col": 9, "row": 1, "col_span": 3, "row_span": 4},
                        "style": {},
                        "bindings": {
                            "select": {
                                "action": "state.set",
                                "key": "var.channel",
                                "value_from": "element",
                            }
                        },
                    },
                ],
            },
            {
                "id": "confirm",
                "name": "Confirm",
                "page_type": "overlay",
                "overlay": {
                    "width": 400,
                    "height": 300,
                    "position": "center",
                    "backdrop": "dim",
                    "dismiss_on_backdrop": True,
                    "animation": "fade",
                },
                "grid": {"columns": 4, "rows": 4},
                "elements": [],
            },
        ],
    },
}


@pytest.fixture
async def engine_and_client():
    """Start engine with a test project, yield (engine, TestClient)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(TEST_PROJECT, f)
        tmp_path = f.name

    engine = Engine(tmp_path)

    from openavc.core.project_loader import load_project
    engine.project = load_project(tmp_path)
    engine._running = True

    # Initialize state
    engine.state.set("var.channel", "", source="system")
    engine.state.set("var.slider_val", 50, source="system")

    rest.set_engine(engine)
    ws.set_engine(engine)

    yield engine, TestClient(app)

    rest.set_engine(None)
    ws.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Connection and initial messages
# ---------------------------------------------------------------------------

async def test_ws_connect_receives_snapshot(engine_and_client):
    """WebSocket connects and receives a state snapshot with variables."""
    _, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        msg = websocket.receive_json()
        assert msg["type"] == "state.snapshot"
        snapshot = msg.get("state", {})
        assert "var.channel" in snapshot
        assert "var.slider_val" in snapshot
        assert snapshot["var.slider_val"] == 50


async def test_ws_connect_receives_ui_definition(engine_and_client):
    """WebSocket receives UI definition after snapshot."""
    _, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        _snapshot = websocket.receive_json()  # state.snapshot
        ui_msg = websocket.receive_json()     # ui.definition
        assert ui_msg["type"] == "ui.definition"
        # UI data is nested under "ui" key
        ui_data = ui_msg.get("ui", {})
        pages = ui_data.get("pages", [])
        assert len(pages) == 2
        assert pages[0]["id"] == "main"
        assert pages[1]["id"] == "confirm"


# ---------------------------------------------------------------------------
# Button press → state change
# ---------------------------------------------------------------------------

async def test_ws_press_sets_state(engine_and_client):
    """Button press triggers state.set binding, verified via state store."""
    engine, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()  # snapshot
        websocket.receive_json()  # ui.definition

        websocket.send_json({
            "type": "ui.press",
            "element_id": "btn1",
        })

        # Give the async handler a moment to process
        time.sleep(0.1)

    # Verify state was changed by the binding
    assert engine.state.get("var.channel") == "pressed"


# ---------------------------------------------------------------------------
# Keypad submit → state change
# ---------------------------------------------------------------------------

async def test_ws_submit_dispatches_event(engine_and_client):
    """Keypad submit dispatches the submit event and resolves $value.

    The state.set binding value $value now resolves through the shared
    resolver against the submit event, so it lands the submitted value (the
    keypad has no output range, so it passes through unscaled).
    """
    engine, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.submit",
            "element_id": "kp1",
            "value": "123",
        })

        time.sleep(0.1)

    # The binding value "$value" resolves to the submitted value.
    assert engine.state.get("var.channel") == "123"


# ---------------------------------------------------------------------------
# List item select → state change (the list `select` binding)
# ---------------------------------------------------------------------------

async def test_ws_select_fires_list_select_binding(engine_and_client):
    """Tapping a list item sends ui.select, which fires the list's `select`
    binding. The binding uses value_from: element, so the tapped value lands
    in state — proving the previously-dead select binding now works end to end.
    """
    engine, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()  # snapshot
        websocket.receive_json()  # ui.definition

        websocket.send_json({
            "type": "ui.select",
            "element_id": "list1",
            "value": "hdmi_2",
        })

        time.sleep(0.1)

    assert engine.state.get("var.channel") == "hdmi_2"


# ---------------------------------------------------------------------------
# Matrix route → state change
# ---------------------------------------------------------------------------

async def test_ws_route_dispatches_event(engine_and_client):
    """Matrix route dispatches the route event and sets state.

    The state.set key is not a $-reference target, so $output stays literal in
    the key. The state.set value $input now resolves through the shared resolver
    against the route event, landing the routed input number.
    """
    engine, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.route",
            "element_id": "matrix1",
            "input": 1,
            "output": 2,
        })

        time.sleep(0.1)

    # The key keeps its literal $output; the value $input resolves to the input.
    assert engine.state.get("device.sw.output_$output_source") == 1


async def test_ws_route_with_audio_fires_audio_route_binding(engine_and_client):
    """ui.route with audio=true triggers the element's audio_route binding."""
    engine, client = engine_and_client
    engine.state.set("var.audio_route_called", "no", source="system")

    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.route",
            "element_id": "matrix1",
            "input": 1,
            "output": 2,
            "audio": True,
        })

        time.sleep(0.1)

    # audio_route binding fired (sets var.audio_route_called=yes)
    assert engine.state.get("var.audio_route_called") == "yes"


async def test_ws_route_with_mute_fires_mute_route_binding(engine_and_client):
    """ui.route with mute present triggers the element's mute_route binding."""
    engine, client = engine_and_client
    engine.state.set("var.mute_route_called", "no", source="system")

    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.route",
            "element_id": "matrix1",
            "output": 2,
            "mute": True,
        })

        time.sleep(0.1)

    # mute_route binding fired (sets var.mute_route_called=yes)
    assert engine.state.get("var.mute_route_called") == "yes"


async def test_ws_route_with_audio_and_mute_fires_audio_mute_route_binding(engine_and_client):
    """ui.route with audio=true AND mute present triggers audio_mute_route binding,
    not mute_route or audio_route. Panel sends this as a secondary message when
    AFV is enabled and the user toggles a mute button."""
    engine, client = engine_and_client
    engine.state.set("var.audio_mute_route_called", "no", source="system")
    engine.state.set("var.mute_route_called", "no", source="system")
    engine.state.set("var.audio_route_called", "no", source="system")

    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.route",
            "element_id": "matrix1",
            "output": 2,
            "mute": True,
            "audio": True,
        })

        time.sleep(0.1)

    # Only audio_mute_route binding fires; mute_route and audio_route stay untouched
    assert engine.state.get("var.audio_mute_route_called") == "yes"
    assert engine.state.get("var.mute_route_called") == "no"
    assert engine.state.get("var.audio_route_called") == "no"


# ---------------------------------------------------------------------------
# Slider change → two-way variable binding
# ---------------------------------------------------------------------------

async def test_ws_change_sets_variable(engine_and_client):
    """Slider change event sets the bound variable via two-way binding."""
    engine, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.change",
            "element_id": "slider1",
            "value": 75,
        })

        time.sleep(0.1)

    assert engine.state.get("var.slider_val") == 75


# ---------------------------------------------------------------------------
# Page navigation → broadcast
# ---------------------------------------------------------------------------

async def test_ws_page_broadcasts_navigate(engine_and_client):
    """Page navigation is broadcast back to the client."""
    _, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.page",
            "page_id": "confirm",
        })

        msg = websocket.receive_json()
        assert msg["type"] == "ui.navigate"
        assert msg["page_id"] == "confirm"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

async def test_ws_press_missing_element_returns_error(engine_and_client):
    """Press with empty element_id returns error."""
    _, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.press",
            "element_id": "",
        })

        msg = websocket.receive_json()
        assert msg["type"] == "error"


async def test_a_press_that_never_reached_its_device_comes_back_with_the_reason(
    engine_and_client,
):
    """The failure the panel used to hear nothing about.

    A device command inside a binding is caught where it happens, so the rest
    of the press still runs -- and that is where it used to end, in the server
    log. From the room it looked identical to a button with nothing on it.
    """
    _, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()  # snapshot
        websocket.receive_json()  # ui.definition

        websocket.send_json({"type": "ui.press", "element_id": "btn_dead"})

        msg = websocket.receive_json()
        assert msg["type"] == "error"
        # Named by the interaction somebody performed, not by the action that
        # failed inside it.
        assert msg["source_type"] == "ui.press"
        assert "ghost_projector" in msg["message"]
        # And by the control it came from. A panel moves a control the moment
        # it is touched, so a command that never ran leaves the operator's own
        # value standing with nothing coming to correct it -- this is what
        # tells the panel which one to put back.
        assert msg["element_id"] == "btn_dead"


async def test_a_press_that_worked_says_nothing(engine_and_client):
    """Silence is still the answer when the press did what it said.

    Asserted by sending something with a known reply straight after: an error
    frame for the press would have to arrive before it.
    """
    _, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()  # snapshot
        websocket.receive_json()  # ui.definition

        websocket.send_json({"type": "ui.press", "element_id": "btn1"})
        websocket.send_json({"type": "ui.page", "page_id": "confirm"})

        msg = websocket.receive_json()
        assert msg["type"] == "ui.navigate", msg


async def test_ws_page_missing_id_returns_error(engine_and_client):
    """Page navigation with empty page_id returns error."""
    _, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.page",
            "page_id": "",
        })

        msg = websocket.receive_json()
        assert msg["type"] == "error"


async def test_ws_panel_cannot_send_restricted_types(engine_and_client):
    """Panel sending a non-allowed message type gets error."""
    _, client = engine_and_client
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()
        websocket.receive_json()

        # state.set and macro.execute are now allowed for panel (presets + plugin iframes)
        # but project.reload is still restricted
        websocket.send_json({
            "type": "project.reload",
        })

        msg = websocket.receive_json()
        assert msg["type"] == "error"


# ---------------------------------------------------------------------------
# Connection cap and rate limiting
# ---------------------------------------------------------------------------

async def test_ws_connection_cap_rejects_excess_connections(engine_and_client, monkeypatch):
    """Connections beyond MAX_WS_CONNECTIONS are rejected at the handshake."""
    from fastapi import WebSocketDisconnect

    _, client = engine_and_client
    monkeypatch.setattr(ws, "MAX_WS_CONNECTIONS", 2)
    with client.websocket_connect("/ws?client=panel") as ws1:
        ws1.receive_json()  # snapshot
        with client.websocket_connect("/ws?client=panel") as ws2:
            ws2.receive_json()  # snapshot
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws?client=panel"):
                    pass


async def test_ws_connection_cap_releases_slot_on_disconnect(engine_and_client, monkeypatch):
    """Closing a connection frees its slot for a new client."""
    _, client = engine_and_client
    monkeypatch.setattr(ws, "MAX_WS_CONNECTIONS", 1)
    with client.websocket_connect("/ws?client=panel") as ws1:
        ws1.receive_json()
    time.sleep(0.2)  # let the server side finish its disconnect cleanup
    with client.websocket_connect("/ws?client=panel") as ws2:
        msg = ws2.receive_json()
        assert msg["type"] == "state.snapshot"


async def test_ws_rate_limit_rejects_flood(engine_and_client, monkeypatch):
    """Messages beyond the per-window cap get an error and are not dispatched."""
    engine, client = engine_and_client
    monkeypatch.setattr(ws, "WS_RATE_LIMIT_MAX_MESSAGES", 5)
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()  # snapshot
        websocket.receive_json()  # ui.definition

        for _ in range(5):
            websocket.send_json({"type": "pong"})
        websocket.send_json({"type": "ui.press", "element_id": "btn1"})

        msg = websocket.receive_json()
        assert msg["type"] == "error"
        assert "rate limit" in msg["message"].lower()
        time.sleep(0.1)

    # The rate-limited press was never dispatched
    assert engine.state.get("var.channel") == ""
