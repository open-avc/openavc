"""The "Which driver?" answer: the driver chosen, exactly which file, and the
person's own account of the device.

A report must tell the published driver from an edited copy, so the chosen
driver's files are hashed against the catalog's published hashes. The model
the person typed is checked against the models the driver lists, and their
choice is compared with the verdict the network check reached first.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi import HTTPException

from openavc.api.models import AuditDriverRequest, AuditStartRequest
from openavc.api.routes import audit as routes
from openavc.audit.driver_choice import (
    SOURCE_CATALOG,
    SOURCE_IMPORTED,
    driver_identity,
    model_listing,
    verdict_agreement,
)
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from tests.test_audit_api import wired  # noqa: F401  (the fixture)

YAML = """\
id: acme_choice
name: Acme Choice
manufacturer: Acme
category: utility
version: 1.2.0
transport: tcp
default_config: {port: 7000}
compatible_models:
  - manufacturer: Acme
    models: [W-100, W-200]
    confidence: partial
state_variables: {}
commands: {}
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A driver_repo holding one YAML driver, registered as the loader would."""
    import yaml

    from openavc import system_config

    repo = tmp_path / "driver_repo"
    repo.mkdir()
    (repo / "acme_choice.avcdriver").write_text(YAML, encoding="utf-8")
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    _DRIVER_REGISTRY["acme_choice"] = create_configurable_driver_class(yaml.safe_load(YAML))
    yield repo
    _DRIVER_REGISTRY.pop("acme_choice", None)


def _catalog(digest: str, version: str = "1.2.0") -> list[dict]:
    return [{
        "id": "acme_choice", "version": version, "verified": False,
        "files": {"utility/acme_choice.avcdriver": digest},
        "compatible_models": [{"manufacturer": "Acme", "models": ["W-100", "W-200"],
                               "confidence": "partial"}],
    }]


def test_the_published_file_matches_the_catalog(repo):
    digest = hashlib.sha256((repo / "acme_choice.avcdriver").read_bytes()).hexdigest()
    identity = driver_identity("acme_choice", _catalog(digest))
    assert identity["source"] == SOURCE_CATALOG
    assert identity["version"] == "1.2.0" and identity["format"] == "avcdriver"
    assert identity["files"] == [{
        "name": "acme_choice.avcdriver", "sha256": digest, "catalog_sha256": digest,
        "matches_catalog": True,
    }]
    assert identity["modified"] is False
    assert identity["catalog"] == {"listed": True, "version": "1.2.0", "verified": False,
                                   "checked": True}


def test_a_locally_edited_copy_is_flagged(repo):
    published = hashlib.sha256(b"what the catalog serves").hexdigest()
    identity = driver_identity("acme_choice", _catalog(published))
    assert identity["modified"] is True
    assert identity["files"][0]["matches_catalog"] is False


def test_a_driver_the_catalog_does_not_list_was_imported(repo):
    identity = driver_identity("acme_choice", [])
    assert identity["source"] == SOURCE_IMPORTED
    assert identity["catalog"]["listed"] is False and identity["catalog"]["checked"] is False
    assert identity["files"][0]["catalog_sha256"] is None


def test_the_model_typed_is_checked_against_the_drivers_list(repo):
    catalog = _catalog("x")
    assert model_listing("acme_choice", "Acme", "w-100", catalog) == {
        "listed": True, "confidence": "partial",
    }
    assert model_listing("acme_choice", "Acme", "W-300", catalog) == {
        "listed": False, "confidence": None,
    }
    assert model_listing("acme_choice", "Acme", "", catalog)["listed"] is None
    # Without a catalog the driver's own list is read.
    assert model_listing("acme_choice", "Acme", "W-200", None)["listed"] is True


def test_the_choice_is_compared_with_the_verdict():
    verdict = {"identification": {"driver_id": "acme_a", "candidates": ["acme_b"]}}
    assert verdict_agreement(verdict, "acme_a") == "agrees"
    assert verdict_agreement(verdict, "acme_b") == "candidate"
    assert verdict_agreement(verdict, "acme_c") == "differs"
    assert verdict_agreement({"identification": {}}, "acme_c") == "no_verdict"
    assert verdict_agreement(None, "acme_c") == "no_verdict"


def test_the_request_model_declares_every_field():
    assert set(AuditDriverRequest.model_fields) == {
        "driver_id", "manufacturer", "model", "firmware",
    }
    with pytest.raises(Exception):
        AuditDriverRequest(driver_id="x", serial="y")


async def _checked_session(wired):  # noqa: F811
    started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    session_id = started["session"]["session_id"]
    await routes.run_network_check(session_id)
    for _ in range(100):
        if wired.manager.current().footprint is not None:
            break
        await asyncio.sleep(0.01)
    return session_id


async def test_choosing_a_driver_records_it_and_the_device(wired, repo):  # noqa: F811
    session_id = await _checked_session(wired)
    result = await routes.set_driver(session_id, AuditDriverRequest(
        driver_id="acme_choice", manufacturer="Acme", model="W-100", firmware="2.04",
    ))
    state = result["session"]
    assert state["device"] == {"manufacturer": "Acme", "model": "W-100", "firmware": "2.04"}
    (run,) = state["runs"]
    assert run["choice"]["driver_id"] == "acme_choice"
    assert run["choice"]["identity"]["version"] == "1.2.0"
    assert "_paths" not in run["choice"]["identity"]
    assert run["choice"]["model_listing"]["listed"] is True
    assert run["active"] is False and "driver" in state["steps"]

    # Changing their mind before connecting replaces the choice.
    result = await routes.set_driver(session_id, AuditDriverRequest(
        driver_id="acme_audit_api", manufacturer="Acme", model="X",
    ))
    assert [r["choice"]["driver_id"] for r in result["session"]["runs"]] == ["acme_audit_api"]
    await wired.manager.shutdown()


async def test_no_driver_yet_and_refusals(wired, repo):  # noqa: F811
    started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
    session_id = started["session"]["session_id"]
    with pytest.raises(HTTPException) as exc:
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_choice"))
    assert exc.value.status_code == 409 and "network check" in exc.value.detail

    await routes.run_network_check(session_id)
    for _ in range(100):
        if wired.manager.current().footprint is not None:
            break
        await asyncio.sleep(0.01)
    with pytest.raises(HTTPException) as exc:
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_missing"))
    assert exc.value.status_code == 409 and "Install acme_missing first" in exc.value.detail

    result = await routes.set_driver(session_id, AuditDriverRequest(
        driver_id=None, manufacturer="Acme", model="W-900",
    ))
    assert result["session"]["no_driver"] is True
    assert result["session"]["runs"] == []
    assert result["session"]["device"]["model"] == "W-900"
    await wired.manager.shutdown()


async def test_test_another_driver_keeps_the_first_run(wired, repo):  # noqa: F811
    session_id = await _checked_session(wired)
    await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_choice"))
    session = wired.manager.current()
    session.runs[0].started_at = 1.0  # as if it had connected
    result = await routes.test_another_driver(session_id)
    assert result["session"]["runs"][0]["finished_at"] is not None
    result = await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_audit_api"))
    assert [r["choice"]["driver_id"] for r in result["session"]["runs"]] == [
        "acme_choice", "acme_audit_api",
    ]
    await wired.manager.shutdown()


async def test_teardown_stops_a_running_sandbox(wired, repo):  # noqa: F811
    session_id = await _checked_session(wired)
    await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_choice"))
    session = wired.manager.current()
    stopped = []

    class FakeSandbox:
        started = True

        async def stop(self):
            stopped.append(True)
            self.started = False

    session.runs[0].sandbox = FakeSandbox()
    session.runs[0].started_at = 1.0
    with pytest.raises(HTTPException) as exc:
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_audit_api"))
    assert exc.value.status_code == 409 and "still connected" in exc.value.detail
    await routes.end_session(session_id, cancel=True)
    assert stopped == [True]
