"""A simulator: section's template handler: a ``command_handlers`` entry with
a ``receive:`` (or ``match:``) pattern and ``set_state:`` / ``respond:`` text
instead of a script, and how its placeholders fill.

A template says ``{N}`` for the line's capture group N and ``{state.key}``
for a value the device holds, written in the device's own numbers (the
caller's ``to_wire``), as every other reply the simulator sends is. The
groups fill first, then the state references found in the result, so a
capture that reads like a reference is filled too. A group that did not
match is empty up to the last group that did and stays as written after it;
``{0}`` and a reference to a key the device does not hold stay as written.

A ``set_state:`` value with a placeholder in it carries the device's own
number, which the caller reads back to the real value; one without is the
author's, stored as written. The writes are made in the order the section
lists them, each one filled after the one before it is stored, so a later
value can read an earlier one.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from typing import Any

#: A template's placeholders: a capture group (`{1}`) or another state value
#: (`{state.key}`). Either brings the device's own number into a
#: `set_state` value; a value with neither is the author's, as written.
PLACEHOLDER = re.compile(r"\{(?:\d+|state\.[^{}]+)\}")
STATE_REF = re.compile(r"\{state\.([^{}]+)\}")

#: The device's own number for a state value: (key, value) -> wire value.
ToWire = Callable[[str, Any], Any]


class ExplicitHandler:
    """A template handler from the simulator: section's command_handlers."""
    def __init__(self, pattern: re.Pattern, respond: str | None, set_state: dict):
        self.pattern = pattern
        self.respond = respond
        self.set_state = set_state


def fill(template: str, match: re.Match | None, state: Mapping[str, Any], to_wire: ToWire) -> str:
    """``template`` with its capture groups, then its state references, filled."""
    return _fill_state(_fill_groups(template, match), state, to_wire)


def state_writes(
    handler: ExplicitHandler, match: re.Match, state: Mapping[str, Any], to_wire: ToWire
) -> Iterator[tuple[str, str, bool]]:
    """Each ``set_state:`` entry as (key, its filled text, whether that text
    is the device's own number), in order. Lazy: an entry is filled only when
    asked for, so it reads the state the earlier ones left."""
    for key, value in handler.set_state.items():
        text = str(value)
        yield key, fill(text, match, state, to_wire), bool(PLACEHOLDER.search(text))


def _fill_groups(text: str, match: re.Match | None) -> str:
    if match is None:
        return text
    for index in range(1, (match.lastindex or 0) + 1):
        text = text.replace(f"{{{index}}}", match.group(index) or "")
    return text


def _fill_state(text: str, state: Mapping[str, Any], to_wire: ToWire) -> str:
    for key in set(STATE_REF.findall(text)):
        if key in state:
            text = text.replace(f"{{state.{key}}}", str(to_wire(key, state[key])))
    return text
