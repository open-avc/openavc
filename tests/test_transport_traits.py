"""The transport table (``drivers/spec.py`` ``TRANSPORT_TRAITS``) is THE answer
to what a transport is and can do.

Each row is held complete, the platform's connection builders and simulator
servers are held to the rows, and the lists the schema and the checks' messages
read are held to the values they had before they became views of the table.
"""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

from openavc.drivers import spec
from openavc.drivers.spec import TRANSPORT_TRAITS, TransportTraits

# What each view read before it became a column of the table, in order: the
# schema's enums, the checks' messages and the IDE's pickers print these.
_VIEWS_AS_THEY_WERE = {
    "YAML_TRANSPORTS": ("tcp", "serial", "udp", "http", "osc", "bridge"),
    "PYTHON_ONLY_TRANSPORTS": ("ssh", "mqtt", "snmp"),
    "INTERCHANGEABLE_TRANSPORTS": ("tcp", "serial", "udp", "http", "osc"),
    "AUTH_TRANSPORTS": ("tcp", "serial"),
    "LIVENESS_TRANSPORTS": ("tcp", "serial", "udp", "osc"),
    "SILENCE_CHECK_TRANSPORTS": ("tcp", "udp", "osc"),
    "LINE_GAP_TRANSPORTS": ("tcp", "serial"),
    "PORT_OPEN_TRANSPORTS": ("tcp", "http"),
}

# The simulator server each `simulator` value names, as its module and class.
_SIMULATOR_CLASSES = {
    "tcp": ("openavc.simulator.tcp_simulator", "TCPSimulator"),
    "udp": ("openavc.simulator.udp_simulator", "UDPSimulator"),
    "http": ("openavc.simulator.http_simulator", "HTTPSimulator"),
    "osc": ("openavc.simulator.osc_simulator", "OSCSimulator"),
    "mqtt": ("openavc.simulator.mqtt_simulator", "MQTTSimulator"),
    "snmp": ("openavc.simulator.snmp_simulator", "SNMPSimulator"),
}

_LINE_FORMS = {"raw", "osc", "http", None}


@pytest.mark.parametrize("view, values", sorted(_VIEWS_AS_THEY_WERE.items()))
def test_each_view_reads_what_it_did(view, values):
    assert getattr(spec, view) == values


def test_the_table_has_a_row_for_every_transport_a_driver_may_declare():
    assert tuple(TRANSPORT_TRAITS) == spec.YAML_TRANSPORTS + spec.PYTHON_ONLY_TRANSPORTS
    transport_field = spec.FIELDS["transport"]
    assert tuple(transport_field["python_enum"]) == tuple(TRANSPORT_TRAITS)


@pytest.mark.parametrize("name", list(TRANSPORT_TRAITS))
def test_each_row_answers_every_column(name):
    row = TRANSPORT_TRAITS[name]
    assert isinstance(row, TransportTraits)
    for column in TransportTraits._fields:
        value = getattr(row, column)
        if column == "simulator":
            assert value is None or value in _SIMULATOR_CLASSES, (name, value)
        elif column == "line_form":
            assert value in _LINE_FORMS, (name, value)
        else:
            assert isinstance(value, bool), (name, column, value)


def test_a_listed_transport_is_one_a_driver_may_declare():
    for name, row in TRANSPORT_TRAITS.items():
        if row.interchangeable:
            assert row.yaml, name


def test_every_transport_the_platform_connects_over_has_a_builder():
    """A device opens its connection through the ladder's builders; the only
    row with none is the bridge sentinel, whose commands ride a bridge."""
    from openavc.drivers.transport_ladder import _BUILDERS

    opens_its_own = {name for name, row in TRANSPORT_TRAITS.items() if row.simulator is not None}
    assert set(_BUILDERS) == opens_its_own
    assert set(TRANSPORT_TRAITS) - opens_its_own == {"bridge"}


@pytest.mark.parametrize("server", sorted(_SIMULATOR_CLASSES))
def test_each_simulator_server_the_table_names_exists(server):
    module_name, class_name = _SIMULATOR_CLASSES[server]
    module = importlib.import_module(module_name)
    assert isinstance(getattr(module, class_name), type)


def test_a_stand_in_is_named_only_for_a_transport_the_simulator_does_not_serve():
    stood_in = {
        name: spec.simulator_stand_in(name) for name in TRANSPORT_TRAITS
        if spec.simulator_stand_in(name) is not None
    }
    assert stood_in == {"serial": "tcp", "ssh": "tcp"}
    assert spec.simulator_stand_in("bridge") is None
    assert spec.simulator_stand_in("nonsense") is None
    assert spec.simulator_stand_in(None) is None


def test_an_unknown_name_has_no_row():
    assert spec.transport_traits("nonsense") is None
    assert spec.transport_traits(None) is None
    assert spec.transport_traits(["tcp"]) is None
    assert spec.transport_traits("tcp") is TRANSPORT_TRAITS["tcp"]


# --- no list of its own -------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = _ROOT / "openavc"

# Lists of transport names that are not a transport's traits, and why.
_NOT_TRAITS = {
    # A plugin's declared port is a TCP or UDP socket, not a driver transport.
    "openavc/core/plugin_declarations.py",
    # The command forms a read-back command takes (spec.SEND_FORMS), which
    # share two names with transports.
    "openavc/drivers/command_checks.py",
    # Traffic channels, which name the push listeners beside the transports.
    "openavc/audit/observe.py",
}


def _transport_names(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return []
    return [
        element.value for element in node.elts
        if isinstance(element, ast.Constant) and element.value in TRANSPORT_TRAITS
    ]


def _python_lists() -> list[str]:
    found = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel == "openavc/drivers/spec.py" or rel in _NOT_TRAITS or "node_modules" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            names = _transport_names(node)
            if len(names) >= 2:
                found.append(f"{rel}:{node.lineno} {names}")
    return found


_TS_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_TS_NAME = "|".join(TRANSPORT_TRAITS)
_TS_LIST = re.compile(
    rf"[\[\(\{{]\s*([\"'])({_TS_NAME})\1\s*,\s*([\"'])({_TS_NAME})\3"
)


def _typescript_lists() -> list[str]:
    found = []
    for app in ("programmer", "simulator"):
        src = _PACKAGE / "web" / app / "src"
        for path in sorted([*src.rglob("*.ts"), *src.rglob("*.tsx")]):
            name = path.name
            if name.endswith(".gen.ts") or ".test." in name:
                continue
            text = _TS_COMMENT.sub("", path.read_text(encoding="utf-8"))
            for match in _TS_LIST.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(_ROOT).as_posix()}:{line} {match.group(0)}")
    return found


def test_no_module_lists_transports_for_itself():
    """A reader asks the table (spec.transport_traits, or transportTraits.ts in
    the Programmer) rather than listing the transports that have a trait: a
    list of its own is the copy that misses the next transport."""
    assert _python_lists() == []
    assert _typescript_lists() == []


def test_the_scan_finds_a_list():
    tree = ast.parse('X = frozenset({"tcp", "udp"})\nY = ("tcp", "other")\n')
    lists = [sorted(names) for node in ast.walk(tree) if len(names := _transport_names(node)) >= 2]
    assert lists == [["tcp", "udp"]]
    assert _TS_LIST.search('const NETWORK = ["tcp", "udp", "http"];')
    assert not _TS_LIST.search(_TS_COMMENT.sub("", '// e.g. ["tcp", "serial"]'))
