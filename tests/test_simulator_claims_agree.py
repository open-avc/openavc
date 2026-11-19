"""The driver ids a simulator file serves, read from its source
(``drivers/simulator_info.simulator_claims``, which the community catalog asks
because it cannot import a simulator), agree with what the simulator's loader
finds when it imports the file (``simulator/simulator_file.load_simulator_file``).

Platform test: invented simulator files for invented drivers (``acme_*``).
"""

from __future__ import annotations

import pytest

from openavc.drivers.simulator_info import (
    SimulatorClaim,
    check_simulator_file,
    claim_conflicts,
    simulator_claims,
)
from openavc.simulator.simulator_file import load_simulator_file

_HEAD = (
    "from __future__ import annotations\n"
    "from openavc.simulator.tcp_simulator import TCPSimulator\n"
)


def _cls(name: str, driver_id: str | None = "acme_widget", base: str = "TCPSimulator") -> str:
    table = "" if driver_id is None else (
        f'    SIMULATOR_INFO = {{"driver_id": "{driver_id}", "name": "{name}", "initial_state": {{}}}}\n'
    )
    return (
        f"class {name}({base}):\n{table}"
        "    def handle_command(self, data):\n"
        "        return None\n"
    )


CORPUS = {
    "one_class": _cls("AcmeWidgetSimulator"),
    "a_second_name": _cls("AcmeWidgetSimulator") + "SIMULATOR_CLASS = AcmeWidgetSimulator\n",
    "two_classes_one_id": _cls("AlphaSimulator") + _cls("ZetaSimulator"),
    "three_classes_one_id": _cls("AlphaSimulator") + _cls("BetaSimulator") + _cls("ZetaSimulator"),
    "a_subclass_inheriting_the_id": _cls("AcmeBase") + _cls("AcmeWidgetSimulator", None, "AcmeBase"),
    "a_base_naming_no_id": _cls("AcmeBase", "") + _cls("AcmeWidgetSimulator", base="AcmeBase"),
    "a_subclass_dropping_the_id": (
        _cls("AcmeBase")
        + "class AcmeQuiet(AcmeBase):\n"
        '    SIMULATOR_INFO = {"name": "quiet"}\n'
    ),
    "two_classes_two_ids": _cls("AcmeWidgetSimulator") + _cls("AcmeGadgetSimulator", "acme_gadget"),
    "a_conflict_beside_a_served_id": (
        _cls("AlphaSimulator") + _cls("ZetaSimulator") + _cls("AcmeGadgetSimulator", "acme_gadget")
    ),
    "a_dataclass_beside_it": (
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\n"
        "class Reading:\n"
        "    level: int\n"
        + _cls("AcmeWidgetSimulator")
    ),
    "no_simulator_info": _cls("AcmeWidgetSimulator", None),
    "a_class_deriving_from_nothing": (
        "class Table:\n"
        '    SIMULATOR_INFO = {"driver_id": "acme_widget"}\n'
    ),
}


@pytest.mark.parametrize("case", sorted(CORPUS))
def test_the_source_reading_agrees_with_the_import(tmp_path, case):
    path = tmp_path / f"{case}_sim.py"
    path.write_text(_HEAD + CORPUS[case], encoding="utf-8")

    imported = load_simulator_file(path)
    claims = simulator_claims(path)
    refused = claim_conflicts((c.class_name, c.driver_id) for c in claims)
    served = {c.driver_id: c.class_name for c in claims if c.driver_id not in refused}

    assert {i: cls.__name__ for i, cls in imported.classes.items()} == served
    assert imported.refused == refused


def test_an_id_written_in_code_is_a_claim_of_an_unknown_id(tmp_path):
    path = tmp_path / "acme_widget_sim.py"
    path.write_text(
        _HEAD
        + 'ID = "acme_widget"\n'
        + "class AcmeWidgetSimulator(TCPSimulator):\n"
        + '    SIMULATOR_INFO = {"driver_id": ID, "name": "w"}\n',
        encoding="utf-8",
    )
    assert simulator_claims(path) == [SimulatorClaim("AcmeWidgetSimulator", None)]


def test_a_conflict_is_one_sentence_naming_each_class_in_order():
    sentence = claim_conflicts([("ZetaSimulator", "acme_widget"), ("AlphaSimulator", "acme_widget")])
    assert sentence == {
        "acme_widget": (
            "AlphaSimulator and ZetaSimulator each name driver id 'acme_widget' in "
            "SIMULATOR_INFO, and a driver has one simulator, so none of them is used. "
            "Keep the id on one class; a class another builds on can leave driver_id out."
        )
    }


def test_the_contract_check_of_a_simulator_file_refuses_two_classes_on_one_id(tmp_path):
    path = tmp_path / "acme_widget_sim.py"
    path.write_text(_HEAD + CORPUS["two_classes_one_id"], encoding="utf-8")
    errors = check_simulator_file(path).errors
    assert any("AlphaSimulator and ZetaSimulator each name driver id 'acme_widget'" in e for e in errors)


def test_the_contract_check_passes_a_class_under_a_second_name(tmp_path):
    path = tmp_path / "acme_widget_sim.py"
    path.write_text(_HEAD + CORPUS["a_second_name"], encoding="utf-8")
    assert check_simulator_file(path).errors == []
