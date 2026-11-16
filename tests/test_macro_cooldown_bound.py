"""A macro's own Cooldown is a number of seconds, 0 or more, as a trigger's is.

The macro engine reads a negative one as no cooldown, and the macro editor
showed it in its Cooldown box as written. The project schema declares the
bound on the macro model, the 0.17.0 step brings an older project's value
inside it, and the editor's box reads it from the generated table.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openavc.core import project_loader
from openavc.core.project_loader import MacroConfig, ProjectConfig
from openavc.core.project_migration import migrate_0_16_to_0_17, migrate_project
from openavc.core.project_values_gen import project_values


@pytest.mark.parametrize("value", [-5, -0.5, float("nan"), float("inf"), float("-inf")])
def test_the_schema_refuses_a_cooldown_below_zero_or_no_finite_number(value):
    with pytest.raises(ValidationError) as caught:
        MacroConfig(id="m", name="M", cooldown_seconds=value)
    assert [e["loc"] for e in caught.value.errors()] == [("cooldown_seconds",)]


def test_zero_and_more_are_taken():
    assert MacroConfig(id="m", name="M").cooldown_seconds == 0
    assert MacroConfig(id="m", name="M", cooldown_seconds=0).cooldown_seconds == 0
    assert MacroConfig(id="m", name="M", cooldown_seconds=2.5).cooldown_seconds == 2.5


def test_the_table_carries_the_bound():
    assert project_values()["MacroConfig"]["cooldown_seconds"] == {
        "default": 0, "min": project_loader.SECONDS_MIN, "finite": True, "integer": False,
    }


def _v016_macros(*cooldowns) -> dict:
    return {
        "openavc_version": "0.16.0",
        "project": {"id": "p", "name": "P"},
        "macros": [
            {"id": f"m{i}", "name": "M", "cooldown_seconds": value}
            for i, value in enumerate(cooldowns)
        ],
    }


def test_the_0_17_step_brings_a_saved_negative_to_zero():
    result = migrate_0_16_to_0_17(_v016_macros(-5, "-1", 0, 3.5, "4", True))
    assert [m["cooldown_seconds"] for m in result["macros"]] == [0, 0, 0, 3.5, "4", True]


def test_the_0_17_step_removes_one_that_is_no_finite_number():
    """The engine read a NaN as no cooldown. An infinite one held the macro
    to one start a server run; a macro has no off switch, so it loads as no
    cooldown. Either way the Programmer could not open the project."""
    values = (float("nan"), float("inf"), float("-inf"), "NaN", "1e999")
    result, _ = migrate_project(_v016_macros(*values))
    assert [("cooldown_seconds" in m) for m in result["macros"]] == [False] * len(values)
    loaded = ProjectConfig(**result)
    assert [m.cooldown_seconds for m in loaded.macros] == [0] * len(values)
    json.dumps(loaded.model_dump(mode="json"), allow_nan=False)


def test_the_0_17_step_leaves_what_the_schema_refused_before():
    result = migrate_0_16_to_0_17(_v016_macros("soon", None))
    assert [m["cooldown_seconds"] for m in result["macros"]] == ["soon", None]
