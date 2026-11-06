"""A device name from a request is cleaned in one pass, at both doors.

Renaming a device (PATCH /api/devices/{id}) and adding one from a discovery
scan (POST /api/discovery/add-device) store the name with any markup taken
out, whitespace collapsed and at most 128 characters kept. Both run on the
event loop, and a request body has no size limit, so the clean-up has to be
linear in the name's length: a quadratic one holds every panel and every
Programmer tab for as long as it runs.

The markup rule is the one the doors have always applied, held here to the
regex they used to run, on every short string over the characters that
matter.
"""

from __future__ import annotations

import itertools
import re
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest, ws
from openavc.api.routes.devices import sanitize_device_name, strip_markup
from openavc.core.engine import Engine
from openavc.core.project_loader import (
    DeviceConfig,
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
)
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.main import app

# What both doors used to run. Correct, but it scans to the end of the text
# from every "<" that has no ">" after it.
_OLD_PATTERN = re.compile(r"<[^>]+>")

# A run of "<" with no ">" after it: about ten seconds under the old pattern
# on a dev Mac, and four times that for every doubling.
_HOSTILE = "<" * 200_000

# The one-pass strip takes milliseconds on that input.
_STRIP_BOUND_S = 1.0
# A whole request, JSON and project save included; the old pattern alone was
# several times this on the fastest machine it ran on.
_DOOR_BOUND_S = 3.0


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_strip_markup_agrees_with_the_old_pattern_on_every_short_string():
    checked = 0
    for length in range(8):
        for chars in itertools.product("<>a ", repeat=length):
            text = "".join(chars)
            assert strip_markup(text) == _OLD_PATTERN.sub("", text), repr(text)
            checked += 1
    assert checked == sum(4 ** n for n in range(8))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Projector <b>Left</b>", "Projector Left"),
        ("a < b", "a < b"),
        ("<>", "<>"),
        ("<<b>Hall", "Hall"),
        # A tag split by another one does not join up again. The linear
        # pattern that looks like a drop-in, <[^<>]+>, leaves "<img ...>" here.
        ("<im<x>g src=x onerror=alert(1)>", "g src=x onerror=alert(1)>"),
    ],
)
def test_strip_markup_examples(text, expected):
    assert strip_markup(text) == expected


@pytest.mark.parametrize(
    "text", [_HOSTILE, "<a" * 100_000], ids=["lt-run", "lt-letter-run"]
)
def test_a_long_run_with_no_closing_bracket_is_one_pass(text):
    started = time.perf_counter()
    assert strip_markup(text) == text
    assert time.perf_counter() - started < _STRIP_BOUND_S


def test_sanitize_device_name_collapses_whitespace_and_caps_the_length():
    name = "  <i>Main</i>\n  Hall\t" + "x" * 200
    cleaned = sanitize_device_name(name)
    assert cleaned.startswith("Main Hall xxx")
    assert len(cleaned) == 128


# ---------------------------------------------------------------------------
# The two doors
# ---------------------------------------------------------------------------


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_names",
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 5000},
        "state_variables": {},
        "commands": {},
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
def widget_driver():
    register_driver(_AcmeWidget)
    yield _AcmeWidget
    unregister_driver("acme_widget_names")


@pytest.fixture
async def rename_client(tmp_path, widget_driver):
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig(
        project=ProjectMeta(id="names", name="Names"),
        devices=[
            DeviceConfig(
                id="widget1", driver="acme_widget_names", name="Widget", config={}
            ),
        ],
        connections={"widget1": {"host": "192.0.2.10", "port": 5000}},
    )
    save_project(project_path, engine.project)
    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device))
    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app), engine
    finally:
        await engine.devices.disconnect_all()
        rest.set_engine(None)
        ws.set_engine(None)


async def test_renaming_a_device_cleans_the_name_in_one_pass(rename_client):
    client, engine = rename_client
    name = "<b>Left</b>  Display " + _HOSTILE

    started = time.perf_counter()
    resp = client.patch("/api/devices/widget1", json={"name": name})
    elapsed = time.perf_counter() - started

    assert resp.status_code == 200, resp.text
    saved = load_project(engine.project_path).devices[0].name
    assert saved == sanitize_device_name(name)
    assert saved.startswith("Left Display <")
    assert elapsed < _DOOR_BOUND_S


async def test_adding_a_discovered_device_cleans_the_name_in_one_pass(
    tmp_path, widget_driver, monkeypatch
):
    from openavc.api import discovery as discovery_api

    fake_discovery = MagicMock()
    fake_discovery.results = {}
    monkeypatch.setattr(discovery_api, "_engine", fake_discovery)
    engine = Engine(str(tmp_path / "test.avc"))
    engine.project = ProjectConfig(
        project=ProjectMeta(id="names", name="Names"), devices=[], connections={}
    )
    monkeypatch.setattr(
        "openavc.core.project_loader.save_project", lambda *a, **k: None
    )
    monkeypatch.setattr(discovery_api, "_app_engine", engine)
    name = "<b>Hall</b>  Widget " + _HOSTILE

    try:
        started = time.perf_counter()
        result = await discovery_api.add_device(
            discovery_api.AddDeviceRequest(
                ip="192.0.2.60", driver_id="acme_widget_names", name=name
            )
        )
        elapsed = time.perf_counter() - started

        assert result["status"] == "ok"
        saved = engine.project.devices[-1].name
        assert saved == sanitize_device_name(name)
        assert saved.startswith("Hall Widget <")
        assert elapsed < _DOOR_BOUND_S
    finally:
        await engine.devices.disconnect_all()
