"""Shared helpers for the AI tool mixins."""


class ToolEditError(Exception):
    """Abort an ``apply_project_edit`` mutate with a structured tool result.

    AI tools report failures as ``{"error": ...}`` dicts, not exceptions.
    Raising this inside a mutate callback aborts the edit (nothing is
    applied) and hands the dict back through :func:`apply_tool_edit`.
    """

    def __init__(self, result: dict):
        super().__init__(result.get("error", "tool edit aborted"))
        self.result = result


async def apply_tool_edit(engine, mutate) -> dict | None:
    """Run ``engine.apply_project_edit(mutate)`` for an AI tool.

    Returns the :class:`ToolEditError` result dict when the mutate aborted,
    else None. Any other exception propagates to the tool dispatcher.
    """
    try:
        await engine.apply_project_edit(mutate)
    except ToolEditError as e:
        return e.result
    return None
