"""The property every reply text the simulator rebuilds from a pattern must hold.

``emit_template``, ``emit_template_multi`` and ``emit_literal`` rebuild the
text a driver's response pattern matches. Whatever text they return must be
text that pattern reads: filled in, the pattern matches it the way the driver
searches a line, and each slot's capture group takes exactly what was put in
the slot. The helpers here run the walker and hold its answer to that, with
Python's ``re`` as the judge and samples of their own, so a pin can never
record a reply its own pattern rejects.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

from openavc.simulator.reply_templates import (
    emit_literal,
    emit_template,
    emit_template_multi,
    fill_template,
)

#: What a ``{name}`` slot is filled with: one of these must be read back.
SAMPLES = ("0", "1", "a", "x", "A", "", "0 0")
_NAME_SLOT = re.compile(r"\{\w+\}")


def _spelled(template: str, placeholder: str) -> str | None:
    """How ``{name}`` is written in the template: as it is, or with the
    zero-pad spec a fixed run of digits gives it (``{value:03d}``)."""
    name = re.escape(placeholder[1:-1])
    m = re.search(r"\{" + name + r"(?::0\d+d)?\}", template)
    return m.group(0) if m else None


def _samples(rx: re.Pattern) -> list[str]:
    """``SAMPLES``, then what each group captures in the pattern's own
    representative line (``(\\d{2}x)`` takes ``00x``), when it has one."""
    samples = list(SAMPLES)
    line = emit_template_multi(rx.pattern, {})
    m = rx.search(line) if line is not None else None
    if m:
        samples += [g for g in m.groups() if g is not None and g not in samples]
    return samples


def reads_back(pattern: str, placeholders: dict[int, str], template: str) -> bool:
    """True when some fill of ``template``'s ``{name}`` slots is a line
    ``pattern`` matches with every slot group capturing exactly its text, a
    literal slot (a wire token) its own text."""
    try:
        rx = re.compile(pattern)
    except re.error:
        return False
    named = {n: ph for n, ph in placeholders.items() if _NAME_SLOT.fullmatch(ph)}
    spelled = {n: _spelled(template, ph) for n, ph in named.items()}
    if None in spelled.values():
        return False
    for combo in itertools.product(_samples(rx), repeat=len(named)):
        values = {ph[1:-1]: sample for ph, sample in zip(named.values(), combo)}
        line = fill_template(template, values)
        wanted = {n: fill_template(spelled[n], values) for n in named}
        wanted.update({n: ph for n, ph in placeholders.items() if n not in named})
        m = rx.search(line)
        if m and all(m.group(n) == text for n, text in wanted.items()):
            return True
    return False


def template_multi_of(pattern: str, placeholders: dict[int, str]) -> str | None:
    """``emit_template_multi``'s answer, held to the property."""
    template = emit_template_multi(pattern, placeholders)
    if template is not None:
        assert reads_back(pattern, placeholders, template), (pattern, template)
    return template


def template_of(pattern: str, group: int = 1) -> str | None:
    """``emit_template``'s answer, held to the property."""
    template = emit_template(pattern, group)
    if template is not None:
        assert reads_back(pattern, {group: "{value}"}, template), (pattern, template)
    return template


def literal_of(pattern: str) -> Any:
    """``emit_literal``'s answer, held to the property."""
    literal = emit_literal(pattern)
    if literal is not None:
        assert reads_back(pattern, {}, literal), (pattern, literal)
    return literal
