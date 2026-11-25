"""The Device Audit reads the driver it tests one way (``audit/driver_reading``).

Which driver a run reads (the live instance while its connection holds one,
the installed class otherwise), that a reading is never kept (a command a
driver adds or drops once connected is there on the next ask), and that no
other audit module reads a ``DRIVER_INFO``, a YAML class's ``_definition`` or
the driver registry itself. An invented device (Acme) throughout.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import openavc.audit
from openavc.audit.command_catalog import command_catalog
from openavc.audit.driver_reading import installed, model_for, reading_of
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_model import PYTHON, YAML
from openavc.drivers.registry import _DRIVER_REGISTRY


class _AcmeHub(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_hub", "name": "Acme Hub", "transport": "tcp",
        "commands": {"reboot": {"label": "Reboot"}},
    }

    async def send_command(self, command, params=None):
        return None


def _run(driver=None, driver_id="acme_hub"):
    listen = NS(sandbox=NS(driver=driver)) if driver is not None else None
    return NS(listen=listen, choice=NS(driver_id=driver_id))


def _names(reading):
    return [entry["name"] for entry in command_catalog(reading)]


def test_a_command_the_driver_adds_once_connected_is_read_on_the_next_ask(monkeypatch):
    monkeypatch.setitem(_DRIVER_REGISTRY, "acme_hub", _AcmeHub)
    hub = _AcmeHub("hub1", {}, StateStore(), EventBus())
    run = _run(hub)
    assert _names(model_for(run)) == ["reboot"]
    # Replaced, as a driver builds its commands once it knows the device.
    hub.DRIVER_INFO = {**_AcmeHub.DRIVER_INFO, "commands": {
        **_AcmeHub.DRIVER_INFO["commands"], "mute": {"label": "Mute"},
    }}
    assert _names(model_for(run)) == ["reboot", "mute"]
    # Changed in place, as a driver drops a command the device has no use for.
    hub.DRIVER_INFO = deepcopy(hub.DRIVER_INFO)
    hub.DRIVER_INFO["commands"].pop("reboot")
    assert _names(model_for(run)) == ["mute"]
    # The class was never touched.
    assert _names(installed("acme_hub")) == ["reboot"]


def test_a_run_with_no_live_driver_reads_the_installed_one(monkeypatch):
    monkeypatch.setitem(_DRIVER_REGISTRY, "acme_hub", _AcmeHub)
    assert model_for(_run()).driver is _AcmeHub
    # A connection that holds no driver (stopped, or not yet started).
    assert model_for(NS(listen=NS(sandbox=NS(driver=None)), choice=NS(driver_id="acme_hub"))).driver is _AcmeHub


def test_a_named_attempt_is_read_over_the_runs_current_one(monkeypatch):
    monkeypatch.setitem(_DRIVER_REGISTRY, "acme_hub", _AcmeHub)
    earlier = _AcmeHub("hub1", {}, StateStore(), EventBus())
    current = _AcmeHub("hub2", {}, StateStore(), EventBus())
    run = _run(current)
    assert model_for(run).driver is current
    assert model_for(run, NS(sandbox=NS(driver=earlier))).driver is earlier


def test_a_driver_no_longer_installed_declares_nothing():
    reading = installed("acme_not_installed")
    assert (reading.driver, dict(reading.info), command_catalog(reading)) == (None, {}, [])


def test_a_yaml_driver_is_read_over_the_definition_it_runs():
    """The class's own model, and a device whose protocol is merged over the
    file reads over its merged definition (its own commands with it)."""
    cls = create_configurable_driver_class({
        "id": "acme_widget", "name": "Acme Widget", "manufacturer": "Acme",
        "category": "utility", "transport": "tcp",
        "commands": {"power_query": {"label": "Power?", "send": "PWR?"}},
        "polling": {"queries": ["power_query"]},
    })
    reading = reading_of(cls)
    assert reading.model is cls._driver_model and reading.model.kind == YAML
    assert reading.polled_commands() == {"power_query"}

    config = {"host": "127.0.0.1", "commands": {"input_query": {"label": "Input?", "send": "INP?"}}}
    widget = cls("w1", config, StateStore(), EventBus())
    live = reading_of(widget)
    assert live.model.definition is widget._definition
    assert "input_query" in _names(live)
    assert live.polled_commands() == {"power_query"}


def test_a_python_driver_is_read_over_its_driver_info():
    reading = reading_of(_AcmeHub)
    assert reading.model.kind == PYTHON and reading.model.wire is None
    assert reading.polled_commands() == set()


# -- no audit module reads the driver itself ------------------------------------

# The names a module reads a driver by: its declaration, a YAML class's file,
# and the registry.
_DRIVER_READS = {"DRIVER_INFO", "_definition", "get_driver_class"}


def _driver_reads(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute) and node.attr in _DRIVER_READS:
            found.append(f"{path.name}:{node.lineno} .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _DRIVER_READS:
            found.append(f"{path.name}:{node.lineno} {node.id}")
        elif isinstance(node, ast.alias) and node.name in _DRIVER_READS:
            found.append(f"{path.name} imports {node.name}")
        elif (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("getattr", "hasattr", "setattr")
            and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _DRIVER_READS
        ):
            found.append(f"{path.name}:{node.lineno} {node.func.id}(..., {node.args[1].value!r})")
    return found


def test_no_audit_module_but_the_reading_reads_the_driver_itself():
    folder = Path(openavc.audit.__file__).parent
    reads = [
        read for path in sorted(folder.glob("*.py")) if path.name != "driver_reading.py"
        for read in _driver_reads(path)
    ]
    assert reads == []
