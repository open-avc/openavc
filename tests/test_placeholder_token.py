"""The ``{name}`` placeholder token has one home, ``spec.PLACEHOLDER``.

The runtime fills it (``compiled_protocol.safe_substitute``) and every other
reader of a template reads the same token: the placeholder check, a udp:
port's config field, the runtime's warning for a token that survives
substitution, push's port, group and path, the simulator's command matcher
and its validator's samples, the inline HTTP poll matcher, a registration
command's matcher and the inline command's auto-declared parameters. The
simulator's send-template reader keeps its own copy, because that module
imports nothing but the standard library (``test_compiled_protocol_purity``),
so its copy is held equal here.

Both directions: a token a reader finds is one the fill fills, and a token
the fill fills is one every reader finds. A ``{name}`` whose name is not an
identifier (``{1x}``, ``{été}``, ``{in-1}``) is no placeholder to any of them:
it goes out as typed.

A ``sets:`` value names a parameter with the same token, bare (no format
spec): ``spec.bare_placeholder``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from openavc.audit.effects import _declared
from openavc.audit.observe import recorded
from openavc.drivers import (
    command_checks,
    compiled_protocol,
    configurable,
    spec,
    substitution_checks,
)
from openavc.drivers.compiled_protocol import safe_substitute
from openavc.drivers.inline_protocol import derive_command_params
from openavc.drivers.push import push_port, resolve_push_value
from openavc.drivers.push_block_checks import push_block_errors
from openavc.simulator import osc_coverage, send_templates
from openavc.simulator.driver_sends import _left_unfilled
from openavc.simulator.inline_device import _poll_pattern
from openavc.simulator.push_channels import _registration_matcher
from openavc.simulator.side_receiver import _is_lone_placeholder
from openavc.simulator.yaml_auto import YAMLAutoSimulator


def test_every_reader_takes_the_token_from_spec() -> None:
    assert compiled_protocol.PLACEHOLDER is spec.PLACEHOLDER
    assert substitution_checks.placeholder_names is spec.placeholder_names
    assert configurable.placeholder_names is spec.placeholder_names
    assert command_checks.bare_placeholder is spec.bare_placeholder
    assert osc_coverage.PLACEHOLDER is spec.PLACEHOLDER


def test_the_send_template_readers_copy_is_the_same_token() -> None:
    assert send_templates._ANY_PLACEHOLDER.pattern == spec.PLACEHOLDER.pattern
    assert send_templates._ANY_PLACEHOLDER.flags == spec.PLACEHOLDER.flags


# Names a placeholder can and cannot carry, specs, a literal JSON brace, a
# token inside another's braces, and doubled braces around one.
TEMPLATES = [
    "{1x}", "{été}", "{Unit}", "{zone2:02d}", "{in-1}", "{a:{b}}", '{"x": {a}}',
    "{a}{b:x}", "{{a}}", "{a:}", "{_a1}", "x{level:03d}y{1x}", "{a:0{2}d}", "{ a }",
]


def _candidates(template: str) -> list[str]:
    """Every name some reader could take a brace in ``template`` to name."""
    found = {m.group(1) for m in re.finditer(r"\{([^{}]*)\}", template)}
    found |= {m.group(1) for m in re.finditer(r"\{([^{}:]+)", template)}
    return sorted(found)


def _names(template: str) -> list[str]:
    return spec.placeholder_names(template)


def _sent(template: str) -> str:
    """``template`` as the fill sends it with 7 for every name."""
    return safe_substitute(template, dict.fromkeys(_candidates(template), 7))


def _filled_by(fill, template: str) -> set[str]:
    """The names ``fill(template, values)`` replaces, one name at a time."""
    return {n for n in _candidates(template) if fill(template, {n: 7}) != template}


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_fill_fills_exactly_the_tokens_the_contract_names(template: str) -> None:
    assert _filled_by(safe_substitute, template) == set(_names(template))


@pytest.mark.parametrize("template", TEMPLATES)
def test_push_fills_the_same_tokens_with_the_same_specs(template: str) -> None:
    assert _filled_by(resolve_push_value, template) == set(_names(template))
    values = dict.fromkeys(_candidates(template), 7)
    assert resolve_push_value(template, values) == safe_substitute(template, values)


def test_a_push_port_with_a_format_spec_resolves_as_the_simulator_fills_it() -> None:
    assert push_port({"port": "{port:04d}"}, {"port": 5}) == 5
    errors = push_block_errors({
        "default_config": {"port": 5},
        "push": {"type": "udp_listener", "port": "{port:04d}"},
    })
    assert errors == []


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_push_check_reads_the_same_tokens(template: str) -> None:
    errors = push_block_errors({"push": {"type": "sse", "path": "/e" + template}})
    named = set(re.findall(r"config field '([^']*)'", " ".join(errors)))
    assert named == set(_names(template))


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_command_matcher_captures_the_same_tokens(template: str) -> None:
    params = {n: {"type": "string"} for n in _candidates(template)}
    assert send_templates.declared_placeholders(template, params) == _names(template)
    groups = send_templates.send_param_groups(template, params)
    assert set(groups) == set(_names(template))
    pattern = re.compile(send_templates.send_regex(template, params))
    assert pattern.groups == len(_names(template))
    for name, group in groups.items():
        assert 1 <= group <= pattern.groups, name


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_specs_and_renames_read_the_same_tokens(template: str) -> None:
    params = {n: {} for n in _candidates(template)}
    first_spec = {}
    for m in spec.PLACEHOLDER.finditer(template):
        first_spec.setdefault(m.group(1), m.group(2) or "")
    specs = send_templates.send_param_specs(template, params)
    assert {n: s for n, s in specs.items() if n in first_spec} == first_spec
    assert all(s == "" for n, s in specs.items() if n not in first_spec)
    for name in _candidates(template):
        renamed = send_templates.rename_placeholder(template, name, "zz")
        assert _names(renamed) == ["zz" if n == name else n for n in _names(template)]


@pytest.mark.parametrize("template", TEMPLATES)
def test_an_inline_command_declares_a_param_for_each_token(template: str) -> None:
    assert set(derive_command_params(template, set(), None)) == set(_names(template))


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_inline_poll_matcher_matches_the_line_the_driver_sends(template: str) -> None:
    pattern = _poll_pattern({"method": "GET", "path": "/p/" + template, "poll": True})
    assert pattern.match("GET /p/" + _sent(template))
    assert pattern.pattern.count(r"[^\s|]+") == len(_names(template))


def test_an_inline_poll_path_with_a_format_spec_matches_what_is_sent() -> None:
    pattern = _poll_pattern({"path": "/zone/{zone:02d}/status", "poll": True})
    assert pattern.match("GET /zone/03/status")


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_registration_matcher_matches_the_line_the_driver_sends(template: str) -> None:
    commands = {"reg": {"send": "REG " + template + " {listener_port}"}}
    pattern = _registration_matcher(commands, "reg")
    match = pattern.match("REG " + _sent(template) + " 7001")
    assert match and match.group("lport") == "7001"
    assert pattern.pattern.count(r"[^&\s|]+") == len(_names(template))


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_validator_sees_every_token_left_unfilled(template: str) -> None:
    assert _left_unfilled(template, template) == bool(_names(template))
    assert not _left_unfilled(template, _sent(template))


# ── A sets: value names a parameter with one bare token ──

SET_VALUES = ["{a}", "{a:02d}", "{in-1}", "{1x}", "{ a }", "{Unit}", "{a}{b}", "x{a}", "a"]


@pytest.mark.parametrize("value", SET_VALUES)
def test_a_sets_reference_is_one_bare_placeholder(value: str) -> None:
    name = spec.bare_placeholder(value)
    m = spec.PLACEHOLDER.fullmatch(value)
    assert name == (m.group(1) if m and m.group(2) is None else None)


@pytest.mark.parametrize("value", SET_VALUES)
def test_every_sets_reader_reads_the_same_reference(value: str) -> None:
    name = spec.bare_placeholder(value)
    params = {n: {"type": "string"} for n in ("a", "b", "in-1", "1x", " a ", "Unit", "a:02d")}
    braces = "{" in value
    assert command_checks._literal_or_param(value, params) == (not braces or name is not None)
    assert _is_lone_placeholder(value) == (name is not None)
    given = dict.fromkeys(params, 3)
    expected = 3 if name is not None else value
    assert _declared("level", value, None, given)["expected"] == recorded(expected)


@pytest.mark.parametrize("value", ["{level}", "{in-1}", "{level:02d}"])
def test_the_simulator_sets_from_a_bare_reference_only(value: str) -> None:
    sim = YAMLAutoSimulator("w1", {}, driver_def={
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp", "delimiter": "\r",
        "state_variables": {"level": {"type": "integer"}},
        "commands": {"set": {
            "send": "L{level}\r",
            "params": {"level": {"type": "integer"}, "in-1": {"type": "integer"},
                       "level:02d": {"type": "integer"}},
            "sets": {"level": value},
        }},
    })
    (handler,) = sim._command_handlers
    if spec.bare_placeholder(value) == "level":
        assert handler.state_changes == {"level": 1}
    else:
        # Not a reference: a literal, as the checker and the audit read it.
        assert handler.state_changes == {"level": ("literal", value)}


def test_a_name_that_is_not_an_identifier_is_sent_as_typed() -> None:
    # The device receives what the contract reads: no placeholder at all.
    assert safe_substitute("A{1x}B{été}", {"1x": 5, "été": 6}) == "A{1x}B{été}"


# ── No reader writes its own token ──

ROOT = Path(__file__).resolve().parents[1] / "openavc"

# Regex text that starts a brace token of a reader's own. A reader asks
# spec.PLACEHOLDER (or the send-template reader's held copy) instead.
_OWN_TOKEN = re.compile(
    r"""\\\{\(\\w|\\\{\(\[\^\{\}\]|\\\{\(\[a-z|\\\{\(\[A-Za-z|\\\{\[\^\{\}\]\+\\\}"""
    r"""|\\\{\\w\+\\\}|\\\\\\\{\\w\+\\\\\\\}|\\\{child_id|\\\{\[A-Za-z|\\\{\[a-zA-Z"""
)

# Each file allowed one, and why: the token's home, its held copy, and
# template languages that are not a driver's placeholders.
_OWN_TOKEN_ALLOWED = {
    "drivers/spec.py": "the token, and the schema's each_child send pattern",
    "simulator/send_templates.py": "the held copy (standard library only)",
    "simulator/notification_checks.py": "simulator notifications: {value} and {state.key}",
    "simulator/reply_templates.py": "the simulator's own emitted reply slots",
    "core/custom_ui_review.py": "CSS rule bodies",
}


def test_no_reader_writes_a_placeholder_token_of_its_own() -> None:
    found = sorted(
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*.py")
        if _OWN_TOKEN.search(path.read_text(encoding="utf-8"))
    )
    assert [f for f in found if f not in _OWN_TOKEN_ALLOWED] == []
    # An allowance with nothing left to allow goes too.
    assert sorted(_OWN_TOKEN_ALLOWED) == [f for f in found if f in _OWN_TOKEN_ALLOWED]
