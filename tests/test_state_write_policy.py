"""One write policy for every door into the state store.

State writes arrive through three remote doors — REST, the WebSocket, and the
cloud AI tools — and each used to carry its own guards. They had drifted: REST
accepted any key prefix at all, the WebSocket gated only panel clients, and the
AI tools required one of the store's six namespaces. So whether a write
succeeded depended on which door it came through, and the flat-primitive rule
existed as seven separate copies of the same isinstance tuple.

These tests pin the fix: one predicate, one policy function, same verdict
everywhere, and a static guard so a new door can't quietly grow its own copy.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from openavc.core.state_store import (
    DOOR_READONLY_PREFIXES,
    PANEL_WRITABLE_PREFIXES,
    VALID_KEY_PREFIXES,
    check_state_write,
    is_flat_primitive,
)

_SERVER = Path(__file__).resolve().parents[1] / "openavc"


# --- The policy itself ---


def test_off_namespace_key_is_rejected():
    assert check_state_write("foo.bar", 1) is not None


def test_every_valid_namespace_is_accepted():
    # "Valid" is where a key may LIVE — the panel binds by namespace, the relay
    # and ISC filter by it. Whether a door may write one is a second question,
    # and the mirrored namespaces answer it differently (see below).
    for prefix in VALID_KEY_PREFIXES:
        if prefix in DOOR_READONLY_PREFIXES:
            continue
        assert check_state_write(prefix + "thing", "x") is None, prefix


def test_empty_key_is_rejected():
    assert check_state_write("", 1) is not None


def test_nested_value_is_rejected():
    assert check_state_write("var.x", {"a": 1}) is not None
    assert check_state_write("var.x", [1, 2]) is not None


def test_panel_is_a_strict_subset_of_the_authenticated_policy():
    """The unauthenticated door narrows the same list — it never diverges."""
    assert set(PANEL_WRITABLE_PREFIXES) < set(VALID_KEY_PREFIXES)
    for prefix in set(VALID_KEY_PREFIXES) - set(PANEL_WRITABLE_PREFIXES):
        assert check_state_write(prefix + "thing", "x", panel=True) is not None
    for prefix in PANEL_WRITABLE_PREFIXES:
        assert check_state_write(prefix + "thing", "x", panel=True) is None


def test_rejection_names_what_is_allowed():
    """An integrator reading the message should not have to guess the fix."""
    err = check_state_write("foo.bar", 1)
    assert err and all(p in err for p in VALID_KEY_PREFIXES)


# --- The doors agree ---


@pytest.fixture
def client():
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


def test_rest_rejects_an_off_namespace_key(client):
    """REST had no prefix gate at all: the key landed in the store and only a
    debug line recorded it."""
    c, engine = client
    resp = c.put("/api/state/foo.bar", json={"value": 1})
    assert resp.status_code == 422
    assert not engine.state.has("foo.bar")


def test_rest_still_accepts_a_namespaced_key(client):
    c, engine = client
    resp = c.put("/api/state/var.room_active", json={"value": True})
    assert resp.status_code == 200
    assert engine.state.get("var.room_active") is True


def test_rest_rejects_a_nested_value(client):
    c, engine = client
    resp = c.put("/api/state/var.x", json={"value": {"a": 1}})
    assert resp.status_code == 422
    assert not engine.state.has("var.x")


@pytest.mark.asyncio
async def test_ws_programmer_rejects_an_off_namespace_key():
    """The programmer WebSocket was ungated too — it acked success for a write
    the store then only debug-logged."""
    from tests.test_websocket_protocol import FakeWS, _make_engine

    ws = FakeWS()
    engine = _make_engine()
    with patch("openavc.api._engine._engine", engine):
        await _handle_ws(ws, {"type": "state.set", "key": "foo.bar", "value": 1}, "programmer")
    engine.state.set.assert_not_called()
    assert ws.sent[0]["type"] == "error"


@pytest.mark.asyncio
async def test_ws_programmer_still_writes_a_namespaced_key():
    from tests.test_websocket_protocol import FakeWS, _make_engine

    ws = FakeWS()
    engine = _make_engine()
    with patch("openavc.api._engine._engine", engine):
        await _handle_ws(
            ws, {"type": "state.set", "key": "device.proj1.power", "value": "on"}, "programmer"
        )
    engine.state.set.assert_called_once()


async def _handle_ws(ws, msg, client_type):
    from openavc.api.ws import _handle_message

    await _handle_message(ws, msg, client_type)


@pytest.mark.asyncio
async def test_ai_tool_gives_the_same_verdict_as_rest():
    """Same input, same answer — the drift this item closed."""
    from openavc.cloud.tools.macro_tools import MacroToolsMixin

    class _Tools(MacroToolsMixin):
        def __init__(self):
            self._agent = type("A", (), {"state": type("S", (), {"set": lambda *a, **k: None})()})()

        def _get_engine(self):
            return None

    tools = _Tools()
    for key, value in [("foo.bar", 1), ("var.x", {"a": 1}), ("", 1)]:
        result = await tools._set_state_value({"key": key, "value": value})
        assert "error" in result, (key, value)
        assert result["error"] == check_state_write(key, value)

    ok = await tools._set_state_value({"key": "var.x", "value": 1})
    assert "error" not in ok


# --- Static guard: one definition of the predicate ---

# Three exemptions, each a genuinely different contract rather than a copy of
# this one: the engine's variant *coerces* (stringifies a driver-mapped value)
# instead of rejecting; the discovery route validates a device *config* object
# with its own length limits; and the driver loader reads scalar literals out of
# a Python driver's DRIVER_INFO, where an explicit None is dropped rather than
# kept. Sharing the state predicate with any of them would change behavior.
_PREDICATE_EXEMPT = {
    ("core", "engine.py"),
    ("api", "discovery.py"),
    ("drivers", "driver_loader.py"),
}


def test_the_flat_primitive_predicate_has_exactly_one_definition():
    """Seven copies of this isinstance tuple is how the doors drifted apart.

    Any new door must import ``is_flat_primitive`` rather than re-type the
    check, so that tightening the invariant reaches all of them at once.
    """
    offenders: list[str] = []
    # Both sweeps below walk this tree. A root that has stopped existing makes
    # every one of them pass on an empty walk, saying nothing.
    assert len(list(_SERVER.rglob("*.py"))) > 100, f"source sweep root is empty: {_SERVER}"
    for path in sorted(_SERVER.rglob("*.py")):
        rel = path.relative_to(_SERVER)
        if path.name == "state_store.py" or (rel.parts[0], path.name) in _PREDICATE_EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "isinstance" or len(node.args) != 2:
                continue
            types = node.args[1]
            if not isinstance(types, ast.Tuple):
                continue
            names = {t.id for t in types.elts if isinstance(t, ast.Name)}
            if {"str", "bool"} <= names and ("int" in names or "float" in names):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "These re-implement the flat-primitive predicate instead of importing "
        "openavc.core.state_store.is_flat_primitive:\n  " + "\n  ".join(offenders)
    )


def test_no_second_copy_of_the_key_prefix_list():
    """Two prefix lists is how REST, the WebSocket, and the AI tools disagreed."""
    offenders: list[str] = []
    for path in sorted(_SERVER.rglob("*.py")):
        if path.name == "state_store.py":
            continue
        source = path.read_text(encoding="utf-8")
        if '"device."' in source and '"isc."' in source and '"plugin."' in source:
            offenders.append(str(path.relative_to(_SERVER)))
    assert not offenders, (
        "These carry their own copy of the state-key namespace list; import "
        "VALID_KEY_PREFIXES from openavc.core.state_store instead:\n  "
        + "\n  ".join(offenders)
    )


def test_predicate_accepts_exactly_the_documented_types():
    assert is_flat_primitive(None) and is_flat_primitive("s")
    assert is_flat_primitive(1) and is_flat_primitive(1.5) and is_flat_primitive(True)
    assert not is_flat_primitive(b"bytes")
    assert not is_flat_primitive({"a": 1}) and not is_flat_primitive([1])


# ---------------------------------------------------------------------------
# A namespace the doors may read but not write
#
# isc.* holds state received FROM a peer. The policy listed it as writable, so
# a caller wrote a peer's mirror, was told it worked, and lost the value at
# that peer's next update — the one answer worse than a refusal, because it is
# indistinguishable from success until the thing it was steering stops
# responding. ISC's own writer goes through StateStore.set directly, so the
# door can refuse without costing the mesh anything.
# ---------------------------------------------------------------------------


def test_a_mirrored_namespace_is_refused_at_every_door():
    reason = check_state_write("isc.peer7.room_temp", 21)
    assert reason is not None
    # The refusal names the key, why it would not stick, and what to do instead.
    assert "isc.peer7.room_temp" in reason
    assert "var." in reason
    assert "Shared State Pattern" in reason


def test_the_refusal_does_not_depend_on_the_value():
    # Nothing about the value makes a mirrored key writable, so every value
    # gets the SAME reason. Ordering is the real assertion: with this check
    # placed after the flat-primitive one, a dict would come back told to send
    # a JSON string — advice that leads somewhere it cannot go.
    expected = check_state_write("isc.peer7.k", 21)
    for value in (21, "warm", True, None, {"nested": 1}, [1, 2]):
        assert check_state_write("isc.peer7.k", value) == expected, value


def test_a_panel_is_still_refused_for_its_own_reason():
    # Two rules reach isc.* for a panel client. The panel one runs first and
    # says the thing a panel author needs to hear.
    reason = check_state_write("isc.peer7.k", 1, panel=True)
    assert reason is not None
    assert "Panel clients" in reason


def test_the_mirrored_namespace_is_still_a_valid_key():
    # Refusing the write must not make the key illegal: a page binds to
    # isc.<peer>.<key> to show what a peer reports, and that has to keep
    # working. The two questions are separate, and only one of them changed.
    assert "isc." in VALID_KEY_PREFIXES


def test_every_other_namespace_still_writes():
    for key in (
        "var.house_lights",
        "plugin.scheduler.next_run",
        "ui.page1.visible",
        "system.help_state",
        "device.projector1.power",
    ):
        assert check_state_write(key, "x") is None, key


def test_isc_own_writer_does_not_go_through_the_door():
    """The precondition this fix rested on, pinned so it stays true.

    If ISC ever routed its peer updates through ``check_state_write``, this
    refusal would silently empty the mesh's state — the failure would be a
    room showing nothing rather than an error anybody sees. The mesh writes
    through ``StateStore.set`` with a source tag instead.
    """
    source = (_SERVER / "core" / "isc.py").read_text(encoding="utf-8")
    assert "check_state_write" not in source
    assert 'source="isc"' in source
