"""Render the driver-contract registry into the Programmer IDE's types.

Produces ``openavc/web/programmer/src/api/types.gen.ts``: the driver-definition
interfaces the Builder edits, plus the validator constant tables, all from
the field registry in ``openavc/drivers/spec.py``. The registry decides
WHAT exists (fields, accepted values, which members a driver must write,
docs); the tables in this module decide only how each member is spelled in
TypeScript (a string enum as ``string``, a block by an interface's name, a
loose ``Record``). A field added to the registry therefore appears in the
generated types automatically — mechanically mapped when no presentation
override names it.

The types say what a driver file may hold, not what an editor draft holds
(the Builder's ``DriverDraft`` adds the collections its draft fills in). So
a spelling may be wider than the contract, never narrower, and a member is
required only where the registry requires it or every door refuses a driver
without it (``RUNTIME_REQUIRED``). ``render_types_ts`` refuses to render
anything else: ``ts_type_check`` holds THE rule.

Part of ``python -m openavc.drivers.contract_gen``; pure stdlib.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any, Callable

from openavc.drivers import serial_line, spec, ts_type_check

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the driver-contract registry (openavc/drivers/spec.py).
// Regenerate with:  python -m openavc.drivers.contract_gen
// A test compares this file against a fresh render, so hand edits fail CI.

"""

# Names for shared definitions when another node references them ($ref).
DEF_TS_NAMES: dict[str, str] = {
    "enumValue": "EnumOption",
    "commandEntry": "DriverCommandDef",
    "udpSendEntry": "DriverUdpSend",
    "paramEntry": "DriverParamDef",
    "oscArg": "DriverOscArg",
    "mappingEntry": "DriverResponseMapping",
    "responseEntry": "DriverResponseDef",
    "childSetEntry": "DriverChildSetEntry",
    "childListEntry": "DriverChildListEntry",
    "eachChildQuery": "DriverEachChildQuery",
    "queryEntry": "DriverQueryEntry",
    "whenStateGate": "DriverWhenState",
    "actionEntry": "DriverActionDef",
    "visibleWhen": "DriverVisibleWhen",
    "visibleWhenCondition": "DriverVisibleWhenCondition",
    "helpBlock": "DriverHelpDef",
    "compatibleModelsEntry": "DriverCompatibleModelsEntry",
    "stateVariableEntry": "DriverStateVarDef",
    "childStateVariableEntry": "DriverChildStateVarDef",
    "childEntityType": "DriverChildEntityType",
    "childInstances": "DriverChildInstances",
    "deviceSettingEntry": "DriverDeviceSettingDef",
    "deviceSettingWrite": "DriverDeviceSettingWrite",
    "simulatorSection": "DriverSimulatorDef",
    "authBlock": "DriverAuthDef",
    "livenessBlock": "DriverLivenessDef",
    "pushBlock": "DriverPushDef",
    "pushSession": "DriverPushSessionDef",
    "pushRegisterEntry": "DriverPushRegisterEntry",
    "routingBlock": "DriverRoutingDef",
    "routingPlane": "DriverRoutingPlane",
    "discoveryBlock": "DriverDiscoveryConfig",
    "amxDdpItem": "DriverDiscoveryAmxDdpFingerprint",
    "extractField": "DriverDiscoveryExtractRule",
    "tcpProbe": "DriverDiscoveryProbe",
    "udpProbe": "DriverDiscoveryProbe",
    "frameParser": "DriverFrameParserShape",
    "sendFrame": "DriverSendFrameShape",
}

def _doc_block(doc: str, indent: str) -> str:
    wrapped = textwrap.wrap(doc, width=76 - len(indent))
    if len(wrapped) == 1:
        return f"{indent}/** {wrapped[0]} */\n"
    lines = "\n".join(f"{indent} * {line}" for line in wrapped)
    return f"{indent}/**\n{lines}\n{indent} */\n"


def _union(parts: list[str]) -> str:
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return " | ".join(seen)


def _scalar_ts(type_name: str, node: dict[str, Any]) -> str:
    if type_name == "string":
        enum = node.get("enum")
        if enum and all(isinstance(v, str) for v in enum):
            return _union([f'"{v}"' for v in enum])
        return "string"
    if type_name in ("integer", "number"):
        return "number"
    if type_name == "boolean":
        return "boolean"
    if type_name == "null":
        return "null"
    if type_name == "array":
        item = node.get("items")
        item_ts = _mechanical_ts(item) if item else "unknown"
        return f"({item_ts})[]" if "|" in item_ts else f"{item_ts}[]"
    if type_name == "object":
        fields = node.get("fields")
        if fields:
            members = []
            required = set(node.get("required", ()))
            for name, sub in fields.items():
                opt = "" if name in required else "?"
                members.append(f"{name}{opt}: {_mechanical_ts(sub)}")
            return "{ " + "; ".join(members) + " }"
        extra = node.get("extra")
        if isinstance(extra, dict) and extra.get("any") is not True:
            return f"Record<string, {_mechanical_ts(extra)}>"
        return "Record<string, unknown>"
    raise ValueError(f"unmapped scalar type: {type_name!r}")


def _mechanical_ts(node: dict[str, Any]) -> str:
    """Default registry-node -> TypeScript type mapping: a node an interface
    presents by that interface's name, a definition DEF_TS_NAMES names by
    its name, any other definition written out."""
    named = _named_ts(node)
    if named is not None:
        return named
    if "ref" in node:
        return _ref_ts(node["ref"])
    for comb in ("one_of", "any_of"):
        if comb in node:
            return _union([_mechanical_ts(sub) for sub in node[comb]])
    node_type = node.get("type")
    if node_type is None:
        return "unknown"
    if isinstance(node_type, (tuple, list)):
        return _union([_scalar_ts(t, node) for t in node_type])
    return _scalar_ts(node_type, node)


def _named_ts(node: dict[str, Any]) -> str | None:
    """``unknown`` for a node that takes anything, an interface's name for
    the node it presents, else None."""
    if node.get("any") is True:
        return "unknown"
    return _interface_presenting(node)


def _ref_ts(ref: str) -> str:
    """A definition by its name when DEF_TS_NAMES gives one, else written
    out; inside itself, ``unknown`` (no narrower)."""
    if ref in DEF_TS_NAMES:
        return DEF_TS_NAMES[ref]
    if ref in _EXPANDING:
        return "unknown"
    _EXPANDING.add(ref)
    try:
        return _mechanical_ts(spec.DEFS[ref])
    finally:
        _EXPANDING.discard(ref)


_PRESENTED: list[tuple[dict[str, Any], str]] = []
_EXPANDING: set[str] = set()


def _interface_presenting(node: dict[str, Any]) -> str | None:
    """The interface whose source is this very node, if one is."""
    if not _PRESENTED:
        _PRESENTED.extend((getter(), name) for name, getter, *_ in INTERFACES)
    return next((name for source, name in _PRESENTED if source is node), None)


# --- interface emission table ------------------------------------------------
#
# (interface name, node getter, member presentation overrides, options)
#
# Member override keys: "type" (TS type text, no narrower than the node),
# "doc" (replaces the registry doc), "skip" (omit member). Requiredness is
# never an override: it is the node's own, plus RUNTIME_REQUIRED.
# Options: "all_optional" (every member optional), "extra_members" (TS-only
# members appended, never required), "doc" (interface JSDoc; defaults to the
# node's doc).

_D = dict  # keep the table compact


def _either_probe() -> dict[str, Any]:
    """tcpProbe's node with udpProbe's own fields appended: the Builder edits
    both probes through one type, so a UDP-only field has to be on it too."""
    tcp, udp = spec.DEFS["tcpProbe"], spec.DEFS["udpProbe"]
    fields = dict(tcp["fields"])
    for name, node in udp["fields"].items():
        fields.setdefault(name, node)
    return {**tcp, "fields": fields}

INTERFACES: tuple[tuple[str, Callable[[], dict], dict[str, dict], dict], ...] = (
    (
        "DriverOscArg",
        lambda: spec.DEFS["oscArg"],
        {
            "type": _D(type="string"),
        },
        _D(doc="One typed OSC argument (type tag + value; see OSC_ARG_TYPES)."),
    ),
    (
        "ParamOptionsFrom",
        lambda: spec.DEFS["paramEntry"]["fields"]["options_from"],
        {},
        _D(),
    ),
    (
        "ParamTypeFrom",
        lambda: spec.DEFS["paramEntry"]["fields"]["type_from"],
        {},
        _D(),
    ),
    (
        "DriverParamDef",
        lambda: spec.DEFS["paramEntry"],
        {
            "type": _D(type="string"),
        },
        _D(),
    ),
    (
        "DriverUdpSend",
        lambda: spec.DEFS["udpSendEntry"],
        {},
        _D(all_optional=True),
    ),
    (
        "DriverCommandDef",
        lambda: spec.DEFS["commandEntry"],
        {},
        _D(),
    ),
    (
        "DriverResponseMapping",
        lambda: spec.DEFS["mappingEntry"],
        {
            "type": _D(type="string"),
        },
        _D(),
    ),
    (
        "DriverChildSetEntry",
        lambda: spec.DEFS["childSetEntry"],
        {
            "id": _D(type="string | number | DriverChildSetIdSpec"),
        },
        _D(),
    ),
    (
        "DriverChildListEntry",
        lambda: spec.DEFS["childListEntry"],
        {
            "id": _D(type="string | DriverChildSetIdSpec"),
        },
        _D(),
    ),
    (
        "DriverResponseDef",
        lambda: spec.DEFS["responseEntry"],
        {},
        _D(),
    ),
    (
        "DriverEachChildQuery",
        lambda: spec.DEFS["eachChildQuery"],
        {},
        _D(),
    ),
    (
        "DriverQueryEntry",
        lambda: spec.DEFS["queryEntry"],
        {},
        _D(),
    ),
    (
        "DriverOscConnectItem",
        lambda: spec.FIELDS["on_connect"]["items"]["one_of"][3],
        {},
        _D(doc=(
            "An OSC on_connect item that carries typed arguments — a "
            "bring-up message that isn't a bare subscription address. "
            "when: gates it on a config field like the other entry shapes."
        )),
    ),
    (
        "DriverVisibleWhenCondition",
        lambda: spec.DEFS["visibleWhenCondition"],
        {},
        _D(),
    ),
    (
        "DriverActionDef",
        lambda: spec.DEFS["actionEntry"],
        {},
        _D(),
    ),
    (
        "DriverDiscoveryMdnsFingerprint",
        lambda: spec.DEFS["mdnsItem"]["any_of"][1],
        {},
        _D(),
    ),
    (
        "DriverDiscoverySsdpFingerprint",
        lambda: spec.DEFS["ssdpItem"]["any_of"][1],
        {},
        _D(),
    ),
    (
        "DriverDiscoveryAmxDdpFingerprint",
        lambda: spec.DEFS["amxDdpItem"],
        {},
        _D(),
    ),
    (
        "DriverDiscoveryProbe",
        lambda: _either_probe(),
        {},
        _D(doc=(
            "A tcp_probe / udp_probe declaration. tls, cert_subject and then "
            "apply to TCP probes only, reply_port to UDP probes only; UDP "
            "probes must declare a send payload and exactly one matcher."
        )),
    ),
    (
        "DriverDiscoveryPython",
        lambda: spec.DEFS["pythonProbe"]["any_of"][1],
        {},
        _D(),
    ),
    (
        "DriverDiscoveryConfig",
        lambda: spec.DEFS["discoveryBlock"],
        {},
        _D(),
    ),
    (
        "DriverDeviceSettingWrite",
        lambda: spec.DEFS["deviceSettingWrite"],
        {},
        _D(),
    ),
    (
        "DriverDeviceSettingDef",
        lambda: spec.DEFS["deviceSettingEntry"],
        {
            "type": _D(type="string"),
        },
        _D(),
    ),
    (
        "DriverSimulatorDef",
        lambda: spec.DEFS["simulatorSection"],
        {
            "controls": _D(type="Array<Record<string, unknown>>"),
            "command_handlers": _D(type="Array<Record<string, unknown>>"),
            "state_machines": _D(type="Record<string, Record<string, unknown>>"),
        },
        _D(),
    ),
    (
        "DriverStateVarDef",
        lambda: spec.DEFS["stateVariableEntry"],
        {
            "type": _D(type="string"),
        },
        _D(),
    ),
    (
        "DriverChildStateVarDef",
        lambda: spec.DEFS["childStateVariableEntry"],
        {
            "type": _D(type="string"),
        },
        _D(),
    ),
    (
        "DriverChildIdFormat",
        lambda: spec.DEFS["childEntityType"]["fields"]["id_format"],
        {},
        _D(),
    ),
    (
        "DriverChildInstances",
        lambda: spec.DEFS["childInstances"],
        {},
        _D(),
    ),
    (
        "DriverChildEntityType",
        lambda: spec.DEFS["childEntityType"],
        {},
        _D(),
    ),
    (
        "DriverHelpDef",
        lambda: spec.DEFS["helpBlock"],
        {},
        _D(all_optional=True),
    ),
    (
        "DriverCompatibleModelsEntry",
        lambda: spec.DEFS["compatibleModelsEntry"],
        {},
        _D(),
    ),
    (
        "DriverAuthDef",
        lambda: spec.DEFS["authBlock"],
        {"type": _D(type="string")},
        _D(all_optional=True),
    ),
    (
        "DriverPushSessionDef",
        lambda: spec.DEFS["pushSession"],
        {},
        _D(all_optional=True),
    ),
    (
        "DriverPushRegisterEntry",
        lambda: spec.DEFS["pushRegisterEntry"],
        {},
        _D(),
    ),
    (
        "DriverPushDef",
        lambda: spec.DEFS["pushBlock"],
        {
            "type": _D(type="string"),
            "frame_parser": _D(type="DriverFrameParserShape | null"),
        },
        _D(all_optional=True),
    ),
    (
        "DriverLivenessDef",
        lambda: spec.DEFS["livenessBlock"],
        {"args": _D(type="unknown[]")},
        _D(all_optional=True),
    ),
    (
        "DriverRoutingPlane",
        lambda: spec.DEFS["routingPlane"],
        {},
        _D(),
    ),
    (
        "DriverRoutingDef",
        lambda: spec.DEFS["routingBlock"],
        {},
        _D(),
    ),
    (
        "DriverBridgePortDef",
        lambda: spec.FIELDS["bridge"]["fields"]["ports"]["items"],
        {},
        _D(),
    ),
    (
        "DriverBridgeDef",
        lambda: spec.FIELDS["bridge"],
        {},
        _D(),
    ),
    (
        "DriverDefinition",
        # A driver is refused at every door without the fields REQUIRED_FIELDS
        # names; the published schema's other required fields are the
        # catalog's, which a driver on this system may leave out.
        lambda: {
            "type": "object", "fields": spec.FIELDS, "required": spec.REQUIRED_FIELDS, "extra": False,
        },
        {
            "category": _D(type="string"),
            "transport": _D(type="string"),
            "transports": _D(type="string[]"),
            "config_schema": _D(type="Record<string, unknown>"),
            "on_connect": _D(type=(
                "(string | DriverEachChildQuery | DriverQueryEntry | "
                "DriverOscConnectItem | Record<string, unknown>)[]"
            )),
            "frame_parser": _D(type="DriverFrameParserShape | null"),
            "send_frame": _D(type="DriverSendFrameShape | null"),
        },
        _D(
            doc=(
                "A full driver definition — the .avcdriver document as the "
                "Builder edits it."
            ),
            extra_members=(
                (
                    "source",
                    '"builtin" | "user"',
                    "Where the file lives on disk — set by the list "
                    "endpoint, not authored. builtin ships with the "
                    "platform (read-only; use Customize a Copy); user "
                    "lives in driver_repo. Absent on an unsaved draft.",
                    True,
                ),
            ),
        ),
    ),
)

# Members every door refuses a driver without, though the registry node they
# sit on leaves them optional: the definition walk's own rules, not the shape
# walk's. Each renders required, and tests/test_driver_types_contract.py
# proves each one with a driver missing it that the loader refuses.
RUNTIME_REQUIRED: dict[str, tuple[str, ...]] = {
    # state_variable_checks: "State variable '<name>': missing 'label'"
    "DriverStateVarDef": ("label",),
    # framing_checks: "frame_parser: missing 'type'" (top level and push:)
    "DriverFrameParserShape": ("type",),
}


@dataclass(frozen=True)
class Alias:
    """A type alias in the rendered file. ``source`` names the registry
    definition it presents: rendered from it when ``ts`` is None, and
    checked against it either way. One with no ``source`` presents a shape
    the Builder edits several definitions through, checked wherever a
    member uses it."""

    name: str
    doc: Callable[[], str]
    source: str | None = None
    ts: str | None = None

    def text(self) -> str:
        if self.ts is not None:
            return self.ts
        assert self.source is not None
        return _mechanical_ts(spec.DEFS[self.source])


ALIASES: tuple[Alias, ...] = (
    Alias("EnumOption", lambda: spec.DEFS["enumValue"]["doc"], source="enumValue"),
    Alias(
        "DriverChildSetIdSpec",
        lambda: (
            "The long-form child_set id: which capture group (regex rules) "
            "or address segment (OSC rules) holds the wire id, with an "
            "optional wire-id -> local-id translation map."
        ),
        ts=(
            "{ group?: number | string; segment?: number; "
            "map?: Record<string, string | number> }"
        ),
    ),
    Alias(
        "DriverDiscoveryExtractRule",
        lambda: (
            "An extract: rule — a static literal, or a regex capture "
            "against the probe response."
        ),
        source="extractField",
    ),
    Alias("DriverVisibleWhen", lambda: spec.DEFS["visibleWhen"]["doc"], source="visibleWhen"),
    Alias("DriverWhenState", lambda: spec.DEFS["whenStateGate"]["doc"], source="whenStateGate"),
    # Framing blocks as the editors hold them: a type tag plus free-form
    # keys, so a hand-authored variant round-trips.
    Alias(
        "DriverFrameParserShape",
        lambda: (
            "A frame_parser block as the Builder holds it: its type and "
            "whatever keys that type reads."
        ),
        source="frameParser",
        ts="{ type: string; [key: string]: unknown }",
    ),
    Alias(
        "DriverSendFrameShape",
        lambda: (
            "A send_frame block as the Builder holds it: its type, when it "
            "names one, and whatever keys that type reads."
        ),
        source="sendFrame",
        ts="{ type?: string; [key: string]: unknown }",
    ),
)

# Constant tables consumed by the Builder's validator and editors, emitted
# in the shapes those consumers use (arrays for pickers, Sets for checks).
_LIST = "list"
_SET = "set"
_NUM_SET = "num_set"

CONSTANTS: tuple[tuple[str, str, Callable[[], Any], str], ...] = (
    ("YAML_TRANSPORTS", _LIST, lambda: spec.YAML_TRANSPORTS,
     "Transports a YAML driver may declare."),
    ("PYTHON_ONLY_TRANSPORTS", _LIST, lambda: spec.PYTHON_ONLY_TRANSPORTS,
     "Transports that need a Python driver (no YAML surface)."),
    ("INTERCHANGEABLE_TRANSPORTS", _LIST, lambda: spec.INTERCHANGEABLE_TRANSPORTS,
     "Values allowed in the transports: interchangeable list."),
    ("BRIDGE_PORT_KINDS", _LIST, lambda: spec.BRIDGE_PORT_KINDS,
     "Port kinds a bridge driver may advertise."),
    ("DRIVER_CATEGORY_IDS", _LIST, lambda: spec.CATEGORIES,
     "The catalog's driver categories (the Builder's labeled list in "
     "driverCategories.ts must cover exactly these)."),
    ("CONFIDENCE_LEVELS", _LIST, lambda: spec.CONFIDENCE_LEVELS,
     "compatible_models confidence levels."),
    ("VALUE_TYPES", _LIST, lambda: spec.VALUE_TYPES,
     "Value types for state variables, child state variables, and device settings."),
    ("STATE_VAR_TYPES", _SET, lambda: spec.VALUE_TYPES,
     "Set form of VALUE_TYPES for membership checks."),
    ("PARAM_TYPES", _LIST, lambda: spec.PARAM_TYPES,
     "Types a command/action parameter may declare."),
    ("PARAM_OPTIONS_FROM_SOURCES", _SET,
     lambda: spec.PARAM_OPTIONS_FROM_SOURCES,
     "Sources a param's options_from cascade may read its choices from."),
    ("CONFIG_FIELD_TYPES", _LIST, lambda: spec.CONFIG_FIELD_TYPES,
     "Types a config_schema field may declare."),
    ("CONNECTION_FIELDS", _SET, lambda: spec.CONNECTION_FIELDS,
     "Config keys a project keeps in its connections table rather than in "
     "the device's config. Such a field cannot be learned_from a state "
     "variable."),
    ("CLOUD_PRIORITIES", _LIST, lambda: spec.CLOUD_PRIORITIES,
     "cloud_priority forwarding tiers."),
    ("ACTION_KINDS_YAML", _LIST,
     lambda: tuple(k for k in spec.ACTION_KINDS
                   if k not in spec.PYTHON_ONLY_ACTION_KINDS),
     "Action kinds a YAML driver may declare (setup needs a Python driver)."),
    ("ACTION_AVAILABILITIES", _LIST, lambda: spec.AVAILABILITIES,
     "When an action button shows, relative to connection state."),
    ("VISIBLE_WHEN_OPERATORS", _SET, lambda: spec.VISIBLE_WHEN_OPERATORS,
     "Operators a visible_when condition accepts."),
    ("OSC_ARG_TYPES", _LIST, lambda: spec.OSC_ARG_TYPES,
     "OSC argument type tags the runtime can encode."),
    ("AUTH_TRANSPORTS", _SET, lambda: spec.AUTH_TRANSPORTS,
     "Transports the auth: login handshake supports."),
    ("AUTH_TYPES", _LIST, lambda: spec.AUTH_TYPES,
     "Auth handshake types the runtime implements."),
    ("LIVENESS_TRANSPORTS", _SET, lambda: spec.LIVENESS_TRANSPORTS,
     "Transports the liveness: watchdog supports."),
    ("PUSH_FRAME_PARSER_TYPES", _SET, lambda: spec.PUSH_FRAME_PARSER_TYPES,
     "Frame parsers a push: tcp_listener subscription may declare."),
    ("FRAME_PARSER_TYPES", _LIST, lambda: spec.FRAME_PARSER_TYPES,
     "Frame parser types valid on the top-level frame_parser block."),
    ("FRAME_HEADER_SIZES", _NUM_SET, lambda: spec.LENGTH_HEADER_SIZES,
     "Legal length_prefix header sizes."),
    ("STRUCT_LENGTH_SIZES", _NUM_SET, lambda: spec.STRUCT_LENGTH_SIZES,
     "Legal struct_frame length-field sizes."),
    ("LENGTH_ENDIANS", _LIST, lambda: spec.LENGTH_ENDIANS,
     "Byte orders a length field may declare."),
    ("SEND_FRAME_TYPES", _LIST, lambda: spec.SEND_FRAME_TYPES,
     "Send-side frame types."),
    ("CHILD_ID_TYPES", _LIST, lambda: spec.CHILD_ID_TYPES,
     "child id_format.type values."),
    ("INSTANCE_SOURCES", _LIST, lambda: spec.INSTANCE_SOURCES,
     "The mutually exclusive child-roster sources."),
    ("CHILD_RESERVED_PROPS", _SET, lambda: spec.CHILD_RESERVED_PROPS,
     "State keys the platform provides on every child, on top of whatever "
     "the type declares. A driver never declares these and always may write "
     "them, so every picker, validator and column chooser has to know they "
     "are real."),
    ("DISALLOWED_OPEN_PORTS", _NUM_SET, lambda: spec.DISALLOWED_OPEN_PORTS,
     "Ports a discovery port_open hint may not use (they match every "
     "web/SSH host)."),
)


def _render_constant(name: str, shape: str, values: Any, doc: str) -> str:
    out = _doc_block(doc, "")
    if shape == _LIST:
        items = ", ".join(f'"{v}"' for v in values)
        out += f"export const {name} = [{items}] as const;\n"
    elif shape == _SET:
        items = ", ".join(f'"{v}"' for v in sorted(values))
        out += (
            f"export const {name}: ReadonlySet<string> = new Set([{items}]);\n"
        )
    elif shape == _NUM_SET:
        items = ", ".join(str(v) for v in values)
        out += (
            f"export const {name}: ReadonlySet<number> = new Set([{items}]);\n"
        )
    else:
        raise ValueError(shape)
    return out


def _push_keys_by_type() -> str:
    out = _doc_block(
        "The keys each push type accepts (unknown keys are rejected at "
        "load).", "",
    )
    out += "export const PUSH_KEYS_BY_TYPE: Readonly<Record<string, ReadonlySet<string>>> = {\n"
    for ptype, keys in spec.PUSH_TYPE_KEYS.items():
        items = ", ".join(f'"{k}"' for k in sorted(keys))
        out += f"  {ptype}: new Set([{items}]),\n"
    out += "};\n"
    return out


def _contract_keys() -> str:
    """Emit the declared key set of every block the registry closes.

    The Builder needs these to tell an author that ``state_varibles`` is a
    typo rather than saving a driver whose state variables silently vanish.
    Generated rather than hand-typed for the usual reason: a field added to
    the registry has to become a legal key everywhere at once, and a
    hand-maintained copy is the drift this contract exists to prevent.

    Selected by ``spec.block_has_fixed_keys``, the same predicate the Python
    validator asks — so the Builder, the schema, and the server cannot
    disagree about which keys are legal. ``__root__`` is the top-level node.
    """
    blocks: list[tuple[str, Any]] = [("__root__", spec.FIELDS)]
    for name, node in spec.DEFS.items():
        if spec.block_has_fixed_keys(node):
            blocks.append((name, node["fields"]))

    out = _doc_block(
        "Declared keys of every closed contract block, for the unknown-key "
        "check. __root__ is the top level; the rest are $defs names.", "",
    )
    out += (
        "export const DRIVER_CONTRACT_KEYS: "
        "Readonly<Record<string, ReadonlySet<string>>> = {\n"
    )
    for name, fields in blocks:
        items = ", ".join(f'"{k}"' for k in sorted(fields))
        out += f"  {name}: new Set([{items}]),\n"
    out += "};\n"
    return out


def _serial_line_settings() -> str:
    """Emit the serial line-setting table (``serial_line.SERIAL_LINE_SETTINGS``) for
    the Builder's Connection tab and the device form, which read it through
    ``components/shared/serialLine.ts``. Each setting is its JSON, as the
    table holds it."""
    out = _doc_block(
        "One choice a serial line setting may take: its value, the label a "
        "picker shows, and the other ways it may be written.", "",
    )
    out += (
        "export interface SerialLineChoice {\n"
        "  readonly value: string | number;\n"
        "  readonly label?: string;\n"
        "  readonly spellings?: readonly string[];\n"
        "}\n\n"
    )
    out += _doc_block(
        "A serial line setting: its label, the value it has when nothing is "
        "written, what a refusal says it must be, and either its choices or, "
        "for the baud rate (any whole number above zero), the rates a picker "
        "offers.", "",
    )
    out += (
        "export interface SerialLineSetting {\n"
        "  readonly label: string;\n"
        "  readonly default: string | number;\n"
        "  readonly allowed: string;\n"
        "  readonly offered?: readonly number[];\n"
        "  readonly choices?: readonly SerialLineChoice[];\n"
        "}\n\n"
    )
    keys = " | ".join(f'"{key}"' for key in serial_line.SERIAL_LINE_SETTINGS)
    out += f"export type SerialLineKey = {keys};\n\n"
    out += _doc_block(
        "THE serial line-setting table: what each setting may be and how "
        "each way of writing one reads (serialLine.ts reads it). A value "
        "written in a word or as text reads as the choice it names: Even, "
        "even and E are one parity.", "",
    )
    out += (
        "export const SERIAL_LINE_SETTINGS: "
        "Readonly<Record<SerialLineKey, SerialLineSetting>> = {\n"
    )
    for key, setting in serial_line.SERIAL_LINE_SETTINGS.items():
        out += f"  {key}: {json.dumps(setting, ensure_ascii=False)},\n"
    out += "};\n"
    return out


@dataclass(frozen=True)
class _Member:
    """One member as the interface renders it."""

    name: str
    node: dict[str, Any]
    ts: str
    required: bool
    doc: str


_OVERRIDE_KEYS = frozenset({"type", "doc", "skip"})


def _check_overrides(ts_name: str, fields: dict[str, Any], overrides: dict[str, dict]) -> None:
    """Refuse an override for a member the node lacks, or one that is more
    than presentation."""
    unknown = set(overrides) - set(fields)
    if unknown:
        raise ValueError(f"{ts_name}: overrides for unknown members {sorted(unknown)}")
    for member, override in overrides.items():
        extra = set(override) - _OVERRIDE_KEYS
        if extra:
            raise ValueError(
                f"{ts_name}.{member}: override keys {sorted(extra)} are not presentation "
                f"(type, doc, skip); requiredness comes from the registry or RUNTIME_REQUIRED"
            )


def _member(name: str, node: dict[str, Any], override: dict[str, Any], required: bool) -> _Member:
    # The Builder edits .avcdriver files, so a member's floors are the YAML
    # enum's -- never python_enum's, whose values it cannot offer.
    return _Member(
        name,
        node,
        override.get("type") or _mechanical_ts(node),
        required,
        override.get("doc", spec.node_doc(node, node.get("enum"))),
    )


def _members(
    ts_name: str,
    node: dict[str, Any],
    overrides: dict[str, dict],
    options: dict[str, Any],
) -> list[_Member]:
    """The members one interface renders, in the registry's order: each
    one's TypeScript type (its override's spelling, else the mechanical
    mapping) and whether it is required (the node's ``required``, plus
    ``RUNTIME_REQUIRED``; none in an ``all_optional`` interface)."""
    fields = node.get("fields")
    if not isinstance(fields, dict):
        raise ValueError(f"{ts_name}: source node has no fields")
    _check_overrides(ts_name, fields, overrides)
    required = (
        set() if options.get("all_optional")
        else set(node.get("required", ())) | set(RUNTIME_REQUIRED.get(ts_name, ()))
    )
    return [
        _member(name, sub, overrides.get(name, {}), name in required)
        for name, sub in fields.items()
        if not overrides.get(name, {}).get("skip")
    ]


def _render_interface(
    ts_name: str,
    node: dict[str, Any],
    overrides: dict[str, dict],
    options: dict[str, Any],
) -> str:
    doc = options.get("doc", spec.node_doc(node))
    out = _doc_block(doc, "") if doc else ""
    out += f"export interface {ts_name} {{\n"
    for member in _members(ts_name, node, overrides, options):
        out += _doc_block(member.doc, "  ") if member.doc else ""
        out += f"  {member.name}{'' if member.required else '?'}: {member.ts};\n"
    for extra_name, extra_type, extra_doc, extra_opt in options.get(
        "extra_members", ()
    ):
        out += _doc_block(extra_doc, "  ")
        out += f"  {extra_name}{'?' if extra_opt else ''}: {extra_type};\n"
    out += "}\n"
    return out


def contract_narrowings() -> list[str]:
    """Where the rendered types promise more than the contract, one
    sentence each (``ts_type_check``); [] when nowhere."""
    interfaces: dict[str, dict[str, tuple[str, bool]]] = {}
    members: list[tuple[str, str, Any]] = []
    sources: list[tuple[str, dict[str, Any]]] = []
    for ts_name, getter, overrides, options in INTERFACES:
        node = getter()
        rendered = _members(ts_name, node, overrides, options)
        interfaces[ts_name] = {m.name: (m.ts, m.required) for m in rendered}
        for extra_name, extra_type, _doc, extra_opt in options.get("extra_members", ()):
            interfaces[ts_name][extra_name] = (extra_type, not extra_opt)
        members.extend((f"{ts_name}.{m.name}", m.ts, m.node) for m in rendered)
        sources.append((ts_name, node))
    table = ts_type_check.TypeTable(
        interfaces,
        {alias.name: alias.text() for alias in ALIASES},
        spec.DEFS,
        RUNTIME_REQUIRED,
    )
    presented = [(a.name, spec.DEFS[a.source]) for a in ALIASES if a.source]
    return ts_type_check.narrowings(table, members, sources, presented)


def render_types_ts() -> str:
    sections = [BANNER]

    sections.append("// --- contract constant tables ---\n\n")
    for name, shape, getter, doc in CONSTANTS:
        sections.append(_render_constant(name, shape, getter(), doc) + "\n")
    sections.append(_push_keys_by_type() + "\n")
    sections.append(_contract_keys() + "\n")
    sections.append(_serial_line_settings() + "\n")

    sections.append("// --- driver definition types ---\n\n")
    rendered = {name for name, *_ in INTERFACES}
    for alias in ALIASES:
        sections.append(_doc_block(alias.doc(), ""))
        sections.append(f"export type {alias.name} = {alias.text()};\n\n")
        rendered.add(alias.name)
    for ts_name, getter, overrides, options in INTERFACES:
        sections.append(
            _render_interface(ts_name, getter(), overrides, options) + "\n"
        )

    text = "".join(sections).rstrip("\n") + "\n"
    _check_rendered(text, rendered)
    return text


def _named_not_defined(name: str, text: str) -> bool:
    defined = f"export interface {name} " in text or f"export type {name} =" in text
    return name in text and not defined


def _check_rendered(text: str, rendered: set[str]) -> None:
    """Refuse a rendered file that names a type it never defines (an
    override typo would otherwise only surface at tsc time), or whose types
    promise more than the contract (code reading a value a driver may not
    have)."""
    for name in set(DEF_TS_NAMES.values()) | rendered:
        if _named_not_defined(name, text):
            raise ValueError(f"type {name} is referenced but never rendered")
    narrowed = contract_narrowings()
    if narrowed:
        raise ValueError(
            "the driver types promise more than the contract:\n  " + "\n  ".join(narrowed)
        )
