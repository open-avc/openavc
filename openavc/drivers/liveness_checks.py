"""A driver's ``liveness:`` block, checked when it loads: the watchdog's probe
("send X every N seconds, wait T for a reply, drop the connection after K
misses"), run at runtime by ``drivers/liveness_block.py``.

A misdeclared block would silently never arm (no watchdog: the never-goes-
offline failure it exists to fix) or tear healthy devices down, so it is
refused here. The runtime reads the block defensively as well, for a file that
reaches it unchecked.

Each part of the block is one function, in the order its messages have always
come: the transport (only ``spec.LIVENESS_TRANSPORTS``, the socket transports
that can die silently: HTTP polling already awaits every reply, and a bridge
device owns no transport), the probe to ``send``, ``expect``, ``interval`` and
``timeout``, ``max_failures``, and ``args`` (OSC only). A number is compared as
written, so NaN and infinity pass, as they always have.

Purity contract, as for ``avcdriver_semantic``: standard library, the sibling
``spec`` table and the stdlib-only regex-safety helper. The module is vendored
with the rest of the driver contract (openavc-drivers' catalog CI,
openavc-sdk).
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.spec import LIVENESS_TRANSPORTS
from openavc.utils.regex_safety import regex_safety_error

# The fewest seconds the watchdog takes between probes, and for a reply.
_SECONDS_FLOORS = (("interval", 1.0), ("timeout", 0.1))


def liveness_errors(driver_def: dict[str, Any]) -> list[str]:
    """The ``liveness:`` block, in the order its messages have always come."""
    block = driver_def.get("liveness")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["liveness: must be a mapping"]
    transport = driver_def.get("transport", "")
    return [
        *_transport_errors(transport),
        *_send_errors(block),
        *_expect_errors(block),
        *_seconds_errors(block),
        *_max_failures_errors(block),
        *_args_errors(block, transport),
    ]


def _transport_errors(transport: Any) -> list[str]:
    if not transport or transport in LIVENESS_TRANSPORTS:
        return []
    return [
        f"liveness: not supported on transport '{transport}' "
        f"(only {'/'.join(LIVENESS_TRANSPORTS)})"
    ]


def _send_errors(block: dict[str, Any]) -> list[str]:
    send = block.get("send")
    if isinstance(send, str) and send:
        return []
    return [
        "liveness: missing required 'send' (the probe payload: "
        "a raw protocol string, or an OSC address on osc)"
    ]


def _expect_errors(block: dict[str, Any]) -> list[str]:
    expect = block.get("expect")
    if expect is None:
        return []
    if not isinstance(expect, str) or not expect:
        return ["liveness: 'expect' must be a regex string"]
    error = regex_safety_error("liveness.expect", expect)
    return [error] if error else []


def _below(value: Any, floor: float) -> bool:
    return not isinstance(value, (int, float)) or isinstance(value, bool) or value < floor


def _seconds_errors(block: dict[str, Any]) -> list[str]:
    errors = []
    for key, floor in _SECONDS_FLOORS:
        value = block.get(key)
        if value is not None and _below(value, floor):
            errors.append(f"liveness: '{key}' must be a number >= {floor}")
    return errors


def _not_a_count_from_one(value: Any) -> bool:
    return not isinstance(value, int) or isinstance(value, bool) or value < 1


def _max_failures_errors(block: dict[str, Any]) -> list[str]:
    max_failures = block.get("max_failures")
    if max_failures is None or not _not_a_count_from_one(max_failures):
        return []
    return ["liveness: 'max_failures' must be an integer >= 1"]


def _args_errors(block: dict[str, Any], transport: Any) -> list[str]:
    args = block.get("args")
    if args is None:
        return []
    if transport != "osc":
        return ["liveness: 'args' is only valid on osc"]
    if not isinstance(args, list):
        return ["liveness: 'args' must be a list"]
    return []
