"""Numeric command-value formatting.

The value a slider/fader (or a macro, or the REST API) hands to a device must
reach the wire in the shape the protocol needs — an integer command sends
``26``, not ``26.0``. These pin the platform pieces that make that true: command
param type coercion, format-spec substitution, and UI value scaling. Per the
platform test policy they use an INVENTED device (``generic_acme`` / ``acme_*``)
and synthetic values; no real product, driver file, or captured fixture.
"""

from types import SimpleNamespace

from server.core.ui_events import UIEventRuntime
from server.drivers.configurable import (
    ConfigurableDriver,
    _normalize_and_validate_command_params,
)
from server.drivers.driver_loader import validate_driver_definition


# --- command param type coercion ----------------------------------------

def test_integer_param_coerces_float_to_int():
    # A slider that scaled to 26.0 must send "26" to an integer protocol.
    out = _normalize_and_validate_command_params(
        "set_volume", {"vol": {"type": "integer"}}, {"vol": 26.0}
    )
    assert out["vol"] == 26 and isinstance(out["vol"], int)
    assert ConfigurableDriver._safe_substitute("MVL{vol}", out) == "MVL26"


def test_integer_param_string_value_passes_through():
    # A value already shaped as text (a keypad's digit string, a hand-authored
    # macro value) is not reformatted — the long-standing string convention.
    # Only arithmetic-produced floats/ints get normalized. "26" already renders
    # as "26", so there is nothing to fix here.
    out = _normalize_and_validate_command_params(
        "set_volume", {"vol": {"type": "integer"}}, {"vol": "26"}
    )
    assert out["vol"] == "26"
    assert ConfigurableDriver._safe_substitute("MVL{vol}", out) == "MVL26"


def test_number_param_decimals_rounds():
    zero = _normalize_and_validate_command_params(
        "gain", {"g": {"type": "number", "decimals": 0}}, {"g": 26.4}
    )
    assert zero["g"] == 26 and isinstance(zero["g"], int)
    one = _normalize_and_validate_command_params(
        "gain", {"g": {"type": "number", "decimals": 1}}, {"g": 26.44}
    )
    assert one["g"] == 26.4


def test_number_param_without_decimals_is_untouched():
    # A `number` with no rounding rule keeps whatever was passed.
    out = _normalize_and_validate_command_params(
        "gain", {"g": {"type": "number"}}, {"g": 26.5}
    )
    assert out["g"] == 26.5


# --- format-spec substitution -------------------------------------------

def test_integer_spec_on_whole_float():
    # {v:d} used to throw on a float and leave the literal token.
    assert ConfigurableDriver._safe_substitute("MVL{v:d}", {"v": 26.0}) == "MVL26"


def test_zero_pad_and_hex_specs_on_float():
    assert ConfigurableDriver._safe_substitute("P{v:02d}", {"v": 5.0}) == "P05"
    assert ConfigurableDriver._safe_substitute("A{v:02X}", {"v": 26.0}) == "A1A"


def test_fixed_decimal_spec_cleans_fp_noise():
    assert ConfigurableDriver._safe_substitute("MVL{v:.0f}", {"v": 25.9999999}) == "MVL26"


def test_bad_integer_spec_on_fractional_float_left_verbatim():
    # A fractional value can't render as an integer; show the token rather than
    # silently truncating.
    assert ConfigurableDriver._safe_substitute("X{v:d}", {"v": 26.5}) == "X{v:d}"


# --- UI value scaling (UIEventRuntime.scale_value_forward) --------------

def _el(**kw):
    kw.setdefault("scale_to_full", True)
    return SimpleNamespace(**kw)


def test_identity_scale_returns_clean_int():
    # display 1..64 -> output 1..64, step 1: 26 must be int 26, not 25.9999996.
    el = _el(min=1, max=64, output_min=1, output_max=64, step=1)
    v = UIEventRuntime.scale_value_forward(el, 26)
    assert v == 26 and isinstance(v, int)


def test_fractional_output_keeps_float():
    el = _el(min=0, max=100, output_min=0, output_max=1, step=1)
    assert UIEventRuntime.scale_value_forward(el, 26) == 0.26


def test_no_output_range_passes_value_through():
    el = _el(min=0, max=100, output_min=None, output_max=None, step=1)
    v = UIEventRuntime.scale_value_forward(el, 26)
    assert v == 26 and isinstance(v, int)


def test_fractional_step_stays_float():
    el = _el(min=0, max=64, output_min=0, output_max=64, step=0.5)
    v = UIEventRuntime.scale_value_forward(el, 26)
    assert v == 26.0 and isinstance(v, float)


# --- loader validation of the decimals rule -----------------------------

def _def(params: dict) -> dict:
    return {
        "id": "generic_acme",
        "name": "Acme",
        "transport": "tcp",
        "commands": {"set_volume": {"send": "MVL{vol}", "params": params}},
        "responses": [],
        "state_variables": {},
    }


def test_bad_decimals_rejected_at_load():
    errs = validate_driver_definition(_def({"vol": {"type": "number", "decimals": -1}}))
    assert any("decimals" in e for e in errs), errs


def test_valid_decimals_accepted():
    errs = validate_driver_definition(_def({"vol": {"type": "number", "decimals": 2}}))
    assert errs == []
