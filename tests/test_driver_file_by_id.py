"""A driver's file is the one that declares its id, whatever it was called.

A browser saves a second download of the same file as "acme_widget (2).py",
and a person renames a starter copy before importing it. An upload used to
keep the name it arrived with, while the Code view, export and uninstall
looked for ``<id>.py``: the driver installed and could not be opened, and a
renamed copy left beside a later ``<id>.py`` shadowed it at the next start
(the first file alphabetically won). One rule now: an upload is saved under
its declared id, every door finds a driver by what each file declares, and
when two files are one driver the one named for its id wins.
"""

import io
import sys
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from openavc.api.routes.drivers import uninstall_driver, upload_driver, upload_driver_bundle
from openavc.api.routes.python_drivers import (
    delete_python_driver,
    export_python_driver_bundle,
    get_python_driver_source,
    save_python_driver_source,
)
from openavc.drivers.driver_loader import list_python_drivers, load_python_drivers
from openavc.drivers.registry import get_driver_class, unregister_driver


def _source(mark: str, driver_id: str = "acme_widget") -> str:
    return (
        "from openavc.drivers.base import BaseDriver\n"
        "\n"
        f"MARK = {mark!r}\n"
        "\n"
        "\n"
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
    )


@pytest.fixture
def driver_repo(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr("openavc.api.routes.drivers._get_driver_repo_dir", lambda: repo)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)
    return repo


@pytest.fixture(autouse=True)
def stub_engine_wiring(monkeypatch):
    for module in ("drivers", "python_drivers"):
        monkeypatch.setattr(f"openavc.api.routes.{module}.register_driver", lambda cls: None)
        monkeypatch.setattr(f"openavc.api.routes.{module}.unregister_driver", lambda driver_id: None)
    monkeypatch.setattr(
        "openavc.api.discovery.refresh_all_device_matches",
        AsyncMock(return_value=None),
        raising=False,
    )
    engine = MagicMock()
    engine.project = None
    engine.devices.retry_all_orphans = AsyncMock(return_value=[])
    engine.devices.get_devices_using_driver = lambda driver_id: []
    monkeypatch.setattr("openavc.api.routes.drivers._get_engine", lambda: engine)
    monkeypatch.setattr("openavc.api.routes.python_drivers._get_engine", lambda: engine)
    monkeypatch.setattr(
        "openavc.api.routes.drivers.announce_drivers_changed", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "openavc.api.routes.python_drivers._announce", AsyncMock(return_value=None)
    )
    yield
    for name in [n for n in sys.modules if n.startswith("openavc_driver_acme_widget")]:
        sys.modules.pop(name, None)
    unregister_driver("acme_widget")


class _Upload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _request(filename: str, data: bytes) -> MagicMock:
    req = MagicMock()
    req.form = AsyncMock(return_value={"file": _Upload(filename, data)})
    return req


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _py_files(repo):
    return sorted(p.name for p in repo.glob("*.py"))


# --- the upload doors --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("arrived_as", ["acme_widget (2).py", "acme-widget-starter.py"])
async def test_an_upload_is_saved_under_its_declared_id(driver_repo, arrived_as):
    result = await upload_driver(_request(arrived_as, _source("new").encode()))

    assert result["driver_id"] == "acme_widget"
    assert result["file"] == "acme_widget.py"
    assert _py_files(driver_repo) == ["acme_widget.py"]
    source = await get_python_driver_source("acme_widget")
    assert "MARK = 'new'" in source["source"]


@pytest.mark.asyncio
async def test_a_yaml_upload_is_saved_under_its_declared_id(driver_repo):
    yaml_text = (
        "id: acme_widget\nname: Acme Widget\nmanufacturer: Acme\ncategory: utility\n"
        "version: 1.0.0\ntransport: tcp\ncommands: {}\n"
    )
    result = await upload_driver(_request("acme_widget (2).avcdriver", yaml_text.encode()))
    assert result["file"] == "acme_widget.avcdriver"
    assert sorted(p.name for p in driver_repo.iterdir()) == ["acme_widget.avcdriver"]


@pytest.mark.asyncio
async def test_a_driver_whose_id_cannot_be_read_keeps_a_safe_name_only(driver_repo):
    with pytest.raises(HTTPException) as refused:
        await upload_driver(_request("not a driver (2).py", b"def broken(:\n"))
    assert refused.value.status_code == 422
    assert _py_files(driver_repo) == []


@pytest.mark.asyncio
async def test_a_bundle_is_saved_under_its_declared_id_with_its_companions(driver_repo):
    bundle = _zip({
        "acme-widget-starter.py": _source("new"),
        "acme-widget-starter_sim.py": "SIM = 1\n",
        "acme-widget-starter_discovery.py": "DISCOVERY = 1\n",
    })
    result = await upload_driver_bundle(_request("acme_widget (7).zip", bundle))

    assert result["driver_id"] == "acme_widget"
    assert result["files"] == [
        "acme_widget.py", "acme_widget_discovery.py", "acme_widget_sim.py",
    ]
    assert _py_files(driver_repo) == result["files"]


@pytest.mark.asyncio
async def test_an_upload_replaces_a_renamed_copy_and_keeps_its_simulator(driver_repo):
    (driver_repo / "acme-widget-starter.py").write_text(_source("old"), encoding="utf-8")
    (driver_repo / "acme-widget-starter_sim.py").write_text("SIM = 1\n", encoding="utf-8")

    await upload_driver(_request("acme_widget.py", _source("new").encode()))

    assert _py_files(driver_repo) == ["acme_widget.py", "acme_widget_sim.py"]
    assert (driver_repo / "acme_widget_sim.py").read_text(encoding="utf-8") == "SIM = 1\n"


# --- an install made before uploads were named by id --------------------------


@pytest.mark.asyncio
async def test_a_renamed_install_opens_saves_exports_and_deletes_by_its_id(driver_repo):
    renamed = driver_repo / "acme-widget-starter.py"
    renamed.write_text(_source("old"), encoding="utf-8")
    (driver_repo / "acme-widget-starter_sim.py").write_text("SIM = 1\n", encoding="utf-8")

    opened = await get_python_driver_source("acme_widget")
    assert opened["filename"] == "acme-widget-starter.py"

    saved = await save_python_driver_source("acme_widget", {"source": _source("edited")})
    assert saved["status"] == "saved"
    assert "MARK = 'edited'" in renamed.read_text(encoding="utf-8")

    exported = await export_python_driver_bundle("acme_widget")
    names = sorted(zipfile.ZipFile(io.BytesIO(exported.body)).namelist())
    assert names == ["acme_widget.py", "acme_widget_sim.py"]

    deleted = await delete_python_driver("acme_widget")
    assert deleted["removed_companions"] == ["acme-widget-starter_sim.py"]
    assert _py_files(driver_repo) == []


@pytest.mark.asyncio
async def test_half_written_source_stays_the_driver_its_name_says(driver_repo):
    (driver_repo / "acme_widget.py").write_text("class AcmeWidget(:\n", encoding="utf-8")
    opened = await get_python_driver_source("acme_widget")
    assert opened["filename"] == "acme_widget.py"


@pytest.mark.asyncio
async def test_two_renamed_copies_are_refused_for_editing_and_deleted_together(driver_repo):
    (driver_repo / "copy-one.py").write_text(_source("one"), encoding="utf-8")
    (driver_repo / "copy-two.py").write_text(_source("two"), encoding="utf-8")

    with pytest.raises(HTTPException) as refused:
        await get_python_driver_source("acme_widget")
    assert refused.value.status_code == 409
    assert "copy-one.py" in refused.value.detail and "copy-two.py" in refused.value.detail

    await delete_python_driver("acme_widget")
    assert _py_files(driver_repo) == []


@pytest.mark.asyncio
async def test_uninstall_removes_every_copy(driver_repo):
    (driver_repo / "acme-widget-starter.py").write_text(_source("old"), encoding="utf-8")
    (driver_repo / "acme_widget.py").write_text(_source("new"), encoding="utf-8")
    (driver_repo / "acme_widget_sim.py").write_text("SIM = 1\n", encoding="utf-8")

    await uninstall_driver("acme_widget")
    assert _py_files(driver_repo) == []


# --- what loads ----------------------------------------------------------------


def test_the_copy_named_for_its_id_loads_and_lists(driver_repo):
    # "acme-widget-starter.py" sorts before "acme_widget.py", and the first
    # file alphabetically used to win.
    (driver_repo / "acme-widget-starter.py").write_text(_source("old"), encoding="utf-8")
    (driver_repo / "acme_widget.py").write_text(_source("new"), encoding="utf-8")

    assert load_python_drivers([driver_repo]) == 1
    assert get_driver_class("acme_widget").MARK == "new"

    listed = [d for d in list_python_drivers([driver_repo]) if d["id"] == "acme_widget"]
    assert [d["filename"] for d in listed] == ["acme_widget.py"]
