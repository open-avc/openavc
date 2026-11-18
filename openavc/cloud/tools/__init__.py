"""Shared helpers for the AI tool mixins."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

#: The parts each project change made by the tool call running now could not
#: start; set by the dispatcher around one call (``collecting_not_running``).
_not_running: ContextVar[list | None] = ContextVar("ai_tool_not_running", default=None)


@contextmanager
def collecting_not_running() -> Iterator[list]:
    """Collect the parts the project changes made inside this block could
    not start (``note_applied``), for the tool call's answer to name. Each
    tool call is its own task, so a tool running beside it adds nothing."""
    parts: list = []
    token = _not_running.set(parts)
    try:
        yield parts
    finally:
        _not_running.reset(token)


def note_applied(outcome) -> None:
    """Keep what an applied project change (its ``ApplyOutcome``) could not
    start, for the answer of the tool call that made it."""
    parts = _not_running.get()
    if parts is not None:
        parts.extend(outcome.failed)


class ToolEditError(Exception):
    """Abort an ``apply_project_edit`` mutate with a structured tool result.

    AI tools report failures as ``{"error": ...}`` dicts, not exceptions.
    Raising this inside a mutate callback aborts the edit (nothing is
    applied) and hands the dict back through :func:`apply_tool_edit`.
    """

    def __init__(self, result: dict):
        super().__init__(result.get("error", "tool edit aborted"))
        self.result = result


async def apply_tool_edit(engine, mutate, *, files=None) -> dict | None:
    """Run ``engine.apply_project_edit(mutate)`` for an AI tool.

    Returns the :class:`ToolEditError` result dict when the mutate aborted,
    else None. Any other exception propagates to the tool dispatcher, which
    words a change that was not applied. A change that ran with a part it
    could not start is noted for the tool's answer (``note_applied``).
    ``files`` is the ``core/change_files.ChangeFiles`` the mutate writes and
    removes beside the project with, handed to the door.
    """
    error, _ = await apply_tool_change(engine, mutate, files=files)
    return error


async def apply_tool_change(engine, mutate, *, files=None) -> tuple[dict | None, Any]:
    """:func:`apply_tool_edit` for a tool that answers from what the change
    did: the refusal's result dict and None, or None and the change's
    ``ApplyOutcome``."""
    with_files = {} if files is None else {"files": files}
    try:
        outcome = await engine.apply_project_edit(mutate, **with_files)
    except ToolEditError as e:
        return e.result, None
    except ValueError as e:
        from openavc.core.installed_equipment import InstalledEquipmentError
        from openavc.core.saved_values import UnknownWordError
        if isinstance(e, InstalledEquipmentError | UnknownWordError):
            return {"error": str(e)}, None
        raise
    note_applied(outcome)
    return None, outcome


def merge_forward_compat(existing, model_cls: type, partial: dict):
    """Apply a partial update to a forward-compat (extra='allow') sub-model.

    Dumps the existing model, overlays the partial input, then re-validates —
    so omitted fields keep their current values (not the model defaults) and
    any unknown forward-compat keys a newer platform stored survive the
    round-trip, instead of being reset by ``model_cls(**partial)``.
    """
    merged = {**existing.model_dump(), **partial}
    return model_cls(**merged)
