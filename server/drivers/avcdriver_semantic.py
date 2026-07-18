"""Driver-definition validation rules (the .avcdriver contract).

Every cross-field rule that decides whether a driver definition is valid
lives in this one module. The runtime loader wraps it (adding the
discovery-block check, which needs the discovery engine's parser), and
the community driver catalog runs a copy of the same rules in its CI —
so a definition that passes catalog review is exactly a definition the
platform will load.

Purity contract: standard library plus the sibling ``spec`` constants
table and the stdlib-only regex-safety helper. Nothing here may import
the server runtime, transports, or discovery machinery; checks that need
them are passed in (see ``validate_driver_definition``'s
``discovery_validator`` hook). An import-guard test enforces this.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from server.drivers.spec import (
    ACTION_KINDS,
    AUTH_TRANSPORTS,
    AUTH_TYPES,
    AVAILABILITIES,
    CHILD_ID_TYPES,
    CLOUD_PRIORITIES,
    CONFIG_FIELD_SOURCES,
    GENERIC_ID_PREFIXES,
    INSTANCE_SOURCES,
    LENGTH_ENDIANS,
    LENGTH_HEADER_SIZES,
    LIVENESS_TRANSPORTS,
    OSC_ARG_TYPES as _OSC_ARG_TYPES,
    PARAM_OPTIONS_FROM_SOURCES as _PARAM_OPTIONS_FROM_SOURCES,
    PUSH_FRAME_PARSER_TYPES,
    PUSH_TYPE_KEYS,
    REQUIRED_FIELDS,
    SEND_FRAME_TYPES,
    STRUCT_LENGTH_SIZES,
    VALUE_TYPES,
    VISIBLE_WHEN_OPERATORS as _VISIBLE_WHEN_OPERATORS,
    YAML_TRANSPORTS,
    is_multicast_group,
)
from server.utils.regex_safety import regex_safety_error as _regex_redos_error

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

        # A `decimals` rounding rule (number params) must be a non-negative int.
        decimals = pdef.get("decimals")
        if decimals is not None and (
            not isinstance(decimals, int) or isinstance(decimals, bool) or decimals < 0
        ):
            errors.append(
                f"{where} param '{pname}': decimals must be a non-negative integer"
            )

        # `trim: false` opts a string param out of the runtime whitespace
        # trim (raw passthrough payloads where a terminator is meaningful).
        trim = pdef.get("trim")
        if trim is not None and not isinstance(trim, bool):
            errors.append(f"{where} param '{pname}': trim must be true or false")

        # A wire-value `map` translates the validated value before it is
        # substituted into the send template (0-based channel numbers,
        # letter codes). Keys/values must be scalars; a typo'd shape would
        # silently never translate.
        value_map = pdef.get("map")
        if value_map is not None:
            if not isinstance(value_map, dict) or not value_map:
                errors.append(
                    f"{where} param '{pname}': map must be a non-empty "
                    f"mapping of value -> wire value"
                )
            else:
                for mk, mv in value_map.items():
                    if isinstance(mk, bool) or isinstance(mv, bool) or not (
                        isinstance(mk, (str, int, float))
                        and isinstance(mv, (str, int, float))
                    ):
                        errors.append(
                            f"{where} param '{pname}': map entries must be "
                            f"scalar value -> wire value pairs"
                        )
                        break

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


def validate_driver_definition(
    driver_def: dict[str, Any],
    *,
    discovery_validator: Callable[[dict[str, Any]], list[str]] | None = None,
) -> list[str]:
    """
    Validate a driver definition.

    Returns a list of error strings. Empty list means valid.

    ``discovery_validator`` is the hook for the deep discovery-block check:
    the runtime loader passes the discovery engine's parser through it, and
    it runs exactly where the check always ran so error order is unchanged.
    When None, the discovery block is not validated.
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
    if transport and transport not in YAML_TRANSPORTS:
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

    # Raw maps used by child_set / instances / each_child validation below.
    _raw_child_types = driver_def.get("child_entity_types")
    child_types_map: dict[str, Any] = (
        _raw_child_types if isinstance(_raw_child_types, dict) else {}
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
        # Optional per-rule throttle (any response kind): positive seconds.
        # A zero/negative/non-numeric value would silently disable the rule
        # or the throttle depending on the runtime's mood — reject it here.
        throttle = resp.get("throttle")
        if throttle is not None and (
            isinstance(throttle, bool)
            or not isinstance(throttle, (int, float))
            or throttle <= 0
        ):
            errors.append(
                f"Response {i}: throttle must be a positive number of seconds"
            )
        # Optional `require:` scope on json rules — a misdeclared value would
        # silently disable the rule (never matches) or leave it unscoped.
        require = resp.get("require")
        if require is not None:
            if not resp.get("json"):
                errors.append(
                    f"Response {i}: require only applies to json: true "
                    f"responses"
                )
            if isinstance(require, str):
                if not require.strip():
                    errors.append(
                        f"Response {i}: require must name a JSON key"
                    )
            elif isinstance(require, list):
                if not require or not all(
                    isinstance(k, str) and k.strip() for k in require
                ):
                    errors.append(
                        f"Response {i}: require list entries must be "
                        f"non-empty JSON key names"
                    )
            else:
                errors.append(
                    f"Response {i}: require must be a JSON key name or a "
                    f"list of them"
                )
        # OSC responses use "address" key — validate it starts with /
        if "address" in resp:
            addr = resp["address"]
            if not isinstance(addr, str) or not addr.startswith("/"):
                errors.append(f"Response {i}: OSC address must start with '/'")
            # child_set on an OSC rule routes by address segment + positional
            # args (OSC has no capture groups). A misdeclared entry would
            # silently never write child state, so enforce the shape here.
            osc_child_set = resp.get("child_set")
            if osc_child_set is not None:
                if not isinstance(osc_child_set, list) or not osc_child_set:
                    errors.append(
                        f"Response {i}: child_set must be a non-empty list"
                    )
                    continue
                # Best-effort segment bound: fnmatch '*' can in theory span
                # '/', but no real OSC pattern does — the pattern's own
                # segment count is the practical upper bound.
                nsegs = (
                    len(addr.strip("/").split("/"))
                    if isinstance(addr, str) and addr.strip("/")
                    else None
                )
                for j, entry in enumerate(osc_child_set):
                    where = f"Response {i}: child_set[{j}]"
                    if not isinstance(entry, dict):
                        errors.append(f"{where}: must be a mapping")
                        continue
                    ctype = entry.get("type")
                    if not isinstance(ctype, str) or ctype not in child_types_map:
                        errors.append(
                            f"{where}: type {ctype!r} is not a declared "
                            f"child_entity_type"
                        )
                        continue
                    tdef = child_types_map.get(ctype)
                    tdef = tdef if isinstance(tdef, dict) else {}
                    cvars = tdef.get("state_variables")
                    cvars = cvars if isinstance(cvars, dict) else {}
                    id_fmt = tdef.get("id_format")
                    id_fmt = id_fmt if isinstance(id_fmt, dict) else {}
                    id_type = id_fmt.get("type", "integer")
                    cid = entry.get("id")
                    if cid is None:
                        errors.append(
                            f"{where}: missing 'id' ({{segment: N}} for an "
                            f"address segment, or a literal)"
                        )
                    elif isinstance(cid, dict):
                        seg = cid.get("segment")
                        if isinstance(seg, bool) or not isinstance(seg, int):
                            errors.append(
                                f"{where}: id needs an integer 'segment' "
                                f"(0-based index into the /-split address; "
                                f"OSC rules have no capture groups)"
                            )
                        elif seg < 0:
                            errors.append(
                                f"{where}: segment must be 0 or higher"
                            )
                        elif nsegs is not None and seg >= nsegs:
                            errors.append(
                                f"{where}: segment {seg} is past the end of "
                                f"the address pattern ({nsegs} segment(s))"
                            )
                        id_map = cid.get("map")
                        if id_map is not None:
                            if not isinstance(id_map, dict) or not id_map:
                                errors.append(
                                    f"{where}: id map must be a non-empty "
                                    f"mapping of wire id -> local child id"
                                )
                            else:
                                for mk, mv in id_map.items():
                                    if isinstance(mv, bool) or not isinstance(
                                        mk, (str, int)
                                    ) or not isinstance(mv, (str, int)):
                                        errors.append(
                                            f"{where}: id map entries must be "
                                            f"scalar wire id -> local id pairs"
                                        )
                                        break
                                    if id_type == "integer":
                                        try:
                                            int(str(mv).strip())
                                        except ValueError:
                                            errors.append(
                                                f"{where}: id map value "
                                                f"{mv!r} is not an integer "
                                                f"({ctype} declares integer "
                                                f"ids)"
                                            )
                                            break
                    elif isinstance(cid, str) and cid.startswith("$"):
                        errors.append(
                            f"{where}: OSC rules have no capture groups — "
                            f"use {{segment: N}} for the id, {{arg: N}} for "
                            f"values"
                        )
                    state_map = entry.get("state")
                    if not isinstance(state_map, dict) or not state_map:
                        errors.append(
                            f"{where}: missing 'state' mapping "
                            f"(prop -> {{arg: N}} or literal)"
                        )
                        continue
                    for prop, expr in state_map.items():
                        if prop not in cvars:
                            errors.append(
                                f"{where}: state prop '{prop}' is not "
                                f"declared in child_entity_types.{ctype}."
                                f"state_variables"
                            )
                        if isinstance(expr, str) and expr.startswith("$"):
                            errors.append(
                                f"{where}: state '{prop}' — OSC rules have "
                                f"no capture groups; use {{arg: N}}"
                            )
                        elif isinstance(expr, dict):
                            arg_ref = expr.get("arg")
                            if arg_ref is None and "value" not in expr:
                                errors.append(
                                    f"{where}: state '{prop}' needs "
                                    f"{{arg: N}} or {{value: ...}}"
                                )
                            elif arg_ref is not None and (
                                isinstance(arg_ref, bool)
                                or not isinstance(arg_ref, int)
                                or arg_ref < 0
                            ):
                                errors.append(
                                    f"{where}: state '{prop}' arg must be an "
                                    f"integer >= 0 (got {arg_ref!r})"
                                )
            continue

        # json-body rules parse the whole reply as JSON and map fields by
        # key/path — they carry no regex pattern, so exempt them from the
        # pattern requirement. They need a set map or mappings list to do
        # anything, and child_set doesn't apply (no capture groups).
        if resp.get("json"):
            if resp.get("child_set") is not None:
                errors.append(
                    f"Response {i}: child_set is not supported on json responses"
                )
            if not isinstance(resp.get("set"), dict) and not isinstance(
                resp.get("mappings"), list
            ):
                errors.append(
                    f"Response {i}: json response needs a 'set' map or a "
                    f"'mappings' list"
                )
            continue

        pattern = resp.get("pattern", "") or resp.get("match", "")
        if not pattern:
            errors.append(f"Response {i}: missing pattern, match, or address")
        else:
            err = _regex_redos_error(f"Response {i}", pattern)
            if err:
                errors.append(err)

        # Validate child_set (route captures into child-entity state). A
        # misdeclared entry would silently never write child state, so
        # enforce the shape + capture-ref bounds at load time.
        child_set = resp.get("child_set")
        if child_set is None:
            continue
        if not isinstance(child_set, list) or not child_set:
            errors.append(f"Response {i}: child_set must be a non-empty list")
            continue
        # Capture-group count, when the raw pattern compiles cleanly (it may
        # contain {config} placeholders substituted at runtime — skip the
        # bound check then).
        ngroups: int | None = None
        if isinstance(pattern, str) and pattern:
            try:
                ngroups = re.compile(pattern).groups
            except re.error:
                ngroups = None

        def _check_group_ref(where: str, ref: str) -> None:
            try:
                group = int(ref[1:])
            except ValueError:
                errors.append(
                    f"{where}: {ref!r} is not a numeric capture ref ($1, $2, ...)"
                )
                return
            if group < 1:
                errors.append(f"{where}: capture ref must be $1 or higher")
            elif ngroups is not None and group > ngroups:
                errors.append(
                    f"{where}: capture ref ${group} exceeds the pattern's "
                    f"{ngroups} group(s)"
                )

        for j, entry in enumerate(child_set):
            where = f"Response {i}: child_set[{j}]"
            if not isinstance(entry, dict):
                errors.append(f"{where}: must be a mapping")
                continue
            ctype = entry.get("type")
            if not isinstance(ctype, str) or ctype not in child_types_map:
                errors.append(
                    f"{where}: type {ctype!r} is not a declared child_entity_type"
                )
                continue
            tdef = child_types_map.get(ctype)
            tdef = tdef if isinstance(tdef, dict) else {}
            cvars = tdef.get("state_variables")
            cvars = cvars if isinstance(cvars, dict) else {}
            cid = entry.get("id")
            id_fmt = tdef.get("id_format")
            id_fmt = id_fmt if isinstance(id_fmt, dict) else {}
            id_type = id_fmt.get("type", "integer")
            if cid is None:
                errors.append(
                    f"{where}: missing 'id' (a capture ref like $1, a "
                    f"literal, or {{group, map}})"
                )
            elif isinstance(cid, dict):
                # Long form: {group: N | $N, map: {wire: local_id}}.
                gref = cid.get("group")
                if isinstance(gref, str) and gref.startswith("$"):
                    _check_group_ref(f"{where}: id group", gref)
                elif isinstance(gref, int) and not isinstance(gref, bool):
                    _check_group_ref(f"{where}: id group", f"${gref}")
                else:
                    errors.append(
                        f"{where}: id group must be a capture ref "
                        f"(1, 2, ... or '$1')"
                    )
                id_map = cid.get("map")
                if id_map is not None:
                    if not isinstance(id_map, dict) or not id_map:
                        errors.append(
                            f"{where}: id map must be a non-empty mapping "
                            f"of wire id -> local child id"
                        )
                    else:
                        for mk, mv in id_map.items():
                            if isinstance(mv, bool) or not isinstance(
                                mk, (str, int)
                            ) or not isinstance(mv, (str, int)):
                                errors.append(
                                    f"{where}: id map entries must be "
                                    f"scalar wire id -> local id pairs"
                                )
                                break
                            if id_type == "integer":
                                try:
                                    int(str(mv).strip())
                                except ValueError:
                                    errors.append(
                                        f"{where}: id map value {mv!r} is "
                                        f"not an integer ({ctype} declares "
                                        f"integer ids)"
                                    )
                                    break
            elif isinstance(cid, str) and cid.startswith("$"):
                _check_group_ref(f"{where}: id", cid)
            state_map = entry.get("state")
            if not isinstance(state_map, dict) or not state_map:
                errors.append(
                    f"{where}: missing 'state' mapping (prop -> $N or literal)"
                )
                continue
            for prop, expr in state_map.items():
                if prop not in cvars:
                    errors.append(
                        f"{where}: state prop '{prop}' is not declared in "
                        f"child_entity_types.{ctype}.state_variables"
                    )
                if isinstance(expr, str) and expr.startswith("$"):
                    _check_group_ref(f"{where}: state '{prop}'", expr)

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

    # Opt-in send-side command framing: a constant prefix/suffix wraps every
    # byte-stream command. Both must be strings when present.
    for frame_key in ("command_prefix", "command_suffix"):
        frame_val = driver_def.get(frame_key)
        if frame_val is not None and not isinstance(frame_val, str):
            errors.append(f"{frame_key}: must be a string")

    # Device settings: each entry must be writable (a `write:` block — the
    # runtime raises NotImplementedError without one) and its state_key must
    # name a declared state variable. A typo'd state_key used to load fine
    # and just show "(not set)" forever while writes silently fired.
    declared_vars = driver_def.get("state_variables")
    declared_vars = declared_vars if isinstance(declared_vars, dict) else {}
    valid_setting_types = set(VALUE_TYPES)
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
    is_template = any(driver_id.startswith(p) for p in GENERIC_ID_PREFIXES)
    if not is_template and discovery_validator is not None:
        errors.extend(discovery_validator(driver_def))

    # Validate the optional `push:` block (device-initiated notifications —
    # frames arriving on a channel the platform must open, not the established
    # control connection). A misdeclared block would silently never deliver a
    # frame (the exact still-polling failure it exists to fix); enforce shape
    # and addressing at load time. Values accept `{config_field}` templates so
    # a device whose notification target is user-configurable can resolve
    # them per instance. Per-type keys: multicast joins a group:port; sse
    # holds GET path(s) open on the driver's own HTTP session; tcp_listener
    # opens a local port the device dials back to after a registration
    # command tells it where ({listener_port} substitution); http_listener
    # accepts device POSTs on a platform-assigned callback path (no keys of
    # its own — the URL is built at runtime and substitutes into commands as
    # {push_callback_url}).
    push_def = driver_def.get("push")
    if push_def is not None:
        if not isinstance(push_def, dict):
            errors.append("push: must be a mapping")
        else:
            # config_derived keys resolve into config at runtime, so a push
            # template may reference them like any declared field.
            _push_config_fields: set[str] = set()
            for src_key in CONFIG_FIELD_SOURCES:
                src = driver_def.get(src_key)
                if isinstance(src, dict):
                    _push_config_fields.update(src)

            _push_known_keys = PUSH_TYPE_KEYS
            ptype = push_def.get("type")
            if ptype not in _push_known_keys:
                errors.append(
                    "push: missing or unknown 'type' "
                    "(supported: multicast, sse, tcp_listener, http_listener)"
                )
            known_keys = _push_known_keys.get(
                ptype,
                {
                    "type", "group", "port", "path", "idle_timeout",
                    "frame_parser", "register", "unregister",
                },
            )
            unknown_push = set(push_def) - known_keys
            if unknown_push:
                errors.append(
                    f"push: unknown key(s): {', '.join(sorted(unknown_push))} "
                    f"(known keys for type {ptype!r}: "
                    f"{', '.join(sorted(known_keys))})"
                )

            def _push_template_ok(where: str, value: str) -> None:
                fields = re.findall(r"\{(\w+)\}", value)
                if not fields:
                    errors.append(
                        f"push: {where} {value!r} has braces but no "
                        f"{{config_field}} token"
                    )
                for field in fields:
                    if field not in _push_config_fields:
                        errors.append(
                            f"push: {where} references config field "
                            f"'{field}' that is not declared in "
                            f"config_schema, default_config, or "
                            f"config_derived"
                        )

            if ptype == "multicast":
                group = push_def.get("group")
                if group is None:
                    errors.append("push: missing 'group'")
                elif isinstance(group, str) and "{" in group:
                    _push_template_ok("group", group)
                else:
                    if not isinstance(group, str) or not is_multicast_group(group):
                        errors.append(
                            f"push: group {group!r} must be an IPv4 multicast "
                            f"address (224.0.0.0 - 239.255.255.255) or a "
                            f"{{config_field}} template"
                        )

                pport = push_def.get("port")
                if pport is None:
                    errors.append("push: missing 'port'")
                elif isinstance(pport, str) and "{" in pport:
                    _push_template_ok("port", pport)
                elif (
                    isinstance(pport, bool)
                    or not isinstance(pport, int)
                    or not (0 < pport < 65536)
                ):
                    errors.append(
                        "push: port must be an integer 1-65535 or a "
                        "{config_field} template"
                    )

            elif ptype == "sse":
                # SSE rides the driver's own HTTP session — it is a streaming
                # mode of the control transport, not a separate listener.
                if transport and transport != "http":
                    errors.append(
                        f"push: type 'sse' requires the http transport, "
                        f"not '{transport}'"
                    )

                raw_path = push_def.get("path")
                paths = (
                    [raw_path]
                    if isinstance(raw_path, str)
                    else raw_path if isinstance(raw_path, list) else None
                )
                if raw_path is None or paths == []:
                    errors.append(
                        "push: missing 'path' (an event-stream URL path, "
                        "or a list of them)"
                    )
                elif paths is None:
                    errors.append(
                        "push: path must be a string or a list of strings"
                    )
                else:
                    for p in paths:
                        if not isinstance(p, str) or not p.strip():
                            errors.append(
                                f"push: path entry {p!r} must be a non-empty "
                                f"string"
                            )
                        elif "{" in p:
                            _push_template_ok("path", p)
                        elif not p.startswith("/"):
                            errors.append(
                                f"push: path {p!r} must start with '/' "
                                f"(a URL path on the device) or be a "
                                f"{{config_field}} template"
                            )

                idle = push_def.get("idle_timeout")
                if idle is not None and (
                    isinstance(idle, bool)
                    or not isinstance(idle, (int, float))
                    or idle <= 0
                ):
                    errors.append(
                        "push: idle_timeout must be a positive number of "
                        "seconds"
                    )

            elif ptype == "tcp_listener":
                # The local inbound port the device dials back to. 0 lets the
                # OS assign one (fine when the registration command carries
                # {listener_port}; a fixed port is easier to firewall).
                pport = push_def.get("port")
                if pport is None:
                    errors.append("push: missing 'port'")
                elif isinstance(pport, str) and "{" in pport:
                    _push_template_ok("port", pport)
                elif (
                    isinstance(pport, bool)
                    or not isinstance(pport, int)
                    or not (0 <= pport < 65536)
                ):
                    errors.append(
                        "push: port must be an integer 0-65535 (0 = "
                        "OS-assigned) or a {config_field} template"
                    )

                # Per-subscription framing for the pushed frames. The control
                # transport's framing doesn't apply here — the dial-back
                # channel is its own byte stream.
                frame_cfg = push_def.get("frame_parser")
                if frame_cfg is not None:
                    if not isinstance(frame_cfg, dict):
                        errors.append("push: frame_parser must be a mapping")
                    else:
                        ftype = frame_cfg.get("type")
                        if ftype not in PUSH_FRAME_PARSER_TYPES:
                            errors.append(
                                f"push: frame_parser type {ftype!r} must be "
                                f"struct_frame, length_prefix, or "
                                f"fixed_length"
                            )
                        elif ftype == "struct_frame":
                            for fkey in (
                                "header_reserve", "mid_reserve",
                                "trailer_reserve",
                            ):
                                fval = frame_cfg.get(fkey, 0)
                                if (
                                    isinstance(fval, bool)
                                    or not isinstance(fval, int)
                                    or fval < 0
                                ):
                                    errors.append(
                                        f"push: frame_parser {fkey} must be "
                                        f"a non-negative integer"
                                    )
                            fsize = frame_cfg.get("length_size", 2)
                            if fsize not in STRUCT_LENGTH_SIZES:
                                errors.append(
                                    "push: frame_parser length_size must be "
                                    "1, 2, or 4"
                                )
                            fadj = frame_cfg.get("length_adjust", 0)
                            if isinstance(fadj, bool) or not isinstance(
                                fadj, int
                            ):
                                errors.append(
                                    "push: frame_parser length_adjust must "
                                    "be an integer"
                                )
                            fend = frame_cfg.get("length_endian", "big")
                            if fend not in LENGTH_ENDIANS:
                                errors.append(
                                    "push: frame_parser length_endian must "
                                    "be 'big' or 'little'"
                                )

                # register / unregister name driver commands (run after the
                # listener opens / before it closes). A typo here would
                # silently never arm the device — enforce the reference.
                _commands = driver_def.get("commands")
                _command_names = (
                    set(_commands) if isinstance(_commands, dict) else set()
                )
                for ckey in ("register", "unregister"):
                    cval = push_def.get(ckey)
                    if cval is None:
                        continue
                    if not isinstance(cval, str) or not cval.strip():
                        errors.append(
                            f"push: {ckey} must be a command name"
                        )
                    elif cval not in _command_names:
                        errors.append(
                            f"push: {ckey} command '{cval}' is not declared "
                            f"in commands"
                        )

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
            if auth_type not in AUTH_TYPES:
                errors.append(
                    f"auth: unsupported type '{auth_type}' (only 'telnet_login')"
                )
            # The handshake assumes a TCP/serial byte stream; on udp/http/osc the
            # frame-parser swap and raw buffering break the normal data path.
            if transport and transport not in AUTH_TRANSPORTS:
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

    # Validate the optional `liveness:` watchdog block ("send X every N, await
    # a reply within T, reconnect after K misses"). A misdeclared block would
    # silently never arm (no watchdog — the exact never-goes-offline failure it
    # exists to fix) or tear healthy devices down; enforce at load time.
    liveness_def = driver_def.get("liveness")
    if liveness_def is not None:
        if not isinstance(liveness_def, dict):
            errors.append("liveness: must be a mapping")
        else:
            # HTTP polling already awaits every response and raises on
            # failure, so the missed-poll watchdog covers it; `bridge` devices
            # own no transport. The probe only makes sense on the socket
            # transports that can die silently.
            if transport and transport not in LIVENESS_TRANSPORTS:
                errors.append(
                    f"liveness: not supported on transport '{transport}' "
                    f"(only tcp/serial/udp/osc)"
                )
            send = liveness_def.get("send")
            if not isinstance(send, str) or not send:
                errors.append(
                    "liveness: missing required 'send' (the probe payload — "
                    "a raw protocol string, or an OSC address on osc)"
                )
            expect = liveness_def.get("expect")
            if expect is not None:
                if not isinstance(expect, str) or not expect:
                    errors.append("liveness: 'expect' must be a regex string")
                else:
                    err = _regex_redos_error("liveness.expect", expect)
                    if err:
                        errors.append(err)
            for key, minimum in (("interval", 1.0), ("timeout", 0.1)):
                value = liveness_def.get(key)
                if value is not None:
                    if not isinstance(value, (int, float)) or isinstance(
                        value, bool
                    ) or value < minimum:
                        errors.append(
                            f"liveness: '{key}' must be a number >= {minimum}"
                        )
            max_failures = liveness_def.get("max_failures")
            if max_failures is not None and (
                not isinstance(max_failures, int)
                or isinstance(max_failures, bool)
                or max_failures < 1
            ):
                errors.append("liveness: 'max_failures' must be an integer >= 1")
            if liveness_def.get("args") is not None:
                if transport != "osc":
                    errors.append("liveness: 'args' is only valid on osc")
                elif not isinstance(liveness_def["args"], list):
                    errors.append("liveness: 'args' must be a list")

    # Validate the optional actions / quick_actions blocks (Quick Action strip).
    # quick_actions promote command ids to buttons; actions is the full form
    # (kind:"command" promotes a command, kind:"setup" is a provisioning wizard).
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
    valid_types = set(VALUE_TYPES)
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
        unit = var_def.get("unit")
        if unit is not None and not isinstance(unit, str):
            errors.append(
                f"State variable '{var_name}': unit must be a string "
                f"(got {unit!r})"
            )
        control = var_def.get("control")
        if control is not None and not isinstance(control, bool):
            errors.append(
                f"State variable '{var_name}': control must be true or false "
                f"(got {control!r})"
            )
        # The cloud state relay reads this tag to pick the forwarding tier;
        # a typo would silently fall back to the default cadence.
        cloud_priority = var_def.get("cloud_priority")
        if cloud_priority is not None and cloud_priority not in CLOUD_PRIORITIES:
            errors.append(
                f"State variable '{var_name}': cloud_priority must be 'low' "
                f"or 'high' (got {cloud_priority!r}); omit it for the "
                f"default cadence"
            )

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
                if header_size not in LENGTH_HEADER_SIZES:
                    errors.append(
                        f"frame_parser: header_size must be 1, 2, or 4 (got {header_size!r})"
                    )
                offset = frame_parser.get("header_offset", 0)
                if isinstance(offset, bool) or not isinstance(offset, int):
                    errors.append(
                        f"frame_parser: header_offset must be an integer (got {offset!r})"
                    )
                # Length field not at byte 0 (e.g. eISCP: length at offset 8,
                # behind magic + header-size, followed by version/reserved).
                for extra_key in ("length_offset", "header_extra"):
                    extra_val = frame_parser.get(extra_key, 0)
                    if (
                        isinstance(extra_val, bool)
                        or not isinstance(extra_val, int)
                        or extra_val < 0
                    ):
                        errors.append(
                            f"frame_parser: {extra_key} must be a non-negative "
                            f"integer (got {extra_val!r})"
                        )
                endian = frame_parser.get("length_endian", "big")
                if endian not in LENGTH_ENDIANS:
                    errors.append(
                        f"frame_parser: length_endian must be 'big' or 'little' "
                        f"(got {endian!r})"
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

    # Validate the optional send_frame block (send-side packet framing — the
    # send twin of frame_parser). Only length_prefix is supported; the header
    # bytes are literal-escape strings and length_size must be a positive int.
    send_frame = driver_def.get("send_frame")
    if send_frame is not None:
        if not isinstance(send_frame, dict):
            errors.append("send_frame: must be a mapping")
        else:
            sf_type = send_frame.get("type", "length_prefix")
            if sf_type not in SEND_FRAME_TYPES:
                errors.append(
                    f"send_frame: unknown type '{sf_type}' (expected 'length_prefix')"
                )
            else:
                length_size = send_frame.get("length_size", 4)
                if (
                    isinstance(length_size, bool)
                    or not isinstance(length_size, int)
                    or length_size < 1
                ):
                    errors.append(
                        f"send_frame: length_size must be a positive integer "
                        f"(got {length_size!r})"
                    )
                sf_endian = send_frame.get("length_endian", "big")
                if sf_endian not in LENGTH_ENDIANS:
                    errors.append(
                        f"send_frame: length_endian must be 'big' or 'little' "
                        f"(got {sf_endian!r})"
                    )
                for byte_key in ("header", "after_length"):
                    byte_val = send_frame.get(byte_key)
                    if byte_val is not None and not isinstance(byte_val, str):
                        errors.append(
                            f"send_frame: {byte_key} must be a string (got {byte_val!r})"
                        )

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
                    if id_type not in CHILD_ID_TYPES:
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
                    if cp is not None and cp not in CLOUD_PRIORITIES:
                        errors.append(
                            f"{where}.state_variables.{var_name}: cloud_priority "
                            f"must be 'low' or 'high' (got {cp!r}); omit it for "
                            f"the default cadence"
                        )
                    cunit = var_def.get("unit")
                    if cunit is not None and not isinstance(cunit, str):
                        errors.append(
                            f"{where}.state_variables.{var_name}: unit must be "
                            f"a string (got {cunit!r})"
                        )
                    cctl = var_def.get("control")
                    if cctl is not None and not isinstance(cctl, bool):
                        errors.append(
                            f"{where}.state_variables.{var_name}: control must "
                            f"be true or false (got {cctl!r})"
                        )

            # Validate the optional `instances:` roster block (declarative
            # children). A misdeclared block would silently register nothing
            # — the exact "declared types, empty panel" failure it replaces.
            instances = type_def.get("instances")
            if instances is not None:
                if not isinstance(instances, dict):
                    errors.append(f"{where}.instances: must be a mapping")
                else:
                    # config_derived keys resolve into config at runtime, so
                    # count_from / ids_from may name them too.
                    config_fields = set()
                    for src in CONFIG_FIELD_SOURCES:
                        block = driver_def.get(src)
                        if isinstance(block, dict):
                            config_fields.update(block.keys())
                    id_fmt = type_def.get("id_format")
                    id_fmt = id_fmt if isinstance(id_fmt, dict) else {}
                    id_type = id_fmt.get("type", "integer")
                    cfs = instances.get("count_from_state")
                    if cfs is not None:
                        if not isinstance(cfs, str) or not cfs:
                            errors.append(
                                f"{where}.instances: count_from_state must "
                                f"name a state variable"
                            )
                        elif cfs not in (driver_def.get("state_variables") or {}):
                            errors.append(
                                f"{where}.instances: count_from_state {cfs!r} "
                                f"is not a declared state variable"
                            )
                    sources = [
                        k for k in INSTANCE_SOURCES
                        if k in instances
                    ]
                    if len(sources) != 1:
                        errors.append(
                            f"{where}.instances: declare exactly one of "
                            f"'count', 'count_from', 'ids_from', 'ids'"
                        )
                    elif sources[0] == "ids":
                        raw_ids = instances["ids"]
                        if not isinstance(raw_ids, list) or not raw_ids:
                            errors.append(
                                f"{where}.instances: ids must be a non-empty "
                                f"list of literal child ids"
                            )
                        else:
                            for item in raw_ids:
                                if isinstance(item, bool) or not isinstance(
                                    item, (str, int)
                                ):
                                    errors.append(
                                        f"{where}.instances: ids entries must "
                                        f"be scalars (got {item!r})"
                                    )
                                    break
                                if id_type == "integer":
                                    try:
                                        int(str(item).strip())
                                    except ValueError:
                                        errors.append(
                                            f"{where}.instances: id {item!r} "
                                            f"is not an integer ({child_type} "
                                            f"declares integer ids)"
                                        )
                                        break
                    elif sources[0] == "count":
                        count = instances["count"]
                        if (
                            isinstance(count, bool)
                            or not isinstance(count, int)
                            or count < 1
                        ):
                            errors.append(
                                f"{where}.instances: count must be an "
                                f"integer >= 1 (got {count!r})"
                            )
                        else:
                            mx = id_fmt.get("max")
                            if (
                                isinstance(mx, int)
                                and not isinstance(mx, bool)
                                and count > mx
                            ):
                                errors.append(
                                    f"{where}.instances: count ({count}) "
                                    f"exceeds id_format.max ({mx})"
                                )
                        if id_type == "string":
                            errors.append(
                                f"{where}.instances: 'count' requires integer "
                                f"ids (id_format.type is 'string' — use "
                                f"'ids_from')"
                            )
                    else:
                        src_key = sources[0]
                        field = instances[src_key]
                        if not isinstance(field, str) or not field:
                            errors.append(
                                f"{where}.instances: {src_key} must name a "
                                f"config field"
                            )
                        elif field not in config_fields:
                            errors.append(
                                f"{where}.instances: {src_key} '{field}' is "
                                f"not a declared config field (config_schema "
                                f"/ default_config / config_derived)"
                            )
                        if src_key == "count_from" and id_type == "string":
                            errors.append(
                                f"{where}.instances: 'count_from' requires "
                                f"integer ids (id_format.type is 'string' — "
                                f"use 'ids_from')"
                            )
                    label = instances.get("label")
                    if label is not None and not isinstance(label, str):
                        errors.append(
                            f"{where}.instances: label must be a string"
                        )

    # Validate mapping entries in polling.queries and on_connect: per-child
    # query templates (each_child) and their optional `when:` gate. A bad entry
    # would silently poll nothing.
    # config_derived keys resolve into config at runtime, so a `when:` gate
    # may name them like any declared field.
    query_config_fields: set[str] = set()
    for _src in CONFIG_FIELD_SOURCES:
        _block = driver_def.get(_src)
        if isinstance(_block, dict):
            query_config_fields.update(_block.keys())

    def _validate_each_child(name: str, entries: Any, allow_osc_dict: bool) -> None:
        if not isinstance(entries, list):
            return
        for i, q in enumerate(entries):
            if not isinstance(q, dict):
                continue
            # `when: <config_field>` gates the entry on a truthy config value.
            # A field name that doesn't exist would silently disable the entry
            # forever, so a typo is an error rather than a quiet no-op.
            if "when" in q:
                when = q.get("when")
                if not isinstance(when, str) or not when:
                    errors.append(
                        f"{name}[{i}]: 'when' must name a config field"
                    )
                elif when not in query_config_fields:
                    errors.append(
                        f"{name}[{i}]: 'when' field '{when}' is not a declared "
                        f"config field (config_schema / default_config / "
                        f"config_derived)"
                    )
            if "each_child" not in q:
                if allow_osc_dict and "address" in q:
                    continue  # OSC on_connect {address, args} form
                send = q.get("send")
                if isinstance(send, str) and send:
                    continue  # plain {send, when} query — dict form only for `when`
                errors.append(
                    f"{name}[{i}]: mapping entries must be "
                    f"{{each_child, send}} or {{send, when}}"
                    + (" or {address, args}" if allow_osc_dict else "")
                )
                continue
            ec = q.get("each_child")
            if not isinstance(ec, str) or ec not in child_types_map:
                errors.append(
                    f"{name}[{i}]: each_child type {ec!r} is not a declared "
                    f"child_entity_type"
                )
            elif not isinstance(
                (child_types_map.get(ec) or {}).get("instances")
                if isinstance(child_types_map.get(ec), dict) else None,
                dict,
            ):
                errors.append(
                    f"{name}[{i}]: each_child type '{ec}' declares no "
                    f"instances: block — nothing would ever be polled"
                )
            send = q.get("send")
            if not isinstance(send, str) or not send:
                errors.append(f"{name}[{i}]: missing 'send' template")
            elif not re.search(r"\{child_id(?::[^{}]*)?\}", send):
                errors.append(
                    f"{name}[{i}]: 'send' must contain {{child_id}} "
                    f"(a format spec like {{child_id:02d}} works too)"
                )

    polling_def = driver_def.get("polling")
    if polling_def is not None and not isinstance(polling_def, dict):
        errors.append("polling: must be a mapping")
    elif isinstance(polling_def, dict):
        # The runtime sources poll cadence from default_config.poll_interval
        # only; an interval here would silently do nothing.
        if "interval" in polling_def:
            errors.append(
                "polling.interval is not read by the runtime — remove the "
                "interval: key and set default_config.poll_interval instead"
            )
        _validate_each_child(
            "polling.queries", polling_def.get("queries"), allow_osc_dict=False
        )
    _validate_each_child(
        "on_connect", driver_def.get("on_connect"), allow_osc_dict=True
    )

    return errors


def validate_actions(driver_def: dict[str, Any]) -> list[str]:
    """Validate the ``actions`` + ``quick_actions`` blocks of a driver
    definition. Returns a list of error strings (empty when valid).

    Mirrored by the catalog validator in ``openavc-drivers/scripts/
    build_index.py`` (kept stdlib-only there); keep the two in sync.
    """
    errors: list[str] = []
    commands = driver_def.get("commands")
    command_ids = set(commands.keys()) if isinstance(commands, dict) else set()

    quick = driver_def.get("quick_actions")
    if quick is not None:
        if not isinstance(quick, list):
            errors.append("quick_actions: must be a list of command ids")
        else:
            for i, cid in enumerate(quick):
                if not isinstance(cid, str) or not cid:
                    errors.append(
                        f"quick_actions[{i}]: must be a non-empty command id string"
                    )
                elif command_ids and cid not in command_ids:
                    errors.append(
                        f"quick_actions[{i}]: '{cid}' is not a declared command"
                    )

    actions = driver_def.get("actions")
    if actions is not None:
        if not isinstance(actions, list):
            errors.append("actions: must be a list")
        else:
            seen: set[str] = set()
            for i, entry in enumerate(actions):
                errors.extend(
                    _validate_action_entry(i, entry, command_ids, seen)
                )

    return errors


def _validate_action_entry(
    index: int, entry: Any, command_ids: set[str], seen: set[str],
) -> list[str]:
    errors: list[str] = []
    where = f"actions[{index}]"
    if not isinstance(entry, dict):
        return [f"{where}: must be a mapping"]

    action_id = entry.get("id")
    if not isinstance(action_id, str) or not action_id:
        errors.append(f"{where}: missing required 'id' (non-empty string)")
    else:
        if action_id in seen:
            errors.append(f"{where}: duplicate action id '{action_id}'")
        seen.add(action_id)

    kind = entry.get("kind", "command")
    if kind not in ACTION_KINDS:
        errors.append(
            f"{where}: unknown kind '{kind}' (expected one of {list(ACTION_KINDS)})"
        )

    label = entry.get("label")
    if label is not None and not isinstance(label, str):
        errors.append(f"{where}: 'label' must be a string")

    icon = entry.get("icon")
    if icon is not None and not isinstance(icon, str):
        errors.append(f"{where}: 'icon' must be a string (lucide icon name)")

    availability = entry.get("availability")
    if availability is not None and availability not in AVAILABILITIES:
        errors.append(
            f"{where}: 'availability' must be one of {list(AVAILABILITIES)}"
        )

    confirm = entry.get("confirm")
    if confirm is not None and not isinstance(confirm, (bool, str)):
        errors.append(f"{where}: 'confirm' must be a boolean or a message string")

    params = entry.get("params")
    if params is not None and not isinstance(params, dict):
        errors.append(f"{where}: 'params' must be a mapping")

    url = entry.get("url")
    if kind == "link":
        if url is not None and (not isinstance(url, str) or not url):
            errors.append(f"{where}: 'url' must be a non-empty string")
    elif url is not None:
        errors.append(f"{where}: 'url' is only valid on a kind:link action")

    errors.extend(_validate_visible_when(where, entry.get("visible_when")))

    # A kind:"command" action must resolve to a declared command. The command
    # is the explicit `command` field, or the action id itself.
    if kind == "command" and isinstance(action_id, str) and action_id:
        command_id = entry.get("command")
        if command_id is not None and not isinstance(command_id, str):
            errors.append(f"{where}: 'command' must be a string")
        else:
            target = command_id or action_id
            if command_ids and target not in command_ids:
                errors.append(
                    f"{where}: command '{target}' is not a declared command"
                )

    return errors


def _validate_visible_when(where: str, vw: Any) -> list[str]:
    """Validate a visible_when block: a single {key, operator, value} condition,
    or a {any: [...]} / {all: [...]} group of them. Light-touch — unknown extra
    keys are tolerated, only the recognized shapes are checked.
    """
    if vw is None:
        return []
    if not isinstance(vw, dict):
        return [f"{where}: 'visible_when' must be a mapping"]

    errors: list[str] = []
    if "any" in vw or "all" in vw:
        for group_key in ("any", "all"):
            if group_key not in vw:
                continue
            group = vw[group_key]
            if not isinstance(group, list) or not group:
                errors.append(
                    f"{where}: visible_when.{group_key} must be a non-empty list"
                )
                continue
            for j, cond in enumerate(group):
                errors.extend(
                    _validate_condition(f"{where}: visible_when.{group_key}[{j}]", cond)
                )
    else:
        errors.extend(_validate_condition(f"{where}: visible_when", vw))
    return errors


def _validate_condition(where: str, cond: Any) -> list[str]:
    if not isinstance(cond, dict):
        return [f"{where}: condition must be a mapping"]
    errors: list[str] = []
    key = cond.get("key")
    if not isinstance(key, str) or not key:
        errors.append(f"{where}: condition missing 'key' (state key string)")
    op = cond.get("operator", "eq")
    if op not in _VISIBLE_WHEN_OPERATORS:
        errors.append(f"{where}: unknown operator '{op}'")
    return errors
