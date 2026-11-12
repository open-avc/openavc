"""A stream device's side receiver: the datagram server a simulated TCP or
HTTP device runs beside its stream server when the driver has ``udp:``
commands.

A command that sends a datagram BESIDE the main transport (a ``udp:`` block:
a Wake-on-LAN magic packet, a message to a presentation's UDP receiver) is
received on the stream server's own port number, so the datagram shows in
the protocol log and its declared state effect applies. The rules worth
knowing before changing anything here:

* **One number for both servers.** ``start(0)`` promises a port number both
  servers answer on. Windows keeps whole blocks of numbers for itself on one
  protocol and not the other (WinError 10013), and it hands out free numbers
  in order, so when one server's pick sits in a block the other refuses, so
  do its next several picks; asking the same side again only walks the
  block. The side that refused picks the next number instead: a number a
  protocol hands out itself is never one it keeps. A number the caller named
  gets one try and is never traded.
* **The last refusal escapes as it came**, with the server that picked that
  number still running; the caller's stop() closes it.
* **A magic packet applies, a payload runs as a command.** A magic packet
  applies the ``sets`` of every magic-packet command, in command order, but a
  value that is one placeholder (a magic packet carries no params). Any other
  datagram runs through the command pipeline the way a line on the stream
  would; yaml_auto discards its reply, because the sender's socket closed
  with the send and a real device answers on its control link.

The servers themselves are the simulator's (the TCP, HTTP and datagram
servers its bases provide); this module says in which order they are asked
for a number and what a magic packet applies, and yaml_auto runs the rest
(which datagram is a side-send, the command pipeline, stopping both).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, NamedTuple

#: How many numbers start(0) tries before a refusal stands.
SIDE_PORT_ATTEMPTS = 5

#: What every Wake-on-LAN magic packet starts with: six 0xFF bytes, then the
#: MAC sixteen times.
MAGIC_PACKET_PREFIX = b"\xff" * 6
_MAGIC_PACKET_LENGTH = 102

#: A value that is nothing but one placeholder, which a magic packet cannot
#: fill.
_LONE_PLACEHOLDER = re.compile(r"\{[^{}]+\}")


def side_send_blocks(driver_def: dict) -> dict[str, dict]:
    """Each command's udp: block, by command name."""
    return {
        name: cmd["udp"]
        for name, cmd in (driver_def.get("commands") or {}).items()
        if isinstance(cmd, dict) and isinstance(cmd.get("udp"), dict)
    }


class Server(NamedTuple):
    """One of the two servers: how it starts on a number (0 = pick one) and
    how it stops."""

    start: Callable[[int], Awaitable[None]]
    stop: Callable[[], Awaitable[None]]


async def start_on_one_port(
    stream: Server, datagram: Server, port: int, bound_port: Callable[[], int],
) -> None:
    """Start both servers on one number: the stream server on ``port``, then
    the datagram server on the number it bound (``bound_port``, read after
    each start). On a refusal of that number the server that picked it is
    stopped and the side that refused picks next, up to SIDE_PORT_ATTEMPTS
    times for ``port`` 0 and once for a named number. The roles swap only
    when ``port`` is 0, so whichever side picks asks for ``port``; letting a
    named number retry would have the datagram side bind it first."""
    attempts = SIDE_PORT_ATTEMPTS if port == 0 else 1
    picks, joins = stream, datagram
    for attempt in range(attempts):
        await picks.start(port)
        try:
            await joins.start(bound_port())
        except OSError:
            if attempt == attempts - 1:
                raise
            await picks.stop()
            picks, joins = joins, picks
            continue
        return


def is_magic_packet(data: bytes) -> bool:
    """Whether a datagram is a Wake-on-LAN magic packet."""
    return data.startswith(MAGIC_PACKET_PREFIX) and len(data) >= _MAGIC_PACKET_LENGTH


def wake_sets(driver_def: dict, side_commands: dict[str, dict]) -> Iterator[tuple[str, Any]]:
    """What a magic packet applies, in order: the ``sets`` of each
    magic-packet command (``side_commands``, by name, each read from the
    definition as it is when its turn comes), but a value that is one
    placeholder. A generator, so each value is applied before the next
    command is read."""
    for cmd_name, udp_def in side_commands.items():
        if not udp_def.get("magic_packet"):
            continue
        cmd_def = (driver_def.get("commands") or {}).get(cmd_name) or {}
        sets = cmd_def.get("sets")
        if isinstance(sets, dict):
            yield from (
                (var_name, value) for var_name, value in sets.items()
                if not _is_lone_placeholder(value)
            )


def _is_lone_placeholder(value: Any) -> bool:
    return isinstance(value, str) and _LONE_PLACEHOLDER.fullmatch(value) is not None
