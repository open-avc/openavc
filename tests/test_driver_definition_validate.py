"""POST /api/driver-definitions/validate — the platform's rules, on demand.

The Driver Builder used to carry its own copy of the driver contract in
TypeScript, which meant a driver could be valid to one and invalid to the
other. This endpoint is what replaces that copy: the editor asks the server
what is wrong with a draft, and the server answers with the exact rules its
own save path enforces.

Two properties matter, and both are checked here rather than assumed:

1. **Verdict parity.** Everything the save route refuses, this reports; a
   draft this reports nothing for is one the save route accepts. Proven over
   the whole 221-case rejection corpus, not on samples.
2. **Each issue knows where it belongs.** ``path`` is what lets the editor put
   a message on the right tab beside the right control. It comes from tags the
   validation walk stamps as it enters each section, so a section whose tag is
   missing sends its issues to the wrong place — a silent failure with no
   other symptom. The table below pins one issue per section, and a companion
   test fails if a new tag is introduced without a case joining the table.

Also covered: the warning channel this endpoint adds (a rule worth showing an
author that must never block a save), and that validating a draft writes
nothing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.api.routes import drivers as drivers_routes
from server.api.routes.drivers import (
    create_driver_definition,
    validate_driver_definition_draft,
)
from server.api.models import DriverDefinitionRequest
from server.drivers import avcdriver_semantic

# Through the loader's wrappers, not the rules module directly: the loader is
# what wires the discovery-block check (it needs the discovery engine's
# parser), and it is what the endpoint calls. Validating without it silently
# accepts a malformed `discovery:` block.
from server.drivers.driver_loader import (
    validate_driver_definition,
    validate_driver_issues,
)

CASES_FIXTURE = (
    Path(__file__).parent / "fixtures" / "driver_validation_cases.json"
)


def _d(**over: Any) -> dict[str, Any]:
    """A minimal valid definition with per-case overrides."""
    base: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"noop": {"send": "NOOP\r"}},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
    }
    base.update(over)
    return base


def _errors(definition: dict[str, Any]) -> list[dict[str, str]]:
    return [
        issue
        for issue in validate_driver_issues(definition, strict=True)
        if issue["severity"] == "error"
    ]


def _paths(definition: dict[str, Any]) -> set[str]:
    return {issue["path"] for issue in _errors(definition)}


# --- Every section tags its own issues ---------------------------------------
#
# One definition per section, each carrying exactly one deliberate mistake, and
# the path its message must come back with. A section missing its tag inherits
# whichever section ran before it, so these fail loudly rather than subtly.

SECTION_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        "top-level",
        _d(transport="carrier_pigeon"),
        "",
    ),
    (
        "command_prefix",
        _d(command_prefix=7),
        "command_prefix",
    ),
    (
        "command_suffix",
        _d(command_suffix=7),
        "command_suffix",
    ),
    (
        "min_platform_version",
        _d(min_platform_version="0.20.0", web_ui=True),
        "min_platform_version",
    ),
    (
        "responses",
        _d(responses={"not": "a list"}),
        "responses",
    ),
    (
        "responses[i]",
        _d(responses=[{"match": "OK"}, {"match": "("}]),
        "responses[1]",
    ),
    (
        "commands",
        _d(commands=["not", "a", "mapping"]),
        "commands",
    ),
    (
        "commands.<name>",
        _d(commands={"noop": {"send": "N"}, "broken": {}}),
        "commands.broken",
    ),
    (
        "device_settings",
        _d(device_settings=["not a mapping"]),
        "device_settings",
    ),
    (
        "device_settings.<name>",
        _d(device_settings={"brightness": {"type": "integer"}}),
        "device_settings.brightness",
    ),
    (
        "discovery",
        _d(discovery={"tcp_probe": {"port": "eighty"}}),
        "discovery",
    ),
    (
        "push",
        _d(push={"type": "multicast"}),
        "push",
    ),
    (
        "auth",
        _d(auth={"type": "prompt_response"}),
        "auth",
    ),
    (
        "liveness",
        _d(liveness={"interval": -1}),
        "liveness",
    ),
    (
        "actions (block)",
        _d(quick_actions="noop"),
        "actions",
    ),
    (
        "actions[i]",
        _d(actions=[{"id": "setup_wizard", "kind": "setup", "label": "Set up"}]),
        "actions[0]",
    ),
    (
        "state_variables",
        _d(state_variables=["not a mapping"]),
        "state_variables",
    ),
    (
        "state_variables.<name>",
        _d(
            state_variables={
                "power": {"type": "string", "label": "Power"},
                "volume": {"type": "zzz", "label": "Volume"},
            }
        ),
        "state_variables.volume",
    ),
    (
        "frame_parser",
        _d(frame_parser={"type": "length_prefix", "header_size": 3}),
        "frame_parser",
    ),
    (
        "send_frame",
        _d(send_frame={"type": "length_prefix", "length_size": 0}),
        "send_frame",
    ),
    (
        "child_entity_types",
        _d(child_entity_types=["not a mapping"]),
        "child_entity_types",
    ),
    (
        "child_entity_types.<name>",
        _d(child_entity_types={"zone": {"id_format": {"type": "roman"}}}),
        "child_entity_types.zone",
    ),
    (
        "polling",
        _d(polling={"interval": 30}),
        "polling",
    ),
    (
        "substitutions in a command",
        _d(commands={"noop": {"send": "SET {levl}\r",
                              "params": {"level": {"type": "integer"}}}}),
        "commands.noop.send",
    ),
    (
        "substitutions in a polling query",
        _d(polling={"queries": ["{missing_tag} STA\r"]}),
        "polling.queries[0]",
    ),
    (
        "polling.queries[i]",
        _d(polling={"queries": [{"send": "Q1"}, {"when": "nope", "send": "Q2"}]}),
        "polling.queries[1]",
    ),
    (
        "on_connect",
        _d(on_connect=[{"when": "nope", "send": "INIT"}]),
        "on_connect[0]",
    ),
    (
        "unknown key (top level)",
        _d(state_varibles={}),
        "",
    ),
    (
        "unknown key (nested)",
        _d(config_schema={"host": {"type": "string", "typo_type": "string"}}),
        "config_schema.host",
    ),
]


@pytest.mark.parametrize(
    "label,definition,expected_path",
    SECTION_CASES,
    ids=[case[0] for case in SECTION_CASES],
)
def test_each_section_tags_its_issues_with_its_own_path(
    label: str, definition: dict[str, Any], expected_path: str
):
    paths = _paths(definition)
    assert paths, f"{label}: expected at least one error to tag"
    assert expected_path in paths, (
        f"{label}: expected an issue at path {expected_path!r}, got {sorted(paths)}"
    )


def test_every_context_tag_has_a_section_case():
    """A new section without a case here would tag its issues untested.

    The walk sets ``errors.ctx`` on entering each part of a definition. This
    reads those assignments straight out of the source and requires the table
    above to exercise every one — so adding a section to the validator is a
    change that has to say where its issues land.
    """
    source = Path(avcdriver_semantic.__file__).read_text(encoding="utf-8")
    assignments = re.findall(
        r"^\s*(?:errors|warnings)\.ctx = (.+)$", source, re.MULTILINE
    )
    assert assignments, "no ctx assignments found — did the tagging move?"

    # A tag is either a literal ("commands") or an f-string whose placeholders
    # are author-chosen names ("commands.{cmd_name}"). Reduce both to the shape
    # they produce, with `*` standing in for whatever the author named things.
    shapes = set()
    for expr in (a.strip() for a in assignments):
        if not expr.startswith(('"', 'f"')):
            # Bound to a value the enclosing walk computes. Each is covered
            # through the section that supplies it: `loc` by the unknown-key
            # cases, `name` by polling.queries / on_connect, `frame_key` by
            # command_prefix / command_suffix, `substitution_loc` by the two
            # unresolved-placeholder cases (a command and a polling query),
            # which is why both are in the table rather than one.
            assert expr in {"loc", "name", "frame_key", "substitution_loc"}, (
                f"new computed context tag {expr!r} — add a case for what it produces"
            )
            continue
        literal = re.sub(r'^f?"(.*)"$', r"\1", expr)
        shapes.add(re.sub(r"\{[^}]+\}", "*", literal))

    def variants(path: str) -> set[str]:
        """Every shape a concrete path could have been produced by."""
        forms = {path, re.sub(r"\[\d+\]", "[*]", path)}
        forms |= {re.sub(r"\.[^.]+$", ".*", form) for form in set(forms)}
        forms |= {re.sub(r"^[^\[]+\[", "*[", form) for form in set(forms)}
        return forms

    covered: set[str] = set()
    for _, _, path in SECTION_CASES:
        covered |= variants(path)

    missing = sorted(shapes - covered)
    assert not missing, (
        f"validator sections with no case in SECTION_CASES: {missing}"
    )


# --- Verdict parity with the save path ---------------------------------------


def test_the_whole_rejection_corpus_is_still_rejected():
    """Every definition an authoring gate refuses comes back with an error.

    This is the corpus the save routes, the AI tool and the community catalog
    CI are all pinned against. The endpoint has to agree with them exactly —
    it is meant to be the same rules, not a friendlier subset of them.
    """
    cases = json.loads(CASES_FIXTURE.read_text(encoding="utf-8"))
    assert len(cases) >= 200, f"corpus shrank unexpectedly: {len(cases)} cases"

    accepted = [name for name, definition in cases.items() if not _errors(definition)]
    assert not accepted, f"{len(accepted)} corpus cases were accepted: {accepted[:10]}"


def test_error_messages_match_the_save_path_exactly():
    """Not just the verdict — the wording, over the whole corpus.

    The messages are part of the authoring contract; an editor that rephrased
    them would be a second contract again.
    """
    cases = json.loads(CASES_FIXTURE.read_text(encoding="utf-8"))
    for name, definition in cases.items():
        from_save = validate_driver_definition(definition, strict=True)
        from_endpoint = [issue["message"] for issue in _errors(definition)]
        assert from_endpoint == list(from_save), f"messages diverged for {name}"


def test_a_valid_definition_yields_no_issues():
    assert validate_driver_issues(_d(), strict=True) == []


# --- The warning channel -----------------------------------------------------


SECRET_DEFAULT = _d(
    config_schema={
        "password": {
            "type": "string",
            "label": "Password",
            "secret": True,
            "default": "admin",
        }
    }
)


def test_a_masked_field_with_a_default_warns_and_does_not_error():
    """The rule that used to lock two shipped drivers out of the Builder.

    A published factory default is a starting point for whoever commissions
    the device, not a secret — worth a nudge, never a refusal.
    """
    issues = validate_driver_issues(SECRET_DEFAULT, strict=True)
    assert [i["severity"] for i in issues] == ["warning"]
    assert issues[0]["path"] == "config_schema.password"
    assert "plain text" in issues[0]["message"]


def test_the_default_can_live_in_default_config_instead():
    definition = _d(
        config_schema={
            "password": {"type": "string", "label": "Password", "secret": True}
        },
        default_config={"password": "integration"},
    )
    issues = validate_driver_issues(definition, strict=True)
    assert [i["severity"] for i in issues] == ["warning"]


def test_a_masked_field_with_no_default_says_nothing():
    definition = _d(
        config_schema={
            "password": {"type": "string", "label": "Password", "secret": True}
        }
    )
    assert validate_driver_issues(definition, strict=True) == []


def test_warnings_never_reach_the_error_list():
    """The save routes, the loader and catalog CI all read that list.

    A warning that leaked into it would block a save, drop a driver at load,
    and fail a catalog build — which is the whole reason it is a separate
    channel rather than a severity flag on the existing one.
    """
    assert validate_driver_definition(SECRET_DEFAULT, strict=True) == []


# --- port_open on a transport the port scan can never reach ------------------
#
# The hint is matched against a TCP connect sweep. The rule that already guards
# this field only rejects ports that are too generic, so a port on the wrong
# protocol passed every gate and shipped looking declared while doing nothing.


def test_port_open_on_an_osc_only_driver_warns():
    definition = _d(
        transport="osc",
        commands={"go": {"address": "/go"}},
        discovery={"port_open": [10023]},
    )
    issues = validate_driver_issues(definition, strict=True)
    assert [i["severity"] for i in issues] == ["warning"]
    assert issues[0]["path"] == "discovery"
    assert "can never fire" in issues[0]["message"]


def test_port_open_on_a_udp_only_driver_warns():
    definition = _d(transport="udp", discovery={"port_open": [6454]})
    issues = validate_driver_issues(definition, strict=True)
    assert any("can never fire" in i["message"] for i in issues)


def test_port_open_says_nothing_on_a_tcp_driver():
    definition = _d(discovery={"port_open": [4998]})
    assert validate_driver_issues(definition, strict=True) == []


def test_port_open_says_nothing_on_an_http_driver():
    definition = _d(
        transport="http",
        commands={"status": {"method": "GET", "path": "/status"}},
        discovery={"port_open": [8123]},
    )
    assert validate_driver_issues(definition, strict=True) == []


def test_a_second_transport_that_speaks_tcp_makes_the_hint_reachable():
    """An OSC driver that also runs over TCP can be found by a port scan, so
    the hint is real and must not be nagged about."""
    definition = _d(
        transport="osc",
        transports=["osc", "tcp"],
        commands={"go": {"address": "/go"}},
        discovery={"port_open": [10023]},
    )
    assert validate_driver_issues(definition, strict=True) == []


def test_the_port_open_nudge_never_reaches_the_error_list():
    definition = _d(
        transport="osc",
        commands={"go": {"address": "/go"}},
        discovery={"port_open": [10023]},
    )
    assert validate_driver_definition(definition, strict=True) == []


# --- The endpoint itself -----------------------------------------------------


@pytest.fixture()
def driver_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    builtin_dir = tmp_path / "definitions"
    repo_dir = tmp_path / "driver_repo"
    builtin_dir.mkdir()
    repo_dir.mkdir()
    monkeypatch.setattr(
        drivers_routes, "_get_driver_dirs", lambda: (builtin_dir, repo_dir)
    )

    async def _reload_driver(driver_id: str) -> int:
        return 0

    monkeypatch.setattr(
        drivers_routes,
        "_get_engine",
        lambda: SimpleNamespace(devices=SimpleNamespace(reload_driver=_reload_driver)),
    )
    return builtin_dir, repo_dir


async def test_endpoint_reports_issues_for_a_broken_draft():
    result = await validate_driver_definition_draft(_d(commands={"broken": {}}))
    paths = {i["path"] for i in result["issues"]}
    assert "commands.broken" in paths
    assert all(set(i) == {"severity", "message", "path"} for i in result["issues"])


async def test_endpoint_is_clean_for_a_valid_draft():
    assert await validate_driver_definition_draft(_d()) == {"issues": []}


async def test_endpoint_tolerates_the_listing_decorations_an_editor_sends_back():
    """``GET /driver-definitions`` adds ``source``; the editor hands it back."""
    draft = _d()
    draft["source"] = "user"
    draft["_source_file"] = "/somewhere/acme_widget.avcdriver"
    assert await validate_driver_definition_draft(draft) == {"issues": []}


async def test_endpoint_reports_a_non_mapping_body_as_an_issue():
    """A draft that isn't a mapping is an authoring mistake, not a 422.

    The editor renders whatever comes back; a validation error it cannot parse
    is how it goes blank.
    """
    result = await validate_driver_definition_draft(["not", "a", "definition"])
    assert result["issues"] == [
        {
            "severity": "error",
            "message": "Driver definition must be a mapping",
            "path": "",
        }
    ]


async def test_endpoint_writes_nothing(driver_dirs):
    _, repo_dir = driver_dirs
    await validate_driver_definition_draft(_d())
    await validate_driver_definition_draft(_d(commands={"broken": {}}))
    assert list(repo_dir.iterdir()) == []


async def test_endpoint_agrees_with_what_the_save_route_accepts(driver_dirs):
    """One draft through both doors, and they have to say the same thing."""
    _, repo_dir = driver_dirs

    clean = _d()
    assert await validate_driver_definition_draft(clean) == {"issues": []}
    saved = await create_driver_definition(DriverDefinitionRequest(**clean))
    assert saved["status"] == "created"


async def test_endpoint_flags_what_the_save_route_would_refuse():
    from server.api.errors import StructuredApiError

    broken = _d(commands={"broken": {}})
    result = await validate_driver_definition_draft(broken)
    assert [i["severity"] for i in result["issues"]] == ["error"]

    with pytest.raises(StructuredApiError):
        await create_driver_definition(DriverDefinitionRequest(**broken))


# --- The tagging mechanism keeps the old contract ----------------------------


def test_the_error_list_is_still_an_ordinary_list():
    """Every existing caller reads ``validate_driver_definition``'s return as a
    plain ``list[str]`` — the loader joins it, the routes render it, the
    catalog vendors a copy that compares it. The tagging rides along on a list
    subclass precisely so none of that had to change."""
    errors = validate_driver_definition(_d(commands={"broken": {}}), strict=True)
    assert isinstance(errors, list)
    assert all(isinstance(message, str) for message in errors)
    assert errors == list(errors)
    assert validate_driver_definition(_d(), strict=True) == []


def test_a_tag_is_recorded_for_every_message():
    """Nothing may fall out of step: one path per message, always."""
    definition = _d(
        commands={"broken": {}},
        state_variables={"volume": {"type": "zzz"}},
        responses=[{"match": "("}],
        config_schema={"host": {"type": "string", "typo_type": "x"}},
    )
    errors = validate_driver_definition(definition, strict=True)
    assert len(errors.tags) == len(errors)
    assert len(_errors(definition)) == len(errors)
