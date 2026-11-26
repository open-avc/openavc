"""What counts as a saved-on-this-system marker, answered in one place.

``core/saved_markers.py`` owns both markers and ``is_marker(value, kind)``.
The cases in ``fixtures/saved_marker_cases.json`` are run here, by the
Programmer's ``savedParams.test.ts``, and by every copy of the module taken
elsewhere, so each reads a value alike: a real marker as a marker, and text
that only starts like one as the value it is.

The tripwire at the end refuses a module that spells a marker itself: a
second rule is how a value came to be refused at the send while the
Programmer drew it as text.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from openavc.core import saved_markers
from openavc.core.saved_markers import ANY, LOGIN, PARAM, is_marker, param_marker, param_name

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = OPENAVC_ROOT / "tests" / "fixtures" / "saved_marker_cases.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
OWNER = OPENAVC_ROOT / "openavc" / "core" / "saved_markers.py"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus_case(case):
    value = case["value"]
    assert is_marker(value, LOGIN) is case["login"], case["name"]
    assert is_marker(value, PARAM) is case["param"], case["name"]
    assert is_marker(value, ANY) is (case["login"] or case["param"]), case["name"]
    assert param_name(value) == case["param_name"], case["name"]


def test_the_corpus_reaches_every_branch():
    """Parity over cases that never take a branch passes for free."""
    assert any(c["login"] for c in CASES), "no login marker"
    assert any(c["param"] for c in CASES), "no parameter marker"
    near = [c for c in CASES if not (c["login"] or c["param"])
            and isinstance(c["value"], str) and c["value"].startswith(saved_markers.SAVED_LOGIN[:-1])]
    assert any(c["value"].endswith(">") for c in near), "no text shaped like a marker that is not one"
    assert any(c["value"].startswith(saved_markers.PARAM_PREFIX) for c in near), "no broken parameter marker"
    assert any(not isinstance(c["value"], str) for c in CASES), "no value that is not text"


def test_a_parameter_marker_gives_back_its_name():
    assert param_name(param_marker("0123456789abcdef")) == "0123456789abcdef"
    assert is_marker(param_marker("0123456789abcdef"), PARAM)


def test_a_kind_it_does_not_know_is_refused():
    with pytest.raises(ValueError, match="Not a kind of marker"):
        is_marker(saved_markers.SAVED_LOGIN, "either")


def test_the_module_imports_nothing_from_the_package():
    """It is copied as it stands to wherever the same question is asked."""
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported <= {"__future__", "re", "typing"}, imported


def test_the_device_and_plugin_logins_hold_the_same_marker():
    """Plugins import the login marker from ``core/device_logins``."""
    from openavc.core.device_logins import SAVED_LOGIN

    assert SAVED_LOGIN is saved_markers.SAVED_LOGIN


# --- the tripwire ---------------------------------------------------------------


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _spelled_markers(path: Path) -> list[int]:
    """The lines where ``path`` holds the markers' common start in a string
    that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    start = saved_markers.SAVED_LOGIN[:-1]
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and start in node.value and id(node) not in skip
    ]


def test_no_other_module_spells_a_marker():
    found = [
        f"{path.relative_to(OPENAVC_ROOT).as_posix()}:{line}"
        for path in sorted((OPENAVC_ROOT / "openavc").rglob("*.py"))
        if path != OWNER and "node_modules" not in path.parts
        for line in _spelled_markers(path)
    ]
    assert not found, (
        "These spell a saved-on-this-system marker themselves; use "
        "openavc.core.saved_markers (SAVED_LOGIN, param_marker, is_marker):\n" + "\n".join(found)
    )
