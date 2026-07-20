"""Render the driver-contract registry into the Programmer IDE's types.

Produces ``web/programmer/src/api/types.gen.ts``: the driver-definition
interfaces the Builder edits, plus the validator constant tables, all from
the field registry in ``server/drivers/spec.py``. The registry decides
WHAT exists (fields, accepted values, docs); the tables in this module
decide only how each member is presented in TypeScript (optionality
conventions for drafts, ``Record`` shapes, named sub-interfaces). A field
added to the registry therefore appears in the generated types
automatically — mechanically mapped when no presentation override names
it.

Part of ``python -m server.drivers.contract_gen``; pure stdlib.
"""

from __future__ import annotations

import textwrap
from typing import Any, Callable

from server.drivers import spec

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the driver-contract registry (server/drivers/spec.py).
// Regenerate with:  python -m server.drivers.contract_gen
// A test compares this file against a fresh render, so hand edits fail CI.

"""

# Names for shared definitions when another node references them ($ref).
DEF_TS_NAMES: dict[str, str] = {
    "enumValue": "EnumOption",
    "commandEntry": "DriverCommandDef",
    "paramEntry": "DriverParamDef",
    "oscArg": "DriverOscArg",
    "mappingEntry": "DriverResponseMapping",
    "responseEntry": "DriverResponseDef",
    "childSetEntry": "DriverChildSetEntry",
    "eachChildQuery": "DriverEachChildQuery",
    "gatedQuery": "DriverGatedQuery",
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
    "discoveryBlock": "DriverDiscoveryConfig",
    "mdnsItem": "DriverDiscoveryMdnsFingerprint",
    "ssdpItem": "DriverDiscoverySsdpFingerprint",
    "amxDdpItem": "DriverDiscoveryAmxDdpFingerprint",
    "extractField": "DriverDiscoveryExtractRule",
    "tcpProbe": "DriverDiscoveryProbe",
    "udpProbe": "DriverDiscoveryProbe",
    "pythonProbe": "DriverDiscoveryPython",
    "frameParser": "DriverFrameParserShape",
    "sendFrame": "DriverSendFrameShape",
}

# Loose builder shapes for framing blocks: the editors treat them as a type
# tag plus free-form keys, so a hand-authored variant round-trips.
_FRAME_SHAPE = "{ type: string; [key: string]: unknown } | null"


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
    """Default registry-node -> TypeScript type mapping."""
    if node.get("any") is True:
        return "unknown"
    if "ref" in node:
        return DEF_TS_NAMES[node["ref"]]
    for comb in ("one_of", "any_of"):
        if comb in node:
            return _union([_mechanical_ts(sub) for sub in node[comb]])
    node_type = node.get("type")
    if node_type is None:
        return "unknown"
    if isinstance(node_type, (tuple, list)):
        return _union([_scalar_ts(t, node) for t in node_type])
    return _scalar_ts(node_type, node)


# --- interface emission table ------------------------------------------------
#
# (interface name, node getter, member presentation overrides, options)
#
# Member override keys: "type" (TS type text), "req" (True/False forces
# requiredness), "doc" (replaces the registry doc), "skip" (omit member).
# Options: "all_optional" (draft-friendly: every member optional unless a
# member override forces it), "extra_members" (TS-only members appended),
# "doc" (interface JSDoc; defaults to the node's doc).

_D = dict  # keep the table compact

INTERFACES: tuple[tuple[str, Callable[[], dict], dict[str, dict], dict], ...] = (
    (
        "DriverOscArg",
        lambda: spec.DEFS["oscArg"],
        {
            "type": _D(req=True, type="string"),
            "value": _D(req=True, type="string"),
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
            "type": _D(req=True, type="string"),
            "values": _D(type="EnumOption[]"),
            "default": _D(type="unknown"),
            "map": _D(type="Record<string, string | number>"),
        },
        _D(extra_members=(
            (
                "description",
                "string",
                "Accepted alias for help — read either, write help.",
                True,
            ),
        )),
    ),
    (
        "DriverCommandDef",
        lambda: spec.DEFS["commandEntry"],
        {
            "label": _D(req=True, type="string"),
            "send": _D(req=True, type="string"),
            "headers": _D(type="Record<string, string>"),
            "query_params": _D(type="Record<string, string>"),
            "params": _D(req=True, type="Record<string, DriverParamDef>"),
        },
        _D(),
    ),
    (
        "DriverResponseMapping",
        lambda: spec.DEFS["mappingEntry"],
        {
            "group": _D(req=True),
            "state": _D(req=True),
            "type": _D(type="string"),
            "value": _D(type="unknown"),
            "map": _D(type="Record<string, string>"),
        },
        _D(),
    ),
    (
        "DriverChildSetEntry",
        lambda: spec.DEFS["childSetEntry"],
        {
            "id": _D(type="string | number | DriverChildSetIdSpec"),
            "state": _D(type="Record<string, unknown>"),
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
        "DriverGatedQuery",
        lambda: spec.DEFS["gatedQuery"],
        {},
        _D(),
    ),
    (
        "DriverOscConnectItem",
        lambda: spec.FIELDS["on_connect"]["items"]["one_of"][3],
        {"args": _D(type="DriverOscArg[]")},
        _D(doc=(
            "An OSC on_connect item that carries typed arguments — a "
            "bring-up message that isn't a bare subscription address. "
            "when: gates it on a config field like the other entry shapes."
        )),
    ),
    (
        "DriverVisibleWhenCondition",
        lambda: spec.DEFS["visibleWhenCondition"],
        {"value": _D(type="unknown")},
        _D(),
    ),
    (
        "DriverActionDef",
        lambda: spec.DEFS["actionEntry"],
        {
            "params": _D(type="Record<string, DriverParamDef>"),
            "visible_when": _D(type="DriverVisibleWhen"),
        },
        _D(),
    ),
    (
        "DriverDiscoveryMdnsFingerprint",
        lambda: spec.DEFS["mdnsItem"]["any_of"][1],
        {"txt": _D(type="Record<string, string>")},
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
        lambda: spec.DEFS["tcpProbe"],
        {
            "extract": _D(type="Record<string, DriverDiscoveryExtractRule>"),
        },
        _D(doc=(
            "A tcp_probe / udp_probe declaration. tls and cert_subject "
            "apply to TCP probes only; UDP probes must declare a send "
            "payload and exactly one matcher."
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
        {
            "mdns": _D(type="Array<string | DriverDiscoveryMdnsFingerprint>"),
            "ssdp": _D(type="Array<string | DriverDiscoverySsdpFingerprint>"),
            "amx_ddp": _D(type="DriverDiscoveryAmxDdpFingerprint[]"),
            "tcp_probe": _D(type="DriverDiscoveryProbe"),
            "udp_probe": _D(type="DriverDiscoveryProbe"),
            "python": _D(type="string | DriverDiscoveryPython"),
        },
        _D(),
    ),
    (
        "DriverDeviceSettingWrite",
        lambda: spec.DEFS["deviceSettingWrite"],
        {
            "headers": _D(type="Record<string, string>"),
            "args": _D(type="DriverOscArg[]"),
        },
        _D(),
    ),
    (
        "DriverDeviceSettingDef",
        lambda: spec.DEFS["deviceSettingEntry"],
        {
            "label": _D(req=True, type="string"),
            "type": _D(req=True, type="string"),
            "values": _D(type="EnumOption[]"),
            "default": _D(type="unknown"),
            "write": _D(type="DriverDeviceSettingWrite"),
        },
        _D(),
    ),
    (
        "DriverSimulatorDef",
        lambda: spec.DEFS["simulatorSection"],
        {
            "initial_state": _D(type="Record<string, unknown>"),
            "delays": _D(type="Record<string, number>"),
            "controls": _D(type="Array<Record<string, unknown>>"),
            "command_handlers": _D(type="Array<Record<string, unknown>>"),
            "error_modes": _D(type=(
                "Record<string, { behavior?: string; description?: string; "
                "set_state?: Record<string, unknown> }>"
            )),
            "state_machines": _D(type="Record<string, Record<string, unknown>>"),
            "notifications": _D(type="Record<string, unknown>"),
        },
        _D(),
    ),
    (
        "DriverStateVarDef",
        lambda: spec.DEFS["stateVariableEntry"],
        {
            "type": _D(req=True, type="string"),
            "label": _D(req=True),
            "values": _D(type="string[]"),
            "default": _D(type="unknown"),
        },
        _D(),
    ),
    (
        "DriverChildStateVarDef",
        lambda: spec.DEFS["childStateVariableEntry"],
        {
            "type": _D(req=True, type="string"),
            "values": _D(type="string[]"),
            "default": _D(type="unknown"),
        },
        _D(),
    ),
    (
        "DriverChildIdFormat",
        lambda: spec.DEFS["childEntityType"]["fields"]["id_format"],
        {"type": _D(req=True)},
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
        {
            "id_format": _D(req=True, type="DriverChildIdFormat"),
            "state_variables": _D(
                req=True, type="Record<string, DriverChildStateVarDef>"
            ),
            "summary_fields": _D(type="string[]"),
            "instances": _D(type="DriverChildInstances"),
        },
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
        "DriverPushDef",
        lambda: spec.DEFS["pushBlock"],
        {
            "type": _D(type="string"),
            "frame_parser": _D(type=_FRAME_SHAPE),
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
        "DriverBridgePortDef",
        lambda: spec.FIELDS["bridge"]["fields"]["ports"]["items"],
        {},
        _D(),
    ),
    (
        "DriverBridgeDef",
        lambda: spec.FIELDS["bridge"],
        {"ports": _D(type="DriverBridgePortDef[]")},
        _D(),
    ),
    (
        "DriverDefinition",
        lambda: {"type": "object", "fields": spec.FIELDS},
        {
            "id": _D(req=True),
            "name": _D(req=True),
            "manufacturer": _D(req=True, type="string"),
            "category": _D(req=True, type="string"),
            "version": _D(req=True),
            "author": _D(req=True),
            "description": _D(req=True),
            "transport": _D(req=True, type="string"),
            "delimiter": _D(req=True),
            "transports": _D(type="string[]"),
            "bridge": _D(type="DriverBridgeDef"),
            "default_config": _D(req=True, type="Record<string, unknown>"),
            "config_derived": _D(type="Record<string, string>"),
            "config_schema": _D(req=True, type="Record<string, unknown>"),
            "device_settings": _D(type="Record<string, DriverDeviceSettingDef>"),
            "state_variables": _D(req=True, type="Record<string, DriverStateVarDef>"),
            "child_entity_types": _D(type="Record<string, DriverChildEntityType>"),
            "commands": _D(req=True, type="Record<string, DriverCommandDef>"),
            "quick_actions": _D(type="string[]"),
            "actions": _D(type="DriverActionDef[]"),
            "responses": _D(req=True, type="DriverResponseDef[]"),
            "on_connect": _D(type=(
                "(string | DriverEachChildQuery | DriverGatedQuery | "
                "DriverOscConnectItem | Record<string, unknown>)[]"
            )),
            "polling": _D(req=True, type=(
                "{ queries?: (string | DriverEachChildQuery | "
                "DriverGatedQuery)[] }"
            )),
            "frame_parser": _D(type=_FRAME_SHAPE),
            "send_frame": _D(type=_FRAME_SHAPE),
            "help": _D(type="DriverHelpDef"),
            "ports": _D(type="number[]"),
            "protocols": _D(type="string[]"),
            "tags": _D(type="string[]"),
            "compatible_models": _D(type="DriverCompatibleModelsEntry[]"),
        },
        _D(
            doc=(
                "A full driver definition — the .avcdriver document (or a "
                "Python driver's DRIVER_INFO) as the Builder edits it."
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

# Type aliases that present a registry union in its idiomatic TS form.
ALIASES: tuple[tuple[str, str, Callable[[], str]], ...] = (
    (
        "EnumOption",
        "string | { value: string; label?: string }",
        lambda: spec.DEFS["enumValue"]["doc"],
    ),
    (
        "DriverChildSetIdSpec",
        "{ group?: number | string; segment?: number; "
        "map?: Record<string, string | number> }",
        lambda: (
            "The long-form child_set id: which capture group (regex rules) "
            "or address segment (OSC rules) holds the wire id, with an "
            "optional wire-id -> local-id translation map."
        ),
    ),
    (
        "DriverDiscoveryExtractRule",
        "string | { regex: string; group?: number }",
        lambda: (
            "An extract: rule — a static literal, or a regex capture "
            "against the probe response."
        ),
    ),
    (
        "DriverVisibleWhen",
        "DriverVisibleWhenCondition | { any: DriverVisibleWhenCondition[] } "
        "| { all: DriverVisibleWhenCondition[] }",
        lambda: spec.DEFS["visibleWhen"]["doc"],
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
    ("CONFIG_FIELD_TYPES", _LIST, lambda: spec.CONFIG_FIELD_TYPES,
     "Types a config_schema field may declare."),
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


def _render_member(
    name: str,
    node: dict[str, Any],
    override: dict[str, Any],
    required: bool,
) -> str:
    if override.get("skip"):
        return ""
    if "req" in override:
        required = override["req"]
    ts = override.get("type") or _mechanical_ts(node)
    doc = override.get("doc", node.get("doc"))
    out = _doc_block(doc, "  ") if doc else ""
    opt = "" if required else "?"
    return out + f"  {name}{opt}: {ts};\n"


def _render_interface(
    ts_name: str,
    node: dict[str, Any],
    overrides: dict[str, dict],
    options: dict[str, Any],
) -> str:
    fields = node.get("fields")
    if not isinstance(fields, dict):
        raise ValueError(f"{ts_name}: source node has no fields")
    unknown = set(overrides) - set(fields)
    if unknown:
        raise ValueError(f"{ts_name}: overrides for unknown members {sorted(unknown)}")
    schema_required = set(node.get("required", ()))
    doc = options.get("doc", node.get("doc"))
    out = _doc_block(doc, "") if doc else ""
    out += f"export interface {ts_name} {{\n"
    for member, sub in fields.items():
        required = (
            False if options.get("all_optional") else member in schema_required
        )
        out += _render_member(member, sub, overrides.get(member, {}), required)
    for extra_name, extra_type, extra_doc, extra_opt in options.get(
        "extra_members", ()
    ):
        out += _doc_block(extra_doc, "  ")
        out += f"  {extra_name}{'?' if extra_opt else ''}: {extra_type};\n"
    out += "}\n"
    return out


def render_types_ts() -> str:
    sections = [BANNER]

    sections.append("// --- contract constant tables ---\n\n")
    for name, shape, getter, doc in CONSTANTS:
        sections.append(_render_constant(name, shape, getter(), doc) + "\n")
    sections.append(_push_keys_by_type() + "\n")

    sections.append("// --- driver definition types ---\n\n")
    rendered = {name for name, *_ in INTERFACES}
    for alias_name, alias_type, doc_getter in ALIASES:
        sections.append(_doc_block(doc_getter(), ""))
        sections.append(f"export type {alias_name} = {alias_type};\n\n")
        rendered.add(alias_name)
    for ts_name, getter, overrides, options in INTERFACES:
        sections.append(
            _render_interface(ts_name, getter(), overrides, options) + "\n"
        )

    text = "".join(sections).rstrip("\n") + "\n"

    # Every type name the emitted members reference must be defined in the
    # file — an override typo would otherwise only surface at tsc time.
    for name in set(DEF_TS_NAMES.values()) | rendered:
        if name in text and (
            f"export interface {name} " not in text
            and f"export type {name} =" not in text
        ):
            raise ValueError(f"type {name} is referenced but never rendered")
    return text
