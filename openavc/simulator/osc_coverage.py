"""Whether the OSC simulator answers each address an OSC driver sends, for the validator.

An OSC driver sends an address (and arguments), not a text line. The
simulator answers an address with a hand-written handler whose ``address:``
pattern (fnmatch) matches it, with a response rule's address (it answers
those with the current state), or with one of the console's own addresses
(``osc_addresses``). ``OscAddresses`` is that surface, read once with the
config filled in, and three checks ask it:

* ``check_osc_command_coverage``: each command's address with its
  parameters' sample values (``driver_sends.command_sample``), else the shape
  of every address the template can produce.
* ``check_osc_poll_coverage``: each poll query, ``{child_id}`` given as 1
  (written that way only: a format spec is left in place).
* ``check_osc_read_back_coverage``: each command's ``read_back`` lines, every
  placeholder given as 1.

The three sample a placeholder differently; each says how above. A text
driver's lines are ``send_coverage``'s.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterator
from typing import Any

from openavc.drivers.compiled_protocol import safe_substitute
from openavc.drivers.spec import PLACEHOLDER
from openavc.simulator.driver_sends import command_sample, effective_config, entry_text
from openavc.simulator.osc_addresses import SYSTEM_ADDRESSES, SYSTEM_PREFIXES
from openavc.simulator.reread_queries import read_back_query_lines
from openavc.simulator.validation_result import ValidationResult


# Marker for param placeholders while building a template regex; plain ASCII
# so re.escape leaves it alone, unusual enough not to appear in addresses.
_PARAM_MARKER = "Q0PARAMQ0"


class OscAddresses:
    """The addresses the OSC simulator answers, with the driver's config."""

    def __init__(self, sim_handlers: list, responses: list, driver_def: dict):
        self._patterns = [*_addresses(sim_handlers), *_addresses(responses)]
        self._config = effective_config(driver_def)

    def filled(self, template: str) -> str:
        """A template with the config filled in (shared runtime rules)."""
        return safe_substitute(template, self._config)

    def answers(self, address: str) -> bool:
        """Whether the simulator answers this address. The console's own
        addresses it answers before any rule (``osc_addresses.system_reply``)."""
        if address in SYSTEM_ADDRESSES or address.startswith(SYSTEM_PREFIXES):
            return True
        return any(fnmatch.fnmatch(address, p) for p in self._patterns)

    def answers_shape(self, template: str, params: dict) -> bool:
        """Whether any address a parameterized template can produce is one the
        simulator answers.

        A command like /mtx/{mtx}/mix/on with a string param is covered by
        literal response addresses /mtx/01/mix/on … /mtx/06/mix/on even though
        no single sample value proves it. Wildcard handler patterns are probed
        with their * / ? / [...] parts collapsed to a plain segment token.
        """
        rx = _template_regex(self.filled(template), params)
        if rx is None:
            return False
        return any(rx.match(_probe(pattern)) for pattern in self._patterns)


def _addresses(entries: list) -> Iterator[str]:
    """The ``address:`` of each entry that has one: a handler's pattern, or a
    response rule's address (the simulator answers those with current state)."""
    for entry in entries:
        if isinstance(entry, dict) and entry.get("address"):
            yield str(entry["address"])


def _template_regex(template: str, params: dict) -> re.Pattern | None:
    """Regex matching every address the template can produce (params match
    any single address segment)."""
    pat = template
    for param_name in params:
        pat = re.sub(r"\{" + re.escape(param_name) + r"(:[^}]*)?\}", _PARAM_MARKER, pat)
    if "{" in pat:
        return None  # unresolved config placeholders — can't model statically
    escaped = re.escape(pat).replace(_PARAM_MARKER, "[^/]+")
    try:
        return re.compile(f"^{escaped}$")
    except re.error:
        return None


def _probe(pattern: str) -> str:
    """A handler pattern as one address it matches: each wildcard part a
    plain segment token."""
    if not any(ch in pattern for ch in "*?["):
        return pattern
    probe = re.sub(r"\[[^\]]*\]", "x", pattern)
    return re.sub(r"[*?]+", "x", probe)


def check_osc_command_coverage(
    result: ValidationResult, commands: dict, addresses: OscAddresses, driver_def: dict,
) -> None:
    """Check every OSC command's address is answered by the simulator."""
    for cmd_name, cmd_def in commands.items():
        address_template = cmd_def.get("address", "")
        if not address_template:
            continue

        params = cmd_def.get("params", {})
        sample = command_sample(address_template, params, driver_def)
        if sample is not None and addresses.answers(sample):
            continue
        # A string param's sample value may miss literal response addresses
        # that do cover the command (e.g. /mtx/{mtx}/mix/on vs /mtx/01/mix/on),
        # so fall back to matching the template's address shape.
        if addresses.answers_shape(address_template, params):
            continue
        if sample is None:
            result.warning(
                "command_coverage",
                f"Could not generate sample address for command '{cmd_name}' "
                f"(address: {address_template!r})"
            )
            continue
        result.error(
            "command_coverage",
            f"No simulator handler matches OSC command '{cmd_name}' "
            f"(sample address: {sample!r}) — add a command_handlers entry "
            f"with a matching address: pattern or a responses entry for it"
        )


def check_osc_poll_coverage(
    result: ValidationResult, queries: list, commands: dict, addresses: OscAddresses,
) -> None:
    """Check every OSC polling query is answered by the simulator.

    OSC poll queries are either command names (run as that command — covered
    by command coverage) or raw addresses.
    """
    for query in queries:
        query_text = str(entry_text(query))
        if not query_text:
            continue
        if query_text in commands:
            continue  # runs as that command; command coverage checks it
        sample = addresses.filled(query_text).replace("{child_id}", "1")
        if not addresses.answers(sample):
            result.error(
                "poll_coverage",
                f"No simulator handler matches OSC polling query: {sample!r}"
            )


def check_osc_read_back_coverage(
    result: ValidationResult, commands: Any, addresses: OscAddresses,
) -> None:
    """Check every OSC address a command's ``read_back`` sends is answered
    by the simulator. ``true`` sends the command's own address, which
    command coverage checks; a read-back naming a command is that
    command's. A parameter in the address takes a sample value."""
    for line in _read_back_lines(commands):
        sample = PLACEHOLDER.sub("1", addresses.filled(line))
        if not addresses.answers(sample):
            result.error(
                "poll_coverage",
                f"No simulator handler matches OSC read_back query: {sample!r}"
            )


def _read_back_lines(commands: Any) -> Iterator[str]:
    """Each command's read-back query lines, command by command."""
    for cmd_def in (commands or {}).values():
        if isinstance(cmd_def, dict):
            yield from read_back_query_lines(cmd_def, commands)
