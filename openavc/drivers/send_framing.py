"""How a YAML driver frames the bytes its own protocol puts on the wire, from
every place that sends them.

The rules worth knowing before changing anything here:

* **THE rule for a command's framing is ``framed_send``**: the text a
  command's ``send`` goes on the wire as, before its placeholders are filled.
  The runtime sends it, the simulator answers it (``yaml_auto``, and
  ``inline_device`` for a device's own commands) and the validator samples it
  (``send_coverage``), each with its own config: the device's settings, or
  the driver's defaults where no device is known. It is pure, but keeps this
  module's logger: no checker asks it.
* **A file command** (``commands.<name>.send``) goes inside
  ``command_prefix`` / ``command_suffix`` (the device's config first, then
  the definition) at send time, unless it says ``raw: true``, whatever the
  send already starts or ends with; then its placeholders are filled, its
  escape sequences decoded, and the whole goes inside the ``send_frame``
  header (``build_wire``). Neither half falls back to the delimiter: a file
  command's send string carries its own terminator, and a fallback would end
  it twice.
* **A command the device authored itself** (the inline protocol) is framed
  once, when it is merged in (``framed_commands``), with the prefix unless it
  already starts with it, and the suffix, else the line ending (the device's
  ``delimiter``, else the driver's), unless it already ends with it, so its
  poll line and the simulator see the framed form; ``build_wire`` leaves it
  alone (``_inline_command_names``).
* **Sent unframed on purpose:** the Live Test's raw probe and the summary of
  what it sent (``api/routes/driver_test.py``), and the device page's Send
  raw box (``BaseDriver.send_raw``), which adds the line ending only.
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

from collections.abc import Mapping
from typing import Any, Callable

from openavc.drivers import compiled_protocol
from openavc.transport.binary_helpers import encode_escape_sequences as _safe_encode_escapes
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")


def command_prefix(config: Mapping[str, Any], definition: Mapping[str, Any]) -> str:
    """What goes before a command: the device's ``command_prefix``, else the
    definition's."""
    return config.get("command_prefix") or definition.get("command_prefix") or ""


def command_suffix(config: Mapping[str, Any], definition: Mapping[str, Any]) -> str:
    """What goes after a command: the device's ``command_suffix``, else the
    definition's."""
    return config.get("command_suffix") or definition.get("command_suffix") or ""


def inline_suffix(config: Mapping[str, Any], definition: Mapping[str, Any]) -> str:
    """What ends a command the device authored: the suffix, else the line
    ending, which is the device's configured delimiter, else the driver's
    (a YAML driver's ``DRIVER_INFO`` carries the definition's), so a command
    typed in the editor does not need its own \\r. A configured suffix or
    delimiter that is not text is passed over, as the simulator reads it."""
    suffix, delimiter = command_suffix(config, definition), config.get("delimiter")
    return _text(suffix) or _text(delimiter) or definition.get("delimiter", "")


def framed_send(
    definition: Mapping[str, Any],
    config: Mapping[str, Any],
    command: Mapping[str, Any],
    *,
    authored: bool = False,
) -> Any:
    """The text ``command``'s ``send`` goes on the wire as, its placeholders
    not yet filled: the module docstring's rule, for a file command, or for
    one the device authored (``authored``). A ``raw: true`` command, and a
    send that is not text where a rule would have to change it, are as
    written."""
    send = command.get("send")
    if command.get("raw"):
        return send
    prefix = command_prefix(config, definition)
    if authored:
        return _framed_authored(send, prefix, inline_suffix(config, definition))
    return _framed_file(send, prefix, command_suffix(config, definition))


def framed_commands(
    definition: Mapping[str, Any], config: Mapping[str, Any], commands: Mapping[str, Any],
) -> dict[str, Any]:
    """The commands a device authored, each with its send framed once
    (``framed_send``), for the runtime's merge and the simulator's; a command
    with no ``send`` (an HTTP or OSC row) is kept as it is."""
    return {
        name: {**cmd, "send": framed_send(definition, config, cmd, authored=True)}
        if "send" in cmd else cmd
        for name, cmd in commands.items()
    }


def _framed_file(send: Any, prefix: str, suffix: str) -> Any:
    """A file command's send inside the prefix and the suffix, or as written
    when the driver declares neither (so a ``send`` that is not text,
    ``send: 123``, is refused by the substitution rather than turned into
    text here)."""
    if not prefix and not suffix:
        return send
    return f"{prefix}{send}{suffix}"


def _text(value: Any) -> str:
    """``value`` when it is text, else nothing: an affix of a device's own
    command that is not text is passed over."""
    return value if isinstance(value, str) else ""


def _framed_authored(send: Any, prefix: Any, line_ending: str) -> Any:
    """A device's own command: the prefix unless it already starts with it,
    the line ending unless it already ends with it."""
    prefix = _text(prefix)
    if not isinstance(send, str):
        return send
    if prefix and not send.startswith(prefix):
        send = prefix + send
    if line_ending and not send.endswith(line_ending):
        send = send + line_ending
    return send


class SendFraming:
    """Send-side framing, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver; declared here for the
    # methods.
    device_id: str
    config: dict[str, Any]
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
        self._command_prefix = command_prefix(self.config, self._definition)
        self._command_suffix = command_suffix(self.config, self._definition)
        self._send_frame = self._build_send_frame(self._definition.get("send_frame"))

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
        """A file command's send string as ``framed_send`` frames it, with
        the prefix and the suffix this driver read when it was set up; as
        written when the command is exempt."""
        if self._exempt_from_framing(command, cmd_def):
            return send
        return _framed_file(
            send, getattr(self, "_command_prefix", ""), getattr(self, "_command_suffix", ""),
        )

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
