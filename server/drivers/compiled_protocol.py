"""Shared pure helpers for the protocol interpreters.

"Interpret a driver's protocol" happens in three places: the driver runtime
(``server.drivers.configurable``), the auto-generated device simulator
(``simulator.yaml_auto``), and the driver/simulator validator
(``simulator.validate``). The helpers here are the pieces those interpreters
must agree on byte-for-byte — placeholder substitution, value coercion,
send_frame packet framing, and delimiter decoding. Each interpreter used to
carry its own copy, and a copy that drifts shows up as a simulator that
answers a command differently than the real device would — so they live here
once instead.

``compile_driver()`` turns a driver definition's receive side into a
``CompiledProtocol`` — the pre-compiled response tables the runtime matches
incoming data against. The other interpreters reason about the same rules,
so the compile lives here with the helpers rather than inside the runtime.

Pure on purpose: stdlib plus ``server.transport.binary_helpers`` only — the
same contract as ``server.drivers.inline_protocol``, and for the same reason:
the simulator runs as a separate process and imports this module directly, so
it must not pull in the driver runtime or transport stack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from server.transport.binary_helpers import encode_escape_sequences, pack_length_prefix
from server.utils.logger import get_logger

log = get_logger(__name__)


# ── Placeholder substitution ──


def safe_substitute(template: str, params: dict[str, Any]) -> str:
    """
    Substitute {name} and {name:spec} placeholders with values from params.

    Only replaces {name} where name is a key in params. An optional
    ``:format_spec`` (Python format-spec mini-language) formats the value —
    e.g. ``{preset:02d}`` zero-pads and ``{addr:04X}`` hex-formats, both
    common in device protocols. A numeric spec applied to a numeric string
    coerces it first, so a param that arrives as ``"5"`` still pads to
    ``"05"``. Literal JSON braces and unknown placeholders are left
    untouched, and an invalid spec leaves the placeholder verbatim rather
    than raising — this avoids the problem with Python's str.format()
    choking on JSON body strings.
    """
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        if key not in params:
            return match.group(0)  # Leave unmatched {name} as-is
        spec = match.group(2)
        value = params[key]
        if not spec:
            return str(value)
        try:
            return format(value, spec)
        except (ValueError, TypeError):
            # An integer spec ('d'/'x'/'X'/'o'/'b') rejects a float even when
            # it's whole (26.0), which is exactly what a scaled slider value
            # is. Coerce a whole-number float to int and retry so {vol:d}
            # renders "26".
            if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
                try:
                    return format(int(value), spec)
                except (ValueError, TypeError):
                    pass
            # A numeric spec applied to a numeric string: coerce, then
            # format. int first so '02d' works on "5".
            if isinstance(value, str):
                for conv in (int, float):
                    try:
                        return format(conv(value), spec)
                    except (ValueError, TypeError):
                        continue
            # Unformattable — leave the placeholder verbatim so a bad spec
            # is visible to the author instead of crashing the send.
            return match.group(0)

    # {name} or {name:spec}; the spec excludes braces so it can't span tokens.
    return re.sub(r"\{(\w+)(?::([^{}]*))?\}", replacer, template)


def derive_config(config: dict[str, Any], derived: Any) -> None:
    """Populate ``config`` in place with values derived from other fields.

    ``derived`` is the driver's optional top-level ``config_derived`` map of
    ``{name: template}``. Each template is substituted against config; if any
    ``{field}`` it references resolves to an empty/missing value, the derived
    value is ``""`` — so an optional prefixed segment simply disappears.
    Entries are processed in declaration order, so a later template may
    reference an earlier derived name.
    """
    if not isinstance(derived, dict):
        return
    for name, template in derived.items():
        if not isinstance(template, str):
            continue
        refs = re.findall(r"\{(\w+)(?::[^{}]*)?\}", template)
        if any(not str(config.get(f, "") or "").strip() for f in refs):
            config[name] = ""
        else:
            config[name] = safe_substitute(template, config)


# ── Value coercion ──


def coerce_value(raw: str, value_type: str) -> Any:
    """Convert a raw string (a regex capture or static value) to the type."""
    if value_type == "integer":
        try:
            return int(raw)
        except ValueError:
            log.warning("Cannot coerce %r to integer, returning raw string", raw)
            return raw
    elif value_type in ("float", "number"):
        try:
            return float(raw)
        except ValueError:
            log.warning("Cannot coerce %r to %s, returning raw string", raw, value_type)
            return raw
    elif value_type == "boolean":
        return raw.lower() in ("1", "true", "yes", "on")
    return raw  # string or enum


def coerce_json_value(value: Any, value_type: str) -> Any:
    """Coerce a native JSON value to the declared state type.

    Unlike ``coerce_value`` (string in), this keeps real JSON bools / ints /
    floats instead of round-tripping through ``str`` (which would turn JSON
    ``true`` into the string ``"True"``).
    """
    if value is None:
        return None
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if value_type == "integer":
        if isinstance(value, bool):
            return int(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                log.warning("Cannot coerce %r to integer, returning string", value)
                return str(value)
    if value_type in ("float", "number"):
        try:
            return float(value)
        except (TypeError, ValueError):
            log.warning("Cannot coerce %r to %s, returning string", value, value_type)
            return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def coerce_osc_value(value: Any, value_type: str) -> Any:
    """Convert an already-typed OSC value to the declared state type."""
    if value_type in ("float", "number"):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    elif value_type == "integer":
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    elif value_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).lower() in ("1", "true", "yes", "on")
    return str(value) if value is not None else None


# ── Delimiter decoding ──


def decode_delimiter(delim: str) -> str:
    """Decode a driver-declared delimiter string to its real characters.

    A YAML double-quoted scalar already carries real control characters
    (``"\\r\\n"`` arrives as CR LF), but a single-quoted or hand-typed value
    arrives as literal backslash escapes instead. Decodes the same escape set
    ``binary_helpers.encode_escape_sequences`` handles (``\\r``, ``\\n``,
    ``\\t``, ``\\\\``, ``\\xHH``) so both spellings mean the same bytes on the
    wire; anything else passes through untouched.
    """

    def _one(m: re.Match) -> str:
        seq = m.group(0)
        mapped = _DELIMITER_ESCAPES.get(seq)
        if mapped is not None:
            return mapped
        # \xHH — the regex guarantees exactly two hex digits (0x00-0xFF).
        return chr(int(seq[2:], 16))

    return re.sub(r"\\(?:r|n|t|\\|x[0-9a-fA-F]{2})", _one, delim)


_DELIMITER_ESCAPES = {"\\r": "\r", "\\n": "\n", "\\t": "\t", "\\\\": "\\"}


# ── send_frame packet framing ──


def build_send_frame(cfg: Any) -> dict[str, Any] | None:
    """Precompute a send_frame block's constant header bytes, or None.

    The header/after_length strings are escape-decoded once here (they carry
    raw bytes like eISCP's ``ISCP\\x00\\x00\\x00\\x10`` magic + header-size).
    Only ``length_prefix`` is supported; any other type is ignored with a
    warning so an unknown block never silently breaks sends.
    """
    if not cfg or not isinstance(cfg, dict):
        return None
    frame_type = cfg.get("type", "length_prefix")
    if frame_type != "length_prefix":
        log.warning(
            "Unsupported send_frame type %r; ignoring (only 'length_prefix')",
            frame_type,
        )
        return None
    return {
        "header": encode_escape_sequences(str(cfg.get("header", "") or "")),
        "after_length": encode_escape_sequences(str(cfg.get("after_length", "") or "")),
        "length_size": int(cfg.get("length_size", 4)),
        "length_endian": "little" if cfg.get("length_endian") == "little" else "big",
    }


def apply_send_frame(sf: dict[str, Any] | None, data: bytes) -> bytes:
    """Wrap an escape-decoded byte-stream payload in the send_frame header.

    No-op when ``sf`` is None. The data-length field is computed from
    ``len(data)`` per message — the piece a static command_prefix can't
    express. Output is ``header + packed_length + after_length + data``.
    """
    if not sf:
        return data
    length = pack_length_prefix(len(data), sf["length_size"], sf["length_endian"])
    return sf["header"] + length + sf["after_length"] + data


def split_send_frames(sf: dict[str, Any], buffer: bytearray) -> list[bytes]:
    """Strip complete send_frame packets off the front of ``buffer``.

    Consumes each complete frame (fixed header + computed-length body) from
    the buffer in place and returns the bare bodies, leaving any partial
    trailing frame for the next read — exactly what the receive side's
    length-prefix frame parser does on the other end of the wire.
    """
    header_len = len(sf["header"])
    length_size = sf["length_size"]
    total_header = header_len + length_size + len(sf["after_length"])
    messages: list[bytes] = []
    while len(buffer) >= total_header:
        data_len = int.from_bytes(
            buffer[header_len : header_len + length_size], sf["length_endian"]
        )
        total = total_header + data_len
        if len(buffer) < total:
            break
        messages.append(bytes(buffer[total_header:total]))
        del buffer[:total]
    return messages


# ── Receive-side compile ──


@dataclass
class CompiledProtocol:
    """The pre-compiled receive side of one driver instance.

    Three separate tables:

    1. ``responses`` — regex rules for TCP/serial/UDP/HTTP data
       (pattern, flat state mappings, compiled child_set entries, throttle)
    2. ``osc_responses`` — OSC address rules (address, mappings, child_set,
       throttle)
    3. ``json_responses`` — JSON-body rules applied together from one parsed
       object (mappings, throttle, require-keys scope)

    Every entry carries an optional throttle state ({window, last} or None) —
    a rule with ``throttle: <seconds>`` skips re-fires inside its window
    (drop-style; built for continuous push telemetry like audio level meters,
    where every skipped frame is superseded by the next).

    Compiled per driver INSTANCE, not per driver class: the tables embed the
    instance's config substitutions, and their mapping lists are mutable
    per-instance copies — sharing them across instances would let one
    instance's edits leak into every other instance of the same driver type.
    """

    responses: list[
        tuple[
            re.Pattern[str],
            list[dict[str, Any]],
            list[dict[str, Any]],
            dict[str, Any] | None,
        ]
    ] = field(default_factory=list)
    osc_responses: list[
        tuple[
            str,
            list[dict[str, Any]],
            list[dict[str, Any]],
            dict[str, Any] | None,
        ]
    ] = field(default_factory=list)
    json_responses: list[
        tuple[list[dict[str, Any]], dict[str, Any] | None, tuple[str, ...]]
    ] = field(default_factory=list)


def build_throttle(resp: dict[str, Any]) -> dict[str, Any] | None:
    """Compile a response entry's optional ``throttle: <seconds>`` into a
    per-instance {window, last} state dict (None when absent/invalid).
    ``last`` maps a throttle scope to its last-fire time. The loader validates
    the value up-front; runtime parsing stays defensive so a hand-installed
    file can't crash the driver."""
    raw = resp.get("throttle")
    if raw is None:
        return None
    try:
        window = float(raw)
    except (TypeError, ValueError):
        log.warning("Invalid response throttle %r ignored", raw)
        return None
    if window <= 0:
        return None
    return {"window": window, "last": {}}


def compile_child_set(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str = "",
) -> list[dict[str, Any]]:
    """Compile a response entry's ``child_set:`` list — route regex
    captures into child-entity state. Each entry is
    ``{type, id: $N | literal | {group, map}, state: {prop: $N | literal
    | {group/value/map/type}}}``.
    Value coercion uses the child type's declared ``state_variables``,
    mirroring the flat ``set:`` shorthand (static values coerce too).
    Malformed entries are skipped with a warning; the loader validates
    the same shape up-front so a catalog driver never gets here wrong.
    """
    raw = resp.get("child_set")
    if not isinstance(raw, list):
        return []
    compiled: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ctype = entry.get("type")
        tdef = child_types.get(ctype)
        if not isinstance(tdef, dict):
            log.warning(
                f"[{device_id}] child_set: unknown child type "
                f"{ctype!r}; skipping entry"
            )
            continue
        cvars = tdef.get("state_variables") or {}
        cid = entry.get("id")
        idspec: tuple[str, Any]
        id_map: dict[str, Any] | None = None
        if isinstance(cid, dict):
            # Long form: {group: N | $N, map: {wire: local_id}} — route
            # by capture ref, translating the captured wire id to the
            # local child id (0-based protocols, letter codes). A wire
            # id the map doesn't cover skips the entry at apply time.
            gref = cid.get("group")
            if isinstance(gref, str) and gref.startswith("$"):
                gref = gref[1:]
            try:
                idspec = ("group", int(gref))
            except (TypeError, ValueError):
                log.warning(
                    f"[{device_id}] child_set: id group "
                    f"{cid.get('group')!r} is not a capture ref; "
                    f"skipping entry"
                )
                continue
            raw_map = cid.get("map")
            if isinstance(raw_map, dict) and raw_map:
                id_map = {str(k): v for k, v in raw_map.items()}
        elif isinstance(cid, str) and cid.startswith("$"):
            try:
                idspec = ("group", int(cid[1:]))
            except ValueError:
                log.warning(
                    f"[{device_id}] child_set: id {cid!r} is not a "
                    f"numeric capture ref ($1, $2, ...); skipping entry"
                )
                continue
        elif cid is not None:
            idspec = ("literal", cid)
        else:
            log.warning(
                f"[{device_id}] child_set: missing 'id'; skipping entry"
            )
            continue
        state_map = entry.get("state")
        if not isinstance(state_map, dict):
            continue
        props: list[dict[str, Any]] = []
        for prop, expr in state_map.items():
            var_def = cvars.get(prop, {})
            var_type = (
                var_def.get("type", "string")
                if isinstance(var_def, dict)
                else "string"
            )
            if isinstance(expr, dict):
                pm: dict[str, Any] = {"prop": prop, "type": expr.get("type", var_type)}
                if "group" in expr:
                    try:
                        pm["group"] = int(expr["group"])
                    except (TypeError, ValueError):
                        continue
                elif "value" in expr:
                    pm["value"] = expr["value"]
                else:
                    continue
                if isinstance(expr.get("map"), dict):
                    pm["map"] = expr["map"]
                props.append(pm)
            elif isinstance(expr, str) and expr.startswith("$"):
                try:
                    group = int(expr[1:])
                except ValueError:
                    log.warning(
                        f"[{device_id}] child_set: state '{prop}' ref "
                        f"{expr!r} is not a numeric capture ref; skipping"
                    )
                    continue
                props.append({"prop": prop, "group": group, "type": var_type})
            else:
                # Static value — coerce by declared type like flat set:.
                props.append({"prop": prop, "value": expr, "type": var_type})
        if props:
            compiled.append(
                {
                    "type": ctype,
                    "id": idspec,
                    "id_map": id_map,
                    "props": props,
                }
            )
    return compiled


def compile_osc_child_set(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str = "",
) -> list[dict[str, Any]]:
    """Compile an OSC response entry's ``child_set:`` list — route an
    address-matched message into child-entity state. OSC has no capture
    groups, so the child id comes from an **address segment**
    (``id: {segment: N}``, 0-based over the /-split address — in
    ``/ch/07/mix/fader`` segment 1 is ``"07"``) or a literal, and prop
    values come from **positional args** (``{arg: N}``) or literals.
    ``map:`` semantics (id translation with unmapped-skip, per-prop value
    maps) mirror the regex path. Malformed entries are skipped with a
    warning; the loader validates the same shape up-front.
    """
    raw = resp.get("child_set")
    if not isinstance(raw, list):
        return []
    compiled: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ctype = entry.get("type")
        tdef = child_types.get(ctype)
        if not isinstance(tdef, dict):
            log.warning(
                f"[{device_id}] child_set: unknown child type "
                f"{ctype!r}; skipping entry"
            )
            continue
        cvars = tdef.get("state_variables") or {}
        cid = entry.get("id")
        idspec: tuple[str, Any]
        id_map: dict[str, Any] | None = None
        if isinstance(cid, dict):
            try:
                idspec = ("segment", int(cid.get("segment")))
            except (TypeError, ValueError):
                log.warning(
                    f"[{device_id}] child_set: OSC id needs a "
                    f"'segment' index (got {cid!r}); skipping entry"
                )
                continue
            raw_map = cid.get("map")
            if isinstance(raw_map, dict) and raw_map:
                id_map = {str(k): v for k, v in raw_map.items()}
        elif cid is not None and not (
            isinstance(cid, str) and cid.startswith("$")
        ):
            idspec = ("literal", cid)
        else:
            log.warning(
                f"[{device_id}] child_set: OSC rules have no capture "
                f"groups — id must be {{segment: N}} or a literal "
                f"(got {cid!r}); skipping entry"
            )
            continue
        state_map = entry.get("state")
        if not isinstance(state_map, dict):
            continue
        props: list[dict[str, Any]] = []
        for prop, expr in state_map.items():
            var_def = cvars.get(prop, {})
            var_type = (
                var_def.get("type", "string")
                if isinstance(var_def, dict)
                else "string"
            )
            if isinstance(expr, dict):
                pm: dict[str, Any] = {
                    "prop": prop,
                    "type": expr.get("type", var_type),
                }
                if "arg" in expr:
                    try:
                        pm["arg"] = int(expr["arg"])
                    except (TypeError, ValueError):
                        continue
                elif "value" in expr:
                    pm["value"] = expr["value"]
                else:
                    continue
                if isinstance(expr.get("map"), dict):
                    pm["map"] = expr["map"]
                props.append(pm)
            elif isinstance(expr, str) and expr.startswith("$"):
                log.warning(
                    f"[{device_id}] child_set: state '{prop}' — OSC "
                    f"rules have no capture groups; use {{arg: N}}. Skipping"
                )
                continue
            else:
                # Static value — coerce by declared type like flat set:.
                props.append({"prop": prop, "value": expr, "type": var_type})
        if props:
            compiled.append(
                {
                    "type": ctype,
                    "id": idspec,
                    "id_map": id_map,
                    "props": props,
                }
            )
    return compiled


def build_json_mappings(
    resp: dict[str, Any], state_variables: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build {state, key, type, map} mappings for a ``json: true`` response.

    Accepts the detailed ``mappings`` list or the friendly ``set`` map. In a
    json rule a ``set`` value is the JSON key to read (string shorthand) or a
    ``{key/path, type, map}`` spec — not a regex capture ref. Types default
    to the matching state variable's declared type.
    """
    mappings: list[dict[str, Any]] = list(resp.get("mappings", []))
    set_map = resp.get("set")
    if not mappings and isinstance(set_map, dict):
        for state_key, spec in set_map.items():
            var_def = state_variables.get(state_key, {})
            default_type = (
                var_def.get("type", "string") if isinstance(var_def, dict) else "string"
            )
            if isinstance(spec, dict):
                mappings.append({
                    "state": state_key,
                    "key": spec.get("key", spec.get("path", state_key)),
                    "type": spec.get("type", default_type),
                    "map": spec.get("map"),
                })
            else:
                mappings.append({
                    "state": state_key, "key": str(spec), "type": default_type,
                })
    return mappings


def compile_driver(
    definition: dict[str, Any],
    config: dict[str, Any],
    device_id: str = "",
) -> CompiledProtocol:
    """Compile a driver definition's receive side against one instance's
    config. Rule order is preserved — matching is first-match-wins on the
    runtime side. Mapping lists are copied per call: the source lists live
    in the (class-shared) definition, and aliasing them would let one
    instance's edits leak into every instance of the driver type."""
    compiled = CompiledProtocol()
    child_types = definition.get("child_entity_types") or {}
    for resp in definition.get("responses", []):
        # OSC responses use "address" key instead of "pattern"/"match"
        if "address" in resp:
            addr = safe_substitute(resp["address"], config)
            mappings = list(resp.get("mappings", []))
            compiled.osc_responses.append(
                (
                    addr,
                    mappings,
                    compile_osc_child_set(resp, child_types, device_id),
                    build_throttle(resp),
                )
            )
            continue

        # JSON-body response: parse the whole body once and map many keys
        # at a time. Additive — does not change the regex first-match path.
        # Optional `require:` scopes the rule to bodies carrying the named
        # key(s) — different endpoints on one REST device often reuse a
        # field name (`status`, `id`, `serialNumber`) with different
        # meanings, and an unscoped rule would misapply across them.
        if resp.get("json"):
            raw_require = resp.get("require")
            if isinstance(raw_require, str):
                require = (raw_require,)
            elif isinstance(raw_require, list):
                require = tuple(str(k) for k in raw_require if k)
            else:
                require = ()
            compiled.json_responses.append(
                (
                    build_json_mappings(resp, definition.get("state_variables", {})),
                    build_throttle(resp),
                    require,
                )
            )
            continue

        try:
            # Canonical key is "match"; "pattern" remains accepted as an alias.
            raw_pattern = resp.get("match", "") or resp.get("pattern", "")
            if not raw_pattern:
                continue
            resolved = safe_substitute(raw_pattern, config)
            pattern = re.compile(resolved)

            # Accept both "mappings" (detailed) and "set" (shorthand) formats.
            mappings = list(resp.get("mappings", []))
            if not mappings and "set" in resp:
                # Convert shorthand: {"set": {"input": "$1", "mute": "true"}}
                # to mappings: [{"group": 1, "state": "input"}, ...]
                state_vars = definition.get("state_variables", {})
                for state_key, value_expr in resp["set"].items():
                    var_def = state_vars.get(state_key, {})
                    var_type = var_def.get("type", "string") if isinstance(var_def, dict) else "string"
                    if isinstance(value_expr, str) and value_expr.startswith("$"):
                        try:
                            group = int(value_expr[1:])
                        except ValueError:
                            # A non-numeric $-reference is an author typo.
                            # Silently capturing group 0 (the whole match)
                            # would write a wrong value with no warning, so
                            # surface it and skip this mapping. Use "$0"
                            # explicitly if the whole match is intended.
                            log.warning(
                                "[%s] set-shorthand reference %r for state "
                                "'%s' is not a numeric group ($1, $2, ...); "
                                "skipping this mapping",
                                device_id, value_expr, state_key,
                            )
                            continue
                        mappings.append({"group": group, "state": state_key, "type": var_type})
                    else:
                        # Static values coerce by the state var's declared
                        # type too — without it a boolean var fed
                        # `set: {mute: "true"}` stored the string "True".
                        mappings.append({
                            "group": 0,
                            "state": state_key,
                            "value": value_expr,
                            "type": var_type,
                        })

            child_mappings = compile_child_set(resp, child_types, device_id)
            compiled.responses.append(
                (pattern, mappings, child_mappings, build_throttle(resp))
            )
        except re.error as e:
            log.warning(
                f"[{device_id}] Invalid response pattern "
                f"'{resp.get('match', resp.get('pattern', ''))}': {e}"
            )
    return compiled
