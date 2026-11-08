"""A state value written against its declared type: what the contract observer
is told and what the debug log says.

Neither refuses the write; listeners get the value as written. The two
differ on purpose in one place, pinned here: Python's ``bool`` is an ``int``,
and the observer (a device audit, the Driver Builder's test) reports
``True`` written to an integer or number variable while the debug log
accepts it.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import TYPE_MISMATCH, BaseDriver

_LOGGER = "openavc.drivers.base"


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {"id": "acme_widget", "state_variables": {}}

    async def send_command(self, command: str, params: dict | None = None) -> None:
        return None


def _widget(var_def: dict[str, Any] | None, observed: list[tuple[str, dict]] | None) -> _AcmeWidget:
    cls = type("_Acme", (_AcmeWidget,), {
        "DRIVER_INFO": {
            "id": "acme_widget",
            "state_variables": {} if var_def is None else {"volume": var_def},
        },
    })
    drv = cls("acme1", {}, StateStore(), EventBus())
    if observed is not None:
        drv.contract_observer = lambda kind, detail: observed.append((kind, detail))
    return drv


def _debug_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.DEBUG and "State 'volume'" in r.getMessage()
    ]


_CASES = [
    ({"type": "integer"}, "loud", "declared integer, got str",
     "[acme1] State 'volume' declared as integer but got str: 'loud'"),
    ({"type": "integer"}, 2.5, "declared integer, got float",
     "[acme1] State 'volume' declared as integer but got float: 2.5"),
    ({"type": "integer"}, True, "declared integer, got bool", None),
    ({"type": "integer"}, 3, None, None),
    ({"type": "number"}, True, "declared number, got bool", None),
    ({"type": "number"}, 4, None, None),
    ({"type": "number"}, 4.5, None, None),
    ({"type": "number"}, "4", "declared number, got str",
     "[acme1] State 'volume' declared as number but got str: '4'"),
    ({"type": "float"}, "x", "declared float, got str",
     "[acme1] State 'volume' declared as float but got str: 'x'"),
    ({"type": "float"}, False, "declared float, got bool", None),
    ({"type": "boolean"}, 1, "declared boolean, got int",
     "[acme1] State 'volume' declared as boolean but got int: 1"),
    ({"type": "boolean"}, False, None, None),
    ({"type": "enum", "values": [1, "b"]}, "1", None, None),
    ({"type": "enum", "values": [1, "b"]}, "b", None, None),
    ({"type": "enum", "values": [1, "b"]}, "c", "not one of the declared values",
     "[acme1] State 'volume' value 'c' not in declared enum values [1, 'b']"),
    ({"type": "enum"}, "anything", None, None),
    ({"type": "string"}, 5, None, None),
    ({}, 5, None, None),
    ({"label": "Volume"}, 5, None, None),
    ({"type": "integer"}, None, None, None),
    ({"type": "acme_unknown"}, 5, None, None),
    # A declaration that is not a type name at all (the validator reports it)
    # checks nothing, and the write still lands.
    ({"type": ["integer", "null"]}, 1, None, None),
    ({"type": {"integer": True}}, "x", None, None),
]


@pytest.mark.parametrize(("var_def", "value", "problem", "line"), _CASES)
def test_what_a_mismatched_write_reports(
    caplog, var_def: dict[str, Any], value: Any, problem: str | None, line: str | None,
) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    observed: list[tuple[str, dict]] = []
    drv = _widget(var_def, observed)
    drv.set_state("volume", value)
    assert drv.get_state("volume") == value
    mismatches = [detail for kind, detail in observed if kind == TYPE_MISMATCH]
    if problem is None:
        assert mismatches == []
    else:
        assert mismatches == [{
            "state": "volume",
            "declared": var_def.get("type", "string"),
            "value": value,
            "problem": problem,
        }]
    assert _debug_lines(caplog) == ([] if line is None else [line])


@pytest.mark.parametrize(("var_def", "value", "problem", "line"), _CASES)
def test_without_an_observer_only_the_log_reports(
    caplog, var_def: dict[str, Any], value: Any, problem: str | None, line: str | None,
) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    drv = _widget(var_def, None)
    drv.set_state("volume", value)
    assert _debug_lines(caplog) == ([] if line is None else [line])


def test_a_value_that_is_not_flat_reaches_the_observer_as_its_repr() -> None:
    observed: list[tuple[str, dict]] = []
    drv = _widget({"type": "integer"}, observed)
    drv._warn_on_type_mismatch("volume", [1, 2], {"type": "integer"})
    assert observed == [(TYPE_MISMATCH, {
        "state": "volume", "declared": "integer", "value": "[1, 2]",
        "problem": "declared integer, got list",
    })]
