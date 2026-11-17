"""A refused driver import, install or update leaves the driver that was
already installed exactly as it was: its files byte for byte, its class in the
registry, its module in ``sys.modules``, and no staging folder behind.

One case per door (the single-file import, the bundle import, the community
install and the community update), each starting from a good copy that is
installed and registered for real, because a refusal that starts from an empty
folder cannot show what a refusal takes away. And a driver's own platform
floor is read from the file for both kinds of driver, at every door, Discovery's
Install & Add included, which sends no floor with its request.
"""

from __future__ import annotations

import hashlib
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

from openavc.api import driver_catalog
from openavc.api.models import CommunityDriverInstallRequest
from openavc.api.routes import drivers as routes
from openavc.api.routes.drivers import _BUNDLE_REFUSED, _IMPORT_REFUSED, _UPDATE_REFUSED
from openavc.drivers import registry
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import load_driver_file, load_python_driver_file
from openavc.drivers.python_modules import NO_DRIVER_CLASS
from openavc.utils.community_integrity import ArtifactHashes

CATALOG = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/"
DRIVER_ID = "acme_widget"
MODULE = f"openavc_driver_{DRIVER_ID}"


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
    """Each catalog URL answers bytes, a status code, or raises; an unlisted
    one is a 404."""

    def __init__(self):
        self.pages: dict[str, object] = {}
        self.hashes: dict[str, str] | None = None

    def __call__(self, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str):
        page = self.pages.get(url, 404)
        if isinstance(page, BaseException):
            raise page
        if isinstance(page, int):
            return _Response(url, page, b"")
        return _Response(url, 200, page)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    catalog = _Catalog()
    monkeypatch.setattr("httpx.AsyncClient", catalog)
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: repo)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)
    # The real registry, on a copy this test owns, without this driver: the
    # repo starts empty, and another test in the same process can leave an
    # acme_widget registered.
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY",
                        {k: v for k, v in registry._DRIVER_REGISTRY.items() if k != DRIVER_ID})

    async def _hashes(driver_id, force=False):
        return ArtifactHashes(f"Driver '{driver_id}'", catalog.hashes, source="catalog")

    monkeypatch.setattr(driver_catalog, "_catalog_hashes", _hashes)

    async def _nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr("openavc.api.discovery.refresh_all_device_matches", _nothing)
    monkeypatch.setattr(routes, "announce_drivers_changed", _nothing)
    engine = MagicMock()
    engine.project = None

    async def _none(*_args):
        return []

    engine.devices.retry_all_orphans = _none
    engine.devices.reload_driver = _none
    engine.devices.get_devices_using_driver = lambda driver_id: []
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    # And without its module: another test in the same process can leave
    # openavc_driver_acme_widget imported, which a refusal must not be blamed for.
    modules_before = {n: m for n, m in sys.modules.items() if "acme" in n}
    for name in modules_before:
        del sys.modules[name]
    yield SimpleNamespace(repo=repo, catalog=catalog)
    for name in [n for n in sys.modules if "acme" in n]:
        del sys.modules[name]
    sys.modules.update(modules_before)


@pytest.fixture
def leftover(monkeypatch):
    """A registration another test in the same process left behind."""
    monkeypatch.setitem(registry._DRIVER_REGISTRY, DRIVER_ID, object)


def test_a_registration_left_by_another_test_is_not_carried_in(leftover, lib):
    assert registry.get_driver_class(DRIVER_ID) is None


@pytest.fixture
def leftover_module(monkeypatch):
    """A driver module another test in the same process left imported."""
    monkeypatch.setitem(sys.modules, MODULE, SimpleNamespace())


def test_a_module_left_by_another_test_is_not_carried_in(leftover_module, lib):
    assert MODULE not in sys.modules


def _yaml(**extra) -> bytes:
    body: dict = {
        "id": DRIVER_ID,
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "commands": {"power_on": {"label": "On", "send": "P\r", "params": {}}},
    }
    body.update(extra)
    return yaml.safe_dump(body, sort_keys=False).encode()


def _python(mark: str, **info) -> bytes:
    extra = "".join(f"        {k!r}: {v!r},\n" for k, v in info.items())
    return (
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeWidget(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        f"        'id': {DRIVER_ID!r},\n"
        "        'name': 'Acme Widget',\n"
        "        'manufacturer': 'Acme',\n"
        "        'category': 'utility',\n"
        "        'version': '1.0.0',\n"
        "        'transport': 'tcp',\n"
        f"{extra}"
        "    }\n"
        f"    MARK = {mark!r}\n"
    ).encode()


COMPANION = b"async def probe(ctx):\n    return None\n"


def _installed_yaml(lib, data: bytes = None) -> type:
    """A YAML driver installed and registered the way a start-up loads it
    (any companion it names is written first)."""
    path = lib.repo / "acme_widget.avcdriver"
    path.write_bytes(data if data is not None else _yaml())
    cls = create_configurable_driver_class(load_driver_file(path))
    registry.register_driver(cls)
    return cls


def _installed_python(lib) -> type:
    """A Python driver installed and imported the way a start-up loads it."""
    path = lib.repo / "acme_widget.py"
    path.write_bytes(_python("old"))
    cls = load_python_driver_file(path)
    registry.register_driver(cls)
    return cls


def _tree(repo: Path) -> dict[str, bytes]:
    """Every file below the driver folder, by its path there, so a staging
    folder left behind is seen."""
    return {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def _kept(lib, before: dict[str, bytes], cls: type) -> None:
    assert _tree(lib.repo) == before
    assert [p.name for p in lib.repo.iterdir() if p.is_dir() and p.name != "__pycache__"] == []
    assert registry.get_driver_class(DRIVER_ID) is cls
    assert not [n for n in sys.modules if n.startswith("_openavc_driver_validate_")]


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await call
    return caught.value


class _Upload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _form(filename: str, data: bytes) -> MagicMock:
    request = MagicMock()

    async def _read_form():
        return {"file": _Upload(filename, data)}

    request.form = _read_form
    return request


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


def _install(file_name: str):
    return routes.install_community_driver(
        CommunityDriverInstallRequest(driver_id=DRIVER_ID, file_url=CATALOG + file_name)
    )


def _update(file_name: str):
    request = MagicMock()

    async def _json():
        return {"file_url": CATALOG + file_name}

    request.json = _json
    return routes.update_driver(DRIVER_ID, request)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- the single-file import ---------------------------------------------------


@pytest.mark.asyncio
async def test_an_import_with_a_misspelled_key_keeps_the_installed_copy(lib):
    cls = _installed_yaml(lib)
    before = _tree(lib.repo)
    refusal = await _refused(
        routes.upload_driver(_form("acme_widget.avcdriver", _yaml(comands={"x": {}})))
    )
    assert refusal.status_code == 422
    assert refusal.detail.startswith("The driver was not imported.")
    _kept(lib, before, cls)


@pytest.mark.asyncio
async def test_an_import_whose_companion_is_not_here_keeps_the_installed_copy(lib):
    cls = _installed_yaml(lib)
    before = _tree(lib.repo)
    data = _yaml(discovery={"python": {"file": "./acme_widget_discovery.py"}})
    refusal = await _refused(routes.upload_driver(_form("acme_widget.avcdriver", data)))
    assert refusal.status_code == 422
    assert refusal.detail == (
        f"{_IMPORT_REFUSED}:\nIt names the discovery file './acme_widget_discovery.py' "
        "(discovery.python.file), which is not in the driver folder. Add that file first."
    )
    _kept(lib, before, cls)


@pytest.mark.asyncio
async def test_an_import_of_a_python_file_without_a_class_keeps_the_installed_copy(lib):
    cls = _installed_python(lib)
    old_module = sys.modules[MODULE]
    before = _tree(lib.repo)
    refusal = await _refused(routes.upload_driver(_form("acme_widget.py", b"VALUE = 1\n")))
    assert (refusal.status_code, refusal.detail) == (
        422, f"{_IMPORT_REFUSED}:\n{NO_DRIVER_CLASS}",
    )
    _kept(lib, before, cls)
    assert sys.modules[MODULE] is old_module


@pytest.mark.asyncio
async def test_an_import_whose_registration_fails_keeps_the_installed_copy(lib, monkeypatch):
    cls = _installed_python(lib)
    old_module = sys.modules[MODULE]
    before = _tree(lib.repo)
    real = registry.register_driver

    def _refuse_new(driver_class):
        if getattr(driver_class, "MARK", None) == "new":
            raise RuntimeError("registry full")
        real(driver_class)

    monkeypatch.setattr(registry, "register_driver", _refuse_new)
    for module in ("openavc.drivers.driver_change", "openavc.api.routes.drivers"):
        if module in sys.modules and hasattr(sys.modules[module], "register_driver"):
            monkeypatch.setattr(sys.modules[module], "register_driver", _refuse_new)
    refusal = await _refused(routes.upload_driver(_form("acme_widget.py", _python("new"))))
    assert refusal.status_code == 500
    _kept(lib, before, cls)
    assert sys.modules[MODULE] is old_module


# --- the bundle import ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bundle_without_a_class_keeps_the_installed_copy_and_its_simulator(lib):
    cls = _installed_python(lib)
    (lib.repo / "acme_widget_sim.py").write_bytes(b"OLD_SIM = 1\n")
    before = _tree(lib.repo)
    data = _zip({"acme_widget.py": b"VALUE = 1\n", "acme_widget_sim.py": b"SIM = 1\n"})
    refusal = await _refused(routes.upload_driver_bundle(_form("b.zip", data)))
    assert (refusal.status_code, refusal.detail) == (
        422, f"{_BUNDLE_REFUSED}:\n{NO_DRIVER_CLASS}",
    )
    _kept(lib, before, cls)


# --- the community install --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reinstall_above_this_platform_keeps_the_installed_copy(lib):
    cls = _installed_yaml(lib)
    before = _tree(lib.repo)
    lib.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(min_platform_version="999.0.0")
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert refusal.status_code == 422
    assert refusal.detail.startswith("This driver requires OpenAVC 999.0.0 or later.")
    _kept(lib, before, cls)


@pytest.mark.asyncio
async def test_a_reinstall_whose_companion_fails_keeps_the_installed_copy(lib):
    (lib.repo / "acme_widget_discovery.py").write_bytes(COMPANION)
    cls = _installed_yaml(lib, _yaml(discovery={"python": {"file": "./acme_widget_discovery.py"}}))
    before = _tree(lib.repo)
    lib.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        discovery={"python": {"file": "./acme_widget_discovery.py"}}, version="2.0.0"
    )
    lib.catalog.pages[CATALOG + "acme_widget_discovery.py"] = 500
    refusal = await _refused(_install("acme_widget.avcdriver"))
    assert refusal.status_code == 502
    _kept(lib, before, cls)


@pytest.mark.asyncio
async def test_a_python_install_above_this_platform_is_refused_with_nothing_written(lib):
    lib.catalog.pages[CATALOG + "acme_widget.py"] = _python(
        "new", min_platform_version="999.0.0"
    )
    refusal = await _refused(_install("acme_widget.py"))
    assert refusal.status_code == 422
    assert refusal.detail.startswith("This driver requires OpenAVC 999.0.0 or later.")
    assert _tree(lib.repo) == {}
    assert registry.get_driver_class(DRIVER_ID) is None
    assert MODULE not in sys.modules


@pytest.mark.asyncio
async def test_a_python_driver_above_this_platform_is_refused_through_install_and_add(
    lib, monkeypatch
):
    from openavc.api import discovery
    from openavc.api.discovery import InstallAndMatchRequest, install_and_match

    monkeypatch.setattr(discovery, "_engine", MagicMock())
    lib.catalog.pages[CATALOG + "acme_widget.py"] = _python(
        "new", min_platform_version="999.0.0"
    )
    refusal = await _refused(install_and_match(InstallAndMatchRequest(
        ip="192.0.2.60", driver_id=DRIVER_ID, file_url=CATALOG + "acme_widget.py",
    )))
    assert refusal.status_code == 422
    assert refusal.detail.startswith("This driver requires OpenAVC 999.0.0 or later.")
    assert _tree(lib.repo) == {}
    assert registry.get_driver_class(DRIVER_ID) is None


# --- the community update -----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_update_the_loader_refuses_keeps_the_installed_driver(lib):
    cls = _installed_yaml(lib)
    before = _tree(lib.repo)
    lib.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(transport="carrier_pigeon")
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert (refusal.status_code, refusal.detail) == (
        422, f"{_UPDATE_REFUSED}:\nUnsupported transport: carrier_pigeon",
    )
    _kept(lib, before, cls)


@pytest.mark.asyncio
async def test_an_update_whose_companion_fails_keeps_the_driver_and_its_companion(lib):
    declares = {"discovery": {"python": {"file": "./acme_widget_discovery.py"}}}
    (lib.repo / "acme_widget_discovery.py").write_bytes(b"OLD = 1\n")
    cls = _installed_yaml(lib, _yaml(**declares))
    before = _tree(lib.repo)
    lib.catalog.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(**declares, version="2.0.0")
    lib.catalog.pages[CATALOG + "acme_widget_discovery.py"] = 500
    refusal = await _refused(_update("acme_widget.avcdriver"))
    assert refusal.status_code == 502
    _kept(lib, before, cls)


@pytest.mark.asyncio
async def test_a_python_update_whose_simulator_fails_the_check_keeps_the_driver(lib):
    cls = _installed_python(lib)
    (lib.repo / "acme_widget_sim.py").write_bytes(b"old simulator\n")
    old_module = sys.modules[MODULE]
    before = _tree(lib.repo)
    new = _python("new")
    lib.catalog.pages[CATALOG + "acme_widget.py"] = new
    lib.catalog.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION
    lib.catalog.pages[CATALOG + "acme_widget_sim.py"] = b"class Simulator:\n    pass\n"
    lib.catalog.hashes = {
        "utility/acme_widget.py": _sha(new),
        "utility/acme_widget_discovery.py": _sha(COMPANION),
        "utility/acme_widget_sim.py": _sha(b"published simulator"),
    }
    refusal = await _refused(_update("acme_widget.py"))
    assert refusal.status_code == 502
    _kept(lib, before, cls)
    assert sys.modules[MODULE] is old_module


@pytest.mark.asyncio
async def test_a_python_update_above_this_platform_keeps_the_installed_driver(lib):
    cls = _installed_python(lib)
    old_module = sys.modules[MODULE]
    before = _tree(lib.repo)
    lib.catalog.pages[CATALOG + "acme_widget.py"] = _python(
        "new", min_platform_version="999.0.0"
    )
    refusal = await _refused(_update("acme_widget.py"))
    assert refusal.status_code == 422
    assert refusal.detail.startswith("This driver requires OpenAVC 999.0.0 or later.")
    _kept(lib, before, cls)
    assert sys.modules[MODULE] is old_module


@pytest.mark.asyncio
async def test_an_update_registers_the_new_driver_and_replaces_its_files(lib):
    _installed_python(lib)
    (lib.repo / "acme_widget_sim.py").write_bytes(b"old simulator\n")
    lib.catalog.pages[CATALOG + "acme_widget.py"] = _python("new")
    lib.catalog.pages[CATALOG + "acme_widget_sim.py"] = b"SIM = 2\n"
    answer = await _update("acme_widget.py")
    assert answer["status"] == "updated"
    assert _tree(lib.repo) == {"acme_widget.py": _python("new"), "acme_widget_sim.py": b"SIM = 2\n"}
    cls = registry.get_driver_class(DRIVER_ID)
    assert cls.MARK == "new"
    assert cls.__module__ == MODULE
    assert sys.modules[MODULE].__file__ == str(lib.repo / "acme_widget.py")
