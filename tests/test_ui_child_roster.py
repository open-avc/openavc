"""What the child-roster check will and will not say.

The check itself is one line of ``page_references``; everything interesting is
here, in when it declines to answer. It is advisory and it runs on every AI
write, so a false positive is worse than a miss: it fires on a page that is
correct, the author learns the warning is noise, and the real one goes past
unread six months later.

So these are mostly tests that it stays quiet.
"""

from __future__ import annotations

from openavc.cloud.tools.ui_tools import _declared_child_roster, _unknown_child_id


class _Driver:
    def __init__(self, child_types: dict, config: dict | None = None):
        self.DRIVER_INFO = {"child_entity_types": child_types}
        self.config = config or {}


class _Devices:
    def __init__(self, drivers: dict):
        self._drivers = drivers

    def get_driver(self, device_id):
        return self._drivers.get(device_id)


def _ask(child_types: dict, key: str, config: dict | None = None):
    devices = _Devices({"acme_switcher": _Driver(child_types, config)})
    return _unknown_child_id(devices, ["acme_switcher"], key)


FIXED_FOUR = {"output": {"instances": {"count": 4}}}


# --- The one case it speaks about -------------------------------------------


def test_an_id_past_a_fixed_count_is_named() -> None:
    roster = _ask(FIXED_FOUR, "device.acme_switcher.output.7.signal")
    assert roster == {"1", "2", "3", "4"}


def test_an_id_inside_a_fixed_count_is_silent() -> None:
    assert _ask(FIXED_FOUR, "device.acme_switcher.output.4.signal") is None


def test_a_literal_id_list_is_honored_exactly() -> None:
    """A sparse or string roster is not a range, and 2 is not on this one."""
    types = {"zone": {"instances": {"ids": ["st", "m", 4]}}}
    assert _ask(types, "device.acme_switcher.zone.st.level") is None
    assert _ask(types, "device.acme_switcher.zone.4.level") is None
    assert _ask(types, "device.acme_switcher.zone.2.level") == {"st", "m", "4"}


def test_a_count_from_a_filled_in_config_field_is_honored() -> None:
    """One driver covering several frame sizes still knows this frame's size."""
    types = {"output": {"instances": {"count_from": "outputs"}}}
    assert _ask(types, "device.acme_switcher.output.9.signal", {"outputs": 8}) \
        == {str(i) for i in range(1, 9)}
    assert _ask(types, "device.acme_switcher.output.8.signal", {"outputs": 8}) is None


def test_an_ids_from_config_field_is_split_on_commas() -> None:
    types = {"output": {"instances": {"ids_from": "installed"}}}
    config = {"installed": "1, 2, 4"}
    assert _ask(types, "device.acme_switcher.output.4.signal", config) is None
    assert _ask(types, "device.acme_switcher.output.3.signal", config) == {"1", "2", "4"}


# --- Everything it declines to answer ---------------------------------------


def test_a_device_reported_count_is_never_second_guessed() -> None:
    """``count_from_state`` means the hardware resizes the roster on connect.

    An ID past the declared count is then a prediction about a device that has
    not answered yet, not a mistake.
    """
    types = {"output": {"instances": {"count": 4, "count_from_state": "num_outputs"}}}
    assert _ask(types, "device.acme_switcher.output.9.signal") is None


def test_a_config_field_nobody_filled_in_says_nothing() -> None:
    types = {"output": {"instances": {"count_from": "outputs"}}}
    assert _ask(types, "device.acme_switcher.output.9.signal", {}) is None
    assert _ask(types, "device.acme_switcher.output.9.signal", {"outputs": ""}) is None
    assert _ask(types, "device.acme_switcher.output.9.signal", {"outputs": 0}) is None


def test_a_child_type_with_no_declared_roster_says_nothing() -> None:
    """Every Python driver that registers its children in code lands here."""
    assert _ask({"output": {"state_variables": {"signal": {}}}},
                "device.acme_switcher.output.7.signal") is None


def test_an_unknown_child_type_says_nothing() -> None:
    """It may be a plain property with dots in it, which is not ours to judge."""
    assert _ask(FIXED_FOUR, "device.acme_switcher.preset.7.name") is None


def test_a_plain_property_says_nothing() -> None:
    assert _ask(FIXED_FOUR, "device.acme_switcher.online") is None


def test_an_unknown_device_or_missing_driver_says_nothing() -> None:
    devices = _Devices({"acme_switcher": None})
    assert _unknown_child_id(devices, ["acme_switcher"],
                             "device.acme_switcher.output.7.signal") is None
    assert _unknown_child_id(None, ["acme_switcher"],
                             "device.acme_switcher.output.7.signal") is None
    assert _ask(FIXED_FOUR, "var.house_lights") is None


def test_a_true_count_is_not_a_roster_of_one() -> None:
    """``bool`` is an ``int`` in Python, and True would otherwise mean 1..1."""
    assert _declared_child_roster({"instances": {"count": True}}, {}) is None


def test_a_driver_that_raises_is_not_allowed_to_cost_a_write() -> None:
    """Advisory means advisory: nothing here may turn a save into an error."""
    class _Exploding:
        config: dict = {}

        @property
        def DRIVER_INFO(self):
            raise RuntimeError("driver is mid-reload")

    devices = _Devices({"acme_switcher": _Exploding()})
    assert _unknown_child_id(
        devices, ["acme_switcher"], "device.acme_switcher.output.7.signal",
    ) is None
