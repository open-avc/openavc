"""Guards for the inbound WebSocket frame-size cap on the app's listeners.

uvicorn leaves ``ws_max_size`` at an implicit 16 MiB default. Every listener
that serves ``openavc.main:app`` (and therefore the ``/ws`` and ``/isc/ws``
endpoints) must pin an explicit, tighter cap so the unauthenticated /
pre-auth socket paths can't be handed a needlessly large frame. The aux
HTTP->HTTPS redirect listener serves no WebSocket, so it is exempt (it is
constructed from a passed-in ``app`` variable, not the ``"openavc.main:app"``
import string, so the AST filter below skips it).
"""

import ast
from pathlib import Path

from openavc.main import _WS_MAX_SIZE

MAIN_PY = Path(__file__).resolve().parents[1] / "openavc" / "main.py"

# uvicorn's built-in default that we're tightening away from.
_UVICORN_DEFAULT_WS_MAX = 16 * 1024 * 1024


def test_ws_max_size_is_tighter_than_uvicorn_default():
    assert isinstance(_WS_MAX_SIZE, int)
    # Tighter than the implicit 16 MiB default...
    assert _WS_MAX_SIZE < _UVICORN_DEFAULT_WS_MAX
    # ...but still far above any legitimate control/state frame.
    assert _WS_MAX_SIZE >= 256 * 1024


def _app_listener_calls(tree):
    """Yield every uvicorn.Config / uvicorn.run Call node whose first positional
    arg is the ``"openavc.main:app"`` string — i.e. a WebSocket-serving listener."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "uvicorn"
            and func.attr in ("Config", "run")
        ):
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "openavc.main:app"
        ):
            yield node


def test_every_app_listener_caps_ws_frame_size():
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    calls = list(_app_listener_calls(tree))
    # TLS listener, plain-HTTP listener, and the default single-listener run().
    assert len(calls) >= 3, f"expected >=3 openavc.main:app listeners, found {len(calls)}"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "ws_max_size" in kwargs, (
            f"a uvicorn listener for openavc.main:app at line {call.lineno} "
            "does not pin ws_max_size"
        )
