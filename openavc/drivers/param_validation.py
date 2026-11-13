"""Command parameters and device-setting values, checked against what the
driver declares.

This is the runtime gate: every caller passes through it (DeviceManager's
dispatch, ConfigurableDriver, the device audit, installed equipment), because
the IDE's pickers are an authoring aid and never the enforcement. The static
checks (the macro lint, the UI binding review, the Builder's Validate) ask
:func:`missing_required_params` here too, so an authoring gate and the
runtime never disagree about the same step.

The number and format checks are shared by both doors (:func:`_check_range`,
:func:`_check_format`), so a refused command parameter and a refused setting
give their reason in the same words. Where the two differ, they differ on
purpose: a device setting is persisted config, so a value outside its
declared list is refused, while a command parameter's unrecognized value
passes through (a ``$var`` may resolve to a wire value outside the authored
set).

A command left without a parameter it requires is refused in one sentence,
:func:`required_param_sentence`, in the names the Programmer shows ("Set
Level: Level Value is required."). The runtime raises it here; the macro lint,
the Builder's Validate and the UI binding review say it through
``core/command_params.required_refusal``. The key goes beside it, on
:class:`CommandParamError`'s ``param``, for a caller that has to correct the
call (the AI assistant).

``openavc.drivers.base`` re-exports the public names, and that is the path
drivers and the rest of the platform import them from. Pure: no driver, no
transport, no I/O.
"""

from __future__ import annotations

import math
import re
from typing import Any

from openavc.drivers.param_labels import param_label

# Tuples, not sets: a declared type is whatever the definition holds, and a
# membership test must not raise on an unhashable one.
_NUMERIC_TYPES = ("integer", "number", "float")
_TRUE_WORDS = ("true", "1", "yes", "on")
_FALSE_WORDS = ("false", "0", "no", "off")


class CommandParamError(ValueError):
    """A command parameter value failed the driver's declared validation
    (wrong type, out of min/max range, or not matching a declared pattern).

    Distinct from a generic ValueError so the API can map it to a 400 (bad
    request) with the message instead of a misleading 404 "device not found".
    Subclasses ValueError so existing ``except ValueError`` handlers still
    catch it. The message is user-facing and actionable — surface it verbatim.

    ``param`` is the key of the parameter a required-parameter refusal names
    by its label, for a caller that has to correct the call rather than read
    the sentence; None on every other refusal.
    """

    def __init__(self, message: str = "", *, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


def required_param_sentence(command: str, param: str) -> str:
    """The refusal of a command sent without a parameter its driver requires,
    in the names a person sees: the command's label (else its id) and the
    parameter's label (else its key made readable)."""
    return f"{command}: {param} is required."


class DeviceSettingValueError(ValueError):
    """A device-setting value failed the setting's declared validation
    (wrong type, out of min/max range, not one of the declared values, or
    a regex mismatch).

    The min/max/values/regex on a ``device_settings`` entry used to be
    enforced only by the IDE's editor; the runtime is the source of truth
    (a script, macro, cloud command, or raw REST call can send anything —
    and an unchecked value can even transmit a literal ``{value:d}``
    placeholder to the device when a format spec fails). Same 400-mapping
    rationale as CommandParamError.
    """


class UnknownDeviceSettingError(DeviceSettingValueError):
    """A settings write named a key the driver does not declare.

    The key half of the refusal above, split out because the two doors answer
    it differently and both used to answer it wrong: the setting key is a path
    segment on ``PUT /devices/{id}/settings/{key}`` (404 — that setting is not
    there) and a body field on the pending-settings door (400, beside the value
    checks). The first reached the caller as ``Device 'x' or setting 'y' not
    found``, which blames the device for the author's typo.

    Subclasses DeviceSettingValueError so a caller that only wants "this
    settings write was refused" still needs one branch.
    """


# --- The checks both doors share ---


def _number_kind(ptype: str) -> str:
    """What a refusal calls the number a type wants."""
    return "whole number" if ptype == "integer" else "number"


def _check_range(
    num: float | int, decl: dict[str, Any], where: str, error: type[ValueError],
) -> None:
    """Refuse a number outside the declared ``min``/``max``. Either bound may
    be absent; one that is not a number is ignored. ``where`` names the
    value at the front of the refusal."""
    mn, mx = decl.get("min"), decl.get("max")
    if isinstance(mn, (int, float)) and num < mn:
        raise error(f"{where} must be at least {mn}, got {num:g}")
    if isinstance(mx, (int, float)) and num > mx:
        raise error(f"{where} must be at most {mx}, got {num:g}")


def _check_format(pattern: Any, text: str, where: str, error: type[ValueError]) -> None:
    """Refuse text that does not fully match the declared pattern.

    A malformed pattern refuses nothing: rejecting it is load-time
    validation's job, and blocking a write on it would punish the person
    sending the value for the author's typo.
    """
    try:
        matched = re.fullmatch(pattern, text) is not None
    except re.error:
        return
    if not matched:
        raise error(
            f"{where} value {text!r} does not match the required format ({pattern})"
        )


def _resolve_enum_param_value(value: str, options: list[Any]) -> str:
    """Map a command-param value against an ``enum`` option list whose entries
    may be plain strings or ``{value, label}`` objects.

    Returns the wire value: a value already matching an option's ``value`` is
    kept; otherwise a human ``label`` maps to its ``value``; an unrecognized
    value passes through unchanged (a ``$var`` may resolve to a computed wire
    value outside the authored set). Lets an author label a hex/code set once
    (``{value: "0f", label: "Multi Channel Stereo"}``) instead of defining one
    command per code.
    """
    label_map: dict[str, str] = {}
    for opt in options:
        if isinstance(opt, dict):
            v = opt.get("value")
            if v is None:
                continue
            vs = str(v)
            if value == vs:
                return value
            lbl = opt.get("label")
            if isinstance(lbl, str) and lbl:
                label_map.setdefault(lbl, vs)
        elif value == str(opt):
            return value
    return label_map.get(value, value)


def _enum_wire_values(options: list[Any]) -> list[str]:
    """The wire values of an enum option list — each entry a plain value or a
    ``{value, label}`` object (a label-only / value-less dict is skipped, the
    same entries :func:`_resolve_enum_param_value` can map to)."""
    out: list[str] = []
    for opt in options:
        if isinstance(opt, dict):
            v = opt.get("value")
            if v is not None:
                out.append(str(v))
        else:
            out.append(str(opt))
    return out


# --- Device settings ---


def validate_device_setting_value(key: str, sdef: Any, value: Any) -> Any:
    """Validate + coerce one device-setting write against its declared schema.

    Returns the coerced value; raises :class:`DeviceSettingValueError` with a
    user-facing message. Mirrors the IDE editor's rules so no caller can push
    past them: boolean → a real bool (tolerant of "true"/"false"/0/1 for REST
    callers), integer/number → numeric honoring ``min``/``max``, declared
    ``values`` → membership, accepting a {value, label} entry's
    label and normalizing to the wire value (settings are persisted device config — there is
    no forgiving-free-text rationale like command pickers), string → trimmed,
    full-matching ``regex`` when declared (``pattern`` accepted as an alias —
    command params use that spelling and the mix-up was a silent no-op).
    """
    if not isinstance(sdef, dict):
        return value
    if value is None:
        raise DeviceSettingValueError(f"'{key}': a value is required")
    stype = sdef.get("type", "string")
    if stype == "boolean":
        return _setting_boolean(key, value)
    if stype in _NUMERIC_TYPES:
        return _setting_number(key, stype, sdef, value)
    return _setting_text(key, sdef, value)


def _setting_boolean(key: str, value: Any) -> bool:
    """A boolean setting's value as a real bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    word = value.strip().lower() if isinstance(value, str) else None
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise DeviceSettingValueError(f"'{key}' must be true or false, got {value!r}")


def _setting_number(
    key: str, stype: str, sdef: dict[str, Any], value: Any,
) -> float | int:
    """A numeric setting's value, of its declared type and inside its range."""
    num = _setting_numeric_value(value, stype)
    if num is None:
        raise DeviceSettingValueError(
            f"'{key}' must be a {_number_kind(stype)}, got {value!r}"
        )
    _check_range(num, sdef, f"'{key}'", DeviceSettingValueError)
    return num


def _setting_numeric_value(value: Any, stype: str) -> float | int | None:
    """``value`` as a number of the declared type, or None when it is not
    one: an integer setting takes 5.0 as 5 and refuses 5.5."""
    num = _parsed_setting_number(value)
    if num is None or stype != "integer":
        return num
    return int(num) if float(num).is_integer() else None


def _parsed_setting_number(value: Any) -> float | int | None:
    """A number as given, or parsed from text; None for anything else. A
    boolean is never a number, and neither are NaN and infinity, for the
    reason ``_as_number`` gives: the range check cannot stop them, and the
    device would get ``nan`` formatted into its command."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        num = value
    elif isinstance(value, str):
        try:
            num = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return num if math.isfinite(num) else None


def _setting_text(key: str, sdef: dict[str, Any], value: Any) -> str:
    """A text setting's value, trimmed: one of its declared values when it
    has a list, else matching its declared format."""
    sval = str(value).strip() if not isinstance(value, str) else value.strip()
    values = sdef.get("values")
    if isinstance(values, list) and values:
        return _setting_choice(key, values, sval, value)
    regex = sdef.get("regex") or sdef.get("pattern")
    if regex and sval != "":
        _check_format(regex, sval, f"'{key}'", DeviceSettingValueError)
    return sval


def _setting_choice(key: str, values: list[Any], sval: str, value: Any) -> str:
    """The wire value a setting with a declared list is written as.

    An enum list may carry {value, label} entries (label shown in the editor,
    wire value written). Either the wire value or the label is accepted and
    normalized to the wire value. Unlike a command picker, a device setting
    is persisted config — there is no forgiving-free-text path, so anything
    not resolving into the set is rejected.
    """
    wire = _enum_wire_values(values)
    resolved = _resolve_enum_param_value(sval, values)
    if resolved not in wire:
        raise DeviceSettingValueError(
            f"'{key}' must be one of: {', '.join(wire)} — got {value!r}"
        )
    return resolved


# --- Command parameters ---


def _as_number(value: Any, ptype: str) -> float | None:
    """Coerce a command-param value to a float for range checking, or None when
    it isn't a valid number of the declared type. Booleans never count as
    numbers; an integer param rejects a non-integral value (5.0 is fine, 5.5 is
    not).

    NaN and infinity are not numbers here, and the range check below cannot be
    the thing that stops them: every comparison with NaN is False, so NaN
    cleared ``num < min`` and ``num > max`` alike and went out on the wire --
    a fader set to an undefined level, reported as a success. Infinity was
    caught only where a ``max`` happened to be declared, so a param without one
    took it. Rejected here, before the integer check, because that check is
    where it would otherwise stop being a value question at all:
    ``int(float("nan"))`` raises ValueError and ``int(float("inf"))`` raises
    OverflowError, and the REST door answers a bare ValueError with
    ``Device 'x' not found`` -- about a device that is connected and rendering.
    """
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    if ptype == "integer" and f != int(f):
        return None
    return f


def missing_required_params(
    param_defs: Any, params: Any
) -> list[str]:
    """The params a command declares required that this call does not supply.

    The runtime gate below is one caller. The other callers are the static
    ones -- the macro lint, the UI binding review, the Builder's Validate --
    which have to answer the same question BEFORE anything is sent, and had
    better answer it the same way: a gate that says a step is fine and a
    runtime that refuses it is worse than no gate, because the gate is what
    somebody trusts at handover.

    So the rule lives here once, next to the enforcement it describes.
    Absent and ``None`` count as not supplied. So does a BLANK, on every type
    except a string: a blank names no quantity, no endpoint and no option, and
    reading it as supplied is what let ``level: ""`` through the required check
    and then through the trim-and-skip below -- sent with no type check and no
    range check, and answered `success: true` while the device stayed where it
    was. For free text a blank IS a value and stays one: the platform cannot
    tell a blank somebody meant from a blank somebody left, and clearing a name
    by sending an empty one is a real thing a driver offers. A param declaring
    ``trim: false`` says its edge whitespace is protocol-meaningful, so only a
    truly empty string counts there. Declared order, so a caller listing them
    names them the way the driver does.
    """
    if not isinstance(param_defs, dict):
        return []
    supplied = params if isinstance(params, dict) else {}
    return [
        name for name, pdef in param_defs.items()
        if _required(pdef) and _is_unsupplied(supplied.get(name), pdef)
    ]


def _required(pdef: Any) -> bool:
    """Whether a param definition declares the param required."""
    return isinstance(pdef, dict) and bool(pdef.get("required"))


def _is_unsupplied(value: Any, pdef: dict[str, Any]) -> bool:
    """Whether a required param has nothing in it -- see the caller for why a
    blank is nothing everywhere except on a string."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    if pdef.get("type", "string") == "string":
        return False
    return (value if pdef.get("trim", True) is False else value.strip()) == ""


def _with_declared_defaults(param_defs: dict[str, Any], params: Any) -> Any:
    """``params`` with each optional param the call leaves unsupplied given
    its declared ``default``; ``params`` itself when there is nothing to fill.

    Unsupplied means what it means for a required param (_is_unsupplied):
    absent, None, or a blank on anything but free text. A required param is
    never filled: leaving one out is refused, and its default is only where
    the Programmer's form starts.
    """
    filled: dict[str, Any] | None = None
    supplied = params if isinstance(params, dict) else {}
    for name, pdef in param_defs.items():
        if not _takes_default(pdef, supplied, name):
            continue
        if filled is None:
            filled = dict(supplied)
        filled[name] = pdef["default"]
    return params if filled is None else filled


def _takes_default(pdef: Any, supplied: dict[str, Any], name: str) -> bool:
    """Whether an optional param this call leaves unsupplied is given its
    declared default."""
    if not isinstance(pdef, dict) or pdef.get("required"):
        return False
    if pdef.get("default") is None:
        return False
    return _is_unsupplied(supplied.get(name), pdef)


def normalize_and_validate_command_params(
    command: str,
    param_defs: dict[str, Any],
    params: dict[str, Any],
    *,
    label: str | None = None,
) -> dict[str, Any]:
    """Trim and validate user-supplied command params against the command's
    declared param schema, returning a normalized copy.

    This is the runtime gate for command values. The IDE's pickers and inline
    validation are an authoring aid, but the runtime is the source of truth —
    it must never trust that the client narrowed the value correctly (a macro
    can resolve a ``$var`` to anything, the cloud/REST API can post a raw value,
    an old client may predate the validation). So every command value passes
    through here regardless of caller. The DeviceManager dispatch path applies
    it to every driver (Python drivers included — declared ``min``/``max``/
    ``pattern`` are enforced, not cosmetic); ConfigurableDriver also runs it
    internally so direct YAML-driver use (the Driver Builder test harness)
    stays gated.

    Normalization: string values are whitespace-trimmed (the overwhelmingly
    correct default for AV text protocols). A param whose edge whitespace is
    protocol-meaningful — a raw passthrough payload with a trailing line
    terminator, text typed verbatim into an on-screen keyboard — can declare
    ``trim: false`` to opt out. Validation, per the declared type:
      - integer/number/float -> must parse as a number and honor min/max.
      - string (and any type carrying a ``pattern``) -> must fullmatch the
        declared regex.
    Only declared params are checked; config-passthrough keys and params with
    no schema entry are left untouched, and empty optional values are skipped.

    A param declaring ``required: true`` must be supplied. That used to be a
    label the IDE drew a red asterisk from and nothing else read, so omitting
    one produced whatever the driver's code did next — a ``KeyError`` inside a
    YAML ``send:`` template or a Python handler, surfacing to the caller as the
    generic ``Failed to send command 'route' to device 'switcher_1'`` while the
    *value* checks on the very same command answered with
    ``'route': 'output' must be at most 8, got 99``. The declaration is the
    driver author's, so the runtime enforces it here, at the one gate every
    caller passes through. Absent, ``None`` and -- on everything but a string
    param -- a blank all count as not supplied. Which ones are missing is
    :func:`missing_required_params` above, because the authoring gates have to
    ask the same question before anything is sent. The refusal is
    :func:`required_param_sentence`, naming the command by ``label`` (its id
    when the caller has none) and carrying the parameter's key as ``param``.

    An optional param the call leaves unsupplied gets its declared
    ``default`` here, and is then checked like a supplied value. The
    Programmer's forms start from the default, so anything built there
    already carries it; this is what gives the same value to a script, the
    REST API or the AI assistant leaving the param out, where a YAML send
    template used to go out with ``{step}`` in it. A caller that passed None
    gets a dict back when there is a default to fill.

    Raises ``CommandParamError`` (a ValueError) with a user-facing message.
    """
    if not isinstance(param_defs, dict):
        return params
    _refuse_missing_required(label or command, param_defs, params)
    params = _with_declared_defaults(param_defs, params)
    if not params:
        # Returned unchanged, not normalized to {}: a caller that passed None
        # gets None back, so a driver handler sees the argument it always did.
        return params
    out = dict(params)
    for name, pdef in param_defs.items():
        if isinstance(pdef, dict) and name in out:
            out[name] = _normalized_param(command, name, pdef, out[name])
    return out


def _refuse_missing_required(
    command: str, param_defs: dict[str, Any], params: dict[str, Any] | None,
) -> None:
    """Raise the refusal for the first required parameter ``params`` leaves
    out, naming ``command`` (its label, else its id) and the parameter by
    their labels, with the parameter's key as ``param``."""
    missing = missing_required_params(param_defs, params)
    if missing:
        name = missing[0]
        raise CommandParamError(
            required_param_sentence(command, param_label(name, param_defs.get(name))),
            param=name,
        )


def _normalized_param(command: str, name: str, pdef: dict[str, Any], value: Any) -> Any:
    """One declared param's value, trimmed, checked and normalized. None and
    an optional left blank are passed back as they are."""
    if value is None:
        return value
    if isinstance(value, str):
        if pdef.get("trim", True) is not False:
            value = value.strip()
        if value == "":
            return value
    where = f"'{command}': '{name}'"
    ptype = pdef.get("type", "string")
    if ptype in _NUMERIC_TYPES:
        return _normalized_number(where, ptype, pdef, value)
    return _normalized_text(where, pdef, value)


def _normalized_number(where: str, ptype: str, pdef: dict[str, Any], value: Any) -> Any:
    """A numeric param's value, checked against its range.

    A *numeric* value is normalized to the declared type so a scaled control
    value lands on the wire the way the protocol needs: an ``integer`` param
    sends 26, not 26.0, whether a slider, a macro, or the REST API produced
    it. ``decimals`` (number only) rounds to that many places; ``decimals: 0``
    yields a whole number. A string is left as it is: a value already shaped
    as text (a zero-padded id) is not reformatted, and only what arithmetic
    produces is normalized. A ``number`` with no ``decimals`` rule is left
    as it is too.
    """
    num = _as_number(value, ptype)
    if num is None:
        raise CommandParamError(f"{where} must be a {_number_kind(ptype)}, got {value!r}")
    _check_range(num, pdef, where, CommandParamError)
    if isinstance(value, str):
        return value
    if ptype == "integer":
        return int(num)
    decimals = pdef.get("decimals")
    if not isinstance(decimals, int):
        return value
    return int(round(num)) if decimals <= 0 else round(num, decimals)


def _normalized_text(where: str, pdef: dict[str, Any], value: Any) -> Any:
    """A non-numeric param's value: a declared label mapped to its wire value,
    then checked against its declared pattern.

    Labels are accepted on any param that declares ``values`` (type: enum, or
    a string param with a dropdown), so they work from every caller: picker,
    macro ``$var``, or the REST/cloud API. Only text is mapped or checked.
    """
    if not isinstance(value, str):
        return value
    options = pdef.get("values")
    if isinstance(options, list) and options:
        value = _resolve_enum_param_value(value, options)
    pattern = pdef.get("pattern")
    if pattern:
        _check_format(pattern, value, where, CommandParamError)
    return value
