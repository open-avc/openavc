"""THE reading of a YAML driver's ``auth:`` block: its defaults, when a sign-in
is skipped, and which transports carry one.

Three readers ask it: the driver, which signs in (``drivers/sign_in.py``); the
simulated device, which plays the other half (``simulator/telnet_sign_in.py``);
and the checker, through the driver model (``driver_model``'s problems) and
``sign_in_checks``. None of them restates a default or a skip rule.

``read_auth(block)`` reads the block into an ``AuthModel``, config-free, so one
reading serves every device on a driver. ``sign_in_plan(auth, transport,
config)`` says what one connection does about it: ``RUN`` the sign-in,
``BLANK`` (skipped because the credential typed first is blank, so on TCP only
listen for a prompt nobody can answer), or None (no sign-in applies).

The rules worth knowing before changing anything here:

* **It never raises.** A block that is not a mapping reads as None; a value
  that cannot be read takes its default.
* **The timeout.** A number of seconds above 0. Anything else (0 or less, no
  value, text that is not a number) is the 10-second default: a box emptied
  to 0 in the Driver Builder would otherwise abort the sign-in on the first
  look, and the device could never connect. The author's doors refuse a
  number of 0 or less (``auth_problems``); a door that loads the driver names
  it and reads it as the default. Text and other shapes are the shape walk's
  to name.
* **A prompt or pattern that is not text reads as none.** With either prompt
  missing nothing signs in; the checker refuses that block at every door.
* **The transport is the caller's.** The driver passes the transport the
  device connects over (``transport_choice``); a simulated device passes the
  one it serves, TCP for every stream it stands in for.

Pure: the standard library and ``spec``, with absolute ``openavc.`` imports
(``tests/test_avcdriver_semantic_purity.py``). Vendored with the driver
contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openavc.drivers.spec import AUTH_TRANSPORTS
from openavc.drivers.usable_number import number_value, usable_number

#: The sign-in type a block that names none runs.
TELNET_LOGIN = "telnet_login"

#: The two prompts a telnet_login waits for, and every pattern it matches.
PROMPT_KEYS: tuple[str, ...] = ("username_prompt", "password_prompt")
PATTERN_KEYS: tuple[str, ...] = (*PROMPT_KEYS, "success_pattern", "failure_pattern")

#: What a connection does about the block (``sign_in_plan``).
RUN = "run"
BLANK = "blank"

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_LINE_ENDING = "\r\n"
DEFAULT_USERNAME_FIELD = "username"
DEFAULT_PASSWORD_FIELD = "password"

#: The code of a timeout the author's doors refuse.
TIMEOUT_NOT_POSITIVE = "auth_timeout"


@dataclass(frozen=True)
class AuthModel:
    """One ``auth:`` block, read. ``type`` and the two field names are as
    written (a type but ``telnet_login`` signs in to nothing); the prompts are
    "" when missing."""

    type: Any
    username_prompt: str
    password_prompt: str
    success_pattern: str | None
    failure_pattern: str | None
    username_field: Any
    password_field: Any
    skip_if_empty: bool
    timeout: float
    line_ending: str

    @property
    def signs_in(self) -> bool:
        """A telnet_login with both prompts: the only block that signs in."""
        return self.type == TELNET_LOGIN and bool(self.username_prompt and self.password_prompt)

    def username(self, config: Mapping[str, Any] | None) -> str:
        """The username this device's settings hold, "" when blank."""
        return _setting(config, self.username_field)

    def password(self, config: Mapping[str, Any] | None) -> str:
        """The password this device's settings hold, "" when blank."""
        return _setting(config, self.password_field)


def read_auth(block: Any) -> AuthModel | None:
    """The ``auth:`` block as every reader reads it; None when it is not a
    mapping (the checker names that)."""
    if not isinstance(block, Mapping):
        return None
    return AuthModel(
        type=block.get("type", TELNET_LOGIN),
        username_prompt=_text(block.get("username_prompt")) or "",
        password_prompt=_text(block.get("password_prompt")) or "",
        success_pattern=_text(block.get("success_pattern")),
        failure_pattern=_text(block.get("failure_pattern")),
        username_field=block.get("username_field", DEFAULT_USERNAME_FIELD),
        password_field=block.get("password_field", DEFAULT_PASSWORD_FIELD),
        skip_if_empty=bool(block.get("skip_if_empty", True)),
        timeout=_timeout(block.get("timeout_seconds")),
        line_ending=_line_ending(block.get("line_ending", DEFAULT_LINE_ENDING)),
    )


def signs_in_over(transport: str) -> bool:
    """True when a sign-in can run over this transport: one that carries a
    byte stream (``spec.AUTH_TRANSPORTS``)."""
    return transport in AUTH_TRANSPORTS


def sign_in_plan(
    auth: AuthModel | None, transport: str, config: Mapping[str, Any] | None,
) -> str | None:
    """What one connection over ``transport`` does about the block, for a
    device with these settings: ``RUN``, ``BLANK`` or None (the module
    docstring)."""
    if auth is None or not auth.signs_in:
        return None
    if not signs_in_over(transport):
        return None
    if auth.skip_if_empty and not auth.username(config):
        # A serial line has no connect for a device to prompt on.
        return BLANK if transport == "tcp" else None
    return RUN


def auth_problems(block: Any) -> tuple[tuple[str, str], ...]:
    """What the author's doors refuse in the block that every door reads
    anyway, as (place, words): a timeout that is a number of 0 or less."""
    if not isinstance(block, Mapping):
        return ()
    seconds = number_value(block.get("timeout_seconds"))
    if seconds is not None and seconds <= 0:
        return (("auth.timeout_seconds", "must be a positive number"),)
    return ()


def _timeout(value: Any) -> float:
    """Seconds per stage: the value when it reads as a usable number above 0
    (``usable_number``), else the default."""
    seconds = usable_number(value)
    return DEFAULT_TIMEOUT_S if seconds is None or seconds <= 0 else seconds


def _line_ending(value: Any) -> str:
    """The ending as written ("" is no ending); the default when it is not
    text."""
    return value if isinstance(value, str) else DEFAULT_LINE_ENDING


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _setting(config: Mapping[str, Any] | None, field: Any) -> str:
    if not isinstance(config, Mapping):
        return ""
    return str(config.get(field, "") or "")
