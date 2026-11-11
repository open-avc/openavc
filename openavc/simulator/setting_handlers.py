"""How a generated simulator answers a ``device_settings`` write: each
setting's ``write`` read into the handler a command would get
(command_handler), so a write is matched, lands in its variable and is
reported back.

A setting's ``write`` carries the same templates a command's ``send`` does,
and its ``state_key`` names where the value lands, so the pair is everything
a handler needs. Without one, a write reached the end of the dispatch chain
and was dropped as an unrecognized command. The rules worth knowing before
changing anything here:

* **Reporting the value back is the point.** A setting write waits for the
  device to say the value took, so a handler that changed state silently
  would leave the setting pending forever and re-sent on every connect. The
  reply comes from the ``responses:`` rule the real driver reads the
  device's report with.
* **The write form is asked in the runtime's order** (``setting_writes.py``):
  an ``address`` write is OSC and never reaches this chain (OSC answers from
  the response address map), then ``path`` / ``method`` is HTTP, built into
  the exact line ``respond_http`` synthesizes (``"METHOD /path|body"``, POST
  by default), then ``send``. Here each form is picked by being filled in,
  where the runtime picks by its key being present.
* **No command_prefix.** ``set_device_setting`` substitutes, escape-decodes,
  applies ``send_frame`` and sends; it never adds the prefix a command gets,
  so neither does the pattern.
* **One line carries the value.** A write may be a set and a read-back sent
  as one blob that arrives as separate lines: only the segment holding
  ``{value}`` is the write. Its ``{config_field}`` placeholders are filled
  from the instance's config, as the driver fills them, before the pattern
  is built.
* **The capture follows the setting's type**, except that a setting with a
  ``map`` writes the device's own words, so it captures like a string and
  the word is read back.

It logs as yaml_auto, whose log the validator mutes while it builds a
simulator.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from typing import Any

from openavc.drivers.compiled_protocol import conversion_of, decode_delimiter, safe_substitute
from openavc.drivers.spec import placeholder_names
from openavc.simulator.command_handler import CommandHandler
from openavc.simulator.send_templates import send_param_specs, send_regex, spec_int_base

logger = logging.getLogger("openavc.simulator.yaml_auto")

#: A setting's declared type, read as the command-parameter type whose capture
#: matches the same wire form. `enum` and `float` have no param equivalent: an
#: enum's members are wire tokens of any shape, so it captures like a string,
#: and a float captures like a number.
_SETTING_PARAM_TYPE: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "float": "number",
    "boolean": "boolean",
    "enum": "string",
}


def setting_handlers(
    driver_def: dict,
    instance_config: Callable[[], dict],
    var_def_for_key: Callable[[str], dict],
) -> Iterator[CommandHandler]:
    """A handler for each setting whose write reaches the command chain, in
    the order the driver declares them. ``instance_config`` gives the fields
    a write's placeholders are filled from, asked only for a line that has
    one; ``var_def_for_key`` gives a state key's variable."""
    settings = driver_def.get("device_settings") or {}
    if not isinstance(settings, dict):
        return
    for name, entry in settings.items():
        handler = _handler(name, entry, instance_config, var_def_for_key)
        if handler is not None:
            yield handler


def _handler(
    name: str,
    entry: Any,
    instance_config: Callable[[], dict],
    var_def_for_key: Callable[[str], dict],
) -> CommandHandler | None:
    """One setting's handler, or None when its write never reaches this chain
    or puts no value on the wire."""
    line = _value_line(entry, instance_config)
    if line is None:
        return None
    value_map = _value_map(entry)
    params = {"value": {"type": _capture_type(entry, value_map)}}
    pattern = _pattern(name, line, params)
    if pattern is None:
        return None
    state_key = str(entry.get("state_key") or name)
    base = spec_int_base(send_param_specs(line, params).get("value", ""))
    conversion = conversion_of(var_def_for_key(state_key))
    return CommandHandler(
        name=f"setting:{name}",
        pattern=pattern,
        state_changes={state_key: 1},
        response_var=state_key,
        group_bases={1: base} if base else {},
        group_conversions={1: conversion} if conversion else None,
        group_value_maps={1: value_map} if value_map else None,
    )


def _value_line(entry: Any, instance_config: Callable[[], dict]) -> str | None:
    """The line of a setting's write that carries the value, or None."""
    if not isinstance(entry, dict):
        return None
    write = entry.get("write")
    if not isinstance(write, dict):
        return None
    template = write_template(write)
    if not template:
        return None
    # None when every segment was a query, or config substitution left
    # nothing addressable: a handler with no {value} capture would set the
    # state key to nothing on every match.
    return wire_line(template, instance_config)


def write_template(write: dict) -> str | None:
    """The line a ``device_settings`` write puts on this transport, in the
    runtime's order (the module docstring); None for an OSC write, which
    never reaches this chain, and an empty one."""
    if write.get("address"):
        return None
    path = _filled(write.get("path"))
    if path or write.get("method"):
        return _http_line(write, path)
    return _filled(write.get("send"))


def _http_line(write: dict, path: str | None) -> str:
    """An HTTP write as ``"METHOD /path|body"``; no body section without a
    body, ``/`` without a path."""
    method = str(write.get("method") or "POST").upper()
    line = f"{method} {path or '/'}"
    body = _filled(write.get("body"))
    return f"{line}|{body}" if body else line


def _filled(value: Any) -> str | None:
    """A write field that is text with something in it, else None."""
    return value if isinstance(value, str) and value else None


def wire_line(template: str, instance_config: Callable[[], dict]) -> str | None:
    """The one segment of a write that holds ``{value}`` (the one name the
    runtime always fills with the value being written, with or without a
    format spec), its config placeholders filled the way the driver fills
    them (yaml_auto's ``_query_wire_line`` does the same for a raw poll
    query), stripped; None when no segment holds it. ``{value}`` is never a
    config key: it is left for ``send_regex`` to turn into the capture."""
    segments = [
        s for s in decode_delimiter(template).replace("\r", "\n").split("\n")
        if s.strip()
    ]
    wire = next((s for s in segments if "value" in placeholder_names(s)), None)
    if wire is None:
        return None
    if "{" in wire:
        fields = instance_config()
        fields.pop("value", None)
        wire = safe_substitute(wire, fields)
    return wire.strip() or None


def _value_map(entry: dict) -> dict | None:
    """The setting's value -> device word table, when it declares one."""
    value_map = entry.get("map")
    return value_map if isinstance(value_map, dict) and value_map else None


def _capture_type(entry: dict, value_map: dict | None) -> str:
    """The param type whose capture matches what the write puts on the
    wire: the setting's own type, or a string for a mapped setting's word."""
    if value_map:
        return "string"
    return _SETTING_PARAM_TYPE.get(str(entry.get("type", "")), "string")


def _pattern(name: str, line: str, params: dict) -> re.Pattern | None:
    pattern_str = send_regex(line, params)
    try:
        return re.compile(f"^{pattern_str}$")
    except re.error:
        logger.warning("Invalid regex from setting '%s': %s", name, pattern_str)
        return None
