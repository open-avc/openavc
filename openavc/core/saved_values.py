"""What every load does to a saved project, whatever its version.

The format chain (``core/project_migration.py``) repairs what a release before
a rule saved, keyed on the project's version. A project can hold the same
things with no step to come: an earlier release that took a value past a bound
can save it into a project stamped with the current format, and a later
release can save a word a fixed-choice field here does not know. So after the
chain, every load that brings a saved project in (``migrate_project``) runs
this, and a project with nothing in it to repair or say passes untouched:

- **A value past a bound** the schema declares today is brought inside it by
  the 0.17.0 step's own repair (``core/bound_migration.py``,
  ``core/box_migration.py``) run with today's numbers, read from the project
  models (``todays_bounds``), and each rewrite is a note, as the step's are.
- **A container link the panel draws wrongly or not at all** (a ``parent``
  naming nothing, a loop of containers, a control inside a button, a master's
  ``parent``) is repaired as ``ui/page_repair.py`` says, each repair a person
  would see a note.
- **A word a fixed-choice field does not know** (``project_loader.OneOf``) is
  kept as written, so the project goes back to the release that saved it
  unchanged, and the load says what it does here: a note per word
  (``unknown_words``). A dependency list's ``source`` is not said: every save
  writes it again.

THE edit rule for such a word is ``refuse_new_words``: an edit to the running
project (a save from the Programmer, an AI tool) that adds one is refused, and
one the running project already holds is kept, so a project from a later
release goes on being edited here. A save is not repaired either: it refuses a
value past a bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from openavc.core.bound_migration import RepairBounds, repair_bounds_0_17
from openavc.core.box_migration import repair_boxes_0_17
from openavc.core.project_loader import (
    DISPLAY_DECIMALS_MAX,
    DISPLAY_DECIMALS_MIN,
    OVERLAY_BOX_MAX,
    OVERLAY_BOX_MIN,
    PANEL_PIN_MAX_DIGITS,
    SECONDS_MIN,
    Layout,
    MacroConfig,
    MacroStep,
    PageBackground,
    TriggerConfig,
    UIPage,
    UISettings,
    choice_rule,
    reads_as,
)
from openavc.core.project_update import CHANGED, MigrationNotes
from openavc.ui.page_repair import repair_page_structure

#: The panel settings each repaired to its range.
_PANEL_SETTINGS = ("page_transition_duration", "element_stagger_ms", "idle_timeout_seconds")


def todays_bounds() -> RepairBounds:
    """The bounds the project models declare now."""

    def ends(field: str) -> tuple[int, int]:
        rules = UISettings.model_fields[field].metadata
        return next(r.ge for r in rules if hasattr(r, "ge")), next(r.le for r in rules if hasattr(r, "le"))

    return RepairBounds(
        decimals=(DISPLAY_DECIMALS_MIN, DISPLAY_DECIMALS_MAX),
        pin_length=PANEL_PIN_MAX_DIGITS,
        seconds_min=SECONDS_MIN,
        overlay_box=(OVERLAY_BOX_MIN, OVERLAY_BOX_MAX),
        panel_settings={field: ends(field) for field in _PANEL_SETTINGS},
    )


def repair_saved_values(data: dict, elements: list[dict], notes: MigrationNotes) -> bool:
    """The project dict ``data`` repaired in place and its words said in
    ``notes``; ``elements`` are its page and master elements. True when a
    value was rewritten, so the load saves the file."""
    rewrites = _rewrites(notes)
    bounds = todays_bounds()
    repair_bounds_0_17(data, elements, notes, bounds)
    ui = data.get("ui")
    contained = False
    if isinstance(ui, dict):
        repair_boxes_0_17(ui, notes, bounds.overlay_box)
        contained = repair_page_structure(ui, notes)
    for word in unknown_words(data):
        notes.found(word.found, word.location, page_id=word.page_id)
    return contained or _rewrites(notes) > rewrites


def _rewrites(notes: MigrationNotes) -> int:
    return sum(1 for n in notes.notes if n.kind == CHANGED)


# --- words a fixed-choice field does not know -----------------------------------

#: What each fixed-choice field a person sets is called, and what a word it
#: does not know does here. ``{layout}`` is the layout's id.
_SAID: dict[tuple[type[BaseModel], str], tuple[str, str]] = {
    (MacroConfig, "overlap"): ("Overlap", "it works as Allow"),
    (MacroStep, "on_timeout"): ("On timeout", "a timeout fails the macro"),
    (TriggerConfig, "type"): ("Trigger type", "the trigger never fires"),
    (TriggerConfig, "overlap"): ("Overlap", "it works as Skip"),
    (UIPage, "render_mode"): ("Contents", "the page draws the controls placed on it"),
    (PageBackground, "image_size"): ("Background picture size", "the picture is drawn as Cover"),
    (Layout, "orientation"): ('Orientation of layout "{layout}"', "no screen is matched to that layout"),
}


@dataclass(frozen=True)
class UnknownWord:
    """A word a fixed-choice field holds that this release does not know."""

    model: type[BaseModel]
    field: str
    word: str
    location: str
    page_id: str = ""
    layout_id: str = ""

    def _named(self) -> str:
        name = _SAID[(self.model, self.field)][0].format(layout=self.layout_id)
        head, _, tail = name.partition(" of ")
        quoted = f"{head} '{self.word}'"
        return f"{quoted} of {tail}" if tail else quoted

    @property
    def found(self) -> str:
        """What the load says: the word, and what it does here."""
        return f"{self._named()} is not one this version of OpenAVC knows, so {_SAID[(self.model, self.field)][1]}."

    @property
    def refusal(self) -> str:
        """What an edit that adds it is told."""
        words = ", ".join(w for w in choice_rule(self.model, self.field).words if w)
        return f"{self._named()} is not one this version of OpenAVC knows. Use: {words}"


def unknown_words(data: dict) -> list[UnknownWord]:
    """Every word in ``data`` (a project as a dict) that a fixed-choice field a
    person sets does not know, in the order the Programmer lists them."""
    found: list[UnknownWord] = []
    for macro in _dicts(data.get("macros")):
        _Words(found, f"Macros > {macro.get('name') or macro.get('id', '')}").macro(macro)
    ui = data.get("ui")
    for page in _dicts(ui.get("pages") if isinstance(ui, dict) else None):
        _Words(found, f"{page.get('name') or page.get('id', '')} > page", str(page.get("id", ""))).page(page)
    return found


class _Words:
    """The words one holder's fields hold, each said at its place."""

    def __init__(self, found: list[UnknownWord], location: str, page_id: str = ""):
        self.found = found
        self.location = location
        self.page_id = page_id

    def check(self, rule: tuple[type[BaseModel], str], holder: Any, location: str = "", layout_id: str = "") -> None:
        """``holder``'s word in the field ``rule`` names, kept when unknown."""
        model, field = rule
        word = holder.get(field) if isinstance(holder, dict) else None
        if _unknown(model, field, word):
            self.found.append(UnknownWord(
                model, field, word, location or self.location, self.page_id, layout_id,
            ))

    def macro(self, macro: dict) -> None:
        self.check((MacroConfig, "overlap"), macro)
        for number, step in enumerate(_dicts(macro.get("steps")), start=1):
            for each in _steps_within(step):
                self.check((MacroStep, "on_timeout"), each, f"{self.location} > step {number}")
        for trigger in _dicts(macro.get("triggers")):
            where = f"{self.location} > {trigger.get('id', '')}"
            self.check((TriggerConfig, "type"), trigger, where)
            self.check((TriggerConfig, "overlap"), trigger, where)

    def page(self, page: dict) -> None:
        self.check((UIPage, "render_mode"), page)
        self.check((PageBackground, "image_size"), page.get("background"))
        for layout in _dicts(page.get("layouts")):
            self.check((Layout, "orientation"), layout, layout_id=str(layout.get("id", "")))


def _unknown(model: type[BaseModel], field: str, word: Any) -> bool:
    """A word the field does not know. A blank is no word: it is not set, and
    reads as the field's default."""
    return isinstance(word, str) and word != "" and reads_as(model, field, word) != word


def _steps_within(step: dict) -> list[dict]:
    """A step and every step its branches hold."""
    steps = [step]
    for branch in ("then_steps", "else_steps"):
        for inner in _dicts(step.get(branch)):
            steps.extend(_steps_within(inner))
    return steps


def _dicts(items: Any) -> list[dict]:
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


# --- an edit that adds one ------------------------------------------------------


class UnknownWordError(ValueError):
    """An edit that adds a word a fixed-choice field does not know."""


def refuse_new_words(running: BaseModel | None, proposed: BaseModel) -> None:
    """Raise ``UnknownWordError`` when ``proposed`` holds a word a fixed-choice
    field does not know that ``running`` does not hold in that field."""
    sections = {"macros": True, "ui": {"pages"}}
    held = set() if running is None else {
        (w.model, w.field, w.word) for w in unknown_words(running.model_dump(mode="json", include=sections))
    }
    for word in unknown_words(proposed.model_dump(mode="json", include=sections)):
        if (word.model, word.field, word.word) not in held:
            raise UnknownWordError(word.refusal)
