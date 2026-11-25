"""THE placeholder fill: a template's ``{name}`` placeholders filled from config and parameters.

A driver file's templates name a value with the contract's token,
``spec.PLACEHOLDER``: ``{name}``, or ``{name:spec}`` with a Python format spec
(``{preset:02d}``, ``{addr:04X}``). There are two fills, and they differ only
in what a value becomes once it is formatted:

* ``safe_substitute`` fills a template that goes out as it is (a command's
  send, a query line, an OSC address, a header): the value's text.
* ``fill_pattern`` fills a reply rule's ``match:`` regex: the value's text as
  literal text (``re.escape``), because a config value is the device's text,
  never regex. A unit called ``A.1`` matches ``A.1`` and not ``AX1``, and one
  holding ``(`` still compiles. The running driver
  (``compiled_protocol.compile_driver``), the auto-generated simulator
  (``simulator/driver_sends.filled_rules``) and the simulator validator
  (``simulator/reply_reading``) all fill a pattern through it.

Both fills leave a placeholder as it is when it names nothing in the values
or its spec cannot format the value, so JSON braces never crash a send and a
bad spec is visible to the author. A numeric spec retries a whole float as an
int and a numeric string as a number (``_spec_retries``), so a saved ``"5"``
or a scaled ``5.0`` still pads to ``05``.

What a template is filled from where a device is known, and with the driver's
declared defaults where none is, is ``effective_config``: the driver's
``default_config``, the device's own settings over it, then the
``config_derived`` values (``derive_config``) worked out from both, as the
running driver holds its config. ``unfillable_pattern`` is the checker's
question: a pattern that compiles as written and not once the declared
defaults are in, which the driver model refuses at the author's doors
(``driver_model``, ``PATTERN_FILL``).

Purity contract, as ``spec``: standard library and ``spec`` only, and no
logger. It is part of the driver contract the community catalog vendors
(openavc-drivers' ``scripts/vendor_platform_contract.py``) and the SDK syncs.
``compiled_protocol`` re-exports ``safe_substitute`` and ``derive_config``,
and ``simulator/driver_sends`` ``effective_config``, for their callers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from openavc.drivers.spec import PLACEHOLDER, placeholder_names


def safe_substitute(template: str, params: Mapping[str, Any]) -> str:
    """``template`` with each placeholder ``params`` holds filled in by its
    value's text, through its format spec when it has one. An unknown
    placeholder, a name that is not an identifier (``{1x}``) and one whose
    spec cannot format the value are left as typed."""
    return _filled(template, params, _as_text)


def fill_pattern(template: str, config: Mapping[str, Any]) -> str:
    """A reply pattern with each placeholder ``config`` holds filled in as
    literal text: formatted as ``safe_substitute`` formats it, then escaped.
    A placeholder left as typed is left byte for byte, not escaped."""
    return _filled(template, config, re.escape)


def _as_text(text: str) -> str:
    return text


def _filled(template: str, params: Mapping[str, Any], inserted: Callable[[str], str]) -> str:
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        if key not in params:
            return match.group(0)
        spec = match.group(2)
        if not spec:
            return inserted(str(params[key]))
        formatted = _formatted(params[key], spec)
        # Unformattable: the placeholder stays verbatim, so a bad spec is
        # visible to the author instead of crashing the send.
        return match.group(0) if formatted is None else inserted(formatted)

    return PLACEHOLDER.sub(replacer, template)


def _formatted(value: Any, spec: str) -> str | None:
    """``value`` formatted by ``spec``, else the first number it stands for
    that the spec takes (``_spec_retries``); None when none does. The retries
    run while the first refusal is being handled, so anything one of them
    raises is chained to it."""
    try:
        return format(value, spec)
    except (ValueError, TypeError):
        return _first_retry_taken(value, spec)


def _first_retry_taken(value: Any, spec: str) -> str | None:
    """The first of ``_spec_retries(value)`` that ``spec`` formats, or None."""
    for number in _spec_retries(value):
        try:
            return format(number, spec)
        except (ValueError, TypeError):
            continue
    return None


def _spec_retries(value: Any) -> Iterator[Any]:
    """What a format spec that refused ``value`` is tried with next, in order.

    An integer spec ('d'/'x'/'X'/'o'/'b') rejects a float even when it's
    whole (26.0), which is exactly what a scaled slider value is, so a
    whole-number float is tried as an int and {vol:d} renders "26". A numeric
    spec applied to a numeric string is tried with it as an int, then as a
    float, so '02d' works on "5". A conversion that fails is skipped."""
    if isinstance(value, float) and value.is_integer():
        conversions: tuple[Any, ...] = (int,)
    elif isinstance(value, str):
        conversions = (int, float)
    else:
        return
    for conv in conversions:
        try:
            number = conv(value)
        except (ValueError, TypeError):
            continue
        yield number


def derive_config(config: dict[str, Any], derived: Any) -> None:
    """Populate ``config`` in place with values derived from other fields.

    ``derived`` is the driver's optional top-level ``config_derived`` map of
    ``{name: template}``. Each template is substituted against config; if any
    ``{field}`` it references resolves to an empty/missing value, the derived
    value is ``""`` — so an optional prefixed segment simply disappears.
    Entries are processed in declaration order, so a later template may
    reference an earlier derived name.
    """
    if not isinstance(derived, dict):
        return
    for name, template in derived.items():
        if not isinstance(template, str):
            continue
        refs = placeholder_names(template)
        if any(not str(config.get(f, "") or "").strip() for f in refs):
            config[name] = ""
        else:
            config[name] = safe_substitute(template, config)


def effective_config(driver_def: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict:
    """The config a device's templates are filled from, as the running
    driver holds it: the driver's ``default_config``, the device's own
    settings over it (``config``; none where no device is known, which
    gives the driver's declared defaults), then the ``config_derived``
    values worked out from both.

    The derivation is the runtime's own (``derive_config``), run last as the
    driver runs it, so a derived template whose referenced fields are all
    non-empty is substituted, otherwise the derived value is "" (an optional
    prefix segment simply disappears). A ``default_config`` that is no
    mapping raises as ``dict()`` does; a reader that must not raise asks
    first. A new dict each call, so a caller may change its copy.
    """
    merged = dict(driver_def.get("default_config") or {})
    merged.update(config or {})
    derive_config(merged, driver_def.get("config_derived"))
    return merged


def unfillable_pattern(pattern: Any, config: Mapping[str, Any]) -> str | None:
    """Why a reply pattern that compiles as written does not compile once
    ``config`` is filled in (the regex error's words), or None: it compiles
    filled, it names no placeholder, it is not text, or it does not compile
    as written either (which every door refuses as it is)."""
    if not isinstance(pattern, str) or not placeholder_names(pattern):
        return None
    try:
        re.compile(pattern)
    except re.error:
        return None
    try:
        re.compile(fill_pattern(pattern, config))
    except re.error as e:
        return str(e)
    return None
