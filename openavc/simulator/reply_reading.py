"""Whether a driver reads what its simulator sends: its reply rules, as the simulator validator reads them.

A driver reads a reply with its response rules. A regex rule searches the text
(its ``{config_field}`` placeholders filled in first), and a ``json: true``
rule reads a body that is a JSON object carrying a key the rule maps. A driver
on a delimited transport splits what arrives on its own terminator before
either is asked. ``ReplyRules`` holds the three, read once per check.

The validator asks this of three things the simulator sends, and each asks it
its own way, so each is its own reader and none is a variant of another:

* a handler's answer is read line by line, by either kind of rule (``reads``);
* a notification is read whole (stripped) and line by line by the regex
  rules, and whole by the JSON rules (``reads_pushed``);
* the text a reply trails after its last delimiter is read by the regex rules
  alone (``compile_response_patterns``, without the JSON keys).

These are the validator's own readings of the rules. The runtime reads replies
in ``openavc.drivers.text_replies`` and ``openavc.drivers.json_replies``; a JSON
rule's keys are read here by name only (a ``set:`` spec's ``key``, else its
``path``; a ``mappings:`` entry's ``key``; a ``child_set`` state's ``key``, else
its ``path``), cut to the top-level name a body must carry.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from openavc.drivers.compiled_protocol import (
    decode_delimiter,
    parse_json_reply,
    safe_substitute,
)


# Transports where the driver reads replies by splitting a byte stream on its
# delimiter. A datagram transport frames one message per packet, so a reply
# there needs no terminator to be complete.
DELIMITED_TRANSPORTS = ("tcp", "serial")


def frame_delimiter(driver_def: dict) -> str | None:
    """The terminator the driver's frame parser splits replies on, decoded,
    or None for a transport that is not delimiter-framed (or a delimiter
    that is not text, or decodes to nothing)."""
    if driver_def.get("transport", "tcp") not in DELIMITED_TRANSPORTS:
        return None
    raw = driver_def.get("delimiter")
    if not isinstance(raw, str) or not raw:
        return None
    return decode_delimiter(raw) or None


def response_lines(response_text: str, delimiter: str | None = None) -> list[str]:
    """One reply, split the way the driver's frame parser will split it.

    A handler answering with several lines sends the driver several messages,
    and each is matched against the response rules on its own — matching the
    concatenation instead finds nothing and reports a driver that is fine.
    The terminator arrives either as a real newline (a Python literal in
    script code) or as the two characters ``\\r`` (a YAML respond: template);
    both mean the same thing here. A driver framed on a terminator of its own
    (``>``, ``#``) is split on it too, and the driver never sees it: its
    rules are written without it.
    """
    text = (
        response_text.replace("\\r\\n", "\n")
        .replace("\\r", "\n")
        .replace("\\n", "\n")
    )
    if delimiter and delimiter.strip("\r\n"):
        text = text.replace(delimiter, "\n")
    return [line for line in (raw.strip() for raw in text.splitlines()) if line]


def compile_response_patterns(responses: list, config: dict) -> list[re.Pattern]:
    """The rules' ``match:`` patterns with the config filled in, compiled. A
    rule with no pattern, or one that does not compile, is passed over; a
    rule that is not a mapping raises."""
    patterns = []
    for resp in responses:
        pattern_str = resp.get("match", "")
        if not pattern_str:
            continue
        pattern_str = safe_substitute(pattern_str, config)
        try:
            patterns.append(re.compile(pattern_str))
        except re.error:
            pass
    return patterns


def _spec_key(spec: Any) -> Any:
    """The key a ``set:`` or ``state:`` value spec names: its ``key``, else
    its ``path``; a shorthand spec is the key itself."""
    return spec.get("key", spec.get("path")) if isinstance(spec, dict) else spec


def _set_keys(resp: dict) -> Iterator[Any]:
    for spec in (resp.get("set") or {}).values():
        yield _spec_key(spec)


def _mapping_keys(resp: dict) -> Iterator[Any]:
    for mapping in resp.get("mappings") or []:
        yield mapping.get("key") if isinstance(mapping, dict) else None


def _child_state_keys(resp: dict) -> Iterator[Any]:
    # A json rule's child_set routes by JSON path too (literal id per entry):
    # a body carrying one of those paths' top-level keys is parsed just the
    # same, so a per-child notification is not ignored.
    for entry in resp.get("child_set") or []:
        state_map = entry.get("state") if isinstance(entry, dict) else None
        for spec in (state_map or {}).values():
            yield _spec_key(spec)


def _top_level_keys(resp: dict) -> Iterator[str]:
    """Every key a JSON rule names that is text, cut to its top-level name,
    in the order the rule lists them: its ``set:`` specs, its ``mappings:``
    entries (a mapping is read by its ``key`` only) and its ``child_set``
    entries' states."""
    for named in (_set_keys, _mapping_keys, _child_state_keys):
        for key in named(resp):
            if isinstance(key, str) and key:
                yield key.split(".")[0].split("[")[0]


def _is_json_rule(resp: Any) -> bool:
    return isinstance(resp, dict) and bool(resp.get("json"))


def json_rule_keys(responses: list) -> set[str]:
    """Top-level JSON keys mapped by ``json: true`` response rules.

    A simulated body that parses as a JSON object carrying one of these keys
    IS read by the driver, even though it matches no regex pattern. A key that
    is not text, or is empty, is passed over.
    """
    keys: set[str] = set()
    for resp in responses:
        if _is_json_rule(resp):
            keys.update(_top_level_keys(resp))
    return keys


def _one_object(obj: Any) -> Any:
    """A list holding exactly one object stands for that object (the runtime
    unwraps it); anything else stands for itself."""
    if not isinstance(obj, list) or len(obj) != 1:
        return obj
    return obj[0] if isinstance(obj[0], dict) else obj


def matches_json_rules(message: str, json_keys: set[str]) -> bool:
    """True when ``message`` is a JSON object carrying a json-rule key, or a
    list holding exactly one such object (the runtime unwraps that too)."""
    if not json_keys:
        return False
    try:
        obj = parse_json_reply(message)
    except (ValueError, TypeError):
        return False
    obj = _one_object(obj)
    return isinstance(obj, dict) and any(k in obj for k in json_keys)


@dataclass(frozen=True)
class ReplyRules:
    """A driver's reply rules, read once: its regex patterns, its JSON rules'
    keys and the delimiter it splits a stream on."""

    patterns: list[re.Pattern]
    json_keys: set[str]
    delimiter: str | None

    @classmethod
    def of(cls, responses: list, config: dict, driver_def: dict) -> ReplyRules:
        """The rules ``responses`` declare, ``config`` filled into their
        patterns, framed the way ``driver_def`` frames its replies. Read in
        that order: the patterns (a rule that is not a mapping raises first),
        the JSON keys, the delimiter."""
        patterns = compile_response_patterns(responses, config)
        json_keys = json_rule_keys(responses)
        return cls(patterns, json_keys, frame_delimiter(driver_def))

    @property
    def any(self) -> bool:
        """Whether there is a rule of either kind to read a reply with."""
        return bool(self.patterns or self.json_keys)

    def lines(self, text: str) -> list[str]:
        """``text`` split into the messages the driver reads one at a time."""
        return response_lines(text, self.delimiter)

    def _searched(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)

    def reads(self, line: str) -> bool:
        """Whether a regex rule reads this line, or it is a JSON body a JSON
        rule reads."""
        return self._searched(line) or matches_json_rules(line, self.json_keys)

    def reads_pushed(self, message: str) -> bool:
        """Whether the driver reads a pushed message: a regex rule reads the
        whole message (stripped) or one of its lines, or a JSON rule reads
        the whole message. Pushed data is split on line endings and the
        driver's own delimiter, and stripped, before it is matched."""
        candidates = [message.strip()] + self.lines(message)
        return any(self._searched(c) for c in candidates) or matches_json_rules(
            message, self.json_keys
        )
