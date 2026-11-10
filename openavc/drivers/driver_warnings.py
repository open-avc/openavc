"""The warnings a driver definition draws: rules an author should see that
never block anything.

Everything ``validate_driver_definition`` returns is an error to every one of
its callers (the loader drops the driver, the save routes refuse it, catalog
CI fails the build), so a rule that only wants to say "are you sure?" lives
here. The walk (``avcdriver_semantic.validate_driver_warnings``) asks each
rule in turn and tags what it says; ``validate_driver_issues`` carries the
warnings beside the errors. One function per rule, in the order they report:

* ``masked_default_warnings``: a masked config field with a default
* ``port_open_warnings``: a ``discovery.port_open`` hint the TCP scan can
  never match
* ``unlabeled_param_warnings``: a command parameter with no label

The two per-field rules yield a field's name only with what it draws, so the
walk writes a name into a tag only when there is a warning to tag.
* ``poll_length_warnings``: a poll cycle longer than the poll interval
* ``unwatched_silence_warnings``: a device whose silence nothing notices

The last two read what the driver does at its default config
(``_AtDefaults``) and the numbers the runtime acts on from ``spec``: the line
gap, the transports it applies to, and the transports the poll loop watches
for silence. Some of those reads raise on a value no driver should carry (a
number too large for a float), so each rule reads in the order it always has.

Purity contract: standard library plus the sibling contract modules, like the
rest of the validator, which the community catalog and the SDK run copies of.
"""

from __future__ import annotations

from typing import Any, Iterator

from openavc.drivers.child_ids import declared_child_ids, page_starts
from openavc.drivers.spec import (
    DEFAULT_LINE_GAP_S,
    LINE_GAP_TRANSPORTS,
    LIVENESS_TRANSPORTS,
    PORT_OPEN_TRANSPORTS,
    SILENCE_CHECK_TRANSPORTS,
    is_secret_setting,
)

# The transports that wait `inter_command_delay` after each write themselves;
# the driver adds no gap of its own there.
_DELAY_ONLY_TRANSPORTS = ("udp", "http")


def masked_default_warnings(driver_def: dict[str, Any]) -> Iterator[tuple[Any, list[str]]]:
    """Each config field that draws the masked-default warning, with it."""
    defaults = _mapping(driver_def.get("default_config"))
    for field_name, field_def in _mapping(driver_def.get("config_schema")).items():
        found = _masked_default(field_name, field_def, defaults)
        if found:
            yield field_name, found


def _masked_default(field_name: Any, field_def: Any, defaults: dict[str, Any]) -> list[str]:
    """A masked config field whose default sits in plain text in the file.

    Worth a nudge, never a refusal: the value an author puts there is nearly
    always the factory default the manufacturer prints in its own manual, a
    starting point for whoever commissions the device rather than a secret.
    The rule worth having is "don't ship a site password", and a driver file
    cannot tell the two apart, so the nudge is the honest ceiling. (The same
    stance is declared on the ``secret`` field in spec.py, which is what the
    published schema and every editor read.)
    """
    if not isinstance(field_def, dict) or not is_secret_setting(field_name, field_def):
        return []
    if not _has_value(field_def.get("default")) and not _has_value(defaults.get(field_name)):
        return []
    return [
        f"Config field '{field_name}' is masked but has a default "
        f"value, which is stored in plain text inside the driver "
        f"file. That is fine for a published factory default, but "
        f"never put a real site password here."
    ]


def _has_value(value: Any) -> bool:
    return value is not None and value != ""


def port_open_warnings(driver_def: dict[str, Any]) -> list[str]:
    """A ``port_open`` hint on a driver no TCP connect sweep can reach.

    The hint is matched against a TCP port scan, so on a driver that only
    speaks UDP or OSC it can never fire. The rule on the field itself only
    rejects ports that are too generic; nothing else notices a port on the
    wrong protocol, so the hint ships looking declared and does nothing. A
    nudge rather than a refusal because the device may genuinely also listen
    on TCP; it is the driver that has no way to be found that way.
    """
    discovery = driver_def.get("discovery")
    if not isinstance(discovery, dict) or not discovery.get("port_open"):
        return []
    declared = _with_extras(driver_def.get("transport"), driver_def)
    transports = {t for t in declared if isinstance(t, str) and t}
    if not transports or transports & set(PORT_OPEN_TRANSPORTS):
        return []
    return [
        f"discovery.port_open is matched against a TCP port scan, but "
        f"this driver's transport is "
        f"{', '.join(sorted(transports))}, so the hint can never fire. "
        f"Drop it, or add a transport that listens on TCP."
    ]


def _with_extras(first: Any, driver_def: dict[str, Any]) -> list[Any]:
    """``first`` and the driver's ``transports:`` list, as declared."""
    declared = [first]
    extra = driver_def.get("transports")
    if isinstance(extra, list):
        declared.extend(extra)
    return declared


def unlabeled_param_warnings(driver_def: dict[str, Any]) -> Iterator[tuple[Any, Any, list[str]]]:
    """Each command parameter that draws the no-label warning, as (command,
    parameter, the warning); a command or params block that is not a mapping
    has none."""
    for cmd_name, param_name, param_def in _command_params(driver_def):
        found = _unlabeled(cmd_name, param_name, param_def)
        if found:
            yield cmd_name, param_name, found


def _command_params(driver_def: dict[str, Any]) -> Iterator[tuple[Any, Any, Any]]:
    """Each parameter a command declares, as (command, parameter, its
    definition)."""
    for cmd_name, cmd_def in _mapping(driver_def.get("commands")).items():
        params = cmd_def.get("params") if isinstance(cmd_def, dict) else None
        for param_name, param_def in _mapping(params).items():
            yield cmd_name, param_name, param_def


def _unlabeled(cmd_name: Any, param_name: Any, param_def: Any) -> list[str]:
    """A command parameter with no label is named on every form by its key
    made readable (``param_labels.py``), which often says too little
    ("Value", "Src"). A nudge per field, never a refusal: the field works
    either way."""
    if not isinstance(param_def, dict):
        return []
    label = param_def.get("label")
    if isinstance(label, str) and label.strip():
        return []
    return [
        f"Command '{cmd_name}' parameter '{param_name}' has no label, so "
        f"forms show its key made readable. Give it a label that says "
        f"what to enter."
    ]


class _AtDefaults:
    """What the driver does at its default config: the config, the transport
    it opens (the config's, else the driver's), its poll queries and, read
    when asked, its poll interval."""

    def __init__(self, driver_def: dict[str, Any]) -> None:
        self.config = _mapping(driver_def.get("default_config"))
        self.transport = self.config.get("transport") or driver_def.get("transport")
        polling = driver_def.get("polling")
        self.queries = polling.get("queries") if isinstance(polling, dict) else None

    def number(self, field: str) -> float | None:
        """A config field read as a number, None when it is not one."""
        return _number(self.config.get(field))


def _mapping(value: Any) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def poll_length_warnings(driver_def: dict[str, Any]) -> list[str]:
    """A poll cycle that takes longer than the poll interval.

    Each line of a poll waits ``inter_command_delay`` after it, or
    DEFAULT_LINE_GAP_S over LINE_GAP_TRANSPORTS when no delay is set, and the
    next cycle starts one poll interval after this one ends. So a cycle longer
    than the interval keeps the device answering polls more than half the time
    and refreshes every reading less than half as often as the interval says.
    Checked at the driver's default config and, when a child roster is sized
    by a config field, with every such field at the largest value the driver
    accepts (the field's ``max``, else the child id's ``max``). OSC lines go
    out differently and are not counted here.
    """
    at = _AtDefaults(driver_def)
    cycle = _poll_cycle(at)
    if cycle is None:
        return []
    child_types = _mapping(driver_def.get("child_entity_types"))
    at_default = cycle.verdict(_poll_lines(at.queries, child_types, at.config))
    if at_default:
        return [
            f"Each poll sends {at_default}. Poll less per cycle, or set a longer "
            f"poll_interval in default_config."
        ]
    schema = _mapping(driver_def.get("config_schema"))
    largest, raised = _largest_rosters(child_types, schema, at.config)
    if not raised:
        return []
    at_largest = cycle.verdict(_poll_lines(at.queries, child_types, largest))
    if not at_largest:
        return []
    return [
        f"At the largest roster this driver accepts ({', '.join(raised)}), "
        f"each poll sends {at_largest}. Poll less per child, set a longer "
        f"poll_interval, or lower the roster's max to the largest unit the "
        f"driver supports."
    ]


class _PollCycle:
    """One poll's lines, spaced ``gap`` apart, against its interval."""

    def __init__(self, gap: float, delay: float, interval: float) -> None:
        self.gap = gap
        self.interval = interval
        self.spacing = f"{round(gap * 1000)} ms apart"
        if delay <= 0:
            self.spacing += " (the spacing used when no inter_command_delay is set)"

    def verdict(self, lines: int) -> str | None:
        """How long ``lines`` take and what that does, or None when they fit."""
        cycle = lines * self.gap
        if cycle <= self.interval:
            return None
        return (
            f"{lines} lines, {_seconds(cycle)} s at {self.spacing}, longer than the "
            f"{_seconds(self.interval)} s poll interval: the device answers polls more "
            f"than half the time, and each reading refreshes every "
            f"{_seconds(cycle + self.interval)} s"
        )


def _poll_cycle(at: _AtDefaults) -> _PollCycle | None:
    """The poll at the default config, or None when it is not counted: no
    spacing on this transport, no positive interval, no query list."""
    delay = at.number("inter_command_delay") or 0.0
    gap = _line_gap(at.transport, delay)
    if gap is None:
        return None
    interval = at.number("poll_interval")
    if gap <= 0 or _no_interval(interval):
        return None
    if not isinstance(at.queries, list):
        return None
    return _PollCycle(gap, delay, interval)


def _line_gap(transport: Any, delay: float) -> float | None:
    """The time between a poll's lines on ``transport``, None for a transport
    whose lines are not counted."""
    if transport in LINE_GAP_TRANSPORTS:
        return delay if delay > 0 else DEFAULT_LINE_GAP_S
    if transport in _DELAY_ONLY_TRANSPORTS:
        return delay
    return None


def _no_interval(interval: float | None) -> bool:
    """No interval to measure against: missing, zero or below. A NaN passes,
    and then every cycle reads as longer than it (``cycle <= nan`` is false)."""
    return not interval or interval <= 0


def _poll_lines(
    queries: list[Any], child_types: dict[str, Any], config: dict[str, Any]
) -> int:
    """How many lines one poll cycle sends with this config.

    Counted the way ConfigurableDriver._expand_query expands them: a string
    is one line, a ``{send}`` entry one line while its ``when:`` field is
    truthy, an ``each_child`` entry one line per child the declaration
    registers (none while the roster is empty), or one per page of
    ``page_size`` children. A ``when_state:`` gate, on an entry or on the
    whole poll, is counted as sent: the device's state is not known here, and
    the poll is as long as it gets while the gate is open. A roster the device resizes
    (``count_from_state``) is counted at its config field, which is what the
    runtime builds before the first report and what a matrix of that size
    reports anyway.
    """
    lines = 0
    for entry in queries:
        if isinstance(entry, str):
            lines += 1
        elif isinstance(entry, dict) and not _held_off(entry, config):
            lines += _entry_lines(entry, child_types, config)
    return lines


def _held_off(entry: dict[str, Any], config: dict[str, Any]) -> bool:
    """An entry whose ``when:`` names a config field that is off."""
    when = entry.get("when")
    if not isinstance(when, str) or not when:
        return False
    return not config.get(when)


def _entry_lines(
    entry: dict[str, Any], child_types: dict[str, Any], config: dict[str, Any]
) -> int:
    """How many lines one mapping entry sends."""
    child_type = entry.get("each_child")
    if child_type is None:
        return 1 if isinstance(entry.get("send"), str) else 0
    ids = declared_child_ids(_config_sized(child_types.get(child_type)), config)
    return len(page_starts(ids, entry.get("page_size"))) if ids else 0


def _config_sized(type_def: Any) -> Any:
    """A child type with ``count_from_state`` dropped, so its roster is the
    one its config field declares."""
    if not isinstance(type_def, dict):
        return type_def
    instances = type_def.get("instances")
    if not isinstance(instances, dict) or not instances.get("count_from_state"):
        return type_def
    trimmed = {k: v for k, v in instances.items() if k != "count_from_state"}
    return {**type_def, "instances": trimmed}


def _largest_rosters(
    child_types: dict[str, Any], schema: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """``config`` with every roster's config field at the largest value the
    driver accepts, and each field raised, as "<its label> <value>"."""
    largest = dict(config)
    raised: list[str] = []
    for type_def in child_types.values():
        roster = _sized_by_config(type_def, schema)
        if roster is None:
            continue
        field, top, name = roster
        current = _number(largest.get(field))
        if current is not None and current >= top:
            continue
        largest[field] = top
        raised.append(f"{name} {top}")
    return largest, raised


def _sized_by_config(type_def: Any, schema: dict[str, Any]) -> tuple[str, int, str] | None:
    """A roster sized by a config field, as (the field, the largest value it
    takes, its label); None for any other roster or one with no usable
    maximum. The field's ``max`` wins over the child id's."""
    if not isinstance(type_def, dict):
        return None
    instances = type_def.get("instances")
    field = instances.get("count_from") if isinstance(instances, dict) else None
    if not isinstance(field, str) or not field:
        return None
    field_def = _mapping(schema.get(field))
    top = field_def.get("max")
    id_format = type_def.get("id_format")
    if top is None and isinstance(id_format, dict):
        top = id_format.get("max")
    if not _a_count(top):
        return None
    return field, top, _label(field_def, field)


def _a_count(value: Any) -> bool:
    """A whole number of one or more, a boolean not counting as one."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value >= 1


def _label(field_def: dict[str, Any], field: str) -> str:
    label = field_def.get("label")
    if isinstance(label, str) and label:
        return label
    return field


def unwatched_silence_warnings(driver_def: dict[str, Any]) -> list[str]:
    """A device that stops answering would keep reading connected.

    The poll loop drops a connection whose polls draw nothing, but only on
    SILENCE_CHECK_TRANSPORTS and only for a driver that sends polls; a
    ``liveness:`` block covers every LIVENESS_TRANSPORTS link. A driver with no
    response rules reads nothing, so it has nothing to watch.
    """
    if not _reads_replies_unprobed(driver_def):
        return []
    at = _AtDefaults(driver_def)
    declared = _with_extras(at.transport, driver_def)
    transports = [t for t in dict.fromkeys(declared) if t in LIVENESS_TRANSPORTS]
    polls = _sends_polls(at)
    unwatched = [
        t for t in transports if not polls or t not in SILENCE_CHECK_TRANSPORTS
    ]
    if not unwatched:
        return []
    return [_unwatched_message(" or ".join(unwatched), polls)]


def _reads_replies_unprobed(driver_def: dict[str, Any]) -> bool:
    """A driver with response rules to read and no liveness block."""
    if driver_def.get("liveness"):
        return False
    responses = driver_def.get("responses")
    return isinstance(responses, list) and bool(responses)


def _unwatched_message(over: str, polls: bool) -> str:
    if polls:
        reason = f"polls over {over} are not checked for a reply"
    else:
        reason = "it sends no poll at its default config"
    return (
        f"Nothing notices if the device stops answering over {over}: {reason}, "
        f"and there is no liveness block, so the device reads connected for as "
        f"long as the connection stays open. Add a liveness block that asks "
        f"something the device answers in every state."
    )


def _sends_polls(at: _AtDefaults) -> bool:
    """Whether the driver polls at its default config: queries and a
    positive interval. A NaN interval is no poll here."""
    interval = at.number("poll_interval")
    if not at.queries:
        return False
    return interval is not None and interval > 0
