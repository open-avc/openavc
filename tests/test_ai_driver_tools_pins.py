"""The AI's driver tools, pinned where nothing else looked.

Each passes on the code as it is, so a restructure that changes an answer, a
write or a fetch fails here:

- The community catalog: how a free-text query scores an entry (each field's
  weight), the filters, the ranking, the limit and how a bad one is read, the
  lean entry a search answers with, the index cached for ten minutes (a stale
  copy answered when a fetch fails, nothing to answer without one), every
  entry's download address, one driver's full entry, and the exact
  manufacturer and model lookup over ``devices.json`` (fetched on every call,
  rows for the same model added together).
- ``get_installed_drivers``: what the driver folder lists and how.
- ``create_driver_definition`` and ``update_driver_definition``: every
  refusal, where the file is saved, a rename, the driver registered and
  announced.
- ``test_driver_command``: what it sends, answers and closes on each way out.

The network is never touched: ``httpx.AsyncClient.get`` and the TCP transport
are replaced. An invented device throughout.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.tools.catalog_tools import _lean_driver_entry, _score_driver_match
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import (
    is_driver_registered,
    list_registered_drivers,
    register_driver,
    unregister_driver,
)

BASE = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"


class _AcmePython(BaseDriver):
    """A Python-format driver: registered, with no definition file."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_other", "name": "Acme Other", "transport": "tcp",
        "commands": {}, "state_variables": {},
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _handler() -> AIToolHandler:
    return AIToolHandler(MagicMock(), MagicMock(), MagicMock())


class _Web:
    """Stands in for GitHub: a body (or an exception) per path, every GET recorded."""

    def __init__(self, monkeypatch, bodies: dict[str, Any]) -> None:
        self.bodies = bodies
        self.fetched: list[str] = []
        web = self

        async def get(client, url, *args, **kwargs):
            web.fetched.append(str(url))
            body = web.bodies[str(url).rsplit("/", 1)[1]]
            if isinstance(body, BaseException):
                raise body
            request = httpx.Request("GET", url)
            if isinstance(body, tuple):
                status, content = body
                return httpx.Response(status, content=content, request=request)
            return httpx.Response(200, json=body, request=request)

        monkeypatch.setattr(httpx.AsyncClient, "get", get)


def _entry(driver_id: str, **fields: Any) -> dict[str, Any]:
    return {"id": driver_id, "file": f"utility/{driver_id}.avcdriver", **fields}


INDEX = {
    "drivers": [
        _entry(
            "acme_widget", name="Acme Widget Pro", manufacturer="Acme", category="switcher",
            transport="tcp", description="Controls the widget line", tags=["widget", "lan", 5],
            compatible_models=[{"models": ["WX-100", 7]}, {}], help={"overview": "x"},
            source_url="https://example.invalid/acme", files={"a": "b"}, version="1.0.0",
        ),
        _entry("acme_gadget", name="Acme Gadget", manufacturer="Acme", category="display", transport="http"),
        _entry("zeta_widget", name="Zeta", manufacturer="Zeta", category="switcher", transport="TCP"),
        {"id": "nofile_widget", "name": "No File", "manufacturer": "Nofile", "category": "utility"},
    ],
}


# --- scoring and the lean entry -----------------------------------------------------


DRV = INDEX["drivers"][0]


@pytest.mark.parametrize(("query", "score"), [
    ("", 1),
    ("acme", 100 + 30 + 30 + 20),
    ("acme_widget", 100 + 30),
    ("widget", 30 + 20 + 15 + 5 + 3),
    ("wx-100", 25),
    ("lan", 15 + 5),
    ("pro", 20),
    ("line", 3),
    ("zzz", 0),
])
def test_how_a_query_scores_an_entry(query, score):
    assert _score_driver_match(DRV, query) == score


def test_an_entry_with_no_fields_scores_nothing():
    assert _score_driver_match({}, "acme") == 0
    assert _score_driver_match(
        {"id": None, "tags": None, "compatible_models": [{"models": None}]}, "acme",
    ) == 0


def test_the_lean_entry_keeps_what_picks_a_driver():
    lean = _lean_driver_entry({**DRV, "download_url": "u", "ports": [23], "deprecated": True})
    assert lean == {
        "id": "acme_widget", "name": "Acme Widget Pro", "manufacturer": "Acme", "category": "switcher",
        "version": "1.0.0", "transport": "tcp", "description": "Controls the widget line",
        "file": "utility/acme_widget.avcdriver", "ports": [23], "tags": ["widget", "lan", 5],
        "deprecated": True, "download_url": "u",
    }


# --- search_community_drivers ---------------------------------------------------------


async def test_search_with_no_catalog(monkeypatch):
    _Web(monkeypatch, {"index.json": httpx.ConnectError("offline")})
    assert await _handler()._search_community_drivers({"query": "acme"}) == {
        "drivers": [], "total": 0, "error": "Failed to fetch community index",
    }


async def test_search_with_no_query_lists_by_id(monkeypatch):
    _Web(monkeypatch, {"index.json": INDEX})

    answer = await _handler()._search_community_drivers({})

    assert [d["id"] for d in answer["drivers"]] == ["acme_gadget", "acme_widget", "nofile_widget", "zeta_widget"]
    assert (answer["total"], answer["returned"], answer["truncated"], answer["error"]) == (4, 4, False, None)
    assert answer["drivers"][1] == _lean_driver_entry(
        {**DRV, "download_url": f"{BASE}/utility/acme_widget.avcdriver"},
    )


async def test_search_ranks_by_score_then_id_and_drops_misses(monkeypatch):
    _Web(monkeypatch, {"index.json": INDEX})

    answer = await _handler()._search_community_drivers({"query": "  Widget "})

    # acme_widget 73; nofile_widget and zeta_widget 30 each (the id only), so
    # by id; acme_gadget never mentions it.
    assert [d["id"] for d in answer["drivers"]] == ["acme_widget", "nofile_widget", "zeta_widget"]


@pytest.mark.parametrize(("sent", "ids"), [
    ({"category": " SWITCHER "}, ["acme_widget", "zeta_widget"]),
    ({"manufacturer": "acme"}, ["acme_gadget", "acme_widget"]),
    ({"transport": "tcp"}, ["acme_widget", "zeta_widget"]),
    ({"category": "switcher", "manufacturer": "Zeta"}, ["zeta_widget"]),
    ({"category": None, "manufacturer": None, "transport": None, "query": None}, [
        "acme_gadget", "acme_widget", "nofile_widget", "zeta_widget",
    ]),
    ({"transport": "serial"}, []),
    ({"query": "acme", "category": "display"}, ["acme_gadget"]),
])
async def test_search_filters(monkeypatch, sent, ids):
    _Web(monkeypatch, {"index.json": INDEX})
    answer = await _handler()._search_community_drivers(sent)
    assert [d["id"] for d in answer["drivers"]] == ids


@pytest.mark.parametrize(("limit", "returned"), [
    (2, 2), ("3", 3), (0, 1), (-5, 1), (500, 4), ("many", 4), (None, 4), (2.9, 2),
])
async def test_search_limit(monkeypatch, limit, returned):
    _Web(monkeypatch, {"index.json": INDEX})

    answer = await _handler()._search_community_drivers({"limit": limit})

    assert (answer["returned"], answer["total"], answer["truncated"]) == (returned, 4, returned < 4)


async def test_search_limit_defaults_to_twenty_five(monkeypatch):
    many = {"drivers": [_entry(f"acme_{n:02d}") for n in range(30)]}
    _Web(monkeypatch, {"index.json": many})

    answer = await _handler()._search_community_drivers({})

    assert (answer["returned"], answer["total"], answer["truncated"]) == (25, 30, True)

    capped = {"drivers": [_entry(f"acme_{n:03d}") for n in range(130)]}
    handler = _handler()
    _Web(monkeypatch, {"index.json": capped})
    assert (await handler._search_community_drivers({"limit": 1000}))["returned"] == 100


# --- the index and its cache ------------------------------------------------------------


async def test_every_entry_with_a_file_gets_its_download_address(monkeypatch):
    _Web(monkeypatch, {"index.json": INDEX})

    drivers = await _handler()._fetch_community_index()

    assert {d["id"]: d.get("download_url") for d in drivers} == {
        "acme_widget": f"{BASE}/utility/acme_widget.avcdriver",
        "acme_gadget": f"{BASE}/utility/acme_gadget.avcdriver",
        "zeta_widget": f"{BASE}/utility/zeta_widget.avcdriver",
        "nofile_widget": None,
    }


async def test_a_list_shaped_index_is_read_too(monkeypatch):
    web = _Web(monkeypatch, {"index.json": [_entry("acme_widget")]})

    drivers = await _handler()._fetch_community_index()

    assert [d["id"] for d in drivers] == ["acme_widget"]
    assert web.fetched == [f"{BASE}/index.json"]


async def test_the_index_is_kept_for_ten_minutes(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    web = _Web(monkeypatch, {"index.json": INDEX})
    handler = _handler()

    first = await handler._fetch_community_index()
    now[0] += 599
    assert await handler._fetch_community_index() is first
    assert len(web.fetched) == 1

    now[0] += 2
    web.bodies["index.json"] = {"drivers": [_entry("acme_new")]}
    assert [d["id"] for d in await handler._fetch_community_index()] == ["acme_new"]
    assert len(web.fetched) == 2


@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), OSError("no route"), (503, b"down")])
async def test_a_failed_fetch_answers_the_stale_copy(monkeypatch, failure):
    now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    web = _Web(monkeypatch, {"index.json": INDEX})
    handler = _handler()
    first = await handler._fetch_community_index()

    now[0] += 3600
    web.bodies["index.json"] = failure
    assert await handler._fetch_community_index() is first
    # The stale copy keeps its age: the next call tries again.
    await handler._fetch_community_index()
    assert len(web.fetched) == 3


@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), OSError("no route"), (404, b"gone")])
async def test_a_failed_fetch_with_no_copy_answers_none(monkeypatch, failure):
    _Web(monkeypatch, {"index.json": failure})
    assert await _handler()._fetch_community_index() is None


async def test_an_index_that_is_not_json_fails_the_tool(monkeypatch):
    # Not caught today: the tool fails and the dispatcher words it.
    _Web(monkeypatch, {"index.json": (200, b"<html>")})
    with pytest.raises(ValueError):
        await _handler()._search_community_drivers({})


# --- get_community_driver_detail ------------------------------------------------------------


async def test_detail_answers_the_whole_entry(monkeypatch):
    _Web(monkeypatch, {"index.json": INDEX})

    answer = await _handler()._get_community_driver_detail({"driver_id": " acme_widget "})

    assert answer == {**DRV, "download_url": f"{BASE}/utility/acme_widget.avcdriver"}


async def test_detail_of_an_entry_with_no_file(monkeypatch):
    _Web(monkeypatch, {"index.json": INDEX})

    answer = await _handler()._get_community_driver_detail({"driver_id": "nofile_widget"})

    assert answer == INDEX["drivers"][3]


async def test_detail_refusals(monkeypatch):
    web = _Web(monkeypatch, {"index.json": INDEX})
    handler = _handler()
    assert await handler._get_community_driver_detail({}) == {"error": "driver_id is required"}
    assert await handler._get_community_driver_detail({"driver_id": "  "}) == {"error": "driver_id is required"}
    assert web.fetched == []
    assert await handler._get_community_driver_detail({"driver_id": "acme_ghost"}) == {
        "error": "Driver 'acme_ghost' not found in community catalog",
    }
    web.bodies["index.json"] = httpx.ConnectError("offline")
    assert await _handler()._get_community_driver_detail({"driver_id": "acme_widget"}) == {
        "error": "Failed to fetch community index",
    }


# --- find_driver_for_device ------------------------------------------------------------------


DEVICES = {
    "devices": [
        {"manufacturer": "Acme", "model": "WX-100", "drivers": [
            {"id": "acme_widget", "confidence": "verified", "notes": "Firmware 2+"},
        ]},
        {"manufacturer": "ACME", "model": "wx-100", "drivers": [{"id": "acme_generic"}]},
        {"manufacturer": "Acme", "model": "WX-200", "drivers": [{"id": "acme_widget"}]},
        {"manufacturer": "Acme", "model": "WX-300", "drivers": None},
        {"model": "WX-100", "drivers": [{"id": "orphan"}]},
    ],
}


async def test_find_adds_up_every_row_for_the_model(monkeypatch):
    web = _Web(monkeypatch, {"devices.json": DEVICES})

    answer = await _handler()._find_driver_for_device({"manufacturer": " acme ", "model": "Wx-100 "})

    assert answer == {
        "manufacturer": "acme",
        "model": "Wx-100",
        "matches": [
            {"driver_id": "acme_widget", "confidence": "verified", "notes": "Firmware 2+"},
            {"driver_id": "acme_generic", "confidence": "untested", "notes": None},
        ],
        "error": None,
    }
    assert web.fetched == [f"{BASE}/devices.json"]


async def test_find_fetches_the_catalog_every_time(monkeypatch):
    web = _Web(monkeypatch, {"devices.json": DEVICES["devices"]})
    handler = _handler()

    await handler._find_driver_for_device({"manufacturer": "Acme", "model": "WX-300"})
    answer = await handler._find_driver_for_device({"manufacturer": "Acme", "model": "WX-200"})

    assert answer["matches"] == [{"driver_id": "acme_widget", "confidence": "untested", "notes": None}]
    assert len(web.fetched) == 2


async def test_find_with_no_match(monkeypatch):
    _Web(monkeypatch, {"devices.json": DEVICES})
    answer = await _handler()._find_driver_for_device({"manufacturer": "Acme", "model": "WX-300"})
    assert answer == {"manufacturer": "Acme", "model": "WX-300", "matches": [], "error": None}


@pytest.mark.parametrize("sent", [{}, {"manufacturer": "Acme"}, {"model": "WX-100"}, {"manufacturer": " ", "model": "x"}])
async def test_find_needs_both(monkeypatch, sent):
    web = _Web(monkeypatch, {"devices.json": DEVICES})
    assert await _handler()._find_driver_for_device(sent) == {
        "matches": [], "error": "manufacturer and model are required",
    }
    assert web.fetched == []


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("offline"), OSError("no route"), (500, b"oops"), (200, b"<html>"),
])
async def test_find_with_no_catalog(monkeypatch, caplog, failure):
    caplog.set_level(logging.WARNING)
    _Web(monkeypatch, {"devices.json": failure})

    answer = await _handler()._find_driver_for_device({"manufacturer": "Acme", "model": "WX-100"})

    assert answer == {"matches": [], "error": "Failed to fetch the community device catalog"}
    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("Community device catalog")]


# --- get_installed_drivers ---------------------------------------------------------------------


async def test_installed_drivers_list_the_driver_folder(tmp_path):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    (repo / "b_switch.avcdriver").write_text("id: acme_switch\nname: Acme Switch\n", encoding="utf-8")
    (repo / "a_bare.avcdriver").write_text("transport: tcp\n", encoding="utf-8")
    (repo / "c_broken.avcdriver").write_text("id: [unclosed\n", encoding="utf-8")
    (repo / "d_list.avcdriver").write_text("- one\n- two\n", encoding="utf-8")
    (repo / "acme_widget.py").write_text("", encoding="utf-8")
    (repo / "acme_widget_sim.py").write_text("", encoding="utf-8")
    (repo / "_helpers.py").write_text("", encoding="utf-8")
    (repo / "notes.txt").write_text("", encoding="utf-8")
    handler = _handler()
    handler._get_engine = lambda: object()
    handler._get_driver_repo_dir = lambda: repo

    answer = await handler._get_installed_drivers({})

    assert answer == {"drivers": [
        {"id": "a_bare", "name": "a_bare", "format": "avcdriver", "filename": "a_bare.avcdriver"},
        {"id": "acme_switch", "name": "Acme Switch", "format": "avcdriver", "filename": "b_switch.avcdriver"},
        {"id": "c_broken", "name": "c_broken", "format": "avcdriver", "filename": "c_broken.avcdriver"},
        {"id": "acme_widget", "name": "Acme Widget", "format": "python", "filename": "acme_widget.py"},
        {"id": "acme_widget_sim", "name": "Acme Widget Sim", "format": "python", "filename": "acme_widget_sim.py"},
    ]}


async def test_installed_drivers_without_an_engine_or_a_folder(tmp_path):
    handler = _handler()
    handler._get_engine = lambda: None
    assert await handler._get_installed_drivers({}) == {"drivers": []}
    handler._get_engine = lambda: object()
    handler._get_driver_repo_dir = lambda: tmp_path / "absent"
    assert await handler._get_installed_drivers({}) == {"drivers": []}


# --- driver definitions ----------------------------------------------------------------------------


def _definition(driver_id: str = "acme_yaml", name: str = "Acme YAML") -> dict[str, Any]:
    return {"id": driver_id, "name": name, "transport": "tcp"}


@pytest.fixture
def driver_dirs(tmp_path, monkeypatch):
    builtin = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin.mkdir()
    repo.mkdir()
    monkeypatch.setattr("openavc.system_config.DRIVER_DEFINITIONS_DIR", builtin)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)
    announced = AsyncMock()
    monkeypatch.setattr("openavc.api.routes.drivers.announce_drivers_changed", announced)
    yield builtin, repo, announced
    for driver_id in ("acme_yaml", "acme_renamed", "acme_other"):
        unregister_driver(driver_id)


async def test_create_definition_saves_registers_and_announces(driver_dirs):
    _builtin, repo, announced = driver_dirs

    answer = await _handler()._create_driver_definition({"definition": _definition()})

    assert answer == {"status": "created", "id": "acme_yaml"}
    assert (repo / "acme_yaml.avcdriver").exists()
    assert is_driver_registered("acme_yaml")
    announced.assert_awaited_once_with("acme_yaml")


async def test_create_definition_with_one_folder_saves_there(driver_dirs, tmp_path):
    _builtin, _repo, _announced = driver_dirs
    only = tmp_path / "only"
    only.mkdir()
    handler = _handler()
    handler._get_driver_dirs = lambda: [only]

    await handler._create_driver_definition({"definition": _definition()})

    assert (only / "acme_yaml.avcdriver").exists()


async def test_create_definition_refusals(driver_dirs, monkeypatch):
    _builtin, repo, announced = driver_dirs
    handler = _handler()
    assert await handler._create_driver_definition({}) == {"error": "Driver definition must have an 'id' field"}
    assert await handler._create_driver_definition({"definition": {"name": "x"}}) == {
        "error": "Driver definition must have an 'id' field",
    }
    assert await handler._create_driver_definition({"definition": {**_definition(), "bogus": 1, "other": 2}}) == {
        "error": "unknown key 'bogus'; unknown key 'other'",
    }
    (repo / "acme_yaml.avcdriver").write_text("id: acme_yaml\nname: Old\ntransport: tcp\n", encoding="utf-8")
    assert await handler._create_driver_definition({"definition": _definition()}) == {
        "error": "Driver definition 'acme_yaml' already exists",
    }
    register_driver(_AcmePython)
    assert await handler._create_driver_definition({"definition": _definition("acme_other")}) == {
        "error": (
            "Driver 'acme_other' is already installed as a Python-format driver. A YAML "
            "definition sharing its id would shadow it. Pick a different id, or use the "
            "driver that is already there."
        ),
    }
    announced.assert_not_awaited()


async def test_update_definition_rewrites_in_place(driver_dirs, monkeypatch):
    _builtin, repo, announced = driver_dirs
    (repo / "acme_yaml.avcdriver").write_text("id: acme_yaml\nname: Old\ntransport: tcp\n", encoding="utf-8")
    restored = MagicMock()
    monkeypatch.setattr("openavc.drivers.driver_loader.restore_driver_registration", restored)

    answer = await _handler()._update_driver_definition(
        {"driver_id": "acme_yaml", "definition": _definition(name="Acme New")},
    )

    assert answer == {"status": "updated", "id": "acme_yaml"}
    assert "Acme New" in (repo / "acme_yaml.avcdriver").read_text(encoding="utf-8")
    assert is_driver_registered("acme_yaml")
    restored.assert_not_called()
    announced.assert_awaited_once_with("acme_yaml")


async def test_update_definition_renamed_drops_the_old_file(driver_dirs, monkeypatch):
    _builtin, repo, announced = driver_dirs
    (repo / "acme_yaml.avcdriver").write_text("id: acme_yaml\nname: Old\ntransport: tcp\n", encoding="utf-8")
    restored = MagicMock()
    monkeypatch.setattr("openavc.drivers.driver_loader.restore_driver_registration", restored)
    handler = _handler()

    answer = await handler._update_driver_definition(
        {"driver_id": "acme_yaml", "definition": _definition("acme_renamed")},
    )

    assert answer == {"status": "updated", "id": "acme_renamed"}
    assert not (repo / "acme_yaml.avcdriver").exists()
    assert (repo / "acme_renamed.avcdriver").exists()
    assert is_driver_registered("acme_renamed")
    restored.assert_called_once_with("acme_yaml", handler._get_driver_dirs())
    announced.assert_awaited_once_with("acme_renamed")


async def test_update_definition_refusals(driver_dirs):
    _builtin, repo, announced = driver_dirs
    (repo / "acme_yaml.avcdriver").write_text("id: acme_yaml\nname: Old\ntransport: tcp\n", encoding="utf-8")
    (repo / "acme_other.avcdriver").write_text("id: acme_other\nname: Other\ntransport: tcp\n", encoding="utf-8")
    handler = _handler()

    assert await handler._update_driver_definition({"driver_id": "acme_yaml"}) == {
        "error": "Driver definition must have an 'id' field",
    }
    assert await handler._update_driver_definition({"driver_id": "acme_ghost", "definition": _definition()}) == {
        "error": "Driver definition 'acme_ghost' not found",
    }
    assert await handler._update_driver_definition(
        {"driver_id": "acme_yaml", "definition": {**_definition(), "bogus": 1}},
    ) == {"error": "unknown key 'bogus'"}
    assert await handler._update_driver_definition(
        {"driver_id": "acme_yaml", "definition": _definition("acme_other")},
    ) == {"error": "A driver definition with id 'acme_other' already exists"}
    assert "Old" in (repo / "acme_yaml.avcdriver").read_text(encoding="utf-8")
    announced.assert_not_awaited()


# --- test_driver_command ------------------------------------------------------------------------


class _Transport:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.sent: list[tuple[bytes, Any]] = []
        self.closed = 0

    async def send_and_wait(self, data: bytes, timeout: Any = None) -> bytes:
        self.sent.append((data, timeout))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture
def tcp(monkeypatch):
    made: list[dict[str, Any]] = []
    holder: dict[str, Any] = {"transport": _Transport(b"PWR=1\xff")}

    async def create(**kwargs: Any):
        made.append(kwargs)
        if isinstance(holder["transport"], BaseException):
            raise holder["transport"]
        return holder["transport"]

    monkeypatch.setattr("openavc.transport.tcp.TCPTransport.create", create)
    return made, holder


async def test_raw_command_sends_the_decoded_text(tcp):
    made, holder = tcp

    answer = await _handler()._test_driver_command({"host": "192.0.2.50", "command_string": "PWR?\\r"})

    assert answer == {"success": True, "error": None, "response": "PWR=1�"}
    assert [(k["host"], k["port"], k["delimiter"], k["timeout"]) for k in made] == [
        ("192.0.2.50", 23, b"\r\n", 5),
    ]
    assert holder["transport"].sent == [(b"PWR?\r", 5)]
    assert holder["transport"].closed == 1


async def test_raw_command_takes_its_port_delimiter_and_timeout(tcp):
    made, holder = tcp

    await _handler()._test_driver_command({
        "host": "192.0.2.50", "port": 4000, "command_string": "X", "delimiter": "\\x03", "timeout": 2,
    })

    assert [(k["port"], k["delimiter"], k["timeout"]) for k in made] == [(4000, b"\x03", 2)]
    assert holder["transport"].sent == [(b"X", 2)]


@pytest.mark.parametrize(("reply", "error"), [
    (TimeoutError(), "Timeout waiting for response"),
    (ConnectionError("reset by peer"), "reset by peer"),
    (OSError("broken pipe"), "broken pipe"),
])
async def test_raw_command_failures_still_close(tcp, reply, error):
    _made, holder = tcp
    holder["transport"] = _Transport(reply)

    answer = await _handler()._test_driver_command({"host": "192.0.2.50", "command_string": "X"})

    assert answer == {"success": False, "error": error, "response": None}
    assert holder["transport"].closed == 1


async def test_raw_command_that_cannot_connect(tcp):
    _made, holder = tcp
    holder["transport"] = ConnectionError("Connection refused")

    answer = await _handler()._test_driver_command({"host": "192.0.2.50", "command_string": "X"})

    assert answer == {"success": False, "error": "Connection refused", "response": None}


@pytest.mark.parametrize("sent", [{"host": "192.0.2.50"}, {"command_string": "X"}, {"host": "", "command_string": "X"}])
async def test_raw_command_needs_a_host_and_text(tcp, sent):
    made, _holder = tcp
    assert await _handler()._test_driver_command(sent) == {
        "success": False, "error": "host and command_string are required", "response": None,
    }
    assert made == []


class _Devices:
    """A device manager whose ``network_connections`` (the ports held on the
    network, which the raw command asks) is ``connections``."""

    def __init__(self, connections: Any) -> None:
        self.network_connections = connections


class _Engine:
    def __init__(self, devices: Any) -> None:
        self.devices = devices


@pytest.mark.parametrize("devices", [object(), _Devices(None)])
async def test_raw_command_with_no_connected_list_dials(tcp, devices):
    made, _holder = tcp
    handler = _handler()
    handler._get_engine = lambda: _Engine(devices)

    answer = await handler._test_driver_command({"host": "192.0.2.50", "command_string": "X"})

    assert answer["success"] is True
    assert len(made) == 1


async def test_raw_command_dials_when_the_connected_list_cannot_be_read(tcp, caplog):
    made, _holder = tcp

    def broken():
        raise RuntimeError("manager gone")

    handler = _handler()
    handler._get_engine = lambda: _Engine(_Devices(broken))

    answer = await handler._test_driver_command({"host": "192.0.2.50", "command_string": "X"})

    assert answer["success"] is True
    assert len(made) == 1
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == [
        "Could not read the project's connected devices",
    ]


async def test_raw_command_with_a_port_that_is_not_a_number_skips_the_check(tcp):
    made, _holder = tcp
    asked: list[Any] = []

    def live():
        asked.append(True)
        return []

    handler = _handler()
    handler._get_engine = lambda: _Engine(_Devices(live))

    await handler._test_driver_command({"host": "192.0.2.50", "port": "telnet", "command_string": "X"})

    assert asked == []
    assert [k["port"] for k in made] == ["telnet"]


async def test_raw_command_with_nothing_connected_dials(tcp):
    made, _holder = tcp
    handler = _handler()
    handler._get_engine = lambda: _Engine(_Devices(lambda: []))

    answer = await handler._test_driver_command({"host": "192.0.2.50", "port": "4000", "command_string": "X"})

    assert answer["success"] is True
    assert [k["port"] for k in made] == ["4000"]


# --- the simple reads -----------------------------------------------------------------------------


async def test_list_drivers_answers_the_registry():
    register_driver(_AcmePython)
    try:
        answer = await _handler()._list_drivers({})
        assert answer == list_registered_drivers()
        assert "acme_other" in [d["id"] for d in answer]
    finally:
        unregister_driver("acme_other")


def test_tags_and_models_that_are_not_text_never_match():
    assert _score_driver_match(DRV, "5") == 0
    assert _score_driver_match(DRV, "7") == 0


async def test_search_filters_compare_whole_values_in_any_case(monkeypatch):
    _Web(monkeypatch, {"index.json": {"drivers": [
        _entry("acme_one", category="Switcher", manufacturer="ACME", transport="Tcp"),
        _entry("acme_two", category="switcher-pro", manufacturer="AcmePro", transport="tcp+tls"),
        _entry("acme_three"),
    ]}})
    handler = _handler()

    for sent in ({"category": "switcher"}, {"manufacturer": "acme"}, {"transport": "TCP"}):
        answer = await handler._search_community_drivers(sent)
        assert [d["id"] for d in answer["drivers"]] == ["acme_one"], sent


@pytest.mark.parametrize("limit", ["many", None, [3], {}])
async def test_search_reads_a_limit_it_cannot_parse_as_twenty_five(monkeypatch, limit):
    _Web(monkeypatch, {"index.json": {"drivers": [_entry(f"acme_{n:02d}") for n in range(30)]}})

    answer = await _handler()._search_community_drivers({"limit": limit})

    assert (answer["returned"], answer["truncated"]) == (25, True)


async def test_update_definition_of_a_built_in_is_refused_before_it_is_validated(driver_dirs):
    builtin, _repo, announced = driver_dirs
    (builtin / "acme_yaml.avcdriver").write_text("id: acme_yaml\nname: Ship\ntransport: tcp\n", encoding="utf-8")

    answer = await _handler()._update_driver_definition(
        {"driver_id": "acme_yaml", "definition": {**_definition(), "bogus": 1}},
    )

    assert answer == {
        "error": "Driver 'acme_yaml' is a built-in and cannot be modified. Duplicate it under a new id first.",
    }
    announced.assert_not_awaited()
