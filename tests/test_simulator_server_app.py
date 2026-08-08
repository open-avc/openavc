"""Smoke test for the simulator FastAPI app wiring.

Importing ``openavc.simulator.server`` builds the FastAPI app at module load — routes,
middleware, and the WebSocket endpoint. A regression there raises at import
time, so the frozen simulator dies before a single device starts (the user sees
only a traceback). This guards the WebSocket registration in particular: it
broke when a Starlette release removed FastAPI's app-level
``add_websocket_route`` in favour of ``add_api_websocket_route``.
"""

from starlette.routing import WebSocketRoute


def test_simulator_app_imports_and_registers_ws():
    # The import itself is half the assertion — a bad websocket registration
    # raises AttributeError here, exactly as the frozen build did.
    from openavc.simulator.server import app

    ws_paths = {
        route.path for route in app.routes if isinstance(route, WebSocketRoute)
    }
    assert "/ws" in ws_paths
