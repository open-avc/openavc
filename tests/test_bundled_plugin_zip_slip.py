"""Regression test for zip-slip in bundled-plugin extraction (audit C3).

`_install_bundled_plugins` extracted each member of a bundle's ``plugins/``
tree by joining the plugin id (the path component after ``plugins/``) and the
per-file relative path straight onto ``plugin_repo`` — no containment check.
A crafted bundle member like ``plugins/foo/../../evil.py`` (or a plugin id of
``..``) therefore wrote files outside ``plugin_repo``. Plugin ``.py`` files are
imported on load, so an escape is arbitrary code execution.

This test feeds a crafted zip to ``_install_bundled_plugins`` and asserts the
traversal members are skipped (nothing written outside the repo) while a
legitimate plugin still installs.

The flat siblings, an import's bundled drivers and scripts, take the last
part of each member's path as the file name. That alone does not keep a file
in its folder on Windows: the last part of ``drivers/D:x.py`` is ``D:x.py``,
a path on drive D:. Both ask ``utils.paths.plain_file_in``, which refuses a
name with a colon on every platform, so the tests below fail on any OS
without it.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from unittest.mock import patch

import pytest

from openavc.core import project_library
from openavc.utils.paths import plain_file_in


def _make_zip(members: dict[str, bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


def test_install_bundled_plugins_blocks_zip_slip(tmp_path, monkeypatch):
    plugin_repo = tmp_path / "plugin_repo"
    plugin_repo.mkdir()
    monkeypatch.setattr(project_library, "_PLUGIN_REPO_DIR", plugin_repo)

    zf = _make_zip(
        {
            # Malicious: per-file relative path escapes the plugin dir.
            "plugins/goodname/../../escape_rel.py": b"RCE = 1",
            # Malicious: the plugin id itself is a traversal component.
            "plugins/../escape_id.py": b"RCE = 1",
            # Legitimate plugin (control) — including a nested file.
            "plugins/realplugin/__init__.py": b"OK = 1",
            "plugins/realplugin/sub/helper.py": b"HELPER = 1",
        }
    )

    installed, _unloadable = project_library._install_bundled_plugins(zf)

    # Legit plugin installed inside the repo, nested file preserved.
    assert (plugin_repo / "realplugin" / "__init__.py").read_bytes() == b"OK = 1"
    assert (plugin_repo / "realplugin" / "sub" / "helper.py").read_bytes() == b"HELPER = 1"
    assert "realplugin" in installed

    # Nothing escaped the plugin repo (the documented escape targets).
    assert not (tmp_path / "escape_rel.py").exists()
    assert not (tmp_path / "escape_id.py").exists()

    # Belt-and-suspenders: no "escape*" file landed anywhere under tmp_path
    # outside the repo, and none above it.
    strays = [p for p in tmp_path.rglob("escape*.py")]
    assert strays == [], f"files escaped containment: {strays}"


# A colon names a drive (``D:x.py``) or an alternate data stream (``x.py:s``)
# on Windows. Both members below would leave the folder there, and neither is
# a file name a real bundle carries, so they are refused on every platform.
OFF_THE_DRIVE = ("D:escape_sim.py", "x_sim.py:stream")


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "a/b.py", "a\\b.py", "D:x.py", "C:", "x.py:stream", "x\x00.py"],
)
def test_plain_file_in_refuses_a_name_that_is_not_one_file(tmp_path, name):
    assert plain_file_in(tmp_path, name) is None


@pytest.mark.parametrize("name", ["acme_widget.py", "acme widget.py", "acme..widget.avcdriver"])
def test_plain_file_in_keeps_a_plain_name_in_its_folder(tmp_path, name):
    assert plain_file_in(tmp_path, name) == (tmp_path / name).resolve()


def test_install_bundled_drivers_skips_a_member_named_for_another_drive(
    tmp_path, monkeypatch, caplog
):
    driver_repo = tmp_path / "driver_repo"
    monkeypatch.setattr(project_library, "_DRIVER_REPO_DIR", driver_repo)
    members = {f"drivers/{name}": b"ESCAPE = 1" for name in OFF_THE_DRIVE}
    members["drivers/acme_widget_sim.py"] = b"OK = 1"  # a companion: written, never imported
    zf = _make_zip(members)

    with caplog.at_level(logging.WARNING, logger=project_library.log.name):
        project_library._install_bundled_drivers(zf)

    assert (driver_repo / "acme_widget_sim.py").read_bytes() == b"OK = 1"
    assert sorted(p.name for p in driver_repo.iterdir()) == ["acme_widget_sim.py"]
    for name in OFF_THE_DRIVE:
        assert repr(f"drivers/{name}") in caplog.text


def test_project_import_skips_a_script_named_for_another_drive(tmp_path, monkeypatch):
    from openavc.core.project_library import create_blank_project

    monkeypatch.setattr(project_library, "_DRIVER_REPO_DIR", tmp_path / "driver_repo")
    monkeypatch.setattr(project_library, "_PLUGIN_REPO_DIR", tmp_path / "plugin_repo")

    lib = tmp_path / "saved_projects"
    lib.mkdir()
    avc = json.dumps(create_blank_project("drive_room", "drive_room").model_dump(mode="json"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("project.avc", avc)
        zf.writestr("scripts/main.py", b"OK = 1")
        for name in ("D:escape.py", "x.py:escape.py"):
            zf.writestr(f"scripts/{name}", b"ESCAPE = 1")

    with patch("openavc.core.project_library.config") as mock_config:
        mock_config.SAVED_PROJECTS_DIR = lib
        project_library.import_project(buf.getvalue(), "drive_room.zip")

    scripts = lib / "drive_room" / "scripts"
    assert sorted(p.name for p in scripts.iterdir()) == ["main.py"]


def test_a_driver_is_read_from_a_probe_whose_name_is_never_the_arrival_name(monkeypatch):
    """A driver's declared id is read, before anything is installed, from a
    copy in a temporary folder. The copy is named ``driver.py`` whatever the
    file arrived as: an upload or a bundle member named ``D:x.py`` would
    otherwise write that copy to drive D: on Windows."""
    from openavc.drivers import python_info
    from openavc.drivers.driver_loader import declared_driver_info

    read_from = []
    real = python_info.extract_python_driver_info_full

    def recording(path):
        read_from.append(path)
        return real(path)

    monkeypatch.setattr(python_info, "extract_python_driver_info_full", recording)
    source = (
        b"from openavc.drivers.base import BaseDriver\n\n\n"
        b"class AcmeWidget(BaseDriver):\n"
        b"    DRIVER_INFO = {'id': 'acme_widget', 'name': 'Acme Widget'}\n"
    )
    info = declared_driver_info("D:escape.py", source)

    assert info.get("id") == "acme_widget"
    assert [p.name for p in read_from] == ["driver.py"]
