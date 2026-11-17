"""A driver file that loads but holds what the author's doors refuse (a value
of the wrong kind, a key the contract does not declare) keeps loading at every
door that loads a driver: the start-up pass, the upload, the bundle upload and
a project bundle's import. What is wrong with it is said instead, on its
Installed entry and in the upload's answer, and no longer once the file
changes. A null top-level section is no notice: it reads as absent.
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
from typing import Any

import pytest
import yaml

from openavc.api.routes import drivers as routes
from openavc.core import project_library
from openavc.drivers import driver_change, driver_loader, refused_files, registry

DRIVER_ID = "acme_widget"
ON_CONNECT = "on_connect: must be a list; write a single entry as a one-item list"
VERSION = "version: must be a string; put it in quotes"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    folder = tmp_path / "driver_repo"
    folder.mkdir()
    monkeypatch.setattr(
        registry, "_DRIVER_REGISTRY",
        {k: v for k, v in registry._DRIVER_REGISTRY.items() if not k.startswith("acme")},
    )
    monkeypatch.setattr(refused_files, "_refused", {})
    monkeypatch.setattr(refused_files, "_noticed", {})
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: folder)

    async def _nothing(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(driver_change, "_running_engine", lambda: None)
    monkeypatch.setattr("openavc.api.discovery.refresh_all_device_matches", _nothing)
    modules_before = {n: m for n, m in sys.modules.items() if "acme" in n}
    yield folder
    for name in [n for n in sys.modules if "acme" in n]:
        if name in modules_before:
            sys.modules[name] = modules_before[name]
        else:
            sys.modules.pop(name, None)


def _yaml(**extra: Any) -> bytes:
    body: dict[str, Any] = {
        "id": DRIVER_ID,
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"power_on": {"label": "On", "send": "P\r"}},
    }
    body.update(extra)
    return yaml.safe_dump(body, sort_keys=False).encode()


def _form(filename: str, data: bytes) -> Any:
    class _Upload:
        def __init__(self) -> None:
            self.filename = filename

        async def read(self) -> bytes:
            return data

    class _Request:
        async def form(self) -> dict[str, Any]:
            return {"file": _Upload()}

    return _Request()


async def _installed(repo: Any) -> dict[str, dict[str, Any]]:
    entries = (await routes.list_installed_community_drivers())["drivers"]
    return {entry["filename"]: entry for entry in entries}


def test_the_loader_reads_a_null_section_as_absent_and_says_nothing(repo) -> None:
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_yaml(polling=None, responses=None))
    read = driver_loader.read_driver_file(path)
    assert read.definition is not None
    assert "polling" not in read.definition and "responses" not in read.definition
    assert read.notices == ()


def test_the_loader_loads_a_wrong_shape_and_names_it(repo) -> None:
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_yaml(on_connect="P?\r", version=1.0))
    read = driver_loader.read_driver_file(path)
    assert read.definition is not None and read.reasons == ()
    assert read.notices == (VERSION, ON_CONNECT)


async def test_the_start_up_pass_puts_the_notices_on_the_installed_entry(repo) -> None:
    (repo / "acme_widget.avcdriver").write_bytes(_yaml(on_connect="P?\r"))
    (repo / "acme_good.avcdriver").write_bytes(_yaml(id="acme_good"))
    assert driver_loader.load_all_drivers([repo]) == 2
    listed = await _installed(repo)
    assert listed["acme_widget.avcdriver"]["load_error"] is None
    assert listed["acme_widget.avcdriver"]["load_notices"] == [ON_CONNECT]
    assert listed["acme_good.avcdriver"]["load_notices"] == []


async def test_a_notice_ends_when_its_file_changes(repo) -> None:
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_yaml(on_connect="P?\r"))
    driver_loader.load_all_drivers([repo])
    path.write_bytes(_yaml(on_connect=["P?\r"]))
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    assert (await _installed(repo))["acme_widget.avcdriver"]["load_notices"] == []


async def test_the_upload_loads_it_and_answers_with_the_notices(repo) -> None:
    answer = await routes.upload_driver(_form("acme_widget.avcdriver", _yaml(on_connect="P?\r")))
    assert answer["status"] == "uploaded" and registry.is_driver_registered(DRIVER_ID)
    assert answer["notices"] == [ON_CONNECT]
    assert (await _installed(repo))["acme_widget.avcdriver"]["load_notices"] == [ON_CONNECT]


REPLY_TARGET = (
    "Response 0: set 'powr' writes a state variable this driver does not "
    "declare (did you mean 'power'?); declare it under state_variables"
)


def _undeclared_target() -> bytes:
    return _yaml(
        state_variables={"power": {"type": "string", "label": "Power"}},
        responses=[{"match": r"PWR=(\w+)", "set": {"powr": "$1"}}],
    )


def test_the_loader_loads_a_reply_rule_writing_an_undeclared_name_and_names_it(repo) -> None:
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_undeclared_target())
    read = driver_loader.read_driver_file(path)
    assert read.definition is not None and read.reasons == ()
    assert read.notices == (REPLY_TARGET,)


async def test_the_upload_answers_with_a_reply_rule_s_undeclared_name(repo) -> None:
    answer = await routes.upload_driver(_form("acme_widget.avcdriver", _undeclared_target()))
    assert answer["status"] == "uploaded" and registry.is_driver_registered(DRIVER_ID)
    assert answer["notices"] == [REPLY_TARGET]
    assert (await _installed(repo))["acme_widget.avcdriver"]["load_notices"] == [REPLY_TARGET]


CHILD_CLAIM = (
    "Response 0: child_set[0]: state 'offline_reason' writes 'parent_offline', "
    "the code the platform writes while the device is unreachable; write "
    'not_fitted, not_responding or service_fault, or "" for none'
)


def _child_claiming_parent_offline() -> bytes:
    return _yaml(
        child_entity_types={
            "output": {"label": "Output", "instances": {"count": 2}, "state_variables": {}}
        },
        responses=[
            {
                "match": r"OUT(\d+) DOWN",
                "child_set": [
                    {
                        "type": "output",
                        "id": "$1",
                        "state": {"online": False, "offline_reason": "parent_offline"},
                    }
                ],
            }
        ],
    )


def test_the_loader_loads_a_child_claiming_parent_offline_and_names_it(repo) -> None:
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_child_claiming_parent_offline())
    read = driver_loader.read_driver_file(path)
    assert read.definition is not None and read.reasons == ()
    assert read.notices == (CHILD_CLAIM,)


async def test_the_upload_answers_with_a_child_s_fault_code(repo) -> None:
    answer = await routes.upload_driver(
        _form("acme_widget.avcdriver", _child_claiming_parent_offline())
    )
    assert answer["status"] == "uploaded" and registry.is_driver_registered(DRIVER_ID)
    assert answer["notices"] == [CHILD_CLAIM]
    assert (await _installed(repo))["acme_widget.avcdriver"]["load_notices"] == [CHILD_CLAIM]


PARITY_X = (
    "default_config.parity: a serial port can't use 'X' as its parity; write "
    "N (none), E (even), O (odd), M (mark) or S (space)"
)


def _serial(**default_config: Any) -> bytes:
    return _yaml(transport="serial", default_config={"port": "", **default_config})


def test_the_loader_loads_a_line_setting_with_no_reading_and_names_it(repo) -> None:
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(_serial(parity="X"))
    read = driver_loader.read_driver_file(path)
    assert read.definition is not None and read.reasons == ()
    assert read.notices == (PARITY_X,)


async def test_the_upload_answers_with_a_line_setting_with_no_reading(repo) -> None:
    answer = await routes.upload_driver(_form("acme_widget.avcdriver", _serial(parity="X")))
    assert answer["status"] == "uploaded" and registry.is_driver_registered(DRIVER_ID)
    assert answer["notices"] == [PARITY_X]


def test_line_settings_written_as_words_are_no_notice(repo) -> None:
    path = repo / "acme_widget.avcdriver"
    path.write_bytes(
        _serial(parity="Even", stopbits="1", baudrate="19200", flow_control="RTS/CTS")
    )
    read = driver_loader.read_driver_file(path)
    assert read.definition is not None and read.reasons == () and read.notices == ()


async def test_a_clean_upload_answers_with_no_notices(repo) -> None:
    answer = await routes.upload_driver(_form("acme_widget.avcdriver", _yaml()))
    assert answer["notices"] == []


async def test_the_bundle_upload_loads_it_and_answers_with_the_notices(repo) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("acme_widget.avcdriver", _yaml(version=1.0))
    answer = await routes.upload_driver_bundle(_form("acme_widget.zip", buf.getvalue()))
    assert answer["status"] == "uploaded" and registry.is_driver_registered(DRIVER_ID)
    assert answer["notices"] == [VERSION]
    assert (await _installed(repo))["acme_widget.avcdriver"]["load_notices"] == [VERSION]


async def test_a_project_bundle_s_driver_loads_with_its_notices_on_the_installed_entry(
    repo, monkeypatch
) -> None:
    monkeypatch.setattr(project_library, "_DRIVER_REPO_DIR", repo)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("drivers/acme_widget.avcdriver", _yaml(on_connect="P?\r"))
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as archive:
        assert project_library._install_bundled_drivers(archive) == [DRIVER_ID]
    assert (await _installed(repo))["acme_widget.avcdriver"]["load_notices"] == [ON_CONNECT]


# --- a Python driver: what the author's doors refuse, said where it loads ------------

PY_RELAY = "State variable 'relay': unknown type 'bool'; use 'boolean'"


def _py(state_type: str = "boolean") -> bytes:
    return (
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeWidget(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        f"        'id': '{DRIVER_ID}',\n"
        "        'name': 'Acme Widget',\n"
        "        'transport': 'tcp',\n"
        f"        'state_variables': {{'relay': {{'type': '{state_type}', 'label': 'Relay'}}}},\n"
        "    }\n"
    ).encode()


async def test_the_start_up_pass_loads_a_python_driver_and_lists_its_notices(repo) -> None:
    (repo / "acme_widget.py").write_bytes(_py("bool"))
    assert driver_loader.load_all_drivers([repo]) == 1
    entry = (await _installed(repo))["acme_widget.py"]
    assert entry["load_error"] is None
    assert entry["load_notices"] == [PY_RELAY]


async def test_a_clean_python_driver_has_no_notices(repo) -> None:
    (repo / "acme_widget.py").write_bytes(_py())
    driver_loader.load_all_drivers([repo])
    assert (await _installed(repo))["acme_widget.py"]["load_notices"] == []


async def test_a_python_upload_loads_it_and_answers_with_the_notices(repo) -> None:
    answer = await routes.upload_driver(_form("acme_widget.py", _py("bool")))
    assert answer["status"] == "uploaded" and registry.is_driver_registered(DRIVER_ID)
    assert answer["notices"] == [PY_RELAY]
    assert (await _installed(repo))["acme_widget.py"]["load_notices"] == [PY_RELAY]


async def test_a_python_reload_answers_with_the_notices_and_lists_them(repo) -> None:
    path = repo / "acme_widget.py"
    path.write_bytes(_py())
    driver_loader.load_all_drivers([repo])
    path.write_bytes(_py("bool"))
    result = driver_loader.reload_python_driver(path)
    assert result["status"] == "reloaded"
    assert result["notices"] == [PY_RELAY]
    assert (await _installed(repo))["acme_widget.py"]["load_notices"] == [PY_RELAY]
