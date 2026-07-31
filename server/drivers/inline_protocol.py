"""Inline-protocol parsing for the no-code Generic devices.

A Generic device can carry its own ``commands`` / ``responses`` /
``state_variables`` in its project-file config; these are merged over the
(usually empty) driver-file definition so the existing ConfigurableDriver
engine runs a config-authored protocol. The helpers here coerce the friendly,
possibly hand- or AI-authored config shapes into the engine's canonical forms.

Pure and stdlib-only on purpose: both the driver runtime
(``server.drivers.configurable``) and the device simulator
(``simulator.yaml_auto``) import these, so they must not pull in the driver
runtime or transport stack.

The six public names below are that shared surface — ``normalize_config_*``,
``derive_command_params``, ``derive_state_vars_from_responses`` and
``normalize_ir_codes``. Everything underscore-prefixed is internal to this
module and free to change: sharing is the whole reason this file exists, so
"private" has to mean something narrower here than "not imported yet".
"""

from __future__ import annotations

import json
import re
from typing import Any

from server.utils.logger import get_logger

log = get_logger(__name__)


# Capture pattern for the "after a prefix, take the number" response mode:
# an optional sign followed by a run of digits/dot. Matches 42, -3, 21.5 — the
# shapes AV gear reports for volume/levels/counts. Kept as a single flat group
# (no nested group) so the simulator can reverse it into an emit template.
_NUMBER_CAPTURE = r"(-?[\d.]+)"


def _as_dict(raw: Any) -> dict[str, Any]:
    """Coerce a config value that should be a dict.

    Accepts a dict, or a JSON object string (hand- or AI-edited configs
    sometimes store the map as text). Anything else → ``{}`` so a malformed
    value degrades to "no inline protocol" instead of crashing driver init.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(raw: Any) -> list[Any]:
    """Coerce a config value that should be a list (dict/JSON-string tolerant)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def normalize_config_commands(
    raw: Any, line_ending: str = "", prefix: str = "",
) -> dict[str, Any]:
    """Normalize device-config ``commands`` into the canonical command map.

    Tolerates the flat ``{name: "raw string"}`` shape (what the legacy Generic
    TCP driver used) by promoting a string value to ``{"send": <string>}``, so
    a migrated config and the friendly editor produce identical runtime
    behavior.

    ``prefix`` is the driver's ``command_prefix`` and ``line_ending`` its send
    terminator (``command_suffix``, else the ``delimiter`` config). When set,
    they wrap every send-style command as ``<prefix><send><line_ending>`` so the
    author declares a constant packet header / terminator once and never repeats
    it per row. A command flagged ``raw: true`` is left exactly as written.
    HTTP/OSC commands (``path`` / ``method`` / ``address``) carry no send string
    and are left untouched.
    """
    out: dict[str, Any] = {}
    for name, val in _as_dict(raw).items():
        if isinstance(val, str):
            cmd: dict[str, Any] = {"send": val}
        elif isinstance(val, dict):
            cmd = dict(val)  # copy so the framing never mutates config
        else:
            log.warning(
                "inline command %r has an unsupported shape (%s); skipped",
                name, type(val).__name__,
            )
            continue
        send = cmd.get("send")
        if isinstance(send, str) and not cmd.get("raw"):
            if prefix and not send.startswith(prefix):
                send = prefix + send
            if line_ending and not send.endswith(line_ending):
                send = send + line_ending
            cmd["send"] = send
        out[str(name)] = cmd
    return out


def derive_command_params(
    send: str, config_keys: set[str], existing: Any,
) -> dict[str, Any]:
    """Auto-declare a string param for each ``{placeholder}`` in a send string.

    So a friendly-editor command like ``VOL {level}`` prompts for ``level`` in
    the Send Command card and substitutes it at send time — without the user
    authoring a params table. Placeholders that name a config field (e.g.
    ``{host}``) are skipped: those resolve from config, not a prompt. Explicit
    params already declared on the command are preserved.
    """
    params: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    for ph in re.findall(r"\{(\w+)(?::[^{}]*)?\}", send):
        if ph in config_keys or ph in params:
            continue
        params[ph] = {"type": "string"}
    return params


def normalize_ir_codes(raw: Any) -> dict[str, Any]:
    """Normalize an ``ir_codes`` map into canonical IR command definitions.

    An IR device's code-set is a map of ``{name: {label, pronto, repeat}}``
    (authored per-device in the device config, or shipped as a community IR
    driver's ``default_config.ir_codes``). Each entry becomes a command with an
    ``ir`` payload the runtime emits through the bound bridge — the same command
    surface as any other device command, so panel buttons and macros bind to it
    via ``do: device.command`` with no IR-specific UI.

    The canonical stored form is Pronto hex plus a per-command repeat count.
    Pronto validity is checked at emit time by the bridge driver (this stays
    pure/stdlib and tolerant of hand- or AI-authored config); an entry missing a
    ``pronto`` string is skipped with a warning rather than crashing driver init.
    """
    out: dict[str, Any] = {}
    for name, val in _as_dict(raw).items():
        if not isinstance(val, dict):
            log.warning(
                "ir_codes entry %r has an unsupported shape (%s); skipped",
                name, type(val).__name__,
            )
            continue
        pronto = val.get("pronto")
        if not isinstance(pronto, str) or not pronto.strip():
            log.warning("ir_codes entry %r has no 'pronto' string; skipped", name)
            continue
        try:
            repeat = int(val.get("repeat", 1))
        except (TypeError, ValueError):
            repeat = 1
        if repeat < 1:
            repeat = 1
        out[str(name)] = {
            "label": val.get("label", name),
            "params": {},
            "ir": {"pronto": pronto, "repeat": repeat},
        }
    return out


def normalize_config_state_vars(raw: Any) -> dict[str, Any]:
    """Normalize device-config ``state_variables`` into the canonical schema.

    Accepts a ``{name: {type: ...}}`` map or a ``{name: "type"}`` shorthand.
    """
    out: dict[str, Any] = {}
    for name, val in _as_dict(raw).items():
        if isinstance(val, dict):
            out[str(name)] = val
        elif isinstance(val, str):
            out[str(name)] = {"type": val}
    return out


def _normalize_one_response(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one friendly response row into the engine's canonical
    ``{match, mappings}`` shape.

    Regex-free modes, keyed by ``mode``:
      - ``contains``:      reply contains ``text`` → set ``state`` = ``value``
      - ``prefix_number``: number after ``prefix`` → ``state`` (numeric)
      - ``prefix_text``:   text after ``prefix`` (rest of line) → ``state``
      - ``regex``:         raw ``pattern`` + capture ``group`` → ``state``

    A row already in canonical form (carrying ``mappings`` or set-shorthand
    ``set``) passes straight through, so hand- or AI-authored configs and the
    file definition keep working unchanged. Returns ``None`` for an
    unrecognized / incomplete row (skipped by the caller).
    """
    # Already canonical (detailed mappings or set-shorthand) — trust the engine.
    if "mappings" in entry or "set" in entry:
        return entry

    mode = entry.get("mode")
    state = entry.get("state")
    vtype = entry.get("type") or "string"

    # Infer the mode when omitted (hand/AI-authored rows) from whichever
    # friendly field is present. The frontend always writes an explicit mode.
    if not mode:
        if "contains" in entry or "text" in entry:
            mode = "contains"
        elif "after" in entry or "prefix" in entry:
            mode = "prefix_number" if entry.get("number") else "prefix_text"
        elif "json" in entry or "field" in entry:
            mode = "json"
        elif "pattern" in entry or "match" in entry:
            mode = "regex"

    if mode == "contains":
        text = entry.get("text") or entry.get("contains")
        if not text or not state:
            return None
        return {
            "match": re.escape(str(text)),
            "mappings": [
                {"state": str(state), "value": entry.get("value", ""), "type": vtype}
            ],
        }

    if mode in ("prefix_number", "prefix_text"):
        prefix = entry.get("prefix", entry.get("after", ""))
        if not state:
            return None
        if mode == "prefix_number":
            capture = _NUMBER_CAPTURE
            rtype = entry.get("type") or "number"
        else:
            capture = r"(.+)"
            rtype = entry.get("type") or "string"
        return {
            "match": re.escape(str(prefix)) + capture,
            "mappings": [{"group": 1, "state": str(state), "type": rtype}],
        }

    if mode == "regex":
        pattern = entry.get("pattern") or entry.get("match")
        if not pattern or not state:
            return None
        mapping: dict[str, Any] = {
            "group": int(entry.get("group", 1)), "state": str(state), "type": vtype,
        }
        if isinstance(entry.get("map"), dict):
            mapping["map"] = entry["map"]
        return {"match": str(pattern), "mappings": [mapping]}

    if mode == "json":
        # Pull one field out of a JSON-object body by key (dot path allowed).
        # One row = one field; multiple json rows are additive at runtime, so
        # several fields from the same response body each populate. The engine
        # parses the whole body once and never stops at the first match.
        key = entry.get("key") or entry.get("field")
        if not key or not state:
            return None
        spec: dict[str, Any] = {"key": str(key), "type": vtype}
        if isinstance(entry.get("map"), dict):
            spec["map"] = entry["map"]
        return {"json": True, "set": {str(state): spec}}

    return None


def normalize_config_responses(raw: Any) -> list[dict[str, Any]]:
    """Normalize a device-config ``responses`` list into canonical entries."""
    out: list[dict[str, Any]] = []
    for entry in _as_list(raw):
        if not isinstance(entry, dict):
            continue
        canon = _normalize_one_response(entry)
        if canon is not None:
            out.append(canon)
    return out


def derive_state_vars_from_responses(
    responses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Auto-declare a state variable for every ``state`` a response writes.

    So the var seeds an initial value and shows on the device card before the
    first matching reply arrives. Explicit ``state_variables`` config wins over
    these derived defaults.
    """
    out: dict[str, Any] = {}
    for resp in responses:
        for m in resp.get("mappings", []):
            if not isinstance(m, dict):
                continue
            s = m.get("state")
            if s and str(s) not in out:
                out[str(s)] = {"type": m.get("type", "string")}
        # Set-shorthand rows (incl. json: true) name their state vars as the
        # keys of `set`; seed those too so the var shows before the first reply.
        set_map = resp.get("set")
        if isinstance(set_map, dict):
            for state_key, spec in set_map.items():
                if str(state_key) in out:
                    continue
                vtype = spec.get("type", "string") if isinstance(spec, dict) else "string"
                out[str(state_key)] = {"type": vtype}
    return out
