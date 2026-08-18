"""
OpenAVC project file loader.

Loads and validates project.avc using Pydantic models.
All downstream code works with typed ProjectConfig objects, never raw dicts.
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _ForwardCompatModel(BaseModel):
    """Base for project schema models.

    `extra="allow"` preserves unknown fields through load/save, so a project
    file written by a newer platform isn't destructively re-saved by an older
    one. Unknown fields land in `__pydantic_extra__` and round-trip through
    `model_dump()`.
    """

    model_config = ConfigDict(extra="allow")

from openavc.utils.logger import get_logger
from openavc.drivers.registry import get_driver_class

log = get_logger(__name__)


# fnmatch metacharacters. An id carrying one of these becomes part of a state
# key (var.<id>, ui.<id>.<prop>, device.<id>.*) and then a per-key
# `state.changed.<key>` event name; fnmatch-based subscription dispatch
# (event_bus, state_store) would mis-route or drop notifications for it
# (e.g. a key `var.a[1]` never matches an exact subscription to itself, and
# `var.a1` wrongly matches a subscription to `var.a[1]`).
_GLOB_METACHARS = "*?["


def _reject_glob_metachars(value: str, label: str) -> None:
    """Raise if ``value`` contains an fnmatch metacharacter."""
    bad = [c for c in _GLOB_METACHARS if c in value]
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


class ProjectMeta(_ForwardCompatModel):
    id: str
    name: str
    description: str = ""
    created: str = ""
    modified: str = ""


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
    action: str  # "device.command", "group.command", "delay", "state.set", "macro", "event.emit", "conditional", "wait_until", "ui.navigate"
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
    # on_timeout: "fail" (default) raises and triggers stop_on_error handling; "continue" proceeds silently
    timeout: float | None = None
    on_timeout: Literal["fail", "continue"] | None = None

    # Step-level guard: skip this step if condition is true
    skip_if: StepCondition | None = None

    # Device offline guard: skip device.command if device is disconnected
    skip_if_offline: bool = False


class TriggerConfig(_ForwardCompatModel):
    """Trigger definition — when should a macro fire automatically."""
    id: str
    type: Literal["schedule", "state_change", "event", "startup"]

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

    # Execution control
    delay_seconds: float = 0
    debounce_seconds: float = 0
    cooldown_seconds: float = 0
    overlap: Literal["skip", "queue", "allow"] = "skip"

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
    overlap: Literal["skip", "queue", "allow"] = "allow"
    cooldown_seconds: float = 0


class Placement(_ForwardCompatModel):
    """Where an element sits, as a percentage of its parent box.

    The parent is the page, or a container element the child names via
    ``UIElement.parent``. Percentages mean a panel drawn once looks the same on
    any screen of the same shape, which whole-cell grid coordinates could never
    promise.
    """

    x: float = 0.0
    y: float = 0.0
    w: float = 100.0
    h: float = 100.0


class Layout(_ForwardCompatModel):
    """One arrangement of a page's elements.

    The elements themselves live on the page and are shared by every layout, so
    adding a control adds it everywhere. A layout only says where things sit,
    and a secondary layout only has to say what *moved* -- anything it leaves
    out is inherited from the layout named in ``inherits``.
    """

    id: str
    orientation: Literal["landscape", "portrait"] = "landscape"
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
    display_decimals: int | None = None  # slider/fader/gauge/label: decimal places shown in the value readout
    target_page: str | None = None
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
    format: str | None = None  # clock: format string (h:mm A)
    timezone: str | None = None  # clock: IANA timezone
    target_time: str | None = None  # clock: countdown target
    start_key: str | None = None  # clock: elapsed timer start state key
    duration_minutes: int | None = None  # clock: meeting timer duration
    digits: int | None = None  # keypad: max input digits
    auto_send: bool | None = None  # keypad: auto-submit after max digits
    auto_send_delay_ms: int | None = None  # keypad: delay before auto-submit
    keypad_style: str | None = None  # keypad: numeric/phone
    show_display: bool | None = None  # keypad: show digit buffer display
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
    # Authoring-time protection: a locked element cannot be dragged, resized,
    # nudged or deleted in the Builder. It has no runtime meaning -- the panel
    # renders a locked element exactly like any other -- but it has to live in
    # the project, or pinning the background art survives until the next reload.
    locked: bool = False
    style: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[str, Any] = Field(default_factory=dict)

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


class PageBackground(_ForwardCompatModel):
    color: str | None = None
    image: str | None = None  # asset reference e.g. "assets://bg.jpg"
    image_opacity: float = 1.0
    image_size: Literal["cover", "contain", "stretch"] = "cover"
    image_position: str = "center"
    gradient: dict[str, Any] | None = None  # {type, angle, from, to}


class OverlayConfig(_ForwardCompatModel):
    # Percentages of the viewport, not pixels. An overlay used to be the one
    # part of a panel still measured in hard px, which is why it was the first
    # thing to look wrong on a different screen.
    width: float | None = None
    height: float | None = None
    position: str = "center"
    backdrop: str = "dim"
    dismiss_on_backdrop: bool = True
    animation: str = "fade"
    side: str | None = None


class UIPage(_ForwardCompatModel):
    id: str
    name: str
    page_type: str = "page"
    # How this page is drawn. "elements" is the ordinary page: the renderer lays
    # out the elements below. "custom" hands the whole screen to a page the
    # integrator wrote themselves, in the project's ui/ tree, in one sandboxed
    # frame -- the same machinery a `custom` ELEMENT runs in, sized to the page
    # instead of to a box. The elements below are kept and are not drawn, so
    # switching back restores the page exactly.
    render_mode: Literal["elements", "custom"] = "elements"
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
    theme_id: str = ""
    theme_overrides: dict[str, Any] = Field(default_factory=dict)
    accent_color: str = ""
    font_family: str = ""
    lock_code: str = ""
    idle_timeout_seconds: int = 0
    idle_page: str = "main"
    # orientation removed in 0.8.0 -- it was one string for the whole project,
    # which is exactly what stopped a project serving a landscape wall panel and
    # a portrait phone at the same time. Orientation is now a layout property.
    page_transition: str = "none"
    page_transition_duration: int = 200
    element_entry: str = "none"
    element_stagger_ms: int = 30
    element_stagger_style: str = "fade-up"
    # A failed press says why, on the glass, for about five seconds. On by
    # default because the room that needs it most belongs to somebody who never
    # thought about it: press Power on a projector that is not there and the
    # alternative is a panel that does nothing and explains nothing. Off is for
    # a room that draws its own status and would rather ours kept quiet.
    show_error_messages: bool = True


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
        if not v or not _SCRIPT_ID_RE.match(v):
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
    source: Literal["builtin", "community", "unknown", ""] = ""


class PluginDependency(_ForwardCompatModel):
    """A plugin required by this project (auto-populated on save)."""
    plugin_id: str
    plugin_name: str = ""
    version: str = ""
    source: Literal["community", "unknown", ""] = ""
    platforms: list[str] = Field(default_factory=lambda: ["all"])


class PluginConfig(_ForwardCompatModel):
    """Configuration for a single plugin in the project file."""
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class ProjectConfig(_ForwardCompatModel):
    # Keep in sync with project_migration.CURRENT_VERSION — the default stamped
    # on a freshly-created project so it isn't immediately "migrated" on reload.
    # A stale default here is not cosmetic: a project written with it gets the
    # whole 0.7->0.8 migration re-run over an already-0.8 body on its next
    # save, which collapses every placement and re-divides every rem value.
    openavc_version: str = "0.11.0"
    project: ProjectMeta
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


def load_project(path: str | Path) -> ProjectConfig:
    """
    Load and validate a project.avc file.

    Automatically migrates older project formats to the current version.

    Args:
        path: Path to the project.avc file.

    Returns:
        Validated ProjectConfig object.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        json.JSONDecodeError: If the file isn't valid JSON.
        pydantic.ValidationError: If the JSON doesn't match the schema.
    """
    from openavc.core.project_migration import migrate_project

    path = Path(path)
    log.info(f"Loading project from {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))

    # Apply migrations if needed
    raw, was_migrated = migrate_project(raw)

    project = ProjectConfig(**raw)
    log.info(
        f'Loaded project "{project.project.name}" '
        f"({len(project.devices)} devices, {len(project.macros)} macros, "
        f"{len(project.ui.pages)} pages)"
    )

    # Persist migrated format so we don't re-migrate next time
    if was_migrated:
        log.info("Saving migrated project format to disk")
        save_project(path, project)

    return project




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
    """Build default configuration from a plugin's CONFIG_SCHEMA."""
    config: dict = {}
    for key, field in schema.items():
        if field.get("type") == "group":
            config[key] = build_default_plugin_config(field.get("fields", {}))
        elif field.get("type") == "mapping_list":
            config[key] = field.get("default", [])
        elif "default" in field:
            config[key] = field["default"]
    return config


def get_plugin_setup_fields(schema: dict) -> dict:
    """Return fields that need user input before the plugin can start."""
    return {
        key: field for key, field in schema.items()
        if field.get("required", False) and "default" not in field
    }


import threading

# Serialize concurrent writes to the project file.
# This is a threading lock (not asyncio) because save_project() is sync
# and may be called from both async and sync contexts.
_project_save_lock = threading.Lock()


def save_project(path: str | Path, project: ProjectConfig) -> None:
    """
    Save a ProjectConfig back to a JSON file atomically.

    Auto-populates driver_dependencies from project devices.
    Creates a .avc.bak crash-protection copy before overwriting
    the existing file.

    Uses write-to-temp-then-rename for crash safety: if the process dies
    mid-write, the original file remains intact.

    Args:
        path: Path to write the file.
        project: The project configuration to save.
    """
    path = Path(path)

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

    data = project.model_dump(mode="json")
    content = json.dumps(data, indent=4, ensure_ascii=False)

    with _project_save_lock:
        # Crash-protection backup — single rolling copy before each write
        if path.exists():
            try:
                shutil.copy2(path, path.with_suffix(".avc.bak"))
            except OSError as e:
                log.error(f"Cannot create crash-protection backup — aborting save: {e}")
                from openavc.api.error_messages import friendly_save_error
                raise OSError(friendly_save_error(e)) from e

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
            from openavc.api.error_messages import friendly_save_error
            raise OSError(friendly_save_error(e)) from e
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
