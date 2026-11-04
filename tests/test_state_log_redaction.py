"""A state write's debug line masks a value kept under a credential's name.

Every state write is logged at DEBUG, and the in-app log buffer keeps DEBUG.
A driver that writes a token it was handed into state (``session_token``)
without registering it would otherwise put it in the log in clear. The rule is
the one a problem report applies to state. Invented keys, synthetic values.
"""

from __future__ import annotations

import logging

from openavc.core.state_store import StateStore

TOKEN = "tok-Acme-55f1e2"


def _debug_lines(caplog) -> str:
    return "\n".join(
        r.getMessage() for r in caplog.records if r.name == "openavc.core.state_store"
    )


def test_a_credential_named_value_is_masked_and_others_are_not(caplog):
    store = StateStore()
    with caplog.at_level(logging.DEBUG, logger="openavc.core.state_store"):
        store.set("device.acme1.session_token", TOKEN)
        store.set("device.acme1.power", "on")
        store.set("device.acme1.username", "tech")
        store.set_batch({"device.acme1.auth_token": TOKEN + "-2"})
        store.delete("device.acme1.session_token")
    lines = _debug_lines(caplog)
    assert TOKEN not in lines
    assert "State: device.acme1.session_token = ***" in lines
    assert "State: device.acme1.auth_token = ***" in lines
    assert "State: device.acme1.session_token deleted (was ***" in lines
    assert "State: device.acme1.power = 'on'" in lines
    assert "State: device.acme1.username = 'tech'" in lines
