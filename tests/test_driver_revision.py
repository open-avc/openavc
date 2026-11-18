"""A driver's revision: every read returns it, every save names it, and a save
made from an older copy is refused instead of undoing the change made since.

The case this holds closed: the Builder opens acme_widget, another door (the
assistant, a second Programmer) adds a command to it, and the Builder then
saves the copy it opened with one edit. Before revisions the save answered
"updated" and the added command was gone from the file and the registry, with
nothing said. Now the save is refused with a 409 that names the copy on disk
(``code`` ``driver_changed`` and its ``revision``), and the file keeps the
command.

A revision is the SHA-256 of the stored file's bytes, compared for equality
and nothing else. The Builder, the Code view and the assistant always name
one; a REST save that names none is not held to one, as a project save with
no If-Match is not. The assistant's update is always held: one that names no
revision is refused and told to read the driver first.
"""

# The route tests' fixtures (env) are shared, so each test that takes one
# names the import again.
# ruff: noqa: F811

from __future__ import annotations

import hashlib

import pytest
from fastapi import HTTPException

from openavc.api.models import DriverDefinitionRequest
from openavc.api.routes import driver_definitions as ROUTES
from openavc.api.routes import python_drivers as PYTHON_ROUTES
from openavc.drivers.driver_loader import save_driver_definition
from tests.test_driver_definition_routes_pins import (  # noqa: F401  (fixtures)
    WIDGET,
    Env,
    _assistant,
    _saved,
    env,
)

CHANGED = "This driver was changed by another session since you opened it."
POWER_OFF = {"power_off": {"send": "PWR OFF\\r"}}


def _revision_of(env: Env, name: str) -> str:
    return hashlib.sha256((env.repo / name).read_bytes()).hexdigest()


def _another_door_adds_power_off(env: Env) -> None:
    """What the assistant or a second Programmer did while the Builder had the
    driver open: a command added, saved to the same file."""
    save_driver_definition({**WIDGET, "commands": {**WIDGET["commands"], **POWER_OFF}}, env.repo)


def _refused(exc: pytest.ExceptionInfo[HTTPException]) -> tuple[int, str, dict]:
    return exc.value.status_code, exc.value.detail, getattr(exc.value, "fields", {})


# --- every read returns it ----------------------------------------------------------------------


async def test_the_listing_and_the_read_carry_the_stored_file_s_revision(env):
    save_driver_definition(dict(WIDGET), env.repo)
    revision = _revision_of(env, "acme_widget.avcdriver")

    listed = (await ROUTES.list_driver_definitions())["definitions"]
    assert [d["_revision"] for d in listed] == [revision]
    assert (await ROUTES.get_driver_definition("acme_widget"))["_revision"] == revision


async def test_the_assistant_s_read_carries_the_revision(env, monkeypatch):
    save_driver_definition(dict(WIDGET), env.repo)

    read = await _assistant(monkeypatch, env)._get_driver_definition({"driver_id": "acme_widget"})

    assert read["_revision"] == _revision_of(env, "acme_widget.avcdriver")


async def test_a_change_to_the_file_is_a_new_revision(env):
    save_driver_definition(dict(WIDGET), env.repo)
    before = (await ROUTES.get_driver_definition("acme_widget"))["_revision"]

    _another_door_adds_power_off(env)

    assert (await ROUTES.get_driver_definition("acme_widget"))["_revision"] != before


# --- a save from the copy opened before another door's change -----------------------------------


async def test_the_builder_s_save_from_an_older_copy_is_refused_and_keeps_the_change(env):
    save_driver_definition(dict(WIDGET), env.repo)
    opened = await ROUTES.get_driver_definition("acme_widget")
    _another_door_adds_power_off(env)
    env.events.clear()

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition(
            "acme_widget", DriverDefinitionRequest(**{**opened, "name": "Acme Widget 2"}),
        )

    status, detail, fields = _refused(exc)
    assert (status, detail) == (409, CHANGED)
    assert fields == {"code": "driver_changed", "revision": _revision_of(env, "acme_widget.avcdriver")}
    assert "power_off" in _saved(env, "acme_widget.avcdriver")["commands"]
    assert env.events == []


async def test_the_builder_s_save_naming_the_revision_on_disk_saves_over_it(env):
    """"Save mine over it": the same save again, naming the revision the
    refusal answered."""
    save_driver_definition(dict(WIDGET), env.repo)
    opened = await ROUTES.get_driver_definition("acme_widget")
    _another_door_adds_power_off(env)
    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition("acme_widget", DriverDefinitionRequest(**opened))

    mine = {**opened, "name": "Acme Widget 2", "_revision": exc.value.fields["revision"]}
    answer = await ROUTES.update_driver_definition("acme_widget", DriverDefinitionRequest(**mine))

    assert _saved(env, "acme_widget.avcdriver") == {**WIDGET, "name": "Acme Widget 2"}
    assert answer["revision"] == _revision_of(env, "acme_widget.avcdriver")


async def test_a_save_answers_the_revision_the_next_save_names(env):
    """A tab's own save never makes its next save stale."""
    save_driver_definition(dict(WIDGET), env.repo)
    opened = await ROUTES.get_driver_definition("acme_widget")

    first = await ROUTES.update_driver_definition(
        "acme_widget", DriverDefinitionRequest(**{**opened, "name": "Acme Widget 2"}),
    )
    second = await ROUTES.update_driver_definition(
        "acme_widget",
        DriverDefinitionRequest(**{**opened, "name": "Acme Widget 3", "_revision": first["revision"]}),
    )

    assert first["revision"] != second["revision"]
    assert second["revision"] == _revision_of(env, "acme_widget.avcdriver")
    assert _saved(env, "acme_widget.avcdriver")["name"] == "Acme Widget 3"


async def test_a_rename_is_held_to_the_revision_of_the_driver_it_renames(env):
    save_driver_definition(dict(WIDGET), env.repo)
    opened = await ROUTES.get_driver_definition("acme_widget")
    _another_door_adds_power_off(env)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition(
            "acme_widget", DriverDefinitionRequest(**{**opened, "id": "acme_widget_two"}),
        )

    assert exc.value.status_code == 409 and exc.value.detail == CHANGED
    assert env.files() == ["acme_widget.avcdriver"]


async def test_create_answers_its_revision(env):
    answer = await ROUTES.create_driver_definition(DriverDefinitionRequest(**WIDGET))

    assert answer["revision"] == _revision_of(env, "acme_widget.avcdriver")


async def test_a_rest_save_that_names_no_revision_is_not_held_to_one(env):
    """As a project save with no If-Match: a client that never read the
    driver replaces it."""
    save_driver_definition(dict(WIDGET), env.repo)
    _another_door_adds_power_off(env)

    await ROUTES.update_driver_definition(
        "acme_widget", DriverDefinitionRequest(**{**WIDGET, "name": "Acme Widget 2"}),
    )

    assert _saved(env, "acme_widget.avcdriver") == {**WIDGET, "name": "Acme Widget 2"}


async def test_a_door_s_own_refusals_come_before_the_revision(env):
    """A built-in is read-only whatever revision the save names."""
    save_driver_definition(dict(WIDGET), env.builtin)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.update_driver_definition(
            "acme_widget", DriverDefinitionRequest(**{**WIDGET, "_revision": "stale"}),
        )

    assert exc.value.status_code == 403


# --- the patch ----------------------------------------------------------------------------------


async def test_a_patch_from_an_older_copy_is_refused(env):
    save_driver_definition(dict(WIDGET), env.repo)
    opened = await ROUTES.get_driver_definition("acme_widget")
    _another_door_adds_power_off(env)

    with pytest.raises(HTTPException) as exc:
        await ROUTES.patch_driver_definition(
            "acme_widget", {"name": "Acme Widget 2", "_revision": opened["_revision"]},
        )

    assert (exc.value.status_code, exc.value.detail) == (409, CHANGED)
    assert _saved(env, "acme_widget.avcdriver")["name"] == "Acme Widget"


async def test_a_patch_naming_the_current_revision_saves_and_answers_the_next(env):
    save_driver_definition(dict(WIDGET), env.repo)
    opened = await ROUTES.get_driver_definition("acme_widget")

    answer = await ROUTES.patch_driver_definition(
        "acme_widget", {"name": "Acme Widget 2", "_revision": opened["_revision"]},
    )

    assert _saved(env, "acme_widget.avcdriver") == {**WIDGET, "name": "Acme Widget 2"}
    assert answer["revision"] == _revision_of(env, "acme_widget.avcdriver")


# --- the assistant ------------------------------------------------------------------------------


async def test_the_assistant_s_read_names_its_revision_first(env, monkeypatch):
    """The cloud cuts a long tool result short; the revision is in the part
    it keeps."""
    save_driver_definition(dict(WIDGET), env.repo)

    read = await _assistant(monkeypatch, env)._get_driver_definition({"driver_id": "acme_widget"})

    assert next(iter(read)) == "_revision"


async def test_the_assistant_s_get_then_update_over_the_wire_saves(env, monkeypatch):
    """A get then an update as the cloud sends them: each answer crosses the
    wire as JSON, and the definition the read answered goes back with one
    change, its _revision included."""
    import asyncio
    import json
    from unittest.mock import AsyncMock

    from openavc.cloud.protocol import AI_TOOL_CALL, _now_iso

    save_driver_definition(dict(WIDGET), env.repo)
    tools = _assistant(monkeypatch, env)
    tools._agent.send_message = AsyncMock()

    async def call(seq: int, tool_name: str, tool_input: dict) -> dict:
        await tools.handle({
            "type": AI_TOOL_CALL, "ts": _now_iso(), "seq": seq, "session": "test",
            "payload": {"request_id": f"req-{seq}", "tool_name": tool_name, "tool_input": tool_input},
        })
        for _ in range(20):
            await asyncio.sleep(0)
            if tools._agent.send_message.await_count == seq:
                break
        _kind, payload = tools._agent.send_message.await_args.args
        return json.loads(json.dumps(payload))

    read = await call(1, "get_driver_definition", {"driver_id": "acme_widget"})
    updated = await call(2, "update_driver_definition", {
        "driver_id": "acme_widget", "definition": {**read["result"], "name": "Acme Widget 2"},
    })

    assert updated["success"] is True, updated
    assert _saved(env, "acme_widget.avcdriver") == {**WIDGET, "name": "Acme Widget 2"}


async def test_the_assistant_s_update_from_an_older_read_is_refused_and_keeps_the_change(
    env, monkeypatch,
):
    save_driver_definition(dict(WIDGET), env.repo)
    tools = _assistant(monkeypatch, env)
    read = await tools._get_driver_definition({"driver_id": "acme_widget"})
    _another_door_adds_power_off(env)

    answer = await tools._update_driver_definition(
        {"driver_id": "acme_widget", "definition": {**read, "name": "Acme Widget 2"}},
    )

    assert answer == {
        "error": (
            "'acme_widget' was changed after you read it, in the Driver Builder or another "
            "session, so your update was not saved. Read it again with get_driver_definition "
            "and make your change to that copy."
        ),
    }
    assert "power_off" in _saved(env, "acme_widget.avcdriver")["commands"]


async def test_the_assistant_s_update_that_names_no_revision_is_refused(env, monkeypatch):
    save_driver_definition(dict(WIDGET), env.repo)
    _another_door_adds_power_off(env)

    answer = await _assistant(monkeypatch, env)._update_driver_definition(
        {"driver_id": "acme_widget", "definition": {**WIDGET, "name": "Acme Widget 2"}},
    )

    assert answer == {
        "error": (
            "Read 'acme_widget' with get_driver_definition first, and send back the definition "
            "it answers with your change made to it: its _revision says which copy you changed."
        ),
    }
    assert "power_off" in _saved(env, "acme_widget.avcdriver")["commands"]


async def test_the_assistant_s_create_and_update_answer_the_revision_its_next_update_names(
    env, monkeypatch,
):
    tools = _assistant(monkeypatch, env)

    created = await tools._create_driver_definition({"definition": dict(WIDGET)})
    updated = await tools._update_driver_definition({
        "driver_id": "acme_widget",
        "definition": {**WIDGET, "name": "Acme Widget 2", "_revision": created["_revision"]},
    })

    assert updated["status"] == "updated"
    assert updated["_revision"] == _revision_of(env, "acme_widget.avcdriver")


# --- the Code view's save -----------------------------------------------------------------------


PYTHON_WIDGET = '''\
from openavc.drivers.base import BaseDriver


class AcmeWidget(BaseDriver):
    DRIVER_INFO = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp"}
'''
PYTHON_WIDGET_WITH_POWER_OFF = PYTHON_WIDGET + "\n    async def power_off(self):\n        pass\n"


@pytest.fixture()
def python_repo(tmp_path, monkeypatch):
    import openavc.system_config as system_config

    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    (repo / "acme_widget.py").write_text(PYTHON_WIDGET, encoding="utf-8")
    return repo


async def test_the_code_view_s_read_carries_the_revision(python_repo):
    read = await PYTHON_ROUTES.get_python_driver_source("acme_widget")

    assert read["revision"] == hashlib.sha256((python_repo / "acme_widget.py").read_bytes()).hexdigest()


@pytest.mark.parametrize("require_valid_syntax", [False, True], ids=["save", "save and reload"])
async def test_the_code_view_s_save_from_an_older_copy_is_refused_and_keeps_the_change(
    python_repo, require_valid_syntax,
):
    opened = await PYTHON_ROUTES.get_python_driver_source("acme_widget")
    (python_repo / "acme_widget.py").write_text(PYTHON_WIDGET_WITH_POWER_OFF, encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        await PYTHON_ROUTES.save_python_driver_source("acme_widget", {
            "source": PYTHON_WIDGET.replace("Acme Widget", "Acme Widget 2"),
            "revision": opened["revision"],
            "require_valid_syntax": require_valid_syntax,
        })

    current = hashlib.sha256((python_repo / "acme_widget.py").read_bytes()).hexdigest()
    assert _refused(exc) == (409, CHANGED, {"code": "driver_changed", "revision": current})
    assert (python_repo / "acme_widget.py").read_text(encoding="utf-8") == PYTHON_WIDGET_WITH_POWER_OFF


async def test_the_code_view_s_stale_save_is_refused_before_its_syntax_is_judged(python_repo):
    """Source that will not parse, saved from an older copy, is the stale
    copy's refusal: whether it parses is a question about an edit that is not
    going to be written."""
    opened = await PYTHON_ROUTES.get_python_driver_source("acme_widget")
    (python_repo / "acme_widget.py").write_text(PYTHON_WIDGET_WITH_POWER_OFF, encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        await PYTHON_ROUTES.save_python_driver_source("acme_widget", {
            "source": "class Broken(", "revision": opened["revision"], "require_valid_syntax": True,
        })

    assert exc.value.status_code == 409


async def test_the_code_view_s_save_answers_the_revision_its_next_save_names(python_repo):
    opened = await PYTHON_ROUTES.get_python_driver_source("acme_widget")

    first = await PYTHON_ROUTES.save_python_driver_source(
        "acme_widget", {"source": PYTHON_WIDGET + "\n", "revision": opened["revision"]},
    )
    second = await PYTHON_ROUTES.save_python_driver_source(
        "acme_widget", {"source": PYTHON_WIDGET + "\n\n", "revision": first["revision"]},
    )

    assert second["status"] == "saved"
    assert second["revision"] == hashlib.sha256(
        (python_repo / "acme_widget.py").read_bytes()
    ).hexdigest()
