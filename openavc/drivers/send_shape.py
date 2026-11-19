"""THE reading of what a YAML driver's sends carry: where each send sits, the
templates it carries, and what each OSC argument type takes.

A command, a device setting's ``write``, the ``liveness:`` probe and an OSC
``on_connect`` message (``{address, args}``) go out through the same builders
(a line of the device's protocol, an HTTP request, an OSC message). Which
fields each origin's block takes, how the runtime fills each one, and what
fills it are one table in the contract (``spec.SEND_FIELDS``,
``SEND_SHAPES``, ``SEND_FILLS``). This module reads a definition off that
table:

* ``sends`` lists every send a definition makes, with its origin and place.
  An OSC start-up message is one the driver model reads as one
  (``driver_model``'s start-up lines in the ``OSC_MESSAGE`` form).
* ``send_templates`` lists the templates one send carries, each at its own
  place: a text field, each text value of ``query_params`` and ``headers``,
  each OSC argument's ``value``. ``substitution_checks`` judges their
  placeholders against what the origin fills from.
* ``osc_arg`` is what each OSC type tag takes: ``f`` and ``d`` a number,
  ``h`` a whole number, ``i`` a number (cut to a whole one), ``s`` any text,
  ``T`` ``F`` ``N`` no value of their own. The runtime's encoder
  (``osc_wire.build_osc_args``) builds every argument through it, and the
  checker asks it about a value written without a placeholder, so a value
  the encoder would refuse at every send is refused where the driver is
  written (``osc_value_issues``). A value holding a placeholder is known only
  when it is sent.
* ``osc_args_errors`` is the shape of an ``args`` list: each a mapping whose
  type tag the encoder knows. A command and a setting's OSC write are held to
  it at every door, as they always have been; a probe's and a start-up
  message's args are held to it by the shape walk at the author's doors.

Which key decides a send's form is not read here (``command_checks``,
``setting_writes``).

Pure: the standard library, ``spec`` and the driver model. Vendored with the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any, Iterator, NamedTuple

from openavc.drivers.driver_model import START_UP, read_driver
from openavc.drivers.query_entry import OSC_MESSAGE
from openavc.drivers.spec import (
    OSC_ARG_TYPES,
    SEND_FIELDS,
    SEND_OSC_ARGS,
    SEND_SHAPES,
    SEND_TEXT,
    SEND_TEXT_VALUES,
    placeholder_names,
)

COMMAND = "command"
SETTING_WRITE = "setting_write"
LIVENESS = "liveness"
START_UP_MESSAGE = "start_up_message"

# The type an OSC argument takes when it names none.
DEFAULT_OSC_TAG = "f"



def _cut_to_whole(value: str) -> int:
    return int(float(value))


# Tags whose value is read as a number, and how: an "i" value is read as a
# number and cut to a whole one.
_NUMERIC_TAGS: dict[str, Any] = {"f": float, "h": int, "d": float, "i": _cut_to_whole}
# Tags that carry no value of their own.
_CONSTANT_TAGS: dict[str, Any] = {"T": True, "F": False, "N": None}
# What a numeric tag needs, in the checker's words.
_NEEDS = {"f": "a number", "d": "a number", "i": "a number", "h": "a whole number"}


class Send(NamedTuple):
    """One send a definition makes: its origin, its place, its block, and the
    command's or setting's name (None for the probe and a start-up
    message)."""

    origin: str
    place: str
    block: dict[str, Any]
    name: Any = None


class Template(NamedTuple):
    """One template a send carries: the block's field it sits in, its own
    place, and the template as written."""

    field: str
    place: str
    text: Any


def sends(driver_def: dict[str, Any]) -> Iterator[Send]:
    """Every send the definition makes, in the order the checks report them:
    the commands, the setting writes, the probe, then the OSC start-up
    messages. A block that is not a mapping sends nothing."""
    yield from _commands(driver_def.get("commands"))
    yield from _setting_writes(driver_def.get("device_settings"))
    probe = driver_def.get("liveness")
    if isinstance(probe, dict):
        yield Send(LIVENESS, "liveness", probe)
    yield from _start_up_messages(driver_def)


def _mapping_items(value: Any) -> Any:
    return value.items() if isinstance(value, dict) else ()


def _commands(commands: Any) -> Iterator[Send]:
    for name, cmd in _mapping_items(commands):
        if isinstance(cmd, dict):
            yield Send(COMMAND, f"commands.{name}", cmd, name)


def _setting_writes(settings: Any) -> Iterator[Send]:
    for name, setting in _mapping_items(settings):
        write = setting.get("write") if isinstance(setting, dict) else None
        if isinstance(write, dict):
            yield Send(SETTING_WRITE, f"device_settings.{name}.write", write, name)


def _start_up_messages(driver_def: dict[str, Any]) -> Iterator[Send]:
    """The OSC start-up messages, as the driver model reads them."""
    for line in read_driver(driver_def).wire.queries:
        if line.origin == START_UP and line.entry.form == OSC_MESSAGE:
            yield Send(START_UP_MESSAGE, line.place, line.written)


def send_templates(send: Send) -> Iterator[Template]:
    """The templates one send carries, each with its place, in the order its
    block's fields are declared. A field the block leaves out carries none;
    an HTTP method is sent as written."""
    for field in SEND_SHAPES[send.origin]:
        read = _TEMPLATES_BY_KIND.get(SEND_FIELDS[field][1])
        if read and field in send.block:
            for place, text in read(f"{send.place}.{field}", send.block[field]):
                yield Template(field, place, text)


def _text(place: str, value: Any) -> Iterator[tuple[str, Any]]:
    yield place, value


def _text_values(place: str, value: Any) -> Iterator[tuple[str, Any]]:
    for key, text in _mapping_items(value):
        yield f"{place}.{key}", text


def _arg_values(place: str, value: Any) -> Iterator[tuple[str, Any]]:
    for j, arg in _args(value):
        if "value" in arg:
            yield f"{place}[{j}].value", arg["value"]


def _args(value: Any) -> list[tuple[int, dict[str, Any]]]:
    """Each argument of an ``args`` list that is a mapping, with its index."""
    listed = value if isinstance(value, list) else ()
    return [(j, arg) for j, arg in enumerate(listed) if isinstance(arg, dict)]


# How each kind of send field carries its templates (spec.SEND_FIELDS); a
# word is sent as written.
_TEMPLATES_BY_KIND = {
    SEND_TEXT: _text,
    SEND_TEXT_VALUES: _text_values,
    SEND_OSC_ARGS: _arg_values,
}


def osc_arg_tag(arg: dict[str, Any]) -> Any:
    """The type tag an argument is sent as."""
    return arg.get("type", DEFAULT_OSC_TAG)


def osc_arg_text(arg: dict[str, Any]) -> str:
    """The text an argument's value is read from: its value as text, empty
    when it has none."""
    return str(arg.get("value", ""))


def osc_num(tag: str, value: str, converter: Any) -> Any:
    """``value`` read by a numeric tag's ``converter``, or one clear error
    naming the tag (an empty value, an unfilled placeholder, an infinite
    whole number) rather than a bare one."""
    try:
        return converter(value)
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError(
            f"OSC arg of type '{tag}' requires a numeric value, got {value!r}"
        ) from e


def osc_arg(tag: Any, text: str) -> tuple[str, Any] | None:
    """One ``(tag, value)`` argument read from ``text``, or None for a tag
    OSC here does not send (a list or map written by hand among them).
    Raises ValueError when a numeric tag's text is not a number it takes."""
    if not isinstance(tag, str):
        return None
    if tag in _NUMERIC_TAGS:
        return (tag, osc_num(tag, text, _NUMERIC_TAGS[tag]))
    if tag == "s":
        return ("s", text)
    if tag in _CONSTANT_TAGS:
        return (tag, _CONSTANT_TAGS[tag])
    return None


def osc_args_errors(where: str, arg_defs: Any) -> list[str]:
    """An OSC message's ``args``: each a mapping whose ``type`` tag the
    encoder knows, so a typo fails at load rather than being dropped when the
    message is built. A command's args and a device setting's OSC ``write:``
    ask this."""
    if arg_defs is None:
        return []
    if not isinstance(arg_defs, list):
        return [f"{where}: args must be a list"]
    errors: list[str] = []
    for j, arg in enumerate(arg_defs):
        if not isinstance(arg, dict):
            errors.append(f"{where}: args[{j}] must be a mapping")
            continue
        arg_type = osc_arg_tag(arg)
        if arg_type not in OSC_ARG_TYPES:
            errors.append(
                f"{where}: args[{j}] unknown OSC type '{arg_type}' "
                f"(expected one of f/i/s/h/d/T/F/N)"
            )
    return errors


def osc_value_issues(driver_def: dict[str, Any]) -> list[tuple[str, str]]:
    """Each OSC argument, of every send, whose written value its type cannot
    take, with its place: what the encoder would refuse at every send. A
    value holding a placeholder is not judged, nor an argument that is not a
    mapping or names a type the encoder does not know (``osc_args_errors``
    and the shape walk say those)."""
    issues = []
    for send in sends(driver_def):
        for j, arg in _args(send.block.get("args")):
            problem = _value_problem(arg)
            if problem:
                issues.append((f"{send.place}.args[{j}]", f"{send.place}.args[{j}]: {problem}"))
    return issues


def _value_problem(arg: dict[str, Any]) -> str | None:
    """Why the encoder would refuse an argument's written value, or None."""
    tag = osc_arg_tag(arg)
    text = osc_arg_text(arg)
    if not isinstance(tag, str) or tag not in _NEEDS or placeholder_names(text):
        return None
    try:
        osc_arg(tag, text)
    except ValueError:
        return f"an OSC '{tag}' argument needs {_NEEDS[tag]}, so {_written(arg)} cannot be sent"
    return None


def _written(arg: dict[str, Any]) -> str:
    """The value as the author wrote it, for the sentence."""
    value = arg.get("value", "")
    if value == "":
        return "an argument with no value"
    if value is None:
        return "a null value"
    return repr(value)
