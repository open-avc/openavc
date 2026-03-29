"""Tests for WebSocket UI message handling."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.core.engine import Engine
from server.main import app
from server.api import rest, ws


TEST_PROJECT = {
    "project": {"id": "ws_ui_test", "name": "WS UI Test"},
    "devices": [],
    "variables": [],
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
                        "bindings": {},
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
async def client():
    """Start engine with a test project, yield TestClient."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(TEST_PROJECT, f)
        tmp_path = f.name

    engine = Engine(tmp_path)

    from server.core.project_loader import load_project
    engine.project = load_project(tmp_path)
    engine._running = True

    # Initialize state
    engine.state.set("var.channel", "", source="system")

    rest.set_engine(engine)
    ws.set_engine(engine)

    yield TestClient(app)

    rest.set_engine(None)
    ws.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)


async def test_ws_connect(client):
    """WebSocket connects and receives initial messages."""
    with client.websocket_connect("/ws?client=panel") as websocket:
        # Should receive state snapshot
        msg = websocket.receive_json()
        assert msg["type"] in ("state.snapshot", "ui.definition")


async def test_ws_press_message(client):
    """Panel can send button press messages."""
    with client.websocket_connect("/ws?client=panel") as websocket:
        # Drain initial messages
        websocket.receive_json()

        # Send press
        websocket.send_json({
            "type": "ui.press",
            "element_id": "btn1",
        })
        # Should not error — if the WS stays open, the message was handled


async def test_ws_submit_message(client):
    """Panel can send keypad submit messages."""
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.submit",
            "element_id": "kp1",
            "value": "123",
        })


async def test_ws_route_message(client):
    """Panel can send matrix route messages."""
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.route",
            "element_id": "matrix1",
            "input": 1,
            "output": 2,
        })


async def test_ws_page_message(client):
    """Panel can send page navigation messages."""
    with client.websocket_connect("/ws?client=panel") as websocket:
        websocket.receive_json()

        websocket.send_json({
            "type": "ui.page",
            "page_id": "confirm",
        })
