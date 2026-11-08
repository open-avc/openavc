"""A YAML driver's ``liveness:`` block: "send X every N seconds, wait T for a
reply, drop the connection after K misses", the platform's watchdog
(``drivers/liveness.py``) driven by a declaration. It is for links that
cannot see a dead peer for themselves: UDP (a poll sent and forgotten never
notices silence), OSC, and a TCP device that mostly pushes (no FIN when it
vanishes).

The rules worth knowing before changing anything here:

* **On only with something to send.** A block switches the watchdog on when
  its ``send`` is text that is not empty; any other block leaves it off and
  says nothing. ``interval``, ``timeout`` and ``max_failures`` become this
  driver's ``HEALTH_INTERVAL_S`` / ``HEALTH_TIMEOUT_S`` /
  ``HEALTH_MAX_FAILURES`` (30, 5 and 2 when absent), set on the driver, never
  its class.
* **A bad block is off, with one warning.** An ``expect`` that is not a regex,
  or a number that is not one, turns the watchdog off and keeps whichever
  numbers were read before the bad one. The loader validates the block; this
  stays defensive so a hand-installed file cannot crash the driver.
* **The probe** follows ``polling.queries``' conventions: on OSC an address,
  ``{config}`` filled, with its ``args``; on any other link a raw line,
  ``{config}`` filled, escape-decoded and inside the ``send_frame`` header,
  without ``command_prefix`` / ``command_suffix`` (``send_framing``).
* **Any reply answers it**, a poll reply or a push as well as a direct
  answer, unless ``expect`` narrows it to frames that match (searched in a
  permissive text decode, so an OSC packet's leading address matches too). An
  error reply that matches is still an answer. The reply deadline is the
  platform loop's, which cancels the wait; a probe clears its waiter when it
  ends unless a newer probe has replaced it.

``LivenessBlock`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object; it answers ``BaseDriver``'s ``_health_enabled``
and ``_liveness_probe``. The Driver Builder's dry run and the device audit's
connection preview send the probe through ``_send_liveness_probe``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")


def _declares_probe(block: Any) -> bool:
    """A block with a probe to send: text that is not empty."""
    return isinstance(block, dict) and isinstance(block.get("send"), str) and bool(block["send"])


class LivenessBlock:
    """The ``liveness:`` block, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver / the other mixins; declared
    # here for the methods.
    device_id: str
    config: dict[str, Any]
    transport: Any
    _definition: dict[str, Any]
    HEALTH_INTERVAL_S: float
    HEALTH_TIMEOUT_S: float
    HEALTH_MAX_FAILURES: int
    _safe_substitute: Callable[[str, dict[str, Any]], str]
    _osc_address: Callable[[str], str]
    _build_osc_args: Callable[[list[Any], dict[str, Any]], list[Any]]
    _line_bytes: Callable[[str], bytes]

    def _init_liveness_block(self) -> None:
        self._liveness_def: dict[str, Any] | None = None
        self._liveness_expect: re.Pattern[str] | None = None
        self._liveness_waiter: asyncio.Future[None] | None = None
        block = self._definition.get("liveness")
        if _declares_probe(block):
            self._read_liveness_block(block)

    def _read_liveness_block(self, block: dict[str, Any]) -> None:
        """The block's ``expect`` and numbers, in that order; the watchdog on
        only once all of them read."""
        try:
            if block.get("expect"):
                self._liveness_expect = re.compile(str(block["expect"]))
            self.HEALTH_INTERVAL_S = float(block.get("interval", 30.0))
            self.HEALTH_TIMEOUT_S = float(block.get("timeout", 5.0))
            self.HEALTH_MAX_FAILURES = int(block.get("max_failures", 2))
            self._liveness_def = block
        except (re.error, TypeError, ValueError) as e:
            log.warning(
                f"[{self.device_id}] Invalid liveness block — watchdog "
                f"disabled: {e}"
            )
            self._liveness_expect = None

    def _health_enabled(self) -> bool:
        """The watchdog runs only when the definition declares a probe (this
        class always overrides the probe, so the platform's own "is it
        overridden" check would switch it on for every YAML driver)."""
        return self._liveness_def is not None

    async def _liveness_probe(self) -> None:
        """Send the declared probe and wait for a reply that answers it."""
        block = self._liveness_def
        if block is None or self.transport is None:
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._liveness_waiter = waiter
        try:
            await self._send_liveness_probe(block)
            await waiter
        finally:
            if self._liveness_waiter is waiter:
                self._liveness_waiter = None

    async def _send_liveness_probe(self, block: dict[str, Any]) -> None:
        """Send the probe in the form this driver's transport takes."""
        if self._definition.get("transport") == "osc":
            await self.transport.send(self._osc_probe(block))
        else:
            await self.transport.send(self._line_probe(block))

    def _osc_probe(self, block: dict[str, Any]) -> bytes:
        from openavc.transport.osc_codec import osc_encode_message

        address = self._osc_address(block["send"])
        args = self._build_osc_args(block.get("args", []), self.config)
        return osc_encode_message(address, args)

    def _line_probe(self, block: dict[str, Any]) -> bytes:
        line = block["send"]
        if "{" in line:
            line = self._safe_substitute(line, self.config)
        return self._line_bytes(line)

    def _liveness_note_data(self, data: bytes) -> None:
        """Answer a waiting probe when a frame that qualifies arrives. Called
        before the frame goes to the reply rules; never consumes it."""
        waiter = self._liveness_waiter
        if waiter is None or waiter.done():
            return
        if self._liveness_expect is not None:
            text = data.decode("utf-8", errors="replace")
            if not self._liveness_expect.search(text):
                return
        waiter.set_result(None)
