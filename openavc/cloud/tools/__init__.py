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
    except ValueError as e:
        from openavc.core.installed_equipment import InstalledEquipmentError
        if isinstance(e, InstalledEquipmentError):
            return {"error": str(e)}
        raise
    return None


def merge_forward_compat(existing, model_cls: type, partial: dict):
    """Apply a partial update to a forward-compat (extra='allow') sub-model.

    Dumps the existing model, overlays the partial input, then re-validates —
    so omitted fields keep their current values (not the model defaults) and
    any unknown forward-compat keys a newer platform stored survive the
    round-trip, instead of being reset by ``model_cls(**partial)``.
    """
    merged = {**existing.model_dump(), **partial}
    return model_cls(**merged)
