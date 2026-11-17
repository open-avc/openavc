"""The Programmer's driver types promise no more than the driver contract.

``contract_gen_ts`` renders the driver-definition interfaces the Driver
Builder edits (``types.gen.ts``) from the registry in ``spec.py``. The
byte-compare in ``test_driver_contract_generated.py`` proves the file
matches its generator; these prove the generator matches the contract: no
member type is narrower than the registry node it presents, and a member is
required only where the registry requires it or every door refuses a driver
without it (``RUNTIME_REQUIRED``, each entry proven here by a driver the
loader refuses).

The checker is ``openavc/drivers/ts_type_check.py``; the must-fail cases put
back narrowings the generated file once carried, so a check that stopped
biting fails here instead of passing quietly.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

import pytest

from openavc.drivers import contract_gen_ts as gen
from openavc.drivers import ts_type_check
from openavc.drivers.avcdriver_semantic import check_driver_definition

WIDGET: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "default_config": {"host": "", "port": 23},
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {"power_on": {"label": "Power On", "send": "PWR1"}},
}


def _label(d: dict[str, Any], present: bool) -> None:
    if not present:
        del d["state_variables"]["power"]["label"]


def _top_parser(d: dict[str, Any], present: bool) -> None:
    d["frame_parser"] = {"header_size": 2, **({"type": "length_prefix"} if present else {})}


def _push_parser(d: dict[str, Any], present: bool) -> None:
    parser = {"header_size": 2, **({"type": "length_prefix"} if present else {})}
    d["push"] = {"type": "tcp_listener", "port": 9000, "frame_parser": parser}


# What each RUNTIME_REQUIRED member's guarantee is proven by: the places a
# driver writes it, each loading with the member and refused without it.
PROOFS: dict[tuple[str, str], tuple[Callable[[dict[str, Any], bool], None], ...]] = {
    ("DriverStateVarDef", "label"): (_label,),
    ("DriverFrameParserShape", "type"): (_top_parser, _push_parser),
}


def _errors(place: Callable[[dict[str, Any], bool], None], present: bool) -> list[str]:
    driver = copy.deepcopy(WIDGET)
    place(driver, present)
    return list(check_driver_definition(driver).errors)


def test_the_rendered_types_promise_no_more_than_the_contract() -> None:
    assert gen.contract_narrowings() == []


def test_every_member_marked_required_without_the_registry_is_proven() -> None:
    listed = {(name, m) for name, members in gen.RUNTIME_REQUIRED.items() for m in members}
    assert listed == set(PROOFS)


@pytest.mark.parametrize("where", sorted(PROOFS), ids=lambda w: ".".join(w))
def test_a_driver_without_a_required_member_is_refused_at_load(where: tuple[str, str]) -> None:
    for place in PROOFS[where]:
        assert _errors(place, present=True) == [], f"{place.__name__} with the member does not load"
        assert _errors(place, present=False), f"{place.__name__} loads without it: not guaranteed"


# --- the check bites ----------------------------------------------------------


def _with_override(name: str, member: str, override: dict[str, Any]) -> tuple[Any, ...]:
    rows = []
    for ts_name, getter, overrides, options in gen.INTERFACES:
        if ts_name == name:
            overrides = {**overrides, member: override}
        rows.append((ts_name, getter, overrides, options))
    return tuple(rows)


def test_a_record_of_text_over_values_of_any_kind_is_narrower(monkeypatch) -> None:
    monkeypatch.setattr(
        gen, "INTERFACES",
        _with_override("DriverCommandDef", "headers", {"type": "Record<string, string>"}),
    )
    assert any(line.startswith("DriverCommandDef.headers:") for line in gen.contract_narrowings())


def test_a_text_only_enum_option_is_narrower(monkeypatch) -> None:
    narrow = gen.Alias(
        "EnumOption", lambda: "", source="enumValue", ts="string | { value: string; label?: string }",
    )
    monkeypatch.setattr(
        gen, "ALIASES", tuple(narrow if a.name == "EnumOption" else a for a in gen.ALIASES),
    )
    found = gen.contract_narrowings()
    assert any(line.startswith("EnumOption:") for line in found)
    assert any(line.startswith("DriverParamDef.values:") for line in found)


def test_a_name_presenting_one_branch_of_a_definition_is_narrower(monkeypatch) -> None:
    # The mDNS fingerprint interface presents only an entry's mapping form;
    # an entry may also be a bare service string.
    monkeypatch.setitem(gen.DEF_TS_NAMES, "mdnsItem", "DriverDiscoveryMdnsFingerprint")
    assert any(
        line.startswith("DriverDiscoveryConfig.mdns:") for line in gen.contract_narrowings()
    )


def test_a_member_required_beyond_the_contract_is_narrower() -> None:
    mapping = gen.spec.DEFS["mappingEntry"]
    table = ts_type_check.TypeTable(
        {"DriverResponseMapping": {"group": ("number", True), "state": ("string", False)}},
        {},
        gen.spec.DEFS,
    )
    found = ts_type_check.narrowings(table, [], [("DriverResponseMapping", mapping)], [])
    assert found == [
        "DriverResponseMapping.group: required, though the contract lets a driver leave it out"
    ]


def test_the_generator_refuses_to_render_a_narrowing(monkeypatch) -> None:
    monkeypatch.setattr(
        gen, "INTERFACES",
        _with_override("DriverResponseMapping", "map", {"type": "Record<string, string>"}),
    )
    with pytest.raises(ValueError, match="promise more than the contract"):
        gen.render_types_ts()


def test_requiredness_is_never_an_override(monkeypatch) -> None:
    monkeypatch.setattr(
        gen, "INTERFACES", _with_override("DriverResponseMapping", "group", {"req": True}),
    )
    with pytest.raises(ValueError, match="RUNTIME_REQUIRED"):
        gen.render_types_ts()


@pytest.mark.parametrize("text", ["Map<string, string>", "string |", "{ a: string", "Partial<X>"])
def test_type_text_the_checker_cannot_read_is_refused(text: str) -> None:
    with pytest.raises(ts_type_check.TsTypeError):
        ts_type_check.parse(text)


def test_a_member_the_contract_does_not_name_takes_what_an_open_object_holds() -> None:
    # headers declares no keys and leaves its values open, so a key spelled
    # out with text values is narrower than the contract.
    table = ts_type_check.TypeTable({}, {}, gen.spec.DEFS)
    open_node = gen.spec.DEFS["commandEntry"]["fields"]["headers"]
    assert ts_type_check.narrowings(table, [("C.headers", "{ accept?: string }", open_node)], [], [])
    assert not ts_type_check.narrowings(
        table, [("C.headers", "{ accept?: unknown }", open_node)], [], [],
    )


def test_a_name_the_file_does_not_define_is_refused() -> None:
    table = ts_type_check.TypeTable({}, {}, gen.spec.DEFS)
    with pytest.raises(ts_type_check.TsTypeError, match="not defined"):
        ts_type_check.narrowings(table, [("X.y", "DriverNoSuchType", {"type": "string"})], [], [])
