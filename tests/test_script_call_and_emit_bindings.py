"""A control can call a script function with arguments, and emit an event.

Two silences closed together, both the same shape: a control names something
the runtime knows and nothing happens in the room.

``script.call`` used to emit ``script.call.<function>`` and nothing subscribed
that event to the function of the same name, so naming one ran it only if the
script had ALSO written ``@on_event("script.call.<function>")`` -- a spelling no
document mentions. Meanwhile the Builder offered every function in every enabled
script, so picking one off that list produced a dead button. It now calls the
function and passes what the author wrote.

``event.emit`` was a macro step and not a binding action, with no comment, doc
or test giving a reason, so a button written with one reached no branch at all.

Every device, script and function here is invented. This tests platform
capabilities.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.script_engine import ScriptEngine, describe_parameters


def _project(tmp_path: Path, elements: list[dict], variables: list[dict] | None = None) -> Path:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.12.0",
        "project": {"id": "sim", "name": "Sim", "description": ""},
        "devices": [],
        "macros": [],
        "variables": variables or [{"id": "level", "type": "number", "default": 0}],
        "ui": {
            "settings": {},
            "pages": [{"id": "main", "name": "Main", "elements": elements, "layouts": []}],
        },
    }), encoding="utf-8")
    return path


def _write_script(tmp_path: Path, filename: str, code: str) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / filename).write_text(textwrap.dedent(code), encoding="utf-8")


def _engine(
    tmp_path: Path,
    elements: list[dict],
    scripts: list[dict] | None = None,
    variables: list[dict] | None = None,
) -> Engine:
    engine = Engine(_project(tmp_path, elements, variables))
    engine.project = load_project(engine.project_path)
    se = ScriptEngine(engine.state, engine.events, engine.devices, tmp_path)
    se.install()
    if scripts:
        se.load_scripts(scripts)
    engine.scripts = se
    return engine


_ROOM = [{"id": "room", "file": "room.py", "enabled": True}]


# --- what a control passes ---------------------------------------------------

@pytest.mark.asyncio
async def test_a_button_calls_the_function_and_passes_what_the_author_wrote(tmp_path):
    """The corner this closes: three source buttons, one handler.

    Before, each needed its own script function, a variable, or a one-step
    macro to carry the value -- which is exactly the workaround the starter
    project shipped.
    """
    _write_script(tmp_path, "room.py", """\
        from openavc import state

        def select_source(source, level=0):
            state.set("var.picked", f"{source}:{level}")
    """)
    engine = _engine(tmp_path, [{
        "id": "btn_laptop", "type": "button", "label": "Laptop",
        "bindings": {"do": {"press": [{
            "action": "script.call", "function": "select_source",
            "params": {"source": "laptop", "level": 7},
        }]}},
    }], _ROOM)

    dispatched = await engine.handle_ui_event("press", "btn_laptop")

    assert engine.state.get("var.picked") == "laptop:7"
    assert dispatched == [{
        "action": "script.call", "function": "select_source",
        "params": {"source": "laptop", "level": 7}, "called": True,
    }]


@pytest.mark.asyncio
async def test_a_slider_passes_its_own_position_as_a_named_argument(tmp_path):
    """``$value`` is how the control's own data reaches a function argument.

    It resolves through the same event context a device.command param does, so
    one spelling means one thing wherever a binding carries a value.
    """
    _write_script(tmp_path, "room.py", """\
        from openavc import state

        def set_level(level):
            state.set("var.seen", level)
    """)
    engine = _engine(tmp_path, [{
        "id": "sld_vol", "type": "slider", "label": "Volume",
        "bindings": {"do": {"change": [{
            "action": "script.call", "function": "set_level",
            "params": {"level": "$value"},
        }]}},
    }], _ROOM)

    await engine.handle_ui_event("change", "sld_vol", {"value": 42})

    assert engine.state.get("var.seen") == 42


@pytest.mark.asyncio
async def test_a_variable_reference_resolves_the_same_way(tmp_path):
    _write_script(tmp_path, "room.py", """\
        from openavc import state

        def echo(text):
            state.set("var.echoed", text)
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{
            "action": "script.call", "function": "echo",
            "params": {"text": "$var.room_name"},
        }]}},
    }], _ROOM)
    engine.state.set("var.room_name", "Lecture Hall")

    await engine.handle_ui_event("press", "btn")

    assert engine.state.get("var.echoed") == "Lecture Hall"


@pytest.mark.asyncio
async def test_an_async_function_is_awaited(tmp_path):
    _write_script(tmp_path, "room.py", """\
        from openavc import state, delay

        async def warm_up():
            await delay(0)
            state.set("var.warm", True)
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{"action": "script.call", "function": "warm_up"}]}},
    }], _ROOM)

    await engine.handle_ui_event("press", "btn")

    assert engine.state.get("var.warm") is True


# --- what it refuses, and what it says ---------------------------------------

@pytest.mark.asyncio
async def test_a_name_nothing_defines_reports_a_sentence_to_the_glass(tmp_path):
    """The dead button, now with a reason attached.

    An ``error`` on a dispatch record is what the WebSocket door turns into a
    message on the panel, so the person who pressed it learns something.
    """
    _write_script(tmp_path, "room.py", """\
        def present():
            pass
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{"action": "script.call", "function": "absent"}]}},
    }], _ROOM)

    dispatched = await engine.handle_ui_event("press", "btn")

    assert dispatched[0]["called"] is False
    assert "No script function named 'absent'" in dispatched[0]["error"]


@pytest.mark.asyncio
async def test_the_rest_of_the_press_still_runs_after_a_failed_call(tmp_path):
    """A press is a list, and one bad entry must not strand the others.

    Same rule a device.command that never reached its device follows.
    """
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [
            {"action": "script.call", "function": "absent"},
            {"action": "state.set", "key": "var.after", "value": "ran"},
        ]}},
    }], _ROOM)

    dispatched = await engine.handle_ui_event("press", "btn")

    assert engine.state.get("var.after") == "ran"
    assert len(dispatched) == 2


@pytest.mark.asyncio
async def test_an_event_handler_is_not_callable_by_name(tmp_path):
    """A handler takes the Event the bus hands it, and a control has none.

    Offering one would put a name in the dropdown that can only be called
    wrong -- which is what shipped, for every function in the starter project.
    """
    _write_script(tmp_path, "room.py", """\
        from openavc import on_event, state

        @on_event("custom.thing")
        async def handle_thing(event):
            state.set("var.ran", True)
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [
            {"action": "script.call", "function": "handle_thing"},
        ]}},
    }], _ROOM)

    dispatched = await engine.handle_ui_event("press", "btn")

    assert engine.state.get("var.ran") is None
    assert "No script function named 'handle_thing'" in dispatched[0]["error"]
    assert [fn["function"] for fn in engine.scripts.get_callable_functions()] == []


@pytest.mark.asyncio
async def test_two_scripts_defining_one_name_refuse_rather_than_guess(tmp_path):
    """Whichever-loaded-first is a room that behaves differently after a reload."""
    _write_script(tmp_path, "one.py", """\
        def set_lights():
            pass
    """)
    _write_script(tmp_path, "two.py", """\
        def set_lights():
            pass
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{"action": "script.call", "function": "set_lights"}]}},
    }], [
        {"id": "one", "file": "one.py", "enabled": True},
        {"id": "two", "file": "two.py", "enabled": True},
    ])

    dispatched = await engine.handle_ui_event("press", "btn")

    assert "More than one script defines 'set_lights'" in dispatched[0]["error"]
    assert "one, two" in dispatched[0]["error"]


@pytest.mark.asyncio
async def test_naming_the_script_settles_which_one_is_meant(tmp_path):
    """What the Builder writes, because it knows which script it listed."""
    _write_script(tmp_path, "one.py", """\
        from openavc import state

        def set_lights():
            state.set("var.who", "one")
    """)
    _write_script(tmp_path, "two.py", """\
        from openavc import state

        def set_lights():
            state.set("var.who", "two")
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{
            "action": "script.call", "function": "set_lights", "script": "two",
        }]}},
    }], [
        {"id": "one", "file": "one.py", "enabled": True},
        {"id": "two", "file": "two.py", "enabled": True},
    ])

    await engine.handle_ui_event("press", "btn")

    assert engine.state.get("var.who") == "two"


@pytest.mark.asyncio
async def test_arguments_that_do_not_fit_are_caught_before_anything_runs(tmp_path):
    """Half a room is worse than none of it.

    The signature is checked first, so a mistyped parameter name cannot leave a
    function part-way through its work.
    """
    _write_script(tmp_path, "room.py", """\
        from openavc import state

        def select_source(source):
            state.set("var.ran", True)
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{
            "action": "script.call", "function": "select_source",
            "params": {"sauce": "laptop"},
        }]}},
    }], _ROOM)

    dispatched = await engine.handle_ui_event("press", "btn")

    assert engine.state.get("var.ran") is None
    assert "It takes source; this control passed sauce" in dispatched[0]["error"]


@pytest.mark.asyncio
async def test_a_function_that_raises_reports_to_the_glass_and_the_script_surface(tmp_path):
    """Both audiences at once: the room gets a sentence, the IDE gets the trace."""
    _write_script(tmp_path, "room.py", """\
        def boom():
            raise RuntimeError("no projector")
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{"action": "script.call", "function": "boom"}]}},
    }], _ROOM)
    errors: list[dict] = []
    engine.events.on("script.error", lambda _e, payload: errors.append(payload))

    dispatched = await engine.handle_ui_event("press", "btn")

    assert "'boom' failed: no projector" in dispatched[0]["error"]
    assert len(errors) == 1
    assert errors[0]["script_id"] == "room"
    assert errors[0]["handler"] == "boom"
    assert "RuntimeError" in errors[0]["traceback"]


@pytest.mark.asyncio
async def test_a_dry_run_resolves_the_arguments_and_calls_nothing(tmp_path):
    """The half worth previewing is what the arguments resolve TO."""
    _write_script(tmp_path, "room.py", """\
        from openavc import state

        def set_level(level):
            state.set("var.ran", True)
    """)
    engine = _engine(tmp_path, [{
        "id": "sld", "type": "slider",
        "bindings": {"do": {"change": [{
            "action": "script.call", "function": "set_level",
            "params": {"level": "$value"},
        }]}},
    }], _ROOM)

    dispatched = await engine.handle_ui_event("change", "sld", {"value": 9}, dry_run=True)

    assert engine.state.get("var.ran") is None
    assert dispatched == [{
        "action": "script.call", "function": "set_level",
        "params": {"level": 9}, "called": False, "would_run": True,
    }]


# --- what the Builder is told ------------------------------------------------

def test_the_parameters_reported_are_the_functions_own():
    """The Builder draws an editor off this, the way it does a device command."""
    def select_source(source: str, level=3, *, mode=None, **extra):
        pass

    described = describe_parameters(select_source)

    assert described["accepts_extra"] is True
    assert described["params"] == [
        {"name": "source", "required": True, "type": "str"},
        {"name": "level", "required": False, "default": 3, "type": "int"},
        {"name": "mode", "required": False, "default": None},
    ]


def test_a_parameter_with_nothing_to_go_on_reports_no_type():
    """A guess is worse than silence: the Builder picks its input off this."""
    def dim(level):
        pass

    assert describe_parameters(dim)["params"] == [{"name": "level", "required": True}]


@pytest.mark.asyncio
async def test_the_listing_carries_each_functions_parameters(tmp_path):
    _write_script(tmp_path, "room.py", """\
        def select_source(source, level=0):
            '''Route a source.'''
    """)
    engine = _engine(tmp_path, [], _ROOM)

    listed = engine.scripts.get_callable_functions()

    assert listed == [{
        "script": "room", "function": "select_source", "doc": "Route a source.",
        "params": [
            {"name": "source", "required": True},
            {"name": "level", "required": False, "default": 0, "type": "int"},
        ],
        "accepts_extra": False,
    }]


@pytest.mark.asyncio
async def test_calling_with_no_scripts_loaded_says_so_rather_than_raising(tmp_path):
    """The engine has no ScriptEngine before it starts, and a press can arrive."""
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{"action": "script.call", "function": "anything"}]}},
    }])
    engine.scripts = None

    dispatched = await engine.handle_ui_event("press", "btn")

    assert dispatched[0]["error"] == "No script function named 'anything'."


@pytest.mark.asyncio
async def test_a_reloaded_script_is_the_one_that_gets_called(tmp_path):
    """Hot reload replaces the module, and the call path reads it live."""
    _write_script(tmp_path, "room.py", """\
        from openavc import state

        def which():
            state.set("var.which", "first")
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{"action": "script.call", "function": "which"}]}},
    }], _ROOM)

    _write_script(tmp_path, "room.py", """\
        from openavc import state

        def which():
            state.set("var.which", "second")
    """)
    engine.scripts.reload_script({"id": "room", "file": "room.py", "enabled": True})
    await engine.handle_ui_event("press", "btn")

    assert engine.state.get("var.which") == "second"


# --- event.emit as a binding action ------------------------------------------

@pytest.mark.asyncio
async def test_a_button_emits_the_authored_payload(tmp_path):
    """The five dead buttons in the starter project, alive.

    Their author wrote event.emit on a binding, it reached no branch, and the
    workaround was a one-step macro per button.
    """
    engine = _engine(tmp_path, [{
        "id": "btn_laptop", "type": "button", "label": "Laptop",
        "bindings": {"do": {"press": [{
            "action": "event.emit", "event": "custom.select_source",
            "payload": {"source": "laptop"},
        }]}},
    }])
    heard: list[tuple[str, dict]] = []
    engine.events.on("custom.*", lambda name, payload: heard.append((name, payload)))

    dispatched = await engine.handle_ui_event("press", "btn_laptop")

    assert heard == [("custom.select_source", {"source": "laptop"})]
    assert dispatched == [{
        "action": "event.emit", "event": "custom.select_source",
        "payload": {"source": "laptop"},
    }]


@pytest.mark.asyncio
async def test_an_emitted_payload_resolves_references_too(tmp_path):
    """Same resolver as every other value a binding carries."""
    engine = _engine(tmp_path, [{
        "id": "sld", "type": "slider",
        "bindings": {"do": {"change": [{
            "action": "event.emit", "event": "custom.level",
            "payload": {"level": "$value", "room": "$var.room_name"},
        }]}},
    }])
    engine.state.set("var.room_name", "Lecture Hall")
    heard: list[dict] = []
    engine.events.on("custom.level", lambda _n, payload: heard.append(payload))

    await engine.handle_ui_event("change", "sld", {"value": 55})

    assert heard == [{"level": 55, "room": "Lecture Hall"}]


@pytest.mark.asyncio
async def test_an_emit_with_no_payload_is_a_bare_event(tmp_path):
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{"action": "event.emit", "event": "custom.ping"}]}},
    }])
    heard: list[dict] = []
    engine.events.on("custom.ping", lambda _n, payload: heard.append(payload))

    await engine.handle_ui_event("press", "btn")

    assert heard == [{}]


@pytest.mark.asyncio
async def test_a_dry_run_emits_nothing(tmp_path):
    """A preview that drives the room is not a preview."""
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{
            "action": "event.emit", "event": "custom.ping", "payload": {"n": "$var.n"},
        }]}},
    }])
    engine.state.set("var.n", 3)
    heard: list[dict] = []
    engine.events.on("custom.ping", lambda _n, payload: heard.append(payload))

    dispatched = await engine.handle_ui_event("press", "btn", dry_run=True)

    assert heard == []
    assert dispatched == [{
        "action": "event.emit", "event": "custom.ping", "payload": {"n": 3},
        "would_run": True,
    }]


@pytest.mark.asyncio
async def test_an_emitted_event_reaches_a_script_handler(tmp_path):
    """What it reaches that a direct call cannot: a subscriber by pattern.

    The same door a trigger and a plugin come in through.
    """
    _write_script(tmp_path, "room.py", """\
        from openavc import on_event, state

        @on_event("custom.select_source")
        async def handle_source(event):
            state.set("var.picked", event.get("source"))
    """)
    engine = _engine(tmp_path, [{
        "id": "btn", "type": "button",
        "bindings": {"do": {"press": [{
            "action": "event.emit", "event": "custom.select_source",
            "payload": {"source": "wireless"},
        }]}},
    }], _ROOM)

    await engine.handle_ui_event("press", "btn")

    assert engine.state.get("var.picked") == "wireless"


# --- the doors agree about what an action is ---------------------------------

def test_the_write_door_offers_exactly_what_the_runtime_dispatches():
    """A second list of action names is how a name the runtime knows gets
    refused at the write door.

    The AI's validator kept its own copy, so ``event.emit`` would have been
    rejected as "not valid" by the one door that writes most of the pages.
    Required fields stay a hand-written table -- they cannot be derived -- so
    this pins their keys instead, and a new action with no required-field entry
    fails here rather than at somebody's write.
    """
    from openavc.cloud.ai_tool_handler import _ACTION_REQUIRED_FIELDS, _VALID_ACTION_TYPES
    from openavc.core.ui_events import DISPATCHED_ACTIONS

    assert _VALID_ACTION_TYPES == DISPATCHED_ACTIONS
    assert set(_ACTION_REQUIRED_FIELDS) == set(DISPATCHED_ACTIONS)


def test_the_write_door_takes_an_emit_and_still_names_its_required_field():
    from openavc.cloud.ai_tool_handler import _validate_action

    assert _validate_action(
        {"action": "event.emit", "event": "custom.ping", "payload": {"a": 1}}, "p"
    ) is None
    assert "requires 'event'" in _validate_action({"action": "event.emit"}, "p")


def test_a_rejected_action_lists_every_name_that_works():
    """The sentence used to be a hand-typed list, one name short of the truth."""
    from openavc.cloud.ai_tool_handler import _validate_action

    message = _validate_action({"action": "delay", "seconds": 2}, "p")
    assert "event.emit" in message and "script.call" in message


@pytest.mark.asyncio
async def test_a_macro_step_resolves_its_payload_like_the_binding_does(tmp_path):
    """One spelling, one meaning.

    ``event.emit`` written as a macro step used to emit its payload verbatim,
    so a ``$var.`` reference arrived at the handler as the literal string. The
    same step written on a control resolves, and two behaviours under one name
    is what this removes.
    """
    from openavc.core.event_bus import EventBus
    from openavc.core.macro_engine import MacroEngine
    from openavc.core.state_store import StateStore

    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    state.set("var.room_name", "Lecture Hall")
    macros = MacroEngine(state, events, devices=None)
    macros.load_macros([{
        "id": "announce", "name": "Announce", "steps": [{
            "action": "event.emit", "event": "custom.announce",
            "payload": {"room": "$var.room_name", "fixed": "yes"},
        }],
    }])
    heard: list[dict] = []
    events.on("custom.announce", lambda _n, payload: heard.append(payload))

    await macros.execute("announce")

    assert heard == [{"room": "Lecture Hall", "fixed": "yes"}]


# --- the starter project uses it ---------------------------------------------

@pytest.mark.asyncio
async def test_the_starter_projects_source_buttons_reach_its_script(tmp_path):
    """The template that shipped the workaround, now doing it directly.

    Its three source buttons each ran a one-step macro whose only step emitted
    an event, because a button could not carry a value. They call one function
    now, and this presses the real button in the real shipped file: element id
    -> script id -> function name -> parameters, the chain that goes silent
    when any link is renamed.
    """
    import shutil

    templates = Path(__file__).resolve().parents[1] / "openavc" / "templates"
    project_dir = tmp_path / "project"
    (project_dir / "scripts").mkdir(parents=True)
    shutil.copy(templates / "advanced_av_suite.avc", project_dir / "project.avc")
    for script in (templates / "advanced_av_suite.scripts").glob("*.py"):
        shutil.copy(script, project_dir / "scripts" / script.name)

    engine = Engine(project_dir / "project.avc")
    engine.project = load_project(engine.project_path)
    se = ScriptEngine(engine.state, engine.events, engine.devices, project_dir)
    se.install()
    se.load_scripts([s.model_dump() for s in engine.project.scripts])
    engine.scripts = se

    sent: list[tuple] = []

    async def send(device_id, command, params):
        sent.append((device_id, command, params))

    engine.devices.send_command = send
    engine.state.set("var.system_power", "on")

    await engine.handle_ui_event("press", "btn_wireless")

    assert ("switcher1", "set_route", {"input": 2, "output": 1}) in sent
    assert engine.state.get("var.current_source") == "wireless"


def test_the_new_binding_fields_survive_a_save(tmp_path):
    """A field the runtime reads and the save drops is a control that works
    until somebody presses Save.

    ``bindings`` is an untyped dict rather than a model, so nothing inside it
    is filtered -- this pins that, because the failure mode is silent and the
    fix (declaring the field, or extra='allow') lives somewhere else entirely.
    """
    authored = {
        "do": {"press": [
            {"action": "script.call", "function": "select_source",
             "script": "room", "params": {"source": "laptop", "level": "$value"}},
            {"action": "event.emit", "event": "custom.picked",
             "payload": {"source": "laptop"}},
        ]},
    }
    path = _project(tmp_path, [{"id": "btn", "type": "button", "bindings": authored}])

    reloaded = load_project(path)

    assert reloaded.ui.pages[0].elements[0].bindings == authored
