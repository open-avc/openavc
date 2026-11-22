"""The script editor's completions are the scripting API, rendered.

``python -m openavc.core.script_api_gen`` renders ``openavc/__init__.py``'s
``__all__``, each object's members in ``core/script_api.py``, the words in
``core/script_api_help.py`` and ``core/event_bus.EVENTS`` into the IDE's
``scriptApi.gen.ts``. This re-renders and compares byte for byte, so a name
added to the API, a member removed, or a hand edit to the file fails here
instead of leaving the editor offering a name that does not exist. It also
holds the words to the names, each example to Python, and each decorator's
snippet to the handler form the script engine loads.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

import openavc
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.script_api_gen import ARTIFACT, render, surface, surface_paths
from openavc.core.script_api_help import HELP
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_store import StateStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_committed_artifact_matches_the_api() -> None:
    path = REPO_ROOT / ARTIFACT
    assert path.is_file(), f"{ARTIFACT} is missing -- run 'python -m openavc.core.script_api_gen'"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render(), (
        f"{ARTIFACT} does not match the scripting API -- regenerate with "
        f"'python -m openavc.core.script_api_gen' (never edit it by hand)"
    )


def test_every_published_name_is_offered() -> None:
    names, _members = surface()
    assert [n["name"] for n in names] == list(openavc.__all__)


def test_each_object_offers_its_members() -> None:
    _names, members = surface()
    assert {m["name"] for m in members["state"]} >= {"get", "set", "delete", "get_namespace"}
    assert {m["name"] for m in members["isc"]} >= {"send_command", "RemoteError"}
    assert "plugins" not in members, "a plugin's methods are the running plugin's"


def test_every_name_has_words_and_every_word_a_name() -> None:
    paths = surface_paths()
    assert sorted(HELP) == sorted(paths), (
        "core/script_api_help.py HELP and the scripting API differ: "
        f"no words for {sorted(set(paths) - set(HELP))}, "
        f"words for nothing {sorted(set(HELP) - set(paths))}"
    )


def test_what_is_awaited_is_marked() -> None:
    _names, members = surface()
    by_path = {f"{o}.{m['name']}": m for o, listed in members.items() for m in listed}
    assert by_path["devices.send"]["awaited"]
    assert not by_path["macros.start"]["awaited"]
    assert by_path["devices.list"]["returns"] == "list[dict[str, Any]]"


@pytest.mark.parametrize("path", sorted(k for k, h in HELP.items() if h.example))
def test_every_example_is_python(path: str) -> None:
    example = HELP[path].example
    ast.parse("async def _example():\n" + textwrap.indent(example, "    "))


@pytest.mark.parametrize("path", sorted(k for k, h in HELP.items() if h.summary))
def test_the_words_follow_the_writing_rules(path: str) -> None:
    for text in (HELP[path].summary, HELP[path].example):
        assert "—" not in text, f"{path}: no em dashes"


FILLED = {"event_pattern": "custom.test", "key_pattern": "var.test"}


def _filled(snippet: str) -> str:
    """A snippet as the editor leaves it once each place is filled in."""
    text = re.sub(r"\$\{(\d+):([^}]*)\}", lambda m: FILLED.get(m[2], m[2]), snippet)
    return text.replace("${0}", "pass")


@pytest.mark.parametrize("name", sorted(k for k, h in HELP.items() if h.snippet))
def test_a_decorator_s_snippet_loads(name: str, tmp_path: Path) -> None:
    """The handler a decorator's snippet writes is one the engine loads."""
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    (tmp_path / "scripts").mkdir()
    engine = ScriptEngine(state, events, DeviceManager(state, events), tmp_path)
    engine.install()
    try:
        source = "from openavc import *\n\n@" + _filled(HELP[name].snippet) + "\n"
        (tmp_path / "scripts" / "snippet.py").write_text(source, encoding="utf-8")
        count = engine.load_scripts([{"id": "snippet", "file": "snippet.py", "enabled": True}])
        assert not engine.get_load_errors(), f"{source}\n{engine.get_load_errors()}"
        assert count == 1
    finally:
        engine.unload_all()
