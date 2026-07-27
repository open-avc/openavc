"""Regression tests for PATCH /api/driver-definitions/{id} (routes/drivers.py).

The handler merged the body over the current definition with a SHALLOW
top-level spread, so a partial update of one nested entry (one command, one
state variable) silently replaced the entire block and persisted the
truncated driver to driver_repo — permanent, validation-passing data loss
that broke every device bound to the dropped entries. PATCH now applies
JSON Merge Patch semantics (RFC 7386): objects merge recursively, null
deletes a key, arrays and scalars replace.

Also covered: the saved YAML must not absorb the listing's internal
metadata (``_source_file``) the old spread carried through, and built-in
definitions stay read-only (403) rather than being deleted and replaced
with a user copy.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from server.api.routes import drivers as drivers_routes
from server.api.models import DriverDefinitionRequest
from server.api.routes.drivers import (
    _merge_patch,
    create_driver_definition,
    patch_driver_definition,
    update_driver_definition,
)
from server.drivers.driver_loader import save_driver_definition

from fastapi import HTTPException


# --- _merge_patch semantics -------------------------------------------------

def test_nested_patch_preserves_sibling_entries():
    current = {
        "commands": {
            "power_on": {"send": "PWR ON"},
            "power_off": {"send": "PWR OFF"},
        },
        "state_variables": {"power": {"type": "boolean"}},
    }
    patch = {"commands": {"power_on": {"send": "PWR 1"}}}
    merged = _merge_patch(current, patch)
    assert merged["commands"]["power_on"] == {"send": "PWR 1"}
    # The old shallow spread dropped these two.
    assert merged["commands"]["power_off"] == {"send": "PWR OFF"}
    assert merged["state_variables"] == {"power": {"type": "boolean"}}


def test_null_deletes_a_key():
    current = {"commands": {"a": {"send": "A"}, "b": {"send": "B"}}}
    merged = _merge_patch(current, {"commands": {"b": None}})
    assert merged == {"commands": {"a": {"send": "A"}}}


def test_arrays_and_scalars_replace_wholesale():
    current = {"name": "Old", "tags": ["x", "y"]}
    merged = _merge_patch(current, {"name": "New", "tags": ["z"]})
    assert merged == {"name": "New", "tags": ["z"]}


def test_patch_into_missing_key_creates_it_and_strips_nulls():
    merged = _merge_patch({}, {"commands": {"a": {"send": "A"}, "b": None}})
    assert merged == {"commands": {"a": {"send": "A"}}}


# --- endpoint behavior -------------------------------------------------------

DEFINITION = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "commands": {
        "power_on": {"send": "PWR ON\\r"},
        "power_off": {"send": "PWR OFF\\r"},
        "set_input": {"send": "IN {input}\\r"},
    },
}


@pytest.fixture()
def driver_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    builtin_dir = tmp_path / "definitions"
    repo_dir = tmp_path / "driver_repo"
    builtin_dir.mkdir()
    repo_dir.mkdir()
    monkeypatch.setattr(
        drivers_routes, "_get_driver_dirs", lambda: (builtin_dir, repo_dir)
    )

    async def _reload_driver(driver_id: str) -> int:
        return 0

    monkeypatch.setattr(
        drivers_routes,
        "_get_engine",
        lambda: SimpleNamespace(devices=SimpleNamespace(reload_driver=_reload_driver)),
    )
    return builtin_dir, repo_dir


async def test_patch_one_command_keeps_the_others(driver_dirs):
    _, repo_dir = driver_dirs
    save_driver_definition(dict(DEFINITION), repo_dir)

    result = await patch_driver_definition(
        "acme_widget", {"commands": {"power_on": {"send": "PWR 1\\r"}}}
    )
    assert result["status"] == "updated"

    saved = yaml.safe_load((repo_dir / "acme_widget.avcdriver").read_text())
    assert saved["commands"]["power_on"] == {"send": "PWR 1\\r"}
    assert saved["commands"]["power_off"] == {"send": "PWR OFF\\r"}
    assert saved["commands"]["set_input"] == {"send": "IN {input}\\r"}


async def test_patch_does_not_persist_listing_metadata(driver_dirs):
    _, repo_dir = driver_dirs
    save_driver_definition(dict(DEFINITION), repo_dir)

    await patch_driver_definition("acme_widget", {"name": "Acme Widget II"})

    text = (repo_dir / "acme_widget.avcdriver").read_text()
    assert "_source_file" not in text
    saved = yaml.safe_load(text)
    assert saved["name"] == "Acme Widget II"


async def test_patch_builtin_is_rejected_and_untouched(driver_dirs, monkeypatch):
    builtin_dir, _ = driver_dirs
    path = save_driver_definition(dict(DEFINITION), builtin_dir)
    before = path.read_text()

    # Built-ins are recognized by living under the real bundled definitions
    # tree; treat this test's definitions dir as that tree.
    from server.drivers import driver_loader

    monkeypatch.setattr(
        driver_loader,
        "is_builtin_definition_path",
        lambda p: Path(p).parent == builtin_dir,
    )

    with pytest.raises(HTTPException) as exc:
        await patch_driver_definition("acme_widget", {"name": "Hacked"})
    assert exc.value.status_code == 403
    assert path.read_text() == before


# --- the listing's own bookkeeping must survive a round trip -----------------
#
# GET /driver-definitions stamps `source` ("builtin"/"user") onto every
# definition it returns, and the Driver Builder edits what that endpoint gave
# it. Saving therefore hands `source` back. The authoring gate is strict about
# undeclared keys, so before the strip the server answered its own decoration
# with "unknown key 'source'" — a 422 on every Builder save, with nothing
# written. These pin all three save routes: the key is accepted, and it is not
# written into the driver file either.


def _decorated() -> dict:
    """A definition shaped exactly as the listing endpoint returns one."""
    return {**DEFINITION, "source": "user", "_source_file": "/tmp/acme.avcdriver"}


async def test_create_accepts_a_definition_carrying_listing_metadata(driver_dirs):
    _, repo_dir = driver_dirs

    result = await create_driver_definition(
        DriverDefinitionRequest(**_decorated())
    )
    assert result["status"] == "created"

    text = (repo_dir / "acme_widget.avcdriver").read_text()
    assert "source" not in yaml.safe_load(text)
    assert "_source_file" not in text


async def test_replace_accepts_a_definition_carrying_listing_metadata(driver_dirs):
    _, repo_dir = driver_dirs
    save_driver_definition(dict(DEFINITION), repo_dir)

    result = await update_driver_definition(
        "acme_widget", DriverDefinitionRequest(**{**_decorated(), "name": "Acme II"})
    )
    assert result["status"] == "updated"

    saved = yaml.safe_load((repo_dir / "acme_widget.avcdriver").read_text())
    assert saved["name"] == "Acme II"
    assert "source" not in saved


async def test_patch_accepts_a_definition_carrying_listing_metadata(driver_dirs):
    _, repo_dir = driver_dirs
    save_driver_definition(dict(DEFINITION), repo_dir)

    result = await patch_driver_definition("acme_widget", _decorated())
    assert result["status"] == "updated"

    saved = yaml.safe_load((repo_dir / "acme_widget.avcdriver").read_text())
    assert "source" not in saved
