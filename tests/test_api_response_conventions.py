"""The REST surface's response-shape conventions, enforced.

These are the rules documented as the 1.x compatibility surface: collections
come back enveloped in an object, mutations acknowledge with ``status``,
``success`` is reserved for calls that can fail at HTTP 200, and identifiers
are echoed under an explicit ``<thing>_id`` name.

The static half walks every route handler in ``server/api`` so a new endpoint
that returns a bare array (or says ``ok``) fails here rather than shipping and
freezing into the surface. The behavioral half pins a representative endpoint
per rule, so the conventions are checked against the running app too.
"""

import ast
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parent.parent / "server" / "api"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _route_handlers(tree: ast.AST):
    """Yield (function_node, [route_labels]) for every HTTP route handler."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = []
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            method = dec.func.attr
            if method not in _HTTP_METHODS:
                continue
            path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else "?"
            routes.append(f"{method.upper()} {path}")
        if routes:
            yield node, routes


def _api_modules():
    for path in sorted(API_ROOT.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _annotation_is_list(node: ast.expr | None) -> bool:
    """True for `list[...]` / `List[...]` return annotations."""
    if node is None:
        return False
    target = node.value if isinstance(node, ast.Subscript) else node
    name = getattr(target, "id", None) or getattr(target, "attr", None)
    return name in ("list", "List")


def test_no_route_returns_a_bare_array():
    """Rule 1: collections are enveloped in an object.

    A bare top-level array can never grow sibling metadata without breaking
    every client, and it forces callers to branch on the JSON's top-level type.
    """
    offenders = []
    for path, tree in _api_modules():
        for fn, routes in _route_handlers(tree):
            if _annotation_is_list(fn.returns):
                offenders.append(f"{path.name}:{fn.name} ({', '.join(routes)})")
    assert not offenders, (
        "These route handlers return a bare array — wrap the collection in an "
        "object keyed by its plural name, e.g. {'devices': [...]}:\n  "
        + "\n  ".join(offenders)
    )


def test_no_route_acknowledges_with_ok():
    """Rules 2+3: mutations say ``status``; fallible commands say ``success``.

    ``ok`` was a third spelling of the same idea. It survives *inside*
    ``server/system/network.py`` and ``server/host_control.py`` (those mirror
    the privileged helper's on-disk protocol), but the HTTP boundary translates.
    """
    offenders = []
    for path, tree in _api_modules():
        for fn, routes in _route_handlers(tree):
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)):
                    continue
                keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
                if "ok" in keys:
                    offenders.append(f"{path.name}:{fn.name} line {node.lineno} ({', '.join(routes)})")
    assert not offenders, (
        "These route handlers return an 'ok' key. Use {'status': '<verb>'} for a "
        "mutation, or {'success': bool} for a command that can fail at HTTP 200:\n  "
        + "\n  ".join(offenders)
    )


def test_no_route_echoes_a_bare_id():
    """Rule 4: an echoed identifier names its resource (``theme_id``, not ``id``).

    Bare ``id`` belongs inside a record that carries its own identity — a device
    in the devices list — not as a top-level echo, where it reads as "whose?".
    """
    offenders = []
    for path, tree in _api_modules():
        for fn, routes in _route_handlers(tree):
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)):
                    continue
                keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
                # Only a top-level echo alongside an acknowledgement is wrong;
                # a lone {"id": ...} is a record being returned whole.
                if "id" in keys and ("status" in keys or "success" in keys):
                    offenders.append(f"{path.name}:{fn.name} line {node.lineno} ({', '.join(routes)})")
    assert not offenders, (
        "These route handlers echo a bare 'id' next to an acknowledgement — name "
        "it (driver_id, theme_id, project_id, script_id):\n  " + "\n  ".join(offenders)
    )


# --- Behavioral half: the conventions against the running app ---


@pytest.fixture
def client():
    """TestClient with the same mock engine the REST endpoint tests use."""
    from fastapi.testclient import TestClient

    from server.api import rest, ws
    from server.main import app
    from tests.test_api_endpoints import _make_mock_engine

    engine = _make_mock_engine()
    rest.set_engine(engine)
    ws.set_engine(engine)
    yield TestClient(app), engine
    rest.set_engine(None)
    ws.set_engine(None)


def test_devices_collection_is_enveloped(client):
    c, engine = client
    engine.devices.list_devices.return_value = [{"id": "dev1", "name": "Projector"}]
    body = c.get("/api/devices").json()
    assert isinstance(body, dict)
    assert body["devices"] == [{"id": "dev1", "name": "Projector"}]


def test_state_snapshot_is_enveloped(client):
    c, engine = client
    engine.state.set("var.x", 1, source="test")
    body = c.get("/api/state").json()
    assert isinstance(body, dict)
    assert body["state"]["var.x"] == 1


def test_device_partial_update_is_patch_not_put(client):
    """Rule 5: the body is a partial merge, so the verb is PATCH.

    PUT on the same path must not answer — a client sending a partial body to
    PUT deserves a 405, not a silent merge behind a replace-shaped verb.
    """
    c, _ = client
    assert c.put("/api/devices/dev1", json={"name": "x"}).status_code == 405
