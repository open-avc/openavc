"""Pins for the doors that put a driver file on this server: the community
install and update, the single-file and bundle uploads, and the uninstall.

Each door is pinned for what it leaves behind (the files in the driver folder),
what it answers (status and detail), and the order of what it does around the
file (registry writes, catalog downloads, the discovery refresh, the orphan
retry, the device rebuild, the announce). The doors look alike and differ in
small ways that are deliberate or at least long-standing; these tests record
each as it is today, so a change that makes two doors share code cannot quietly
make one of them behave like the other.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import yaml
from fastapi import HTTPException

from openavc.api.models import CommunityDriverInstallRequest
from openavc.api.routes import drivers as routes
from openavc.api.routes.drivers import (
    _companion_relpath_from_yaml,
    _remove_other_copies,
    install_community_driver,
    uninstall_driver,
    update_driver,
    upload_driver,
    upload_driver_bundle,
)
from openavc.utils.community_integrity import ArtifactHashes

CATALOG = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/"
DRIVER_ID = "acme_widget"


# --- the world around a door ----------------------------------------------


class _Response:
    def __init__(self, url: str, status: int, body: bytes):
        self.url = url
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "refused",
                request=httpx.Request("GET", self.url),
                response=httpx.Response(self.status_code),
            )


class _Catalog:
    """The community catalog as the doors fetch it: each URL answers bytes, a
    status code, or raises. An unlisted URL is a 404. Every GET is an event."""

    def __init__(self, events: list):
        self.pages: dict[str, object] = {}
        self._events = events

    def __call__(self, **_kwargs):  # httpx.AsyncClient(**kwargs)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str):
        self._events.append(("get", url.rsplit("/", 1)[-1]))
        page = self.pages.get(url, 404)
        if isinstance(page, BaseException):
            raise page
        if isinstance(page, int):
            return _Response(url, page, b"")
        return _Response(url, 200, page)


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    events: list = []
    catalog = _Catalog(events)
    monkeypatch.setattr("httpx.AsyncClient", catalog)
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: repo)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)

    def _register(cls):
        events.append(("register", cls.DRIVER_INFO.get("id")))

    monkeypatch.setattr(routes, "register_driver", _register)
    monkeypatch.setattr(
        routes, "unregister_driver", lambda driver_id: events.append(("unregister", driver_id))
    )

    async def _no_hashes(driver_id, force=False):
        return ArtifactHashes(f"Driver '{driver_id}'", None, source="catalog")

    monkeypatch.setattr(routes, "_catalog_hashes", _no_hashes)

    async def _refresh():
        events.append(("refresh",))

    monkeypatch.setattr("openavc.api.discovery.refresh_all_device_matches", _refresh)

    engine = MagicMock()
    engine.project = None
    state = SimpleNamespace(
        repo=repo, events=events, catalog=catalog, engine=engine,
        using={}, reconnects={}, retry_fails=False, reload_fails=False,
    )

    async def _retry():
        events.append(("retry",))
        if state.retry_fails:
            raise RuntimeError("retry failed")
        return ["waiting_device"]

    async def _reload(driver_id):
        events.append(("reload", driver_id))
        if state.reload_fails:
            raise RuntimeError("reload failed")
        return state.reconnects.get(driver_id, [])

    def _using(driver_id):
        events.append(("using", driver_id))
        return state.using.get(driver_id, [])

    engine.devices.retry_all_orphans = _retry
    engine.devices.reload_driver = _reload
    engine.devices.get_devices_using_driver = _using
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)

    async def _announce(driver_id=None):
        events.append(("announce", driver_id))

    monkeypatch.setattr(routes, "announce_drivers_changed", _announce)
    yield state
    for name in [n for n in sys.modules if n.startswith("openavc_driver_")]:
        if "acme" in name or "helper_tool" in name:
            sys.modules.pop(name, None)


def _tree(repo: Path) -> dict[str, bytes]:
    """The files in the driver folder (an import's ``__pycache__`` aside)."""
    return {p.name: p.read_bytes() for p in sorted(repo.iterdir()) if p.is_file()}


def _yaml(driver_id: str = DRIVER_ID, *, companion: str | None = None, **extra) -> bytes:
    body: dict = {
        "id": driver_id,
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "commands": {"power_on": {"label": "On", "send": "P\r", "params": {}}},
    }
    if companion is not None:
        body["discovery"] = {"python": {"file": companion}}
    body.update(extra)
    return yaml.safe_dump(body, sort_keys=False).encode()


def _python(driver_id: str = DRIVER_ID, mark: str = "new") -> bytes:
    return (
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeWidget(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        f"        'id': {driver_id!r},\n"
        "        'name': 'Acme Widget',\n"
        "        'manufacturer': 'Acme',\n"
        "        'category': 'utility',\n"
        "        'version': '1.0.0',\n"
        "        'transport': 'tcp',\n"
        "    }\n"
        f"    MARK = {mark!r}\n"
    ).encode()


COMPANION = b"async def probe(ctx):\n    return None\n"


class _Upload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _form(filename: str | None, data: bytes = b"") -> MagicMock:
    request = MagicMock()
    form = {} if filename is None else {"file": _Upload(filename, data)}

    async def _read_form():
        return form

    request.form = _read_form
    return request


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


def _json_request(body: dict) -> MagicMock:
    request = MagicMock()

    async def _json():
        return body

    request.json = _json
    return request


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await call
    return caught.value


def _install(file_name: str, *, min_platform_version: str | None = None):
    return install_community_driver(
        CommunityDriverInstallRequest(
            driver_id=DRIVER_ID,
            file_url=CATALOG + file_name,
            min_platform_version=min_platform_version,
        )
    )


def _update(file_name: str | None, **body):
    if file_name is not None:
        body["file_url"] = CATALOG + file_name
    return update_driver(DRIVER_ID, _json_request(body))


# --- the companion a YAML driver names -------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("discovery: [1, 2]", None),
        ("discovery:\n  python: {file: ''}", None),
        ("discovery:\n  python: {file: 7}", None),
        ("discovery:\n  python: {other: x}", None),
        ("discovery:\n  python: ''", None),
        ("discovery:\n  python: ./a_discovery.py", "./a_discovery.py"),
        ("discovery:\n  python: {file: sub/a_discovery.py}", "sub/a_discovery.py"),
        ("discovery: null", None),
        ("- a list", None),
        ("key: [unclosed", None),
        (b"discovery:\n  python: ./a_discovery.py", "./a_discovery.py"),
    ],
)
def test_the_companion_a_yaml_driver_names(text, expected):
    assert _companion_relpath_from_yaml(text) == expected


# --- install -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_refuses_a_url_of_neither_kind_before_downloading(world):
    refusal = await _refused(_install("acme_widget.json"))
    assert (refusal.status_code, refusal.detail) == (
        422, "URL must point to a .avcdriver or .py file",
    )
    assert world.events == []
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_install_answers_a_refused_download_with_github_s_status(world):
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = 410
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (502, "GitHub returned 410")
    assert world.events == [("get", "acme_widget.avcdriver")]
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_install_answers_a_failed_download_by_naming_the_driver(world):
    world.catalog.pages[CATALOG + "acme_widget.py"] = httpx.ConnectError("no route")
    refusal = await _refused(_install("acme_widget.py"))
    assert (refusal.status_code, refusal.detail) == (
        502, "Failed to download driver 'acme_widget'",
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_install_writes_then_removes_a_yaml_above_this_platform_even_over_a_copy(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(b"the copy already here")
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        min_platform_version="999.0.0"
    )
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert refusal.status_code == 422
    assert refusal.detail.startswith("This driver requires OpenAVC 999.0.0 or later.")
    assert _tree(world.repo) == {}
    assert world.events == [("get", "acme_widget.avcdriver")]


@pytest.mark.asyncio
async def test_install_refuses_a_requested_floor_before_anything(world):
    refusal = await _refused(_install("acme_widget.avcdriver", min_platform_version="999.0.0"))
    assert refusal.status_code == 422
    assert world.events == []


@pytest.mark.asyncio
async def test_install_of_a_yaml_driver_and_its_companion_in_order(world):
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION
    result = await _install("acme_widget.avcdriver")
    assert result == {
        "status": "installed",
        "driver_id": DRIVER_ID,
        "file": "acme_widget.avcdriver",
        "activated_devices": ["waiting_device"],
    }
    assert world.events == [
        ("get", "acme_widget.avcdriver"),
        ("get", "acme_widget_discovery.py"),
        ("register", DRIVER_ID),
        ("refresh",),
        ("retry",),
        ("announce", DRIVER_ID),
    ]
    assert set(_tree(world.repo)) == {"acme_widget.avcdriver", "acme_widget_discovery.py"}


@pytest.mark.asyncio
async def test_install_of_a_yaml_the_loader_refuses_removes_it_and_its_companion(world):
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py", transport="carrier_pigeon"
    )
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (422, "Invalid driver definition file")
    assert _tree(world.repo) == {}
    assert ("register", DRIVER_ID) not in world.events


@pytest.mark.asyncio
async def test_install_of_a_yaml_whose_companion_fails_removes_the_yaml(world):
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = 500
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert refusal.status_code == 502
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_install_of_a_yaml_the_loader_refuses_without_a_companion(world):
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(transport="carrier_pigeon")
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (422, "Invalid driver definition file")
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_install_whose_yaml_companion_download_fails_on_the_network(world):
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = httpx.ConnectError("down")
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (
        502,
        "Driver 'acme_widget' references companion 'acme_widget_discovery.py' "
        "but the download failed",
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_install_of_a_python_driver_whose_registration_fails(world, monkeypatch):
    def _explode(cls):
        raise RuntimeError("registry full")

    monkeypatch.setattr(routes, "register_driver", _explode)
    world.catalog.pages[CATALOG + "acme_widget.py"] = _python()
    refusal = await _refused(_install("acme_widget.py"))
    assert (refusal.status_code, refusal.detail) == (500, "Failed to load driver 'acme_widget'")
    assert _tree(world.repo) == {}
    assert world.events == [("get", "acme_widget.py")]


@pytest.mark.asyncio
async def test_install_of_a_python_file_without_a_driver_class(world):
    world.catalog.pages[CATALOG + "acme_widget.py"] = b"VALUE = 1\n"
    refusal = await _refused(_install("acme_widget.py"))
    assert (refusal.status_code, refusal.detail) == (
        422, "No valid driver class found in Python file",
    )
    assert _tree(world.repo) == {}
    assert world.events == [("get", "acme_widget.py")]


@pytest.mark.asyncio
async def test_install_whose_registration_fails_removes_both_files_with_a_500(world, monkeypatch):
    def _explode(cls):
        raise RuntimeError("registry full")

    monkeypatch.setattr(routes, "register_driver", _explode)
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (500, "Failed to load driver 'acme_widget'")
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_install_of_a_python_driver_fetches_its_companions_after_registering(world):
    world.catalog.pages[CATALOG + "acme_widget.py"] = _python()
    world.catalog.pages[CATALOG + "acme_widget_sim.py"] = b"SIM = 1\n"
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = httpx.ConnectError("down")
    result = await _install("acme_widget.py")
    assert result["file"] == "acme_widget.py"
    assert world.events == [
        ("get", "acme_widget.py"),
        ("register", DRIVER_ID),
        ("get", "acme_widget_discovery.py"),
        ("get", "acme_widget_sim.py"),
        ("refresh",),
        ("retry",),
        ("announce", DRIVER_ID),
    ]
    assert set(_tree(world.repo)) == {"acme_widget.py", "acme_widget_sim.py"}


@pytest.mark.asyncio
async def test_install_under_an_empty_id_is_refused_for_the_id_it_declares(world):
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml()
    refusal = await _refused(install_community_driver(CommunityDriverInstallRequest(
        driver_id="", file_url=CATALOG + "acme_widget.avcdriver",
    )))
    assert (refusal.status_code, refusal.detail) == (
        422,
        "Downloaded driver declares id 'acme_widget', not the requested ''. "
        "Install it under id 'acme_widget'.",
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_install_whose_orphan_retry_fails_still_installs(world):
    world.retry_fails = True
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml()
    result = await _install("acme_widget.avcdriver")
    assert result["activated_devices"] == []
    assert world.events[-1] == ("announce", DRIVER_ID)


# --- update ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_without_a_driver_folder(world, monkeypatch):
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: world.repo / "missing")
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (404, "Driver not found")


@pytest.mark.asyncio
async def test_update_without_a_url(world):
    refusal = await _refused(_update(None))
    assert (refusal.status_code, refusal.detail) == (422, "file_url is required")


@pytest.mark.asyncio
async def test_update_refuses_a_requested_floor_before_looking(world):
    refusal = await _refused(_update("acme_widget.avcdriver", min_platform_version="999.0.0"))
    assert refusal.status_code == 422
    assert refusal.detail.startswith("This driver requires OpenAVC 999.0.0")


@pytest.mark.asyncio
async def test_update_finds_nothing_in_an_empty_folder(world):
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (
        404, "Driver 'acme_widget' not found in driver_repo",
    )


@pytest.mark.asyncio
async def test_update_passes_over_a_helper_and_an_unreadable_yaml(world):
    (world.repo / "_acme_widget.avcdriver").write_bytes(_yaml())
    (world.repo / "broken.avcdriver").write_bytes(b"key: [unclosed")
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert refusal.status_code == 404


@pytest.mark.asyncio
async def test_update_passes_over_a_yaml_of_another_id(world):
    (world.repo / "other.avcdriver").write_bytes(_yaml("someone_else"))
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert refusal.status_code == 404
    assert _tree(world.repo) == {"other.avcdriver": _yaml("someone_else")}


@pytest.mark.asyncio
async def test_update_whose_new_companion_fails_with_no_old_companion(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py", version="2.0.0"
    )
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = 404
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (
        502,
        "Driver 'acme_widget' references companion 'acme_widget_discovery.py', "
        "but GitHub returned 404 for it. Install aborted.",
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_update_finds_a_python_file_by_its_name_only(world):
    (world.repo / "renamed.py").write_bytes(_python())
    refusal = await _refused(_update("acme_widget.py"))
    assert refusal.status_code == 404


@pytest.mark.asyncio
async def test_update_finds_a_yaml_by_the_id_it_declares_and_removes_the_old_name(world):
    (world.repo / "renamed.avcdriver").write_bytes(_yaml())
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(version="2.0.0")
    world.reconnects[DRIVER_ID] = ["d1"]
    world.using[DRIVER_ID] = ["d1", "d2"]
    result = await _update("acme_widget.avcdriver")
    assert result == {
        "status": "updated",
        "driver_id": DRIVER_ID,
        "file": "acme_widget.avcdriver",
        "devices_reconnected": ["d1"],
        "devices_not_reconnected": ["d2"],
    }
    assert world.events == [
        ("get", "acme_widget.avcdriver"),
        ("unregister", DRIVER_ID),
        ("register", DRIVER_ID),
        ("using", DRIVER_ID),
        ("reload", DRIVER_ID),
        ("refresh",),
        ("announce", DRIVER_ID),
    ]
    assert _tree(world.repo) == {"acme_widget.avcdriver": _yaml(version="2.0.0")}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("driver_id", "file_name"),
    [("acme-widget", "acme-widget.avcdriver"), ("acme.widget", "acme_widget.avcdriver")],
)
async def test_update_names_the_file_for_the_id_in_safe_characters(world, driver_id, file_name):
    (world.repo / f"{driver_id}.avcdriver").write_bytes(_yaml())
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(version="2.0.0")
    result = await update_driver(
        driver_id, _json_request({"file_url": CATALOG + "acme_widget.avcdriver"})
    )
    assert result["file"] == file_name
    assert set(_tree(world.repo)) == {file_name}


@pytest.mark.asyncio
async def test_update_refuses_a_url_of_neither_kind_after_finding_the_driver(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    refusal = await _refused(_update("acme_widget.json"))
    assert (refusal.status_code, refusal.detail) == (
        422, "URL must point to a .avcdriver or .py file",
    )
    assert world.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "detail"),
    [
        (404, "GitHub returned 404"),
        (httpx.ConnectError("no route"), "Failed to download driver 'acme_widget'"),
    ],
)
async def test_update_whose_download_fails_leaves_the_old_driver(world, page, detail):
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = page
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (502, detail)
    assert _tree(world.repo) == {"acme_widget.avcdriver": _yaml()}
    assert world.events == [("get", "acme_widget.avcdriver")]


@pytest.mark.asyncio
async def test_update_to_a_yaml_above_this_platform_leaves_the_old_driver(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        min_platform_version="999.0.0"
    )
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert refusal.status_code == 422
    assert _tree(world.repo) == {"acme_widget.avcdriver": _yaml()}
    assert world.events == [("get", "acme_widget.avcdriver")]


@pytest.mark.asyncio
async def test_update_whose_new_companion_fails_leaves_neither_driver_nor_companion(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(
        _yaml(companion="./acme_widget_discovery.py")
    )
    (world.repo / "acme_widget_discovery.py").write_bytes(b"OLD = 1\n")
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py", version="2.0.0"
    )
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = 500
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert refusal.status_code == 502
    assert _tree(world.repo) == {}
    assert world.events == [
        ("get", "acme_widget.avcdriver"),
        ("unregister", DRIVER_ID),
        ("get", "acme_widget_discovery.py"),
    ]


@pytest.mark.asyncio
async def test_update_keeps_an_old_companion_the_new_yaml_still_names(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(
        _yaml(companion="./acme_widget_discovery.py")
    )
    (world.repo / "acme_widget_discovery.py").write_bytes(b"OLD = 1\n")
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py", version="2.0.0"
    )
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION
    await _update("acme_widget.avcdriver")
    assert _tree(world.repo)["acme_widget_discovery.py"] == COMPANION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "named",
    ["..", "./acme_widget_probe.py", "./not_here_discovery.py"],
)
async def test_update_removes_only_an_old_companion_that_is_a_discovery_file_here(world, named):
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml(companion=named))
    (world.repo / "acme_widget_probe.py").write_bytes(b"MINE = 1\n")
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(version="2.0.0")
    await _update("acme_widget.avcdriver")
    assert set(_tree(world.repo)) == {"acme_widget.avcdriver", "acme_widget_probe.py"}


@pytest.mark.asyncio
async def test_update_reads_an_unreadable_old_yaml_as_naming_no_companion(world, monkeypatch):
    old = world.repo / "acme_widget.avcdriver"
    old.write_bytes(_yaml(companion="./acme_widget_discovery.py"))
    (world.repo / "acme_widget_discovery.py").write_bytes(b"OLD = 1\n")
    real_read_text = Path.read_text

    def _read_text(self, *args, **kwargs):
        if self == old and old.read_bytes() != _yaml(version="2.0.0"):
            raise PermissionError("locked")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(version="2.0.0")
    await _update("acme_widget.avcdriver")
    assert set(_tree(world.repo)) == {"acme_widget.avcdriver", "acme_widget_discovery.py"}


@pytest.mark.asyncio
async def test_update_to_a_yaml_the_loader_refuses_leaves_it_written_and_unregistered(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    bad = _yaml(transport="carrier_pigeon")
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = bad
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (422, "Invalid driver definition file")
    assert _tree(world.repo) == {"acme_widget.avcdriver": bad}
    assert world.events == [("get", "acme_widget.avcdriver"), ("unregister", DRIVER_ID)]


@pytest.mark.asyncio
async def test_update_to_a_python_file_without_a_class(world):
    (world.repo / "acme_widget.py").write_bytes(_python(mark="old"))
    world.catalog.pages[CATALOG + "acme_widget.py"] = b"VALUE = 2\n"
    refusal = await _refused(_update("acme_widget.py"))
    assert (refusal.status_code, refusal.detail) == (
        422, "No valid driver class found in Python file",
    )
    assert _tree(world.repo) == {"acme_widget.py": b"VALUE = 2\n"}
    assert world.events == [
        ("get", "acme_widget.py"),
        ("unregister", DRIVER_ID),
        ("get", "acme_widget_discovery.py"),
        ("get", "acme_widget_sim.py"),
    ]


@pytest.mark.asyncio
async def test_update_whose_registration_fails_answers_500(world, monkeypatch):
    def _explode(cls):
        raise RuntimeError("registry full")

    monkeypatch.setattr(routes, "register_driver", _explode)
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(version="2.0.0")
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (
        500, "Failed to load updated driver 'acme_widget'",
    )


@pytest.mark.asyncio
async def test_update_of_a_python_driver_refreshes_its_companions_before_registering(world):
    (world.repo / "acme_widget.py").write_bytes(_python(mark="old"))
    (world.repo / "acme_widget_sim.py").write_bytes(b"OLD_SIM = 1\n")
    (world.repo / "acme_widget_discovery.py").write_bytes(b"OLD_DISCOVERY = 1\n")
    world.catalog.pages[CATALOG + "acme_widget.py"] = _python(mark="new")
    world.catalog.pages[CATALOG + "acme_widget_discovery.py"] = httpx.ConnectError("down")
    world.catalog.pages[CATALOG + "acme_widget_sim.py"] = 404
    await _update("acme_widget.py")
    assert _tree(world.repo) == {
        "acme_widget.py": _python(mark="new"),
        "acme_widget_discovery.py": b"OLD_DISCOVERY = 1\n",
    }
    assert world.events[:5] == [
        ("get", "acme_widget.py"),
        ("unregister", DRIVER_ID),
        ("get", "acme_widget_discovery.py"),
        ("get", "acme_widget_sim.py"),
        ("register", DRIVER_ID),
    ]


@pytest.mark.asyncio
async def test_update_says_it_removed_only_an_unpublished_companion_it_had(world, caplog):
    (world.repo / "acme_widget.py").write_bytes(_python(mark="old"))
    (world.repo / "acme_widget_sim.py").write_bytes(b"OLD_SIM = 1\n")
    world.catalog.pages[CATALOG + "acme_widget.py"] = _python(mark="new")
    with caplog.at_level("INFO", logger="openavc.api.routes.drivers"):
        await _update("acme_widget.py")
    removed = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Removed ")]
    assert removed == ["Removed acme_widget_sim.py — no longer published for driver 'acme_widget'"]


@pytest.mark.asyncio
async def test_update_whose_device_rebuild_fails_still_answers_updated(world):
    world.reload_fails = True
    world.using[DRIVER_ID] = ["d1"]
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(version="2.0.0")
    result = await _update("acme_widget.avcdriver")
    assert result["devices_reconnected"] == []
    assert result["devices_not_reconnected"] == ["d1"]
    assert world.events[-2:] == [("refresh",), ("announce", DRIVER_ID)]


# --- single-file upload ------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_without_a_file(world):
    refusal = await _refused(upload_driver(_form(None)))
    assert (refusal.status_code, refusal.detail) == (
        422, "No file provided. Use 'file' field in multipart form.",
    )


@pytest.mark.asyncio
async def test_upload_of_another_kind_of_file(world):
    refusal = await _refused(upload_driver(_form("acme_widget.json", b"{}")))
    assert (refusal.status_code, refusal.detail) == (422, "File must be .avcdriver or .py")


@pytest.mark.asyncio
async def test_upload_of_a_yaml_with_no_id_keeps_its_safe_name_then_is_refused(world):
    refusal = await _refused(upload_driver(_form("my_driver.avcdriver", b"name: Nameless\n")))
    assert (refusal.status_code, refusal.detail) == (422, "Invalid driver definition file")
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_upload_of_text_that_is_not_yaml(world):
    refusal = await _refused(upload_driver(_form("my_driver.avcdriver", b"key: [unclosed")))
    assert (refusal.status_code, refusal.detail) == (422, "Invalid driver definition file")
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_upload_of_a_yaml_whose_companion_is_not_here_yet(world):
    data = _yaml(companion="./sub/acme_widget_discovery.py")
    refusal = await _refused(upload_driver(_form("acme_widget.avcdriver", data)))
    assert refusal.status_code == 422
    assert refusal.detail == (
        "Driver acme_widget.avcdriver declares a Python companion "
        "('./sub/acme_widget_discovery.py') but 'acme_widget_discovery.py' is not "
        "in the driver library yet. Upload the _discovery.py file first, then "
        "re-upload this driver."
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_upload_of_a_yaml_whose_companion_is_here(world):
    (world.repo / "acme_widget_discovery.py").write_bytes(COMPANION)
    data = _yaml(companion="./acme_widget_discovery.py")
    result = await upload_driver(_form("acme_widget (2).avcdriver", data))
    assert result == {
        "status": "uploaded",
        "driver_id": DRIVER_ID,
        "file": "acme_widget.avcdriver",
        "activated_devices": ["waiting_device"],
    }
    assert world.events == [("register", DRIVER_ID), ("retry",), ("announce", DRIVER_ID)]


@pytest.mark.asyncio
async def test_upload_of_a_yaml_with_a_misspelled_key_is_removed(world):
    refusal = await _refused(
        upload_driver(_form("acme_widget.avcdriver", _yaml(comands={"x": {}})))
    )
    assert refusal.status_code == 422
    assert refusal.detail.startswith(
        "The driver was not imported. Check the spelling of these keys against the driver format:"
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_upload_of_a_python_file_without_a_class(world):
    refusal = await _refused(upload_driver(_form("helper_tool.py", b"VALUE = 1\n")))
    assert (refusal.status_code, refusal.detail) == (
        422, "No valid driver class found in Python file",
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_upload_whose_registration_fails(world, monkeypatch):
    def _explode(cls):
        raise RuntimeError("registry full")

    monkeypatch.setattr(routes, "register_driver", _explode)
    refusal = await _refused(upload_driver(_form("acme_widget.avcdriver", _yaml())))
    assert (refusal.status_code, refusal.detail) == (
        500, "Failed to load uploaded driver 'acme_widget.avcdriver'",
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_upload_whose_orphan_retry_fails(world):
    world.retry_fails = True
    result = await upload_driver(_form("acme_widget.py", _python()))
    assert result["activated_devices"] == []
    assert world.events == [("register", DRIVER_ID), ("retry",), ("announce", DRIVER_ID)]


# --- bundle upload -----------------------------------------------------------


@pytest.mark.asyncio
async def test_bundle_without_a_file(world):
    refusal = await _refused(upload_driver_bundle(_form(None)))
    assert (refusal.status_code, refusal.detail) == (
        422, "No file provided. Use 'file' field in multipart form.",
    )


@pytest.mark.asyncio
async def test_bundle_over_25_mb(world):
    refusal = await _refused(upload_driver_bundle(_form("b.zip", b"0" * (25 * 1024 * 1024 + 1))))
    assert (refusal.status_code, refusal.detail) == (422, "Bundle is too large (max 25 MB).")


@pytest.mark.asyncio
async def test_bundle_of_more_than_100_files(world):
    data = _zip({f"f{i}.py": b"" for i in range(101)})
    refusal = await _refused(upload_driver_bundle(_form("b.zip", data)))
    assert (refusal.status_code, refusal.detail) == (422, "Bundle has too many files.")


@pytest.mark.asyncio
async def test_bundle_whose_contents_unpack_past_50_mb(world):
    data = _zip({"acme_widget.py": b"\0" * (50 * 1024 * 1024 + 1)})
    refusal = await _refused(upload_driver_bundle(_form("b.zip", data)))
    assert (refusal.status_code, refusal.detail) == (422, "Bundle contents are too large.")


@pytest.mark.asyncio
async def test_bundle_of_folders_only(world):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("folder/", b"")
    refusal = await _refused(upload_driver_bundle(_form("b.zip", buf.getvalue())))
    assert (refusal.status_code, refusal.detail) == (422, "Bundle is empty.")


@pytest.mark.asyncio
async def test_bundle_of_a_yaml_driver_renames_the_main_and_keeps_the_companion_name(world):
    data = _zip({
        "copy_of_widget.avcdriver": _yaml(companion="./copy_of_widget_discovery.py"),
        "copy_of_widget_discovery.py": COMPANION,
    })
    result = await upload_driver_bundle(_form("b.zip", data))
    assert result == {
        "status": "uploaded",
        "driver_id": DRIVER_ID,
        "files": ["acme_widget.avcdriver", "copy_of_widget_discovery.py"],
        "activated_devices": ["waiting_device"],
    }
    assert world.events == [
        ("register", DRIVER_ID), ("refresh",), ("retry",), ("announce", DRIVER_ID),
    ]


@pytest.mark.asyncio
async def test_bundle_whose_yaml_the_loader_refuses_removes_what_it_created(world):
    data = _zip({"acme_widget.avcdriver": _yaml(transport="carrier_pigeon")})
    refusal = await _refused(upload_driver_bundle(_form("b.zip", data)))
    assert (refusal.status_code, refusal.detail) == (
        422, "Invalid driver definition file in bundle.",
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_bundle_whose_yaml_has_a_misspelled_key(world):
    data = _zip({"acme_widget.avcdriver": _yaml(comands={})})
    refusal = await _refused(upload_driver_bundle(_form("b.zip", data)))
    assert refusal.status_code == 422
    assert refusal.detail.startswith("The driver was not imported.")
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_bundle_without_a_class_keeps_a_file_it_overwrote(world):
    (world.repo / "acme_widget.py").write_bytes(b"PREVIOUS = 1\n")
    data = _zip({"acme_widget.py": b"VALUE = 1\n", "acme_widget_sim.py": b"SIM = 1\n"})
    refusal = await _refused(upload_driver_bundle(_form("b.zip", data)))
    assert (refusal.status_code, refusal.detail) == (
        422, "No valid driver class found in the bundle's Python file.",
    )
    assert _tree(world.repo) == {"acme_widget.py": b"VALUE = 1\n"}


@pytest.mark.asyncio
async def test_bundle_whose_registration_fails(world, monkeypatch):
    def _explode(cls):
        raise RuntimeError("registry full")

    monkeypatch.setattr(routes, "register_driver", _explode)
    data = _zip({"acme_widget.py": _python(), "acme_widget_sim.py": b"SIM = 1\n"})
    refusal = await _refused(upload_driver_bundle(_form("bundle.zip", data)))
    assert (refusal.status_code, refusal.detail) == (
        500, "Failed to load uploaded driver bundle 'bundle.zip'",
    )
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_bundle_whose_orphan_retry_fails(world):
    world.retry_fails = True
    result = await upload_driver_bundle(_form("b.zip", _zip({"acme_widget.py": _python()})))
    assert result["activated_devices"] == []
    assert world.events[-1] == ("announce", DRIVER_ID)


# --- uninstall ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_refuses_a_driver_devices_use(world):
    world.engine.project = SimpleNamespace(devices=[
        SimpleNamespace(id="room_a", driver=DRIVER_ID),
        SimpleNamespace(id="room_b", driver="other"),
        SimpleNamespace(id="room_c", driver=DRIVER_ID),
    ])
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    refusal = await _refused(uninstall_driver(DRIVER_ID))
    assert (refusal.status_code, refusal.detail) == (
        409, "Cannot uninstall: driver is in use by device(s): room_a, room_c",
    )
    assert set(_tree(world.repo)) == {"acme_widget.avcdriver"}


@pytest.mark.asyncio
async def test_uninstall_with_a_project_no_device_of_which_uses_it(world):
    world.engine.project = SimpleNamespace(devices=[SimpleNamespace(id="a", driver="other")])
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml())
    result = await uninstall_driver(DRIVER_ID)
    assert result == {"status": "uninstalled", "driver_id": DRIVER_ID}
    assert world.events == [("unregister", DRIVER_ID), ("refresh",), ("announce", DRIVER_ID)]
    assert _tree(world.repo) == {}


@pytest.mark.asyncio
async def test_uninstall_without_a_driver_folder(world, monkeypatch):
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: world.repo / "missing")
    refusal = await _refused(uninstall_driver(DRIVER_ID))
    assert (refusal.status_code, refusal.detail) == (404, "Driver not found")


@pytest.mark.asyncio
async def test_uninstall_of_a_driver_not_here(world):
    refusal = await _refused(uninstall_driver(DRIVER_ID))
    assert (refusal.status_code, refusal.detail) == (
        404, "Driver 'acme_widget' not found in driver_repo",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("named", "left"),
    [
        ("..", {"acme_widget_probe.py"}),
        ("./acme_widget_probe.py", {"acme_widget_probe.py"}),
        ("./elsewhere/acme_widget_discovery.py", {"acme_widget_probe.py"}),
        ("./not_here_discovery.py", {"acme_widget_probe.py"}),
    ],
)
async def test_uninstall_removes_only_a_named_discovery_companion_here(world, named, left):
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml(companion=named))
    (world.repo / "acme_widget_probe.py").write_bytes(b"MINE = 1\n")
    (world.repo / "acme_widget_discovery.py").write_bytes(COMPANION)
    await uninstall_driver(DRIVER_ID)
    expected = set(left)
    if "acme_widget_discovery.py" not in named:
        expected.add("acme_widget_discovery.py")
    assert set(_tree(world.repo)) == expected


def _link_outside(world, name: str) -> Path:
    """A companion in the driver folder that is a link to a file outside it."""
    outside = world.repo.parent / "outside_discovery.py"
    outside.write_bytes(COMPANION)
    try:
        (world.repo / name).symlink_to(outside)
    except OSError:
        pytest.skip("this system cannot make a symbolic link")
    return outside


@pytest.mark.asyncio
async def test_uninstall_leaves_a_companion_that_links_outside_the_folder(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(
        _yaml(companion="./acme_widget_discovery.py")
    )
    _link_outside(world, "acme_widget_discovery.py")
    await uninstall_driver(DRIVER_ID)
    assert (world.repo / "acme_widget_discovery.py").is_symlink()


@pytest.mark.asyncio
async def test_update_leaves_an_old_companion_that_links_outside_the_folder(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(
        _yaml(companion="./acme_widget_discovery.py")
    )
    _link_outside(world, "acme_widget_discovery.py")
    world.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(version="2.0.0")
    await _update("acme_widget.avcdriver")
    assert (world.repo / "acme_widget_discovery.py").is_symlink()


@pytest.mark.asyncio
async def test_uninstall_reads_an_unreadable_yaml_as_naming_no_companion(world, monkeypatch):
    main = world.repo / "acme_widget.avcdriver"
    main.write_bytes(_yaml(companion="./acme_widget_discovery.py"))
    (world.repo / "acme_widget_discovery.py").write_bytes(COMPANION)
    real_read_text = Path.read_text

    def _read_text(self, *args, **kwargs):
        if self == main:
            raise PermissionError("locked")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    await uninstall_driver(DRIVER_ID)
    assert set(_tree(world.repo)) == {"acme_widget_discovery.py"}


@pytest.mark.asyncio
async def test_uninstall_reads_an_empty_yaml_as_naming_no_companion(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(b"")
    (world.repo / "acme_widget_discovery.py").write_bytes(COMPANION)
    await uninstall_driver(DRIVER_ID)
    assert set(_tree(world.repo)) == {"acme_widget_discovery.py"}


@pytest.mark.asyncio
async def test_uninstall_of_every_copy_and_each_kind_of_companion(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(
        _yaml(companion="./acme_widget_discovery.py")
    )
    (world.repo / "acme_widget_discovery.py").write_bytes(COMPANION)
    (world.repo / "renamed.py").write_bytes(_python())
    (world.repo / "renamed_sim.py").write_bytes(b"SIM = 1\n")
    (world.repo / "unrelated.py").write_bytes(_python("someone_else"))
    await uninstall_driver(DRIVER_ID)
    assert set(_tree(world.repo)) == {"unrelated.py"}


# --- the copies an upload replaces --------------------------------------------


def test_an_upload_removes_a_yaml_copy_of_the_same_id(world):
    kept = world.repo / "acme_widget.avcdriver"
    kept.write_bytes(_yaml())
    (world.repo / "old_name.avcdriver").write_bytes(_yaml())
    (world.repo / "old_name.py").write_bytes(_python())
    assert _remove_other_copies(world.repo, DRIVER_ID, kept) == ["old_name.avcdriver"]
    assert set(_tree(world.repo)) == {"acme_widget.avcdriver", "old_name.py"}


def test_an_upload_drops_a_python_copy_s_companion_the_kept_name_already_has(world):
    kept = world.repo / "acme_widget.py"
    kept.write_bytes(_python())
    (world.repo / "acme_widget_sim.py").write_bytes(b"KEPT_SIM = 1\n")
    (world.repo / "old_name.py").write_bytes(_python())
    (world.repo / "old_name_sim.py").write_bytes(b"OLD_SIM = 1\n")
    (world.repo / "old_name_discovery.py").write_bytes(b"OLD_DISCOVERY = 1\n")
    assert _remove_other_copies(world.repo, DRIVER_ID, kept) == ["old_name.py"]
    assert _tree(world.repo) == {
        "acme_widget.py": _python(),
        "acme_widget_discovery.py": b"OLD_DISCOVERY = 1\n",
        "acme_widget_sim.py": b"KEPT_SIM = 1\n",
    }
