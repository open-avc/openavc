"""``drivers/driver_change.admit``: a change goes in whole or not at all.

A failure is injected at each step (the check, the staged load, the swap, the
final import, the register), and each test asserts what the driver folder,
the registry and ``sys.modules`` hold afterwards: what they held before, with
no staging folder left behind.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest
import yaml

from openavc.drivers import driver_change, registry
from openavc.drivers.driver_change import (
    DriverChange,
    DriverRefused,
    Gates,
    admit,
    check_driver_file,
)
from openavc.drivers.driver_loader import load_python_driver_file

DRIVER_ID = "acme_widget"
MODULE = f"openavc_driver_{DRIVER_ID}"


@pytest.fixture
def folder(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    before = sys.modules.get(MODULE)
    yield repo
    if before is None:
        sys.modules.pop(MODULE, None)
    else:
        sys.modules[MODULE] = before


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
        "        'transport': 'tcp',\n"
        f"{extra}"
        "    }\n"
        f"    MARK = {mark!r}\n"
    ).encode()


def _tree(repo: Path) -> dict[str, bytes]:
    return {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def _no_staging(repo: Path) -> bool:
    return not list(repo.glob(".staging-*"))


def _installed_python(repo: Path) -> type:
    (repo / "acme_widget.py").write_bytes(_python("old"))
    cls = load_python_driver_file(repo / "acme_widget.py")
    registry.register_driver(cls)
    return cls


def _change(repo: Path, files: dict[str, bytes], **kwargs) -> DriverChange:
    main = next(name for name in files if not name.endswith(("_sim.py", "_discovery.py")))
    return DriverChange(repo, main, files, **kwargs)


# --- what goes in --------------------------------------------------------------


def test_a_yaml_driver_and_its_companion_go_in_and_register(folder):
    data = _yaml(discovery={"python": {"file": "./acme_widget_discovery.py"}})
    admitted = admit(_change(folder, {
        "acme_widget.avcdriver": data, "acme_widget_discovery.py": b"X = 1\n",
    }))
    assert registry.get_driver_class(DRIVER_ID) is admitted.driver_class
    assert _tree(folder) == {"acme_widget.avcdriver": data, "acme_widget_discovery.py": b"X = 1\n"}
    assert _no_staging(folder)


def test_a_yaml_driver_whose_companion_is_already_installed_goes_in(folder):
    (folder / "acme_widget_discovery.py").write_bytes(b"X = 1\n")
    data = _yaml(discovery={"python": {"file": "./acme_widget_discovery.py"}})
    admit(_change(folder, {"acme_widget.avcdriver": data}))
    assert registry.get_driver_class(DRIVER_ID) is not None


def test_a_python_driver_registers_from_its_final_place(folder):
    admitted = admit(_change(folder, {"acme_widget.py": _python("new")}))
    assert admitted.driver_class.__module__ == MODULE
    assert sys.modules[MODULE].__file__ == os.path.realpath(folder / "acme_widget.py")


def test_a_same_size_python_driver_written_in_the_same_second_is_the_new_code(folder):
    _installed_python(folder)
    admit(_change(folder, {"acme_widget.py": _python("new")}))
    assert registry.get_driver_class(DRIVER_ID).MARK == "new"


def test_the_files_a_change_removes_go_and_are_named(folder):
    (folder / "old_name.avcdriver").write_bytes(_yaml())
    admitted = admit(_change(
        folder, {"acme_widget.avcdriver": _yaml()}, removes=("old_name.avcdriver", "not_here.py"),
    ))
    assert admitted.removed == ["old_name.avcdriver"]
    assert set(_tree(folder)) == {"acme_widget.avcdriver"}


def test_a_leftover_staging_folder_is_swept(folder):
    leftover = folder / ".staging-crashed"
    leftover.mkdir()
    (leftover / "acme_widget_sim.py").write_bytes(b"STALE = 1\n")
    admit(_change(folder, {"acme_widget.avcdriver": _yaml()}))
    assert _no_staging(folder)


# --- refused before anything is written -------------------------------------------


def test_a_driver_above_this_platform_is_refused_before_staging(folder, monkeypatch):
    monkeypatch.setattr(driver_change, "_write_beside", _never)
    with pytest.raises(DriverRefused) as caught:
        admit(_change(folder, {"acme_widget.py": _python("new", min_platform_version="999.0.0")}))
    assert caught.value.status_code == 422
    assert caught.value.detail.startswith("This driver requires OpenAVC 999.0.0 or later.")


def _never(*_args, **_kwargs):
    raise AssertionError("staged")


def test_the_check_alone_reads_a_python_floor_without_running_the_file(folder):
    source = _python("new", min_platform_version="999.0.0") + b"raise SystemExit('ran')\n"
    with pytest.raises(DriverRefused):
        check_driver_file("acme_widget.py", source)


@pytest.mark.parametrize("name", ["../acme_widget.py", "sub/acme_widget.py", "acme widget.py", "a.txt"])
def test_a_name_that_is_not_a_driver_file_name_writes_nothing(folder, name):
    with pytest.raises(ValueError):
        admit(DriverChange(folder, "acme_widget.avcdriver", {
            "acme_widget.avcdriver": _yaml(), name: b"X = 1\n",
        }))
    assert _tree(folder) == {}
    assert _no_staging(folder)


# --- refused after staging: the folder, registry and module as they were -------------


def test_a_staged_yaml_the_loader_refuses_changes_nothing(folder):
    (folder / "acme_widget.avcdriver").write_bytes(_yaml())
    with pytest.raises(DriverRefused) as caught:
        admit(_change(folder, {"acme_widget.avcdriver": _yaml(transport="carrier_pigeon")}),
              Gates(not_a_definition="said the door"))
    assert (caught.value.status_code, caught.value.detail) == (422, "said the door")
    assert _tree(folder) == {"acme_widget.avcdriver": _yaml()}
    assert _no_staging(folder)


def test_a_gate_s_refusal_changes_nothing(folder):
    def _refuse(_definition):
        raise RuntimeError("the door says no")

    with pytest.raises(RuntimeError, match="the door says no"):
        admit(_change(folder, {"acme_widget.avcdriver": _yaml()}), Gates(definition=_refuse))
    assert _tree(folder) == {}
    assert registry.get_driver_class(DRIVER_ID) is None


def test_a_staged_python_file_is_tried_without_touching_the_running_module(folder):
    old = _installed_python(folder)
    module = sys.modules[MODULE]
    with pytest.raises(DriverRefused):
        admit(_change(folder, {"acme_widget.py": b"VALUE = 1\n"}))
    assert sys.modules[MODULE] is module
    assert registry.get_driver_class(DRIVER_ID) is old
    assert not [n for n in sys.modules if n.startswith("_openavc_driver_validate_")]


def test_a_failed_swap_puts_back_the_files_already_swapped(folder, monkeypatch):
    (folder / "acme_widget.avcdriver").write_bytes(b"old yaml\n")
    real = os.replace

    def _replace(src, dst):
        if str(dst).endswith("_discovery.py"):
            raise PermissionError("locked")
        real(src, dst)

    monkeypatch.setattr(driver_change.os, "replace", _replace)
    data = _yaml(discovery={"python": {"file": "./acme_widget_discovery.py"}})
    with pytest.raises(PermissionError):
        admit(_change(folder, {"acme_widget.avcdriver": data, "acme_widget_discovery.py": b"X\n"}))
    assert _tree(folder) == {"acme_widget.avcdriver": b"old yaml\n"}
    assert _no_staging(folder)


def test_a_failed_register_puts_back_every_file_and_removal(folder, monkeypatch):
    old = _installed_python(folder)
    module = sys.modules[MODULE]
    (folder / "acme_widget_sim.py").write_bytes(b"OLD_SIM = 1\n")
    (folder / "acme_widget_discovery.py").write_bytes(b"OLD_DISCOVERY = 1\n")
    before = _tree(folder)

    def _full(_cls):
        raise RuntimeError("registry full")

    monkeypatch.setattr(driver_change, "register_driver", _full)
    with pytest.raises(RuntimeError, match="registry full"):
        admit(_change(
            folder,
            {"acme_widget.py": _python("new"), "acme_widget_sim.py": b"NEW_SIM = 1\n"},
            removes=("acme_widget_discovery.py",),
        ))
    assert _tree(folder) == before
    assert registry.get_driver_class(DRIVER_ID) is old
    assert sys.modules[MODULE] is module


def test_a_final_import_that_fails_puts_back_the_old_file_and_module(folder, monkeypatch):
    old = _installed_python(folder)
    module = sys.modules[MODULE]
    before = _tree(folder)
    monkeypatch.setattr(driver_change, "load_python_driver_file", lambda path: None)
    with pytest.raises(DriverRefused):
        admit(_change(folder, {"acme_widget.py": _python("new")}))
    assert _tree(folder) == before
    assert registry.get_driver_class(DRIVER_ID) is old
    assert sys.modules[MODULE] is module


def test_a_replaced_link_is_put_back_as_the_same_link(folder, monkeypatch):
    outside = folder.parent / "outside_discovery.py"
    outside.write_bytes(b"MINE = 1\n")
    try:
        (folder / "acme_widget_discovery.py").symlink_to(outside)
    except OSError:
        pytest.skip("this system cannot make a symbolic link")
    monkeypatch.setattr(driver_change, "register_driver", _explode)
    data = _yaml(discovery={"python": {"file": "./acme_widget_discovery.py"}})
    with pytest.raises(RuntimeError):
        admit(_change(folder, {"acme_widget.avcdriver": data, "acme_widget_discovery.py": b"X\n"}))
    link = folder / "acme_widget_discovery.py"
    assert link.is_symlink()
    assert os.readlink(link) == str(outside)
    assert outside.read_bytes() == b"MINE = 1\n"


def _explode(_cls):
    raise RuntimeError("no")


# --- optional files ----------------------------------------------------------------


def test_an_optional_file_with_a_folder_in_its_place_is_left_out_with_a_warning(folder, caplog):
    (folder / "acme_widget_sim.py").mkdir()
    with caplog.at_level(logging.WARNING, logger="openavc.api.routes.drivers"):
        admit(_change(
            folder,
            {"acme_widget.py": _python("new"), "acme_widget_sim.py": b"SIM = 1\n"},
            optional=("acme_widget_sim.py",),
        ))
    assert registry.get_driver_class(DRIVER_ID).MARK == "new"
    assert (folder / "acme_widget_sim.py").is_dir()
    assert any(
        r.getMessage().startswith("Optional companion acme_widget_sim.py not installed: ")
        for r in caplog.records
    )


def test_a_required_file_with_a_folder_in_its_place_refuses_the_change(folder):
    (folder / "acme_widget.avcdriver").write_bytes(b"old yaml\n")
    (folder / "acme_widget_discovery.py").mkdir()
    data = _yaml(discovery={"python": {"file": "./acme_widget_discovery.py"}})
    with pytest.raises(OSError):
        admit(_change(folder, {"acme_widget.avcdriver": data, "acme_widget_discovery.py": b"X\n"}))
    assert _tree(folder) == {"acme_widget.avcdriver": b"old yaml\n"}
    assert (folder / "acme_widget_discovery.py").is_dir()
