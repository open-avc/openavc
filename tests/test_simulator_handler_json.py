"""A simulator's script handler can read and write JSON.

The handler namespace is a curated dict over an emptied ``__builtins__``, so a
name that is not in it raises ``NameError`` and the handler answers nothing.
Before ``json`` was in it, a JSON-protocol simulator had to carry its own
reader and writer -- the one protocol shape a handler cannot fake with ``re``,
because nesting, escaping and number formats all have to come out right.

Invented device (``acme_*``) and synthetic payloads: this is the handler
sandbox under test, not any specific driver.
"""

from __future__ import annotations

from openavc.simulator.yaml_auto import _SAFE_HANDLER_BUILTINS, YAMLAutoSimulator

# A JSON-over-TCP device: one object in, one object out. The handler parses
# the request, walks the parsed body (which is what `isinstance` is for) and
# serializes its answer -- none of it possible with a regex over the text.
_HANDLER = """
body = json.loads(match.group(0))
level = body.get("set", {}).get("level")
if isinstance(level, int):
    state["level"] = level
respond(json.dumps({"level": state["level"], "ok": True}) + "\\r")
"""

_DEFINITION = {
    "id": "acme_json_amp",
    "name": "Acme JSON Amp",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "author": "Test",
    "description": "Invented JSON device for handler-sandbox tests",
    "source_url": "https://example.com",
    "transport": "tcp",
    "delimiter": "\\r",
    "config_schema": {"host": {"type": "string", "required": True}},
    "default_config": {"host": "", "port": 5000},
    "state_variables": {"level": {"type": "integer", "min": 0, "max": 100}},
    "commands": {
        "set_level": {
            "send": '{{"set":{{"level":{level}}}}}',
            "label": "Set Level",
            "params": {"level": {"type": "integer", "min": 0, "max": 100}},
        },
    },
    "responses": [
        {"json": True, "set": {"level": {"key": "level", "type": "integer"}}},
    ],
    "simulator": {
        "initial_state": {"level": 50},
        "command_handlers": [
            {"match": r'\{"set":.*\}', "handler": _HANDLER},
        ],
    },
}


def test_json_and_isinstance_are_in_the_handler_namespace():
    """Both names, so a handler can parse a body and ask what it got."""
    assert _SAFE_HANDLER_BUILTINS["json"].loads('{"a":1}') == {"a": 1}
    assert _SAFE_HANDLER_BUILTINS["isinstance"] is isinstance


def test_a_handler_parses_a_json_request_and_serializes_its_reply():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_DEFINITION)

    reply = sim.handle_command(b'{"set":{"level":42}}\r')

    assert reply is not None
    assert _SAFE_HANDLER_BUILTINS["json"].loads(reply.decode().strip()) == {
        "level": 42,
        "ok": True,
    }
    assert sim.get_state("level") == 42


def test_a_body_the_handler_rejects_leaves_state_alone():
    """The isinstance guard is the handler's own -- a string where a number
    belongs must not become the level."""
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_DEFINITION)

    reply = sim.handle_command(b'{"set":{"level":"loud"}}\r')

    assert reply is not None and b'"level": 50' in reply
    assert sim.get_state("level") == 50


def test_a_name_outside_the_namespace_still_answers_nothing():
    """The sandbox is unchanged apart from the two additions: a handler
    reaching for anything else is caught, logged, and returns no reply --
    which is why a simulator that has to run on an older platform cannot use
    a name that platform lacks."""
    definition = dict(_DEFINITION)
    definition["simulator"] = {
        **_DEFINITION["simulator"],
        "command_handlers": [
            {"match": r'\{"set":.*\}', "handler": "respond(base64.b64encode(b'x'))"},
        ],
    }
    sim = YAMLAutoSimulator("dev2", config={}, driver_def=definition)

    assert sim.handle_command(b'{"set":{"level":1}}\r') is None
