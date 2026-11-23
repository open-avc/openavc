"""A Python simulator says whether it pushes with ``SIMULATOR_INFO["push_state"]``.

The Simulator's device card reads "Pushes state changes" or "Poll-only (no
push)" from the info payload. A YAML simulator works it out from its
``simulator:`` section; a Python simulator pushes in its own code, so it
declares it, and one that declares nothing reads Poll-only.

Invented simulators (acme_widget) throughout.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.simulator.tcp_simulator import TCPSimulator


def _simulator(**info: Any) -> TCPSimulator:
    class AcmeWidgetSimulator(TCPSimulator):
        SIMULATOR_INFO = {
            "driver_id": "acme_widget",
            "name": "Acme Widget Simulator",
            "transport": "tcp",
            "initial_state": {"power": False},
            **info,
        }

        def handle_command(self, data: bytes) -> bytes | None:
            return None

    return AcmeWidgetSimulator("acme1")


def test_a_python_simulator_that_declares_it_pushes_says_so() -> None:
    assert _simulator(push_state=True).to_info_dict()["push_state"] is True


@pytest.mark.parametrize("info", [{}, {"push_state": False}, {"push_state": None}])
def test_one_that_declares_nothing_reads_poll_only(info: dict[str, Any]) -> None:
    assert _simulator(**info).to_info_dict()["push_state"] is False
