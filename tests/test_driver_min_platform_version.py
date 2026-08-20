"""``min_platform_version`` is enforced at every door that installs a driver.

Two layers, and the second is the one that took a while to arrive:

* **The rule itself** — the semver comparison, the YAML peek, and what an
  unparseable version does. A65 added it so that
  ``/api/discovery/install-and-match`` and any other caller that doesn't carry
  the field on the request still gets the check parsed out of the YAML body.
* **The doors** — the catalog install honoured the floor while *hand import*
  (upload, upload a bundle) and the Driver Builder's save did not, so the same
  file installed or refused depending on which way it came in, and the
  permissive doors were the ones with no catalog entry, no version column and
  nothing on screen to say what the file needed. The gate protects systems
  that have not updated; a hand-import bypassed it on exactly those.

The door tests below assert the refusal *and* that nothing was written: a
refusal that had already overwritten the driver on disk would be worse than
no gate at all.
"""

import io
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from fastapi import HTTPException

from openavc.api.routes import drivers as drivers_routes
from openavc.api.models import DriverDefinitionRequest
from openavc.api.routes.drivers import (
    _declared_min_platform_version,
    _enforce_min_platform_version,
    _parse_semver,
    _peek_min_platform_version,
    create_driver_definition,
    patch_driver_definition,
    update_driver_definition,
    upload_driver,
    upload_driver_bundle,
)
from openavc.drivers.driver_loader import save_driver_definition


def test_peek_yaml_min_platform_version():
    yaml_text = """
id: foo
name: Foo
transport: tcp
min_platform_version: "0.6.0"
"""
    assert _peek_min_platform_version(yaml_text) == "0.6.0"


def test_peek_returns_none_when_absent():
    yaml_text = """
id: foo
name: Foo
transport: tcp
"""
    assert _peek_min_platform_version(yaml_text) is None


def test_peek_handles_malformed_yaml():
    assert _peek_min_platform_version("::: not yaml :::") is None


def test_peek_handles_non_string():
    yaml_text = """
id: foo
min_platform_version: 5
"""
    assert _peek_min_platform_version(yaml_text) is None


def test_enforce_blocks_when_running_is_older(monkeypatch):
    # Pretend we're running 0.5.0 and the driver demands 0.6.0.
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.5.0")
    with pytest.raises(HTTPException) as excinfo:
        _enforce_min_platform_version("0.6.0")
    assert excinfo.value.status_code == 422
    assert "0.6.0" in str(excinfo.value.detail)


def test_enforce_passes_when_running_is_equal(monkeypatch):
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.6.0")
    # Should not raise.
    _enforce_min_platform_version("0.6.0")


def test_enforce_passes_when_running_is_newer(monkeypatch):
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.7.1")
    _enforce_min_platform_version("0.6.0")


def test_enforce_swallows_unparseable(monkeypatch):
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.7.1")
    # An unparseable required version logs and allows.
    _enforce_min_platform_version("not-a-version")


def test_parse_semver_pads_short_versions():
    # "0.22" must equal "0.22.0" — a short tuple compares less-than and
    # used to falsely block installs at the gate.
    assert _parse_semver("0.22") == (0, 22, 0)
    assert _parse_semver("0.22") == _parse_semver("0.22.0")
    assert _parse_semver("1") == (1, 0, 0)


def test_parse_semver_keeps_numeric_prefix_of_suffixed_parts():
    # A pre-release/build suffix used to make the whole part vanish from
    # the tuple ("0.22.0-rc1" -> (0, 22)), shortening the comparison.
    assert _parse_semver("0.22.0-rc1") == (0, 22, 0)
    assert _parse_semver("1.2.3+build7") == (1, 2, 3)


def test_parse_semver_plain_three_part():
    assert _parse_semver("1.2.3") == (1, 2, 3)
    assert _parse_semver("1.2.3") < _parse_semver("1.2.10")


def test_enforce_two_part_running_version_not_blocked(monkeypatch):
    import openavc.version
    monkeypatch.setattr(openavc.version, "__version__", "0.22")
    # Running "0.22" satisfies a "0.22.0" requirement.
    _enforce_min_platform_version("0.22.0")


# --- what a file declares, read without running it -------------------------

ACME_YAML = """
id: acme_widget
name: Acme Widget
transport: tcp
min_platform_version: "0.29.0"
commands:
  power_on:
    send: "PWR ON\\r"
"""

# Module-level import of something that cannot resolve: if the floor were read
# by loading the driver rather than by reading it, this file would raise an
# import error instead of the version refusal — which is the whole point of
# reading it first.
ACME_PY = """import openavc_no_such_dependency  # noqa: F401

from openavc.drivers.base import BaseDriver


class AcmeWidgetDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "min_platform_version": "0.29.0",
    }
"""


def test_declared_floor_read_from_yaml_bytes():
    assert _declared_min_platform_version("acme_widget.avcdriver", ACME_YAML.encode()) == "0.29.0"


def test_declared_floor_read_from_python_driver_info():
    # Read out of DRIVER_INFO by the shared AST reader — the module is never
    # imported, which is why its unresolvable import is irrelevant here.
    assert _declared_min_platform_version("acme_widget.py", ACME_PY.encode()) == "0.29.0"


def test_declared_floor_absent_is_none():
    source = ACME_PY.replace('        "min_platform_version": "0.29.0",\n', "")
    assert _declared_min_platform_version("acme_widget.py", source.encode()) is None
    assert _declared_min_platform_version("acme_widget.avcdriver", b"id: acme_widget\n") is None


def test_declared_floor_unreadable_is_none_not_an_error():
    # A file that can't be read for a floor is a file that is about to fail to
    # load anyway, and that message is the useful one.
    assert _declared_min_platform_version("acme_widget.py", b"def broken(:\n") is None
    assert _declared_min_platform_version("acme_widget.avcdriver", b"::: not yaml :::") is None
    assert _declared_min_platform_version("acme_widget.py", b"\xff\xfe\x00bad") is None


# --- the hand-import doors --------------------------------------------------


@pytest.fixture()
def driver_repo(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(drivers_routes, "_get_driver_repo_dir", lambda: repo)
    monkeypatch.setattr(drivers_routes, "register_driver", lambda cls: None)
    fake_engine = MagicMock()
    fake_engine.devices.retry_all_orphans = AsyncMock(return_value=[])
    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(
        "openavc.api.discovery.refresh_all_device_matches",
        AsyncMock(return_value=None),
        raising=False,
    )
    return repo


@pytest.fixture()
def running_0_28(monkeypatch):
    import openavc.version

    monkeypatch.setattr(openavc.version, "__version__", "0.28.0")


class _FakeUpload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _request_with_file(filename: str, data: bytes) -> MagicMock:
    req = MagicMock()
    req.form = AsyncMock(return_value={"file": _FakeUpload(filename, data)})
    return req


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


async def test_upload_refuses_a_yaml_driver_this_platform_is_too_old_for(
    driver_repo, running_0_28
):
    with pytest.raises(HTTPException) as exc:
        await upload_driver(_request_with_file("acme_widget.avcdriver", ACME_YAML.encode()))
    assert exc.value.status_code == 422
    # Both versions and the way out, in the catalog door's words.
    assert "0.29.0" in exc.value.detail
    assert "0.28.0" in exc.value.detail
    assert "update OpenAVC" in exc.value.detail
    assert list(driver_repo.iterdir()) == []


async def test_upload_refuses_a_python_driver_this_platform_is_too_old_for(
    driver_repo, running_0_28
):
    with pytest.raises(HTTPException) as exc:
        await upload_driver(_request_with_file("acme_widget.py", ACME_PY.encode()))
    assert exc.value.status_code == 422
    assert "0.29.0" in exc.value.detail
    # Not "No valid driver class found" — the floor is read from the source,
    # so the driver's own unresolvable import never runs.
    assert "requires OpenAVC" in exc.value.detail
    assert list(driver_repo.iterdir()) == []


async def test_upload_refusal_leaves_the_installed_copy_alone(driver_repo, running_0_28):
    # Re-importing a newer driver over one already installed must not cost the
    # working copy when the newer one is refused.
    installed = driver_repo / "acme_widget.avcdriver"
    installed.write_text("id: acme_widget\nname: Installed\ntransport: tcp\n")

    with pytest.raises(HTTPException):
        await upload_driver(_request_with_file("acme_widget.avcdriver", ACME_YAML.encode()))

    assert yaml.safe_load(installed.read_text())["name"] == "Installed"


async def test_upload_allows_a_driver_this_platform_satisfies(driver_repo, monkeypatch):
    import openavc.version

    monkeypatch.setattr(openavc.version, "__version__", "0.29.0")
    result = await upload_driver(
        _request_with_file("acme_widget.avcdriver", ACME_YAML.encode())
    )
    assert result["status"] == "uploaded"
    assert (driver_repo / "acme_widget.avcdriver").is_file()


async def test_bundle_refuses_when_its_main_driver_needs_a_newer_platform(
    driver_repo, running_0_28
):
    bundle = _zip(
        {
            "acme_widget.py": ACME_PY.encode(),
            "acme_widget_sim.py": b"# simulator\n",
        }
    )
    with pytest.raises(HTTPException) as exc:
        await upload_driver_bundle(_request_with_file("acme_widget.zip", bundle))
    assert exc.value.status_code == 422
    assert "0.29.0" in exc.value.detail
    # Refused before the write loop, so not even the companion landed.
    assert list(driver_repo.iterdir()) == []


# --- the Driver Builder's save doors ---------------------------------------


DEFINITION = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "commands": {"power_on": {"send": "PWR ON\\r"}},
}


@pytest.fixture()
def driver_dirs(tmp_path, monkeypatch):
    builtin_dir = tmp_path / "definitions"
    repo_dir = tmp_path / "driver_repo"
    builtin_dir.mkdir()
    repo_dir.mkdir()
    monkeypatch.setattr(drivers_routes, "_get_driver_dirs", lambda: (builtin_dir, repo_dir))
    monkeypatch.setattr(drivers_routes, "register_driver", lambda cls: None)

    async def _reload_driver(driver_id: str) -> int:
        return 0

    fake_engine = MagicMock()
    fake_engine.devices.reload_driver = _reload_driver
    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: fake_engine)
    return builtin_dir, repo_dir


async def test_create_refuses_a_definition_that_needs_a_newer_platform(
    driver_dirs, running_0_28
):
    _, repo_dir = driver_dirs
    body = DriverDefinitionRequest(**{**DEFINITION, "min_platform_version": "0.29.0"})
    with pytest.raises(HTTPException) as exc:
        await create_driver_definition(body)
    assert exc.value.status_code == 422
    assert "0.29.0" in exc.value.detail
    assert list(repo_dir.iterdir()) == []


async def test_create_allows_a_floor_this_platform_meets(driver_dirs, running_0_28):
    _, repo_dir = driver_dirs
    body = DriverDefinitionRequest(**{**DEFINITION, "min_platform_version": "0.28.0"})
    result = await create_driver_definition(body)
    assert result["status"] == "created"
    assert (repo_dir / "acme_widget.avcdriver").is_file()


async def test_replace_refuses_raising_a_floor_past_this_platform(
    driver_dirs, running_0_28
):
    _, repo_dir = driver_dirs
    save_driver_definition(dict(DEFINITION), repo_dir)
    body = DriverDefinitionRequest(**{**DEFINITION, "min_platform_version": "0.29.0"})
    with pytest.raises(HTTPException) as exc:
        await update_driver_definition("acme_widget", body)
    assert exc.value.status_code == 422
    # Refused before the delete-and-rewrite, so the saved driver is intact.
    saved = yaml.safe_load((repo_dir / "acme_widget.avcdriver").read_text())
    assert saved.get("min_platform_version") is None


async def test_patch_refuses_raising_a_floor_past_this_platform(
    driver_dirs, running_0_28
):
    _, repo_dir = driver_dirs
    save_driver_definition(dict(DEFINITION), repo_dir)
    with pytest.raises(HTTPException) as exc:
        await patch_driver_definition("acme_widget", {"min_platform_version": "0.29.0"})
    assert exc.value.status_code == 422
    saved = yaml.safe_load((repo_dir / "acme_widget.avcdriver").read_text())
    assert saved.get("min_platform_version") is None


async def test_patch_can_lower_a_floor_that_blocks_the_save(driver_dirs, running_0_28):
    # The way out has to stay open: a driver already on disk declaring a floor
    # above this platform (installed before a rollback, say) is still editable,
    # because the gate reads the merged result rather than the stored copy.
    _, repo_dir = driver_dirs
    save_driver_definition({**DEFINITION, "min_platform_version": "0.29.0"}, repo_dir)

    with pytest.raises(HTTPException):
        await patch_driver_definition("acme_widget", {"name": "Acme Widget II"})

    result = await patch_driver_definition("acme_widget", {"min_platform_version": "0.22.0"})
    assert result["status"] == "updated"
    saved = yaml.safe_load((repo_dir / "acme_widget.avcdriver").read_text())
    assert saved["min_platform_version"] == "0.22.0"
