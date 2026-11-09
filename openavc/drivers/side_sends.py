"""A YAML command that goes out without the device's own connection: an
``ir:`` code through the bound bridge, or a ``udp:`` block as one datagram
beside the main transport.

The rules worth knowing before changing anything here:

* **These two come before the connected gate** (``_send_command`` asks
  ``_side_sender`` first): an IR device routed through a bridge has no
  transport of its own, and a wake is sent exactly while the control link is
  down. ``_side_sender`` is where a send decides that a command is one of
  them, and a definition that is not a mapping is neither; the Driver
  Builder's dry run of one command picks its route with its own checks
  (``dry_run.capture_command_transport``), so a new kind is added there too.
* **An ``ir:`` code** goes to the bound bridge's emitter as its Pronto text
  and repeat count (one when absent); the bridge driver converts and
  confirms it.
* **A ``udp:`` block's parameters** are checked and wire-mapped as on the
  transport's own route, and filled with the device's config and the
  platform's push values under them (a parameter wins).
* **``payload``** is filled and escape-decoded like ``send`` but never
  framed: ``command_prefix`` / ``command_suffix`` and ``send_frame`` belong
  to the main transport's protocol, and the side channel speaks its own
  (``_unframed_bytes``). It needs a port; ``broadcast: true`` sends it to
  255.255.255.255.
* **``magic_packet``** names the state variable or config field holding the
  device's MAC: state first, because a MAC the device reported on its last
  connection beats one typed months ago, and a reported value that is not a
  MAC (the all-zero one) gives way to the typed one. Its port defaults to the
  Wake-on-LAN port. With no usable MAC it raises ``WakeAddressError``, which
  names the field as Edit Device does and says whether it fills itself in.
* **``host``** defaults to the device's own; ``port`` may be a literal or a
  ``{config}`` placeholder, so a receiver port that is a device setting stays
  a config field. A port that is not a number from 1 to 65535 is refused,
  with the device and the block named.

``SideSends`` is a mixin ``ConfigurableDriver`` inherits, so every name stays
on the driver object. The datagram itself is ``BaseDriver.send_udp`` and
``wake_on_lan`` (``udp_side``), the bridge emit ``emit_via_bridge``
(``bridge_ports``).
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.drivers.param_labels import param_label
from openavc.drivers.udp_side import WAKE_ON_LAN_PORT, WakeAddressError, normalize_mac
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")


def _udp_port_number(where: str, text: str) -> int:
    """A ``udp:`` block's port text as a port number, or the refusal; ``where``
    leads the message."""
    try:
        port = int(str(text).strip())
    except ValueError:
        raise ValueError(f"{where}: port {text!r} is not a number") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"{where}: port {port} is not between 1 and 65535")
    return port


class SideSends:
    """IR codes and ``udp:`` blocks, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver and its other mixins;
    # declared here for the methods.
    device_id: str
    config: dict[str, Any]
    _definition: dict[str, Any]
    get_state: Callable[[str], Any]
    emit_via_bridge: Callable[..., Any]
    send_udp: Callable[..., Any]
    wake_on_lan: Callable[..., Any]
    _given_and_wire: Callable[
        [str, dict[str, Any], dict[str, Any]],
        tuple[dict[str, Any], dict[str, Any]],
    ]
    _push_params: Callable[[], dict[str, Any]]
    _substitute_for_wire: Callable[[str, str, dict[str, Any]], str]
    _unframed_bytes: Callable[[str], bytes]

    def _side_sender(self, cmd_def: Any) -> Any:
        """The sender for a command that needs no connection of the device's
        own, an IR code or a UDP side-send; None for every other command."""
        if not isinstance(cmd_def, dict):
            return None
        if cmd_def.get("ir"):
            return self._send_ir_command
        if isinstance(cmd_def.get("udp"), dict):
            return self._send_udp_side
        return None

    async def _send_ir_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any],
    ) -> Any:
        """An IR code-set command, emitted through the bound bridge."""
        ir = cmd_def["ir"]
        return await self.emit_via_bridge(
            "ir", {"pronto": ir.get("pronto", ""), "repeat": ir.get("repeat", 1)},
        )

    async def _send_udp_side(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any],
    ) -> Any:
        """A UDP side-send, its params checked and wire-mapped as on the
        transport's own route."""
        _given, wire = self._given_and_wire(command, cmd_def, params)
        return await self._send_udp_command(command, cmd_def, wire)

    async def _send_udp_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any]
    ) -> Any:
        """Send a command's ``udp:`` block as one datagram: the magic packet
        when it names one, else its payload."""
        udp = cmd_def["udp"]
        where = f"commands.{command}.udp"
        all_params = {**self.config, **self._push_params(), **params}

        host_raw = udp.get("host")
        host = (
            self._substitute_for_wire(f"{where}.host", str(host_raw), all_params)
            if host_raw
            else None
        )

        mac_field = str(udp.get("magic_packet") or "")
        if mac_field:
            port = self._resolve_udp_port(
                where, udp.get("port"), all_params, default=WAKE_ON_LAN_PORT
            )
            typed = str(self.config.get(mac_field) or "").strip()
            mac = normalize_mac(self.get_state(mac_field)) or normalize_mac(typed)
            if mac is None:
                raise self._wake_address_error(mac_field, typed)
            await self.wake_on_lan(mac, host=host, port=port)
            log.debug(f"[{self.device_id}] Sent command '{command}': magic packet")
            return True

        port = self._resolve_udp_port(where, udp.get("port"), all_params)
        formatted = self._substitute_for_wire(
            f"{where}.payload", str(udp.get("payload") or ""), all_params
        )
        data = self._unframed_bytes(formatted)
        await self.send_udp(
            data, host=host, port=port, broadcast=bool(udp.get("broadcast"))
        )
        log.debug(f"[{self.device_id}] Sent command '{command}' over UDP: {data!r}")
        return True

    def _wake_address_error(self, mac_field: str, typed: str) -> WakeAddressError:
        """The refusal for a wake with no usable MAC in ``mac_field``.

        Names the field as Edit Device does, and says the MAC fills itself in
        only when the driver does read it from the device: a state variable of
        the same name, or a field the device's report is saved into.
        """
        from openavc.core.learned_config import learned_fields

        field_def = (self._definition.get("config_schema") or {}).get(mac_field)
        field_def = field_def if isinstance(field_def, dict) else {}
        reported = mac_field in learned_fields(self) or mac_field in (
            self._definition.get("state_variables") or {}
        )
        return WakeAddressError(
            field_label=param_label(mac_field, field_def),
            value=typed,
            reported=reported,
        )

    def _resolve_udp_port(
        self,
        where: str,
        raw: Any,
        all_params: dict[str, Any],
        default: int | None = None,
    ) -> int:
        """The port a ``udp:`` block names: a number, or a {config} placeholder;
        ``default`` when it names none."""
        if raw is None or raw == "":
            if default is None:
                raise ValueError(f"[{self.device_id}] {where}: no port to send to")
            return default
        text = self._substitute_for_wire(f"{where}.port", str(raw), all_params)
        return _udp_port_number(f"[{self.device_id}] {where}", text)
