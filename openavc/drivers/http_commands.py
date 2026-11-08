"""A YAML driver's HTTP command: the request a command with ``path`` or
``method`` makes, and what becomes of its answer.

The rules worth knowing before changing anything here:

* **The request.** ``method`` (GET when absent, any case), ``path`` (the root
  when absent), ``body``, ``query_params`` and ``headers``, each filled from
  the command's parameters, the device's config and the platform's push
  values. Only a ``{name}`` naming one of those is replaced, so a JSON body's
  own braces survive. The path and the body warn about a placeholder nothing
  fills (``_substitute_for_wire``); a query value or a header does not. A
  query value that is not text goes as it is, a header as its text.
* **A body that parses as JSON goes as JSON; anything else** (XML, form text,
  a bare token) goes as raw content (``body_parts``, which a device setting's
  HTTP write uses too). Use a ``Content-Type`` header for a body that is not
  JSON.
* **The answer** goes to the reply rules as a line of text and is returned,
  whatever its status. A command with HTTP fields on another transport sends
  nothing, logs an error and returns None.

``HttpCommands`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. A poll or start-up line that is a raw path reads
its answer through ``_process_http_response`` too (``start_up_and_poll``).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")


def body_parts(text: str) -> tuple[Any, bytes | None]:
    """A filled-in body as (JSON, raw content): the JSON when it parses, else
    the text as raw content."""
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError):
        return None, text.encode("utf-8")


class HttpCommands:
    """HTTP commands, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver; declared here for the
    # methods.
    device_id: str
    config: dict[str, Any]
    transport: Any
    on_data_received: Callable[[bytes], Any]
    _push_params: Callable[[], dict[str, Any]]
    _safe_substitute: Callable[[str, dict[str, Any]], str]
    _substitute_for_wire: Callable[[str, str, dict[str, Any]], str]

    def _is_http_command(self, cmd_def: dict[str, Any]) -> bool:
        """Check if a command definition uses HTTP-style fields."""
        return "path" in cmd_def or "method" in cmd_def

    async def _send_http_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any]
    ) -> Any:
        """Send the command's request and read its answer; the answer."""
        from openavc.transport.http_client import HTTPClientTransport

        if not isinstance(self.transport, HTTPClientTransport):
            log.error(
                f"[{self.device_id}] Command '{command}' uses HTTP fields "
                f"but transport is not HTTP"
            )
            return None

        all_params = {**self.config, **self._push_params(), **params}
        method = cmd_def.get("method", "GET").upper()
        headers = self._build_http_headers(cmd_def.get("headers"), all_params)
        path = self._substitute_for_wire(
            f"commands.{command}.path", cmd_def.get("path", "/"), all_params
        )
        json_body, content = self._http_body(command, cmd_def.get("body"), all_params)
        query_params = self._http_query(cmd_def.get("query_params"), all_params)

        response = await self.transport.request(
            method, path, params=query_params, json_body=json_body,
            content=content, headers=headers,
        )
        return await self._process_http_response(command, response)

    def _http_body(
        self, command: str, raw_body: Any, params: dict[str, Any]
    ) -> tuple[Any, bytes | None]:
        """The command's body, filled in, as (JSON, raw content); neither
        with no body."""
        if not raw_body:
            return None, None
        json_body, content = body_parts(
            self._substitute_for_wire(f"commands.{command}.body", raw_body, params)
        )
        if content is not None:
            log.debug(
                f"[{self.device_id}] Body for '{command}' is not JSON, "
                f"sending as raw content"
            )
        return json_body, content

    def _http_query(self, raw_query: Any, params: dict[str, Any]) -> dict[str, Any] | None:
        """The command's ``query_params``, text values filled in; None unless
        it is a dict with something in it."""
        if not raw_query or not isinstance(raw_query, dict):
            return None
        return {
            k: self._safe_substitute(v, params) if isinstance(v, str) else v
            for k, v in raw_query.items()
        }

    def _build_http_headers(
        self, raw_headers: Any, params: dict[str, Any]
    ) -> dict[str, str] | None:
        """Substitute {param} placeholders in YAML-defined HTTP headers."""
        if not raw_headers or not isinstance(raw_headers, dict):
            return None
        out: dict[str, str] = {}
        for k, v in raw_headers.items():
            if isinstance(v, str):
                out[k] = self._safe_substitute(v, params)
            else:
                out[k] = str(v)
        return out

    async def _process_http_response(
        self, command: str, response: Any
    ) -> Any:
        """Read an HTTP answer with the reply rules; the answer.

        ``command`` is the command name, or the raw path when a polling query
        addressed one directly — it only labels the log line.
        """
        log.debug(
            f"[{self.device_id}] HTTP command '{command}' -> "
            f"status={response.status_code}"
        )
        if response.text:
            await self.on_data_received(response.text.encode("utf-8"))
        return response
