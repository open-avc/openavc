"""Render the project models' bounds and defaults into one table, in two files.

What a project value may hold and what a blank one is are written once, on the
models in ``project_loader.py``: each range as the field's ``Field(ge=, le=)``,
each blank as the field's default. This walks every model a project is built
from and writes the table to
``openavc/web/programmer/src/api/projectValues.gen.ts`` (the Builder's fields)
and ``openavc/web/panel/modules/project_values.gen.js`` (the panel, which draws
the Builder's unsaved draft, a project that never passed the loader).

The table carries every field that declares a bound, under the model that
declares it, and every panel setting with a plain default, since the panel
fills a blank setting itself; a blank another setting decides (a model's
``BLANK_FROM``: the theme a project with no theme id draws, the format a clock
with none draws) carries that setting's values too. A bound the table has no
word for stops the generator rather than being left out: left out, the Builder
would offer a value the save refuses.

Data only. Run:  python -m openavc.core.project_values_gen
A test compares both committed files against a fresh render, so a bound changed
without regenerating, or a file edited by hand, fails CI.
"""

from __future__ import annotations

import json
import typing
from pathlib import Path
from typing import Any

import annotated_types
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from openavc.core.project_loader import ProjectConfig, UISettings, blank_rule

TS_ARTIFACT = "openavc/web/programmer/src/api/projectValues.gen.ts"
PANEL_ARTIFACT = "openavc/web/panel/modules/project_values.gen.js"

#: Models whose every plain default is in the table, bounded or not.
SETTINGS_MODELS: tuple[type[BaseModel], ...] = (UISettings,)

#: The bound each ``annotated_types`` constraint becomes in the table.
_BOUND_KEYS = {annotated_types.Ge: ("min", "ge"), annotated_types.Le: ("max", "le")}

_PLAIN = (str, int, float, bool, type(None))


class UnreadableBoundError(ValueError):
    """A field's rule is one the table cannot carry."""


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    """The project models an annotation names (``list[X]``, ``X | None`` ...)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [m for arg in typing.get_args(annotation) for m in _models_in(arg)]


def _base_models(model: type[BaseModel]) -> list[type[BaseModel]]:
    return [
        base for base in model.__mro__[1:]
        if isinstance(base, type) and issubclass(base, BaseModel) and base is not BaseModel
    ]


def _walk(roots: tuple[type[BaseModel], ...]) -> list[type[BaseModel]]:
    """Every model reachable from ``roots``, and each one's base models (where
    an inherited field is listed), each once, in a fixed order."""
    seen: dict[str, type[BaseModel]] = {}
    pending = list(roots)
    while pending:
        model = pending.pop()
        if model.__name__ in seen:
            continue
        seen[model.__name__] = model
        pending.extend(_base_models(model))
        for info in model.model_fields.values():
            pending.extend(_models_in(info.annotation))
    return [seen[name] for name in sorted(seen)]


def _rule(info: FieldInfo) -> tuple[Any, ...]:
    return (info.annotation, info.metadata, info.default)


def _declares(model: type[BaseModel], field: str) -> bool:
    """True when the model gives ``field`` its own rule: a field a base model
    gives it unchanged is listed under that base model, once."""
    for base in _base_models(model):
        inherited = base.model_fields.get(field)
        if inherited is not None:
            return _rule(inherited) != _rule(model.model_fields[field])
    return True


def _is_integer(annotation: Any) -> bool:
    if annotation is int:
        return True
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    return bool(args) and all(a is int for a in args)


def _bounds(model: type[BaseModel], field: str, info: FieldInfo) -> dict[str, Any]:
    bounds: dict[str, Any] = {}
    for rule in info.metadata:
        keys = _BOUND_KEYS.get(type(rule))
        if keys is None:
            raise UnreadableBoundError(
                f"{model.__name__}.{field} declares {rule!r}, which the project-value "
                f"table cannot carry; teach openavc/core/project_values_gen.py its word"
            )
        bounds[keys[0]] = getattr(rule, keys[1])
    return bounds


def _plain_default(info: FieldInfo) -> dict[str, Any]:
    """``{"default": ...}`` when the field's blank is one plain value, else ``{}``."""
    if info.is_required() or info.default_factory is not None:
        return {}
    return {"default": info.default} if isinstance(info.default, _PLAIN) else {}


def _blank_from(model: type[BaseModel], field: str) -> dict[str, Any]:
    """``{"blank_from": {setting: {its value: this field's}}, "otherwise":
    word}`` when another setting decides this field's blank (``BLANK_FROM``,
    read by ``project_loader.blank_rule``), else ``{}``. ``otherwise`` is the
    value a word not named reads as: it must name something drawn, so every
    word reads as something, and it must be that setting's default where it has
    one, so a draft that leaves the setting out draws what saving it stores."""
    if field not in getattr(model, "BLANK_FROM", {}):
        return {}
    setting, by_value, *_ = model.BLANK_FROM[field]
    other = model.model_fields.get(setting)
    if other is None:
        raise _unreadable_blank(model, field)
    unnamed = blank_rule(model, field)[2]
    if by_value.get(unnamed) is None or other.default not in (None, unnamed):
        raise _unreadable_blank(model, field)
    return {"blank_from": {setting: dict(by_value)}, "otherwise": unnamed}


def _unreadable_blank(model: type[BaseModel], field: str) -> UnreadableBoundError:
    setting, by_value, *_ = model.BLANK_FROM[field]
    drawn = sorted(v for v in by_value.values() if v is not None)
    return UnreadableBoundError(
        f"{model.__name__}.{field}'s blank follows {setting!r}, which is not a setting "
        f"of {model.__name__} whose default, or the word its rule names instead, draws one of {drawn}"
    )


def _entry(model: type[BaseModel], field: str, info: FieldInfo, settings: bool) -> dict[str, Any] | None:
    bounds = _bounds(model, field, info)
    blank = _plain_default(info)
    if bounds:
        return {**blank, **bounds, "integer": _is_integer(info.annotation)}
    follows = _blank_from(model, field)
    if follows:
        return {**blank, **follows}
    if settings and blank:
        return blank
    return None


def project_values(roots: tuple[type[BaseModel], ...] = (ProjectConfig,)) -> dict[str, dict[str, Any]]:
    """The table: model name -> field -> its default, min, max and integer."""
    table: dict[str, dict[str, Any]] = {}
    for model in _walk(roots):
        settings = model in SETTINGS_MODELS
        fields = {}
        for field, info in model.model_fields.items():
            if not _declares(model, field):
                continue
            entry = _entry(model, field, info, settings)
            if entry is not None:
                fields[field] = entry
        if fields:
            table[model.__name__] = fields
    return table


BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the project models (openavc/core/project_loader.py).
// Regenerate with:  python -m openavc.core.project_values_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// What a project value may hold and what a blank one is, by the model and the
// field the project schema declares it on: `default` is what the schema stores
// for a blank one, `min` and `max` the range it takes (both ends included),
// `integer` that it refuses a fraction. A value outside its range fails the
// whole project save. `blank_from` says what a blank one draws when another
// setting decides it: that setting's value names it (null: that value draws
// none), and a value not named, a blank one included, reads as the one
// `otherwise` names.
"""

TS_BODY = """
/** One project value: what a blank one is, and the range it may hold. */
export interface ProjectValue {
  readonly default?: string | number | boolean | null;
  readonly min?: number;
  readonly max?: number;
  readonly integer?: boolean;
  readonly blank_from?: Readonly<Record<string, Readonly<Record<string, string | null>>>>;
  readonly otherwise?: string;
}

export const PROJECT_VALUES = %(table)s as const satisfies Record<string, Record<string, ProjectValue>>;
"""

PANEL_BODY = """
export const PROJECT_VALUES = %(table)s;
"""

_BODIES = {TS_ARTIFACT: TS_BODY, PANEL_ARTIFACT: PANEL_BODY}

#: The committed files, each rendered from the same table.
ARTIFACTS = tuple(_BODIES)


def render(artifact: str) -> str:
    table = json.dumps(project_values(), indent=2, ensure_ascii=False)
    return BANNER + _BODIES[artifact] % {"table": table}


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    for artifact in ARTIFACTS:
        path = repo_root / artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(artifact), encoding="utf-8", newline="\n")
        print(f"wrote {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
