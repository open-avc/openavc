"""
OpenAVC project file loader.

Loads and validates project.avc using Pydantic models.
All downstream code works with typed ProjectConfig objects, never raw dicts.

The models are the one place a project value's bound and default are written:
each range on its field (``Field(ge=, le=)``), a measurement that takes finite
numbers only as ``FiniteFloat`` (a box's side, a trigger's delay), each blank
as the field's default, a text value's shape as ``PanelPin`` (the panel lock PIN),
or, for a blank another setting decides (the theme a
project with no theme id draws, the format a clock with none draws), in the
model's ``BLANK_FROM``, read by ``blank_drawn``; a style value whose 0 is blank
(a crosspoint cell size) in ``UIElement.ZERO_IS_BLANK``, read by
``style_is_blank``; the blank of a key inside a dict field (a press's hold and
repeat timings) in ``UIElement.KEY_DEFAULTS``; a fixed choice's words, and what a word this release does
not know reads as, as ``OneOf``, read by ``reads_as`` (the word is kept as
written: a later release's project loads here).
``python -m openavc.core.project_values_gen`` renders them into one
table for the Builder (``web/programmer/src/api/projectValues.gen.ts``) and the
panel (``web/panel/modules/project_values.gen.js``), which draws the Builder's
unsaved draft without it ever passing this loader; a test holds both files to
the models.
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator


class _ForwardCompatModel(BaseModel):
    """Base for project schema models.

    `extra="allow"` preserves unknown fields through load/save, so a project
    file written by a newer platform isn't destructively re-saved by an older
    one. Unknown fields land in `__pydantic_extra__` and round-trip through
    `model_dump()`.
    """

    model_config = ConfigDict(extra="allow")

from openavc.utils.logger import get_logger
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.state_store import GLOB_CHARS
from openavc.drivers.registry import get_driver_class
from openavc.ui.page_rules import PAGE_TYPES, UNKNOWN_TYPE_READS_AS

log = get_logger(__name__)


def _reject_glob_metachars(value: str, label: str) -> None:
    """Raise if ``value`` contains an fnmatch metacharacter (the store's
    ``GLOB_CHARS``).

    An id carrying one of these becomes part of a state key (var.<id>,
    ui.<id>.<prop>, device.<id>.*) and then a per-key `state.changed.<key>`
    event name; fnmatch-based subscription dispatch (event_bus, state_store)
    would mis-route or drop notifications for it (e.g. a key `var.a[1]` never
    matches an exact subscription to itself, and `var.a1` wrongly matches a
    subscription to `var.a[1]`).
    """
    bad = [c for c in GLOB_CHARS if c in value]
    if bad:
        raise ValueError(
            f"{label} '{value}' must not contain glob metacharacters "
            f"({', '.join(bad)}) — they break state-change event dispatch"
        )


def _validate_id(value: str, label: str) -> str:
    """Reject IDs that would break state-key parsing or fnmatch dispatch.

    Every config ID becomes a segment of state keys (``<ns>.<id>.<property>``)
    and of the per-key ``state.changed.<key>`` events. A dot would split the
    key; a glob metacharacter would break fnmatch subscription matching. Shared
    by every ID-bearing model so the rule can't drift between them.
    """
    if "." in value:
        raise ValueError(
            f"{label} '{value}' must not contain dots (used as state key separator)"
        )
    _reject_glob_metachars(value, label)
    return value


# --- Pydantic Models ---
#
# A field that takes a range of numbers says so on the field (``Field(ge=,
# le=)``), and one that takes finite numbers only is a ``FiniteFloat``: the
# schema refuses a value outside it, whoever sends it. A bound added to a field
# a project could already hold comes with a format migration step that brings a
# saved value inside it, so a project an earlier release
# saved still loads; a door that writes into the running project without going
# back through the schema checks the field the same way (the AI's element
# doors validate each assignment).

# A readout's decimal places, on a control and on a monitored reading alike:
# the most the panel's labels and gauges ever drew.
DISPLAY_DECIMALS_MIN = 0
DISPLAY_DECIMALS_MAX = 20

# An overlay's or sidebar's width and height, as a percentage of the screen:
# the range the UI Builder's fields offer. At 0 or less the panel drew the
# dialog as nothing over a dimmed screen.
OVERLAY_BOX_MIN = 5
OVERLAY_BOX_MAX = 100

# The least a trigger's delay, debounce or cooldown, or a macro's own cooldown,
# may hold, in seconds; each is a finite number too. The engines waited no time
# for a negative one, as for 0. The macro lint reads it for a trigger's
# (``core/macro_validation.py``).
SECONDS_MIN = 0

# THE panel lock PIN: what the panel's lock screen can take, the digits 0 to 9
# and at most six of them; blank is no lock. A PIN outside it could never be
# typed there, so that panel could never be unlocked at the glass. The unlock
# route takes an attempt by the same rule (``api/models.py``), and the
# Builder's field and the lock screen's box read it from the generated table.
# Written ``[0-9]``, never ``\d``: Python reads ``\d`` as any script's digits,
# a browser as these ten.
PANEL_PIN_MAX_DIGITS = 6
PanelPin = Annotated[str, Field(max_length=PANEL_PIN_MAX_DIGITS, pattern=r"^[0-9]*$")]


@dataclass(frozen=True)
class OneOf:
    """THE fixed-choice rule, declared on a text field (``Annotated[str,
    OneOf(...)]``): the words this release knows, and what a word it does not
    know reads as (``otherwise``; None reads as nothing at all: a trigger of a
    type this release does not know never fires). Any text is taken and kept as
    written, so a project a later release saved loads here and goes back to it
    unchanged; the load says what each such word does here
    (``core/saved_values.py``), and an edit that adds one is refused.
    ``reads_as`` is the one reading of the field, in the engines and here; the
    panel and the Builder read the same table (``project_values_gen.py``)."""

    words: tuple[str, ...]
    otherwise: str | None


def choice_rule(model: type[BaseModel], field: str) -> OneOf:
    """``field``'s ``OneOf`` on ``model``."""
    return next(r for r in model.model_fields[field].metadata if isinstance(r, OneOf))


def reads_as(model: type[BaseModel], field: str, word: Any) -> str | None:
    """THE word a fixed-choice ``field`` of ``model`` holding ``word`` reads as:
    the word itself when this release knows it, else the field's ``otherwise``
    (a blank one included)."""
    rule = choice_rule(model, field)
    return word if isinstance(word, str) and word in rule.words else rule.otherwise


_OVERLAP_WORDS = ("skip", "queue", "allow")

# A blank another setting decides is declared in its model's ``BLANK_FROM``:
# field -> (that setting, its value -> what this field draws), with a third
# entry, the value a word not named reads as, where that setting's own default
# is None. Left out, it is that setting's default. A drawn None is a value that
# draws no such thing.


def blank_rule(model: type[BaseModel], field: str) -> tuple[str, dict[str, Any], Any]:
    """``field``'s ``BLANK_FROM`` rule as (the setting that decides it, its
    value -> what the field draws, the value a word not named reads as)."""
    setting, by_value, *otherwise = getattr(model, "BLANK_FROM")[field]
    unnamed = otherwise[0] if otherwise else model.model_fields[setting].default
    return setting, by_value, unnamed


def blank_drawn(model: type[BaseModel], field: str, word: Any) -> Any:
    """THE value a blank ``field`` of ``model`` draws, when the deciding
    setting holds ``word``. The panel and the Builder read the same rule from
    the generated table (``project_values_gen.py``)."""
    _, by_value, unnamed = blank_rule(model, field)
    return by_value[word if isinstance(word, str) and word in by_value else unnamed]


class ProjectMeta(_ForwardCompatModel):
    id: str
    name: str
    description: str = ""
    created: str = ""
    modified: str = ""
    # Which project this is, made when it is made (core/project_identity),
    # never by the model: a default here would stamp every file that comes
    # in, and no two would ever read as the same project.
    uuid: str = ""


class ChildEntityConfig(_ForwardCompatModel):
    """Project-side metadata for one child entity owned by a device.

    The runtime state for child entities lives in the state store under
    ``device.<parent>.<child_type>.<local_id_padded>.<property>``; this
    model only persists the bits that the project author controls (user
    label, freeform config). Driver-controlled state (online flag, signal
    presence, etc.) is never persisted in the project file.
    """
    label: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class DeviceConfig(_ForwardCompatModel):
    id: str
    driver: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    pending_settings: dict[str, Any] = Field(default_factory=dict)
    # child_entities keyed by child_type -> local_id_padded -> ChildEntityConfig.
    # Empty for devices whose drivers don't declare child_entity_types. The
    # key is the padded string form (matching the state-store convention)
    # so a controller with encoder 5 looks like
    #   {"encoder": {"005": {"label": "Lobby TX"}}}
    child_entities: dict[str, dict[str, ChildEntityConfig]] = Field(
        default_factory=dict
    )

    @field_validator("id")
    @classmethod
    def id_no_dots(cls, v: str) -> str:
        return _validate_id(v, "Device ID")


class DeviceGroup(_ForwardCompatModel):
    id: str
    name: str
    device_ids: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def id_no_dots(cls, v: str) -> str:
        return _validate_id(v, "Device group ID")


class VariableValidation(_ForwardCompatModel):
    """Optional validation rules for a variable."""
    min: float | None = None  # number type: minimum value
    max: float | None = None  # number type: maximum value
    allowed: list[str] | None = None  # string type: allowed values (enum)


class VariableConfig(_ForwardCompatModel):
    id: str
    type: str = "string"

    @field_validator("id")
    @classmethod
    def id_no_dots(cls, v: str) -> str:
        return _validate_id(v, "Variable ID")
    default: Any = None
    label: str = ""
    description: str = ""  # freeform text explaining the variable's purpose
    persist: bool = False  # save value to disk, restore on restart
    source_key: str | None = None  # auto-sync from this state key
    source_map: dict[str, Any] | None = None  # value mapping for source
    validation: VariableValidation | None = None  # optional validation rules


class MonitorStateEntry(_ForwardCompatModel):
    """One value of a monitored reading: what it is called, and whether it is normal.

    Borrowed in SHAPE from the panel's ``show.look.states`` -- a map keyed by
    value, each entry carrying a label -- so tagging a reading uses the
    authoring muscle memory that already exists. Not borrowed in CONTENT: the
    panel's entries also carry ``bg_color``, ``icon`` and ``text_color``, which
    are theme and brand decisions. A room's accent colour is not a severity, so
    a monitor state says only what a person needs read to them and whether it
    means everything is fine.

    ``normal`` unset is unset, not False: a state entry that only names a value
    ("0" is "Mic") is vocabulary, and declaring vocabulary must not silently
    turn every other value into an alert.
    """
    label: str = ""
    normal: bool | None = None


class MonitorConfig(_ForwardCompatModel):
    """One monitored reading: a state key this room says matters.

    Written by both authoring doors -- the State tab's Monitor button and the
    same block on a device's live state -- into this ONE project-level list, so
    the local Dashboard, the cloud card and the alert all compile from a single
    declaration and cannot disagree about what "bad" means.

    Everything except ``key`` is optional, and the common case writes nothing
    else: type, label, unit and range are already declared by the driver or the
    variable, and the IDE fills them in when the reading is tagged. What the
    author supplies is only what is genuinely their judgement -- what counts as
    normal in *this* room, and how long it has to be wrong before somebody is
    told.

    With no limits declared this is informational: the tile shows the value,
    its label and its age, drawn neutral, and nothing fires.
    """
    key: str
    label: str = ""      # empty falls back to the key
    unit: str = ""       # "dB", "hours", "%" -- shown beside the value
    type: str = "string"  # string | integer | number | boolean | enum | float
    #: A number's normal range. Either end alone is a one-sided limit.
    normal_min: float | None = None
    normal_max: float | None = None
    #: Decimal places for a numeric reading, the same word and the same meaning
    #: the panel's slider/fader/gauge/label carry. Unset is "as reported", not
    #: zero: a float32 crossing a float64 wire reads 0.08000000566244125, and
    #: the room that cares says so once, here, for the tile, the cloud health
    #: card and the alert together. The bound is the element field's.
    display_decimals: int | None = Field(
        default=None, ge=DISPLAY_DECIMALS_MIN, le=DISPLAY_DECIMALS_MAX,
    )
    #: Value -> word, and which values are normal (booleans, enums, strings).
    states: dict[str, MonitorStateEntry] = Field(default_factory=dict)
    #: How long the reading must be outside normal before it fires. 0 fires at
    #: once. This is not decoration: a projector that is off is correct at 3am
    #: and a problem ten minutes into a lecture, and a mute held four seconds is
    #: somebody pressing a button. Without it, monitoring a boolean is noise.
    duration_seconds: float = 0

    @field_validator("key")
    @classmethod
    def key_is_a_state_key(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Monitor key must not be empty")
        return v


class StepCondition(_ForwardCompatModel):
    """Condition for conditional steps and skip_if guards."""
    key: str
    operator: str = "eq"  # eq, ne, gt, lt, gte, lte, truthy, falsy
    value: Any = None


class MacroStep(_ForwardCompatModel):
    action: str  # one of macro_validation.BUILTIN_STEP_ACTIONS, or a plugin-registered action
    # Fields used by different action types (all optional, validated at runtime)
    device: str | None = None
    group: str | None = None  # group.command: target device group ID
    command: str | None = None
    params: dict[str, Any] | None = None
    seconds: float | None = None
    key: str | None = None
    value: Any = None
    macro: str | None = None
    event: str | None = None
    payload: dict[str, Any] | None = None
    page: str | None = None  # ui.navigate: target page id, or "$back" / "$dismiss" to pop overlay
    description: str | None = None  # human-readable step description (for progress display)

    # Conditional step fields (action == "conditional")
    condition: StepCondition | None = None
    then_steps: list["MacroStep"] | None = None
    else_steps: list["MacroStep"] | None = None

    # wait_until step fields (action == "wait_until")
    # timeout: seconds to wait before giving up; None means never time out
    # on_timeout: "fail" (blank, and a word this release does not know) raises and triggers
    # stop_on_error handling; "continue" proceeds silently
    timeout: float | None = None
    on_timeout: Annotated[str | None, OneOf(("fail", "continue"), otherwise="fail")] = None

    # Step-level guard: skip this step if condition is true
    skip_if: StepCondition | None = None

    # Device offline guard: skip device.command if device is disconnected
    skip_if_offline: bool = False


class TriggerConfig(_ForwardCompatModel):
    """Trigger definition — when should a macro fire automatically."""
    id: str
    type: Annotated[str, OneOf(("schedule", "state_change", "event", "startup"), otherwise=None)]

    @field_validator("id")
    @classmethod
    def id_no_dots(cls, v: str) -> str:
        return _validate_id(v, "Trigger ID")
    enabled: bool = True

    # Schedule
    cron: str | None = None

    # State change
    state_key: str | None = None
    state_operator: str | None = None  # "any", "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy"
    state_value: Any = None

    # Event
    event_pattern: str | None = None

    # Execution control, each a number of seconds, 0 or more
    delay_seconds: FiniteFloat = Field(default=0, ge=SECONDS_MIN)
    debounce_seconds: FiniteFloat = Field(default=0, ge=SECONDS_MIN)
    cooldown_seconds: FiniteFloat = Field(default=0, ge=SECONDS_MIN)
    overlap: Annotated[str, OneOf(_OVERLAP_WORDS, otherwise="skip")] = "skip"

    # Guard conditions
    conditions: list[StepCondition] = Field(default_factory=list)


class MacroConfig(_ForwardCompatModel):
    id: str
    name: str

    @field_validator("id")
    @classmethod
    def id_no_dots(cls, v: str) -> str:
        return _validate_id(v, "Macro ID")
    steps: list[MacroStep] = Field(default_factory=list)
    triggers: list[TriggerConfig] = Field(default_factory=list)
    stop_on_error: bool = False
    cancel_group: str | None = None  # macros in the same group preempt each other
    # Macro-level throttle, enforced at the engine's execute() chokepoint so it
    # holds no matter how the macro is fired (script, REST, AI, UI, trigger,
    # another macro). Defaults leave the historic concurrent behaviour: allow.
    # A trigger may still carry its own overlap/cooldown; the two stack and the
    # stricter wins.
    overlap: Annotated[str, OneOf(_OVERLAP_WORDS, otherwise="allow")] = "allow"
    cooldown_seconds: FiniteFloat = Field(default=0, ge=SECONDS_MIN)


class Placement(_ForwardCompatModel):
    """Where an element sits, as a percentage of its parent box.

    The parent is the page, or a container element the child names via
    ``UIElement.parent``. Percentages mean a panel drawn once looks the same on
    any screen of the same shape, which whole-cell grid coordinates could never
    promise.

    Each side is a finite number: one that is not would be written to the file
    as a bare ``NaN`` or ``Infinity``, which no browser reads. A negative one is
    taken: a container's child may sit past its edge on purpose, and a
    negative size is Validate's to report.
    """

    x: FiniteFloat = 0.0
    y: FiniteFloat = 0.0
    w: FiniteFloat = 100.0
    h: FiniteFloat = 100.0


class Layout(_ForwardCompatModel):
    """One arrangement of a page's elements.

    The elements themselves live on the page and are shared by every layout, so
    adding a control adds it everywhere. A layout only says where things sit,
    and a secondary layout only has to say what *moved* -- anything it leaves
    out is inherited from the layout named in ``inherits``.
    """

    id: str
    # A word this release does not know matches no screen: the layout is drawn
    # only as the primary, when it is the page's primary.
    orientation: Annotated[str, OneOf(("landscape", "portrait"), otherwise=None)] = "landscape"
    primary: bool = False
    inherits: str | None = None
    placements: dict[str, Placement] = Field(default_factory=dict)
    hidden: list[str] = Field(default_factory=list)


def normalize_primary_layout(layouts: list[Layout]) -> list[Layout]:
    """Settle a page's layouts on exactly one primary.

    The runtime picks a layout by orientation and falls back to the primary when
    nothing matches, so a page with no primary (or with several) has no answer
    for an unmatched screen. Rather than reject the file, settle it: an empty
    list gets a landscape primary, and if nobody is marked, the first one is it.

    It lives out here rather than inside the validator because anything that
    edits ``page.layouts`` in place -- the AI tools add and remove arrangements
    -- has to re-settle it, and assignment does not re-run validation.
    """
    if not layouts:
        return [Layout(id="landscape", orientation="landscape", primary=True)]
    primaries = [lay for lay in layouts if lay.primary]
    if not primaries:
        layouts[0].primary = True
    elif len(primaries) > 1:
        for extra in primaries[1:]:
            extra.primary = False
    return layouts


class SnapConfig(_ForwardCompatModel):
    """Authoring-only snap increment, in percent.

    This is a ruler, not a container: changing it -- or switching it off --
    never moves anything that is already placed. The defaults are the old
    12x8 grid's spacing, so a fresh page still behaves the way it used to.
    """

    enabled: bool = True
    x: float = 100.0 / 12
    y: float = 100.0 / 8


class ElementGrant(_ForwardCompatModel):
    """What an iframe element (a custom control or a plugin panel element) may
    reach in the room.

    Set by the integrator who places the element, not declared by whoever wrote
    the code inside it -- the person putting it on the page is the one who knows
    what it should touch. Devices and variables are named by id, and the same
    list covers both directions: a control that may read `dsp1` may command it.

    Absent (the default) means no grant: the element renders, is themed, and
    gets its config, but sees no state and sends nothing.
    """

    devices: list[str] = Field(default_factory=list)  # device ids it may read and command
    variables: list[str] = Field(default_factory=list)  # variable ids it may read and write
    macros: bool = False  # may run any macro in the project
    navigate: bool = False  # may move the panel to another page


class UIElement(_ForwardCompatModel):
    id: str
    type: str  # "button", "label", "slider", "status_led", "page_nav", etc.
    label: str | None = None
    text: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    output_min: float | None = None  # device-side minimum (for range scaling)
    output_max: float | None = None  # device-side maximum (for range scaling)
    scale_to_full: bool | None = None  # True: scale display to fill track; False: show dead space
    response: str | None = None  # slider/fader taper: "linear" (default) or "logarithmic"
    response_db_range: float | None = None  # logarithmic taper: dB span of the throw (default 60)
    send_on_release: bool | None = None  # slider/fader: send only when the drag ends, not continuously
    send_throttle_ms: int | None = None  # slider/fader: min ms between live sends (default per element)
    display_decimals: int | None = Field(  # slider/fader/gauge/label: decimal places shown in the value readout
        default=None, ge=DISPLAY_DECIMALS_MIN, le=DISPLAY_DECIMALS_MAX,
    )
    target_page: str | None = None
    # page_nav: lit (accent fill) while its target page or overlay is on this
    # panel's screen. Absent is off, so a nav saved before it existed is unchanged.
    highlight_current: bool | None = None
    options: list[dict[str, Any]] | None = None
    placeholder: str | None = None
    src: str | None = None
    # How an image element fits its box (CSS object-fit). The Builder writes it
    # and the panel reads it; like thumb_size before it, it round-tripped on
    # extra="allow" alone until it was declared. image_fit below is the same
    # idea for a button's background image -- two fields, two element types.
    object_fit: str | None = None  # image: cover, contain, fill
    preset_number: int | None = None
    icon: str | None = None  # Lucide icon name or assets:// reference
    icon_position: str | None = None  # left, right, top, bottom, center
    # Sizes are fractional on purpose: the layout engine stores them as rem
    # (px / 14), so 24px lands as 1.7142857.... An int here would reject every
    # migrated value.
    icon_size: float | None = None  # rem (default 24px-equivalent)
    icon_color: str | None = None  # hex color, inherits text_color if not set
    display_mode: str | None = None  # text, icon_text, icon_only, image, image_text
    button_image: str | None = None  # asset ref or URL for the button image
    image_fit: str | None = None  # cover, contain, fill
    image_blend_mode: str | None = None  # CSS blend mode (multiply, screen, etc.) or "mask"
    image_opacity: float | None = None  # 0.0-1.0
    frameless: bool | None = None  # hide bg_color, border, box_shadow (image-as-button look)
    # Element-specific properties (vary by type, all optional)
    unit: str | None = None  # gauge, fader: unit label (°F, dB, %)
    arc_angle: float | None = None  # gauge: arc sweep in degrees
    zones: list[dict[str, Any]] | None = None  # gauge: color zones
    orientation: str | None = None  # fader, level_meter: vertical/horizontal
    clock_mode: str | None = None  # clock: time/date/datetime/countdown/elapsed/meeting
    # clock: format string; blank is kept as written, what it draws is BLANK_FROM's
    format: str | None = None
    timezone: str | None = None  # clock: IANA timezone
    target_time: str | None = None  # clock: countdown target
    start_key: str | None = None  # clock: elapsed timer start state key
    duration_minutes: int | None = None  # clock: meeting timer duration
    digits: int | None = None  # keypad: max input digits
    # keypad: submit at `digits` keys, and after auto_send_delay_ms with no key press
    auto_send: bool | None = None
    # keypad: the wait after the last key before a shorter entry is submitted (1500)
    auto_send_delay_ms: int | None = None
    keypad_style: str | None = None  # keypad: numeric/phone
    show_display: bool | None = None  # keypad: show digit buffer display
    mask_display: bool | None = None  # keypad: the display shows a dot per digit
    label_position: str | None = None  # group: label position
    list_style: str | None = None  # list: static/selectable/multi_select/action
    item_height: float | None = None  # list: row height in rem (see icon_size)
    # Read by the slider renderer as element.thumb_size, honoured per-element
    # and per-theme, but never declared here — it only round-tripped because
    # the base model allows extras. Declared now so it is part of the contract.
    thumb_size: float | None = None  # slider/fader: thumb size in rem
    items: list[dict[str, Any]] | None = None  # list/select: static items
    matrix_config: dict[str, Any] | None = None  # matrix: inputs/outputs/labels/route pattern
    matrix_style: str | None = None  # matrix: crosspoint/list
    # Plugin element fields
    plugin_type: str | None = None  # plugin-defined element type name
    plugin_id: str | None = None  # which plugin provides this element
    plugin_config: dict[str, Any] = Field(default_factory=dict)
    # Custom control fields: a page the integrator wrote themselves, living in
    # the project's ui/ tree and rendered in a sandboxed iframe. The file is a
    # relative path inside that tree ("room_map/index.html"); the config is
    # handed to it verbatim in the opening message, the way plugin_config is.
    custom_file: str | None = None
    custom_config: dict[str, Any] = Field(default_factory=dict)
    # What this element may reach in the room. Only the two iframe types read
    # it; every other element's reach is its bindings. See ElementGrant.
    grant: ElementGrant | None = None
    # Geometry lives in the page's layouts, not on the element, so the same
    # control can sit in different places in the landscape and portrait
    # arrangements without being duplicated.
    parent: str | None = None  # container element id, or None for page-level
    aspect_lock: float | None = None  # width/height ratio to hold under stretch
    # Power-user hook: names one or more classes the project stylesheet
    # (ui.custom_css) can target. The renderer puts them on the element's node,
    # and the UI Builder's styling editor writes both halves.
    css_class: str | None = None
    # A word this control passes to whatever it runs, so one macro can serve a
    # whole array of buttons. A press hands it to the macro as $trigger.tag
    # alongside $trigger.element, which is what lets nine keypad buttons share
    # one macro instead of needing nine. Free-form and never interpreted here:
    # "1", "hdmi2" and "row_3" are all the same to the platform.
    tag: str | None = None
    # Authoring-time protection: a locked element cannot be dragged, resized,
    # nudged or deleted in the Builder. It has no runtime meaning -- the panel
    # renders a locked element exactly like any other -- but it has to live in
    # the project, or pinning the background art survives until the next reload.
    locked: bool = False
    style: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[str, Any] = Field(default_factory=dict)

    #: A blank format is the clock's mode's (blank_rule has the shape). A
    #: duration (countdown, elapsed, meeting) reads none: it is always hours,
    #: minutes and seconds. A mode not named, a blank one included, draws as
    #: the time.
    BLANK_FROM: ClassVar[dict[str, tuple[str, dict[str, str | None], str]]] = {
        "format": ("clock_mode", {
            "time": "h:mm A", "date": "MMM D, YYYY", "datetime": "MMM D, YYYY h:mm A",
            "countdown": None, "elapsed": None, "meeting": None,
        }, "time"),
    }

    #: Keys of a dict field whose 0 is a blank value, never a size: the
    #: control draws its own, as with none written (style_is_blank reads it).
    #: A crosspoint cell_size of 0 is the cell that fits the box.
    ZERO_IS_BLANK: ClassVar[dict[str, frozenset[str]]] = {"style": frozenset({"cell_size"})}

    #: What a blank key inside a dict field is, by its path in the field.
    #: A press's timings, in milliseconds, on the first action of
    #: ``bindings.do.press`` (the one carrying the press's mode): a
    #: hold-repeat sends its press again every ``hold_repeat_ms``, and a
    #: tap-and-hold is a long press once held ``hold_threshold_ms``. One that
    #: is no number above 0 (not written, 0, a negative one, words) is the
    #: default here. The panel's press and the Builder's fields read them
    #: from the generated table.
    KEY_DEFAULTS: ClassVar[dict[str, dict[str, Any]]] = {
        "bindings": {"do.press.hold_repeat_ms": 200, "do.press.hold_threshold_ms": 500},
    }

    @field_validator("id")
    @classmethod
    def id_no_dots(cls, v: str) -> str:
        return _validate_id(v, "UI element ID")

    @model_validator(mode="after")
    def matrix_config_is_shaped_right(self) -> "UIElement":
        """Say so in the log when a matrix will draw less than it says.

        Logged rather than raised, deliberately. The write doors refuse this
        (``ui_tools._reject_bad_matrix_config``), so a bad shape can only reach
        here from a hand-edited file, an import, or a project written before the
        check existed -- and a project that cannot be OPENED is a far worse
        outcome than a matrix that draws six rows where eight were written.
        This is also the one place every door passes through, which is why it
        lives on the model rather than in ``load_project``.
        """
        if self.type == "matrix" and self.matrix_config:
            from openavc.ui.matrix_model import matrix_config_problems

            for problem in matrix_config_problems(
                self.matrix_config, where=f"'{self.id}' matrix_config",
            ):
                log.warning("Matrix element %s", problem)
        return self


def style_is_blank(key: str, value: Any) -> bool:
    """THE read of a value in a control's ``style``: blank, the control drawing
    its own size, when none is written or when it is a 0 the element declares
    blank (``UIElement.ZERO_IS_BLANK``). The panel and the Builder read the same
    rule from the generated table (``project_values_gen.py``)."""
    if value is None:
        return True
    zero_blank = key in UIElement.ZERO_IS_BLANK["style"]
    return zero_blank and isinstance(value, int | float) and value == 0


class PageBackground(_ForwardCompatModel):
    color: str | None = None
    image: str | None = None  # asset reference e.g. "assets://bg.jpg"
    image_opacity: float = 1.0
    image_size: Annotated[str, OneOf(("cover", "contain", "stretch"), otherwise="cover")] = "cover"
    image_position: str = "center"
    gradient: dict[str, Any] | None = None  # {type, angle, from, to}


class OverlayConfig(_ForwardCompatModel):
    # Percentages of the viewport, not pixels. An overlay used to be the one
    # part of a panel still measured in hard px, which is why it was the first
    # thing to look wrong on a different screen. Blank is the panel's default
    # box for the page type.
    width: float | None = Field(default=None, ge=OVERLAY_BOX_MIN, le=OVERLAY_BOX_MAX)
    height: float | None = Field(default=None, ge=OVERLAY_BOX_MIN, le=OVERLAY_BOX_MAX)
    position: str = "center"
    backdrop: str = "dim"
    dismiss_on_backdrop: bool = True
    animation: str = "fade"
    side: str | None = None


class UIPage(_ForwardCompatModel):
    id: str
    name: str
    # How the page is shown, the page rules' words (ui/page_rules.py): a page in
    # place of the one on screen, or an overlay or sidebar over it. A word this
    # release does not know is drawn as a page, as every reader draws it.
    page_type: Annotated[str, OneOf(PAGE_TYPES, otherwise=UNKNOWN_TYPE_READS_AS)] = "page"
    # How this page is drawn. "elements" is the ordinary page: the renderer lays
    # out the elements below. "custom" hands the whole screen to a page the
    # integrator wrote themselves, in the project's ui/ tree, in one sandboxed
    # frame -- the same machinery a `custom` ELEMENT runs in, sized to the page
    # instead of to a box. The elements below are kept and are not drawn, so
    # switching back restores the page exactly.
    render_mode: Annotated[str, OneOf(("elements", "custom"), otherwise="elements")] = "elements"
    # Only read when render_mode is "custom", and the same two fields a custom
    # element carries: a relative path inside ui/, and settings handed over in
    # the opening message.
    custom_file: str | None = None
    custom_config: dict[str, Any] = Field(default_factory=dict)
    # What the author's page may reach in the room. Same shape and same picker
    # as an element's -- a whole screen is not a reason for a second grant model.
    grant: ElementGrant | None = None
    overlay: OverlayConfig | None = None
    background: PageBackground | None = None
    snap: SnapConfig = Field(default_factory=SnapConfig)
    elements: list[UIElement] = Field(default_factory=list)
    layouts: list[Layout] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def id_no_dots(cls, v: str) -> str:
        return _validate_id(v, "UI page ID")

    @model_validator(mode="after")
    def ensure_one_primary_layout(self) -> "UIPage":
        """A page always has exactly one primary layout to fall back to."""
        self.layouts = normalize_primary_layout(self.layouts)
        return self


class UISettings(_ForwardCompatModel):
    theme: str = "dark"
    # Empty is kept as written; what it draws is drawn_theme_id's answer.
    theme_id: str = ""
    theme_overrides: dict[str, Any] = Field(default_factory=dict)
    accent_color: str = ""
    font_family: str = ""
    lock_code: PanelPin = ""
    # 0 is off. At most a day: the panel's timer cannot hold a delay much past
    # 24 days, and one that overflows fires at once, after every touch.
    idle_timeout_seconds: int = Field(default=0, ge=0, le=86400)
    idle_page: str = "main"
    # orientation removed in 0.8.0 -- it was one string for the whole project,
    # which is exactly what stopped a project serving a landscape wall panel and
    # a portrait phone at the same time. Orientation is now a layout property.
    page_transition: str = "none"
    # The old page stays on screen under the new one until this runs out.
    page_transition_duration: int = Field(default=200, ge=50, le=1000)
    element_entry: str = "none"
    element_stagger_ms: int = Field(default=30, ge=10, le=200)
    element_stagger_style: str = "fade-up"
    # A failed press says why, on the glass, for about five seconds. On by
    # default because the room that needs it most belongs to somebody who never
    # thought about it: press Power on a projector that is not there and the
    # alternative is a panel that does nothing and explains nothing. Off is for
    # a room that draws its own status and would rather ours kept quiet.
    show_error_messages: bool = True

    #: A blank setting another setting decides (blank_rule has the shape). A
    #: blank theme_id is the theme the light or dark `theme` names (the setting
    #: a project carried before themes had ids); a `theme` not named here reads
    #: as the default `theme`.
    BLANK_FROM: ClassVar[dict[str, tuple[str, dict[str, str]]]] = {
        "theme_id": ("theme", {"light": "light-modern", "dark": "dark-default"}),
    }

    def drawn_theme_id(self) -> str:
        """THE theme this project draws: its ``theme_id``, else the one
        ``BLANK_FROM`` gives for its ``theme``. The panel and the Builder read
        the same rule from the generated table."""
        return self.theme_id or blank_drawn(type(self), "theme_id", self.theme)


class MasterElement(UIElement):
    """An element repeated across pages.

    It carries its own placements rather than borrowing a page's, because it has
    to be valid on every page it appears on. They are percentages of the
    viewport, keyed by orientation, and the runtime picks one the same way it
    picks a page layout.
    """

    pages: str | list[str] = "*"
    placements: dict[str, Placement] = Field(default_factory=dict)
    hidden: bool = False


class PageGroup(_ForwardCompatModel):
    name: str
    pages: list[str] = Field(default_factory=list)  # list of page IDs


class UIConfig(_ForwardCompatModel):
    settings: UISettings = Field(default_factory=UISettings)
    # The project stylesheet, and the other half of element.css_class. It sits
    # here rather than in settings on purpose: settings is a flat bag of small
    # scalar knobs that the IDE and the AI tools patch and merge wholesale, and
    # a stylesheet is a document that can run to kilobytes. As a peer of pages it
    # diffs on its own, reads as content rather than configuration, and a partial
    # merge of settings can never mangle it.
    custom_css: str = ""
    pages: list[UIPage] = Field(default_factory=list)
    master_elements: list[MasterElement] = Field(default_factory=list)
    page_groups: list[PageGroup] = Field(default_factory=list)


_SCRIPT_ID_RE = re.compile(r"^[a-z0-9_]+$")


class ScriptConfig(_ForwardCompatModel):
    id: str
    file: str
    enabled: bool = True
    description: str = ""

    @field_validator("id")
    @classmethod
    def id_is_safe(cls, v: str) -> str:
        # Align the project-load path with the REST create path
        # (api/models.py ScriptCreateRequest). The id is used as a state-key
        # segment, a sys.modules key, and a thread name, so an imported
        # project must not carry ids the API would reject (slashes, spaces,
        # dots, uppercase). Lowercase alphanumeric + underscore only.
        if not v or not _SCRIPT_ID_RE.fullmatch(v):
            raise ValueError(
                f"Script ID '{v}' must be lowercase alphanumeric with underscores "
                f"(^[a-z0-9_]+$)"
            )
        return v


class ISCConfig(_ForwardCompatModel):
    enabled: bool = False
    shared_state: list[str] = Field(default_factory=list)
    auth_key: str = ""
    peers: list[str] = Field(default_factory=list)  # Manual peer addresses, e.g. ["192.168.1.10:8080"]
    # Glob allowlist (matched against "<device_id>.<command>") for device
    # commands a remote peer may execute on this instance. Empty = deny all
    # remote commands; authenticating to the mesh does not by itself grant
    # device control. Example: ["projector1.*", "*.power_off"], or ["*"] for all.
    allowed_remote_commands: list[str] = Field(default_factory=list)


class DriverDependency(_ForwardCompatModel):
    """A driver required by this project (auto-populated on save)."""
    driver_id: str
    driver_name: str = ""
    version: str = ""
    source: Annotated[str, OneOf(("builtin", "community", "unknown", ""), otherwise="unknown")] = ""


class PluginDependency(_ForwardCompatModel):
    """A plugin required by this project (auto-populated on save)."""
    plugin_id: str
    plugin_name: str = ""
    version: str = ""
    source: Annotated[str, OneOf(("community", "unknown", ""), otherwise="unknown")] = ""
    platforms: list[str] = Field(default_factory=lambda: ["all"])


class PluginConfig(_ForwardCompatModel):
    """Configuration for a single plugin in the project file."""
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class DisplaySettings(_ForwardCompatModel):
    """How this room's panel behaves when nobody is using it.

    The motivation is burn-in: a panel pinned on one page around the clock
    leaves a ghost of that page on the screen, and a dimmer picture ages it
    more slowly. Nothing here ever puts the display to SLEEP -- measured on
    appliance hardware 2026-09-04, a slept screen does not come back on a real
    finger (only the power button woke it), which on a wall-mounted panel is
    indistinguishable from broken. `dim_level_percent: 0` gets the same LOOK
    safely: the backlight goes to the panel's floor and the content is covered
    in black, so it reads as off across a room while every touch still works.
    """

    # On by default. A panel showing one page all day is the case this exists
    # for, and leaving it off meant every unit needed a visit to switch it on.
    idle_dim_enabled: bool = True
    # Seconds of no touch before the panel dims. Clamped 30..7200 where read.
    idle_dim_timeout_seconds: int = 300
    # Percent OF THE PANEL'S CONFIGURED BRIGHTNESS, not of full scale, so a
    # panel already turned down for a dark room dims further where an absolute
    # level would brighten it. 0 means blackout (see the class docstring);
    # otherwise clamped 1..90 where read.
    idle_dim_level_percent: int = 20
    # Whether the touch that wakes a dim panel also presses what is under it.
    # Off means the first touch only restores brightness, so nobody mutes a
    # live room by tapping a screen they could not read.
    idle_dim_wake_passes_touch: bool = False
    # Optional state key that holds the panel bright while it is truthy. A
    # meeting generates no touches, so a bare timer dims mid-presentation.
    # The same key holds back a panel software update (updater/panel_software.py).
    idle_dim_hold_state_key: str = ""
    # The panel's normal brightness, 1..100. None means "not managed" -- the
    # panel keeps whatever its own maintenance control set, which is what
    # every panel does until somebody sets this.
    brightness_percent: int | None = None

    def hold_engaged(self, state) -> bool:
        """Whether the hold key is set and its value reads on right now: the
        one reading of "the space is in use" this platform has.

        A key nothing has written yet, or a store that cannot answer, reads
        not-held. For the dim that is the safe side, since a touch undoes a
        dim where a stuck hold disables the feature for good.
        """
        key = str(self.idle_dim_hold_state_key or "").strip()
        if not key or state is None:
            return False
        try:
            return bool(state.get(key))
        except Exception:
            return False


class DeviceSettings(_ForwardCompatModel):
    """Deployment-wide device behaviour a project carries to every panel."""

    # Seconds between reconnect attempts for a device that has gone offline
    # for a network reason. This is the rate of connection attempts an IT
    # department sees, so it is a property of the SITE rather than of one box
    # -- which is why it travels with the project. None defers to the
    # instance's own system.json. Clamped 1..300 where read.
    reconnect_interval_seconds: float | None = None


class ProjectSettings(_ForwardCompatModel):
    """Settings that travel WITH the project, to every panel it is pushed to.

    A customer with a hundred panels sets these once and deploys one template;
    before this block they were per-box, so every panel needed a visit.

    What may live here is bounded by one rule, and it is a security rule
    rather than a taste one: **a project file is a MOVABLE artifact.** It
    arrives from a cloud template, an import, a backup restore, and the AI. So
    a setting belongs here only when its worst case is "the panel looks
    wrong" -- recoverable by anyone standing in front of it. Anything whose
    worst case is "the box cannot be reached" or "the wrong people can reach
    it" stays in system.json and never travels: `network` (a pushed
    Accept connections from strands every panel at once, with nothing on them
    able to undo it), `auth` (credentials), `cloud.system_key` (that key IS the
    device's identity -- copied to a hundred panels they would all share one),
    and `tls` (certificates are per-device). `updates.channel` is deliberately
    excluded too: it is a real fleet lever and belongs in cloud fleet
    operations, not hidden inside a room file where a restored old backup
    could move a site back onto beta with nobody noticing.

    tests/test_project_settings.py pins that list, so adding a field here has
    to argue against the rule rather than slip past it.
    """

    display: DisplaySettings = Field(default_factory=DisplaySettings)
    devices: DeviceSettings = Field(default_factory=DeviceSettings)


class ProjectConfig(_ForwardCompatModel):
    # The version stamped on a freshly-created project, so it isn't immediately
    # "migrated" on reload. Taken FROM project_migration rather than written out
    # again: it was a literal with a "keep in sync" comment, and it did not stay
    # in sync. A stale default here is not cosmetic -- a project written with it
    # gets the whole 0.7->0.8 migration re-run over an already-0.8 body on its
    # next save, which collapses every placement and re-divides every rem value.
    # (project_migration imports nothing from here, so this is not a cycle.)
    openavc_version: str = CURRENT_VERSION
    project: ProjectMeta
    # Settings the project carries to every panel it is deployed to. See
    # ProjectSettings for what may live here and, more importantly, what may
    # not.
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    devices: list[DeviceConfig] = Field(default_factory=list)
    device_groups: list[DeviceGroup] = Field(default_factory=list)
    connections: dict[str, dict[str, Any]] = Field(default_factory=dict)
    driver_dependencies: list[DriverDependency] = Field(default_factory=list)
    plugin_dependencies: list[PluginDependency] = Field(default_factory=list)
    plugins: dict[str, PluginConfig] = Field(default_factory=dict)
    variables: list[VariableConfig] = Field(default_factory=list)
    # Readings this room says matter, from both authoring doors. A peer of
    # variables rather than a field on one, because most of what a room's
    # health actually is -- lamp hours, fault flags, signal presence, a DSP's
    # temperature -- lives on device.* state, which has nowhere to carry a flag.
    monitors: list[MonitorConfig] = Field(default_factory=list)
    macros: list[MacroConfig] = Field(default_factory=list)
    ui: UIConfig = Field(default_factory=UIConfig)
    scripts: list[ScriptConfig] = Field(default_factory=list)
    isc: ISCConfig = Field(default_factory=ISCConfig)

    @field_validator("plugins")
    @classmethod
    def plugin_ids_no_dots(cls, v: dict[str, PluginConfig]) -> dict[str, PluginConfig]:
        for pid in v:
            if "." in pid:
                raise ValueError(f"Plugin ID '{pid}' must not contain dots (used as state key separator)")
        return v


# --- Loader Functions ---


def load_project(path: str | Path, *, keep_notes: bool = False, running: ProjectMeta | None = None) -> ProjectConfig:
    """
    Load and validate a project.avc file.

    Automatically migrates older project formats to the current version.
    With ``keep_notes``, what the migration changed and found is kept beside
    the file for the Programmer's notice (``core/project_update.py``), before
    the migrated file is saved: the next load has nothing left to migrate.

    Args:
        path: Path to the project.avc file.
        keep_notes: Keep the migration's notes. The boot's load and a reload
            do; the first boot's seed does not.
        running: The project running, for a file read in its place (a
            reload, a replace): a file without a stamp that is that project
            keeps its stamp (``core/project_identity.stamp_missing``).

    Returns:
        Validated ProjectConfig object.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        json.JSONDecodeError: If the file isn't valid JSON.
        pydantic.ValidationError: If the JSON doesn't match the schema.
    """
    from openavc.core import project_update
    from openavc.core.project_migration import migrate_project

    path = Path(path)
    log.info(f"Loading project from {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))

    # Apply migrations if needed
    notes = project_update.MigrationNotes()
    raw, was_migrated = migrate_project(raw, notes)

    project = ProjectConfig(**raw)
    if keep_notes:
        project_update.record(path.parent, notes)
    log.info(
        f'Loaded project "{project.project.name}" '
        f"({len(project.devices)} devices, {len(project.macros)} macros, "
        f"{len(project.ui.pages)} pages)"
    )

    # A project saved before projects were stamped, or the starter a first
    # start copied in, is given its stamp once (core/project_identity).
    from openavc.core import project_identity
    stamped = project_identity.stamp_missing(project, running)

    # Persist migrated format so we don't re-migrate next time
    if was_migrated:
        log.info("Saving migrated project format to disk")
        from openavc.core.installed_equipment import InstalledEquipment
        project = InstalledEquipment.from_environment().compose(project, for_load=True)
        save_project(path, project)
    elif stamped:
        _save_stamp(path, project)

    return project


def _save_stamp(path: Path, project: ProjectConfig) -> None:
    """The stamp a load gave ``project`` written into its file, so the next
    start reads the same one. Whatever stops the write (a file that cannot
    be written, equipment this server refuses), the project loads as it
    would have, with the stamp in the running project until its next save:
    a load that raised here would have a start put a backup in its place."""
    from openavc.core.installed_equipment import InstalledEquipment

    try:
        save_project(path, InstalledEquipment.from_environment().compose(project.model_copy(deep=True), for_load=True))
    except Exception as error:
        log.warning(f"The project's stamp could not be written to its file: {error}")




def build_driver_dependencies(project: ProjectConfig) -> list[DriverDependency]:
    """Scan project devices and build the driver dependency list."""

    seen: set[str] = set()
    deps: list[DriverDependency] = []

    for device in project.devices:
        driver_id = device.driver
        if driver_id in seen:
            continue
        seen.add(driver_id)

        driver_class = get_driver_class(driver_id)
        if driver_class:
            info = driver_class.DRIVER_INFO
            deps.append(DriverDependency(
                driver_id=driver_id,
                driver_name=info.get("name", ""),
                version=info.get("version", ""),
                source=_get_driver_source(driver_id),
            ))
        else:
            # Driver not installed — still record the dependency
            deps.append(DriverDependency(
                driver_id=driver_id,
                source="unknown",
            ))

    return deps


def _definition_files(definitions_dir: Path) -> list[Path]:
    """Driver-definition files a tree serves (skips ``_``-prefixed helpers)."""
    files: list[Path] = []
    for pattern in ("*.avcdriver", "*.py"):
        for f in definitions_dir.glob(pattern):
            if f.name.startswith("_"):
                continue
            files.append(f)
    return files


def _definition_signature(definitions_dir: Path) -> tuple:
    """A change digest for a definitions tree: each served file's name, mtime,
    and size.

    Adding, removing, or editing a file changes it. This is used to key the
    scan cache instead of the directory's own mtime, because a directory's
    mtime is not a reliable change signal for an in-place child add on Windows
    (so a dir-mtime key serves a stale answer there).
    """
    entries = []
    for f in _definition_files(definitions_dir):
        st = f.stat()
        entries.append((f.name, st.st_mtime_ns, st.st_size))
    return tuple(sorted(entries))


@lru_cache(maxsize=8)
def _scan_builtin_driver_ids(definitions_dir: str, _signature: tuple) -> frozenset[str]:
    """Read the driver ids declared by the files in ``definitions_dir``.

    Keyed on ``_signature`` (the served files' names, mtimes, and sizes) as
    well as the path, so the cache can never serve a stale answer: any add,
    remove, or edit changes the signature, and a different directory is a
    different key. Resolve by each file's DECLARED id rather than its filename
    stem — the two can differ.
    """
    from openavc.drivers.driver_loader import driver_id_from_file

    ids: set[str] = set()
    for f in _definition_files(Path(definitions_dir)):
        driver_id = driver_id_from_file(f)
        if driver_id:
            ids.add(driver_id)
    return frozenset(ids)


def _builtin_driver_ids() -> frozenset[str]:
    """Driver ids served by the built-in definitions tree.

    The expensive part — opening and parsing each definition file — is cached
    on the tree's file signature, so the per-call cost is a directory listing
    plus a stat per file rather than a full re-parse.
    """
    from openavc.system_config import DRIVER_DEFINITIONS_DIR

    try:
        signature = _definition_signature(DRIVER_DEFINITIONS_DIR)
    except OSError:
        return frozenset()
    return _scan_builtin_driver_ids(str(DRIVER_DEFINITIONS_DIR), signature)


def _get_driver_source(driver_id: str) -> str:
    """Classify an already-registered driver as builtin or community.

    Callers reach here only for ids found in the driver registry, so a driver
    that the built-in definitions tree does not serve was necessarily loaded
    from driver_repo.

    This must stay cheap. It runs once per unique driver on every save; the
    per-call disk cost is a directory listing plus a stat per built-in
    definition file (the signature key), never a re-parse. The previous
    implementation globbed and YAML-parsed the entire driver library per
    call — O(unique_drivers x library_files), which cost 6.4s to save a
    30-driver project against a 151-driver library.
    """
    if driver_id in _builtin_driver_ids():
        return "builtin"
    return "community"


def build_plugin_dependencies(project: ProjectConfig) -> list[PluginDependency]:
    """Scan project plugins and build the plugin dependency list."""
    from openavc.core.plugin_loader import get_plugin_registry

    deps: list[PluginDependency] = []
    registry = get_plugin_registry()

    for plugin_id in project.plugins:
        plugin_class = registry.get(plugin_id)
        if plugin_class:
            info = plugin_class.PLUGIN_INFO
            deps.append(PluginDependency(
                plugin_id=plugin_id,
                plugin_name=info.get("name", ""),
                version=info.get("version", ""),
                source=_get_plugin_source(plugin_id),
                platforms=info.get("platforms", ["all"]),
            ))
        else:
            deps.append(PluginDependency(
                plugin_id=plugin_id,
                source="unknown",
            ))

    return deps


def _get_plugin_source(plugin_id: str) -> str:
    """Determine if a plugin is community or user-created."""
    from openavc.system_config import PLUGIN_REPO_DIR
    plugin_repo_dir = PLUGIN_REPO_DIR
    if plugin_repo_dir.is_dir():
        plugin_dir = plugin_repo_dir / plugin_id
        if plugin_dir.is_dir():
            return "community"
    return "unknown"


def build_default_plugin_config(schema: dict) -> dict:
    """Build default configuration from a plugin's CONFIG_SCHEMA.

    A field that is not a dict, or a group's ``fields`` that is not, is passed
    over: the plugin is refused when it starts, with a sentence naming it
    (``plugin_declarations.config_schema_problem``), and an enable builds
    this config before the start."""
    config: dict = {}
    for key, field in _schema_fields(schema):
        if field.get("type") == "group":
            config[key] = build_default_plugin_config(field.get("fields", {}))
        elif field.get("type") == "mapping_list":
            config[key] = field.get("default", [])
        elif "default" in field:
            config[key] = field["default"]
    return config


def get_plugin_setup_fields(schema: dict) -> dict:
    """Return fields that need user input before the plugin can start.
    A field that is not a dict is passed over, as the default config does."""
    return {
        key: field for key, field in _schema_fields(schema)
        if field.get("required", False) and "default" not in field
    }


def _schema_fields(schema: Any) -> list[tuple[str, dict]]:
    """The fields of a plugin schema that are dicts; none when it is not one."""
    if not isinstance(schema, dict):
        return []
    return [(key, field) for key, field in schema.items() if isinstance(field, dict)]


import threading

# Serialize concurrent writes to the project file.
# This is a threading lock (not asyncio) because save_project() is sync
# and may be called from both async and sync contexts.
_project_save_lock = threading.Lock()


def crash_copy_path(path: str | Path) -> Path:
    """THE single rolling crash-protection copy ``save_project`` writes before
    every save: ``project.avc.bak`` for ``project.avc``, ``main-hall.avc.bak``
    for ``main-hall.avc``. One per project file, overwritten each time, so it is
    not a backup anybody chose; ``backup_manager.list_backups`` asks this for the
    name to leave out."""
    return Path(path).with_suffix(".avc.bak")


def refresh_crash_copy(path: str | Path) -> None:
    """Make the crash copy of ``path`` the file as it is now saved.

    For a save that moved device logins out of the project: the copy taken
    before that save is the file with the logins in it, and it would keep
    them until the next save. The save has already landed, so the copy of
    what it replaced has done its job."""
    path = Path(path)
    with _project_save_lock:
        if path.exists() and crash_copy_path(path).exists():
            shutil.copy2(path, crash_copy_path(path))


class ProjectSaveError(OSError):
    """A project save that failed. Its text is what the Programmer shows,
    which may quote the operating system and the file's path; ``reason`` is
    the same answer without either, for a reader off this system. ``cause``
    is the operating system's error, which a change that was not applied
    words its own why from (``api/error_messages.describe_change_failure``)."""

    def __init__(self, cause: OSError) -> None:
        from openavc.api.error_messages import friendly_save_error, save_error_reason

        super().__init__(friendly_save_error(cause))
        self.cause = cause
        self.reason = save_error_reason(cause)


def project_text(project: ProjectConfig) -> str:
    """THE text a project file holds for ``project``: the installed
    equipment composed in, and the driver and plugin dependency lists
    derived from it, stamped onto ``project`` in place, so the running
    project matches the bytes written. ``save_project`` writes it, and so
    does the swap a replace stages (``core/project_replace``)."""
    from openavc.core.installed_equipment import InstalledEquipment
    project.devices = InstalledEquipment.from_environment().compose(project).devices

    # Auto-populate driver dependencies
    try:
        project.driver_dependencies = build_driver_dependencies(project)
    except (KeyError, ValueError, AttributeError):
        log.debug("Could not build driver dependencies (non-critical)")

    # Auto-populate plugin dependencies
    try:
        project.plugin_dependencies = build_plugin_dependencies(project)
    except (KeyError, ValueError, AttributeError):
        log.debug("Could not build plugin dependencies (non-critical)")

    return json.dumps(project.model_dump(mode="json"), indent=4, ensure_ascii=False)


def save_project(path: str | Path, project: ProjectConfig) -> None:
    """
    Save a ProjectConfig back to a JSON file atomically.

    Auto-populates driver_dependencies from project devices
    (``project_text``). Creates a .avc.bak crash-protection copy before
    overwriting the existing file.

    Uses write-to-temp-then-rename for crash safety: if the process dies
    mid-write, the original file remains intact.

    Args:
        path: Path to write the file.
        project: The project configuration to save.
    """
    path = Path(path)
    content = project_text(project)

    with _project_save_lock:
        # Crash-protection backup — single rolling copy before each write
        if path.exists():
            try:
                shutil.copy2(path, crash_copy_path(path))
            except OSError as e:
                log.error(f"Cannot create crash-protection backup — aborting save: {e}")
                raise ProjectSaveError(e) from e

        # Atomic write: write to temp file in the same directory, then rename.
        # os.replace() is atomic on both Windows (NTFS) and Linux.
        fd = None
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".avc.tmp", prefix=".save_"
            )
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            fd = None
            os.replace(tmp_path, str(path))
            tmp_path = None  # Successfully replaced, don't clean up
        except OSError as e:
            log.exception("Failed to save project atomically")
            raise ProjectSaveError(e) from e
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    log.info(f"Saved project to {path}")


async def save_project_async(path: str | Path, project: ProjectConfig) -> None:
    """Async-safe save: run the blocking save_project() in a worker thread.

    save_project() does sync disk I/O (a full-file backup copy + a
    write-temp-then-rename) under a threading.Lock, and it's called from
    ~50 async REST/WS/cloud handlers (add/edit/delete device, plugin config,
    applying pending settings, AI/cloud config push). Calling it directly on
    the event loop stalls the entire async server — no request, WS broadcast,
    device poll, or cloud message progresses — for the duration of two
    full-file writes on a potentially large project. Offloading to a thread
    keeps the loop responsive; the threading.Lock inside save_project() still
    serializes concurrent writers, now across worker threads.

    Async callers should use this. The sync save_project() remains for sync
    contexts (startup load/migrate, project-library open).
    """
    await asyncio.to_thread(save_project, path, project)
