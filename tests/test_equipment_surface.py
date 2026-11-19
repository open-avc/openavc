"""The equipment surface in drivers/spec.py: what a product's served model may
declare, and the floor every release that speaks the equipment protocol
reads it at."""

from openavc.drivers.equipment_protocol import PROTOCOL
from openavc.drivers.spec import (
    DEFS,
    EQUIPMENT_BUILD_COMMAND_FIELDS,
    EQUIPMENT_BUILD_FIELDS,
    EQUIPMENT_PRODUCT_COMMAND_FIELDS,
    EQUIPMENT_PRODUCT_FIELDS,
    EQUIPMENT_SINCE,
    FIELDS,
    equipment_floor_problems,
    parse_version,
)

_TOP = EQUIPMENT_PRODUCT_FIELDS + EQUIPMENT_BUILD_FIELDS
_COMMAND = EQUIPMENT_PRODUCT_COMMAND_FIELDS + EQUIPMENT_BUILD_COMMAND_FIELDS
_COMMAND_ENTRY = DEFS["commandEntry"]["fields"]


def test_every_name_in_the_surface_is_a_contract_field():
    assert set(_TOP) <= set(FIELDS)
    assert set(_COMMAND) <= set(_COMMAND_ENTRY)


def test_a_product_never_declares_what_the_build_adds():
    assert not set(EQUIPMENT_PRODUCT_FIELDS) & set(EQUIPMENT_BUILD_FIELDS)
    assert not set(EQUIPMENT_PRODUCT_COMMAND_FIELDS) & set(EQUIPMENT_BUILD_COMMAND_FIELDS)


def test_the_floor_is_the_first_release_of_this_protocol():
    """A new protocol comes with its own floor: change both together."""
    assert (PROTOCOL, EQUIPMENT_SINCE) == ("openavc-device-1", "0.37.0")


def _newer_than_the_floor():
    """Every place the surface admits whose field needs a release newer than
    the floor."""
    floor = parse_version(EQUIPMENT_SINCE)
    found = set()

    def walk(node, where, refs):
        if not isinstance(node, dict):
            return
        if "ref" in node:
            if node["ref"] not in refs:
                walk(DEFS[node["ref"]], where, refs | {node["ref"]})
            return
        if node.get("since") and parse_version(node["since"]) > floor:
            found.add(where)
        for value, version in (node.get("since_values") or {}).items():
            if parse_version(version) > floor:
                found.add(f"{where}={value}")
        fields = node.get("fields") or {}
        if where.endswith("commands.*"):
            fields = {key: sub for key, sub in fields.items() if key in _COMMAND}
        for key, sub in fields.items():
            walk(sub, f"{where}.{key}", refs)
        for key in ("extra", "items"):
            walk(node.get(key), f"{where}.*", refs)
        for combinator in ("one_of", "any_of", "all_of"):
            for branch in node.get(combinator, ()):
                walk(branch, where, refs)

    for name in _TOP:
        walk(FIELDS[name], name, frozenset())
    return found


# A YAML driver's setting write field no product carries: a product's write
# runs one of its own commands (equipment.py reads the write's body as that
# command and its params), so an HTTP write's query parameters are never a
# product's, and equipment_floor_problems names one a model carries.
_YAML_WRITE_ONLY = {"device_settings.*.write.query_params"}


def test_nothing_in_the_surface_is_newer_than_its_floor_but_the_file_forms():
    """A field the floor release does not read would be dropped by every
    release before it, so it joins the surface with a new protocol. A file
    parameter is the one exception: equipment refuses it at connect. A YAML
    write's own HTTP field is not one a product writes."""
    newer = _newer_than_the_floor() - _YAML_WRITE_ONLY
    assert newer, "the walk reached nothing"
    assert all(".params.*." in place for place in newer), sorted(newer)
    assert {place.rsplit(".", 1)[-1] for place in newer} == {"accept", "max_bytes", "type=file"}


def test_a_model_using_a_field_past_the_floor_is_named():
    model = {
        "id": "acme_widget", "name": "Widget", "transport": "http",
        "commands": {"load": {"params": {"show": {"type": "file", "accept": [".show"]}}}},
    }
    assert equipment_floor_problems(model) == [
        "commands.load.params.show.accept needs OpenAVC 0.37.2; a product works with every "
        "release from 0.37.0, so describe it without this",
        "commands.load.params.show.type: file needs OpenAVC 0.37.2; a product works with every "
        "release from 0.37.0, so describe it without this",
    ]
    del model["commands"]["load"]["params"]
    assert equipment_floor_problems(model) == []

