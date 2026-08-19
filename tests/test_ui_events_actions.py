"""The closed set of binding actions, and what a name outside it does now.

``ui_events.execute_action`` dispatches six action names. Anything else used to
fall off the end of that chain in silence: the panel sent the interaction, the
runtime walked the list, and nothing happened -- indistinguishable, from the
room, from a dead device. The write door (``ui.page_review``) now warns when the
AI or the Builder authors one, and the chain itself warns for the population no
door sees: a hand-edited ``.avc``, and a project written against a different
version.

Every device and macro here is invented. This tests a platform capability.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.ui_events import DISPATCHED_ACTIONS

UI_EVENTS_SRC = (
    Path(__file__).resolve().parents[1] / "openavc" / "core" / "ui_events.py"
)


def _project(tmp_path: Path, elements: list[dict]) -> Path:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.11.0",
        "project": {"id": "sim", "name": "Sim", "description": ""},
        "devices": [],
        "macros": [],
        "variables": [],
        "ui": {
            "settings": {},
            "pages": [{"id": "main", "name": "Main", "elements": elements,
                       "layouts": []}],
        },
    }), encoding="utf-8")
    return path


def _engine(tmp_path: Path, elements: list[dict]) -> Engine:
    engine = Engine(_project(tmp_path, elements))
    engine.project = load_project(engine.project_path)
    return engine


def _button(el_id: str, action: dict) -> dict:
    return {
        "id": el_id, "type": "button", "label": el_id,
        "bindings": {"do": {"press": [action]}},
    }


def _names_the_chain_compares() -> set[str]:
    """Every string ``execute_action`` compares ``action`` against.

    Parsed rather than trusted, because ``DISPATCHED_ACTIONS`` is a second
    spelling of the chain and the two are only useful while they agree. Three
    readers depend on that set -- the review, the Builder through a generated
    table, and the warning below -- so a branch added without touching the
    constant would make all three quietly wrong about one action.
    """
    tree = ast.parse(UI_EVENTS_SRC.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "execute_action":
            continue
        for cmp_node in ast.walk(node):
            if not isinstance(cmp_node, ast.Compare):
                continue
            left = cmp_node.left
            if not (isinstance(left, ast.Name) and left.id == "action"):
                continue
            for op, comparator in zip(cmp_node.ops, cmp_node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                    found.add(comparator.value)
    return found


def test_the_constant_is_the_chain() -> None:
    chain = _names_the_chain_compares()
    assert chain, "found no `action == \"...\"` branches -- teach the parse, not the test"
    assert chain == set(DISPATCHED_ACTIONS)


@pytest.mark.asyncio
async def test_an_action_nothing_dispatches_is_reported(tmp_path, caplog) -> None:
    """The exact defect: `navigate` for what the runtime calls `ui.navigate`."""
    engine = _engine(tmp_path, [_button("btn_nav", {"action": "navigate", "page": "av"})])

    with caplog.at_level("WARNING"):
        await engine.handle_ui_event("press", "btn_nav")

    assert "btn_nav" in caplog.text
    assert "'navigate'" in caplog.text
    # The valid set, because a line saying only that it is wrong leaves the
    # reader exactly where they were.
    assert "ui.navigate" in caplog.text


@pytest.mark.asyncio
async def test_an_entry_with_no_action_is_reported(tmp_path, caplog) -> None:
    engine = _engine(tmp_path, [_button("btn_half", {"device": "acme", "command": "on"})])

    with caplog.at_level("WARNING"):
        await engine.handle_ui_event("press", "btn_half")

    assert "no action" in caplog.text
    assert "btn_half" in caplog.text


@pytest.mark.asyncio
async def test_it_is_said_once_per_element_and_action(tmp_path, caplog) -> None:
    """Deduped per (element, action), not per press.

    A bad action on a slider fires on every tick of a drag, and a real signal
    buried in log spam is not a signal.
    """
    engine = _engine(tmp_path, [
        _button("btn_a", {"action": "navigate", "page": "av"}),
        _button("btn_b", {"action": "navigate", "page": "av"}),
    ])

    with caplog.at_level("WARNING"):
        for _ in range(5):
            await engine.handle_ui_event("press", "btn_a")
        await engine.handle_ui_event("press", "btn_b")

    lines = [r for r in caplog.records if "nothing dispatches" in r.getMessage()]
    assert len(lines) == 2, "five presses on one element, plus a second element"
    assert {"btn_a", "btn_b"} == {
        el for el in ("btn_a", "btn_b")
        if any(el in r.getMessage() for r in lines)
    }


@pytest.mark.asyncio
async def test_a_reload_makes_it_say_so_again(tmp_path, caplog) -> None:
    """Silence after an edit reads as having fixed it."""
    engine = _engine(tmp_path, [_button("btn_a", {"action": "navigate", "page": "av"})])

    with caplog.at_level("WARNING"):
        await engine.handle_ui_event("press", "btn_a")
        engine.ui_events.forget_undispatched_actions()
        await engine.handle_ui_event("press", "btn_a")

    lines = [r for r in caplog.records if "nothing dispatches" in r.getMessage()]
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_the_press_reports_that_it_ran_nothing(tmp_path) -> None:
    """An empty record list is what a control with NO binding returns.

    `simulate_ui_action` is how the cloud AI checks its own write, and that
    ambiguity is the thing it was built to remove -- so an action that reached
    no branch has to come back saying so, rather than looking like a button
    nobody wired.
    """
    engine = _engine(tmp_path, [_button("btn_nav", {"action": "navigate", "page": "av"})])

    dispatched = await engine.handle_ui_event("press", "btn_nav")

    assert dispatched == [{"action": "navigate", "ran": False}]
    # No `error`: the WebSocket door turns one of those into a message on the
    # glass, and the name of an action is not something a room can act on.
    assert "error" not in dispatched[0]


@pytest.mark.asyncio
async def test_an_action_the_chain_knows_says_nothing(tmp_path, caplog) -> None:
    engine = _engine(tmp_path, [_button("btn_ok", {"action": "ui.navigate", "page": "av"})])

    with caplog.at_level("WARNING"):
        await engine.handle_ui_event("press", "btn_ok")

    assert "nothing dispatches" not in caplog.text
