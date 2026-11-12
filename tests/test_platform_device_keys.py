"""The device keys the platform writes for every device have one home
(core/device_info.py PLATFORM_DEVICE_KEYS), and both lists that skip them read
it: the AI's property check and the device audit."""

from __future__ import annotations

import inspect
import re

import pytest

from openavc.audit.observe import is_platform_key
from openavc.cloud.tools.ui_lookups import undeclared_state_property
from openavc.core.device_info import PLATFORM_DEVICE_KEYS
from openavc.core.device_manager import DeviceManager

# A state key the manager names: f"device.{device_id}.connected" and the like.
_KEY = re.compile(r'f"device\.\{\w+\}\.(\w+)"')


def _keys_the_manager_names() -> set[str]:
    """Every device.<id>.<key> literal in DeviceManager's module and the
    mixins it is built from."""
    modules = {inspect.getmodule(cls) for cls in DeviceManager.__mro__}
    sources = [inspect.getsource(m) for m in modules if m and m.__name__.startswith("openavc.")]
    return {key for source in sources for key in _KEY.findall(source)}


def test_the_list_is_every_key_the_device_manager_names():
    named = _keys_the_manager_names()
    assert named, "the scan found no device keys at all; the pattern no longer matches the code"
    assert sorted(named - PLATFORM_DEVICE_KEYS) == [], "written by the device manager, missing from the list"
    assert sorted(PLATFORM_DEVICE_KEYS - named) == [], "on the list, never written by the device manager"


@pytest.mark.parametrize("key", sorted(PLATFORM_DEVICE_KEYS))
def test_the_audit_never_counts_a_platform_key_as_a_change(key):
    assert is_platform_key(key)


class _Amp:
    DRIVER_INFO = {"id": "acme_widget", "state_variables": {"level": {"type": "float"}}}
    config = None


class _Devices:
    def get_driver(self, device_id):
        return _Amp() if device_id == "amp" else None


@pytest.mark.parametrize("key", sorted(PLATFORM_DEVICE_KEYS))
def test_the_ai_never_calls_a_platform_key_undeclared(key):
    assert undeclared_state_property(_Devices(), ["amp"], f"device.amp.{key}") is None


def test_the_ai_still_calls_a_name_the_driver_does_not_declare_undeclared():
    assert undeclared_state_property(_Devices(), ["amp"], "device.amp.levl") == {"level"}
