"""How a YAML driver writes a device setting (``device_settings.<key>.write``).

The rules worth knowing before changing anything here:

* **The value that goes out.** A setting's ``map`` names the device's own
  word for a value (ON for true) and is written as declared. Otherwise the
  setting writes its state variable's real value (``state_key``, else the
  setting's own key), turned back into the device's own number by that
  variable's ``scale`` / ``offset``. ``{value}``, any config field and the
  platform's push values fill the write as they fill a command's send, and a
  placeholder nothing fills goes out as written with the warning a command's
  send gives.
* **Three write forms**, chosen by the keys the ``write`` block has, in this
  order: ``address`` is an OSC message (refused unless connected, and on an
  OSC transport); ``path`` or ``method`` an HTTP request (refused unless the
  transport is HTTP); ``send`` a line of the device's protocol (refused unless
  connected; inside the ``send_frame`` header on every link, UDP included,
  as a command's ``send`` is: ``send_framing`` says where each origin's
  framing differs). An HTTP write is built by a command's own request builder
  (``http_commands._http_request``) and posts when it names no method; the
  contract does not let a write declare ``query_params``.
  A block with none of them is refused before anything is sent.
* **What each returns.** True for an OSC message or a line, the HTTP answer
  for a request. A body that parses as JSON goes as JSON and its answer is
  read by the reply rules; any other body goes as raw content and its answer
  is returned unread. Which body is which is a command's rule too
  (``http_commands.body_parts``).

``SettingWrites`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object; it answers ``BaseDriver``'s
``set_device_setting``, which the device manager calls for a live write and
for a queued one when the device connects.
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.drivers import compiled_protocol
from openavc.drivers.base import UnknownDeviceSettingError
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")


class SettingWrites:
    """Device setting writes, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver / OscWire; declared here for
    # the methods.
    device_id: str
    config: dict[str, Any]
    transport: Any
    _definition: dict[str, Any]
    on_data_received: Callable[[bytes], Any]
    _push_params: Callable[[], dict[str, Any]]
    _substitute_for_wire: Callable[[str, str, dict[str, Any]], str]
    _build_osc_args: Callable[[list[Any], dict[str, Any]], list[Any]]
    _http_request: Callable[..., Any]
    _line_bytes: Callable[[str], bytes]

    async def set_device_setting(self, key: str, value: Any) -> Any:
        """Write one device setting through its declared ``write`` block."""
        setting_def, write_def = self._setting_write_block(key)
        params = {
            **self.config, **self._push_params(),
            "value": self._setting_wire_value(key, setting_def, value),
        }
        writer = self._setting_writer(write_def)
        if writer is None:
            raise NotImplementedError(
                f"Device setting '{key}' write definition has no address, path or send"
            )
        return await writer(key, value, write_def, params)

    def _setting_write_block(self, key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """The setting's declaration and its ``write`` block, or the refusal."""
        setting_def = self._definition.get("device_settings", {}).get(key)
        if not setting_def:
            raise UnknownDeviceSettingError(f"Unknown device setting: {key}")
        write_def = setting_def.get("write")
        if not write_def:
            raise NotImplementedError(
                f"Device setting '{key}' has no write definition"
            )
        return setting_def, write_def

    def _setting_writer(self, write_def: dict[str, Any]) -> Callable[..., Any] | None:
        """The write form a ``write`` block names, in the order they are
        tried."""
        if "address" in write_def:
            return self._write_setting_osc
        if "path" in write_def or "method" in write_def:
            return self._write_setting_http
        if write_def.get("send", ""):
            return self._write_setting_line
        return None

    def _setting_wire_value(self, key: str, setting_def: dict[str, Any], value: Any) -> Any:
        """The device's own word for the value, else its real value as the
        device's own number."""
        word = compiled_protocol.map_word(value, setting_def.get("map"))
        if word is not None:
            return word
        state_var = self._definition.get("state_variables", {}).get(
            setting_def.get("state_key") or key
        )
        return compiled_protocol.value_to_wire(value, state_var)

    def _require_connected(self) -> None:
        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

    def _setting_written(self, key: str, value: Any) -> None:
        log.debug(f"[{self.device_id}] Set device setting '{key}' = {value!r}")

    async def _write_setting_osc(
        self, key: str, value: Any, write_def: dict[str, Any], params: dict[str, Any],
    ) -> bool:
        from openavc.transport.osc import OSCTransport
        from openavc.transport.osc_codec import osc_encode_message

        self._require_connected()
        # Never OSC bytes on a socket that is not OSC, as for HTTP below.
        if not isinstance(self.transport, OSCTransport):
            raise ConnectionError(
                f"[{self.device_id}] Setting '{key}' uses OSC write "
                f"but transport is not OSC"
            )
        address = self._substitute_for_wire(
            f"device_settings.{key}.write.address", write_def.get("address", ""), params,
        )
        args = self._build_osc_args(write_def.get("args", []), params)
        await self.transport.send(osc_encode_message(address, args))
        self._setting_written(key, value)
        return True

    async def _write_setting_http(
        self, key: str, value: Any, write_def: dict[str, Any], params: dict[str, Any],
    ) -> Any:
        from openavc.transport.http_client import HTTPClientTransport

        if not isinstance(self.transport, HTTPClientTransport):
            raise ConnectionError(
                f"[{self.device_id}] Setting '{key}' uses HTTP write "
                f"but transport is not HTTP"
            )
        # A write posts when it names no method.
        response = await self._http_request(
            key, f"device_settings.{key}.write", {"method": "POST", **write_def}, params,
        )
        # Whatever the body was, the answer is read: often the cheapest read
        # of the new value.
        if hasattr(response, "text") and response.text:
            await self.on_data_received(response.text.encode("utf-8"))
        self._setting_written(key, value)
        return response

    async def _write_setting_line(
        self, key: str, value: Any, write_def: dict[str, Any], params: dict[str, Any],
    ) -> bool:
        self._require_connected()
        formatted = self._substitute_for_wire(
            f"device_settings.{key}.write.send", write_def["send"], params,
        )
        await self.transport.send(self._line_bytes(formatted))
        self._setting_written(key, value)
        return True
