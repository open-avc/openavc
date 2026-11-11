"""A macro step that moves the panel to a page this project does not have.

The macro lint's reference checks name a step's device, device group and
macro when the project has no such thing; a ``ui.navigate`` step's page is
the same kind of reference and is checked the same way: warned in the log,
never refused, because a macro may be written before the page it opens. The
panel's own targets, ``$back`` and ``$dismiss``, are never a missing page.
"""

from __future__ import annotations

from openavc.core.macro_validation import validate_macro
from openavc.core.project_loader import ProjectConfig


def _project() -> ProjectConfig:
    return ProjectConfig.model_validate({
        "project": {"id": "n", "name": "Navigate"},
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": []}]},
    })


def _warned(caplog, steps) -> str:
    with caplog.at_level("WARNING", logger="openavc.core.macro_validation"):
        refusal = validate_macro(steps, [], _project())
    assert refusal is None
    return " ".join(r.message for r in caplog.records)


def test_a_step_to_a_page_the_project_does_not_have_is_warned(caplog) -> None:
    logged = _warned(caplog, [{"action": "ui.navigate", "page": "lobby"}])
    assert "steps[0]: page 'lobby' not found in project" in logged


def test_a_nested_step_to_a_missing_page_is_warned_where_it_sits(caplog) -> None:
    steps = [{
        "action": "conditional",
        "condition": {"key": "var.x", "operator": "eq", "value": 1},
        "then_steps": [{"action": "ui.navigate", "page": "lobby"}],
    }]
    logged = _warned(caplog, steps)
    assert "steps[0].then_steps[0]: page 'lobby' not found in project" in logged


def test_a_real_page_and_the_panels_own_targets_are_not(caplog) -> None:
    steps = [
        {"action": "ui.navigate", "page": "main"},
        {"action": "ui.navigate", "page": "$back"},
        {"action": "ui.navigate", "page": "$dismiss"},
    ]
    assert "not found" not in _warned(caplog, steps)
