"""A `null` a YAML driver writes itself leaves the variable empty.

Two places in a definition hold a value the author wrote rather than one the
device sent: a rule's `value:` (or `set:` literal) and a `map:` target. Both
used to go through `str()` before the declared type was applied, so `null`
arrived as the four-character string "None" on a string, integer or number
variable, and as False on a boolean. False is a reading, the opposite of what
the author meant. `null` is now empty (None) whatever the declared type,
which is what the state store uses for "no reading".

Every door that takes such a literal is pinned here, for every declared type,
and each case writes a real value first, so the null is shown replacing a
reading rather than leaving a key that was never set. That first write also
pins the half that must not change: a non-null literal still goes through
`str()` and the declared type exactly as before.

The last test runs the whole loop against the auto-generated simulator: an
empty value on the simulator goes out as the code mapped to null, and the
driver reads it back as empty.

Invented device only (platform-feature test).
"""

import asyncio
import copy

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import YAMLAutoSimulator

# (declared type, a real map target, the value it reads as, the same reading
# as a wire token for rules that read the device's own value)
TYPES = [
    ("string", "alpha", "alpha", "alpha"),
    ("integer", 7, 7, "7"),
    ("number", 0.5, 0.5, "0.5"),
    ("boolean", True, True, "true"),
]
TYPE_IDS = [t[0] for t in TYPES]


def _driver(definition: dict, config: dict | None = None):
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    drv = cls("w1", config or {"host": "127.0.0.1", "port": 4999}, state, events)
    drv._register_declared_children()
    return drv


def _base(transport: str, var_type: str, *, children: bool = False) -> dict:
    d = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": transport,
        "default_config": {"host": "", "port": 4999},
        "state_variables": {"level": {"type": var_type, "label": "Level"}},
        "commands": {},
        "responses": [],
    }
    if transport == "tcp":
        d["delimiter"] = "\r\n"
    if children:
        d["child_entity_types"] = {
            "zone": {
                "label": "Zone",
                "id_format": {"type": "integer", "min": 1, "max": 4},
                "state_variables": {"level": {"type": var_type, "label": "Level"}},
                "instances": {"count": 2, "label": "Zone {id}"},
            },
        }
    return d


def _osc(address, *args):
    from openavc.transport.osc_codec import osc_encode_message

    return osc_encode_message(address, list(args))


# ---------------------------------------------------------------------------
# Text rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_text_rule_map_target_null_is_empty(var_type, target, reads_as, wire):
    d = _base("tcp", var_type)
    d["responses"] = [{
        "match": r"^LVL (\w+)$",
        "mappings": [{
            "group": 1, "state": "level", "type": var_type,
            "map": {"REAL": target, "NONE": None},
        }],
    }]
    drv = _driver(d)

    await drv.on_data_received(b"LVL REAL\r\n")
    assert drv.get_state("level") == reads_as

    await drv.on_data_received(b"LVL NONE\r\n")
    assert drv.get_state("level") is None


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_text_rule_literal_null_is_empty(var_type, target, reads_as, wire):
    d = _base("tcp", var_type)
    d["responses"] = [
        {"match": r"^LVL (\S+)$", "set": {"level": "$1"}},
        {"match": r"^CLR$", "set": {"level": None}},
        {"match": r"^WIPE$", "mappings": [{"state": "level", "value": None}]},
    ]
    drv = _driver(d)

    for clear in (b"CLR\r\n", b"WIPE\r\n"):
        await drv.on_data_received(b"LVL " + wire.encode() + b"\r\n")
        assert drv.get_state("level") == reads_as
        await drv.on_data_received(clear)
        assert drv.get_state("level") is None


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_text_child_rule_map_target_null_is_empty(var_type, target, reads_as, wire):
    d = _base("tcp", var_type, children=True)
    d["responses"] = [{
        "match": r"^Z(\d) LVL (\w+)$",
        "child_set": [{
            "type": "zone", "id": "$1",
            "state": {"level": {"group": 2, "map": {"REAL": target, "NONE": None}}},
        }],
    }]
    drv = _driver(d)

    await drv.on_data_received(b"Z2 LVL REAL\r\n")
    assert drv.get_child_state("zone", 2)["level"] == reads_as

    await drv.on_data_received(b"Z2 LVL NONE\r\n")
    assert drv.get_child_state("zone", 2)["level"] is None


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_text_child_rule_literal_null_is_empty(var_type, target, reads_as, wire):
    d = _base("tcp", var_type, children=True)
    d["responses"] = [
        {
            "match": r"^Z(\d) LVL (\S+)$",
            "child_set": [{"type": "zone", "id": "$1", "state": {"level": "$2"}}],
        },
        {
            "match": r"^Z(\d) CLR$",
            "child_set": [{"type": "zone", "id": "$1", "state": {"level": None}}],
        },
    ]
    drv = _driver(d)

    await drv.on_data_received(b"Z1 LVL " + wire.encode() + b"\r\n")
    assert drv.get_child_state("zone", 1)["level"] == reads_as

    await drv.on_data_received(b"Z1 CLR\r\n")
    assert drv.get_child_state("zone", 1)["level"] is None


# ---------------------------------------------------------------------------
# OSC rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_osc_rule_map_target_null_is_empty(var_type, target, reads_as, wire):
    d = _base("osc", var_type)
    d["responses"] = [{
        "address": "/lvl",
        "mappings": [{
            "arg": 0, "state": "level", "type": var_type,
            "map": {"REAL": target, "NONE": None},
        }],
    }]
    drv = _driver(d)

    await drv.on_data_received(_osc("/lvl", ("s", "REAL")))
    assert drv.get_state("level") == reads_as

    await drv.on_data_received(_osc("/lvl", ("s", "NONE")))
    assert drv.get_state("level") is None


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_osc_child_rule_map_target_null_is_empty(var_type, target, reads_as, wire):
    d = _base("osc", var_type, children=True)
    d["responses"] = [{
        "address": "/z/*/lvl",
        "child_set": [{
            "type": "zone", "id": {"segment": 1},
            "state": {"level": {"arg": 0, "map": {"REAL": target, "NONE": None}}},
        }],
    }]
    drv = _driver(d)

    await drv.on_data_received(_osc("/z/2/lvl", ("s", "REAL")))
    assert drv.get_child_state("zone", 2)["level"] == reads_as

    await drv.on_data_received(_osc("/z/2/lvl", ("s", "NONE")))
    assert drv.get_child_state("zone", 2)["level"] is None


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_osc_child_rule_literal_null_is_empty(var_type, target, reads_as, wire):
    d = _base("osc", var_type, children=True)
    d["responses"] = [
        {
            "address": "/z/*/lvl",
            "child_set": [{
                "type": "zone", "id": {"segment": 1},
                "state": {"level": {"arg": 0, "map": {"REAL": target}}},
            }],
        },
        {
            "address": "/z/*/clr",
            "child_set": [{"type": "zone", "id": {"segment": 1}, "state": {"level": None}}],
        },
    ]
    drv = _driver(d)

    await drv.on_data_received(_osc("/z/1/lvl", ("s", "REAL")))
    assert drv.get_child_state("zone", 1)["level"] == reads_as

    await drv.on_data_received(_osc("/z/1/clr"))
    assert drv.get_child_state("zone", 1)["level"] is None


# ---------------------------------------------------------------------------
# JSON rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_json_rule_map_target_null_is_empty(var_type, target, reads_as, wire):
    d = _base("http", var_type)
    d["responses"] = [{
        "json": True,
        "set": {"level": {
            "key": "lvl", "type": var_type, "map": {"REAL": target, "NONE": None},
        }},
    }]
    drv = _driver(d)

    await drv.on_data_received(b'{"lvl": "REAL"}')
    assert drv.get_state("level") == reads_as

    await drv.on_data_received(b'{"lvl": "NONE"}')
    assert drv.get_state("level") is None


@pytest.mark.parametrize("var_type,target,reads_as,wire", TYPES, ids=TYPE_IDS)
async def test_json_child_rule_map_target_null_is_empty(var_type, target, reads_as, wire):
    d = _base("http", var_type, children=True)
    d["responses"] = [{
        "json": True,
        "child_set": [{
            "type": "zone", "id": 1,
            "state": {"level": {"key": "zones.0", "map": {"REAL": target, "NONE": None}}},
        }],
    }]
    drv = _driver(d)

    await drv.on_data_received(b'{"zones": ["REAL"]}')
    assert drv.get_child_state("zone", 1)["level"] == reads_as

    await drv.on_data_received(b'{"zones": ["NONE"]}')
    assert drv.get_child_state("zone", 1)["level"] is None


# ---------------------------------------------------------------------------
# Against the auto-generated simulator
# ---------------------------------------------------------------------------


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


async def test_an_empty_simulator_value_reads_back_empty():
    """The simulator answers an empty value with the code mapped to null, and
    the driver reads that code back as empty rather than as "None"."""
    d = _base("tcp", "string")
    d["command_suffix"] = "\r\n"
    d["responses"] = [{
        "match": r"^LBL (\w+)$",
        "mappings": [{
            "group": 1, "state": "level", "type": "string",
            "map": {"A": "alpha", "NA": None},
        }],
    }]
    d["polling"] = {"queries": [{"send": "LBL ?\r\n", "query_for": "level"}]}

    sim = YAMLAutoSimulator(d["id"], config={}, driver_def=copy.deepcopy(d))
    await sim.start(0)
    config = {"host": "127.0.0.1", "port": sim.port, "poll_interval": 0}
    drv = _driver(d, config)
    try:
        await drv.connect()

        sim.set_state("level", "alpha")
        await drv.poll()
        await _wait_for(lambda: drv.get_state("level") == "alpha")

        sim.set_state("level", None)
        await drv.poll()
        await _wait_for(lambda: drv.get_state("level") != "alpha")
        assert drv.get_state("level") is None
    finally:
        await drv.disconnect()
        await sim.stop()
