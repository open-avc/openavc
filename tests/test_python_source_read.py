"""A Python driver's source is read the way Python's import reads it.

The import decodes a ``.py`` file's bytes itself: a UTF-8 byte-order mark is
dropped, and a coding line (``# -*- coding: latin-1 -*-``) names the encoding
the rest is read in. Every place that reads a driver without importing it
(the checker, the upload's floor gate, the Code view, the driver list, the
simulator checks) reads it the same way, so a file the import loads is read
the same everywhere, and a file the import cannot decode is an error at every
door rather than an empty reading.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException

from openavc.drivers import registry
from openavc.drivers.check import check_driver_file, main, scan_for_drivers
from openavc.drivers.driver_change import DriverChange, DriverRefused, admit
from openavc.drivers.driver_change import check_driver_file as check_upload
from openavc.drivers.driver_loader import driver_id_from_file, list_python_drivers
from openavc.drivers.python_info import ExtractError, extract_python_driver_info_full
from openavc.drivers.python_source import python_source
from openavc.drivers.simulator_info import check_simulator_file, read_simulator_info

BOM = b"\xef\xbb\xbf"
LATIN_1 = b"# -*- coding: latin-1 -*-\n"

_DRIVER = '''\
from openavc.drivers.base import BaseDriver


class AcmeWidgetDriver(BaseDriver):
    DRIVER_INFO = {{
        "id": "acme_widget",
        "name": "{name}",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
{extra}    }}
'''


def _driver(name: str = "Acme Widget", **extra: str) -> str:
    lines = "".join(f"        {key!r}: {value!r},\n" for key, value in extra.items())
    return _DRIVER.format(name=name, extra=lines)


def _bom_driver(**extra: str) -> bytes:
    return BOM + _driver(**extra).encode("utf-8")


def _latin_1_driver() -> bytes:
    return LATIN_1 + _driver(name="Acme Caf\xe9").encode("latin-1")


# What the import itself cannot decode: an encoding Python has never heard
# of, and a byte that is not UTF-8 in a file that declares no encoding.
UNDECODABLE = {
    "unknown_coding": b"# -*- coding: acme-8 -*-\n" + _driver().encode("ascii"),
    "not_utf_8": _driver(name="Acme \xff").encode("latin-1"),
}


def _imports(path: Path) -> bool:
    """Whether Python's own import can read the file."""
    try:
        compile(path.read_bytes(), str(path), "exec")
    except SyntaxError:
        return False
    return True


# --- the reader ----------------------------------------------------------------


def test_a_byte_order_mark_is_read_past(tmp_path):
    path = tmp_path / "acme_widget.py"
    path.write_bytes(_bom_driver())
    info, _ = extract_python_driver_info_full(path)
    assert info["id"] == "acme_widget"
    assert not python_source(path).startswith("﻿")


def test_a_coding_line_names_the_encoding_the_file_is_read_in(tmp_path):
    path = tmp_path / "acme_widget.py"
    path.write_bytes(_latin_1_driver())
    info, _ = extract_python_driver_info_full(path)
    assert info["name"] == "Acme Caf\xe9"


@pytest.mark.parametrize("case", sorted(UNDECODABLE))
def test_a_file_the_import_cannot_decode_is_a_reader_error_naming_the_file(tmp_path, case):
    path = tmp_path / "acme_widget.py"
    path.write_bytes(UNDECODABLE[case])
    assert not _imports(path)
    with pytest.raises(ExtractError) as caught:
        extract_python_driver_info_full(path)
    assert str(caught.value).startswith("acme_widget.py cannot be read as Python source")
    assert str(caught.value).endswith(
        "Save it as UTF-8, and delete any coding line that names another encoding."
    )


def test_a_byte_the_encoding_cannot_read_is_named_by_its_line(tmp_path):
    path = tmp_path / "acme_widget.py"
    path.write_bytes(UNDECODABLE["not_utf_8"])
    with pytest.raises(ExtractError, match=r"\(line 7 is not utf-8 text\)"):
        extract_python_driver_info_full(path)


def test_the_reader_agrees_with_the_import_on_every_file_it_reads(tmp_path):
    """The same text the import's own decoder gives, BOM and newlines too."""
    path = tmp_path / "acme_widget.py"
    for data in (_bom_driver(), _latin_1_driver(), _driver().replace("\n", "\r\n").encode()):
        path.write_bytes(data)
        assert python_source(path) == importlib.util.decode_source(data)


# --- drivers.check ---------------------------------------------------------------


@pytest.mark.parametrize("data", [_bom_driver(), _latin_1_driver()], ids=["bom", "latin_1"])
def test_the_checker_reads_a_file_the_import_reads(tmp_path, data):
    path = tmp_path / "acme_widget.py"
    path.write_bytes(data)
    result = check_driver_file(path)
    assert result.readable and result.errors == []


@pytest.mark.parametrize("data", [_bom_driver(), _latin_1_driver()], ids=["bom", "latin_1"])
def test_a_folder_walk_finds_a_file_the_import_reads(tmp_path, data):
    (tmp_path / "acme_widget.py").write_bytes(data)
    assert scan_for_drivers(tmp_path).files == [tmp_path / "acme_widget.py"]


@pytest.mark.parametrize("case", sorted(UNDECODABLE))
def test_a_folder_walk_reports_a_file_the_import_cannot_decode(tmp_path, case, capsys):
    (tmp_path / "acme_widget.py").write_bytes(UNDECODABLE[case])
    assert main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "acme_widget.py cannot be read as Python source" in err
    assert "1 unreadable" in err


@pytest.mark.parametrize("case", sorted(UNDECODABLE))
def test_a_named_file_the_import_cannot_decode_is_unreadable(tmp_path, case):
    path = tmp_path / "acme_widget.py"
    path.write_bytes(UNDECODABLE[case])
    result = check_driver_file(path)
    assert not result.readable
    assert result.errors and result.errors[0].startswith(
        "acme_widget.py cannot be read as Python source"
    )


# --- the simulator ---------------------------------------------------------------

_SIMULATOR = '''\
from openavc.simulator.tcp_simulator import TCPSimulator


class AcmeWidgetSimulator(TCPSimulator):
    SIMULATOR_INFO = {
        "driver_id": "acme_widget",
        "name": "Acme Widget Simulator",
        "transport": "tcp",
        "initial_state": {},
    }
'''


def test_a_simulator_with_a_byte_order_mark_is_read(tmp_path):
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_bytes(BOM + _SIMULATOR.encode())
    assert read_simulator_info(sim)["driver_id"] == "acme_widget"
    assert check_simulator_file(sim).errors == []


def test_a_simulator_the_import_cannot_decode_says_why(tmp_path):
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_bytes(b"# -*- coding: acme-8 -*-\n" + _SIMULATOR.encode())
    assert read_simulator_info(sim) is None
    errors = check_simulator_file(sim).errors
    assert len(errors) == 1 and errors[0].startswith("the file cannot be read as Python source")


def test_the_parity_check_reads_a_latin_1_simulator(tmp_path):
    from openavc.simulator.validate import validate_python_driver

    driver = tmp_path / "acme_widget.py"
    driver.write_text(_driver(), encoding="utf-8")
    (tmp_path / "acme_widget_sim.py").write_bytes(
        LATIN_1 + _SIMULATOR.replace("Simulator\"", "Simulator caf\xe9\"").encode("latin-1")
    )
    assert validate_python_driver(driver).errors == []


# --- the library: id, list, upload -------------------------------------------------


@pytest.mark.parametrize("data", [_bom_driver(), _latin_1_driver()], ids=["bom", "latin_1"])
def test_the_id_a_file_declares_is_read(tmp_path, data):
    path = tmp_path / "acme_widget_v2.py"
    path.write_bytes(data)
    assert driver_id_from_file(path) == "acme_widget"


def test_the_code_view_list_shows_what_the_file_declares(tmp_path):
    (tmp_path / "acme_widget_v2.py").write_bytes(_latin_1_driver())
    [entry] = list_python_drivers([tmp_path])
    assert (entry["id"], entry["name"]) == ("acme_widget", "Acme Caf\xe9")


def test_the_upload_floor_reads_a_file_with_a_byte_order_mark():
    with pytest.raises(DriverRefused) as caught:
        check_upload("acme_widget.py", _bom_driver(min_platform_version="999.0.0"))
    assert caught.value.status_code == 422
    assert caught.value.detail.startswith("This driver requires OpenAVC 999.0.0 or later.")


@pytest.fixture
def driver_folder(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(
        registry, "_DRIVER_REGISTRY",
        {k: v for k, v in registry._DRIVER_REGISTRY.items() if k != "acme_widget"},
    )
    return repo


@pytest.mark.parametrize("case", sorted(UNDECODABLE))
def test_an_upload_the_import_cannot_decode_is_refused_and_writes_nothing(driver_folder, case):
    with pytest.raises(DriverRefused) as caught:
        admit(DriverChange(driver_folder, "acme_widget.py", {"acme_widget.py": UNDECODABLE[case]}))
    assert caught.value.status_code == 422
    assert [p.name for p in driver_folder.iterdir()] == []


# --- the Code view -----------------------------------------------------------------


@pytest.fixture
def code_view(tmp_path, monkeypatch):
    import openavc.system_config as system_config

    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    return repo


async def test_the_code_view_opens_a_latin_1_driver_as_text(code_view):
    from openavc.api.routes import python_drivers as routes

    (code_view / "acme_widget.py").write_bytes(_latin_1_driver())
    body = await routes.get_python_driver_source("acme_widget")
    assert '"name": "Acme Caf\xe9"' in body["source"]


async def test_the_code_view_opens_a_bom_driver_without_the_mark(code_view):
    from openavc.api.routes import python_drivers as routes

    (code_view / "acme_widget.py").write_bytes(_bom_driver())
    body = await routes.get_python_driver_source("acme_widget")
    assert body["source"] == _driver()


@pytest.mark.parametrize("case", sorted(UNDECODABLE))
async def test_the_code_view_says_why_it_cannot_open_a_file(code_view, case):
    from openavc.api.routes import python_drivers as routes

    (code_view / "acme_widget.py").write_bytes(UNDECODABLE[case])
    with pytest.raises(HTTPException) as caught:
        await routes.get_python_driver_source("acme_widget")
    assert caught.value.status_code == 422
    assert caught.value.detail.startswith("acme_widget.py cannot be read as Python source")


async def test_opening_and_saving_a_latin_1_driver_keeps_what_it_says(code_view):
    """The save writes the text in the encoding its own coding line names, so
    the import reads back what the editor showed."""
    from openavc.api.routes import python_drivers as routes

    path = code_view / "acme_widget.py"
    path.write_bytes(_latin_1_driver())
    shown = (await routes.get_python_driver_source("acme_widget"))["source"]
    await routes.save_python_driver_source("acme_widget", {"source": shown})
    assert path.read_bytes() == _latin_1_driver()
    info, _ = extract_python_driver_info_full(path)
    assert info["name"] == "Acme Caf\xe9"


async def test_a_save_whose_text_its_coding_line_cannot_hold_is_refused(code_view):
    """By either button: no bytes would keep the text as written."""
    from openavc.api.routes import python_drivers as routes

    path = code_view / "acme_widget.py"
    path.write_bytes(_latin_1_driver())
    source = LATIN_1.decode() + _driver(name="Acme \u20ac")
    for extra in ({}, {"require_valid_syntax": True}):
        with pytest.raises(HTTPException) as caught:
            await routes.save_python_driver_source("acme_widget", {"source": source, **extra})
        assert caught.value.status_code == 422
        assert caught.value.detail.startswith("Line 8 has a character (\u20ac)")
        assert "delete the coding line" in caught.value.detail
    assert path.read_bytes() == _latin_1_driver()


async def test_a_save_naming_an_encoding_python_does_not_know_is_refused(code_view):
    from openavc.api.routes import python_drivers as routes

    path = code_view / "acme_widget.py"
    path.write_bytes(_latin_1_driver())
    with pytest.raises(HTTPException) as caught:
        await routes.save_python_driver_source(
            "acme_widget", {"source": "# -*- coding: acme-8 -*-\n" + _driver()},
        )
    assert caught.value.status_code == 422
    assert "unknown encoding: acme-8" in caught.value.detail
    assert path.read_bytes() == _latin_1_driver()


async def test_a_new_driver_is_written_in_the_encoding_its_coding_line_names(code_view):
    from openavc.api.models import PythonDriverCreateRequest
    from openavc.api.routes import python_drivers as routes

    source = _latin_1_driver().decode("latin-1")
    await routes.create_python_driver(PythonDriverCreateRequest(id="acme_widget", source=source))
    assert (code_view / "acme_widget.py").read_bytes() == _latin_1_driver()
