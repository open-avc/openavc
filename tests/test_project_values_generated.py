"""The generated project-value table must match the project models.

``openavc/core/project_loader.py`` declares what each project value may hold
(``Field(ge=, le=)``) and what a blank one is (the field's default);
``python -m openavc.core.project_values_gen`` renders them into one table, in
two files: ``projectValues.gen.ts`` for the Builder's fields and
``modules/project_values.gen.js`` for the panel, which draws a Builder draft
that never passed the loader. This re-renders and compares both byte for byte,
so a bound changed on a model without regenerating, or an artifact edited by
hand, fails CI instead of letting the Builder offer a value the save refuses.

Same arrangement as tests/test_ui_minimums_generated.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from openavc.core import project_loader
from openavc.core.project_values_gen import (
    ARTIFACTS,
    UnreadableBoundError,
    project_values,
    render,
)
from tests import gates

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("artifact", sorted(ARTIFACTS))
def test_each_committed_artifact_matches_the_models(artifact: str) -> None:
    path = REPO_ROOT / artifact
    assert path.is_file(), f"{artifact} is missing -- run 'python -m openavc.core.project_values_gen'"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render(artifact), (
        f"{artifact} does not match the project models -- regenerate with "
        f"'python -m openavc.core.project_values_gen' (never edit it by hand)"
    )


def _bounded_fields() -> set[tuple[str, str]]:
    """Every (model, field) in the loader whose field declares a bound."""
    found = set()
    for name in dir(project_loader):
        cls = getattr(project_loader, name)
        if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            continue
        if cls.__module__ != project_loader.__name__:
            continue
        for field, info in cls.model_fields.items():
            if any(hasattr(m, "ge") or hasattr(m, "le") for m in info.metadata):
                found.add((cls.__name__, field))
    return found


def test_every_bound_on_a_project_model_reaches_the_table() -> None:
    """A bound added to any project model appears, not silently Python-only.

    A field inherited from another model (a master element's decimals are an
    element's) is listed once, under the model that declares it.
    """
    table = project_values()
    for model, field in _bounded_fields():
        owner = next(
            base.__name__ for base in reversed(getattr(project_loader, model).__mro__)
            if isinstance(base, type) and issubclass(base, BaseModel)
            and field in getattr(base, "model_fields", {})
        )
        assert field in table.get(owner, {}), f"{owner}.{field} declares a bound the table does not carry"


def test_the_table_carries_each_bound_and_default_as_the_model_declares_it() -> None:
    table = project_values()
    assert table["UISettings"]["page_transition_duration"] == {
        "default": 200, "min": 50, "max": 1000, "integer": True,
    }
    assert table["UISettings"]["element_stagger_ms"] == {
        "default": 30, "min": 10, "max": 200, "integer": True,
    }
    assert table["UISettings"]["idle_timeout_seconds"] == {
        "default": 0, "min": 0, "max": 86400, "integer": True,
    }
    for model in ("UIElement", "MonitorConfig"):
        assert table[model]["display_decimals"] == {
            "default": None, "min": project_loader.DISPLAY_DECIMALS_MIN,
            "max": project_loader.DISPLAY_DECIMALS_MAX, "integer": True,
        }
    assert "MasterElement" not in table


def test_every_panel_setting_has_its_blank_in_the_table() -> None:
    """The panel fills a blank setting itself, since a Builder draft reaches
    it without the loader's defaults: each one the models give a plain value."""
    table = project_values()["UISettings"]
    for field, info in project_loader.UISettings.model_fields.items():
        if info.default_factory is not None:
            continue
        assert table[field]["default"] == info.default, field
    assert table["element_stagger_style"] == {"default": "fade-up"}


def test_a_bound_the_table_cannot_say_stops_the_generator() -> None:
    """A rule the table has no word for (here, a value above but never at its
    end) is refused rather than dropped: dropped, the Builder would offer the
    value at that end and the save would refuse it."""

    class Odd(BaseModel):
        level: int = Field(default=1, gt=0)

    with pytest.raises(UnreadableBoundError, match="Odd.level"):
        project_values(roots=(Odd,))


def test_the_panel_module_imports_to_the_same_table() -> None:
    """The panel's copy is an ES module a browser can load as it stands."""
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    module = REPO_ROOT / "openavc" / "web" / "panel" / "modules" / "project_values.gen.js"
    script = (
        "const m = await import(process.argv[1]);"
        "console.log(JSON.stringify(m.PROJECT_VALUES));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script, module.as_uri()],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == project_values()
