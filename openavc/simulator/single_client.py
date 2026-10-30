"""
Single client — a device that serves one control connection at a time.

Many displays and projectors answer one TCP control connection and no more.
What they do when a second connection arrives differs by device, and it
decides whether anything else that connects (a scan, a connection test, a
second controller) takes the device from its controller. Without this setting
a simulated device answers every connection, so that failure cannot happen in
simulation.

Three modes (:data:`MODES`):

- ``newest``: a new connection takes over. Every connection already open stops
  being answered and is not closed: the device reads what it sends and answers
  nothing, unsolicited messages included. Its controller hears nothing until
  it notices the silence.
- ``oldest``: the connection being served keeps the device. One that arrives
  while it is open is held open and never answered: no greeting, no sign-in
  prompt, no replies.
- ``reject``: one that arrives while another is open is closed at once, before
  anything is sent to it. The connect itself succeeds (the operating system
  completes it before the device sees it), so a client sees a connection that
  ends straight away, not a refusal.

A connection that has stopped being answered stays that way until it closes,
even after the connection that displaced it closes or the setting is switched
off. The next new connection is served.

A driver may declare its device's mode (``simulator.single_client`` in YAML,
``SIMULATOR_INFO["single_client"]`` in a Python simulator), and the simulated
device then behaves that way from the start. A device whose driver declares
nothing answers every connection. Either way it can be changed per device
(Simulator UI, or ``POST /api/devices/{id}/single-client``). A change judges
the connections already open at once (``newest`` keeps the most recent one,
the other modes the oldest) and applies to every connection after it.

TCP only: a UDP or OSC device has no connection, each HTTP request is its own
exchange, and a serial device has one cable.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: What a device that serves one connection at a time does with another.
MODES = ("newest", "oldest", "reject")


def single_client_problems(value: Any) -> list[str]:
    """Why ``value`` is not a single_client declaration, or [] when it is one.

    The same rule the driver contract states for ``simulator.single_client``;
    this copy checks a Python simulator's ``SIMULATOR_INFO["single_client"]``,
    which no schema reads, and a change sent to the simulator API.
    """
    if value not in MODES:
        return ["single_client must be newest, oldest or reject"]
    return []


class SingleClient:
    """One simulated device's setting: what is declared, what is in force,
    and how many connections it has closed for arriving while another was
    open (``reject``) since the setting last changed."""

    def __init__(
        self,
        device_id: str,
        declared: Any = None,
        notify: Callable[[], None] | None = None,
    ):
        self.device_id = device_id
        self._notify = notify
        self.declared: str | None = None
        #: The mode in force, or None while the device answers every connection.
        self.mode: str | None = None
        self.refused = 0
        if declared is not None:
            self.declare(declared)

    def declare(self, value: Any) -> None:
        """Take a driver's declaration, in force at once. A malformed one is
        logged and ignored: the validator reports it to the driver's author."""
        problems = single_client_problems(value)
        if problems:
            logger.warning(
                "%s: ignoring the declared single_client (%s)", self.device_id, "; ".join(problems)
            )
            return
        self.declared = value
        self.mode = value

    def set_mode(self, mode: str | None) -> None:
        """Change the mode (None: answer every connection); the caller checked it."""
        if mode != self.mode:
            self.mode = mode
            self.refused = 0
        self.changed()

    def note_refused(self) -> None:
        self.refused += 1
        self.changed()

    def changed(self) -> None:
        if self._notify is not None:
            self._notify()

    @property
    def source(self) -> str:
        if self.declared is not None and self.mode == self.declared:
            return "driver"
        if self.declared is None and self.mode is None:
            return "simulator"
        return "edited"
