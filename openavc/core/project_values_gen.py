"""Render the project models' bounds and defaults into one table, in two files.

What a project value may hold and what a blank one is are written once, on the
models in ``project_loader.py``: each range as the field's ``Field(ge=, le=)``,
each blank as the field's default. This walks every model a project is built
from and writes the table to
``openavc/web/programmer/src/api/projectValues.gen.ts`` (the Builder's fields)
and ``openavc/web/panel/modules/project_values.gen.js`` (the panel, which draws
the Builder's unsaved draft, a project that never passed the loader).

The table carries every field that declares a bound (a range, that it takes
finite numbers only, or a text value's longest and the characters it takes:
the panel lock PIN's ``PanelPin``), under the model that declares it, and
every panel setting with a plain default, since the panel
fills a blank setting itself, and every fixed-choice field (``OneOf``): its
words and what a word it does not know reads as; a blank another setting decides (a model's
``BLANK_FROM``: the theme a project with no theme id draws, the format a clock
with none draws) carries that setting's values too, and a style value whose 0
is blank (a model's ``ZERO_IS_BLANK``: a crosspoint cell size) is listed by its
path, ``style.cell_size``, and so is the default of a key inside a dict field
(a model's ``KEY_DEFAULTS``: a press's timings, ``bindings.do.press.hold_repeat_ms``). A bound the table has no word for stops the
generator rather than being left out: left out, the Builder would offer a value
the save refuses.

Beside it, both files carry the page rules (``ui/page_rules.py``) as
``PAGE_RULES``: the page types, which open over the page on screen, the
navigation words, and the box an overlay draws in for a side it leaves blank.
The Builder's file also carries how a control's id is spelled
(``ui/page_structure.ELEMENT_ID_SPELLING``, ``ELEMENT_ID_SENTENCE``) as
``ELEMENT_ID_RULE`` (``element_id_rule``), the pattern and the sentence its
rename refuses with; the panel names no control.

Data only. Run:  python -m openavc.core.project_values_gen
A test compares both committed files against a fresh render, so a bound changed
without regenerating, or a file edited by hand, fails CI.
"""

from __future__ import annotations

import json
import typing
from typing import Any

import annotated_types
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic.types import AllowInfNan

from openavc.core.project_loader import OneOf, ProjectConfig, UISettings, blank_rule
from openavc.ui.page_rules import page_rules_table
from openavc.ui.page_structure import ELEMENT_ID_SENTENCE, ELEMENT_ID_SPELLING
from openavc.utils.generated import write_artifacts

TS_ARTIFACT = "openavc/web/programmer/src/api/projectValues.gen.ts"
PANEL_ARTIFACT = "openavc/web/panel/modules/project_values.gen.js"

#: Models whose every plain default is in the table, bounded or not.
SETTINGS_MODELS: tuple[type[BaseModel], ...] = (UISettings,)

#: What each rule on a field becomes in the table: an ``annotated_types``
#: constraint its bound, a ``OneOf`` its words and what another reads as.
_RULE_WORDS = {
    annotated_types.Ge: lambda rule: {"min": rule.ge},
    annotated_types.Le: lambda rule: {"max": rule.le},
    annotated_types.MaxLen: lambda rule: {"max_length": rule.max_length},
    OneOf: lambda rule: {"words": list(rule.words), "otherwise": rule.otherwise},
}

#: The table's word for each text pattern a field may declare. A reader
#: cannot run a pattern on what is being typed, only keep what it allows, so
#: each is a rule both readers know.
_PATTERN_WORDS = {r"^[0-9]*$": {"digits": True}}

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


def _bound(rule: Any) -> dict[str, Any] | None:
    """The table's word for one rule on a field, or None when it has none."""
    words = _RULE_WORDS.get(type(rule))
    if words is not None:
        return words(rule)
    if isinstance(rule, AllowInfNan) and not rule.allow_inf_nan:
        return {"finite": True}
    pattern = getattr(rule, "pattern", None)
    if isinstance(pattern, str) and pattern in _PATTERN_WORDS:
        return dict(_PATTERN_WORDS[pattern])
    return None


def _bounds(model: type[BaseModel], field: str, info: FieldInfo) -> dict[str, Any]:
    bounds: dict[str, Any] = {}
    for rule in info.metadata:
        word = _bound(rule)
        if word is None:
            raise UnreadableBoundError(
                f"{model.__name__}.{field} declares {rule!r}, which the project-value "
                f"table cannot carry; teach openavc/core/project_values_gen.py its word"
            )
        bounds.update(word)
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


def _is_text(annotation: Any) -> bool:
    """Text, or text that may be blank (a fixed choice with no default)."""
    return annotation in (str, str | None)


def _entry(model: type[BaseModel], field: str, info: FieldInfo, settings: bool) -> dict[str, Any] | None:
    bounds = _bounds(model, field, info)
    blank = _plain_default(info)
    if bounds and _is_text(info.annotation):
        return {**blank, **bounds}
    if bounds:
        return {**blank, **bounds, "integer": _is_integer(info.annotation)}
    follows = _blank_from(model, field)
    if follows:
        return {**blank, **follows}
    if settings and blank:
        return blank
    return None


def _keys_inside(model: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """The entries for keys inside a dict field, by their path, for the keys
    the model itself declares (a model inheriting them is not listed again):
    ``{"style.cell_size": {"zero_is_blank": True}}`` for ``ZERO_IS_BLANK``,
    ``{"bindings.do.press.hold_repeat_ms": {"default": 200}}`` for
    ``KEY_DEFAULTS``."""
    entries: dict[str, dict[str, Any]] = {}
    for field, keys in _dict_field_keys(model, "ZERO_IS_BLANK").items():
        for key in sorted(keys):
            entries.setdefault(f"{field}.{key}", {})["zero_is_blank"] = True
    for field, defaults in _dict_field_keys(model, "KEY_DEFAULTS").items():
        for key in sorted(defaults):
            entries.setdefault(f"{field}.{key}", {})["default"] = defaults[key]
    return dict(sorted(entries.items()))


def _dict_field_keys(model: type[BaseModel], rule: str) -> dict[str, Any]:
    """The model's own ``rule`` declaration, each field it names checked to be
    a dict field of the model."""
    declared = vars(model).get(rule, {})
    for field in declared:
        info = model.model_fields.get(field)
        if info is None or dict not in (info.annotation, typing.get_origin(info.annotation)):
            raise UnreadableBoundError(
                f"{model.__name__}.{rule} names {field!r}, which is not a dict field "
                f"of {model.__name__}"
            )
    return declared


def project_values(roots: tuple[type[BaseModel], ...] = (ProjectConfig,)) -> dict[str, dict[str, Any]]:
    """The table: model name -> field -> its default, min, max, integer and
    finite, or for text its max_length and digits, or a fixed choice's words
    and otherwise."""
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
        fields.update(_keys_inside(model))
        if fields:
            table[model.__name__] = fields
    return table


BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the project models (openavc/core/project_loader.py) and the
// page rules (openavc/ui/page_rules.py).
// Regenerate with:  python -m openavc.core.project_values_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// What a project value may hold and what a blank one is, by the model and the
// field the project schema declares it on: `default` is what the schema stores
// for a blank one, `min` and `max` the range it takes (both ends included),
// `integer` that it refuses a fraction, `finite` that it refuses a value that
// is no finite number (NaN or infinity). For text, `max_length` is the most
// characters it takes and `digits` that it takes the digits 0 to 9 and nothing
// else. A value outside its range or shape fails the whole project save.
// `blank_from` says what a blank one draws when another setting decides it:
// that setting's value names it (null: that value draws none), and a value not
// named, a blank one included, reads as the one `otherwise` names. A key inside a field (`style.cell_size`) with
// `zero_is_blank` reads a 0 as a blank one, the control drawing its own size,
// never as a size of 0. A key inside `bindings` is a press's timing in
// milliseconds, on the press's first action; one that is no number above 0
// (not written, 0, a negative one, words) is its `default`. A fixed-choice field lists its `words`, and any other
// word, a blank one included, reads as the one `otherwise` names (null: as
// nothing). Such a word is kept as written, never refused.
//
// PAGE_RULES says what a page is: `page_types` the types this release draws,
// `opens_over` those that open over the page on screen in a box of their own,
// and `unknown_type_reads_as` what any other type, or none, reads as. A
// master's `pages` of `every_page` draws on every page. `navigation_words` are
// the moves the panel makes itself, by name (`back` closes the overlay on top
// or goes back a page, `dismiss` only closes the overlay), and a target
// starting with `navigation_prefix` names no page. `overlay_box` is the box,
// as percentages of the screen, each type that opens over draws in for a side
// it leaves blank; a sidebar is always `sidebar_height` tall.
"""

TS_ELEMENT_ID_NOTE = """
// ELEMENT_ID_RULE, from the element id rule (openavc/ui/page_structure.py),
// is how a control's id is spelled: `pattern` is the whole id (a lowercase
// letter, then lowercase letters, digits and underscores), and `sentence`
// what a rename that breaks it is told.
"""

TS_BODY = """
/** One project value: what a blank one is, and the range it may hold. */
export interface ProjectValue {
  readonly default?: string | number | boolean | null;
  readonly min?: number;
  readonly max?: number;
  readonly integer?: boolean;
  readonly finite?: boolean;
  readonly max_length?: number;
  readonly digits?: boolean;
  readonly blank_from?: Readonly<Record<string, Readonly<Record<string, string | null>>>>;
  readonly otherwise?: string | null;
  readonly words?: readonly string[];
  readonly zero_is_blank?: boolean;
}

export const PROJECT_VALUES = %(table)s as const satisfies Record<string, Record<string, ProjectValue>>;

export const PAGE_RULES = %(page_rules)s as const;

export const ELEMENT_ID_RULE = %(element_id_rule)s as const;
"""

PANEL_BODY = """
export const PROJECT_VALUES = %(table)s;

export const PAGE_RULES = %(page_rules)s;
"""

_BODIES = {TS_ARTIFACT: TS_BODY, PANEL_ARTIFACT: PANEL_BODY}

#: The committed files, each rendered from the same table.
ARTIFACTS = tuple(_BODIES)


#: What each file adds to the banner.
_NOTES = {TS_ARTIFACT: TS_ELEMENT_ID_NOTE, PANEL_ARTIFACT: ""}


def element_id_rule() -> dict[str, str]:
    """The element id rule as the Builder reads it: the pattern as a
    JavaScript test writes it (judged whole), and the sentence."""
    return {"pattern": f"^{ELEMENT_ID_SPELLING}$", "sentence": ELEMENT_ID_SENTENCE}


def render(artifact: str) -> str:
    table = json.dumps(project_values(), indent=2, ensure_ascii=False)
    page_rules = json.dumps(page_rules_table(), indent=2, ensure_ascii=False)
    element_id = json.dumps(element_id_rule(), indent=2, ensure_ascii=False)
    return BANNER + _NOTES[artifact] + _BODIES[artifact] % {
        "table": table, "page_rules": page_rules, "element_id_rule": element_id,
    }


def main() -> int:
    return write_artifacts(ARTIFACTS, render)


if __name__ == "__main__":
    raise SystemExit(main())
