"""Which exits may carry a secret at all: one table, asked by every exit.

``utils/secret_audience.MAY_CARRY_SECRET`` has a row for each place a
device's secret (a login, a parameter or setting marked secret, the mesh key)
could leave by. Three may carry one: the device's own connection, the login
file and the backups the platform writes (owner-only on this system), and a
value a driver published into state as the panels and the Programmer on this
system read it (a camera's stream address with its login, which a panel
plays). Every other exit masks it, and an exit with no row is answered as one
that may not.

The WebSocket rows are the client types ``core/ws_hub.MESSAGE_AUDIENCE``
names, so who a push reaches comes from that table and whether it may carry
a secret from this one.

Two sweeps keep every exit on the table: no module reads the secret
registry's values except through ``SecretRegistry.masked`` and
``SecretRegistry.to_mask``, which name the exit, and none masks with one of
the redaction module's own steps (a URL's password, a credential's name)
instead of asking for what its exit may carry.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from openavc.core.state_store import StateStore
from openavc.core.ws_hub import CLIENT_TYPES, MESSAGE_AUDIENCE, PANEL, WSHub
from openavc.utils.log_redaction import REDACTED, get_secret_registry
from openavc.utils.secret_audience import MAY_CARRY_SECRET, Exit, may_carry_secret

PACKAGE = Path(__file__).resolve().parent.parent / "openavc"

PASSWORD = "Widget-Pass-9931"
USERNAME = "acme_operator"


@pytest.fixture
def registry():
    reg = get_secret_registry()
    reg.clear()
    reg.set_config_secrets("acme_widget", {PASSWORD}, {USERNAME})
    yield reg
    reg.clear()


# --- The table ------------------------------------------------------------------


def test_every_exit_has_a_row():
    assert set(MAY_CARRY_SECRET) == set(Exit)


def test_only_the_device_the_login_file_and_driver_state_may_carry_a_secret():
    assert {where for where in Exit if may_carry_secret(where)} == {
        Exit.DEVICE, Exit.LOGIN_FILE, Exit.DRIVER_STATE,
    }


@pytest.mark.parametrize("unknown", ["acme_exit", "", None, 7])
def test_an_exit_with_no_row_may_not_carry_one(unknown: Any):
    assert may_carry_secret(unknown) is False


def test_the_websocket_rows_are_the_client_types_a_push_reaches():
    assert {Exit.PANEL.value, Exit.PROGRAMMER.value} == CLIENT_TYPES
    for msg_type, reach in MESSAGE_AUDIENCE.items():
        for client_type in reach:
            assert Exit(client_type) in MAY_CARRY_SECRET, msg_type


# --- Asking it -------------------------------------------------------------------


def test_an_exit_that_may_not_carry_one_masks_every_device_secret(registry):
    line = f"login {USERNAME} {PASSWORD} sent"
    assert registry.masked(Exit.LOG, line) == f"login {USERNAME} {REDACTED} sent"
    assert registry.to_mask(Exit.CLOUD) == {PASSWORD}


def test_one_device_s_exit_masks_its_username_too(registry):
    line = f"login {USERNAME} {PASSWORD} sent"
    assert registry.masked(Exit.SENTENCE, line, "acme_widget") == (
        f"login {REDACTED} {REDACTED} sent"
    )
    assert registry.to_mask(Exit.REPORT, "acme_widget") == {PASSWORD, USERNAME}


def test_an_exit_that_may_carry_one_is_given_it_as_it_is(registry):
    line = f"login {USERNAME} {PASSWORD} sent"
    assert registry.masked(Exit.DEVICE, line, "acme_widget") == line
    assert registry.to_mask(Exit.DEVICE, "acme_widget") == set()
    assert registry.to_mask(Exit.LOGIN_FILE) == set()


def test_an_exit_with_no_row_is_masked(registry):
    assert registry.masked("acme_exit", PASSWORD) == REDACTED
    assert registry.to_mask("acme_exit") == {PASSWORD}


# --- Driver state on this system --------------------------------------------------


class _Socket:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.received.append(json.loads(text))


@pytest.mark.asyncio
async def test_a_driver_s_stream_address_reaches_a_panel_with_its_login(registry):
    # A camera driver publishes its stream address with the login in it so a
    # panel on this system can play the stream. The login is a registered
    # secret, and the panel still gets the address whole.
    address = f"rtsp://{USERNAME}:{PASSWORD}@10.0.0.5/stream"
    store = StateStore()
    hub = WSHub(store)
    panel = _Socket()
    hub.add_client(panel, client_type=PANEL)
    store.set("device.acme_widget.stream_url", address, source="device.acme_widget")
    hub.on_state_change("device.acme_widget.stream_url", None, address, "device.acme_widget")
    await hub.flush_state_batch()
    await hub.flush_sends()
    hub.remove_client(panel)
    assert panel.received == [
        {"type": "state.update", "changes": {"device.acme_widget.stream_url": address}},
    ]


# --- The sweep -------------------------------------------------------------------

# The registry's own reads: what each exit is masked with. Only the registry
# and the exit-named doors in front of it call them.
_READS = {"redact_for", "redact_any", "secrets_for", "all_secrets"}
_REGISTRY_MODULE = PACKAGE / "utils" / "log_redaction.py"
# The rules built on the registry for each shape a value leaves in.
_EXIT_RULES_MODULE = PACKAGE / "utils" / "secret_exits.py"


def _registry_reads(source: str) -> list[str]:
    """Each call in ``source`` to one of the registry's reads, as ``line name``."""
    return [
        f"{node.lineno} {node.func.attr}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _READS
    ]


def test_no_exit_reads_the_registry_without_naming_itself():
    found = [
        f"{path.relative_to(PACKAGE.parent)}:{read}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if path != _REGISTRY_MODULE
        for read in _registry_reads(path.read_text(encoding="utf-8"))
    ]
    assert not found, (
        "these read the secret registry directly; ask SecretRegistry.masked or "
        f"SecretRegistry.to_mask with the exit named instead: {found}"
    )


def test_the_sweep_sees_a_direct_read():
    stray = (
        "from openavc.utils.log_redaction import get_secret_registry\n"
        "def leak(text):\n"
        "    secrets = get_secret_registry()\n"
        "    return secrets.redact_any(text)\n"
    )
    assert _registry_reads(stray) == ["4 redact_any"]


# --- No exit masks by a rule of its own ------------------------------------------

# The masking helpers below are the redaction module's own steps: an exit asks
# for what it may carry (the registry's masked / to_mask, or secret_exits'
# state_value_as, text_as and config_as, which ask the table), never one step
# alone, so a second rule cannot grow beside the table.
_STEPS = {"mask_url_password", "redact_text", "mask_secrets_by_name", "redact_any", "redact_for"}


def _named(node: ast.AST) -> str | None:
    """The name a reference, an attribute or an import names, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.name if isinstance(node, ast.alias) else None


def _steps_taken(source: str) -> list[str]:
    """Each place in ``source`` naming one of the masking steps, as ``line name``."""
    return [
        f"{node.lineno} {_named(node)}"
        for node in ast.walk(ast.parse(source))
        if _named(node) in _STEPS
    ]


def test_no_exit_masks_by_a_rule_of_its_own():
    found = [
        f"{path.relative_to(PACKAGE.parent)}:{step}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if path not in (_REGISTRY_MODULE, _EXIT_RULES_MODULE)
        for step in _steps_taken(path.read_text(encoding="utf-8"))
    ]
    assert not found, (
        "these mask with one of the redaction module's steps; ask for what the "
        f"exit may carry instead (state_value_as, text_as, config_as, masked): {found}"
    )


def test_the_own_rule_sweep_sees_a_private_rule():
    stray = (
        "from openavc.utils.log_redaction import mask_url_password\n"
        "def shown(value):\n"
        "    return mask_url_password(value)\n"
    )
    assert _steps_taken(stray) == ["1 mask_url_password", "3 mask_url_password"]
