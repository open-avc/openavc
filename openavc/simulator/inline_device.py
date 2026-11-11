"""A Generic device's inline protocol, as a generated simulator plays it.

A Generic device writes its commands, responses and state variables in the
device's config (the no-code Commands & Responses editor), not in a driver
file. The simulator merges them into the definition it builds from, and
answers two things a file driver's simulator never does:

* **The device's own words set its state.** Such a device often uses one
  string both to set a value and to report it ("PWR ON"), so a line the
  panel sends that matches one of its response patterns applies that rule's
  state changes and is echoed back as the device's confirmation.
* **An HTTP device's status poll.** A command marked Poll is answered with
  one JSON body holding every JSON field's current value, a dotted key
  nested the way the driver reads it. A device with no JSON field answers
  nothing: a body none of its responses read would only look like the wrong
  driver.

The rules worth knowing before changing anything here: the merge reads the
config through the runtime's own normalizers (``drivers.inline_protocol``,
which the YAML driver's merge in ``drivers.inline_merge`` reads it through
too) and puts a line rule into the ``match`` + ``set`` shorthand the reply
tables read, keeping a JSON row as a driver file writes it. Commands keep
their ``send`` with no line ending: the simulator matches a line after its
delimiter is stripped. A config with none of the three is not an inline
protocol, and the definition is used as it is.

It logs as yaml_auto, whose log the validator mutes while it builds a
simulator.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from openavc.drivers.compiled_protocol import build_json_mappings
from openavc.drivers.inline_protocol import (
    declare_command_params,
    derive_state_vars_from_responses,
    normalize_config_commands,
    normalize_config_responses,
    normalize_config_state_vars,
)
from openavc.simulator.reply_rules import mappings_to_set

logger = logging.getLogger("openavc.simulator.yaml_auto")


def merge_inline_protocol(driver_def: dict, config: dict | None) -> tuple[dict, bool]:
    """The definition with the config's inline protocol merged over it (a
    new dict; the config's commands and state variables win by name, its
    rules follow the file's), and whether there was one to merge."""
    cfg = config or {}
    norm_cmds = normalize_config_commands(cfg.get("commands"))  # no line-ending
    canonical = normalize_config_responses(cfg.get("responses"))
    norm_vars = normalize_config_state_vars(cfg.get("state_variables"))
    if not any((norm_cmds, canonical, norm_vars)):
        return driver_def, False

    # A {value} in a command is a parameter, as the driver declares it, so the
    # incoming line is matched with the value in it rather than the braces.
    declare_command_params(norm_cmds, set(cfg))

    merged = dict(driver_def)
    merged["commands"] = {**(driver_def.get("commands") or {}), **norm_cmds}
    merged["responses"] = list(driver_def.get("responses") or []) + _simulator_rules(canonical)
    derived = derive_state_vars_from_responses(canonical)
    merged["state_variables"] = {
        **(driver_def.get("state_variables") or {}), **derived, **norm_vars,
    }
    if isinstance(cfg.get("delimiter"), str):
        merged["delimiter"] = cfg["delimiter"]
    return merged, True


def _simulator_rules(canonical: list[dict]) -> list[dict]:
    """The config's rules as the reply tables read them: a line rule in the
    ``match`` + ``set`` shorthand; a JSON-field row, which reads a key out of
    a reply body with no line to match, as written; anything else left out."""
    rules: list[dict] = []
    for rule in canonical:
        if rule.get("json"):
            rules.append(rule)
        elif "match" in rule:
            rules.append({"match": rule["match"], "set": mappings_to_set(rule.get("mappings", []))})
    return rules


def response_handlers(responses: list, driver_id: str) -> list[tuple[re.Pattern, dict]]:
    """Each rule that sets something, as (its whole-line pattern, its set);
    a pattern that will not compile is skipped with a warning."""
    handlers: list[tuple[re.Pattern, dict]] = []
    for resp in responses:
        match_pattern = resp.get("match", "")
        set_dict = resp.get("set", {})
        if not match_pattern or not set_dict:
            continue
        try:
            pattern = re.compile(f"^{match_pattern}$")
        except re.error:
            logger.warning(
                "%s: skipping bad inline response pattern %r", driver_id, match_pattern
            )
            continue
        handlers.append((pattern, set_dict))
    return handlers


def json_fields(driver_def: dict) -> list[dict]:
    """The JSON fields a status poll answers with: every JSON rule's mapping
    that names a state and a key, other than a ``contains`` test."""
    state_vars = driver_def.get("state_variables") or {}
    fields: list[dict] = []
    for resp in driver_def.get("responses") or []:
        if isinstance(resp, dict) and resp.get("json"):
            fields.extend(m for m in build_json_mappings(resp, state_vars) if _answered(m))
    return fields


def _answered(mapping: dict) -> bool:
    if "contains" in mapping:
        return False
    return bool(mapping.get("state") and mapping.get("key"))


def json_polls(driver_def: dict) -> list[re.Pattern]:
    """The line each HTTP command marked Poll arrives as (``METHOD /path``,
    with or without a body section); a ``{placeholder}`` in its path matches
    whatever the driver filled in."""
    return [
        _poll_pattern(cmd)
        for cmd in (driver_def.get("commands") or {}).values()
        if _is_http_poll(cmd)
    ]


def _is_http_poll(cmd: Any) -> bool:
    if not isinstance(cmd, dict) or "send" in cmd:
        return False
    return bool(cmd.get("poll"))


def _poll_pattern(cmd: dict) -> re.Pattern:
    method = str(cmd.get("method") or "GET").upper()
    pattern = re.escape(f"{method} {cmd.get('path') or '/'}")
    pattern = re.sub(r"\\\{\w+\\\}", r"[^\\s|]+", pattern)
    return re.compile(f"^{pattern}(?:\\|.*)?$")


def json_body(fields: list[dict], get_state: Callable[[str], Any]) -> bytes:
    """A JSON body holding each field's current value, a dotted key nested
    the way the driver reads it; a field with no value, or a key with no
    name in it, is left out."""
    body: dict[str, Any] = {}
    for mapping in fields:
        value = get_state(mapping["state"])
        segments = [s for s in str(mapping["key"]).split(".") if s]
        if value is None or not segments:
            continue
        _place(body, segments, value)
    return json.dumps(body).encode()


def _place(body: dict, segments: list[str], value: Any) -> None:
    """``value`` at the end of the path ``segments`` names, making each
    object on the way; a value in the way of a nested key is replaced."""
    node = body
    for seg in segments[:-1]:
        if not isinstance(node.get(seg), dict):
            node[seg] = {}
        node = node[seg]
    node[segments[-1]] = value
