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


def _raises_with_status_and_detail(tree: ast.AST):
    """Yield (lineno, status, detail_node, caught_exception_names) for error raises.

    Covers both spellings — a raw ``HTTPException`` and the ``api_error`` helper
    — and walks whole modules, not just route handlers, because plenty of these
    raises live in the module-level helpers the handlers call.
    """
    def walk(node, caught: tuple[str, ...]):
        if isinstance(node, ast.ExceptHandler) and node.name:
            caught = caught + (node.name,)
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in ("HTTPException", "api_error", "_api_error", "StructuredApiError"):
                status = detail = None
                for kw in node.keywords:
                    if kw.arg == "status_code" and isinstance(kw.value, ast.Constant):
                        status = kw.value.value
                    elif kw.arg == "detail":
                        detail = kw.value
                if status is None and node.args and isinstance(node.args[0], ast.Constant):
                    status = node.args[0].value
                if detail is None and len(node.args) >= 2:
                    detail = node.args[1]
                if detail is not None:
                    yield node.lineno, status, detail, caught
        for child in ast.iter_child_nodes(node):
            yield from walk(child, caught)

    yield from walk(tree, ())


def test_error_detail_is_always_a_string():
    """Rule 6: errors are ``{"detail": <string>}``.

    An object ``detail`` hides the readable sentence inside a shape every error
    extractor then needs a special case for — and the ones without it show the
    user raw JSON. Machine-readable fields belong beside ``detail``, which is
    what ``StructuredApiError`` is for.
    """
    offenders = []
    for path, tree in _api_modules():
        for lineno, _status, detail, _caught in _raises_with_status_and_detail(tree):
            if isinstance(detail, ast.Dict):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "These raises pass a dict as 'detail'. Keep detail a readable string and "
        "put the machine-readable fields beside it with StructuredApiError:\n  "
        + "\n  ".join(offenders)
    )


def test_server_errors_do_not_leak_exception_text():
    """Rule: a 500 reports a sentence, not the exception that caused it.

    Raw exception text is a stack-trace fragment, an OS error, or a filesystem
    path — noise to the integrator reading it and detail we would rather not
    hand out. ``api_error(500, msg, exc)`` logs the real thing server-side and
    returns the sentence.

    Exactly 500, deliberately. Every other 5xx names a specific condition where
    the exception message *is* the answer: a 501 carries the driver's own "this
    device can't do that", a 502 carries what the upstream said. 4xx is exempt
    for the same reason — there the message is usually our own validation code
    telling the caller what they got wrong, in their words.
    """
    roots = list(_api_modules())
    # The simulator serves its own REST API from a separate process; same rule.
    sim_api = API_ROOT.parent.parent / "simulator" / "api.py"
    roots.append((sim_api, ast.parse(sim_api.read_text(encoding="utf-8"))))

    offenders = []
    for path, tree in roots:
        for lineno, status, detail, caught in _raises_with_status_and_detail(tree):
            if not caught or status is None or int(status) != 500:
                continue
            # Reading one field off the exception is fine and often the useful
            # part — a 502 saying `GitHub returned {e.response.status_code}`
            # tells the user something real. Stringifying the whole exception
            # (`str(e)`, `f"...{e}"`) is what hands out internals.
            read_as_attribute = {
                id(sub.value) for sub in ast.walk(detail)
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
            }
            leaked = {
                sub.id for sub in ast.walk(detail)
                if isinstance(sub, ast.Name)
                and sub.id in caught
                and id(sub) not in read_as_attribute
            }
            if leaked:
                offenders.append(f"{path.name}:{lineno} (stringifies {', '.join(sorted(leaked))})")
    assert not offenders, (
        "These 5xx responses interpolate the caught exception into the message. "
        "Use api_error(status, '<what failed, in a sentence>', exc) so the real "
        "exception is logged instead:\n  " + "\n  ".join(offenders)
    )


# --- Behavioral half: the conventions against the running app ---


@pytest.fixture
def client():
    """TestClient with the same mock engine the REST endpoint tests use."""
    from fastapi.testclient import TestClient

    from openavc.api import rest, ws
    from openavc.main import app
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
