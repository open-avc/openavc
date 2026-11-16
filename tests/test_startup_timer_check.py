"""The script check's warning on a repeating timer started in a handler that
runs only at `system.started`.

That event comes once per server start, so such a timer ticks until the
script's first edit and never again. The same `every()` at the top of the
script is armed at every load and cleared at every reload
(`test_script_engine.py` pins that half). `after()` in a startup handler is
one-time boot work and is never warned about.
"""

from __future__ import annotations

import ast
import base64
import re
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.core.event_references import (
    NO_EMITTER,
    STARTUP_TIMER,
    STARTUP_TIMER_MESSAGE,
    script_issues,
    startup_timers,
)
from openavc.core.project_loader import ProjectConfig, ProjectMeta, ScriptConfig

REPO = Path(__file__).resolve().parent.parent


def _src(code: str) -> str:
    return textwrap.dedent(code)


STARTUP_EVERY = _src("""\
    from openavc import on_event, every, log

    def poll():
        log.info("poll")

    @on_event("system.started")
    async def start(event):
        every(60, poll)
""")


def test_an_every_in_a_startup_handler_is_warned_at_its_own_line():
    assert startup_timers(STARTUP_EVERY) == [8]
    assert script_issues({"s": STARTUP_EVERY}) == {
        "s": [{
            "kind": STARTUP_TIMER,
            "line": 8,
            "event": "system.started",
            "message": STARTUP_TIMER_MESSAGE,
        }]
    }


def test_the_message_says_what_happens_and_where_to_move_it():
    assert STARTUP_TIMER_MESSAGE == (
        "This timer starts only when the server starts. Start it at the top of the "
        "script so it survives edits."
    )


@pytest.mark.parametrize("source", [
    pytest.param(_src("""\
        from openavc import on_event, after

        def warm():
            pass

        @on_event("system.started")
        def start(event):
            after(30, warm)
    """), id="after() is one-time boot work"),
    pytest.param(_src("""\
        from openavc import every

        def poll():
            pass

        every(60, poll)
    """), id="every() at the top of the script"),
    pytest.param(_src("""\
        from openavc import on_event, every

        @on_event("system.started")
        @on_event("custom.restart_polling")
        def start(event):
            every(60, print)
    """), id="a handler that also listens for something else"),
    pytest.param(_src("""\
        from openavc import on_event, every

        @on_event("system.*")
        def start(event):
            every(60, print)
    """), id="a pattern, not the event"),
    pytest.param(_src("""\
        from openavc import on_event

        @on_event("system.started")
        def start(event):
            schedule.every(10)
    """), id="another object's every"),
    pytest.param(_src("""\
        from openavc import on_event, every

        @on_event("custom.go")
        def go(event):
            every(60, print)
    """), id="a handler for another event"),
    pytest.param("def broken(:\n", id="a file mid-edit"),
])
def test_what_is_never_warned_about(source):
    assert startup_timers(source) == []


def test_a_timer_started_through_a_helper_inside_the_handler_is_still_warned():
    source = _src("""\
        import openavc
        from openavc import on_event

        @on_event("system.started")
        async def start(event):
            def arm():
                openavc.every(60, print)
            arm()
    """)
    assert startup_timers(source) == [7]


def test_both_kinds_come_back_together_in_line_order():
    source = _src("""\
        from openavc import on_event, every

        @on_event("custom.nobody_sends_this")
        async def dead(event):
            pass

        @on_event("system.started")
        async def start(event):
            every(60, print)
    """)
    issues = script_issues({"s": source})["s"]
    assert [(i["kind"], i["line"]) for i in issues] == [(NO_EMITTER, 3), (STARTUP_TIMER, 9)]


# --- What the platform teaches does not trip it -------------------------------


def _guide_timer_example() -> str:
    guide = (REPO / "docs" / "scripting-guide.md").read_text(encoding="utf-8")
    section = guide.split("### Recurring Status Check with Timer", 1)[1]
    return re.search(r"```python\n(.*?)```", section, re.S).group(1)


def _template_sources() -> dict[str, str]:
    ts = (REPO / "openavc" / "web" / "programmer" / "src" / "components" / "scripts"
          / "scriptTemplates.ts").read_text(encoding="utf-8")
    found = re.findall(r'name: "([^"]+)",\s*description: "[^"]*",\s*code: `(.*?)`,', ts, re.S)
    assert len(found) >= 2, "the templates file no longer reads as expected"
    return dict(found)


def _starts_a_timer_at_the_top(source: str) -> bool:
    return any(
        isinstance(stmt, ast.Expr | ast.Assign) and isinstance(stmt.value, ast.Call)
        and getattr(stmt.value.func, "id", "") == "every"
        for stmt in ast.parse(source).body
    )


def test_the_guides_timer_example_starts_its_timer_at_the_top():
    example = _guide_timer_example()
    assert startup_timers(example) == []
    assert _starts_a_timer_at_the_top(example)


@pytest.mark.parametrize("name", ["Periodic Timer", "Scheduled Task"])
def test_the_timer_templates_start_their_timer_at_the_top(name):
    source = _template_sources()[name]
    assert startup_timers(source) == []
    assert _starts_a_timer_at_the_top(source)


def test_no_template_starts_a_timer_in_a_startup_handler():
    for name, source in _template_sources().items():
        assert startup_timers(source) == [], name


# --- Both doors that ask ------------------------------------------------------


@pytest.fixture
def client(tmp_path, isolated_auth_config):
    """The app over a project with one script on disk."""
    from openavc.api.rest import router, set_engine

    isolated_auth_config.set("auth", "programmer_username", "engineer")
    isolated_auth_config.set("auth", "programmer_password", "disposable-timer-password")
    project_dir = tmp_path / "project"
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "scripts" / "boot.py").write_text("x = 1\n", encoding="utf-8")
    engine = MagicMock()
    engine.project_path = project_dir / "project.avc"
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        scripts=[ScriptConfig(id="boot", file="boot.py")],
    )
    app = FastAPI()
    app.include_router(router)
    set_engine(engine)
    credential = base64.b64encode(b"engineer:disposable-timer-password").decode()
    try:
        yield TestClient(app, headers={"Authorization": "Basic " + credential},
                         raise_server_exceptions=False)
    finally:
        set_engine(None)


def test_the_editor_is_told_through_the_validate_door(client):
    reply = client.post(
        "/api/scripts/validate", json={"scripts": [{"id": "boot", "source": STARTUP_EVERY}]}
    )
    assert reply.status_code == 200
    issues = reply.json()["scripts"]["boot"]["issues"]
    assert [(i["kind"], i["line"], i["message"]) for i in issues] == [
        (STARTUP_TIMER, 8, STARTUP_TIMER_MESSAGE)
    ]


async def test_the_ai_is_told_in_its_write_reply(tmp_path):
    from openavc.api import rest
    from openavc.cloud.ai_tool_handler import AIToolHandler

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "boot.py").write_text("x = 1\n", encoding="utf-8")
    engine = MagicMock()
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        scripts=[ScriptConfig(id="boot", file="boot.py")],
    )
    engine.project_path = tmp_path / "project.avc"
    engine.scripts = None
    rest.set_engine(engine)
    try:
        handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
        result = await handler._update_script_source({"script_id": "boot", "source": STARTUP_EVERY})
    finally:
        rest.set_engine(None)

    assert result["status"] == "saved"
    assert result["warnings"] == [STARTUP_TIMER_MESSAGE]
    assert result["warning_note"].startswith("The write landed -- these are warnings, not failures.")
    assert "top of the script" in result["warning_note"]
    assert "custom. event" not in result["warning_note"]
