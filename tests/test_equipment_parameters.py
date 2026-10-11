"""Equipment commands retain the canonical parameter types on the wire."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openavc.drivers.base import CommandParamError
from openavc.drivers.equipment import EquipmentDriver


def driver():
    instance = object.__new__(EquipmentDriver)
    instance.DRIVER_INFO = {
        "child_entity_types": {
            "output": {"id_format": {"type": "string"}},
            "input": {"id_format": {"type": "integer"}},
        },
        "commands": {"route": {"params": {
            "output": {"type": "child_id", "child_type": "output", "required": True},
            "input": {"type": "child_id", "child_type": "input", "required": True},
            "label": {"type": "string", "trim": False},
            "gain": {"type": "number", "default": -20},
            "mute": {"type": "boolean"},
            "mode": {"type": "enum", "values": [{"value": 3, "label": "USB Input"}, "analog"]},
        }}},
    }
    instance._sdk_command_lock = asyncio.Lock()
    instance._sdk_session = "session"
    instance._reserve_id = lambda: 1
    instance._request = AsyncMock(return_value={"outcome": "completed"})
    instance._refresh = AsyncMock()
    return instance


@pytest.mark.asyncio
async def test_child_ids_optional_values_and_verbatim_strings():
    equipment = driver()
    await equipment.send_command("route", {
        "output": " Main-L ", "input": "2", "label": "  Room A  ", "mute": "false", "gain": None,
        "mode": "USB Input",
    })
    assert equipment._request.call_args.args[1]["params"] == {
        "output": "Main-L", "input": 2, "label": "  Room A  ", "mute": False, "mode": 3,
    }


@pytest.mark.asyncio
async def test_required_is_not_satisfied_by_an_optional_default():
    equipment = driver()
    with pytest.raises(CommandParamError, match="'input' is required"):
        await equipment.send_command("route", {"output": "Main-L"})
    equipment._request.assert_not_called()
