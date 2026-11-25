"""A child type's variables, as every reader takes them.

Every child has ``online``, ``label``, ``offline_reason`` and
``offline_detail`` whether its type declares them or not
(``spec.CHILD_RESERVED_PROP_SCHEMA``); a type that declares one keeps its own.
One reading (``declared_names.child_variables``) answers it for the runtime's
schema of a child, a reply rule's typing of a child's values, the checker's
reading of a reply map and the simulator, so a child's ``online`` is a flag
wherever it is read. An invented device (Acme) throughout.
"""

from __future__ import annotations

from pathlib import Path

import openavc
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.declared_names import child_variables, child_writable_props
from openavc.drivers.spec import CHILD_RESERVED_PROP_SCHEMA
from openavc.simulator.yaml_auto import YAMLAutoSimulator

_OUTPUT = {"state_variables": {"input": {"type": "integer"}, "label": {"type": "string", "label": "Name"}}}


def test_the_reading():
    variables = child_variables(_OUTPUT)
    # The type's own first, then each property every child has that it does
    # not declare, its own declaration winning.
    assert list(variables) == ["input", "label", "online", "offline_reason", "offline_detail"]
    assert variables["label"] == {"type": "string", "label": "Name"}
    assert variables["online"] == {"type": "boolean", "label": "Online"}
    assert child_variables(None) == CHILD_RESERVED_PROP_SCHEMA
    assert child_writable_props(_OUTPUT) == set(variables)
    # A copy: a reader that edits it edits no other reader's.
    child_variables(None)["online"]["type"] = "string"
    assert CHILD_RESERVED_PROP_SCHEMA["online"]["type"] == "boolean"


def test_the_runtime_schema_of_a_child_is_the_reading():
    class _AcmeMatrix(BaseDriver):
        DRIVER_INFO = {"id": "acme_widget", "name": "Acme", "transport": "tcp",
                       "child_entity_types": {"output": _OUTPUT}}

        async def send_command(self, command, params=None):
            return None

    matrix = _AcmeMatrix("m1", {}, StateStore(), EventBus())
    assert matrix._effective_child_schema("output") == child_variables(_OUTPUT)


def test_the_simulator_types_a_property_every_child_has_as_the_runtime_does():
    """A command addressed to one child that sets its ``online`` stores a
    flag, not the text ``"False"`` (which reads as true)."""
    sim = YAMLAutoSimulator("w1", {}, driver_def={
        "id": "acme_widget", "name": "Acme", "transport": "tcp", "delimiter": "\r",
        "child_entity_types": {"output": _OUTPUT},
        "commands": {"mark_offline": {
            "send": "OFF{output}",
            "params": {"output": {"type": "child_id", "child_type": "output"}},
            "sets": {"online": False},
        }},
    })
    sim._register_sim_children("output", [1, 2])
    (handler,) = sim._command_handlers
    sim._execute_command_handler(handler, handler.pattern.match("OFF2"))
    assert sim._state["output.2.online"] is False
    assert "online" not in sim._state


def test_no_module_but_the_reading_adds_the_properties_itself():
    root = Path(openavc.__file__).parent
    readers = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*.py")
        if path.relative_to(root).as_posix() not in {"drivers/spec.py", "drivers/declared_names.py"}
        and "CHILD_RESERVED_PROP_SCHEMA" in path.read_text(encoding="utf-8")
    )
    assert readers == []
