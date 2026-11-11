"""The device's half of a YAML driver's Telnet-style sign-in (``auth:``).

A driver that declares ``auth: {type: telnet_login, ...}`` expects the device to
ask for a username and then a password before it answers anything else. A
generated simulator plays that device, so the driver's sign-in can be tried
without hardware. The driver's half is ``openavc/drivers/sign_in.py``.

The rules worth knowing before changing anything here:

* **The device skips when the driver skips.** With ``skip_if_empty`` (on unless
  the block turns it off) and no username in the device's settings, the driver
  signs in to nothing. The device prompts anyway only while its
  ``sign_in_required`` error mode is on, which plays a unit with a password set.
  A TCP device whose driver can skip, and that has both prompts, offers that
  mode.
* **Credentials are not checked**, with one exception: ``"invalid"`` as the
  username or the password is the designated bad credential. The device
  answers it with the ``failure_pattern`` when the block declares one,
  otherwise with a fresh username prompt, which is what a real Telnet daemon
  does.
* **Prompts go out as text.** A prompt is a pattern the driver matches, so the
  device prints it with the common regex marks taken out (``_literal``).
* **The block is read when a connection starts**, so a value that cannot be
  read (a timeout that is not a number) fails that connection's sign-in and
  never the device's construction.
* The defaults restate the driver's own: type ``telnet_login``, the username in
  the ``username`` setting, ``skip_if_empty`` on, a CRLF line ending, a
  10-second timeout.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from openavc.transport.binary_helpers import encode_escape_sequences

# The sign-in logs as the generated simulator it belongs to.
logger = logging.getLogger("openavc.simulator.yaml_auto")

#: The error mode a device offers when its driver skips the sign-in for a
#: blank credential (``skip_if_empty``): switched on, the device prompts for a
#: sign-in whatever its settings say, the way a unit with a password set does,
#: so what the driver does with one it cannot answer can be tried.
SIGN_IN_REQUIRED_MODE = "sign_in_required"

#: What the device offers for that mode.
SIGN_IN_REQUIRED_DEFINITION = {
    "behavior": SIGN_IN_REQUIRED_MODE,
    "description": "Device asks for a sign-in (even with none set)",
    "builtin": True,
}

#: The username or password the device refuses.
BAD_CREDENTIAL = "invalid"


def telnet_login(driver_def: dict) -> dict | None:
    """The driver's ``auth:`` block when it signs in the Telnet way, else None."""
    auth_def = driver_def.get("auth")
    if not isinstance(auth_def, dict):
        return None
    if auth_def.get("type", "telnet_login") != "telnet_login":
        return None
    return auth_def


def skips_blank_credential(auth_def: dict) -> bool:
    """True when the driver skips its sign-in for a blank username."""
    return bool(auth_def.get("skip_if_empty", True))


def driver_skips(auth_def: dict, config: dict) -> bool:
    """True when the driver signs in to nothing: it skips for a blank
    username, and the device's settings have none."""
    username = str(config.get(auth_def.get("username_field", "username"), "") or "")
    return skips_blank_credential(auth_def) and not username


def offers_sign_in_required(driver_def: dict) -> bool:
    """True when the driver skips its sign-in for a blank credential on a TCP
    device that prompts for both, the case the ``sign_in_required`` mode plays
    out."""
    auth_def = telnet_login(driver_def)
    return (
        auth_def is not None
        and driver_def.get("transport", "tcp") == "tcp"
        and bool(auth_def.get("username_prompt"))
        and bool(auth_def.get("password_prompt"))
        and skips_blank_credential(auth_def)
    )


def encode_line_ending(value: str | bytes) -> bytes:
    """A line ending as the bytes the driver sends it as. A YAML
    double-quoted ``"\\r\\n"`` arrives as the real control characters and a
    single-quoted one as backslash text; the driver runs both through
    ``encode_escape_sequences``, and so does this."""
    if isinstance(value, bytes):
        return value
    return encode_escape_sequences(value)


def _literal(pattern: str | None) -> str:
    """A prompt pattern as the device prints it: the common regex marks taken
    out. The driver matches what comes back with the pattern, so readable
    text is enough; an elaborate regex still prints as something legible."""
    if pattern is None:
        return ""
    out = pattern
    out = re.sub(r"\\[brnt]", " ", out)
    out = re.sub(r"^\^", "", out)
    out = re.sub(r"\$$", "", out)
    out = out.replace("\\", "")
    return out


Emit = Callable[[bytes], Awaitable[None]]


class TelnetSignIn:
    """One connection's sign-in, read from the ``auth:`` block as it starts."""

    def __init__(self, auth_def: dict[str, Any]):
        username_prompt = auth_def.get("username_prompt", "")
        password_prompt = auth_def.get("password_prompt", "")
        success_pattern = auth_def.get("success_pattern")
        failure_pattern = auth_def.get("failure_pattern")
        line_ending = auth_def.get("line_ending", "\r\n")
        self.timeout = float(auth_def.get("timeout_seconds", 10))
        self.username_prompt = _literal(username_prompt)
        self.password_prompt = _literal(password_prompt)
        self.success_text = _literal(success_pattern) if success_pattern else None
        self.failure_text = _literal(failure_pattern) if failure_pattern else None
        self.ending = encode_line_ending(line_ending)

    async def run(
        self, reader: asyncio.StreamReader, emit: Emit, device: str, client_id: str,
    ) -> bool:
        """Prompt, read both credentials, and answer: True admits the client."""
        try:
            await emit(self.username_prompt.encode("utf-8"))
            username = await self._read_credential(reader)
            await emit(self.password_prompt.encode("utf-8"))
            password = await self._read_credential(reader)
            if username == BAD_CREDENTIAL or password == BAD_CREDENTIAL:
                await self._refuse(emit)
                logger.info(
                    "%s: client %s sent the designated bad credential — rejecting login",
                    device, client_id,
                )
                return False
            if self.success_text:
                await emit(self.success_text.encode("utf-8") + self.ending)
            return True
        except asyncio.TimeoutError:
            logger.warning("%s: client %s auth timed out", device, client_id)
            return False
        except (ConnectionError, OSError):
            return False

    async def _refuse(self, emit: Emit) -> None:
        """The failure banner, or with none declared a fresh username prompt,
        which the driver reads as a refused login too."""
        if self.failure_text:
            await emit(self.failure_text.encode("utf-8") + self.ending)
        else:
            await emit(self.ending + self.username_prompt.encode("utf-8"))

    async def _read_credential(self, reader: asyncio.StreamReader) -> str:
        """One credential line, up to the declared ending. ``readline()``
        returns only on a newline, so an ending like ``"\\r"`` is read up to
        itself instead of waiting out the timeout."""
        if self.ending.endswith(b"\n"):
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        else:
            try:
                raw = await asyncio.wait_for(
                    reader.readuntil(self.ending), timeout=self.timeout
                )
            except asyncio.IncompleteReadError as e:
                raw = e.partial
            except asyncio.LimitOverrunError:
                raw = b""
        return raw.decode("utf-8", errors="replace").strip("\r\n")
