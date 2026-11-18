"""A driver id is held to the contract's pattern where it is first given.

The pattern (lowercase letters, digits and underscores) is refused at every
door an author gives a driver its id through: the checker in strict mode
(Validate, the Builder's save, ``drivers.check``, the AI's driver tool, catalog
CI), a rename, the Code view's New Driver, a community install. A driver
already installed under an id that breaks it (the Code view's New Driver let a
hyphen through, the save route turned any other character into ``_``) keeps
loading under that id: it opens, saves under its own id and uploads as before,
and its Installed entry says why a new driver could not use the id.

A save writes ``<id>.avcdriver`` as the id is written, and never over a file
that holds another driver.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

from openavc.api.models import (
    CommunityDriverInstallRequest,
    DriverDefinitionRequest,
    PythonDriverCreateRequest,
)
from openavc.api.routes import driver_definitions as definition_routes
from openavc.api.routes import drivers as driver_routes
from openavc.api.routes import python_drivers as python_routes
from openavc.api.routes.driver_definitions import (
    create_driver_definition,
    patch_driver_definition,
    update_driver_definition,
    validate_driver_definition_draft,
)
from openavc.drivers.check import check_driver_file
from openavc.drivers.driver_loader import read_driver_file, validate_driver_definition
from openavc.drivers.python_info import check_python_driver_info, python_driver_refusals
from openavc.drivers.registry import unregister_driver

ALLOWED = "lowercase letters, digits and underscores"

WIDGET = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
          "commands": {"power_on": {"send": "PWR ON\\r"}}}

PYTHON_SOURCE = '''\
from openavc.drivers.base import BaseDriver


class AcmeWidget(BaseDriver):
    DRIVER_INFO = {
        "id": "{id}",
        "name": "Acme Widget",
        "transport": "tcp",
    }

    async def send_command(self, command, params=None):
        return None
'''


def _python(driver_id: str) -> str:
    return PYTHON_SOURCE.replace("{id}", driver_id)


def _yaml(definition: dict) -> str:
    return yaml.safe_dump(definition, sort_keys=False)


def _id_refusals(messages: list[str]) -> list[str]:
    return [m for m in messages if m.startswith("id:") and ALLOWED in m]


@pytest.fixture()
def library(tmp_path: Path, monkeypatch):
    """One driver folder every door here writes to, and the ids registered
    in it taken out of the process-wide registry after."""
    import openavc.system_config as system_config

    builtin_dir = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin_dir.mkdir()
    repo.mkdir()
    monkeypatch.setattr(definition_routes, "_get_driver_dirs", lambda: (builtin_dir, repo))
    monkeypatch.setattr(driver_routes, "_get_driver_repo_dir", lambda: repo)
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", builtin_dir)

    async def _reload_driver(driver_id: str) -> int:
        return 0

    monkeypatch.setattr(
        "openavc.drivers.driver_change._running_engine",
        lambda: SimpleNamespace(devices=SimpleNamespace(reload_driver=_reload_driver)),
    )
    yield repo
    for driver_id in ("acme_widget", "acme.widget", "acme-widget", "Acme-Widget", "acme_gadget"):
        unregister_driver(driver_id)


# --- the checker ------------------------------------------------------------


@pytest.mark.parametrize("driver_id", ["acme.widget", "Acme-Widget", "acme widget", "acme_widget\n", ""])
def test_an_id_outside_the_pattern_is_refused_at_the_authors_doors(driver_id):
    errors = validate_driver_definition({**WIDGET, "id": driver_id}, strict=True)
    assert _id_refusals(errors), errors


def test_an_id_in_the_pattern_is_not_refused():
    assert validate_driver_definition(dict(WIDGET), strict=True) == []


def test_a_yaml_driver_with_such_an_id_still_loads_and_says_why(tmp_path):
    path = tmp_path / "acme-widget.avcdriver"
    path.write_text(_yaml({**WIDGET, "id": "acme-widget"}), encoding="utf-8")

    read = read_driver_file(path)

    assert read.reasons == ()
    assert read.declared_id == "acme-widget"
    notices = [n for n in read.notices if "acme-widget" in n]
    assert notices and "keeps working" in notices[0]


def test_a_python_driver_info_with_such_an_id_is_refused_where_written_and_noted_where_loaded():
    info = {"id": "acme-widget", "name": "Acme Widget", "transport": "tcp"}

    assert _id_refusals(python_driver_refusals(info))
    loaded = check_python_driver_info(info)
    assert not _id_refusals(loaded.errors)
    assert [n for n in loaded.notices if "keeps working" in n]


def test_a_computed_python_id_is_not_judged():
    from openavc.drivers.python_info import UNEVALUATED

    info = {"id": UNEVALUATED, "name": "Acme Widget", "transport": "tcp"}
    assert not [m for m in python_driver_refusals(info) if m.startswith("id:")]


def test_drivers_check_refuses_such_an_id(tmp_path):
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(_yaml({**WIDGET, "id": "acme.widget"}), encoding="utf-8")

    assert _id_refusals(check_driver_file(path).errors)


# --- the Driver Builder's doors ---------------------------------------------


def _refusal(exc: pytest.ExceptionInfo[HTTPException]) -> tuple[int, list[str]]:
    return exc.value.status_code, getattr(exc.value, "fields", {}).get("errors") or [str(exc.value.detail)]


async def test_a_new_id_with_a_dot_is_refused_and_never_saved_over_another_driver(library):
    await create_driver_definition(DriverDefinitionRequest(**WIDGET))

    with pytest.raises(HTTPException) as exc:
        await create_driver_definition(
            DriverDefinitionRequest(**{**WIDGET, "id": "acme.widget", "name": "Dotted"})
        )

    status, errors = _refusal(exc)
    assert status == 422 and _id_refusals(errors), errors
    assert sorted(p.name for p in library.iterdir()) == ["acme_widget.avcdriver"]
    assert yaml.safe_load((library / "acme_widget.avcdriver").read_text())["id"] == "acme_widget"


async def test_a_save_never_writes_over_a_file_that_holds_another_driver(library):
    # Left by the save before it named files by the id as written: the file
    # acme_widget.avcdriver holds the driver acme.widget.
    held = library / "acme_widget.avcdriver"
    held.write_text(_yaml({**WIDGET, "id": "acme.widget", "name": "Dotted"}), encoding="utf-8")
    before = held.read_text()

    with pytest.raises(HTTPException) as exc:
        await create_driver_definition(DriverDefinitionRequest(**WIDGET))

    status, errors = _refusal(exc)
    assert status == 409
    assert "acme.widget" in errors[0] and "acme_widget.avcdriver" in errors[0]
    assert held.read_text() == before


async def test_a_driver_installed_under_such_an_id_saves_under_it(library):
    path = library / "acme-widget.avcdriver"
    path.write_text(_yaml({**WIDGET, "id": "acme-widget"}), encoding="utf-8")

    replaced = await update_driver_definition(
        "acme-widget", DriverDefinitionRequest(**{**WIDGET, "id": "acme-widget", "name": "Renamed"})
    )
    patched = await patch_driver_definition("acme-widget", {"name": "Patched"})

    assert (replaced["status"], patched["status"]) == ("updated", "updated")
    assert sorted(p.name for p in library.iterdir()) == ["acme-widget.avcdriver"]
    assert yaml.safe_load(path.read_text())["name"] == "Patched"


async def test_a_driver_the_old_save_renamed_saves_into_the_file_it_is_in(library):
    # The save used to turn the dot into an underscore for the file name.
    path = library / "acme_widget.avcdriver"
    path.write_text(_yaml({**WIDGET, "id": "acme.widget"}), encoding="utf-8")

    await update_driver_definition(
        "acme.widget", DriverDefinitionRequest(**{**WIDGET, "id": "acme.widget", "name": "Kept"})
    )

    assert sorted(p.name for p in library.iterdir()) == ["acme_widget.avcdriver"]
    assert yaml.safe_load(path.read_text()) == {**WIDGET, "id": "acme.widget", "name": "Kept"}


async def test_renaming_to_an_id_outside_the_pattern_is_refused(library):
    await create_driver_definition(DriverDefinitionRequest(**WIDGET))

    with pytest.raises(HTTPException) as exc:
        await update_driver_definition(
            "acme_widget", DriverDefinitionRequest(**{**WIDGET, "id": "acme-gadget"})
        )

    status, errors = _refusal(exc)
    assert status == 422 and _id_refusals(errors), errors
    assert sorted(p.name for p in library.iterdir()) == ["acme_widget.avcdriver"]


async def test_validate_refuses_a_new_id_and_not_an_installed_one(library):
    (library / "acme-widget.avcdriver").write_text(
        _yaml({**WIDGET, "id": "acme-widget"}), encoding="utf-8"
    )

    installed = await validate_driver_definition_draft({**WIDGET, "id": "acme-widget"})
    new = await validate_driver_definition_draft({**WIDGET, "id": "acme-gadget"})

    assert not [i for i in installed["issues"] if i["severity"] == "error"], installed
    assert [i for i in new["issues"] if i["severity"] == "error" and ALLOWED in i["message"]]


async def test_the_ais_create_is_refused_naming_the_characters(library):
    from unittest.mock import MagicMock

    from openavc.cloud.ai_tool_handler import AIToolHandler

    answer = await AIToolHandler(MagicMock(), MagicMock(), MagicMock())._create_driver_definition(
        {"definition": {**WIDGET, "id": "acme.widget"}}
    )

    assert ALLOWED in answer["error"]
    assert list(library.iterdir()) == []


# --- the Code view ----------------------------------------------------------


async def test_the_code_views_new_driver_refuses_such_an_id(library):
    with pytest.raises(HTTPException) as exc:
        await python_routes.create_python_driver(
            PythonDriverCreateRequest(id="acme-widget", source=_python("acme-widget"))
        )

    assert exc.value.status_code == 400 and ALLOWED in str(exc.value.detail)
    assert list(library.iterdir()) == []


async def test_a_python_driver_installed_under_such_an_id_opens_and_saves(library):
    path = library / "acme-widget.py"
    path.write_text(_python("acme-widget"), encoding="utf-8")

    read = await python_routes.get_python_driver_source("acme-widget")
    saved = await python_routes.save_python_driver_source(
        "acme-widget",
        {"source": read["source"].replace("Acme Widget", "Acme Widget 2"), "require_valid_syntax": True},
    )

    assert saved["status"] == "saved", saved
    assert "Acme Widget 2" in path.read_text(encoding="utf-8")


async def test_a_code_view_save_giving_a_new_id_outside_the_pattern_names_it(library):
    path = library / "acme_widget.py"
    path.write_text(_python("acme_widget"), encoding="utf-8")

    saved = await python_routes.save_python_driver_source(
        "acme_widget", {"source": _python("acme-gadget"), "require_valid_syntax": True}
    )

    assert saved["status"] == "error" and _id_refusals(saved["issues"]), saved


def test_a_driver_file_name_is_judged_whole():
    from openavc.drivers.driver_file_name import is_driver_file_name

    assert is_driver_file_name("acme-widget.py")
    assert not is_driver_file_name("acme_widget.py\n")
    with pytest.raises(HTTPException):
        python_routes._check_driver_id("acme_widget\n")


# --- the integrator's doors -------------------------------------------------


class _Upload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _form(filename: str, data: bytes):
    async def _read_form():
        return {"file": _Upload(filename, data)}

    return SimpleNamespace(form=_read_form)


async def test_an_upload_of_a_driver_under_such_an_id_still_loads_and_says_why(library):
    data = _yaml({**WIDGET, "id": "Acme-Widget"}).encode()

    answer = await driver_routes.upload_driver(_form("Acme-Widget.avcdriver", data))

    assert (answer["status"], answer["driver_id"]) == ("uploaded", "Acme-Widget")
    assert [n for n in answer["notices"] if "Acme-Widget" in n and "keeps working" in n]


async def test_a_community_install_of_an_id_outside_the_pattern_fetches_nothing(library, monkeypatch):
    async def _no_catalog(*args, **kwargs):
        raise AssertionError("the catalog was asked")

    monkeypatch.setattr("openavc.api.driver_catalog._catalog_hashes", _no_catalog)

    with pytest.raises(HTTPException) as exc:
        await driver_routes.install_community_driver(
            CommunityDriverInstallRequest(
                driver_id="acme.widget",
                file_url="https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/acme_widget.avcdriver",
            )
        )

    assert exc.value.status_code == 422 and ALLOWED in str(exc.value.detail)
    assert list(library.iterdir()) == []
