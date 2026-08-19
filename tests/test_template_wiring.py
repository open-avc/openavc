"""A starter template's controls have to actually reach something.

`test_template_bundles.py` proves a bundle matches its loose sources. Nothing
proved the sources DO anything, and for as long as `advanced_av_suite` has
existed its three source buttons, its mic mute and its mode dropdown did
nothing at all: they carried `event.emit`, which is a macro step and not one of
the six actions a binding dispatches, so the press reached no branch and the
script waiting for that event was never called. A template is the first thing a
new user opens, and it was broken in the way that is hardest to see -- every
control drew, every read-back was correct, and the room stayed silent.

So this presses them, on a real engine, and looks at what went out to the
devices. The devices are the template's own invented ones (`switcher1`,
`dsp1`); nothing here reaches the driver repo.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, load_project
from openavc.core.script_engine import ScriptEngine
from openavc.ui.page_review import do_action_findings

TEMPLATES = Path(__file__).resolve().parents[1] / "openavc" / "templates"

#: Long enough for a macro's fire-and-forget task and the script handler it
#: wakes to finish. A binding starts a macro in the background on purpose, so
#: the press returns before the room has moved.
SETTLE_S = 0.3


def _templates() -> list[Path]:
    return sorted(TEMPLATES.glob("*.avc"))


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.stem)
def test_no_shipped_template_names_an_action_nothing_dispatches(path: Path) -> None:
    """The cheap half, over every template rather than only the one that broke."""
    project = ProjectConfig.model_validate_json(path.read_text(encoding="utf-8"))
    dead = [
        finding.message
        for page in project.ui.pages
        for element in page.elements
        for finding in do_action_findings(element.model_dump(mode="json"))
    ]
    assert dead == []


def _engine_on(template: str, tmp_path: Path) -> tuple[Engine, list[tuple]]:
    """A template running in a temp directory, with the wire faked out.

    Scripts live in `<project dir>/scripts/` at runtime and ship beside the
    template as `<name>.scripts/`, which is the rename `build_templates.py`
    does when it bundles one.
    """
    project_path = tmp_path / "project.avc"
    shutil.copy(TEMPLATES / f"{template}.avc", project_path)
    sidecar = TEMPLATES / f"{template}.scripts"
    if sidecar.is_dir():
        shutil.copytree(sidecar, tmp_path / "scripts")

    engine = Engine(project_path)
    engine.project = load_project(project_path)

    sent: list[tuple] = []

    async def send(device_id, command, params):
        sent.append((device_id, command, params))

    engine.devices.send_command = send
    engine.macros.load_macros([m.model_dump() for m in engine.project.macros])
    engine.scripts = ScriptEngine(
        engine.state, engine.events, engine.devices, tmp_path, engine.macros
    )
    engine.scripts.install()
    engine.scripts.load_scripts([s.model_dump() for s in engine.project.scripts])
    return engine, sent


@pytest.mark.asyncio
async def test_a_source_button_routes_the_switcher(tmp_path) -> None:
    """The defect, from the outside: press Laptop, watch the switcher.

    Both outputs, because the template routes the confidence monitor after the
    main display -- so this also fails if only half the handler runs.
    """
    engine, sent = _engine_on("advanced_av_suite", tmp_path)
    engine.state.set("var.system_power", "on", source="test")

    await engine.handle_ui_event("press", "btn_laptop")
    await asyncio.sleep(SETTLE_S)

    assert sent == [
        ("switcher1", "set_route", {"input": 1, "output": 1}),
        ("switcher1", "set_route", {"input": 1, "output": 2}),
    ]
    assert engine.state.get("var.current_source") == "laptop"


@pytest.mark.asyncio
async def test_the_mic_mute_button_mutes_the_dsp(tmp_path) -> None:
    engine, sent = _engine_on("advanced_av_suite", tmp_path)
    engine.state.set("var.mic_mute", False, source="test")

    await engine.handle_ui_event("press", "btn_mic_mute")
    await asyncio.sleep(SETTLE_S)

    assert sent == [("dsp1", "mute", {"channel": "mics", "muted": True})]
    assert engine.state.get("var.mic_mute") is True


@pytest.mark.asyncio
async def test_the_mode_dropdown_applies_its_preset(tmp_path) -> None:
    """The dropdown writes var.mode itself, so the handler reads it from state.

    Which makes the ORDER the whole test, and it caught the wrong one being
    shipped: `handle` emits the raw `ui.change.<id>` event FIRST, before the
    two-way write_back stores the new value, and only then runs the do binding.
    So a handler hung on the raw event reads whatever was selected last time --
    silently, and correctly on the second press of the same option. Going
    through the do binding is what puts the write first.
    """
    engine, _sent = _engine_on("advanced_av_suite", tmp_path)
    sent = _sent

    await engine.handle_ui_event("change", "sel_mode", {"value": "video"})
    await asyncio.sleep(SETTLE_S)

    assert ("dsp1", "set_fader", {"channel": "program", "level": -6.0}) in sent
    assert ("dsp1", "mute", {"channel": "mics", "muted": True}) in sent
