"""A YAML driver saved under an id another driver already has.

The Driver Builder checks the id against the library itself and lists the
problem on the General tab; the save routes refuse it with that same sentence
in ``errors``, so the editor's header can point at the tab instead of saying
it a second time in other words. A rename onto a taken id is refused too: the
replace route used to save over the other driver and delete the renamed one.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from openavc.api.models import DriverDefinitionRequest
from openavc.api.routes import drivers as drivers_routes
from openavc.api.routes.drivers import create_driver_definition, update_driver_definition
from openavc.drivers.driver_loader import save_driver_definition

from fastapi import HTTPException

VALIDATE_DRIVER_TS = (
    Path(__file__).resolve().parent.parent
    / "openavc" / "web" / "programmer" / "src" / "components" / "driver-builder" / "validateDriver.ts"
)

WIDGET = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
          "commands": {"power_on": {"send": "PWR ON\\r"}}}
GADGET = {"id": "acme_gadget", "name": "Acme Gadget", "transport": "tcp",
          "commands": {"power_on": {"send": "GPWR 1\\r"}}}


@pytest.fixture()
def repo_dir(tmp_path: Path, monkeypatch) -> Path:
    builtin_dir = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin_dir.mkdir()
    repo.mkdir()
    monkeypatch.setattr(drivers_routes, "_get_driver_dirs", lambda: (builtin_dir, repo))

    async def _reload_driver(driver_id: str) -> int:
        return 0

    monkeypatch.setattr(
        drivers_routes,
        "_get_engine",
        lambda: SimpleNamespace(devices=SimpleNamespace(reload_driver=_reload_driver)),
    )
    return repo


def _builders_sentence(driver_id: str) -> str:
    """The duplicate-id message the Driver Builder shows on its General tab."""
    source = VALIDATE_DRIVER_TS.read_text(encoding="utf-8")
    match = re.search(r"`(Another driver named [^`]*)`", source)
    assert match, "validateDriver.ts no longer words the duplicate-id check"
    return match.group(1).replace("${draft.id}", driver_id)


def _refusal(exc: pytest.ExceptionInfo[HTTPException]) -> tuple[int, list[str] | None]:
    return exc.value.status_code, getattr(exc.value, "fields", {}).get("errors")


async def test_a_new_driver_with_a_taken_id_is_refused_in_the_builders_words(repo_dir):
    save_driver_definition(dict(WIDGET), repo_dir)

    with pytest.raises(HTTPException) as exc:
        await create_driver_definition(DriverDefinitionRequest(**{**GADGET, "id": "acme_widget"}))

    assert _refusal(exc) == (409, [_builders_sentence("acme_widget")])
    assert yaml.safe_load((repo_dir / "acme_widget.avcdriver").read_text())["name"] == "Acme Widget"


async def test_renaming_onto_another_drivers_id_is_refused_and_keeps_both(repo_dir):
    widget = save_driver_definition(dict(WIDGET), repo_dir)
    gadget = save_driver_definition(dict(GADGET), repo_dir)
    before = (widget.read_text(), gadget.read_text())

    with pytest.raises(HTTPException) as exc:
        await update_driver_definition(
            "acme_gadget", DriverDefinitionRequest(**{**GADGET, "id": "acme_widget"})
        )

    assert _refusal(exc) == (409, [_builders_sentence("acme_widget")])
    assert (widget.read_text(), gadget.read_text()) == before

