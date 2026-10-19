"""Known command outcomes survive ordinary APIs; unexpected errors stay private."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from openavc.api.error_messages import friendly_error
from openavc.api.errors import StructuredApiError
from openavc.api.routes import devices
from openavc.drivers.command_outcome import CommandOutcomeError


@pytest.mark.parametrize("door", ["command", "action", "setting"])
@pytest.mark.parametrize(
    "outcome,status,phrase",
    [
        ("pending", 409, "check the product's status before sending it again"),
        ("uncertain", 409, "check the product's status before sending it again"),
        ("failed", 502, "Equipment state may have changed"),
        ("rejected", 409, "could not accept"),
    ],
)
async def test_known_outcome_survives(door, outcome, status, phrase, monkeypatch):
    failure = CommandOutcomeError(outcome)
    engine = SimpleNamespace(
        devices=SimpleNamespace(
            send_command=AsyncMock(side_effect=failure),
            set_device_setting=AsyncMock(side_effect=failure),
            get_driver=lambda _: SimpleNamespace(
                DRIVER_INFO={
                    "actions": [
                        {"id": "increment", "kind": "command", "command": "increment"}
                    ]
                }
            ),
        )
    )
    monkeypatch.setattr(devices, "_get_engine", lambda: engine)
    with pytest.raises(StructuredApiError) as caught:
        if door == "command":
            await devices.send_command(
                "local", devices.CommandRequest(command="increment", params={})
            )
        elif door == "action":
            await devices.invoke_device_action(
                "local", "increment", devices.ActionInvokeRequest(params={})
            )
        else:
            await devices.set_device_setting(
                "local", "gain", devices.DeviceSettingRequest(value=-20)
            )
    assert caught.value.status_code == status
    assert phrase in caught.value.detail
    assert caught.value.fields == {"code": "command_incomplete", "outcome": outcome}
    assert friendly_error(failure, device="Fixture equipment") == caught.value.detail


async def test_unexpected_exception_stays_private(monkeypatch):
    engine = SimpleNamespace(
        devices=SimpleNamespace(
            send_command=AsyncMock(
                side_effect=RuntimeError("private: /firmware/secrets/token")
            )
        )
    )
    monkeypatch.setattr(devices, "_get_engine", lambda: engine)
    with pytest.raises(HTTPException) as caught:
        await devices.send_command(
            "local", devices.CommandRequest(command="increment", params={})
        )
    assert caught.value.status_code == 500
    assert caught.value.detail == "Failed to send command 'increment' to device 'local'"


def test_unknown_outcome_cannot_be_used_as_display_text():
    with pytest.raises(ValueError, match="Unknown command outcome"):
        CommandOutcomeError("private: /firmware/secrets/token")
