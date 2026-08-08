"""min_platform_version is computed from the contract, not remembered.

Every field the platform grew carries the release that grew it (``since`` in
the driver-contract registry). That makes "what platform does this driver
need" a property of the definition rather than something an author has to
recall, and it makes an under-declared ``min_platform_version`` catchable:
the install gate honours the declaration, so a driver claiming too low a
floor installs on a release that reads the file, silently ignores the fields
it doesn't know, and runs wrong.

Covered here: the computation (which fields, which nesting, which enum
values), the rule that compares it against the declaration, that both driver
formats get the same verdict, and a sweep proving no shipped driver is
currently under-declared.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from openavc.drivers import spec
from openavc.drivers.avcdriver_semantic import (
    platform_version_errors,
    validate_driver_definition,
)
from openavc.drivers.python_info import python_driver_info_issues
from openavc.drivers.spec import (
    parse_version,
    platform_requirements,
    required_platform_version,
)
from tests import gates


def _d(**over):
    """A minimal valid definition with per-case overrides."""
    base = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"noop": {"send": "NOOP\r"}},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
    }
    base.update(over)
    return base


# --- what the walk finds -----------------------------------------------------


def test_a_plain_driver_has_no_floor():
    """Nothing gated, nothing to declare."""
    assert platform_requirements(_d()) == []
    assert required_platform_version(_d()) is None
    assert platform_version_errors(_d(), require_declaration=True) == []


def test_a_top_level_field_reports_its_own_floor():
    reqs = platform_requirements(_d(web_ui=True))
    assert reqs == [("web_ui", "0.24.0")]


def test_the_highest_floor_wins_and_every_field_is_still_listed():
    definition = _d(
        web_ui=True,                       # 0.24.0
        command_suffix="\r",               # 0.23.0
        liveness={"send": "P\r", "interval": 30},   # 0.22.0
    )
    reqs = dict(platform_requirements(definition))
    assert reqs == {
        "web_ui": "0.24.0",
        "command_suffix": "0.23.0",
        "liveness": "0.22.0",
    }
    assert required_platform_version(definition) == "0.24.0"


def test_a_nested_field_is_reported_by_its_path():
    definition = _d(
        commands={
            "say": {
                "send": "SAY {text}\r",
                "params": {"text": {"type": "string", "trim": False}},
            }
        }
    )
    assert platform_requirements(definition) == [
        ("commands.say.params.text.trim", "0.22.0")
    ]


def test_an_enum_value_can_carry_its_own_floor():
    """Whole fields aren't the only thing that lands in a release.

    ``config_schema`` shipped long before its ``table`` row editor did, so
    the floor belongs to the value, not the field.
    """
    text = _d(config_schema={"notes": {"type": "text", "label": "Notes"}})
    assert platform_requirements(text) == []

    table = _d(
        config_schema={
            "registers": {
                "type": "table",
                "label": "Registers",
                "columns": {"addr": {"type": "integer"}},
            }
        }
    )
    assert platform_requirements(table) == [
        ("config_schema.registers.type: table", "0.23.0")
    ]


def test_an_alternative_shape_does_not_impose_its_floor():
    """``on_connect`` holds three unrelated shapes in one list.

    A bare string is the oldest of them. It must not pick up the floor of
    the per-child template that shares the list, or every driver written
    before child entities existed would read as needing them.
    """
    assert platform_requirements(_d(on_connect=["PWR?\r"])) == []

    gated = _d(on_connect=[{"send": "LVL?\r", "when": "enable_meters"}])
    assert platform_requirements(gated) == [("on_connect[0].when", "0.23.0")]

    per_child = _d(
        child_entity_types={"input": {"label": "Input"}},
        on_connect=[{"each_child": "input", "send": "IN{child_id}?\r"}],
    )
    assert dict(platform_requirements(per_child))["on_connect[0]"] == "0.22.0"


# --- the rule ----------------------------------------------------------------


def test_an_undershooting_declaration_is_rejected_and_names_the_field():
    errors = platform_version_errors(_d(min_platform_version="0.23.0", web_ui=True))
    assert len(errors) == 1
    assert "web_ui" in errors[0]
    assert "0.24.0" in errors[0]
    assert "0.23.0" in errors[0]


def test_a_sufficient_declaration_passes():
    assert platform_version_errors(_d(min_platform_version="0.24.0", web_ui=True)) == []
    assert platform_version_errors(_d(min_platform_version="1.2.0", web_ui=True)) == []


def test_a_pre_release_declaration_counts_as_its_release():
    assert (
        platform_version_errors(_d(min_platform_version="0.24.0-rc1", web_ui=True))
        == []
    )


def test_a_missing_declaration_is_only_a_publishing_error():
    """Local authoring has no install gate to satisfy; publishing does."""
    definition = _d(web_ui=True)
    assert platform_version_errors(definition) == []

    errors = platform_version_errors(definition, require_declaration=True)
    assert len(errors) == 1
    assert "0.24.0" in errors[0] and "web_ui" in errors[0]


def test_a_declaration_that_is_not_a_version_is_reported():
    errors = platform_version_errors(_d(min_platform_version="latest", web_ui=True))
    assert len(errors) == 1
    assert "version number" in errors[0]


def test_the_message_stops_listing_fields_but_says_how_many_it_dropped():
    definition = _d(
        min_platform_version="0.9.0",
        commands={
            f"cmd{i}": {"send": f"C{i}\r", "query_for": "power"}
            for i in range(9)
        },
    )
    (message,) = platform_version_errors(definition)
    assert "(+5 more)" in message


def test_the_rule_runs_in_strict_validation_only():
    """The loader must never drop a driver over this.

    A driver written for a newer platform vanishing at load takes its
    devices offline with it — worse than the wrong declaration. Authoring
    gates are where it belongs.
    """
    definition = _d(min_platform_version="0.23.0", web_ui=True)
    assert validate_driver_definition(definition) == []
    assert any(
        "min_platform_version" in err
        for err in validate_driver_definition(definition, strict=True)
    )


def test_a_python_driver_gets_the_same_verdict():
    info = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "min_platform_version": "0.22.0",
        "push": {"type": "multicast", "group": "239.0.0.100", "port": 17000},
    }
    assert any("0.23.0" in issue for issue in python_driver_info_issues(info))

    info["min_platform_version"] = "0.23.0"
    assert not any("min_platform_version" in i for i in python_driver_info_issues(info))


# --- the annotations themselves ----------------------------------------------


def _annotated_nodes(node, seen=frozenset()):
    """Every registry node carrying a floor, with the versions it names."""
    if not isinstance(node, dict):
        return
    if node.get("since"):
        yield node["since"]
    yield from (node.get("since_values") or {}).values()
    if "ref" in node and node["ref"] not in seen:
        yield from _annotated_nodes(spec.DEFS[node["ref"]], seen | {node["ref"]})
    for sub in (node.get("fields") or {}).values():
        yield from _annotated_nodes(sub, seen)
    for key in ("items", "extra", "prop_names"):
        yield from _annotated_nodes(node.get(key), seen)
    for combinator in ("one_of", "any_of", "all_of"):
        for branch in node.get(combinator, ()):
            yield from _annotated_nodes(branch, seen)


def test_every_declared_floor_is_a_version_number():
    """A typo'd floor is inert: nothing would ever compare against it."""
    versions = [v for f in spec.FIELDS.values() for v in _annotated_nodes(f)]
    assert versions, "no since annotations found — did the key get renamed?"
    bad = sorted({v for v in versions if parse_version(v) is None})
    assert not bad, f"since values that are not version numbers: {bad}"


def test_a_floor_reaches_the_published_documentation():
    """The sentence an author reads is rendered from the enforced value.

    Written twice, the prose and the rule drift; the prose is what a driver
    author actually goes by, so the drift lands on them.
    """
    doc = spec.node_doc(spec.FIELDS["web_ui"])
    assert doc.endswith("Requires platform 0.24.0.")
    kind = spec.node_doc(spec.DEFS["actionEntry"]["fields"]["kind"])
    assert 'Value "link" requires platform 0.24.0.' in kind


# --- the shipped corpus ------------------------------------------------------
#
# A contract-wide property, checked over every driver: raising a floor in the
# registry is exactly the change that can turn a correct declaration into a
# wrong one, and this says so in the same commit rather than in the community
# repo's CI a day later. Names no product and reads no per-driver fixture.

DRIVERS_ROOT = Path(
    os.environ.get("OPENAVC_DRIVERS_ROOT")
    or Path(__file__).resolve().parent.parent.parent / "openavc-drivers"
)
CORPUS = (
    sorted(DRIVERS_ROOT.rglob("*.avcdriver")) if DRIVERS_ROOT.exists() else []
)


@gates.skipif_missing(
    gates.DRIVER_CORPUS,
    None if CORPUS else f"no community drivers found at {DRIVERS_ROOT}",
)
@pytest.mark.parametrize("driver_path", CORPUS, ids=lambda p: p.name)
def test_no_shipped_driver_under_declares_its_floor(driver_path: Path) -> None:
    definition = yaml.safe_load(driver_path.read_text(encoding="utf-8"))
    assert isinstance(definition, dict), f"{driver_path.name} is not a mapping"
    errors = platform_version_errors(definition, require_declaration=True)
    assert not errors, f"{driver_path.name}: {errors[0]}"
