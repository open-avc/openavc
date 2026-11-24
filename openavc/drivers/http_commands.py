"""A YAML driver's HTTP command: the request a command with ``path`` or
``method`` makes (``command_form`` says which commands those are), and what
becomes of its answer.

The rules worth knowing before changing anything here:

* **The request.** ``method`` (GET when absent, any case), ``path`` (the root
  when absent), ``body``, ``query_params`` and ``headers``, each filled from
  the command's parameters, the device's config and the platform's push
  values. Only a ``{name}`` naming one of those is replaced, so a JSON body's
  own braces survive. The path and the body warn about a placeholder nothing
  fills (``_substitute_for_wire``); a query value or a header does not. A
  query value that is not text goes as it is, a header as its text.
* **A body that parses as JSON goes as JSON; anything else** (XML, form text,
  a bare token) goes as raw content (``body_parts``). Use a ``Content-Type``
  header for a body that is not JSON.
* **A device setting's HTTP write** is built by the same request builder
  (``_http_request``), so it fills and warns as a command does; only its
  default method (POST) and what becomes of its answer are its own
  (``setting_writes``).
* **The answer** goes to the reply rules as a line of text, whatever its
  status, so state shows what the device holds. A status of 400 and up then
  fails a command a person sent, and a setting write, with its outcome
  (``command_outcome.answered_outcome``; ``_fail_on_error_status``), once a
  command's read-back is scheduled. A poll, start-up or follow-up line is
  not failed by its status: its answer goes to the credential check
  (``http_credential``). A command with HTTP fields on another transport
  sends nothing and is refused as not sent.

``HttpCommands`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. A poll or start-up line that is a raw path reads
its answer through ``_process_http_response`` too (``start_up_and_poll``).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from openavc.drivers.command_outcome import CommandOutcomeError, answered_outcome, not_sent
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")


def request_target(req_def: dict[str, Any], method: str = "GET") -> tuple[str, str]:
    """A request's method, upper case, and its path as written: ``method``
    and the root when it names none (a null or empty field names none, as
    ``command_form`` reads it)."""
    return str(req_def.get("method") or method).upper(), str(req_def.get("path") or "/")


def body_parts(text: str) -> tuple[Any, bytes | None]:
    """A filled-in body as (JSON, raw content): the JSON when it parses, else
    the text as raw content."""
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError):
        return None, text.encode("utf-8")


def answered_note(error: BaseException) -> str:
    """For a door the driver's author reads (the Driver Builder's Live
    Test): the HTTP status a refused command was answered with, as a
    sentence to follow the outcome's; nothing for any other failure."""
    status = getattr(error, "http_status", None)
    return f" (The device answered HTTP {status}.)" if status is not None else ""


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
    _effective_transport: Callable[[], str]

    async def _send_http_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any]
    ) -> Any:
        """Send the command's request and read its answer; the answer.

        On a connection that is not HTTP nothing goes out, and the command is
        refused as not sent (``command_outcome.not_sent``)."""
        from openavc.transport.http_client import HTTPClientTransport

        if not isinstance(self.transport, HTTPClientTransport):
            log.error(
                f"[{self.device_id}] Command '{command}' uses HTTP fields "
                f"but transport is not HTTP"
            )
            raise not_sent(
                command, cmd_def.get("label"), form="http", transport=self._effective_transport(),
            )

        all_params = {**self.config, **self._push_params(), **params}
        response = await self._http_request(command, f"commands.{command}", cmd_def, all_params)
        return await self._process_http_response(command, response)

    async def _http_request(
        self, name: str, where: str, req_def: dict[str, Any], all_params: dict[str, Any],
    ) -> Any:
        """Send the request ``req_def`` declares, a command's or a device
        setting's ``write``, filled from ``all_params``; the answer, unread.

        GET when ``req_def`` names no method and the root when it names no
        path (``request_target``); ``where`` names its fields in a warning
        and ``name`` names it in the log.
        """
        headers = self._build_http_headers(req_def.get("headers"), all_params)
        method, path = request_target(req_def)
        path = self._substitute_for_wire(f"{where}.path", path, all_params)
        json_body, content = self._http_body(name, where, req_def.get("body"), all_params)
        query_params = self._http_query(req_def.get("query_params"), all_params)
        return await self.transport.request(
            method, path, params=query_params,
            json_body=json_body, content=content, headers=headers,
        )

    def _http_body(
        self, name: str, where: str, raw_body: Any, params: dict[str, Any]
    ) -> tuple[Any, bytes | None]:
        """The request's body, filled in, as (JSON, raw content); neither
        with no body."""
        if not raw_body:
            return None, None
        json_body, content = body_parts(
            self._substitute_for_wire(f"{where}.body", raw_body, params)
        )
        if content is not None:
            log.debug(
                f"[{self.device_id}] Body for '{name}' is not JSON, "
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

    def _fail_on_error_status(self, what: str, answer: Any) -> None:
        """Raise the outcome of an answer whose status is 400 and up
        (``answered_outcome``); anything else, and a send that is not an HTTP
        answer, passes. ``what`` names the send in the log line, which keeps
        the status the outcome's sentence leaves out."""
        status = getattr(answer, "status_code", None)
        outcome = answered_outcome(status)
        if outcome is None:
            return
        log.warning(f"[{self.device_id}] {what} was answered HTTP {status}")
        error = CommandOutcomeError(outcome)
        error.http_status = status
        raise error

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
