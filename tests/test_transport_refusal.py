"""A driver's ``transport`` that names none of the transports is refused with
one sentence that names them, and a simulated device is served the transport
it connects over.

A YAML driver's null or blank transport is refused where a driver is written
and named on its Installed entry where one is loaded (it reads as tcp); a word
that is no transport is refused at every door. A Python driver's is held to
every transport, refused where it is written and named at load. The simulator
serves the device's own pick from its driver's transports, the way the
driver connects (``transport_choice.effective_transport``); a serial pick is
served over TCP, as simulation points the device at TCP.

Invented drivers (acme_widget) throughout.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import check_driver_definition
from openavc.drivers.check import check_driver_file
from openavc.drivers.python_info import check_python_driver_info
from openavc.drivers.shape_checks import transport_refusal
from openavc.drivers.spec import PYTHON_ONLY_TRANSPORTS, YAML_TRANSPORTS
from openavc.simulator.yaml_auto import YAMLAutoSimulator

_YAML_NAMED = "'tcp', 'serial', 'udp', 'http', 'osc' or 'bridge'"

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
}


def _yaml(transport: Any, **extra: Any) -> dict[str, Any]:
    return {**BASE, "transport": transport, **extra}


# ── The sentence ──


def test_the_yaml_list_is_the_one_the_sentence_names() -> None:
    assert ", ".join(f"'{t}'" for t in YAML_TRANSPORTS[:-1]) + f" or '{YAML_TRANSPORTS[-1]}'" == _YAML_NAMED


@pytest.mark.parametrize("value, shown", [
    (None, "null"), ("", "''"), ("foo", "'foo'"), (5, "5"), (["tcp"], "['tcp']"), ("TCP", "'TCP'"),
])
def test_a_yaml_transport_that_is_none_of_them_names_them(value: Any, shown: str) -> None:
    assert transport_refusal(value) == f"transport: must be {_YAML_NAMED}, not {shown}"


def test_a_python_only_transport_in_a_yaml_driver_says_so() -> None:
    assert transport_refusal("ssh") == f"transport: 'ssh' needs a Python driver; a YAML driver takes {_YAML_NAMED}"


@pytest.mark.parametrize("value", YAML_TRANSPORTS)
def test_a_yaml_transport_passes(value: str) -> None:
    assert transport_refusal(value) is None


@pytest.mark.parametrize("value", YAML_TRANSPORTS + PYTHON_ONLY_TRANSPORTS)
def test_every_transport_passes_a_python_driver(value: str) -> None:
    assert transport_refusal(value, "python") is None


def test_a_python_drivers_null_names_every_transport() -> None:
    sentence = transport_refusal(None, "python")
    assert sentence.endswith(", not None")
    assert all(f"'{t}'" in sentence for t in YAML_TRANSPORTS + PYTHON_ONLY_TRANSPORTS)


# ── A YAML driver at each door ──


@pytest.mark.parametrize("value, shown", [(None, "null"), ("", "''")])
def test_a_null_or_blank_yaml_transport_is_refused_where_written_and_named_at_load(
    value: Any, shown: str,
) -> None:
    sentence = f"transport: must be {_YAML_NAMED}, not {shown}"
    strict = check_driver_definition(_yaml(value), strict=True)
    assert strict.errors == [sentence]
    loaded = check_driver_definition(_yaml(value))
    assert (loaded.errors, loaded.notices) == ([], [sentence])


@pytest.mark.parametrize("value", ["foo", "ssh", 5])
def test_a_yaml_transport_that_is_no_transport_is_refused_at_every_door(value: Any) -> None:
    sentence = transport_refusal(value)
    assert check_driver_definition(_yaml(value), strict=True).errors == [sentence]
    assert check_driver_definition(_yaml(value)).errors == [sentence]


# ── A Python driver ──


def _info(transport: Any) -> dict[str, Any]:
    return {**BASE, "transport": transport, "commands": {}}


@pytest.mark.parametrize("value", [None, "", "foo"])
def test_a_python_drivers_transport_that_is_none_of_them_is_refused_where_written(value: Any) -> None:
    sentence = transport_refusal(value, "python")
    assert sentence in check_python_driver_info(_info(value), strict=True).errors
    loaded = check_python_driver_info(_info(value))
    assert sentence not in loaded.errors
    assert sentence in loaded.notices


@pytest.mark.parametrize("value", ["ssh", "tcp"])
def test_a_python_drivers_transport_passes(value: str) -> None:
    verdict = check_python_driver_info(_info(value), strict=True)
    assert not [e for e in verdict.errors if e.startswith("transport:")]


def test_a_python_driver_with_no_transport_is_not_refused() -> None:
    info = {k: v for k, v in _info("tcp").items() if k != "transport"}
    verdict = check_python_driver_info(info, strict=True)
    assert not [e for e in verdict.errors if e.startswith("transport:")]


def test_the_checker_refuses_a_python_drivers_null_transport(tmp_path: Path) -> None:
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''\
        from openavc.drivers.base import BaseDriver


        class AcmeWidgetDriver(BaseDriver):
            DRIVER_INFO = {
                "id": "acme_widget",
                "name": "Acme Widget",
                "manufacturer": "Acme",
                "category": "utility",
                "version": "1.0.0",
                "transport": None,
                "commands": {},
                "state_variables": {},
            }
    '''), encoding="utf-8")
    assert transport_refusal(None, "python") in check_driver_file(driver).errors


# ── The simulator serves the transport the device connects over ──


def _picks(declared: Any = "tcp") -> dict[str, Any]:
    return _yaml(declared, transports=["tcp", "udp", "serial"], default_config={"host": "", "port": 5100})


def test_a_device_that_picks_udp_is_served_udp() -> None:
    sim = YAMLAutoSimulator("acme_widget", config={"transport": "udp"}, driver_def=_picks())
    assert sim.capacity_kind == "datagram"
    assert sim.SIMULATOR_INFO["transport"] == "udp"


def test_a_device_that_picks_nothing_is_served_its_drivers_transport() -> None:
    sim = YAMLAutoSimulator("acme_widget", config={}, driver_def=_picks())
    assert sim.capacity_kind == "stream"
    assert sim.SIMULATOR_INFO["transport"] == "tcp"


def test_a_device_that_picks_serial_is_served_over_tcp_as_one_cable() -> None:
    sim = YAMLAutoSimulator("acme_widget", config={"transport": "serial"}, driver_def=_picks())
    assert sim.capacity_kind == "stream"
    assert sim.SIMULATOR_INFO["transport"] == "serial"
    assert sim.single_client_supported is False


def test_a_driver_written_with_a_null_transport_is_served_tcp() -> None:
    sim = YAMLAutoSimulator("acme_widget", config={}, driver_def=_picks(None))
    assert sim.capacity_kind == "stream"
    assert sim.SIMULATOR_INFO["transport"] == "tcp"
