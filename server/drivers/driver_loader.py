"""
OpenAVC Driver Loader — scans for .avcdriver definition files and Python
driver modules, and registers them.

Supported formats:
    - .avcdriver  YAML definition files (loaded via ConfigurableDriver)
    - .py         Python modules containing BaseDriver subclasses

Directories scanned:
    - server/drivers/definitions/  (built-in .avcdriver definitions)
    - driver_repo/                 (community/user drivers — .avcdriver and .py)

Each valid driver is registered in the global driver registry.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import sys
import tempfile
from pathlib import Path
from typing import Sequence, Any

import yaml

from server.utils.logger import get_logger
from server.utils.regex_safety import regex_safety_error as _regex_redos_error

log = get_logger(__name__)

# OSC argument type tags the ConfigurableDriver runtime can encode from a YAML
# value. 'b' (blob/bytes) is intentionally excluded — there's no unambiguous way
# to express raw bytes in a YAML arg value, so it isn't a declarative type (the
# Driver Builder UI and avcdriver.schema.json omit it too). An unsupported tag
# is dropped silently at send time, yielding a malformed OSC message — catch it
# at load instead.
_OSC_ARG_TYPES = frozenset({"f", "i", "s", "h", "d", "T", "F", "N"})


def _validate_osc_args(where: str, arg_defs: Any, errors: list[str]) -> None:
    """Validate OSC arg `type` tags so an unsupported tag or typo fails at load
    rather than being silently dropped when the message is built."""
    if arg_defs is None:
        return
    if not isinstance(arg_defs, list):
        errors.append(f"{where}: args must be a list")
        return
    for j, arg in enumerate(arg_defs):
        if not isinstance(arg, dict):
            errors.append(f"{where}: args[{j}] must be a mapping")
            continue
        arg_type = arg.get("type", "f")
        if arg_type not in _OSC_ARG_TYPES:
            errors.append(
                f"{where}: args[{j}] unknown OSC type '{arg_type}' "
                f"(expected one of f/i/s/h/d/T/F/N)"
            )


# Sources a param's option list can cascade from (`options_from.source`).
_PARAM_OPTIONS_FROM_SOURCES = frozenset({"child_schema"})


def _validate_param_option_providers(
    where: str, params: Any, errors: list[str],
) -> None:
    """Validate the param-picker option/type providers (§69) on a param map:
    ``options_state`` / ``options_source`` (state-key lists), ``options_from``
    (cascade off a sibling param), and ``type_from`` (take the input type from a
    sibling cascade's chosen control). Also validates the Phase 3 free-text
    aids — a ``pattern`` regex (compiles + isn't ReDoS-prone) and sane
    ``min``/``max`` bounds. Authoring-time aids — the runtime still
    coerces/validates the submitted value — but a typo here silently leaves a
    free-text box, so flag it at load.
    """
    if not isinstance(params, dict):
        return
    for pname, pdef in params.items():
        if not isinstance(pdef, dict):
            continue

        # Phase 3: a free-text param can declare a `pattern` (shape check) and
        # numeric min/max. Validate them at load so a bad regex or inverted
        # range errors here, not at command time.
        pattern = pdef.get("pattern")
        if pattern is not None:
            err = _regex_redos_error(f"{where} param '{pname}': pattern", pattern)
            if err:
                errors.append(err)
        mn, mx = pdef.get("min"), pdef.get("max")
        for bound_name, bound in (("min", mn), ("max", mx)):
            if bound is not None and not isinstance(bound, (int, float)):
                errors.append(
                    f"{where} param '{pname}': {bound_name} must be a number"
                )
        if (
            isinstance(mn, (int, float))
            and isinstance(mx, (int, float))
            and mn > mx
        ):
            errors.append(
                f"{where} param '{pname}': min ({mn}) must be <= max ({mx})"
            )

        for key in ("options_state", "options_source"):
            val = pdef.get(key)
            if val is not None and not (isinstance(val, str) and val):
                errors.append(
                    f"{where} param '{pname}': {key} must be a non-empty string"
                )
        ofrom = pdef.get("options_from")
        if ofrom is not None:
            if not isinstance(ofrom, dict):
                errors.append(
                    f"{where} param '{pname}': options_from must be a mapping "
                    f"with 'param' and 'source'"
                )
            else:
                source = ofrom.get("source")
                if source not in _PARAM_OPTIONS_FROM_SOURCES:
                    errors.append(
                        f"{where} param '{pname}': options_from.source must be "
                        f"one of {sorted(_PARAM_OPTIONS_FROM_SOURCES)}"
                    )
                ref = ofrom.get("param")
                if not (isinstance(ref, str) and ref):
                    errors.append(
                        f"{where} param '{pname}': options_from.param must name "
                        f"a sibling param"
                    )
                elif ref not in params:
                    errors.append(
                        f"{where} param '{pname}': options_from.param '{ref}' is "
                        f"not a param of this command"
                    )
                elif source == "child_schema":
                    sibling = params.get(ref)
                    if isinstance(sibling, dict) \
                            and sibling.get("type") != "child_id":
                        errors.append(
                            f"{where} param '{pname}': options_from.param "
                            f"'{ref}' must be a child_id param for source "
                            f"'child_schema'"
                        )

        tfrom = pdef.get("type_from")
        if tfrom is not None:
            if not isinstance(tfrom, dict):
                errors.append(
                    f"{where} param '{pname}': type_from must be a mapping with "
                    f"'param'"
                )
                continue
            ref = tfrom.get("param")
            if not (isinstance(ref, str) and ref):
                errors.append(
                    f"{where} param '{pname}': type_from.param must name a "
                    f"sibling param"
                )
            elif ref not in params:
                errors.append(
                    f"{where} param '{pname}': type_from.param '{ref}' is not a "
                    f"param of this command"
                )
            else:
                # The named sibling must itself be a child_schema cascade — that
                # is how type_from finds the component + control to read the
                # type from.
                sib = params.get(ref)
                sib_from = sib.get("options_from") if isinstance(sib, dict) else None
                if not (isinstance(sib_from, dict)
                        and sib_from.get("source") == "child_schema"):
                    errors.append(
                        f"{where} param '{pname}': type_from.param '{ref}' must "
                        f"itself be an options_from child_schema cascade"
                    )


# Required top-level fields in a driver definition
REQUIRED_FIELDS = {"id", "name", "transport"}

# File extension for driver definitions
DRIVER_EXTENSION = ".avcdriver"

# Sibling companion files that live next to drivers but aren't drivers
# themselves. Discovery companions (`<id>_discovery.py`) expose
# ``async def probe(ctx)`` for the discovery engine; Python simulators
# (`<id>_sim.py`) expose a Simulator class for the device simulator.
# Neither has a ``DRIVER_INFO`` constant or a BaseDriver subclass, so
# the runtime loader silently skips them — but they would otherwise
# leak into the Code tab and the Installed Drivers panel as if they
# were standalone Python drivers. Filter them at the listing layer
# alongside underscore-prefixed files (which are conventional
# helpers / private modules).
_COMPANION_SUFFIXES: tuple[str, ...] = ("_discovery.py", "_sim.py")


def _is_driver_file(filepath: Path) -> bool:
    """Return False for companion / helper .py files that aren't drivers."""
    name = filepath.name
    if name.startswith("_"):
        return False
    if any(name.endswith(suf) for suf in _COMPANION_SUFFIXES):
        return False
    return True


def validate_driver_definition(driver_def: dict[str, Any]) -> list[str]:
    """
    Validate a driver definition.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []

    # A malformed driver file can yaml-parse to a non-mapping, or carry
    # non-mapping `responses`/`commands`/`state_variables` sections (e.g. a
    # YAML list where a map was expected). Those used to raise uncaught
    # AttributeError/TypeError here, aborting the whole driver-loading pass and
    # taking every other driver down with the one bad file. Validate the shape
    # of each section before iterating it so a bad file is reported and skipped.
    if not isinstance(driver_def, dict):
        return ["Driver definition must be a mapping"]

    for field in REQUIRED_FIELDS:
        if field not in driver_def:
            errors.append(f"Missing required field: {field}")

    transport = driver_def.get("transport", "")
    # "bridge" is the sentinel transport for a device that emits through a live
    # bridge instance (an IR device on an emitter port) rather than dialing a
    # host of its own — it opens no socket and routes commands via the bridge.
    if transport and transport not in (
        "tcp", "serial", "udp", "http", "osc", "bridge"
    ):
        errors.append(f"Unsupported transport: {transport}")

    # The IR code-set opt-in is a boolean flag (like inline_protocol): it turns
    # on the device-page IR Codes editor. The codes themselves live in the
    # device config / default_config ir_codes map, not here.
    ir_codes_flag = driver_def.get("ir_codes")
    if ir_codes_flag is not None and not isinstance(ir_codes_flag, bool):
        errors.append(
            "ir_codes: must be a boolean (the code-set lives in "
            "default_config.ir_codes / the device config)"
        )

    # Validate response patterns compile and don't have catastrophic backtracking
    responses = driver_def.get("responses", [])
    if not isinstance(responses, list):
        errors.append("responses: must be a list")
        responses = []
    for i, resp in enumerate(responses):
        if not isinstance(resp, dict):
            errors.append(f"Response {i}: must be a mapping")
            continue
        # OSC responses use "address" key — validate it starts with /
        if "address" in resp:
            addr = resp["address"]
            if not isinstance(addr, str) or not addr.startswith("/"):
                errors.append(f"Response {i}: OSC address must start with '/'")
            continue

        pattern = resp.get("pattern", "") or resp.get("match", "")
        if not pattern:
            errors.append(f"Response {i}: missing pattern, match, or address")
        else:
            err = _regex_redos_error(f"Response {i}", pattern)
            if err:
                errors.append(err)

    # Validate commands structure
    commands = driver_def.get("commands", {})
    if not isinstance(commands, dict):
        errors.append("commands: must be a mapping")
        commands = {}
    for cmd_name, cmd_def in commands.items():
        if not isinstance(cmd_def, dict):
            errors.append(f"Command '{cmd_name}': must be a dict")
            continue
        # TCP/serial commands need send/string, HTTP need path/method, OSC needs address
        has_send = cmd_def.get("send") or cmd_def.get("string")
        has_http = cmd_def.get("path") or cmd_def.get("method")
        has_osc = cmd_def.get("address") is not None
        if not has_send and not has_http and not has_osc:
            errors.append(
                f"Command '{cmd_name}': must have 'send' (TCP/serial), "
                f"'path'/'method' (HTTP), or 'address' (OSC)"
            )
        if has_osc:
            _validate_osc_args(f"Command '{cmd_name}'", cmd_def.get("args"), errors)
        _validate_param_option_providers(
            f"Command '{cmd_name}'", cmd_def.get("params"), errors,
        )

    # Device settings: each entry must be writable (a `write:` block — the
    # runtime raises NotImplementedError without one) and its state_key must
    # name a declared state variable. A typo'd state_key used to load fine
    # and just show "(not set)" forever while writes silently fired.
    declared_vars = driver_def.get("state_variables")
    declared_vars = declared_vars if isinstance(declared_vars, dict) else {}
    valid_setting_types = {"string", "integer", "number", "boolean", "enum", "float"}
    device_settings = driver_def.get("device_settings")
    if device_settings is not None and not isinstance(device_settings, dict):
        errors.append("device_settings: must be a mapping")
    elif isinstance(device_settings, dict):
        for setting_name, setting_def in device_settings.items():
            where = f"Device setting '{setting_name}'"
            if not isinstance(setting_def, dict):
                errors.append(f"{where}: must be a mapping")
                continue
            stype = setting_def.get("type", "")
            if stype and stype not in valid_setting_types:
                errors.append(f"{where}: unknown type '{stype}'")
            state_key = setting_def.get("state_key", setting_name)
            if state_key not in declared_vars:
                errors.append(
                    f"{where}: state_key '{state_key}' is not a declared "
                    f"state variable — the setting would never read back"
                )
            mn, mx = setting_def.get("min"), setting_def.get("max")
            if (
                isinstance(mn, (int, float)) and isinstance(mx, (int, float))
                and not isinstance(mn, bool) and not isinstance(mx, bool)
                and mn > mx
            ):
                errors.append(f"{where}: min ({mn}) is greater than max ({mx})")
            write = setting_def.get("write")
            if not isinstance(write, dict):
                errors.append(
                    f"{where}: missing 'write' block (send / path / address) — "
                    f"a device setting must be writable"
                )
            elif write.get("address") is not None:
                # OSC writes share the command arg encoder — validate their
                # arg types too so a bad tag fails at load, not write time.
                _validate_osc_args(f"{where} write", write.get("args"), errors)

    # Validate the Phase 6 ``discovery:`` block. Templates (generic_*)
    # are exempt — they don't participate in discovery. Phase 8 dropped
    # the strong-signal-required rule: a driver may declare any
    # combination of strong + soft signals, or none (load-time warning).
    # Signal collisions are caught later when the SignalIndex is built.
    driver_id = driver_def.get("id", "") or ""
    is_template = any(driver_id.startswith(p) for p in ("generic_",))
    if not is_template:
        from server.discovery.hints import DiscoveryHintError, parse_driver_discovery
        try:
            parse_driver_discovery(driver_def)
        except DiscoveryHintError as exc:
            errors.append(f"discovery: {exc}")

    # Validate the optional `auth:` login handshake block. The runtime swaps to
    # raw byte buffering and types credentials before any other traffic — so a
    # misdeclared block silently connects unauthenticated or mangles the
    # transport's data path instead of erroring. Enforce the requirements at
    # load time where the author can see them.
    auth_def = driver_def.get("auth")
    if auth_def is not None:
        if not isinstance(auth_def, dict):
            errors.append("auth: must be a mapping")
        else:
            auth_type = auth_def.get("type", "telnet_login")
            if auth_type != "telnet_login":
                errors.append(
                    f"auth: unsupported type '{auth_type}' (only 'telnet_login')"
                )
            # The handshake assumes a TCP/serial byte stream; on udp/http/osc the
            # frame-parser swap and raw buffering break the normal data path.
            if transport and transport not in ("tcp", "serial"):
                errors.append(
                    f"auth: login handshake is only supported on tcp/serial "
                    f"transports, not '{transport}'"
                )
            # Both prompts are required — without them the handshake silently
            # no-ops and the device connects unauthenticated.
            for required in ("username_prompt", "password_prompt"):
                if not auth_def.get(required):
                    errors.append(f"auth: missing required '{required}'")
            # The prompt/success/failure regexes run synchronously on raw
            # pre-auth device bytes, so they get the same ReDoS check as
            # response patterns.
            for key in (
                "username_prompt",
                "password_prompt",
                "success_pattern",
                "failure_pattern",
            ):
                pat = auth_def.get(key)
                if pat:
                    err = _regex_redos_error(f"auth.{key}", pat)
                    if err:
                        errors.append(err)

    # Validate the optional actions / quick_actions blocks (Quick Action strip).
    # quick_actions promote command ids to buttons; actions is the full form
    # (kind:"command" promotes a command, kind:"setup" is a provisioning wizard).
    from server.drivers.actions import validate_actions
    errors.extend(validate_actions(driver_def))
    # A YAML driver is interpreted by ConfigurableDriver, which has no
    # run_setup_action handler — so kind:"setup" can never do anything here.
    # Reject it at load time rather than render a button that errors on click.
    for i, entry in enumerate(driver_def.get("actions") or []):
        if isinstance(entry, dict) and entry.get("kind") == "setup":
            errors.append(
                f"actions[{i}]: kind 'setup' requires a Python driver "
                f"(a run_setup_action handler); YAML drivers support "
                f"kind 'command' only"
            )
        if isinstance(entry, dict) and isinstance(entry.get("params"), dict):
            _validate_param_option_providers(
                f"actions[{i}]", entry.get("params"), errors,
            )

    # Validate state_variables structure
    valid_types = {"string", "integer", "number", "boolean", "enum", "float"}
    state_variables = driver_def.get("state_variables", {})
    if not isinstance(state_variables, dict):
        errors.append("state_variables: must be a mapping")
        state_variables = {}
    for var_name, var_def in state_variables.items():
        if not isinstance(var_def, dict):
            errors.append(f"State variable '{var_name}': must be a dict")
            continue
        var_type = var_def.get("type", "")
        if var_type and var_type not in valid_types:
            errors.append(f"State variable '{var_name}': unknown type '{var_type}'")
        if not var_def.get("label"):
            errors.append(f"State variable '{var_name}': missing 'label'")

    # Validate the optional frame_parser block (binary protocols). The runtime
    # LengthPrefixFrameParser only accepts header_size in {1, 2, 4} and
    # FixedLengthFrameParser needs a positive length; an out-of-range value
    # (authored by hand or by an older Driver Builder) would otherwise raise
    # in connect() and wedge the device in a permanent reconnect loop. Surface
    # it at load instead, with a clear message.
    frame_parser = driver_def.get("frame_parser")
    if frame_parser is not None:
        if not isinstance(frame_parser, dict):
            errors.append("frame_parser: must be a mapping")
        else:
            fp_type = frame_parser.get("type", "")
            if fp_type == "length_prefix":
                header_size = frame_parser.get("header_size", 2)
                if header_size not in (1, 2, 4):
                    errors.append(
                        f"frame_parser: header_size must be 1, 2, or 4 (got {header_size!r})"
                    )
                offset = frame_parser.get("header_offset", 0)
                if isinstance(offset, bool) or not isinstance(offset, int):
                    errors.append(
                        f"frame_parser: header_offset must be an integer (got {offset!r})"
                    )
            elif fp_type == "fixed_length":
                length = frame_parser.get("length", 1)
                if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
                    errors.append(
                        f"frame_parser: length must be a positive integer (got {length!r})"
                    )
            elif fp_type:
                errors.append(
                    f"frame_parser: unknown type '{fp_type}' "
                    f"(expected 'length_prefix' or 'fixed_length')"
                )
            else:
                errors.append("frame_parser: missing 'type'")

    # Validate child_entity_types keys. The child type name becomes a key
    # segment — device.<id>.<child_type>.<local_id>.<prop> — and feeds
    # subscribe_children's "device.<id>.<child_type>.*" pattern. A dot would
    # corrupt the key structure; a glob metachar (* ? [) breaks the fnmatch
    # dispatch the platform uses to route per-child state changes.
    child_types = driver_def.get("child_entity_types", {})
    if child_types and not isinstance(child_types, dict):
        errors.append("child_entity_types: must be a mapping")
    elif isinstance(child_types, dict):
        for child_type, type_def in child_types.items():
            if not isinstance(child_type, str) or not child_type:
                errors.append(f"child_entity_types: type name {child_type!r} must be a non-empty string")
                continue
            if "." in child_type:
                errors.append(
                    f"child_entity_types: type name '{child_type}' must not contain dots (used as state key separator)"
                )
            bad = [c for c in "*?[" if c in child_type]
            if bad:
                errors.append(
                    f"child_entity_types: type name '{child_type}' must not contain "
                    f"glob metacharacters ({', '.join(bad)}) — they break state-change dispatch"
                )
            # Deep-validate the schema so a malformed declaration fails at
            # load with a clear message — not at connect() (a bad id_format
            # used to surface as a confusing device-offline) or silently
            # (an invalid cloud_priority just fell to the default tier).
            where = f"child_entity_types.{child_type}"
            if not isinstance(type_def, dict):
                errors.append(f"{where}: must be a mapping")
                continue
            id_format = type_def.get("id_format")
            if id_format is not None:
                if not isinstance(id_format, dict):
                    errors.append(f"{where}.id_format: must be a mapping")
                else:
                    id_type = id_format.get("type", "integer")
                    if id_type not in ("integer", "string"):
                        errors.append(
                            f"{where}.id_format: unknown type '{id_type}' "
                            f"(expected 'integer' or 'string')"
                        )
                    mn, mx = id_format.get("min"), id_format.get("max")
                    for label, val in (("min", mn), ("max", mx)):
                        if val is not None and (
                            isinstance(val, bool) or not isinstance(val, int)
                        ):
                            errors.append(
                                f"{where}.id_format: {label} must be an integer "
                                f"(got {val!r})"
                            )
                    if isinstance(mn, int) and isinstance(mx, int) and mn > mx:
                        errors.append(
                            f"{where}.id_format: min ({mn}) is greater than max ({mx})"
                        )
                    pad = id_format.get("pad_width")
                    if pad is not None and (
                        isinstance(pad, bool) or not isinstance(pad, int) or pad < 1
                    ):
                        errors.append(
                            f"{where}.id_format: pad_width must be a positive "
                            f"integer (got {pad!r})"
                        )
            child_vars = type_def.get("state_variables")
            if child_vars is not None and not isinstance(child_vars, dict):
                errors.append(f"{where}.state_variables: must be a mapping")
            elif isinstance(child_vars, dict):
                for var_name, var_def in child_vars.items():
                    if not isinstance(var_def, dict):
                        errors.append(
                            f"{where}.state_variables.{var_name}: must be a mapping"
                        )
                        continue
                    vt = var_def.get("type", "")
                    if vt and vt not in valid_types:
                        errors.append(
                            f"{where}.state_variables.{var_name}: unknown type '{vt}'"
                        )
                    cp = var_def.get("cloud_priority")
                    if cp is not None and cp not in ("low", "high"):
                        errors.append(
                            f"{where}.state_variables.{var_name}: cloud_priority "
                            f"must be 'low' or 'high' (got {cp!r}); omit it for "
                            f"the default cadence"
                        )

    return errors


def companion_relpath_from_def(driver_def: dict[str, Any]) -> str | None:
    """Return the relative ``discovery.python.file`` path if declared.

    Used by ``load_driver_file`` and by the ``/drivers/upload`` REST
    route to spot YAMLs that declare a Python companion before
    accepting them. Returns ``None`` when no ``python:`` declaration is
    present (any other discovery fingerprint type stands alone).
    """
    discovery = driver_def.get("discovery") or {}
    if not isinstance(discovery, dict):
        return None
    block = discovery.get("python")
    if isinstance(block, str):
        return block or None
    if isinstance(block, dict):
        path = block.get("file")
        if isinstance(path, str) and path:
            return path
    return None


def load_driver_file(filepath: Path) -> dict[str, Any] | None:
    """
    Load and validate a single driver definition file (.avcdriver YAML).

    Returns the driver definition dict, or None if invalid.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
        driver_def = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as e:
        log.warning(f"Failed to load driver file {filepath}: {e}")
        return None

    if not isinstance(driver_def, dict):
        log.warning(f"Driver file {filepath} is not a valid YAML mapping")
        return None

    errors = validate_driver_definition(driver_def)
    if errors:
        log.warning(
            f"Invalid driver definition in {filepath}: "
            + "; ".join(errors)
        )
        return None

    # Companion existence check: a ``python:`` declaration that points at
    # a missing file would auto-register two SignalRules under
    # ``custom_<id>_companion_(udp|tcp)`` at hint-load time, but no
    # evidence producer would ever fire — the device would be matchable
    # in theory and silently invisible in practice. Reject up front.
    companion_relpath = companion_relpath_from_def(driver_def)
    if companion_relpath:
        companion_path = (filepath.parent / companion_relpath).resolve()
        if not companion_path.is_file():
            log.warning(
                f"Driver {filepath.name} declares discovery.python "
                f"file={companion_relpath!r} but no such file exists "
                f"at {companion_path}; skipping driver"
            )
            return None

    return driver_def


def _python_driver_id(filepath: Path) -> str | None:
    """Extract ``DRIVER_INFO['id']`` from a .py driver via AST (no import)."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    # DRIVER_INFO may be a module-level assignment or a class attribute;
    # ast.walk covers both. First declared id wins.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "DRIVER_INFO" for t in node.targets
        ):
            continue
        if isinstance(node.value, ast.Dict):
            for k, v in zip(node.value.keys, node.value.values):
                if (
                    isinstance(k, ast.Constant) and k.value == "id"
                    and isinstance(v, ast.Constant) and isinstance(v.value, str)
                ):
                    return v.value or None
    return None


def driver_id_from_file(filepath: Path) -> str | None:
    """Return the driver id a file *declares*, without importing it.

    Drivers are registered under their declared id, which may differ from the
    filename stem (uploads keep their original filename — routes/drivers.py
    does not rename). Resolving a repo file by stem alone therefore misses
    such drivers; callers that map an id back to its file (export bundling,
    builtin/community source classification) use this instead.

    - ``.py``: the ``id`` key of the module/class ``DRIVER_INFO`` dict, read
      via ``ast`` so the module is never executed.
    - everything else (``.avcdriver`` YAML): the top-level ``id`` field.

    Returns the declared id, or None if it can't be determined.
    """
    try:
        if filepath.suffix == ".py":
            return _python_driver_id(filepath)
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            did = data.get("id")
            return did if isinstance(did, str) and did else None
    except (OSError, yaml.YAMLError, SyntaxError, ValueError) as e:
        log.debug(f"Could not read driver id from {filepath}: {e}")
    return None


def find_driver_file_by_id(
    directories: Sequence[Path | str], driver_id: str
) -> Path | None:
    """Find the ``.avcdriver`` file on disk that declares ``driver_id``.

    Scans the given directories (first match wins, so earlier dirs take
    precedence) for a YAML driver whose declared id matches. Matches on the
    declared id, not the filename stem, since uploads keep their original
    name (see ``driver_id_from_file``). Returns the path, or None if no
    ``.avcdriver`` declares that id. (Python ``.py`` drivers are out of
    scope — they have their own reload path keyed by filename.)
    """
    for directory in directories:
        dir_path = Path(directory)
        if not dir_path.is_dir():
            continue
        for filepath in sorted(dir_path.glob(f"*{DRIVER_EXTENSION}")):
            if driver_id_from_file(filepath) == driver_id:
                return filepath
    return None


def load_driver_files(directories: Sequence[Path | str]) -> int:
    """
    Scan directories for .avcdriver files, validate them,
    create ConfigurableDriver subclasses, and register them.

    Returns the number of drivers successfully loaded.
    """
    from server.core.device_manager import register_driver
    from server.drivers.configurable import create_configurable_driver_class

    count = 0
    seen_ids: set[str] = set()
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob(f"*{DRIVER_EXTENSION}")):
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                continue

            driver_id = driver_def.get("id", "")
            if driver_id in seen_ids:
                log.warning(f"Duplicate driver ID '{driver_id}' in {filepath.name} — skipping")
                continue
            seen_ids.add(driver_id)
            try:
                driver_class = create_configurable_driver_class(driver_def)
                register_driver(driver_class)
                count += 1
                log.info(f"Loaded driver: {driver_id} from {filepath.name}")
            except Exception:  # Catch-all: YAML parsing/validation can fail in many ways
                log.exception(f"Failed to create driver class from {filepath}")

    return count


def load_python_driver_file(filepath: Path) -> type | None:
    """
    Load a Python driver module from a .py file and return the BaseDriver subclass.

    Uses importlib to dynamically load the module, then scans it for classes
    that are subclasses of BaseDriver (but not BaseDriver itself).

    Returns the driver class, or None if no valid driver was found.
    """
    from server.drivers.base import BaseDriver

    module_name = f"openavc_driver_{filepath.stem}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec is None or spec.loader is None:
            log.warning(f"Could not create module spec for {filepath}")
            return None

        module = importlib.util.module_from_spec(spec)
        # Add to sys.modules so relative imports within the driver work
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception:  # Catch-all: exec_module runs arbitrary driver code
        log.exception(f"Failed to load Python driver from {filepath}")
        # Drop the half-initialized module: leaving it resident for the process
        # lifetime leaks state, defeats the "module not loaded" health check in
        # list_python_drivers (so the panel shows no load error), and makes a
        # later hot-reload see an inconsistent sys.modules.
        sys.modules.pop(module_name, None)
        return None

    # Find BaseDriver subclasses defined in this module
    driver_class = None
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(obj, BaseDriver)
            and obj is not BaseDriver
            and obj.__module__ == module_name
        ):
            if hasattr(obj, "DRIVER_INFO") and obj.DRIVER_INFO.get("id"):
                driver_class = obj
                break  # Take the first valid one

    if driver_class is None:
        log.warning(f"No BaseDriver subclass with DRIVER_INFO found in {filepath}")
    else:
        _warn_python_driver_info_issues(driver_class)

    return driver_class


def _warn_python_driver_info_issues(driver_class: type) -> None:
    """Structural sanity warnings for a Python driver's DRIVER_INFO.

    Warn-only, never rejects: Python drivers may populate ``commands`` /
    state at runtime (the Q-SYS pattern), so cross-references against the
    class-level dict can false-positive — but STRUCTURE is static, and a
    malformed entry used to be silently skipped by the action resolver (the
    button just never appears) or fail at first write. YAML drivers get the
    equivalent as hard load errors via validate_driver_definition.
    """
    from server.drivers.base import BaseDriver

    info = getattr(driver_class, "DRIVER_INFO", {}) or {}
    driver_id = info.get("id", driver_class.__name__)
    issues: list[str] = []

    qa = info.get("quick_actions")
    if qa is not None and (
        not isinstance(qa, list) or any(not isinstance(x, str) for x in qa)
    ):
        issues.append("quick_actions must be a list of command-id strings")

    actions = info.get("actions")
    declares_setup = False
    if actions is not None and not isinstance(actions, list):
        issues.append("actions must be a list")
    elif isinstance(actions, list):
        for i, entry in enumerate(actions):
            if not isinstance(entry, dict) or not entry.get("id"):
                issues.append(
                    f"actions[{i}] must be a mapping with an 'id' "
                    f"(the resolver silently drops it otherwise)"
                )
                continue
            kind = entry.get("kind", "command")
            if kind not in ("command", "setup"):
                issues.append(
                    f"actions[{i}] ('{entry.get('id')}'): unknown kind {kind!r}"
                )
            elif kind == "setup":
                declares_setup = True
            availability = entry.get("availability", "online")
            if availability not in ("online", "offline", "always"):
                issues.append(
                    f"actions[{i}] ('{entry.get('id')}'): unknown availability "
                    f"{availability!r}"
                )
    if declares_setup and driver_class.run_setup_action is BaseDriver.run_setup_action:
        issues.append(
            "declares a kind:'setup' action but does not override "
            "run_setup_action — the wizard will 501 on launch"
        )

    settings = info.get("device_settings")
    if settings is not None and not isinstance(settings, dict):
        issues.append("device_settings must be a mapping")
    elif isinstance(settings, dict):
        for key, sdef in settings.items():
            if not isinstance(sdef, dict):
                issues.append(f"device_settings['{key}'] must be a mapping")
        if settings and (
            driver_class.set_device_setting is BaseDriver.set_device_setting
        ):
            issues.append(
                "declares device_settings but does not override "
                "set_device_setting — every write will 501"
            )

    for msg in issues:
        log.warning(f"Python driver '{driver_id}': {msg}")


def load_python_drivers(directories: Sequence[Path | str]) -> int:
    """
    Scan directories for .py driver files, load them, and register.

    Returns the number of drivers successfully loaded.
    """
    from server.core.device_manager import register_driver

    count = 0
    seen_ids: set[str] = set()
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob("*.py")):
            if not _is_driver_file(filepath):
                continue

            driver_class = load_python_driver_file(filepath)
            if driver_class is None:
                continue

            driver_id = driver_class.DRIVER_INFO.get("id", "")
            if driver_id in seen_ids:
                log.warning(f"Duplicate Python driver ID '{driver_id}' in {filepath.name} — skipping")
                continue
            seen_ids.add(driver_id)
            try:
                register_driver(driver_class)
                count += 1
                log.info(f"Loaded Python driver: {driver_id} from {filepath.name}")
            except Exception:
                log.exception(f"Failed to register Python driver from {filepath}")

    return count


def load_all_drivers(directories: Sequence[Path | str]) -> int:
    """
    Load both .avcdriver YAML definitions and .py Python drivers from
    the given directories. This is the main entry point for loading all
    driver types in one pass.

    Returns the total number of drivers successfully loaded.
    """
    count = 0
    count += load_driver_files(directories)
    count += load_python_drivers(directories)
    return count


def save_driver_definition(
    driver_def: dict[str, Any],
    directory: Path | str,
) -> Path:
    """
    Save a driver definition as a .avcdriver YAML file.

    The filename is derived from the driver's id field.
    Returns the path to the saved file.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    driver_id = driver_def.get("id", "unknown")
    # Sanitize filename
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in driver_id)
    filepath = directory / f"{safe_id}{DRIVER_EXTENSION}"

    text = yaml.dump(
        driver_def,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    fd, tmp = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, str(filepath))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    log.info(f"Saved driver definition: {filepath}")
    return filepath


def _is_within(path: Path, root: Path) -> bool:
    """True if ``path`` resolves to a location inside ``root``."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def is_builtin_definition_path(filepath: Path) -> bool:
    """True if ``filepath`` lives in the read-only built-in definitions tree.

    The built-in ``.avcdriver`` files ship inside ``APP_DIR`` (the install
    tree on an installed/frozen deployment). They must never be unlinked or
    overwritten by an API call — there is no recovery short of reinstalling.
    """
    from server.system_config import DRIVER_DEFINITIONS_DIR

    return _is_within(filepath, DRIVER_DEFINITIONS_DIR)


def is_builtin_driver(
    driver_id: str,
    directories: Sequence[Path | str],
) -> bool:
    """True if ``driver_id`` is served by a read-only built-in with no override.

    A user copy in ``driver_repo`` with the same id (which the Driver Builder
    never creates — "Customize a copy" forks to a new ``<id>_copy``) takes
    precedence and is freely editable, so we only treat an id as a protected
    built-in when its only on-disk file is under the definitions tree.
    """
    builtin_match = False
    user_match = False
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue
        for filepath in dir_path.glob(f"*{DRIVER_EXTENSION}"):
            try:
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not (isinstance(data, dict) and data.get("id") == driver_id):
                continue
            if is_builtin_definition_path(filepath):
                builtin_match = True
            else:
                user_match = True
    return builtin_match and not user_match


def delete_driver_definition(
    driver_id: str,
    directories: Sequence[Path | str],
) -> bool:
    """
    Delete a driver definition file by driver ID.

    Searches all provided directories. Returns True if a file was deleted.

    Never unlinks a shipped built-in (a file under the read-only definitions
    tree): a single API call with a built-in id would otherwise permanently
    remove a platform driver from the install tree with no recovery. A
    same-id user copy in ``driver_repo`` is still deleted.
    """
    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue
        for filepath in dir_path.glob(f"*{DRIVER_EXTENSION}"):
            try:
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id") == driver_id:
                    if is_builtin_definition_path(filepath):
                        log.warning(
                            f"Refusing to delete built-in driver definition: {filepath}"
                        )
                        continue
                    filepath.unlink()
                    log.info(f"Deleted driver definition: {filepath}")
                    return True
            except (OSError, yaml.YAMLError):
                continue
    return False


def list_driver_definitions(directories: Sequence[Path | str]) -> list[dict[str, Any]]:
    """
    List all driver definitions from the given directories.

    Returns a list of driver definition dicts.
    """
    definitions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob(f"*{DRIVER_EXTENSION}")):
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                continue
            driver_id = driver_def.get("id", "")
            if driver_id in seen_ids:
                continue
            seen_ids.add(driver_id)
            # Add source info
            driver_def["_source_file"] = str(filepath)
            definitions.append(driver_def)

    return definitions


def list_python_drivers(directories: Sequence[Path | str]) -> list[dict[str, Any]]:
    """
    List all Python driver files (.py) from the given directories.

    Returns metadata for each file without doing a full import — uses AST
    parsing to extract DRIVER_INFO safely.
    """
    import ast

    from server.core.device_manager import is_driver_registered

    drivers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for dir_path in directories:
        dir_path = Path(dir_path)
        if not dir_path.exists():
            continue

        for filepath in sorted(dir_path.glob("*.py")):
            if not _is_driver_file(filepath):
                continue

            entry: dict[str, Any] = {
                "id": filepath.stem,
                "filename": filepath.name,
                "name": filepath.stem,
                "manufacturer": "",
                "category": "",
                "loaded": False,
                "load_error": None,
                "devices_using": [],
            }

            # Try AST extraction for DRIVER_INFO metadata
            try:
                source = filepath.read_text(encoding="utf-8")
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        for item in node.body:
                            if (
                                isinstance(item, ast.Assign)
                                and len(item.targets) == 1
                                and isinstance(item.targets[0], ast.Name)
                                and item.targets[0].id == "DRIVER_INFO"
                                and isinstance(item.value, ast.Dict)
                            ):
                                info = _ast_dict_to_simple(item.value)
                                if info.get("id"):
                                    entry["id"] = info["id"]
                                if info.get("name"):
                                    entry["name"] = info["name"]
                                if info.get("manufacturer"):
                                    entry["manufacturer"] = info["manufacturer"]
                                if info.get("category"):
                                    entry["category"] = info["category"]
                                break
                        break  # Only check first class
            except Exception:
                pass  # Fall back to filename-based defaults

            driver_id = entry["id"]
            if driver_id in seen_ids:
                continue
            seen_ids.add(driver_id)

            # Check if loaded in registry
            if is_driver_registered(driver_id):
                entry["loaded"] = True
            else:
                # Not registered under this file's id. Distinguish the two
                # failure modes so the Code tab / Installed Drivers panel can
                # tell the integrator WHY the driver isn't usable instead of
                # showing it as cleanly loaded with no error:
                #   - module present in sys.modules but not registered under
                #     this id → it imported but registration was rejected
                #     (duplicate driver id) or a last hot-reload left a stale
                #     class registered under a different id.
                #   - module absent → it never loaded (failed import, or the
                #     startup scan hasn't run for this file).
                module_name = f"openavc_driver_{filepath.stem}"
                if module_name in sys.modules:
                    entry["load_error"] = (
                        "Imported but not registered — duplicate driver ID "
                        "or a failed last reload"
                    )
                else:
                    entry["load_error"] = "Not loaded"

            drivers.append(entry)

    return drivers


def _ast_dict_to_simple(node: Any) -> dict[str, str | int | float | bool]:
    """Extract simple key-value pairs from an AST Dict node."""
    import ast

    result: dict[str, str | int | float | bool] = {}
    if not isinstance(node, ast.Dict):
        return result
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if isinstance(value, ast.Constant) and isinstance(value.value, (str, int, float, bool)):
                result[key.value] = value.value
    return result


def reload_python_driver(
    filepath: Path,
) -> dict[str, Any]:
    """
    Hot-reload a Python driver from disk.

    Safety: validates the new code by importing into a temporary module first.
    If the new code fails to import, the old driver stays active.

    Returns a dict with status, driver_id, and any errors.
    Does NOT handle device reconnection — that's the caller's responsibility.
    """
    from server.core.device_manager import register_driver, unregister_driver
    from server.drivers.base import BaseDriver

    stem = filepath.stem
    module_name = f"openavc_driver_{stem}"
    temp_module_name = f"_openavc_driver_validate_{stem}"

    # --- Step 1: Validate new code by importing into a temp module ---
    new_driver_class = None
    try:
        spec = importlib.util.spec_from_file_location(temp_module_name, filepath)
        if spec is None or spec.loader is None:
            return {"status": "error", "error": f"Could not create module spec for {filepath}"}

        temp_module = importlib.util.module_from_spec(spec)
        sys.modules[temp_module_name] = temp_module
        spec.loader.exec_module(temp_module)

        # Find BaseDriver subclass
        for _name, obj in inspect.getmembers(temp_module, inspect.isclass):
            if (
                issubclass(obj, BaseDriver)
                and obj is not BaseDriver
                and obj.__module__ == temp_module_name
            ):
                if hasattr(obj, "DRIVER_INFO") and obj.DRIVER_INFO.get("id"):
                    new_driver_class = obj
                    break
    except SyntaxError as e:
        return {
            "status": "error",
            "error": f"SyntaxError: {e.msg} ({filepath.name}, line {e.lineno})",
            "line": e.lineno,
            "old_driver_preserved": True,
        }
    except Exception as e:
        # Try to extract line number from traceback
        import traceback
        tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
        line_num = None
        for tb_line in tb_lines:
            import re as _re
            match = _re.search(r'line (\d+)', tb_line)
            if match and str(filepath) in tb_line:
                line_num = int(match.group(1))
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "line": line_num,
            "old_driver_preserved": True,
        }
    finally:
        # Clean up temp module
        sys.modules.pop(temp_module_name, None)

    if new_driver_class is None:
        return {
            "status": "error",
            "error": "No BaseDriver subclass with DRIVER_INFO found",
            "old_driver_preserved": True,
        }

    new_driver_id = new_driver_class.DRIVER_INFO["id"]

    # --- Step 2: Find old driver ID from this file (may differ if ID changed) ---
    old_driver_id = None
    if module_name in sys.modules:
        old_module = sys.modules[module_name]
        for _name, obj in inspect.getmembers(old_module, inspect.isclass):
            if (
                issubclass(obj, BaseDriver)
                and obj is not BaseDriver
                and obj.__module__ == module_name
            ):
                if hasattr(obj, "DRIVER_INFO") and obj.DRIVER_INFO.get("id"):
                    old_driver_id = obj.DRIVER_INFO["id"]
                    break

    # --- Step 3: Remove old module and load properly ---
    # Keep a handle on the old module so we can restore it if the canonical
    # re-import fails after Step-1 validation already passed (a TOCTOU edit /
    # delete of the file between validation and here, or an environment error).
    # Without the restore, sys.modules would be left empty while the old class
    # stays registered — registry and sys.modules disagreeing, with no repair.
    old_module = sys.modules.get(module_name)
    sys.modules.pop(module_name, None)

    final_class = load_python_driver_file(filepath)
    if final_class is None:
        # Reload failed after validation passed. The old class is still
        # registered (Step 4 hasn't run), so restore its module to keep
        # sys.modules consistent and report that it is still serving devices —
        # matching the old_driver_preserved contract of the Step-1 error paths.
        if old_module is not None:
            sys.modules[module_name] = old_module
        return {
            "status": "error",
            "error": (
                "Failed to reload driver after validation passed; the "
                "previously loaded driver is still active"
            ),
            "old_driver_preserved": True,
        }

    # --- Step 4: Unregister old and register new ---
    if old_driver_id and old_driver_id != new_driver_id:
        unregister_driver(old_driver_id)
    register_driver(final_class)

    log.info(f"Hot-reloaded Python driver: {new_driver_id} from {filepath.name}")

    return {
        "status": "reloaded",
        "driver_id": new_driver_id,
        "old_driver_id": old_driver_id,
    }


# --- Backward compatibility aliases ---
# These map old names to new names so existing code doesn't break during transition
load_json_driver = load_driver_file
load_json_drivers = load_driver_files
