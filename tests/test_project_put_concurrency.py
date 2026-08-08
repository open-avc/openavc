"""Regression tests for PUT /api/project optimistic concurrency.

The revision compare and the save+reload must be atomic. Checked at the
route (outside the engine's reload lock), two concurrent PUTs carrying the
same If-Match can both pass the compare — both save, and the first
writer's edit is silently overwritten despite the 409 contract that exists
to prevent exactly that.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from openavc.api import rest, ws
from openavc.core.engine import Engine
from openavc.main import app

# A body always carries its format version: the route refuses one without it,
# because the migration chain would treat the body as the oldest format and
# destructively re-migrate it (see test at the bottom).
BODY = {"openavc_version": "0.8.0", "project": {"id": "t", "name": "Test"}}


@pytest.fixture
def engine(tmp_path):
    eng = Engine(str(tmp_path / "t.avc"))
    rest.set_engine(eng)
    ws.set_engine(eng)
    yield eng
    rest.set_engine(None)
    ws.set_engine(None)


def _client() -> AsyncClient:
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    return AsyncClient(transport=transport, base_url="http://testserver")


def _stub_persistence(engine, monkeypatch, save_delay: float = 0.0):
    """Replace disk save + subsystem reconcile with counters.

    The revision compare-and-bump itself stays REAL — it runs in
    apply_project under the reload lock, which is exactly the contract
    these tests pin.
    """
    saves = []

    async def fake_save(path, project):
        saves.append(project)
        if save_delay:
            # Hold the save window open so concurrent PUTs genuinely overlap.
            await asyncio.sleep(save_delay)

    monkeypatch.setattr("openavc.core.engine.save_project_async", fake_save)
    monkeypatch.setattr(
        "openavc.api.routes.project.save_project_async", fake_save, raising=False
    )

    async def fake_reconcile(diff, origin):
        pass

    monkeypatch.setattr(engine, "_reconcile", fake_reconcile)
    return saves


@pytest.mark.asyncio
async def test_put_project_stale_if_match_rejected(engine, monkeypatch):
    saves = _stub_persistence(engine, monkeypatch)
    engine._project_revision = 5

    async with _client() as c:
        resp = await c.put("/api/project", json=BODY, headers={"If-Match": '"3"'})

    assert resp.status_code == 409
    assert saves == []
    assert engine._project_revision == 5


@pytest.mark.asyncio
async def test_put_project_match_saves_and_returns_new_etag(engine, monkeypatch):
    saves = _stub_persistence(engine, monkeypatch)
    engine._project_revision = 5

    async with _client() as c:
        resp = await c.put("/api/project", json=BODY, headers={"If-Match": '"5"'})

    assert resp.status_code == 200
    assert len(saves) == 1
    assert resp.headers["etag"] == '"6"'


@pytest.mark.asyncio
async def test_put_project_concurrent_same_revision_one_loses(engine, monkeypatch):
    saves = _stub_persistence(engine, monkeypatch, save_delay=0.05)
    engine._project_revision = 0

    async with _client() as c:
        r1, r2 = await asyncio.gather(
            c.put("/api/project", json=BODY, headers={"If-Match": '"0"'}),
            c.put("/api/project", json=BODY, headers={"If-Match": '"0"'}),
        )

    # One writer wins, the other must get 409 — not a silent overwrite.
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    assert len(saves) == 1
    assert engine._project_revision == 1


@pytest.mark.asyncio
async def test_put_project_legacy_revision_body_field_refused(engine, monkeypatch):
    """The removed `_revision` body field is refused, never quietly dropped.

    It used to be an alternative to If-Match. Ignoring it would be the worst
    outcome: the caller thinks it is protected from a concurrent save and
    isn't, which is the silent lost update the 409 contract exists to stop.
    Letting it through would be almost as bad — the project model allows
    extra fields, so it would be persisted into the saved project.
    """
    saves = _stub_persistence(engine, monkeypatch)
    engine._project_revision = 5

    async with _client() as c:
        resp = await c.put("/api/project", json={**BODY, "_revision": 5})

    assert resp.status_code == 400
    assert "If-Match" in resp.json()["detail"]
    assert saves == []
    assert engine._project_revision == 5


@pytest.mark.asyncio
async def test_put_project_legacy_revision_refused_even_alongside_if_match(
    engine, monkeypatch
):
    """A correct If-Match does not excuse the dead field.

    Accepting the header and silently swallowing the body field would let it
    ride along into the saved project via the model's extra="allow".
    """
    saves = _stub_persistence(engine, monkeypatch)
    engine._project_revision = 5

    async with _client() as c:
        resp = await c.put(
            "/api/project", json={**BODY, "_revision": 5}, headers={"If-Match": '"5"'}
        )

    assert resp.status_code == 400
    assert saves == []
    assert engine._project_revision == 5


@pytest.mark.asyncio
async def test_put_project_without_a_version_is_refused(engine, monkeypatch):
    """No version field, no save.

    The migration chain keys on `openavc_version` and assumes the OLDEST
    format when it is absent -- which runs the whole chain over a
    current-format body, collapses every placement to the 1x1 grid cell,
    re-divides every rem value by 14, and stamps the wreckage current. Every
    legitimate producer includes the field, so absence is refused rather
    than guessed at.
    """
    saves = _stub_persistence(engine, monkeypatch)
    engine._project_revision = 1
    body = {k: v for k, v in BODY.items() if k != "openavc_version"}

    async with _client() as c:
        resp = await c.put("/api/project", json=body, headers={"If-Match": '"1"'})

    assert resp.status_code == 422
    assert "openavc_version" in resp.json()["detail"]
    assert saves == []
    assert engine._project_revision == 1
