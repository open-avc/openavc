"""A driver's ``auth:`` block, checked when it loads: the sign-in the platform
types before any other traffic (``drivers/sign_in.py`` at runtime).

The runtime swaps to raw byte buffering and types the credentials before
anything else goes out, so a misdeclared block silently connects
unauthenticated or mangles the transport's data path instead of failing. Two
kinds of rule apply, in this order:

* what any sign-in is held to: a ``type`` the runtime implements
  (``spec.AUTH_TYPES``), on a transport that carries a byte stream
  (``auth_block.signs_in_over``, ``spec.AUTH_TRANSPORTS``); the messages name
  both lists from ``spec``;
* what ``telnet_login`` reads (``_telnet_login_errors``): both prompts, since
  without them the handshake silently does nothing, and every pattern it
  watches for, which run on raw device bytes and so get the ReDoS check the
  response patterns get.

A type the runtime does not know is still held to ``telnet_login``'s rules, as
it always has been. A second type is a function beside ``telnet_login``'s and a
name in ``spec``, and that change decides which rules an unknown type is held to.

What the block means (the default type, the prompts and patterns, the
transports) is ``auth_block``'s; a timeout of 0 or less is refused at the
author's doors through the driver model's problems, not here.

Purity contract, as for ``avcdriver_semantic``: standard library, the sibling
``spec`` table, ``auth_block`` and the stdlib-only regex-safety helper. The
module is vendored with the rest of the driver contract (openavc-drivers'
catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.auth_block import PATTERN_KEYS, PROMPT_KEYS, TELNET_LOGIN, signs_in_over
from openavc.drivers.spec import AUTH_TRANSPORTS, AUTH_TYPES
from openavc.utils.regex_safety import regex_safety_error


def auth_errors(driver_def: dict[str, Any]) -> list[str]:
    """The ``auth:`` block, in the order its messages have always come."""
    block = driver_def.get("auth")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["auth: must be a mapping"]
    return [
        *_type_errors(block),
        *_transport_errors(driver_def.get("transport", "")),
        *_telnet_login_errors(block),
    ]


def _type_errors(block: dict[str, Any]) -> list[str]:
    auth_type = block.get("type", TELNET_LOGIN)
    if auth_type in AUTH_TYPES:
        return []
    named = " or ".join(f"'{name}'" for name in AUTH_TYPES)
    return [f"auth: unsupported type '{auth_type}' (only {named})"]


def _transport_errors(transport: Any) -> list[str]:
    # On udp, http or osc the frame-parser swap and the raw buffering break the
    # normal data path.
    if not transport or signs_in_over(transport):
        return []
    return [
        f"auth: login handshake is only supported on {'/'.join(AUTH_TRANSPORTS)} "
        f"transports, not '{transport}'"
    ]


def _telnet_login_errors(block: dict[str, Any]) -> list[str]:
    errors = [
        f"auth: missing required '{key}'" for key in PROMPT_KEYS if not block.get(key)
    ]
    for key in PATTERN_KEYS:
        pattern = block.get(key)
        if pattern:
            error = regex_safety_error(f"auth.{key}", pattern)
            if error:
                errors.append(error)
    return errors
