"""What placeholder substitution does at the edges the driver suites never reach.

``safe_substitute`` fills a command's ``{name}`` and ``{name:spec}`` slots on the
send path (the runtime, the simulator and the validator all call it), and
``derive_config`` builds a driver's ``config_derived`` values with it. These pin
the order a spec that refuses a value is retried in, an empty spec, and when a
derived value is left blank. On invented values.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.compiled_protocol import derive_config, safe_substitute


@pytest.mark.parametrize(
    "template, params, expected",
    [
        # A string refused by the spec is tried as an int before a float.
        ("{v:+}", {"v": "5"}, "+5"),
        ("{v:02}", {"v": "5"}, "50"),
        ("{v:.1f}", {"v": "5"}, "5.0"),
        ("{v:02d}", {"v": "2.5"}, "{v:02d}"),
        ("{v:x}", {"v": "255"}, "ff"),
        ("{v:02d}", {"v": " 5"}, "05"),
        # A whole float is retried as an int; a bool is a number already.
        ("{v:03d}", {"v": 7.0}, "007"),
        ("{v:d}", {"v": True}, "1"),
        # An empty spec is no spec.
        ("{v:}", {"v": 5}, "5"),
        ("{v}", {"v": None}, "None"),
        # A value nothing makes fit stays as the placeholder.
        ("{v:02d}", {"v": [1]}, "{v:02d}"),
        ("{v:q}", {"v": 5}, "{v:q}"),
    ],
)
def test_safe_substitute(template: str, params: dict[str, Any], expected: str) -> None:
    assert safe_substitute(template, params) == expected


@pytest.mark.parametrize("value", [1114112.0, "2000000"])
def test_a_retry_that_raises_keeps_the_refusal_it_followed(value: Any) -> None:
    # A spec no value fits raises from the retry, chained to the first refusal.
    with pytest.raises(OverflowError) as raised:
        safe_substitute("{v:c}", {"v": value})
    assert isinstance(raised.value.__context__, ValueError)


class _NoWholeNumber(float):
    """A float that will not become an int."""

    def __int__(self) -> int:
        raise ValueError("no whole number")


def test_a_retry_that_cannot_convert_leaves_the_placeholder() -> None:
    assert safe_substitute("{v:02d}", {"v": _NoWholeNumber(5.0)}) == "{v:02d}"


@pytest.mark.parametrize(
    "config, derived, expected",
    [
        ({"x": "7"}, {"d": "P{x:02d}"}, "P07"),
        # A field named with a spec is still a field the value needs.
        ({"x": ""}, {"d": "P{x:02d}"}, ""),
        # Any one blank field blanks the whole value.
        ({"a": "1", "b": ""}, {"d": "{a}{b}"}, ""),
        ({"a": "1", "b": " "}, {"d": "{a}{b}"}, ""),
        ({"a": "1"}, {"d": "{a}{b}"}, ""),
        ({"a": "1", "b": "2"}, {"d": "{a}{b}"}, "12"),
        ({}, {"d": "fixed"}, "fixed"),
    ],
)
def test_derive_config(config: dict[str, Any], derived: dict[str, Any], expected: str) -> None:
    derive_config(config, derived)
    assert config["d"] == expected


def test_a_derived_value_reads_one_derived_before_it() -> None:
    config = {"host": "h"}
    derive_config(config, {"base": "/{host}", "url": "{base}/go", "skipped": 5})
    assert config == {"host": "h", "base": "/h", "url": "/h/go"}
