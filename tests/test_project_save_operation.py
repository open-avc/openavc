"""A save sent again after its answer was lost.

The Programmer names each save with an operation id (the
``X-OpenAVC-Operation`` header) and sends the same save, under the same id
and the same If-Match, when no answer came back. When the first one was
taken in, the revision has moved past that If-Match, and without the id the
resend is answered as a conflict with another session: the tab is told
someone else changed the project when it was its own save. With the id, the
server recognises the save that made the current revision and answers what
that save did, without applying it twice.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from openavc.api import rest, ws
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig
from openavc.main import app

BODY = {"openavc_version": "0.8.0", "project": {"id": "t", "name": "Test"}}
OTHER = {"openavc_version": "0.8.0", "project": {"id": "t", "name": "Other"}}


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


class _Runs:
    """Counts each save the engine applied; ``fail_next`` makes the next
    reconcile raise, so that apply is not taken in."""

    def __init__(self) -> None:
        self.applied = 0
        self.fail_next = False


@pytest.fixture
def runs(engine, monkeypatch) -> _Runs:
    counter = _Runs()

    async def fake_save(path, project):
        return None

    async def fake_reconcile(run):
        if counter.fail_next:
            counter.fail_next = False
            raise RuntimeError("acme stage failed")
        counter.applied += 1

    async def fake_rollback(run):
        return None

    monkeypatch.setattr("openavc.core.project_changes.save_project_async", fake_save)
    monkeypatch.setattr(engine, "_reconcile", fake_reconcile)
    monkeypatch.setattr(engine, "_rollback_reconcile", fake_rollback)
    return counter


async def _put(client: AsyncClient, body: dict, revision: str, operation: str | None):
    headers = {"If-Match": f'"{revision}"'}
    if operation is not None:
        headers["X-OpenAVC-Operation"] = operation
    return await client.put("/api/project", json=body, headers=headers)


@pytest.mark.asyncio
async def test_a_resent_save_that_was_taken_in_is_answered_saved(engine, runs):
    base = engine._project_revision
    async with _client() as c:
        first = await _put(c, BODY, base, "op-1")
        # The first answer never reached the tab: it sends the same save again.
        again = await _put(c, BODY, base, "op-1")

    assert first.status_code == 200
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "saved"
    assert again.headers["etag"] == first.headers["etag"]
    assert runs.applied == 1


@pytest.mark.asyncio
async def test_a_stale_save_under_another_id_is_still_a_conflict(engine, runs):
    base = engine._project_revision
    async with _client() as c:
        await _put(c, BODY, base, "op-1")
        other = await _put(c, OTHER, base, "op-2")

    assert other.status_code == 409
    assert other.json()["code"] == "conflict"
    assert runs.applied == 1


@pytest.mark.asyncio
async def test_a_stale_save_with_no_id_is_still_a_conflict(engine, runs):
    base = engine._project_revision
    async with _client() as c:
        await _put(c, BODY, base, "op-1")
        bare = await _put(c, BODY, base, None)

    assert bare.status_code == 409


@pytest.mark.asyncio
async def test_a_resend_after_another_change_is_a_conflict(engine, runs):
    """The id is recognised only while the save it names made the current
    revision: once another change has landed, answering "saved" with the
    newer revision would let the tab save over that change unseen."""
    base = engine._project_revision
    async with _client() as c:
        first = await _put(c, BODY, base, "op-1")
        moved = first.headers["etag"].strip('"')
        await _put(c, OTHER, moved, "op-2")
        again = await _put(c, BODY, base, "op-1")

    assert again.status_code == 409
    assert runs.applied == 2


@pytest.mark.asyncio
async def test_a_save_that_was_not_applied_is_applied_when_sent_again(engine, runs):
    base = engine._project_revision
    runs.fail_next = True
    async with _client() as c:
        failed = await _put(c, BODY, base, "op-1")
        again = await _put(c, BODY, base, "op-1")

    assert failed.status_code == 500
    assert failed.json()["code"] == "not_applied"
    assert again.status_code == 200
    assert runs.applied == 1
    assert engine._project_revision != base


@pytest.mark.asyncio
async def test_a_malformed_id_is_ignored(engine, runs):
    base = engine._project_revision
    async with _client() as c:
        await _put(c, BODY, base, "not an id!")
        again = await _put(c, BODY, base, "not an id!")

    assert again.status_code == 409
    assert runs.applied == 1


@pytest.mark.asyncio
async def test_the_resend_answers_what_the_save_did(engine, runs):
    """The resend hears the same answer the lost one held, its revision and
    the parts it could not run included."""
    base = engine._project_revision
    first = await engine.apply_project(
        ProjectConfig(**BODY), expected_revision=base, operation="op-1",
    )
    again = await engine.apply_project(
        ProjectConfig(**BODY), expected_revision=base, operation="op-1",
    )

    assert again == first
    assert runs.applied == 1
