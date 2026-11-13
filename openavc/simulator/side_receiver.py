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
  protocol hands out itself is never one it keeps. When that pick is refused
  too, both protocols' next numbers sit in blocks the other keeps, so the
  rest are drawn at random from the dynamic range, which those blocks cover
  only a small part of. A number the caller named gets one try and is never
  traded.
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

import random
import re
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, NamedTuple

#: How many numbers start(0) tries before a refusal stands: one each server
#: picks, then the rest drawn (DRAWN_PORTS).
SIDE_PORT_ATTEMPTS = 20

#: Where a drawn number comes from: the dynamic range Windows and macOS hand
#: numbers out from.
DRAWN_PORTS = (49152, 65535)

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


class _Try(NamedTuple):
    """One try at a number: ``picks`` starts on ``number``, then ``joins`` on
    the number it bound."""

    picks: Server
    joins: Server
    number: int


async def start_on_one_port(
    stream: Server, datagram: Server, port: int, bound_port: Callable[[], int],
) -> None:
    """Start both servers on one number: the stream server on ``port``, then
    the datagram server on the number it bound (``bound_port``, read after
    each start). A named number gets that one try. For ``port`` 0, on a
    refusal of that number the server that picked it is stopped and the side
    that refused picks next; when that is refused too, each further number
    is drawn from DRAWN_PORTS and asked of the stream server, then the
    datagram server, up to SIDE_PORT_ATTEMPTS numbers in all."""
    if port != 0:
        await _one_try(_Try(stream, datagram, port), bound_port, last=True)
        return
    for attempt, one in enumerate(_tries(stream, datagram)):
        last = attempt == SIDE_PORT_ATTEMPTS - 1
        if await _one_try(one, bound_port, last=last):
            return


def _tries(stream: Server, datagram: Server) -> Iterator[_Try]:
    """Who starts first and on which number, try by try for ``port`` 0: the
    stream server picks, then the datagram server, then drawn numbers."""
    yield _Try(stream, datagram, 0)
    yield _Try(datagram, stream, 0)
    for _ in range(SIDE_PORT_ATTEMPTS - 2):
        yield _Try(stream, datagram, _draw_port())


def _draw_port() -> int:
    return random.randint(*DRAWN_PORTS)


async def _one_try(
    one: _Try, bound_port: Callable[[], int], *, last: bool,
) -> bool:
    """One try (_Try). False on a refusal, with ``picks`` stopped; the last
    try's refusal escapes as it came, and so does a refused pick of 0 (a
    server that may not pick any number)."""
    try:
        await one.picks.start(one.number)
    except OSError:
        if last or one.number == 0:
            raise
        return False
    try:
        await one.joins.start(bound_port())
    except OSError:
        if last:
            raise
        await one.picks.stop()
        return False
    return True


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
