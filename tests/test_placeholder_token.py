"""The ``{name}`` placeholder token has one home, ``spec.PLACEHOLDER``.

The placeholder check, a udp: port's config field, the runtime's warning for
a token that survives substitution and the simulator's read-back coverage check
read templates with it. The simulator's send-template reader keeps its own copy, because that
module imports nothing but the standard library
(``test_compiled_protocol_purity``), so its copy is held equal here.

The runtime fills placeholders with a wider pattern (any ``\\w+`` name), so
every token the readers find has to be one it fills: otherwise the check
would judge a name the runtime never substitutes.
"""
from __future__ import annotations

import pytest

from openavc.drivers import (
    command_checks,
    compiled_protocol,
    configurable,
    spec,
    substitution_checks,
)
from openavc.simulator import send_templates
from openavc.simulator import osc_coverage


def test_every_reader_takes_the_token_from_spec() -> None:
    assert substitution_checks.placeholder_names is spec.placeholder_names
    assert configurable.placeholder_names is spec.placeholder_names
    assert command_checks.PLACEHOLDER is spec.PLACEHOLDER
    assert osc_coverage.PLACEHOLDER is spec.PLACEHOLDER


def test_the_send_template_readers_copy_is_the_same_token() -> None:
    assert send_templates._ANY_PLACEHOLDER.pattern == spec.PLACEHOLDER.pattern
    assert send_templates._ANY_PLACEHOLDER.flags == spec.PLACEHOLDER.flags


@pytest.mark.parametrize(
    "template",
    [
        "{a}", "{a:02d}", "{a:}", "{_a1}", "{A:>4}", '{"x": {a}}', "{a:{b}}",
        "{a}{b:x}", "x{level:03d}y", "{a:0{2}d}", "{{a}}", "{a:\n}",
    ],
)
def test_the_runtime_fills_every_token_the_readers_find(template: str) -> None:
    for found in spec.PLACEHOLDER.finditer(template):
        filled = compiled_protocol._SUBSTITUTION_RE.match(template, found.start())
        assert filled is not None
        assert filled.span() == found.span()
        assert filled.groups() == found.groups()
