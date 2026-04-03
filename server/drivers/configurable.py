"""
OpenAVC ConfigurableDriver — interprets JSON driver definitions at runtime.

This enables creating device drivers without writing Python code. A JSON
driver definition specifies transport, commands, response parsing, and
polling — the ConfigurableDriver reads this at runtime and produces the
same behavior as a hand-coded Python driver.

Usage:
    driver_def = load_json("extron_switcher.json")
    DriverClass = create_configurable_driver_class(driver_def)
    register_driver(DriverClass)
"""

from __future__ import annotations

import json
import re
from typing import Any

from server.drivers.base import BaseDriver
from server.transport.binary_helpers import encode_escape_sequences as _safe_encode_escapes
from server.transport.frame_parsers import FrameParser
from server.utils.logger import get_logger

log = get_logger(__name__)


class ConfigurableDriver(BaseDriver):
    """
    A driver that interprets a JSON driver definition at runtime.

    The definition dict must contain:
        - id, name, manufacturer, category, transport
        - commands: dict of command_name -> {string, params}
        - responses: list of {pattern, mappings} for parsing
        - polling: optional {interval, queries}
        - state_variables, config_schema, default_config
    """

    # DRIVER_INFO is set dynamically by create_configurable_driver_class()
    DRIVER_INFO: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # _definition is set on the class by the factory function
        self._definition: dict[str, Any] = getattr(self.__class__, "_definition", {})
        super().__init__(*args, **kwargs)

        # Pre-compile response patterns
        self._compiled_responses: list[tuple[re.Pattern[str], list[dict[str, Any]]]] = []
        for resp in self._definition.get("responses", []):
            try:
                # Accept both "pattern" and "match" keys
                raw_pattern = resp.get("pattern", "") or resp.get("match", "")
                if not raw_pattern:
                    continue
                pattern = re.compile(raw_pattern)

                # Accept both "mappings" (detailed) and "set" (shorthand) formats
                mappings = resp.get("mappings", [])
                if not mappings and "set" in resp:
                    # Convert shorthand: {"set": {"input": "$1", "mute": "true"}}
                    # to mappings: [{"group": 1, "state": "input"}, ...]
                    for state_key, value_expr in resp["set"].items():
                        if isinstance(value_expr, str) and value_expr.startswith("$"):
                            try:
                                group = int(value_expr[1:])
                            except ValueError:
                                group = 0
                            mappings.append({"group": group, "state": state_key, "type": "string"})
                        else:
                            # Literal value — store as a static mapping
                            mappings.append({"group": 0, "state": state_key, "value": value_expr})

                self._compiled_responses.append((pattern, mappings))
            except re.error as e:
                log.warning(
                    f"[{self.device_id}] Invalid response pattern "
                    f"'{resp.get('pattern', resp.get('match', ''))}': {e}"
                )

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Look up command in definition, substitute params, send."""
        params = params or {}

        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        commands = self._definition.get("commands", {})
        cmd_def = commands.get(command)
        if cmd_def is None:
            log.warning(f"[{self.device_id}] Unknown command: {command}")
            return None

        # Check if this is an HTTP transport command (has 'path' or 'method' keys)
        if self._is_http_command(cmd_def):
            return await self._send_http_command(command, cmd_def, params)

        # Get the raw command string (accept both "string" and "send" keys)
        raw = cmd_def.get("string", "") or cmd_def.get("send", "")
        if not raw:
            log.warning(f"[{self.device_id}] Command '{command}' has no string/send")
            return None

        # Substitute {param} placeholders — merge config values so drivers
        # can use config fields like {set_id} or {level_instance_tag} in commands
        all_params = {**self.config, **params}
        try:
            formatted = raw.format(**all_params)
        except KeyError as e:
            log.error(
                f"[{self.device_id}] Missing param {e} for command '{command}'"
            )
            return None

        # Encode (handle explicit escape sequences only — safe subset)
        data = _safe_encode_escapes(formatted)
        await self.transport.send(data)
        log.debug(f"[{self.device_id}] Sent command '{command}': {data!r}")
        return True

    def _is_http_command(self, cmd_def: dict[str, Any]) -> bool:
        """Check if a command definition uses HTTP-style fields."""
        return "path" in cmd_def or "method" in cmd_def

    async def _send_http_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any]
    ) -> Any:
        """
        Send an HTTP command using the HTTPClientTransport.

        HTTP commands in .avcdriver files use these fields:
            method: GET, POST, PUT, DELETE (default: GET)
            path: URL path (e.g., "/api/power")
            body: JSON body string with {param} substitution
            query_params: Query parameters dict with {param} substitution

        Parameter substitution uses a safe approach: only {name} tokens
        where name matches a known parameter or config key are replaced.
        Literal JSON braces are preserved.
        """
        from server.transport.http_client import HTTPClientTransport

        if not isinstance(self.transport, HTTPClientTransport):
            log.error(
                f"[{self.device_id}] Command '{command}' uses HTTP fields "
                f"but transport is not HTTP"
            )
            return None

        all_params = {**self.config, **params}

        method = cmd_def.get("method", "GET").upper()
        raw_path = cmd_def.get("path", "/")
        raw_body = cmd_def.get("body")

        # Substitute params in path using safe substitution
        path = self._safe_substitute(raw_path, all_params)

        # Substitute params in body
        json_body = None
        if raw_body:
            body_str = self._safe_substitute(raw_body, all_params)
            # Parse body as JSON
            try:
                json_body = json.loads(body_str)
            except (json.JSONDecodeError, ValueError):
                # Not valid JSON — send as raw string body
                log.debug(
                    f"[{self.device_id}] Body for '{command}' is not JSON, "
                    f"sending as raw content"
                )
                response = await self.transport.request(
                    method, path, content=body_str.encode("utf-8")
                )
                return await self._process_http_response(command, response)

        # Build query params if specified
        query_params = None
        raw_query = cmd_def.get("query_params")
        if raw_query and isinstance(raw_query, dict):
            query_params = {}
            for k, v in raw_query.items():
                if isinstance(v, str):
                    query_params[k] = self._safe_substitute(v, all_params)
                else:
                    query_params[k] = v

        response = await self.transport.request(
            method, path, params=query_params, json_body=json_body
        )
        return await self._process_http_response(command, response)

    @staticmethod
    def _safe_substitute(template: str, params: dict[str, Any]) -> str:
        """
        Substitute {name} placeholders in template with values from params.

        Only replaces {name} where name is a key in params. Literal JSON
        braces and unknown placeholders are left untouched. This avoids
        the problem with Python's str.format() choking on JSON body strings.
        """
        def replacer(match: re.Match) -> str:
            key = match.group(1)
            if key in params:
                return str(params[key])
            return match.group(0)  # Leave unmatched {name} as-is

        return re.sub(r"\{(\w+)\}", replacer, template)

    async def _process_http_response(
        self, command: str, response: Any
    ) -> Any:
        """
        Process an HTTP response: check status and match response patterns.

        Returns the HTTPResponse object for the caller.
        """
        log.debug(
            f"[{self.device_id}] HTTP command '{command}' -> "
            f"status={response.status_code}"
        )

        # Run response text through the standard regex-based response matching
        # so .avcdriver response patterns work with HTTP responses too
        if response.text:
            await self.on_data_received(response.text.encode("utf-8"))

        return response

    async def on_data_received(self, data: bytes) -> None:
        """Match response against pre-compiled regex patterns, update state."""
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            return

        for pattern, mappings in self._compiled_responses:
            match = pattern.search(text)
            if match:
                for mapping in mappings:
                    state_key = mapping.get("state")
                    if not state_key:
                        continue

                    # Static value mapping (no regex group needed)
                    if "value" in mapping:
                        static = mapping["value"]
                        coerced = self._coerce_value(str(static), mapping.get("type", "string"))
                        self.set_state(state_key, coerced)
                        continue

                    # Regex group mapping
                    group = mapping.get("group", 0)
                    value_type = mapping.get("type", "string")
                    value_map = mapping.get("map")

                    try:
                        raw_value = match.group(group)
                    except (IndexError, re.error):
                        continue

                    if raw_value is None:
                        continue

                    # Apply value map if defined
                    if value_map and raw_value in value_map:
                        coerced = value_map[raw_value]
                    else:
                        coerced = self._coerce_value(raw_value, value_type)

                    self.set_state(state_key, coerced)

                log.debug(
                    f"[{self.device_id}] Response matched: {pattern.pattern}"
                )
                return  # Stop at first match

        log.debug(f"[{self.device_id}] Unmatched response: {text!r}")

    async def set_device_setting(self, key: str, value: Any) -> Any:
        """
        Write a device setting using the write definition from the driver YAML.

        Supports HTTP (method/path/body) and TCP/serial (send) write formats.
        """
        settings = self._definition.get("device_settings", {})
        setting_def = settings.get(key)
        if not setting_def:
            raise ValueError(f"Unknown device setting: {key}")

        write_def = setting_def.get("write")
        if not write_def:
            raise NotImplementedError(
                f"Device setting '{key}' has no write definition"
            )

        all_params = {**self.config, "value": value}

        # HTTP write
        if "path" in write_def or "method" in write_def:
            from server.transport.http_client import HTTPClientTransport

            if not isinstance(self.transport, HTTPClientTransport):
                raise ConnectionError(
                    f"[{self.device_id}] Setting '{key}' uses HTTP write "
                    f"but transport is not HTTP"
                )

            method = write_def.get("method", "POST").upper()
            raw_path = write_def.get("path", "/")
            raw_body = write_def.get("body")

            path = self._safe_substitute(raw_path, all_params)

            json_body = None
            if raw_body:
                import json as _json
                body_str = self._safe_substitute(raw_body, all_params)
                try:
                    json_body = _json.loads(body_str)
                except (ValueError, _json.JSONDecodeError):
                    response = await self.transport.request(
                        method, path, content=body_str.encode("utf-8")
                    )
                    return response

            response = await self.transport.request(
                method, path, json_body=json_body
            )

            # Run response through pattern matching
            if hasattr(response, "text") and response.text:
                await self.on_data_received(response.text.encode("utf-8"))

            log.debug(
                f"[{self.device_id}] Set device setting '{key}' = {value!r}"
            )
            return response

        # TCP/serial write
        raw_send = write_def.get("send", "")
        if raw_send:
            if not self.transport or not self.transport.connected:
                raise ConnectionError(f"[{self.device_id}] Not connected")

            formatted = self._safe_substitute(raw_send, all_params)
            data = _safe_encode_escapes(formatted)
            await self.transport.send(data)
            log.debug(
                f"[{self.device_id}] Set device setting '{key}' = {value!r}"
            )
            return True

        raise NotImplementedError(
            f"Device setting '{key}' write definition has no path or send"
        )

    async def poll(self) -> None:
        """
        Send query strings from definition at configured interval.

        For HTTP transport, polling queries can be:
            - Command names (e.g., "get_status") — executes that command
            - URL paths (e.g., "/api/status") — sends a GET request
        For TCP/serial, queries are raw protocol strings as before.
        """
        if not self.transport or not self.transport.connected:
            return

        polling = self._definition.get("polling", {})
        queries = polling.get("queries", [])

        is_http = self._definition.get("transport") == "http"

        for query in queries:
            try:
                if is_http:
                    # For HTTP: query can be a command name or a raw path
                    commands = self._definition.get("commands", {})
                    if query in commands:
                        await self.send_command(query)
                    else:
                        # Treat as a raw GET path
                        formatted = self._safe_substitute(query, self.config) if "{" in query else query
                        response = await self.transport.get(formatted)
                        if response.text:
                            await self.on_data_received(response.text.encode("utf-8"))
                else:
                    # TCP/serial: raw protocol string
                    formatted = self._safe_substitute(query, self.config) if "{" in query else query
                    data = _safe_encode_escapes(formatted)
                    await self.transport.send(data)
            except ConnectionError:
                log.warning(f"[{self.device_id}] Poll query failed — not connected")
                return
            except Exception:  # Catch-all: template substitution, encoding, or HTTP errors
                log.exception(f"[{self.device_id}] Poll query error")

    def _create_frame_parser(self) -> FrameParser | None:
        """Check definition for frame parser config."""
        parser_config = self._definition.get("frame_parser")
        if not parser_config:
            return None

        parser_type = parser_config.get("type", "")
        if parser_type == "length_prefix":
            from server.transport.frame_parsers import LengthPrefixFrameParser

            return LengthPrefixFrameParser(
                header_size=parser_config.get("header_size", 2),
                header_offset=parser_config.get("header_offset", 0),
                include_header=parser_config.get("include_header", False),
            )
        elif parser_type == "fixed_length":
            from server.transport.frame_parsers import FixedLengthFrameParser

            return FixedLengthFrameParser(
                length=parser_config.get("length", 1),
            )

        return None

    @staticmethod
    def _coerce_value(raw: str, value_type: str) -> Any:
        """Convert a raw string to the specified type."""
        if value_type == "integer":
            try:
                return int(raw)
            except ValueError:
                return 0
        elif value_type == "float":
            try:
                return float(raw)
            except ValueError:
                return 0.0
        elif value_type == "boolean":
            return raw.lower() in ("1", "true", "yes", "on")
        return raw  # string


def create_configurable_driver_class(
    driver_def: dict[str, Any],
) -> type[ConfigurableDriver]:
    """
    Factory: create a ConfigurableDriver subclass from a JSON definition.

    Returns a new class with the correct DRIVER_INFO and _definition
    attributes, ready to be registered in the driver registry.
    """
    driver_id = driver_def.get("id", "unknown")

    # Build DRIVER_INFO from the definition
    driver_info: dict[str, Any] = {
        "id": driver_id,
        "name": driver_def.get("name", driver_id),
        "manufacturer": driver_def.get("manufacturer", "Generic"),
        "category": driver_def.get("category", "utility"),
        "version": driver_def.get("version", "1.0.0"),
        "author": driver_def.get("author", "Community"),
        "description": driver_def.get("description", ""),
        "transport": driver_def.get("transport", "tcp"),
        "default_config": driver_def.get("default_config", {}),
        "config_schema": driver_def.get("config_schema", {}),
        "state_variables": driver_def.get("state_variables", {}),
    }

    # Copy help from driver definition
    if "help" in driver_def:
        driver_info["help"] = driver_def["help"]

    # Copy protocol declarations from driver definition
    if "protocols" in driver_def:
        driver_info["protocols"] = driver_def["protocols"]

    # Copy discovery hints from driver definition
    if "discovery" in driver_def:
        driver_info["discovery"] = driver_def["discovery"]

    # Copy device_settings from driver definition
    if "device_settings" in driver_def:
        driver_info["device_settings"] = driver_def["device_settings"]

    # Copy help from each state variable
    state_vars = driver_info.get("state_variables", {})
    for var_name, var_def in state_vars.items():
        if isinstance(var_def, dict) and "help" in var_def:
            state_vars[var_name] = {**var_def}

    # Build commands metadata for DRIVER_INFO
    commands_meta: dict[str, Any] = {}
    for cmd_name, cmd_def in driver_def.get("commands", {}).items():
        cmd_meta: dict[str, Any] = {
            "label": cmd_def.get("label", cmd_name),
            "params": cmd_def.get("params", {}),
        }
        # Include HTTP-specific fields if present (for Driver Builder UI)
        if "method" in cmd_def:
            cmd_meta["method"] = cmd_def["method"]
        if "path" in cmd_def:
            cmd_meta["path"] = cmd_def["path"]
        if "body" in cmd_def:
            cmd_meta["body"] = cmd_def["body"]
        # Copy help from command definition
        if "help" in cmd_def:
            cmd_meta["help"] = cmd_def["help"]
        commands_meta[cmd_name] = cmd_meta
    driver_info["commands"] = commands_meta

    # Add delimiter if specified
    if "delimiter" in driver_def:
        driver_info["delimiter"] = driver_def["delimiter"]

    # Create a new class dynamically
    cls = type(
        f"ConfigurableDriver_{driver_id}",
        (ConfigurableDriver,),
        {
            "DRIVER_INFO": driver_info,
            "_definition": driver_def,
        },
    )

    return cls
