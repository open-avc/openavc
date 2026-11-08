"""One UDP datagram beside a driver's main transport, and Wake-on-LAN on it.

``send_udp`` opens a socket, sends one datagram and closes it: nothing is
held open and no connection is needed, so it works while the device is
offline. It is the primitive under a YAML command's ``udp:`` block and under
``wake_on_lan``; a Python driver that needs a datagram beside its transport
calls it rather than opening a socket of its own.

The rules worth knowing before changing anything here:

* A datagram to 255.255.255.255 (``broadcast=True``, or that address given
  as the host) asks Reach devices through which adapter it goes from. With
  one chosen it goes from that adapter alone, and is refused
  (``reach_devices.AdapterUnavailable``) when the adapter has nothing to send
  from, never sent from another. With none chosen it goes once from every
  connected adapter's address, skipping one that cannot send, and once
  unbound when none can.
* While the device is simulated every datagram goes to the simulator
  (``udp_redirect``, set and cleared by core/simulation.py), so a simulated
  display's wake lands in the Simulator UI's protocol log instead of waking
  whatever real display owns that MAC.
* ``_open_udp`` is the one place a socket is made: the Driver Builder's dry
  run replaces it to record the datagram instead of sending it
  (drivers/dry_run.py).

``UdpSideSend`` is a mixin that ``BaseDriver`` inherits, so every name stays
on the driver object; the steps of a send are module functions, so they put
no new names on the driver for a driver's own method to shadow. From its
host this needs ``device_id``, ``config`` and ``local_address()``
(drivers/transport_ladder.py). ``normalize_mac``, ``magic_packet`` and
``WakeAddressError`` stay importable from ``openavc.drivers.base``, the path
drivers use.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from openavc.transport.udp import UDPTransport
from openavc.utils.logger import get_logger

# These lines have always logged under the driver runtime's name, which the
# IDE's log view shows as their source.
log = get_logger("openavc.drivers.base")

#: The port a Wake-on-LAN magic packet is sent to unless a driver says otherwise
#: (the "discard" port; 7 is the other convention, and a listener honours both).
WAKE_ON_LAN_PORT = 9

#: Where ``send_udp(broadcast=True)`` sends: every host on the sending
#: adapter's own network, which no router passes on.
LIMITED_BROADCAST = "255.255.255.255"


class WakeAddressError(ValueError):
    """A Wake-on-LAN wake refused: the MAC address it needs is empty, or is
    not a MAC address.

    Typed so a door can tell it from a driver's other faults without reading
    its text. Somebody at a panel can open no setting, so they are told the
    device's setup is not finished; whoever can open Edit Device is told which
    setting to fill in (``api/error_messages.friendly_error`` does both).

    A driver with its own sentence passes it as the message. Without one, the
    sentence is built from what the refusal knows, so that a door can put the
    device's name in it (:meth:`sentence`): ``field_label`` is the setting the
    MAC goes in, as Edit Device shows it; ``value`` is what that setting holds
    when it is not a MAC ("" when it is empty); ``reported`` says the driver
    reads the MAC from the device when it connects, so the setting fills
    itself in.

    Subclasses ValueError so existing ``except ValueError`` handlers still
    catch it.
    """

    def __init__(
        self,
        message: str = "",
        *,
        field_label: str = "",
        value: str = "",
        reported: bool = False,
    ) -> None:
        self.field_label = field_label or "MAC Address"
        self.value = str(value or "").strip()
        self.reported = reported
        self._message = message
        super().__init__(message or self.sentence())

    def sentence(self, device: str = "") -> str:
        """The refusal for somebody who can open Edit Device, naming the
        device when its name is given."""
        if self._message:
            return self._message
        subject = device or "The device"
        if self.value:
            return (
                f"{subject} can't be woken: {self.field_label} is "
                f"'{self.value}', which is not a MAC address (six pairs of hex "
                f"digits, like aa:bb:cc:dd:ee:ff). Correct it under Edit Device."
            )
        if self.reported:
            return (
                f"{subject} can't be woken yet: {self.field_label} is empty. It "
                f"is filled in when {device or 'the device'} connects; until "
                f"then, enter it under Edit Device."
            )
        return (
            f"{subject} can't be woken: {self.field_label} is empty. Enter it "
            f"under Edit Device."
        )


def normalize_mac(value: Any) -> str | None:
    """``aa:bb:cc:dd:ee:ff`` for any usual spelling of a MAC, else None.

    Accepts colons, hyphens, dots or nothing between the bytes and any case.
    Twelve hex digits are the whole test; the all-zero address a device
    reports before it knows its own is not a MAC anyone can wake.
    """
    digits = re.sub(r"[^0-9A-Fa-f]", "", str(value or "")).lower()
    if len(digits) != 12 or digits == "000000000000":
        return None
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))


def magic_packet(mac: str) -> bytes:
    """The Wake-on-LAN magic packet for ``mac``: six 0xFF, then the MAC sixteen times.

    Raises :class:`WakeAddressError` when ``mac`` is not a MAC address, with
    the message a person fixing the config field needs to read.
    """
    normalized = normalize_mac(mac)
    if normalized is None:
        raise WakeAddressError(
            f"'{mac}' is not a MAC address (expected six hex pairs, "
            f"like aa:bb:cc:dd:ee:ff)",
            value=str(mac),
        )
    return b"\xff" * 6 + bytes.fromhex(normalized.replace(":", "")) * 16


def _udp_target(
    driver: Any, host: str | None, port: int | None, broadcast: bool,
) -> tuple[str, int]:
    """Where a side-send goes: the simulator while the device is simulated,
    else ``host:port``. Refused (ValueError) with no port, then with no host
    to send to, whether or not the device is simulated."""
    port = _udp_port(driver, port)
    host = _udp_host(driver, host, broadcast)
    if driver.udp_redirect is not None:
        return driver.udp_redirect
    return (host, port)


def _udp_port(driver: Any, port: int | None) -> int:
    """The port a side-send goes to; a missing one, or 0 or below, is refused."""
    if port is None or int(port) <= 0:
        raise ValueError(
            f"[{driver.device_id}] send_udp needs a port to send to"
        )
    return int(port)


def _udp_host(driver: Any, host: str | None, broadcast: bool) -> str:
    """The host a side-send goes to: 255.255.255.255 for a broadcast, else
    the one given or the device's own, trimmed; none at all is refused."""
    if broadcast:
        return LIMITED_BROADCAST
    host = str(host or driver.config.get("host") or "").strip()
    if not host:
        raise ValueError(
            f"[{driver.device_id}] send_udp needs a host: the device has "
            f"no host configured and none was given"
        )
    return host


async def _send_broadcast(
    driver: Any, data: bytes, target: tuple[str, int],
) -> list[str]:
    """Send a datagram to 255.255.255.255 from where Reach devices through
    says. Returns the addresses it went out from when it went once from
    every connected adapter, else an empty list (it went once, from one
    address or unbound)."""
    from openavc import reach_devices

    chosen = await asyncio.to_thread(reach_devices.pinned)
    if chosen is not None:
        # Asked before local_address(), whose refusal is a connection's:
        # a broadcast with nothing to send from says the adapter isn't
        # connected.
        bound = chosen.broadcast_source or None
        if bound is None:
            raise reach_devices.AdapterUnavailable(chosen.adapter_name)
        await driver._send_udp_from(bound, data, target)
        return []
    bound = driver.local_address(target[0])
    if bound is not None:
        await driver._send_udp_from(bound, data, target)
        return []
    sources = await _send_from_every_adapter(driver, data, target)
    if not sources:
        await driver._send_udp_from(None, data, target)
    return sources


async def _send_from_every_adapter(
    driver: Any, data: bytes, target: tuple[str, int],
) -> list[str]:
    """One datagram from each connected adapter's address; one that cannot
    send is skipped. Returns the addresses it went out from."""
    from openavc import reach_devices

    sources: list[str] = []
    for source in await asyncio.to_thread(reach_devices.broadcast_sources):
        try:
            await driver._send_udp_from(source, data, target)
            sources.append(source)
        except OSError as exc:
            log.debug(
                f"[{driver.device_id}] UDP broadcast from {source} failed: {exc}"
            )
    return sources


class UdpSideSend:
    """A datagram beside the transport (``send_udp``) and Wake-on-LAN, for
    ``BaseDriver``."""

    #: Where a datagram this driver sends beside its transport actually goes
    #: while the device is simulated: the simulator's own UDP receiver, set
    #: by core/simulation.py together with the connection redirect and cleared
    #: with it. None means the datagram goes where the driver aimed it. A
    #: simulated display's wake therefore lands in the Simulator UI's protocol
    #: log instead of waking whatever real display owns that MAC.
    udp_redirect: tuple[str, int] | None = None

    # Supplied by BaseDriver; declared here for the methods and the steps.
    device_id: str
    config: dict[str, Any]
    local_address: Callable[[str], str | None]

    async def _open_udp(self, local_addr: str | None = None) -> Any:
        """An opened ad-hoc UDP socket for one side-send, bound to
        ``local_addr`` when Reach devices through chose an adapter.

        A method so the Driver Builder's dry run can hand back a transport
        that records instead of transmitting; nothing else overrides it.
        """
        udp = UDPTransport(name=self.device_id)
        await udp.open(allow_broadcast=True, local_addr=local_addr)
        return udp

    async def send_udp(
        self,
        payload: bytes,
        host: str | None = None,
        port: int | None = None,
        broadcast: bool = False,
    ) -> list[tuple[str, int]]:
        """Send one UDP datagram beside the driver's main transport.

        Opens a socket, sends, closes: the side channel holds nothing open and
        needs no connection, so it works while the device is offline (pair the
        command with ``available_offline``). ``host`` defaults to the device's
        own host; ``broadcast`` sends to 255.255.255.255 instead. The bytes are
        in the device log as the transport's TX line. Returns the addresses the
        datagram went to.

        A datagram to 255.255.255.255 with no adapter chosen in Reach devices
        through goes out once through every connected adapter, from its
        address (``reach_devices.broadcast_sources``): sent unbound, it would
        leave through the one adapter the OS picks. An adapter that cannot
        send is skipped; with none to send from, it goes once, unbound. With
        an adapter chosen it goes from that adapter alone (its 169.254 address
        when it holds no other), and when it has nothing to send from it is
        not sent: ``reach_devices.AdapterUnavailable`` (a ConnectionError),
        never another adapter (``reach_devices.pinned``).

        This is the primitive under a YAML command's ``udp:`` block and under
        :meth:`wake_on_lan`; a Python driver that needs a datagram beside its
        transport (a message to a signage presentation's UDP receiver, say)
        calls it rather than opening its own socket.
        """
        target = _udp_target(self, host, port, broadcast)
        data = bytes(payload)
        sources: list[str] = []
        if target[0] == LIMITED_BROADCAST:
            sources = await _send_broadcast(self, data, target)
        else:
            # Bound for where it really goes: the simulator's redirect is local.
            await self._send_udp_from(self.local_address(target[0]), data, target)
        log.info(
            f"[{self.device_id}] Sent {len(payload)} bytes over UDP to "
            f"{target[0]}:{target[1]}"
            + (f" from {', '.join(sources)}" if sources else "")
        )
        return [target]

    async def _send_udp_from(
        self, local_addr: str | None, data: bytes, target: tuple[str, int],
    ) -> None:
        """One datagram from a socket opened for it and closed after."""
        udp = await self._open_udp(local_addr=local_addr)
        try:
            await udp.send_to(data, target[0], target[1])
        finally:
            await udp.close()

    async def wake_on_lan(
        self,
        mac: str,
        host: str | None = None,
        port: int = WAKE_ON_LAN_PORT,
    ) -> str:
        """Send the Wake-on-LAN magic packet for ``mac``.

        Goes to the broadcast address and directly to ``host`` (default: the
        device's own host), which is what wakes a display whether or not a
        switch between here and there passes broadcast. The direct send is
        best effort: an unroutable host is logged, not raised, because the
        broadcast already went out. Raises :class:`WakeAddressError` for a
        MAC that is not one. Returns the MAC in normalized form.
        """
        packet = magic_packet(mac)
        normalized = normalize_mac(mac) or mac
        await self.send_udp(packet, port=port, broadcast=True)
        direct = str(host or self.config.get("host") or "").strip()
        if self.udp_redirect is not None:
            direct = ""  # both sends land on the simulator; one is enough
        if direct:
            try:
                await self.send_udp(packet, host=direct, port=port)
            except OSError as exc:
                log.warning(
                    f"[{self.device_id}] Wake-on-LAN direct send to "
                    f"{direct}:{port} failed ({exc}); the broadcast went out"
                )
        log.info(
            f"[{self.device_id}] Wake-on-LAN magic packet sent for {normalized}"
        )
        return normalized
