"""The AI's driver create and update answer with the checker's issue records.

The Driver Builder's Validate route answers every issue the checker finds in a
draft, errors and warnings alike, each with the place in the definition it is
about. The AI's ``create_driver_definition`` and ``update_driver_definition``
answer from the same records: a refused draft names each error (and any
warning) with its place, and a saved one names the warnings it was saved with,
so a poll that overruns its interval or a parameter with no label is heard by
the AI instead of saved in silence. A warning never refuses a save.

An invented driver throughout; nothing is dialed.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.api.routes.driver_definitions import validate_driver_definition_draft
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.protocol import AI_TOOL_CALL, AI_TOOL_RESULT, _now_iso
from openavc.drivers.registry import is_driver_registered, unregister_driver

#: A definition the checker accepts with one warning: a parameter with no label.
WARNS: dict[str, Any] = {
    "id": "acme_yaml",
    "name": "Acme YAML",
    "transport": "tcp",
    "commands": {
        "set_input": {"send": "IN {src}\\r", "params": {"src": {"type": "string"}}},
    },
}

UNLABELLED = {
    "severity": "warning",
    "message": (
        "Command 'set_input' parameter 'src' has no label, so forms show its key "
        "made readable. Give it a label that says what to enter."
    ),
    "path": "commands.set_input.params.src",
}

#: The same definition with a key the format does not have: refused.
BROKEN: dict[str, Any] = {**WARNS, "bogus": 1}

UNKNOWN_KEY = {"severity": "error", "message": "unknown key 'bogus'", "path": ""}


def _handler(agent: Any = None) -> AIToolHandler:
    return AIToolHandler(agent or MagicMock(), MagicMock(), MagicMock())


@pytest.fixture
def driver_dirs(tmp_path, monkeypatch):
    builtin = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin.mkdir()
    repo.mkdir()
    monkeypatch.setattr("openavc.system_config.DRIVER_DEFINITIONS_DIR", builtin)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)
    announced = AsyncMock()
    monkeypatch.setattr("openavc.drivers.driver_change.announce", announced)
    yield repo, announced
    unregister_driver("acme_yaml")


def _existing(repo) -> None:
    (repo / "acme_yaml.avcdriver").write_text(
        "id: acme_yaml\nname: Old\ntransport: tcp\n", encoding="utf-8",
    )


async def test_a_created_driver_answers_the_warnings_it_was_saved_with(driver_dirs):
    repo, announced = driver_dirs

    answer = await _handler()._create_driver_definition({"definition": WARNS})

    assert answer == {
        "status": "created", "id": "acme_yaml", "issues": [UNLABELLED], "devices_reconnected": [],
    }
    assert (repo / "acme_yaml.avcdriver").exists()
    assert is_driver_registered("acme_yaml")
    announced.assert_awaited_once_with("acme_yaml")


async def test_an_updated_driver_answers_the_warnings_it_was_saved_with(driver_dirs):
    repo, announced = driver_dirs
    _existing(repo)

    answer = await _handler()._update_driver_definition(
        {"driver_id": "acme_yaml", "definition": WARNS},
    )

    assert answer == {
        "status": "updated", "id": "acme_yaml", "issues": [UNLABELLED], "devices_reconnected": [],
    }
    assert "set_input" in (repo / "acme_yaml.avcdriver").read_text(encoding="utf-8")
    announced.assert_awaited_once_with("acme_yaml")


async def test_a_refused_create_names_each_issue_and_its_place(driver_dirs):
    repo, announced = driver_dirs

    answer = await _handler()._create_driver_definition({"definition": BROKEN})

    assert answer == {"error": "unknown key 'bogus'", "issues": [UNKNOWN_KEY, UNLABELLED]}
    assert not (repo / "acme_yaml.avcdriver").exists()
    announced.assert_not_awaited()


async def test_a_refused_update_names_each_issue_and_its_place(driver_dirs):
    repo, announced = driver_dirs
    _existing(repo)

    answer = await _handler()._update_driver_definition(
        {"driver_id": "acme_yaml", "definition": BROKEN},
    )

    assert answer == {"error": "unknown key 'bogus'", "issues": [UNKNOWN_KEY, UNLABELLED]}
    assert "Old" in (repo / "acme_yaml.avcdriver").read_text(encoding="utf-8")
    announced.assert_not_awaited()


@pytest.mark.parametrize("draft", [WARNS, BROKEN], ids=["saved", "refused"])
async def test_the_ai_hears_what_the_builders_validate_shows(driver_dirs, draft):
    answer = await _handler()._create_driver_definition({"definition": draft})

    assert answer["issues"] == (await validate_driver_definition_draft(draft))["issues"]


async def test_a_refusal_goes_up_as_a_failure_with_its_issues(driver_dirs):
    agent = MagicMock()
    agent.send_message = AsyncMock()
    handler = _handler(agent)

    await handler.handle({
        "type": AI_TOOL_CALL, "ts": _now_iso(), "seq": 1, "session": "test",
        "payload": {
            "request_id": "req-1",
            "tool_name": "create_driver_definition",
            "tool_input": {"definition": BROKEN},
        },
    })
    for _ in range(5):
        await asyncio.sleep(0)

    kind, payload = agent.send_message.await_args.args
    assert kind == AI_TOOL_RESULT
    assert payload["success"] is False
    assert payload["error"] == "unknown key 'bogus'"
    assert payload["result"]["issues"] == [UNKNOWN_KEY, UNLABELLED]
