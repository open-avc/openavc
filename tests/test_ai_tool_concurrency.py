"""Regression test for AI tool concurrency serialization (audit C9).

AI tool calls are dispatched as background asyncio tasks so the agent's
receive loop stays responsive. Before the fix they were fire-and-forget
(untracked, GC-vulnerable, never cancelled on shutdown) and ran fully
concurrently, so two project-mutating tools could interleave on the shared
``engine.project`` and lose updates.

These tests assert: project-mutating tools serialize (never overlap),
read-only tools and ``execute_macro`` still run concurrently, ``handle()``
keeps a strong ref to each in-flight task, and ``shutdown()`` cancels them.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from openavc.cloud.ai_tool_handler import AIToolHandler


def _make_handler() -> AIToolHandler:
    agent = MagicMock()
    agent.send_message = AsyncMock()
    return AIToolHandler(agent, MagicMock(), MagicMock(), project_path=None)


class _Tracker:
    """A fake tool handler that records the peak number of concurrent runs."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def run(self, _tool_input: dict) -> dict:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        return {"ok": True}


async def test_mutating_tools_serialize():
    handler = _make_handler()
    t = _Tracker()
    # "add_device" is a project-mutating (non-concurrent-safe) tool name, so
    # both calls must contend on the project lock and run one at a time.
    await asyncio.gather(
        handler._execute_tool("r1", "add_device", t.run, {}),
        handler._execute_tool("r2", "add_device", t.run, {}),
    )
    assert t.max_active == 1  # never overlapped -> serialized


async def test_read_only_tools_run_concurrently():
    handler = _make_handler()
    t = _Tracker()
    # Read-only tools must not take the lock (a long read shouldn't block).
    await asyncio.gather(
        handler._execute_tool("r1", "list_devices", t.run, {}),
        handler._execute_tool("r2", "get_logs", t.run, {}),
    )
    assert t.max_active == 2  # ran concurrently


async def test_execute_macro_does_not_block_project_edits():
    handler = _make_handler()
    t = _Tracker()
    # execute_macro awaits a (possibly long) macro run but never mutates the
    # project, so it must run concurrently with a project edit, not hold the
    # lock and block it.
    await asyncio.gather(
        handler._execute_tool("r1", "execute_macro", t.run, {}),
        handler._execute_tool("r2", "add_device", t.run, {}),
    )
    assert t.max_active == 2


async def test_handle_tracks_task_and_shutdown_cancels():
    handler = _make_handler()
    started = asyncio.Event()

    async def hang(_tool_input: dict) -> dict:
        started.set()
        await asyncio.sleep(100)
        return {}

    handler._tools["add_device"] = hang  # swap in a hanging handler

    msg = {"payload": {"request_id": "r1", "tool_name": "add_device", "tool_input": {}}}
    await handler.handle(msg)
    await asyncio.wait_for(started.wait(), timeout=1.0)

    # handle() keeps a strong ref to the in-flight task.
    assert len(handler._pending_tasks) == 1

    # shutdown() cancels it and clears the set.
    await handler.shutdown()
    assert handler._pending_tasks == set()
