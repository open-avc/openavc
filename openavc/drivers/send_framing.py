"""How a YAML driver frames the bytes its own protocol puts on the wire, from
every place that sends them.

The rules worth knowing before changing anything here:

* **A file command** (``commands.<name>.send``) goes inside
  ``command_prefix`` / ``command_suffix`` (the device's config first, then
  the definition) at send time, unless it says ``raw: true``; then its
  placeholders are filled, its escape sequences decoded, and the whole goes
  inside the ``send_frame`` header (``build_wire``). Neither half falls back
  to the delimiter: a file command's send string carries its own
  terminator, and a fallback would end it twice.
* **A command the device authored itself** (the inline protocol) is framed
  once, when it is merged in, with the prefix and the suffix, else the line
  ending (the device's ``delimiter``, else the driver's), so its poll line
  and the simulator see the framed form; ``build_wire`` leaves it alone
  (``_inline_command_names``).
* **A raw line** (a start-up, poll or read-back line that names no command)
  takes no prefix or suffix: it is escape-decoded and goes inside the
  ``send_frame`` header on every link but UDP (``_raw_bytes``).
* **The liveness probe and a device setting's ``send`` line** take no prefix
  or suffix either, and go inside the header on every link, UDP included
  (``_line_bytes``), unlike a raw line on UDP.
* **A ``udp:`` side-send** takes none of it: that channel speaks a protocol
  of its own (``_unframed_bytes``).
* **The ``auth:`` sign-in's credential lines** take none of it either: each
  goes as typed with the sign-in's own line ending (``sign_in``), before the
  device reads connected.
* ``send_frame`` is a ``length_prefix`` header whose length field is
  computed per message, so a protocol whose header carries the data length
  (eISCP's 16 bytes) is declared once; ``compiled_protocol`` builds and
  applies it, for the simulator too.

``SendFraming`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object.
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.drivers import compiled_protocol
from openavc.transport.binary_helpers import encode_escape_sequences as _safe_encode_escapes
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")


class SendFraming:
    """Send-side framing, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver; declared here for the
    # methods.
    device_id: str
    config: dict[str, Any]
    DRIVER_INFO: dict[str, Any]
    _definition: dict[str, Any]
    # The commands the device authored itself, set by the inline-protocol
    # merge (inline_merge's _merge_config_protocol); read with a default.
    _inline_command_names: set[str]
    _push_params: Callable[[], dict[str, Any]]
    _substitute_for_wire: Callable[[str, str, dict[str, Any]], str]

    def _init_send_framing(self) -> None:
        """The prefix, suffix and ``send_frame`` header, the device's config
        first, then the definition. Set before the inline-protocol merge,
        which frames the device's own commands with them."""
        self._command_prefix = (
            self.config.get("command_prefix")
            or self._definition.get("command_prefix")
            or ""
        )
        self._command_suffix = (
            self.config.get("command_suffix")
            or self._definition.get("command_suffix")
            or ""
        )
        self._send_frame = self._build_send_frame(self._definition.get("send_frame"))

    def _line_ending(self) -> str:
        """The line terminator a command the device authored ends with when
        no suffix is set: the device's configured delimiter, else the driver's
        (DRIVER_INFO, then the definition), so a command typed in the editor
        does not need its own \\r."""
        return (
            self.config.get("delimiter")
            or self.DRIVER_INFO.get("delimiter")
            or self._definition.get("delimiter", "")
        )

    def _inline_suffix(self) -> str:
        """What ends a command the device authored: the suffix, else the
        line ending."""
        return self._command_suffix or self._line_ending()

    def build_wire(
        self, command: str, params: dict[str, Any] | None = None
    ) -> bytes | None:
        """Build the exact bytes a byte-stream command puts on the wire.

        The whole send-side build in one place: command_prefix/suffix framing,
        placeholder substitution, escape decoding, and the computed send_frame
        header. Returns None when the command is unknown or declares no send
        string (the two cases that make a byte-stream send a no-op).

        Needs no transport, so the send path is testable — and previewable —
        without opening a socket. Two limits on calling it directly: it is the
        byte-stream route only (OSC and HTTP commands are built by their own
        senders), and it takes params as given. send_command validates params
        against the command's declared schema and applies their ``map:``
        translations first, so anything that must match a real send has to go
        through send_command, not here.
        """
        params = params or {}
        cmd_def = self._definition.get("commands", {}).get(command)
        if not isinstance(cmd_def, dict):
            return None

        raw = cmd_def.get("send", "")
        if not raw:
            log.warning(f"[{self.device_id}] Command '{command}' has no send string")
            return None

        # Config values fill placeholders too ({set_id}, {level_instance_tag}).
        # Only {name} tokens naming a known value are replaced, so a JSON
        # protocol's literal braces survive.
        all_params = {**self.config, **self._push_params(), **params}
        formatted = self._substitute_for_wire(
            f"commands.{command}.send", self._framed_send(command, cmd_def, raw), all_params,
        )
        return self._line_bytes(formatted)

    def _framed_send(self, command: str, cmd_def: dict[str, Any], send: Any) -> Any:
        """A file command's send string inside the prefix and suffix, or as
        written when the command is exempt or the driver declares neither (so
        a ``send`` that is not text, ``send: 123``, is refused by the
        substitution rather than turned into text here)."""
        if self._exempt_from_framing(command, cmd_def):
            return send
        prefix = getattr(self, "_command_prefix", "")
        suffix = getattr(self, "_command_suffix", "")
        if not prefix and not suffix:
            return send
        return f"{prefix}{send}{suffix}"

    def _exempt_from_framing(self, command: str, cmd_def: dict[str, Any]) -> bool:
        """A command the device authored (framed at the merge) or one that
        says ``raw: true`` (on the wire exactly as written)."""
        return command in getattr(self, "_inline_command_names", ()) or bool(
            cmd_def.get("raw")
        )

    def _raw_bytes(self, transport_type: Any, text: str) -> bytes:
        """A raw line's protocol bytes: escape-decoded, and on any link but
        UDP inside the ``send_frame`` header (a no-op unless declared)."""
        if transport_type == "udp":
            return self._unframed_bytes(text)
        return self._line_bytes(text)

    def _line_bytes(self, text: str) -> bytes:
        """A line of the device's protocol, escape-decoded, inside the
        ``send_frame`` header."""
        return self._apply_send_frame(_safe_encode_escapes(text))

    @staticmethod
    def _unframed_bytes(text: str) -> bytes:
        """A line escape-decoded and nothing more: a ``udp:`` side-send's."""
        return _safe_encode_escapes(text)

    _build_send_frame = staticmethod(compiled_protocol.build_send_frame)

    def _apply_send_frame(self, data: bytes) -> bytes:
        """Wrap escape-decoded byte-stream payload in the send_frame header.

        No-op unless the driver declares a send_frame block. The data-length
        field is computed from ``len(data)`` per message — the piece a static
        command_prefix can't express.
        """
        return compiled_protocol.apply_send_frame(self._send_frame, data)
