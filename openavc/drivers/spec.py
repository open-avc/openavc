"""Single source of the .avcdriver driver-contract constants.

The driver contract — which fields exist, which values they accept, which
capabilities belong to YAML drivers vs. Python drivers — is consumed in
several places: the definition validator (``avcdriver_semantic``), the
runtime loader, the actions runtime, the community catalog's validator,
the published JSON Schema, and the Driver Builder's types. Each constant
here is THE definition; everything else derives from or imports it, so
the surfaces can't disagree about what the contract says.

Purity contract: standard library only. This module is imported by the
simulator, by validation code that runs outside the server (the community
driver catalog vendors it), and by transports — it must never pull in the
runtime, and it must stay import-cycle-free (nothing in openavc/ is above
it).
"""
from __future__ import annotations

import ipaddress
import re

# --- top-level contract ------------------------------------------------------

# Required top-level fields in a driver definition. Ordered (not a set) so
# missing-field errors always report in the same order run to run.
REQUIRED_FIELDS: tuple[str, ...] = ("id", "name", "transport")

# Top-level fields the community catalog additionally requires before a
# driver can publish (the platform loads a definition without them).
# Ordered as they appear in the published schema's required list.
CATALOG_REQUIRED_FIELDS: tuple[str, ...] = (
    "manufacturer", "category", "version", "author", "description", "source_url",
)

# The catalog's driver categories (index.json category values, the Browse
# Drivers filter, and the Builder's category dropdowns).
CATEGORIES: tuple[str, ...] = (
    "projector", "display", "switcher", "audio", "camera",
    "video", "streaming", "lighting", "power", "utility",
)

# compatible_models confidence levels (how sure the author is that a listed
# model works).
CONFIDENCE_LEVELS: tuple[str, ...] = ("full", "partial", "untested")

# Shared shape patterns (regex source strings). One definition so the
# schema, the catalog validator, and the Builder agree on what a legal id,
# version, tag, or reference URL looks like.
DRIVER_ID_PATTERN = r"^[a-z0-9_]+$"
SEMVER_PATTERN = r"^\d+\.\d+\.\d+(?:[\-+][\w.\-]+)?$"
TAG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
URL_PATTERN = r"^[Hh][Tt][Tt][Pp][Ss]?://"

# Transports a YAML (.avcdriver) definition may declare. "bridge" is the
# sentinel for a device that emits through a live bridge instance (an IR
# device on an emitter port) rather than dialing a host of its own.
YAML_TRANSPORTS: tuple[str, ...] = ("tcp", "serial", "udp", "http", "osc", "bridge")

# Transports only a Python driver can use — they need driver code (message
# routing hooks, session handling) that the declarative runtime doesn't model.
PYTHON_ONLY_TRANSPORTS: tuple[str, ...] = ("ssh", "mqtt")

# Transports a driver may list in its `transports:` interchangeable set (a
# text protocol whose wire strings are byte-identical over the network or a
# serial line). "bridge" is excluded — it is a routing sentinel, not a
# medium the same strings could ride.
INTERCHANGEABLE_TRANSPORTS: tuple[str, ...] = tuple(
    t for t in YAML_TRANSPORTS if t != "bridge"
)

# Port kinds a bridge driver may advertise (bridge.ports[].kind). A serial
# port vends a transparent TCP pass-through; ir/relay ports route commands
# through the bridge at send time.
BRIDGE_PORT_KINDS: tuple[str, ...] = ("serial", "ir", "relay")

# Driver ids with these prefixes are authoring templates (the built-in
# generic devices). They are exempt from discovery validation — templates
# don't participate in device matching.
GENERIC_ID_PREFIXES: tuple[str, ...] = ("generic_",)

# Value types for state variables, child state variables, and device settings.
VALUE_TYPES: tuple[str, ...] = ("string", "integer", "number", "boolean", "enum", "float")

# Types a command/action parameter may declare. No "float" (a number param
# covers it); "child_id" makes the param a picker over a declared child
# entity type's registered children.
PARAM_TYPES: tuple[str, ...] = (
    "string", "integer", "number", "boolean", "enum", "child_id",
)

# Types a config_schema field may declare. "text" renders a multi-line box,
# "table" a row editor whose columns carry the scalar types.
CONFIG_FIELD_TYPES: tuple[str, ...] = (
    "string", "text", "integer", "number", "float", "boolean", "enum", "table",
)

# The cloud state relay's forwarding tiers (state_variables.*.cloud_priority).
CLOUD_PRIORITIES: tuple[str, ...] = ("low", "high")

# Blocks whose keys become config fields a template/reference may name.
CONFIG_FIELD_SOURCES: tuple[str, ...] = ("config_schema", "default_config", "config_derived")

# --- transports with extra constraints ---------------------------------------

# Transports the auth: login-handshake block supports (it swaps the frame
# parser and types credentials over a raw byte stream).
AUTH_TRANSPORTS: tuple[str, ...] = ("tcp", "serial")

# Auth handshake types the runtime implements.
AUTH_TYPES: tuple[str, ...] = ("telnet_login",)

# Transports the liveness: watchdog supports (the socket transports that can
# die silently; HTTP polling already awaits every response, bridge devices
# own no transport).
LIVENESS_TRANSPORTS: tuple[str, ...] = ("tcp", "serial", "udp", "osc")

# Transports that put the device on a TCP port of its own, which is what makes
# a `discovery.port_open` hint able to fire at all: the scan behind that hint is
# a TCP connect sweep (discovery/port_scanner.py). A driver whose transports
# miss this set entirely declares a hint the matcher can never match — inert,
# and silently so, since the generic-port rule that already guards this field
# only rejects ports that are too broad, never ports on the wrong protocol.
# Kept here rather than in the validator so the rule reads off the registry
# like every other one.
PORT_OPEN_TRANSPORTS: tuple[str, ...] = ("tcp", "http")

# --- framing -----------------------------------------------------------------

# Receive-side frame_parser types a YAML driver may declare, and the
# per-type numeric constraints the runtime parsers accept.
FRAME_PARSER_TYPES: tuple[str, ...] = ("length_prefix", "fixed_length")
LENGTH_HEADER_SIZES: tuple[int, ...] = (1, 2, 4)
LENGTH_ENDIANS: tuple[str, ...] = ("big", "little")

# Send-side send_frame types (the send twin of frame_parser).
SEND_FRAME_TYPES: tuple[str, ...] = ("length_prefix",)

# Frame parsers a push: tcp_listener subscription may declare for its
# dial-back channel, and struct_frame's length-field sizes.
PUSH_FRAME_PARSER_TYPES: tuple[str, ...] = ("struct_frame", "length_prefix", "fixed_length")
STRUCT_LENGTH_SIZES: tuple[int, ...] = (1, 2, 4)

# --- push --------------------------------------------------------------------

# Push subscription types and the keys each accepts. Every type needs its
# own channel machinery in the runtime, so an unknown type is an error, and
# unknown keys are rejected against this table.
PUSH_TYPE_KEYS: dict[str, frozenset[str]] = {
    "multicast": frozenset({"type", "group", "port"}),
    "sse": frozenset({"type", "path", "idle_timeout"}),
    "tcp_listener": frozenset({
        "type", "port", "frame_parser", "register", "unregister",
    }),
    "http_listener": frozenset({"type"}),
}

# The keys each push type requires (beyond "type" itself). Ordered — the
# schema's per-type required lists are built from these.
PUSH_TYPE_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "multicast": ("group", "port"),
    "sse": ("path",),
    "tcp_listener": ("port",),
    "http_listener": (),
}

# --- children ----------------------------------------------------------------

# child_entity_types.*.id_format.type values.
CHILD_ID_TYPES: tuple[str, ...] = ("integer", "string")

# The mutually exclusive roster sources an instances: block may declare.
INSTANCE_SOURCES: tuple[str, ...] = ("count", "count_from", "ids_from", "ids")

# State keys the platform provides on every registered child, on top of
# whatever the type declares in state_variables. A driver never declares
# these and always may write them; a driver that DOES declare one keeps its
# own definition (label, help, cloud_priority), which is how a driver adds
# wording without taking the key over.
#
# All four are driver-managed — the platform seeds them and then has no
# opinion, because only the driver can see a sub-unit. `online` is presence.
# `label` is the user's friendly name. `offline_reason` / `offline_detail`
# are the child-level pair of the device's own offline_reason/offline_detail:
# a stable code automation can match on, and the sentence a person reads. See
# openavc/core/connection_fault.py for the codes and what each one means.
#
# This tuple is the contract half — the names. It lives here rather than in
# base.py because avcdriver_semantic.py has to see it (a YAML driver writing
# one of these through child_set: is writing a key it never declared) and
# that module is stdlib-only and vendored into openavc-drivers.
# Name -> the state-variable definition each reserved prop gets when the
# driver has not declared one itself. The TYPE half matters beyond the schema:
# the declarative child_set: compiler coerces a captured value by its declared
# type, so without an entry here a YAML driver reporting `online` would land
# the STRING "false" rather than the boolean False -- and a string "false" is
# truthy, so an offline endpoint would draw as a lit green status light.
CHILD_RESERVED_PROP_SCHEMA: dict[str, dict[str, str]] = {
    "online": {"type": "boolean", "label": "Online"},
    "label": {"type": "string", "label": "Label"},
    "offline_reason": {"type": "string", "label": "Fault"},
    "offline_detail": {"type": "string", "label": "Fault Detail"},
}

CHILD_RESERVED_PROPS: tuple[str, ...] = tuple(CHILD_RESERVED_PROP_SCHEMA)

# --- OSC ---------------------------------------------------------------------

# OSC argument type tags the ConfigurableDriver runtime can encode from a YAML
# value. 'b' (blob/bytes) is intentionally excluded — there's no unambiguous way
# to express raw bytes in a YAML arg value, so it isn't a declarative type (the
# Driver Builder UI and avcdriver.schema.json omit it too). An unsupported tag
# is dropped silently at send time, yielding a malformed OSC message — catch it
# at load instead. Ordered (float, int, string, the wider 64-bit forms, then
# the argument-less tags) — this is the order pickers and the schema list them.
OSC_ARG_TYPES: tuple[str, ...] = ("f", "i", "s", "h", "d", "T", "F", "N")

# --- command params ----------------------------------------------------------

# Sources a param's option list can cascade from (`options_from.source`).
PARAM_OPTIONS_FROM_SOURCES: tuple[str, ...] = ("child_schema",)

# --- actions -----------------------------------------------------------------

# Action kinds the platform understands. "command" promotes an existing command
# (runs online through send_command); "setup" is the offline-capable
# provisioning wizard handled by the driver's run_setup_action(); "link" opens a
# URL (the device's web UI) in a new tab, purely client-side.
ACTION_KINDS: tuple[str, ...] = ("command", "setup", "link")

# Action kinds only a Python driver can declare: "setup" needs a
# run_setup_action handler, which the declarative runtime doesn't have.
PYTHON_ONLY_ACTION_KINDS: tuple[str, ...] = ("setup",)

# How an action's visibility tracks the device's connection state.
AVAILABILITIES: tuple[str, ...] = ("online", "offline", "always")

# Operators accepted in a visible_when condition. Mirrors the shared condition
# evaluator (openavc/core/condition_eval.py) and the panel / Stream Deck
# JS evaluator so an action condition behaves identically everywhere.
# Ordered: canonical names first, then the accepted aliases.
VISIBLE_WHEN_OPERATORS: tuple[str, ...] = (
    "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy",
    "equals", "not_equals", "==", "!=", ">", "<", ">=", "<=",
)

# --- discovery ---------------------------------------------------------------

# Ports a discovery `port_open` hint may NOT use: every web/SSH admin host
# answers on these, so an open-port hint there would match the whole subnet.
# 8000 / 8080 / 8443 / 8888 are admin-UI alternates that show up on far too
# many devices to narrow anything. Ordered for display.
DISALLOWED_OPEN_PORTS: tuple[int, ...] = (22, 80, 443, 8000, 8080, 8443, 8888)

# --- shared predicates -------------------------------------------------------


def is_multicast_group(value: str) -> bool:
    """True when ``value`` is an IPv4 multicast group literal (224.0.0.0/4)."""
    try:
        return ipaddress.IPv4Address(value).is_multicast
    except (ipaddress.AddressValueError, ValueError):
        return False


# =============================================================================
# The field registry
# =============================================================================
#
# FIELDS (top-level driver-definition fields) and DEFS (shared sub-object
# shapes) describe every field of the .avcdriver contract: its type, its
# accepted values, its numeric/pattern constraints, which tier requires it,
# and its documentation. The generator (openavc/drivers/contract_gen.py)
# renders them into the published JSON Schemas (avcdriver.schema.json for
# YAML drivers, pythondriver.schema.json for Python DRIVER_INFO) and the
# Programmer IDE's generated driver types — so adding or changing a field
# here updates every authoring and validation surface in one edit.
#
# A field node is a plain dict. Recognized keys:
#
#   type        JSON type name, or a tuple of them ("string", ("string", "null"))
#   enum        accepted values (ordered tuple — usually a constant from above)
#   python_enum wider value set for the Python-driver tier (ssh/mqtt
#               transports, kind: setup); omitted = same as enum
#   const       exact required value
#   pattern     regex the value must match; format = JSON-Schema format tag
#   min / max / emin           minimum / maximum / exclusiveMinimum
#   min_len / min_items / min_props   minLength / minItems / minProperties
#   doc         description shown by editors and docs
#   since       the platform version that first understood this field. A
#               driver that uses it must declare min_platform_version at
#               least this high or it installs on releases that ignore it —
#               see platform_requirements() below. Rendered into the
#               published description, so the floor is documented and
#               enforced from the one annotation.
#   since_values  per-value floors for an enum whose members did not all
#               land together, e.g. {"table": "0.23.0"} on a config field's
#               type. Applies on top of the node's own `since`.
#   req         top-level requiredness tier: "platform" (loader rejects
#               without it) or "catalog" (community publishing requires it)
#   fields      child fields of an object (ordered dict of nodes)
#   required    required child-field names (ordered tuple)
#   extra       additionalProperties: True/False or a node for map values
#   prop_names  propertyNames constraint node
#   items       array item node
#   one_of / any_of / all_of   alternative shapes (tuple of nodes)
#   not_        negative constraint (raw JSON-Schema fragment)
#   ref         name of a DEFS entry ("$ref" in the schema)
#   raw         raw JSON-Schema fragment merged into the node verbatim —
#               the escape hatch for combinators the keys above don't model
#
# ANY marks a member that accepts any value (JSON Schema `true`).

ANY: dict = {"any": True}

# Column types for a table config field: the scalar config types (a table
# inside a table cell is not a thing).
_COLUMN_TYPES: tuple[str, ...] = tuple(t for t in CONFIG_FIELD_TYPES if t != "table")

# Action kinds a YAML driver may declare (the Python-only ones need driver
# code the declarative runtime doesn't have).
_YAML_ACTION_KINDS: tuple[str, ...] = tuple(
    k for k in ACTION_KINDS if k not in PYTHON_ONLY_ACTION_KINDS
)

FIELDS = {
    'id': {
        'req': 'platform',
        'type': 'string',
        'pattern': DRIVER_ID_PATTERN,
        'doc': 'Unique driver identifier. Lowercase alphanumeric with underscores.',
    },
    'name': {
        'req': 'platform',
        'type': 'string',
        'doc': 'Human-readable display name.',
    },
    'manufacturer': {
        'req': 'catalog',
        'type': 'string',
        'doc': 'Manufacturer name. Must exist in manufacturers.json for catalog submission.',
    },
    'category': {
        'req': 'catalog',
        'type': 'string',
        'enum': CATEGORIES,
        'doc': 'Driver category. One of the ten catalog categories.',
    },
    'version': {
        'req': 'catalog',
        'type': 'string',
        'pattern': SEMVER_PATTERN,
        'doc': 'Semantic version, e.g. 1.0.0.',
    },
    'author': {
        'req': 'catalog',
        'type': 'string',
        'doc': 'Driver author.',
    },
    'transport': {
        'req': 'platform',
        'type': 'string',
        'enum': YAML_TRANSPORTS,
        'python_enum': YAML_TRANSPORTS + PYTHON_ONLY_TRANSPORTS,
        'doc': 'Transport the driver uses to reach the device. Use "bridge" for a device that has no address of its own and emits through a live bridge instance (an IR device on an emitter port); it opens no socket and routes commands via the bridge.',
    },
    'transports': {
        'type': 'array',
        'doc': 'Optional. Transports this driver can use interchangeably (e.g. ["tcp", "serial"] for a text protocol whose command/response strings are byte-identical over the network or a serial line). The per-device connection picks the actual transport; listing "serial" makes the device offerable over a direct serial port or through a bridge. Opt-in: only declare it when the strings really are identical across the listed media.',
        'items': {
            'type': 'string',
            'enum': INTERCHANGEABLE_TRANSPORTS,
        },
    },
    'bridge': {
        'type': 'object',
        'doc': 'Optional. Declares this driver as a bridge: a device that exposes typed ports other devices connect through (e.g. a serial-to-Ethernet or IR bridge). The port declaration is valid in YAML, but the runtime behind a port needs a Python driver: pushing serial line settings to the hardware (prepare_bridge_port), and emitting/learning IR (bridge_emit / bridge_learn_*) for ir ports.',
        'fields': {
            'ports': {
                'type': 'array',
                'doc': 'The typed ports this bridge advertises.',
                'items': {
                    'type': 'object',
                    'fields': {
                        'id': {
                            'type': 'string',
                            'doc': 'Port id referenced by a downstream device\'s bridge_port (e.g. "serial:1").',
                        },
                        'kind': {
                            'type': 'string',
                            'enum': BRIDGE_PORT_KINDS,
                            'doc': 'Port kind. A serial port vends a transparent TCP pass-through; ir/relay ports route commands through the bridge at send time.',
                        },
                        'passthrough_port': {
                            'type': 'integer',
                            'min': 1,
                            'max': 65535,
                            'doc': 'For serial ports: the TCP port on the bridge host that transparently pipes this serial line (e.g. 4999).',
                        },
                        'label': {
                            'type': 'string',
                            'doc': 'Human-readable port label shown in the connection picker.',
                        },
                    },
                    'required': ('id', 'kind'),
                },
            },
        },
        'required': ('ports',),
    },
    'description': {
        'req': 'catalog',
        'type': 'string',
        'doc': 'Brief description of what the driver controls.',
    },
    'source_url': {
        'req': 'catalog',
        'type': 'string',
        'pattern': URL_PATTERN,
        'doc': 'Protocol reference or product documentation URL. Must start with http:// or https://.',
    },
    'delimiter': {
        'type': 'string',
        'doc': 'Message delimiter. Default "\\r". Use "\\r\\n" for CRLF. Escape sequences \\r and \\n are interpreted.',
    },
    'command_prefix': {
        'type': 'string',
        'doc': "Opt-in constant string prepended to every command's send string (a fixed packet header). Set it once instead of repeating it on each command. Byte-stream transports only (tcp/serial/udp); never applied to OSC or HTTP. Supports the same escape sequences and {config} substitution as send. A command can opt out with raw: true.",
        'since': '0.23.0',
    },
    'command_suffix': {
        'type': 'string',
        'doc': "Opt-in constant string appended to every command's send string (its terminator). Set it once so you don't type \\r on each command. Byte-stream transports only (tcp/serial/udp). Supports the same escape sequences and {config} substitution as send. A command can opt out with raw: true.",
        'since': '0.23.0',
    },
    'inline_protocol': {
        'type': 'boolean',
        'doc': 'Built-in generic devices only. When true, the device page shows a no-code Commands & Responses editor that stores commands/responses/state_variables in the device config and merges them into this driver at runtime. Community drivers ship their commands and responses in the driver file and should not set this.',
    },
    'ir_codes': {
        'type': 'boolean',
        'doc': 'Marks this as an IR code-set device (a device controlled by an infrared remote through an IR bridge). When true, the device page shows the IR Codes editor (learn / paste Pronto / type sendir / database search / test emit) and each code in the ir_codes map becomes a device command that emits through the bound bridge\'s IR port. A build-your-own IR device authors codes per-device; a community IR driver ships its code-set in default_config.ir_codes. Codes are stored as vendor-neutral Pronto hex plus a per-command repeat. Use transport "bridge" with this.',
        'since': '0.22.0',
    },
    'ports': {
        'type': 'array',
        'doc': 'TCP/UDP ports the device listens on (catalog metadata only).',
        'items': {
            'type': 'integer',
            'min': 1,
            'max': 65535,
        },
    },
    'protocols': {
        'type': 'array',
        'doc': 'Protocol names this driver speaks, e.g. ["pjlink"]. Helps discovery match devices to drivers.',
        'items': {
            'type': 'string',
            'min_len': 1,
        },
    },
    'simulated': {
        'type': 'boolean',
        'doc': 'True if the driver ships with simulator support (auto-gen or an explicit simulator: section).',
    },
    'verified': {
        'type': 'boolean',
        'doc': 'True once the driver has been validated against real hardware.',
    },
    'web_ui': {
        'type': ['boolean', 'string'],
        'doc': 'Controls the \'Open Web UI\' button. Leave unset for auto-detect: the platform finds a reachable web URL (HTTP devices from config, others from a port probe / discovery scan) and adds the button on its own. true forces it on (opens https://{host}); a string forces it on with that URL template (e.g. "http://{host}:8080") with {host}/{port}/{config_key} substitution; false forces it off.',
        'since': '0.24.0',
    },
    'min_platform_version': {
        'type': ['string', 'null'],
        'pattern': SEMVER_PATTERN,
        'doc': 'Minimum OpenAVC version required. Blocks install on older platforms missing a needed feature. Semantic version.',
    },
    'tags': {
        'type': 'array',
        'doc': 'Search/browse tags. Lowercase, hyphen-separated, alphanumeric.',
        'items': {
            'type': 'string',
            'pattern': TAG_PATTERN,
        },
    },
    'help': {
        'ref': 'helpBlock',
    },
    'deprecated': {
        'type': 'boolean',
        'doc': 'True if this driver is superseded. Requires replacement_id.',
    },
    'replacement_id': {
        'type': 'string',
        'doc': 'ID of the driver that replaces this one. Only valid when deprecated is true.',
    },
    'compatible_models': {
        'type': 'array',
        'doc': 'Specific device models this driver supports, with confidence levels.',
        'items': {
            'ref': 'compatibleModelsEntry',
        },
    },
    'default_config': {
        'type': 'object',
        'doc': 'Default values for config fields (e.g. host, port, poll_interval, inter_command_delay).',
    },
    'config_derived': {
        'type': 'object',
        'doc': 'Computed config values, each a template substituted from other config fields, e.g. {"ws": "/workspace/{workspace_id}"}. If any {field} the template references is empty or missing, the derived value is "" — so an optional prefixed address segment simply disappears. Computed once when the device connects and visible to every command address, on_connect entry, response, and poll query (just like a real config field). Lets one friendly field (e.g. a workspace id) drive both a bare and a prefixed address form without conditional logic in every command.',
        'extra': {
            'type': 'string',
        },
    },
    'config_schema': {
        'type': 'object',
        'doc': 'Per-device connection settings shown in the Add Device dialog. Keyed by config field name.',
        'extra': {
            'ref': 'configSchemaEntry',
        },
    },
    'device_settings': {
        'type': 'object',
        'doc': 'Configurable values that live on the device hardware (polled + writable). Keyed by setting name.',
        'extra': {
            'ref': 'deviceSettingEntry',
        },
    },
    'state_variables': {
        'type': 'object',
        'doc': 'Read-only state properties this driver exposes. Keyed by state variable name.',
        'extra': {
            'ref': 'stateVariableEntry',
        },
    },
    'child_entity_types': {
        'type': 'object',
        'doc': 'Sub-units this device manages (encoders, decoders, zones, presets). Keyed by child type name.',
        'extra': {
            'ref': 'childEntityType',
        },
        'prop_names': {
            'pattern': '^[^.*?\\[]+$',
            'doc': 'Child type names become state-key segments (device.<id>.<child_type>...) and feed fnmatch dispatch, so they must not contain dots or glob metacharacters (. * ? [).',
        },
    },
    'commands': {
        'type': 'object',
        'doc': 'Commands this driver can send. Keyed by command name.',
        'extra': {
            'ref': 'commandEntry',
        },
    },
    'quick_actions': {
        'type': 'array',
        'doc': 'Command ids promoted to one-click Quick Action buttons at the top of the device view. Sugar for actions of kind "command". Each id must name a declared command.',
        'items': {
            'type': 'string',
            'min_len': 1,
        },
    },
    'actions': {
        'type': 'array',
        'doc': 'Driver-declared actions promoted to buttons in the device view. kind:"command" promotes a declared command (runs online); kind:"setup" is an offline-capable provisioning wizard handled by the driver\'s run_setup_action().',
        'items': {
            'ref': 'actionEntry',
        },
    },
    'responses': {
        'type': 'array',
        'doc': 'Patterns for parsing device replies. Regex/OSC rules are checked in order and the first match wins; a json: true rule parses the whole JSON body and applies all of its field mappings, so a multi-field JSON reply fully populates.',
        'items': {
            'ref': 'responseEntry',
        },
    },
    'on_connect': {
        'type': 'array',
        'doc': 'Commands sent immediately after connect, before polling. Strings for TCP/serial/UDP; {address, args} mappings for OSC (args carry typed OSC values for a value-setting bring-up message); {each_child, send} templates expand to one query per registered child. Any mapping entry may add when: <config_field> to run only while that field is on, and a {send} entry may add query_for: <state_var> to name the state variable its reply reports.',
        'items': {
            'one_of': (
                {
                    'type': 'string',
                },
                {
                    'ref': 'eachChildQuery',
                },
                {
                    'ref': 'queryEntry',
                },
                {
                    'type': 'object',
                    'fields': {
                        'address': {
                            'type': 'string',
                        },
                        'args': {
                            'type': 'array',
                            'items': {
                                'ref': 'oscArg',
                            },
                        },
                        'when': {
                            'type': 'string',
                            'doc': 'Config field gating this entry: it runs only while that field is truthy. Must name a field declared in config_schema / default_config.',
                            'since': '0.23.0',
                        },
                    },
                    'required': ('address',),
                    'extra': False,
                },
            ),
        },
    },
    'polling': {
        'type': 'object',
        'doc': 'Periodic status query configuration. NOTE: a polling.interval key is inert and rejected by the catalog validator; set the cadence via default_config.poll_interval instead.',
        'extra': False,
        'fields': {
            'queries': {
                'type': 'array',
                'doc': 'Query strings (or command names) sent each poll cycle; {each_child, send} templates expand to one query per registered child; when: <config_field> gates an entry on a config field; query_for: <state_var> on a mapping entry names the state variable the reply reports (drives the auto-generated simulator). Cadence comes from default_config.poll_interval.',
                'items': {
                    'one_of': (
                        {
                            'type': 'string',
                        },
                        {
                            'ref': 'eachChildQuery',
                        },
                        {
                            'ref': 'queryEntry',
                        },
                    ),
                },
            },
        },
        'not_': {"required": ["interval"]},
    },
    'auth': {
        'ref': 'authBlock',
    },
    'liveness': {
        'ref': 'livenessBlock',
    },
    'push': {
        'ref': 'pushBlock',
    },
    'frame_parser': {
        'doc': "Framing for the control transport's inbound byte stream. Top level accepts length_prefix / fixed_length only; struct_frame is push-only (push.frame_parser, for tcp_listener dial-back frames).",
        'all_of': (
            {
                'ref': 'frameParser',
            },
            {
                'fields': {
                    'type': {
                        'enum': FRAME_PARSER_TYPES,
                    },
                },
            },
        ),
    },
    'send_frame': {
        'ref': 'sendFrame',
    },
    'simulator': {
        'ref': 'simulatorSection',
    },
    'discovery': {
        'ref': 'discoveryBlock',
    },
    'routing': {
        'ref': 'routingBlock',
    },
}

DEFS = {
    'enumValue': {
        'doc': 'One enum option: a bare wire value, or a {value, label} pair where the label is shown in pickers (and read in macros) while the value goes on the wire. A plain scalar means value == label.',
        'one_of': (
            {
                'type': ['string', 'number', 'boolean'],
            },
            {
                'type': 'object',
                'fields': {
                    'value': {
                        'type': ['string', 'number', 'boolean'],
                        'doc': 'The value sent on the wire.',
                    },
                    'label': {
                        'type': 'string',
                        'doc': 'Human-readable label shown in the picker. Defaults to the value.',
                    },
                },
                'required': ('value',),
                'extra': False,
            },
        ),
    },
    'helpBlock': {
        'type': 'object',
        'doc': 'Help text shown in the Add Device dialog and available to the AI assistant. overview + setup are required; connection is optional.',
        'fields': {
            'overview': {
                'type': 'string',
                'min_len': 1,
            },
            'setup': {
                'type': 'string',
                'min_len': 1,
            },
            'connection': {
                'type': 'string',
                'min_len': 1,
                'doc': "Optional short troubleshooting hint shown on the device's offline banner when it can't connect (e.g. a remote-access setting that must be enabled on the device first).",
            },
        },
        'required': ('overview', 'setup'),
        'extra': False,
    },
    'compatibleModelsEntry': {
        'type': 'object',
        'fields': {
            'manufacturer': {
                'type': 'string',
            },
            'models': {
                'type': 'array',
                'min_items': 1,
                'items': {
                    'type': 'string',
                    'min_len': 1,
                },
            },
            'confidence': {
                'type': 'string',
                'enum': CONFIDENCE_LEVELS,
            },
            'notes': {
                'type': ['string', 'null'],
            },
        },
        'required': ('manufacturer', 'models', 'confidence'),
        'extra': False,
    },
    'stateVariableEntry': {
        'type': 'object',
        'fields': {
            'type': {
                'type': 'string',
                'enum': VALUE_TYPES,
            },
            'label': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Human-readable label. Required for top-level state variables.',
            },
            'help': {
                'type': 'string',
            },
            'values': {
                'type': 'array',
                'doc': 'Allowed values for enum type.',
            },
            'min': {
                'type': 'number',
            },
            'max': {
                'type': 'number',
            },
            'step': {
                'type': 'number',
                'doc': "Value resolution for numeric types (e.g. 0.5 for a half-dB fader). Fills a matched control's Step in the UI Builder.",
            },
            'unit': {
                'type': 'string',
                'doc': 'Unit for numeric values (e.g. dB, Hz, %). Fills a matched control\'s Unit in the UI Builder; without it the UI falls back to parsing a trailing "(dB)" from the label.',
            },
            'control': {
                'type': 'boolean',
                'doc': "Marks a variable an integrator would bind a panel control to (a fader level, a mute). The UI Builder's value picker lists flagged variables first. Ordering only — unflagged variables stay pickable.",
            },
            'default': ANY,
            'cloud_priority': {
                'type': 'string',
                'enum': CLOUD_PRIORITIES,
            },
        },
        'required': ('label',),
        'extra': False,
    },
    'childStateVariableEntry': {
        'type': 'object',
        'doc': 'Child state variable. Same shape as device state variables, but label is not required (the platform injects online, label, offline_reason and offline_detail automatically). Declaring one of those four is allowed and keeps your definition; the rest are still added.',
        'fields': {
            'type': {
                'type': 'string',
                'enum': VALUE_TYPES,
            },
            'label': {
                'type': 'string',
            },
            'help': {
                'type': 'string',
            },
            'values': {
                'type': 'array',
            },
            'min': {
                'type': 'number',
            },
            'max': {
                'type': 'number',
            },
            'step': {
                'type': 'number',
                'doc': "Value resolution for numeric types (e.g. 0.5 for a half-dB fader). Fills a matched control's Step in the UI Builder.",
            },
            'unit': {
                'type': 'string',
                'doc': "Unit for numeric values (e.g. dB, Hz, %). Fills a matched control's Unit in the UI Builder's range-match prompt.",
            },
            'control': {
                'type': 'boolean',
                'doc': "Marks a settable control (not a read-only mirror or metadata). The UI Builder's value picker and the options_from: child_schema command cascade list flagged fields first.",
            },
            'default': ANY,
            'cloud_priority': {
                'type': 'string',
                'enum': CLOUD_PRIORITIES,
            },
        },
        'extra': False,
    },
    'childEntityType': {
        'type': 'object',
        'fields': {
            'label': {
                'type': 'string',
            },
            'label_plural': {
                'type': 'string',
            },
            'id_format': {
                'type': 'object',
                'fields': {
                    'type': {
                        'type': 'string',
                        'enum': CHILD_ID_TYPES,
                        'doc': 'Local child id type. min/max/pad_width apply to integer ids; string ids pair with an instances ids_from roster (letter-addressed matrix outputs) or a literal ids list (main buses st/m; platform 0.23.0+).',
                    },
                    'min': {
                        'type': 'integer',
                    },
                    'max': {
                        'type': 'integer',
                    },
                    'pad_width': {
                        'type': 'integer',
                        'min': 0,
                    },
                    'max_length': {
                        'type': 'integer',
                        'min': 1,
                        'doc': 'String ids only: maximum id length the runtime accepts at register_child time. Default 128.',
                    },
                },
                'extra': True,
            },
            'state_variables': {
                'type': 'object',
                'extra': {
                    'ref': 'childStateVariableEntry',
                },
            },
            'summary_fields': {
                'type': 'array',
                'items': {
                    'type': 'string',
                },
            },
            'label_field': {
                'type': 'string',
            },
            'dynamic': {
                'type': 'boolean',
                'doc': 'Mark a type whose children each carry their own control set, discovered at connect time rather than declared here (a DSP\'s user-built components, a hub\'s attached devices). Only a driver that registers children in code can populate one: register_child(..., schema=...) refuses a per-child schema unless the type declares this, and the {source: child_schema} param picker reads those schemas. A declarative roster has nothing to attach a schema to, so leave it off for YAML drivers.',
            },
            'instances': {
                'ref': 'childInstances',
                'since': '0.22.0',
            },
        },
        'extra': False,
    },
    'childInstances': {
        'type': 'object',
        'doc': 'Declarative roster — makes the child type real at runtime for YAML drivers. Exactly one of count (fixed IDs 1..N), count_from (an integer config field), ids_from (a comma-separated config field; sparse IDs), or ids (a literal fixed list; requires platform 0.23.0). An optional count_from_state names a device-reported state var (e.g. num_outputs) the roster follows once connected, with the chosen config source as the offline fallback. Children register on connect, reconcile as a want-set (also whenever count_from_state changes), and back the per-device Refresh from Device button.',
        'fields': {
            'count': {
                'type': 'integer',
                'min': 1,
                'doc': 'Fixed roster: registers integer IDs 1..count.',
            },
            'count_from': {
                'type': 'string',
                'doc': 'Name of an integer config field (config_schema / default_config) holding the count — lets one driver cover different frame sizes.',
            },
            'count_from_state': {
                'type': 'string',
                'doc': 'Name of a device-reported integer state variable (e.g. num_outputs) the roster follows once the device reports it, auto-sizing the roster to the hardware. Optional companion to count_from, which stays the offline fallback used before the device answers (a non-positive/absent value falls back to count_from).',
                'since': '0.23.0',
            },
            'ids_from': {
                'type': 'string',
                'doc': 'Name of a comma-separated config field listing the IDs (e.g. "1,2,4") — for sparse or installer-chosen rosters.',
            },
            'ids': {
                'type': 'array',
                'min_items': 1,
                'doc': 'Literal fixed roster (e.g. [st, m]) — for protocol-fixed string or sparse-integer IDs that no config field should have to carry.',
                'since': '0.23.0',
                'items': {
                    'one_of': (
                        {
                            'type': 'string',
                        },
                        {
                            'type': 'integer',
                        },
                    ),
                },
            },
            'label': {
                'type': 'string',
                'doc': 'Initial label template; {id} substitutes the local ID. A user-set project label always wins.',
            },
        },
        'extra': False,
        # Exactly one roster source, derived from the INSTANCE_SOURCES table.
        'one_of': tuple({'required': (s,)} for s in INSTANCE_SOURCES),
    },
    'childSetEntry': {
        'type': 'object',
        'doc': "Routes a matched response into one child's state. Regex rules: id is a capture ref ($1), a literal, or {group, map}; state values are capture refs or literals. OSC rules (platform 0.23.0+): id is {segment: N} (0-based index into the /-split address) or a literal; state values are {arg: N} positional-argument specs or literals. Values coerce by the child property's declared type.",
        'fields': {
            'type': {
                'type': 'string',
                'doc': 'A declared child_entity_types name.',
            },
            'id': {
                'doc': 'A capture ref ($1, regex rules), {segment: N} (OSC rules), a literal child id, or the map long form to translate a wire id (0-based channels, ST codes) to the local child id.',
                'one_of': (
                    {
                        'type': 'string',
                    },
                    {
                        'type': 'integer',
                    },
                    {
                        'type': 'object',
                        'fields': {
                            'group': {
                                'doc': 'Which capture group holds the wire id.',
                                'one_of': (
                                    {
                                        'type': 'integer',
                                        'min': 1,
                                    },
                                    {
                                        'type': 'string',
                                        'pattern': '^\\$\\d+$',
                                    },
                                ),
                            },
                            'map': {
                                'type': 'object',
                                'min_props': 1,
                                'doc': "Wire id -> local child id translation. A captured id the map doesn't cover skips the entry.",
                                'extra': {
                                    'one_of': (
                                        {
                                            'type': 'string',
                                        },
                                        {
                                            'type': 'integer',
                                        },
                                    ),
                                },
                            },
                        },
                        'required': ('group',),
                        'extra': False,
                    },
                    {
                        'type': 'object',
                        'fields': {
                            'segment': {
                                'type': 'integer',
                                'min': 0,
                                'doc': 'OSC rules only: which address segment holds the wire id (0-based over the /-split address — in /ch/07/mix/fader, segment 1 is "07").',
                            },
                            'map': {
                                'type': 'object',
                                'min_props': 1,
                                'doc': "Wire id -> local child id translation. A segment value the map doesn't cover skips the entry.",
                                'extra': {
                                    'one_of': (
                                        {
                                            'type': 'string',
                                        },
                                        {
                                            'type': 'integer',
                                        },
                                    ),
                                },
                            },
                        },
                        'required': ('segment',),
                        'extra': False,
                    },
                ),
            },
            'state': {
                'type': 'object',
                'min_props': 1,
                'doc': 'Child property -> capture ref or literal (regex rules); {arg: N[, map, type]}, {value: ...}, or literal (OSC rules).',
            },
        },
        'required': ('type', 'id', 'state'),
        'extra': False,
    },
    'eachChildQuery': {
        'type': 'object',
        'doc': 'Per-child query template: expands to one query per registered child of the named type, substituting {child_id} with the unpadded local ID.',
        'since': '0.22.0',
        'fields': {
            'each_child': {
                'type': 'string',
                'doc': 'A declared child_entity_types name (must have an instances: roster).',
            },
            'send': {
                'type': 'string',
                'pattern': '\\{child_id(:[^{}]*)?\\}',
                'doc': 'Query template; must contain {child_id} (a format spec like {child_id:02d} zero-pads — requires platform 0.23.0).',
            },
            'when': {
                'type': 'string',
                'doc': 'Config field gating this entry: it runs only while that field is truthy. Must name a field declared in config_schema / default_config.',
                'since': '0.23.0',
            },
            'query_for': {
                'type': 'string',
                'doc': "State variable each reply reports, from the child type's state_variables. Lets the auto-generated simulator answer the query from that child's own state instead of leaving it unmodeled.",
                'since': '0.24.0',
            },
        },
        'required': ('each_child', 'send'),
        'extra': False,
    },
    'queryEntry': {
        'type': 'object',
        'doc': 'A plain query in mapping form so it can carry extra semantics a bare string cannot: when: gates it on a config field (arm a chatty subscription behind an integrator checkbox), and query_for: names the state variable the reply reports so the auto-generated simulator answers it without name-guessing. At least one of the two must be present — a mapping with only send: is just a string query written the long way.',
        'fields': {
            'send': {
                'type': 'string',
                'doc': 'Query sent as authored — a raw protocol string on tcp/serial, a command name or path on http/udp.',
            },
            'when': {
                'type': 'string',
                'doc': 'Config field gating this entry. Must name a field declared in config_schema / default_config.',
                'since': '0.23.0',
            },
            'query_for': {
                'type': 'string',
                'doc': 'State variable the device reports in answer to this query. Must name a declared state variable.',
                'since': '0.24.0',
            },
        },
        'required': ('send',),
        'any_of': (
            {
                'required': ('when',),
            },
            {
                'required': ('query_for',),
            },
        ),
        'extra': False,
    },
    'paramEntry': {
        'type': 'object',
        'fields': {
            'type': {
                'type': 'string',
                'enum': PARAM_TYPES,
            },
            'required': {
                'type': 'boolean',
                'doc': "The command cannot run without this parameter. Enforced by the runtime at command time for every caller and both driver formats: omitting it (or passing null) is refused with \"'<command>': '<param>' is required\" before the command reaches the driver. A blank string still counts as supplied. Leave it off for a parameter the driver can do without; a value the driver should assume when the user gives none is a default, not a requirement. Enforced from platform 0.24.0 — before that it only drew the asterisk beside the input, and omitting one surfaced as a generic \"Failed to send command\".",
            },
            'label': {
                'type': 'string',
            },
            'help': {
                'type': 'string',
            },
            'values': {
                'type': 'array',
                'doc': 'Allowed values for an enum param. Each entry is a bare wire value or a {value, label} pair (label shown in the picker, value sent on the wire). The runtime accepts either the label or the value from any caller and normalizes to the value.',
                'items': {
                    'ref': 'enumValue',
                },
            },
            'child_type': {
                'type': 'string',
                'doc': 'For child_id type: the child_entity_types name this parameter targets.',
            },
            'options_state': {
                'type': 'string',
                'doc': 'Make this param a picker sourced from a device-relative state key. The IDE reads device.<id>.<options_state> (a JSON-encoded list of strings or {value,label} objects) and offers it as a dropdown. The driver publishes the enumerable set as a state variable.',
            },
            'options_from': {
                'type': 'object',
                'doc': "Cascade: source this param's options from a sibling param's chosen value.",
                'fields': {
                    'param': {
                        'type': 'string',
                        'doc': 'The sibling param whose value selects the option set.',
                    },
                    'source': {
                        'type': 'string',
                        'enum': PARAM_OPTIONS_FROM_SOURCES,
                        'doc': 'child_schema: offer the controls of the child picked in the sibling child_id param.',
                    },
                },
                'required': ('param', 'source'),
                'extra': False,
            },
            'type_from': {
                'type': 'object',
                'doc': "Make this param's input type follow the control chosen in a sibling cascade. The named param is itself an options_from child_schema cascade; this param then renders as that control's type (number+range, Yes/No, etc.).",
                'fields': {
                    'param': {
                        'type': 'string',
                        'doc': "The sibling options_from(child_schema) param whose chosen control supplies this param's type/min/max.",
                    },
                },
                'required': ('param',),
                'extra': False,
            },
            'min': {
                'type': 'number',
                'doc': 'Minimum for an integer/number param. Enforced by the runtime at command time; the IDE also flags violations while authoring.',
            },
            'max': {
                'type': 'number',
                'doc': 'Maximum for an integer/number param. Enforced by the runtime at command time; the IDE also flags violations while authoring.',
            },
            'decimals': {
                'type': 'integer',
                'min': 0,
                'doc': 'For a number param: round the value to this many decimal places on the wire (0 = whole number). An integer param always coerces to a whole number, so decimals is not needed there. For fixed-width or hex output, use a format spec on the placeholder instead (e.g. {level:03d}, {addr:02X}).',
            },
            'unit': {
                'type': 'string',
                'doc': 'Unit shown beside the input when the command is run (e.g. %, ms, dB). Display only — it is never sent on the wire, so the value the device receives is unchanged. Use it where the number alone is ambiguous; the same key on a state variable or device setting means the same thing.',
            },
            'pattern': {
                'type': 'string',
                'format': 'regex',
                'doc': "Regex a free-text value must fully match — a shape check for values that can't be enumerated (IP, hostname, fixed-length ID). The runtime validates it at command time; the IDE shows an inline error while authoring. Must compile and avoid catastrophic backtracking.",
            },
            'trim': {
                'type': 'boolean',
                'doc': 'For string params. Default true: leading/trailing whitespace is trimmed before the value goes on the wire. Set false to pass the value through verbatim — for raw payloads where edge whitespace is meaningful (typed text, verbatim titles, relay bodies whose trailing terminator is part of the protocol).',
                'since': '0.22.0',
            },
            'secret': {
                'type': 'boolean',
                'doc': 'Render the input masked when the command is run, for a param that carries a password, PIN or key. Presentation only: the value still goes on the wire as typed, and nothing is stored. The same key on a config_schema field means the same thing — set it wherever a credential would otherwise sit on screen in plain text.',
            },
            'default': ANY,
            'map': {
                'type': 'object',
                'min_props': 1,
                'doc': "Wire-value translation applied after validation, before substitution: the validated value (string-keyed) is replaced by the mapped wire value. Values not in the map pass through unchanged. Most useful on child_id params whose local ids differ from the protocol's channel numbers.",
                'extra': {
                    'one_of': (
                        {
                            'type': 'string',
                        },
                        {
                            'type': 'integer',
                        },
                        {
                            'type': 'number',
                        },
                    ),
                },
            },
            'description': {
                'type': 'string',
                'doc': 'Accepted alias for help — read either, write help.',
            },
        },
        'extra': False,
    },
    'oscArg': {
        'type': 'object',
        'fields': {
            'type': {
                'type': 'string',
                'enum': OSC_ARG_TYPES,
                'doc': 'OSC type tag: f=float32, i=int32, s=string, h=int64, d=float64, T=true, F=false, N=nil.',
            },
            'value': ANY,
        },
        'extra': False,
    },
    'commandEntry': {
        'type': 'object',
        'doc': 'A command must declare one of: send (TCP/serial/UDP), path/method (HTTP), or address (OSC).',
        'fields': {
            'label': {
                'type': 'string',
            },
            'help': {
                'type': 'string',
            },
            'send': {
                'type': 'string',
                'doc': 'Raw bytes to send (TCP/serial/UDP). {param} and {config} placeholders substituted at runtime.',
            },
            'raw': {
                'type': 'boolean',
                'doc': "Send this command's send string exactly as written, skipping the driver's command_prefix / command_suffix framing. Use it for the odd command that doesn't share the common frame.",
                'since': '0.23.0',
            },
            'method': {
                'type': 'string',
                'doc': 'HTTP method (GET/POST/PUT/DELETE). Default GET.',
            },
            'path': {
                'type': 'string',
                'doc': 'HTTP URL path.',
            },
            'body': {
                'type': 'string',
                'doc': 'HTTP request body. Parsed as JSON when valid, otherwise sent raw.',
            },
            'query_params': {
                'type': 'object',
                'doc': 'HTTP query parameters with {param} substitution.',
            },
            'headers': {
                'type': 'object',
                'doc': 'Per-request HTTP headers with {param} substitution.',
            },
            'address': {
                'type': 'string',
                'doc': 'OSC address pattern.',
            },
            'args': {
                'type': 'array',
                'doc': 'OSC typed arguments.',
                'items': {
                    'ref': 'oscArg',
                },
            },
            'params': {
                'type': 'object',
                'doc': 'Parameter definitions, keyed by {placeholder} name.',
                'extra': {
                    'ref': 'paramEntry',
                },
            },
            'sets': {
                'type': 'object',
                'doc': 'Declared state effect: the state variables this command sets on the device, e.g. {power: true} or {master_volume: "{level}"}. A "{param}" value takes that command parameter\'s value; anything else is a literal. The auto-generated simulator applies these instead of guessing from the command name; keys must name declared state variables. On a command with exactly one child_id parameter, a key may instead name a state variable of that parameter\'s child type — the effect then applies to the addressed child.',
                'since': '0.24.0',
                'extra': {
                    'type': ['string', 'integer', 'number', 'boolean'],
                },
            },
            'query_for': {
                'type': 'string',
                'doc': 'Declares this command as a status query: the device answers it by reporting the named state variable. The auto-generated simulator replies with that variable\'s current value instead of inferring one from the command name. Must name a declared state variable; on a command with exactly one child_id parameter it may instead name a state variable of that parameter\'s child type.',
                'since': '0.24.0',
            },
            'available_offline': {
                'type': 'boolean',
                'doc': "Let this command run while the device is offline (no live connection). Default false: the platform blocks commands to a disconnected device. Set true only for a command whose handler needs no connection — the canonical case is a Wake-on-LAN power_on that sends a magic packet instead of talking to the device over its (dead) control link. Param validation still runs; the driver's handler must not assume a live transport. When such a command is promoted to a Quick Action button, the button stays available regardless of connection state.",
                'since': '0.24.0',
            },
        },
        'extra': False,
        'any_of': (
            {
                'required': ('send',),
            },
            {
                'required': ('path',),
            },
            {
                'required': ('method',),
            },
            {
                'required': ('address',),
            },
        ),
    },
    'actionEntry': {
        'type': 'object',
        'doc': 'A promoted action. kind:"command" must resolve to a declared command (the command field, or the id).',
        'fields': {
            'id': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Unique action id within the driver.',
            },
            'kind': {
                'type': 'string',
                'enum': _YAML_ACTION_KINDS,
                'python_enum': ACTION_KINDS,
                'since_values': {'link': '0.24.0'},
                'doc': "'command' promotes a declared command (runs online via send_command). 'link' opens a URL (e.g. the device's web interface) in a new tab, client-side. Offline-capable 'setup' provisioning wizards require a Python driver with a run_setup_action() handler.",
            },
            'label': {
                'type': 'string',
                'doc': "Button label. Defaults to the promoted command's label, else the id.",
            },
            'icon': {
                'type': 'string',
                'doc': 'lucide icon name (kebab-case), e.g. power, shield, search, radar.',
            },
            'confirm': {
                'type': ['boolean', 'string'],
                'doc': 'Confirm before running. true for a generic prompt, or a custom message string.',
            },
            'availability': {
                'type': 'string',
                'enum': AVAILABILITIES,
                'doc': 'When the button shows. online (default) hides while offline; offline shows only while offline; always ignores connection state.',
            },
            'command': {
                'type': 'string',
                'doc': 'kind:"command" only — the command id to send. Defaults to the action id.',
            },
            'url': {
                'type': 'string',
                'min_len': 1,
                'doc': 'kind:"link" only — the URL to open. Supports {host}/{port}/{config_key} substitution from the device config. Defaults to https://{host}.',
            },
            'params': {
                'type': 'object',
                'doc': 'Input dialog fields. For kind:"command", defaults to the promoted command\'s params.',
                'extra': {
                    'ref': 'paramEntry',
                },
            },
            'visible_when': {
                'ref': 'visibleWhen',
            },
        },
        'required': ('id',),
        'extra': False,
    },
    'visibleWhen': {
        'doc': "Show the action only when a state condition holds. A single {key, operator, value} condition, or an {any:[...]} (OR) / {all:[...]} (AND) group. key may use $id for the device's own id.",
        'any_of': (
            {
                'ref': 'visibleWhenCondition',
            },
            {
                'type': 'object',
                'fields': {
                    'any': {
                        'type': 'array',
                        'min_items': 1,
                        'items': {
                            'ref': 'visibleWhenCondition',
                        },
                    },
                },
                'required': ('any',),
            },
            {
                'type': 'object',
                'fields': {
                    'all': {
                        'type': 'array',
                        'min_items': 1,
                        'items': {
                            'ref': 'visibleWhenCondition',
                        },
                    },
                },
                'required': ('all',),
            },
        ),
    },
    'visibleWhenCondition': {
        'type': 'object',
        'fields': {
            'key': {
                'type': 'string',
                'min_len': 1,
                'doc': 'State key compared against. May contain $id (replaced with the device id).',
            },
            'operator': {
                'type': 'string',
                'enum': VISIBLE_WHEN_OPERATORS,
                'doc': 'Comparison operator. Default eq.',
            },
            'value': {
                'doc': 'Value to compare against (not needed for truthy/falsy).',
            },
        },
        'required': ('key',),
        'extra': False,
    },
    'mappingEntry': {
        'type': 'object',
        'fields': {
            'group': {
                'type': 'integer',
                'doc': 'Regex capture group index (1-based; 0 is the whole match).',
            },
            'state': {
                'type': 'string',
                'doc': 'State variable to update.',
            },
            'type': {
                'type': 'string',
                'enum': ('string', 'integer', 'float', 'number', 'boolean'),
                'doc': 'Coercion applied to the captured value.',
            },
            'map': {
                'type': 'object',
                'doc': 'Lookup table translating raw captured values to friendly values.',
            },
            'value': ANY,
            'arg': {
                'type': 'integer',
                'doc': 'OSC argument index (for OSC responses).',
            },
            'json_path': {
                'type': 'string',
                'doc': 'Optional. The matched value is treated as a JSON string: it is parsed and this dot-separated path is walked (object keys and integer list indices, e.g. "data" or "data.name" or "data.0") to the value used before mapping/coercion. A path landing on an array or object yields its length (so a boolean type becomes "is non-empty?" and an integer type becomes the count). Omit for today\'s positional/raw behavior. Common for OSC devices whose replies carry the value inside a JSON string (e.g. QLab\'s /reply ... {"data": ...}).',
            },
        },
        'extra': False,
    },
    'responseEntry': {
        'type': 'object',
        'doc': 'A response must declare match (regex), address (OSC), or json: true (JSON-body).',
        'fields': {
            'json': {
                'type': 'boolean',
                'doc': 'When true, the whole reply body is parsed as a JSON object and every set/mappings key is read from it. Unlike regex responses, all json rules are applied to a body (not just the first match), so one JSON reply can populate many state variables. In this mode a set value is the JSON field to read (a string key, dot path allowed) or a {key, type, map} object, not a capture ref.',
            },
            'match': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Regex matched against incoming text. Capture groups extract values.',
            },
            'address': {
                'type': 'string',
                'pattern': '^/',
                'doc': 'OSC address pattern (must start with /). Supports fnmatch wildcards.',
            },
            'set': {
                'type': 'object',
                'doc': 'Shorthand mapping state variables to values. For regex responses, values are capture groups ("$1") or static values. For a json: true response, values are JSON field names (dot path allowed) or {key, type, map} specs.',
            },
            'mappings': {
                'type': 'array',
                'doc': 'Verbose mapping form, supporting type coercion and value maps.',
                'items': {
                    'ref': 'mappingEntry',
                },
            },
            'child_set': {
                'type': 'array',
                'min_items': 1,
                'doc': 'Route a matched response into child-entity state. Works on regex responses (captures) and OSC address rules (address segments + positional args; the OSC form needs platform 0.23.0) — not json: true. May coexist with set/mappings on the same entry.',
                'since': '0.22.0',
                'items': {
                    'ref': 'childSetEntry',
                },
            },
            'throttle': {
                'type': 'number',
                'emin': 0,
                'doc': 'Optional. After this rule matches and applies, further matches of the same rule are dropped for this many seconds (drop-style; each skipped frame is superseded by the next). For continuous push telemetry like audio level meters — do not throttle ordinary replies or state-change notices. Works on regex, json, and OSC rules.',
                'since': '0.23.0',
            },
            'require': {
                'type': ['string', 'array'],
                'min_len': 1,
                'doc': 'json: true rules only. Apply this rule only to bodies carrying the named JSON key (or every key in the list). Scopes a rule when different endpoints on one device reuse a field name with different meanings.',
                'since': '0.23.0',
                'items': {
                    'type': 'string',
                    'min_len': 1,
                },
            },
        },
        'extra': False,
        'any_of': (
            {
                'required': ('match',),
            },
            {
                'required': ('address',),
            },
            {
                'required': ('json',),
            },
        ),
    },
    'pushBlock': {
        'type': 'object',
        'doc': "Device-initiated push notifications arriving on a channel the platform opens (not the established control connection). type: multicast joins the device's notification group; incoming datagrams feed the driver's responses rules (split on the driver delimiter first) and are accepted only from the device's own address. type: sse holds GET path(s) open on the driver's own HTTP session with Accept: text/event-stream; each event's data block feeds the responses rules whole (pair with json: true rules for JSON payloads). type: tcp_listener opens a local TCP port the device dials back to after a registration command carrying {listener_port} tells it where; frames are parsed by the declared frame_parser, split on the driver delimiter, and accepted only from the device's own address. In every shape the subscription starts before on_connect (and any register command) runs, and stops on disconnect; a dropped SSE stream reconnects with exponential backoff. type: http_listener accepts the device's own HTTP POSTs (webhooks) on a callback path the platform assigns per device — send the URL to the device from an on_connect registration command, where the token {push_callback_url} substitutes it into command bodies, paths, and headers; request bodies feed the responses rules whole and are accepted only from the device's own address.",
        'since': '0.23.0',
        'fields': {
            'type': {
                'type': 'string',
                'enum': tuple(PUSH_TYPE_KEYS),
                'doc': 'Push channel kind. multicast = UDP group listen; sse = Server-Sent Events stream on the HTTP transport; tcp_listener = local port the device dials back to; http_listener = the device POSTs to a callback URL OpenAVC assigns (no other keys; use {push_callback_url} in the registration command).',
            },
            'group': {
                'type': ['string'],
                'doc': "multicast only: IPv4 multicast group (224.0.0.0 - 239.255.255.255) as a literal, or a {config_field} template naming a declared config_schema/default_config field (use a template when the device's notification target is user-configurable).",
            },
            'port': {
                'type': ['integer', 'string'],
                'doc': 'multicast: UDP port (1-65535) as an integer literal, or a {config_field} template string. tcp_listener: local inbound TCP port (0-65535, 0 = OS-assigned) or a {config_field} template.',
            },
            'path': {
                'type': ['string', 'array'],
                'doc': 'sse only: event-stream URL path on the device (literal starting with /, or a {config_field} template) - or a list of paths for devices that stream each resource separately.',
                'items': {
                    'type': 'string',
                },
            },
            'idle_timeout': {
                'type': 'number',
                'emin': 0,
                'doc': "sse only, optional: seconds of stream silence (keepalives included) before the connection is presumed dead and reopened. Set above the device's keepalive interval; omit to wait indefinitely.",
            },
            'frame_parser': {
                'ref': 'frameParser',
                'doc': "tcp_listener only, optional: framing for the pushed frames (struct_frame / length_prefix / fixed_length) - the dial-back channel is its own byte stream, independent of the control transport's framing. Omit to dispatch raw reads.",
            },
            'register': {
                'type': 'string',
                'doc': 'tcp_listener only, optional: name of the command that registers the dial-back target with the device (reference {listener_port} in its path/send string). Runs after the listener opens, and again on every reconnect.',
            },
            'unregister': {
                'type': 'string',
                'doc': "tcp_listener only, optional: name of the command that cancels the registration. Runs best-effort on graceful disconnect, freeing the device's receiver slot.",
            },
        },
        'required': ('type',),
        'extra': False,
        # Per-type key gating, derived from the push tables above: each type
        # requires its PUSH_TYPE_REQUIRED_KEYS and accepts only its
        # PUSH_TYPE_KEYS.
        'all_of': tuple(
            {
                'raw': {
                    "if": {"properties": {"type": {"const": t}}},
                    "then": {
                        "required": ["type", *PUSH_TYPE_REQUIRED_KEYS[t]],
                        "properties": {k: True for k in sorted(PUSH_TYPE_KEYS[t])},
                        "additionalProperties": False,
                    },
                },
            }
            for t in PUSH_TYPE_KEYS
        ),
    },
    'authBlock': {
        'type': 'object',
        'doc': 'Telnet-style login handshake. Only valid on tcp/serial transports (it reads a raw byte stream); declaring it on udp/http/osc is rejected at load time. username_prompt and password_prompt are required. All four regexes are checked for catastrophic backtracking since they run on raw pre-auth device bytes.',
        'since': '0.9.0',
        'fields': {
            'type': {
                'type': 'string',
                'enum': AUTH_TYPES,
                'doc': 'Handshake type. Only telnet_login is implemented and accepted.',
            },
            'username_prompt': {
                'type': 'string',
                'doc': "Regex matched against the device's username prompt. Required.",
            },
            'password_prompt': {
                'type': 'string',
                'doc': "Regex matched against the device's password prompt. Required.",
            },
            'success_pattern': {
                'type': 'string',
                'doc': 'Optional regex indicating successful login.',
            },
            'failure_pattern': {
                'type': 'string',
                'doc': 'Optional regex indicating rejected login.',
            },
            'username_field': {
                'type': 'string',
                'doc': 'Config field holding the username. Default "username".',
            },
            'password_field': {
                'type': 'string',
                'doc': 'Config field holding the password. Default "password".',
            },
            'skip_if_empty': {
                'type': 'boolean',
                'doc': 'Skip the handshake when the username config is empty. Default true.',
            },
            'timeout_seconds': {
                'type': 'number',
                'doc': 'Per-stage handshake timeout. Default 10.',
            },
            'line_ending': {
                'type': 'string',
                'doc': 'Line ending appended to credentials. Default "\\r\\n".',
            },
        },
        'required': ('username_prompt', 'password_prompt'),
        'extra': False,
    },
    'livenessBlock': {
        'type': 'object',
        'doc': 'Dead-link watchdog: send a probe every `interval` seconds and await a reply; after `max_failures` consecutive silent probes the platform drops the connection with a typed no_response fault and reconnects. Only valid on tcp/serial/udp/osc transports (rejected at load time on http, where polling already awaits every response, and on bridge, which owns no transport). Any inbound data during the wait window counts as alive unless `expect` narrows it. Needed for connectionless transports (UDP/OSC, where fire-and-forget polls never notice silence) and push-mostly TCP (no FIN when the device vanishes).',
        'since': '0.22.0',
        'fields': {
            'send': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Probe payload. Same conventions as polling queries: a raw protocol string with escape processing and {config} substitution (terminator included) for tcp/serial/udp, or an OSC address on osc.',
            },
            'expect': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Optional regex; only inbound data matching it satisfies the probe. Without it, any inbound data counts. Checked for catastrophic backtracking.',
            },
            'interval': {
                'type': 'number',
                'min': 1,
                'doc': 'Seconds between probes. Default 30.',
            },
            'timeout': {
                'type': 'number',
                'min': 0.1,
                'doc': 'Seconds to await a qualifying reply. Default 5.',
            },
            'max_failures': {
                'type': 'integer',
                'min': 1,
                'doc': 'Consecutive misses before the connection is dropped. Default 2.',
            },
            'args': {
                'type': 'array',
                'doc': 'OSC only: arguments sent with the probe address (same shape as command args).',
                'items': {
                    'ref': 'oscArg',
                },
            },
        },
        'required': ('send',),
        'extra': False,
    },
    'frameParser': {
        'type': 'object',
        'fields': {
            'type': {
                'type': 'string',
                'enum': PUSH_FRAME_PARSER_TYPES,
                'doc': 'Frame parser kind interpreted at runtime. struct_frame (fixed reserved regions around a length field and payload - common in device dial-back notification containers) requires platform 0.23.0.',
            },
            'header_size': {
                'type': 'integer',
                'enum': LENGTH_HEADER_SIZES,
                'doc': 'length_prefix: bytes holding the body length. Must be 1, 2, or 4. Default 2.',
            },
            'header_offset': {
                'type': 'integer',
                'doc': 'length_prefix: added to the length the header decodes to. Use a negative value (e.g. -header_size) when the length field counts the header bytes themselves, so only the body is read. Default 0.',
            },
            'include_header': {
                'type': 'boolean',
                'doc': 'length_prefix: whether the parsed frame includes the header bytes (true) or just the body (false). Default false.',
            },
            'length_offset': {
                'type': 'integer',
                'min': 0,
                'doc': "length_prefix: constant bytes before the length field (magic + fixed header fields) when the length isn't first on the wire. e.g. eISCP puts its 4-byte length at offset 8. Default 0.",
                'since': '0.23.0',
            },
            'header_extra': {
                'type': 'integer',
                'min': 0,
                'doc': "length_prefix: constant bytes after the length field, before the data (e.g. eISCP's version + reserved = 4). Full fixed header = length_offset + header_size + header_extra. Default 0.",
                'since': '0.23.0',
            },
            'length_endian': {
                'type': 'string',
                'enum': LENGTH_ENDIANS,
                'doc': 'length_prefix / struct_frame: byte order of the length field. Default big.',
                'since': '0.23.0',
            },
            'length': {
                'type': 'integer',
                'doc': 'fixed_length: frame size in bytes. Default 1.',
            },
            'header_reserve': {
                'type': 'integer',
                'min': 0,
                'doc': 'struct_frame: reserved bytes before the length field (discarded). Default 0.',
                'since': '0.23.0',
            },
            'length_size': {
                'type': 'integer',
                'enum': STRUCT_LENGTH_SIZES,
                'doc': 'struct_frame: bytes holding the length field. Default 2.',
                'since': '0.23.0',
            },
            'length_adjust': {
                'type': 'integer',
                'doc': 'struct_frame: added to the decoded length value to get the payload byte count, for length fields that include constant overhead (Panasonic camera containers count payload + 8, so -8). Default 0.',
                'since': '0.23.0',
            },
            'mid_reserve': {
                'type': 'integer',
                'min': 0,
                'doc': 'struct_frame: reserved bytes between the length field and the payload (discarded). Default 0.',
                'since': '0.23.0',
            },
            'trailer_reserve': {
                'type': 'integer',
                'min': 0,
                'doc': 'struct_frame: reserved bytes after the payload (discarded). Default 0.',
                'since': '0.23.0',
            },
        },
        'extra': False,
    },
    'sendFrame': {
        'type': 'object',
        'doc': 'Send-side packet framing — the send twin of frame_parser. Wraps every byte-stream command in a binary header whose data-length is computed per message (e.g. eISCP).',
        'since': '0.23.0',
        'fields': {
            'type': {
                'type': 'string',
                'enum': SEND_FRAME_TYPES,
                'doc': 'Send frame kind. Only length_prefix is supported.',
            },
            'header': {
                'type': 'string',
                'doc': 'Constant bytes emitted before the computed length field (magic + fixed header fields). Literal-escape string (\\r, \\n, \\xHH). For eISCP: "ISCP\\x00\\x00\\x00\\x10" (magic + header-size 16).',
            },
            'length_size': {
                'type': 'integer',
                'min': 1,
                'doc': 'Width in bytes of the computed data-length field. eISCP uses 4. Default 4.',
            },
            'length_endian': {
                'type': 'string',
                'enum': LENGTH_ENDIANS,
                'doc': 'Byte order of the length field. Default big.',
            },
            'after_length': {
                'type': 'string',
                'doc': 'Constant bytes emitted after the length field, before the command data (e.g. eISCP\'s version + reserved = "\\x01\\x00\\x00\\x00"). Literal-escape string.',
            },
        },
        'extra': False,
    },
    'configSchemaEntry': {
        'type': 'object',
        'fields': {
            'type': {
                'type': 'string',
                'enum': CONFIG_FIELD_TYPES,
                'since_values': {'table': '0.23.0'},
            },
            'label': {
                'type': 'string',
            },
            'default': ANY,
            'required': {
                'type': 'boolean',
            },
            'description': {
                'type': 'string',
            },
            'help': {
                'type': 'string',
            },
            'values': {
                'type': 'array',
                'doc': 'Allowed values for enum type.',
            },
            'row_label': {
                'type': 'string',
                'doc': 'Singular noun for a table field\'s Add-row button (e.g. "register").',
            },
            'columns': {
                'type': 'object',
                'doc': 'For type: table. Map of column id -> column spec (scalar field spec: type/label/required/default/values/min/max/help). Rendered by the device-page table editor.',
                'extra': {
                    'type': 'object',
                    'fields': {
                        'type': {
                            'type': 'string',
                            'enum': _COLUMN_TYPES,
                        },
                        'label': {
                            'type': 'string',
                        },
                        'help': {
                            'type': 'string',
                        },
                        'required': {
                            'type': 'boolean',
                        },
                        'default': ANY,
                        'values': {
                            'type': 'array',
                        },
                        'min': {
                            'type': 'number',
                        },
                        'max': {
                            'type': 'number',
                        },
                    },
                    'extra': True,
                },
            },
            'secret': {
                'type': 'boolean',
                'doc': (
                    'Render as a masked password field. A default may still be '
                    'given and is stored in plain text in the driver file, so '
                    'use it only for a factory default the manufacturer '
                    'publishes -- never a real site password.'
                ),
            },
            'advanced': {
                'type': 'boolean',
                'doc': (
                    'Collapse this field behind "Advanced" when adding or '
                    'editing the device. For a setting whose answer is not '
                    'known at the moment the device is added -- it depends on '
                    'something that gets configured later, or on a decision '
                    'nobody has made yet. It stays editable; it just stops '
                    'being a question asked before it can be answered. Default '
                    'false.'
                ),
            },
            'min': {
                'type': 'number',
            },
            'max': {
                'type': 'number',
            },
            'regex': {
                'type': 'string',
            },
        },
        'extra': False,
    },
    'deviceSettingEntry': {
        'type': 'object',
        'doc': 'type, label, help, and default are expected by the IDE; the runtime enforces only the write definition.',
        'fields': {
            'type': {
                'type': 'string',
                'enum': VALUE_TYPES,
            },
            'label': {
                'type': 'string',
            },
            'help': {
                'type': 'string',
            },
            'state_key': {
                'type': 'string',
                'doc': 'State variable providing the current value. Defaults to the setting key.',
            },
            'default': ANY,
            'setup': {
                'type': 'boolean',
                'doc': 'Prompt for this setting when adding the device to a project. Default false.',
            },
            'unique': {
                'type': 'boolean',
                'doc': 'Generate a non-clashing default (appends device ID). Default false.',
            },
            'values': {
                'type': 'array',
                'doc': 'Allowed values for an enum setting. Each entry is a bare wire value or a {value, label} pair (label shown in the editor, value written to the device). A write that resolves to nothing in the set is rejected.',
                'items': {
                    'ref': 'enumValue',
                },
            },
            'min': {
                'type': 'number',
            },
            'max': {
                'type': 'number',
            },
            'regex': {
                'type': 'string',
            },
            'write': {
                'ref': 'deviceSettingWrite',
            },
        },
        'extra': False,
    },
    'deviceSettingWrite': {
        'type': 'object',
        'fields': {
            'send': {
                'type': 'string',
                'doc': 'TCP/serial write string. {value} and config placeholders substituted.',
            },
            'method': {
                'type': 'string',
                'doc': 'HTTP method. Default POST.',
            },
            'path': {
                'type': 'string',
            },
            'body': {
                'type': 'string',
            },
            'headers': {
                'type': 'object',
            },
            'address': {
                'type': 'string',
                'doc': 'OSC address.',
            },
            'args': {
                'type': 'array',
                'items': {
                    'ref': 'oscArg',
                },
            },
        },
        'extra': False,
    },
    'simulatorSection': {
        'type': 'object',
        'fields': {
            'push_state': {
                'type': 'boolean',
                'doc': 'Push state changes to connected drivers, matching real device feedback behavior.',
            },
            'initial_state': {
                'type': 'object',
                'doc': 'Override initial state values.',
            },
            'delays': {
                'type': 'object',
                'doc': 'Response delays in seconds, e.g. {command_response: 0.05}.',
            },
            'error_modes': {
                'type': 'object',
                'doc': 'Named error behaviors selectable in the simulator UI.',
            },
            'controls': {
                'type': 'array',
                'doc': 'Declarative control widgets for the simulator UI.',
            },
            'state_machines': {
                'type': 'object',
                'extra': {
                    'type': 'object',
                    'fields': {
                        'states': {
                            'type': 'array',
                        },
                        'initial': {

                        },
                        'transitions': {

                        },
                    },
                    'required': ('states', 'initial', 'transitions'),
                    'extra': True,
                },
            },
            'command_handlers': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'fields': {
                        'receive': {
                            'type': 'string',
                            'doc': 'Regex pattern matched against incoming commands (template handler).',
                        },
                        'match': {
                            'type': 'string',
                            'doc': 'Regex pattern matched against incoming commands (template or script handler).',
                        },
                        'address': {
                            'type': 'string',
                            'doc': 'OSC address fnmatch pattern (OSC script handler).',
                        },
                        'respond': {
                            'type': 'string',
                            'doc': 'Response template with {1}, {state.key} placeholders.',
                        },
                        'set_state': {
                            'type': 'object',
                            'doc': 'State updates with template values.',
                        },
                        'handler': {
                            'type': 'string',
                            'doc': 'Inline Python handler body.',
                        },
                    },
                    'extra': True,
                },
            },
            'notifications': {
                'type': 'object',
                'doc': 'Unsolicited messages emitted on state change, keyed by state variable.',
            },
        },
        'extra': False,
    },
    'routingBlock': {
        'type': 'object',
        'doc': 'Optional. Says where this driver\'s routing lives, so a Matrix control can be set up from the device instead of typed from the manual. Purely additive: a driver that declares nothing is read by the same inference as before, and nothing about how the driver RUNS changes — this is answered when somebody draws a panel. Declare it when the guess is wrong or incomplete: a routing command that needs a fixed extra parameter the property name cannot supply (a signal/stream selector), a device that routes itself and has no destination child at all, or a driver carrying properties that merely read like routing (a clip indicator, a priority mode). Declaring it REPLACES the guess: these planes are the planes, in this order.',
        'since': '0.27.0',
        'fields': {
            'destination_child_type': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Default child type holding the things routed TO, in the driver\'s own words (output, decoder, rx, window, zone — or input, on an audio processor whose channels each select a source). Must name a declared child_entity_type. Omit it when the device IS the destination: an AVoIP endpoint that routes its own display has no destination child, and its matrix has one row.',
            },
            'source_child_type': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Default child type enumerating the things routed FROM (input, encoder, tx). Must name a declared child_entity_type. Omit it when the sources come from the routing command\'s own parameter values or its declared range, which is where they are read from otherwise.',
            },
            'command': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Default command that performs a route. Must name a declared command. Omit it only when every plane names its own.',
            },
            'destination_param': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Which parameter of that command takes the destination. Optional: without it the parameter is worked out from the command, which is right for anything named output/zone/decoder or typed to the destination child type. Say it when the name is unusual, and leave it out entirely when the command addresses the device itself and takes no destination.',
            },
            'source_param': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Which parameter of that command takes the source. Optional in the same way as destination_param.',
            },
            'params': {
                'type': 'object',
                'doc': 'Fixed extra parameters sent with every route on every plane, on top of the source and destination. Rarely needed at this level — a signal or stream selector belongs on the plane that selects it.',
                'extra': ANY,
            },
            'planes': {
                'type': 'array',
                'min_items': 1,
                'doc': 'The independent things this device routes, in the order they should be offered. Usually one. A decoder that routes video, audio, IR, RS-232, USB and CEC independently declares six, because each is a separate Matrix control watching a different property — one destination with six planes is not one destination.',
                'items': {
                    'ref': 'routingPlane',
                },
            },
        },
        'required': ('planes',),
        'extra': False,
    },
    'routingPlane': {
        'type': 'object',
        'doc': 'One independently routable thing. Every key the block declares can be overridden here, for a device whose planes do not all route the same way.',
        'fields': {
            'label': {
                'type': 'string',
                'min_len': 1,
                'doc': 'What to call this plane where somebody picks between them ("Video", "Extracted Audio", "RS-232"). Defaults to the routed property\'s own label.',
            },
            'route_property': {
                'type': 'string',
                'min_len': 1,
                'doc': 'The property reporting what is currently routed here — the one a crosspoint lights from. A state variable of the destination child type, or of the device itself when there is no destination child. This is the one field a plane cannot leave out: without it a Matrix can switch and can never show.',
            },
            'destination_child_type': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Overrides the block\'s destination child type for this plane.',
            },
            'source_child_type': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Overrides the block\'s source child type for this plane.',
            },
            'command': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Overrides the block\'s routing command for this plane. Needed wherever each plane has its own (route_video / route_audio / route_usb).',
            },
            'destination_param': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Overrides the block\'s destination parameter for this plane.',
            },
            'source_param': {
                'type': 'string',
                'min_len': 1,
                'doc': 'Overrides the block\'s source parameter for this plane.',
            },
            'params': {
                'type': 'object',
                'doc': 'Fixed extra parameters that select THIS plane, sent with every route on it — a decoder whose one command carries signal: VIDEO / AUDIO / IR, or an encoder whose one command carries stream: usb. This is what a plane name alone cannot supply, and a required parameter left unfilled is a command the device refuses.',
                'extra': ANY,
            },
        },
        'required': ('route_property',),
        'extra': False,
    },
    'discoveryBlock': {
        'type': 'object',
        'doc': 'Discovery fingerprints and hints. Unknown keys are rejected by the platform and the catalog validator.',
        'fields': {
            'requires': {
                'type': 'string',
                'pattern': '^[0-9]+(\\.[0-9]+)*([-+].*)?$',
                'doc': 'Minimum platform version whose discovery parser understands this block. Normally stamped by scripts/build_index.py at catalog emission (e.g. SSDP description filters need 0.23.0); platforms older than this skip the block cleanly. Rarely hand-authored.',
            },
            'mdns': {
                'ref': 'mdnsField',
            },
            'ssdp': {
                'ref': 'ssdpField',
            },
            'amx_ddp': {
                'ref': 'amxDdpField',
            },
            'tcp_probe': {
                'ref': 'tcpProbe',
            },
            'udp_probe': {
                'ref': 'udpProbe',
            },
            'python': {
                'ref': 'pythonProbe',
            },
            'oui': {
                'type': 'array',
                'doc': 'MAC OUI prefixes (vendor) used as a soft hint.',
                'items': {
                    'type': 'string',
                    'min_len': 1,
                },
            },
            'hostname': {
                'type': 'array',
                'doc': 'Hostname regex patterns used as a soft hint.',
                'items': {
                    'type': 'string',
                    'min_len': 1,
                },
            },
            'port_open': {
                'type': 'array',
                'doc': 'Open-port hints. Generic admin/web/SSH ports are disallowed.',
                'items': {
                    'type': 'integer',
                    'min': 1,
                    'max': 65535,
                    'not_': {"enum": list(DISALLOWED_OPEN_PORTS)},
                },
            },
            'manufacturer_alias': {
                'type': 'array',
                'doc': 'Vendor-string aliases matched against discovered banners/TXT records.',
                'items': {
                    'type': 'string',
                    'min_len': 1,
                },
            },
            'snmp_pen': {
                'type': 'integer',
                'min': 1,
                'doc': 'SNMP private enterprise number.',
            },
        },
        'extra': False,
    },
    'mdnsItem': {
        'any_of': (
            {
                'type': 'string',
                'min_len': 1,
            },
            {
                'type': 'object',
                'fields': {
                    'service': {
                        'type': 'string',
                        'min_len': 1,
                    },
                    'txt': {
                        'type': 'object',
                    },
                    'cross_vendor': {
                        'type': 'boolean',
                    },
                },
                'required': ('service',),
                'extra': False,
            },
        ),
    },
    'mdnsField': {
        'any_of': (
            {
                'ref': 'mdnsItem',
            },
            {
                'type': 'array',
                'items': {
                    'ref': 'mdnsItem',
                },
            },
        ),
    },
    'ssdpItem': {
        'any_of': (
            {
                'type': 'string',
                'min_len': 1,
            },
            {
                'type': 'object',
                'fields': {
                    'device_type': {
                        'type': 'string',
                        'min_len': 1,
                    },
                    'cross_vendor': {
                        'type': 'boolean',
                    },
                    'model': {
                        'type': 'string',
                        'min_len': 1,
                    },
                    'manufacturer': {
                        'type': 'string',
                        'min_len': 1,
                    },
                    'friendly_name': {
                        'type': 'string',
                        'min_len': 1,
                    },
                },
                'required': ('device_type',),
                'extra': False,
            },
        ),
    },
    'ssdpField': {
        'any_of': (
            {
                'ref': 'ssdpItem',
            },
            {
                'type': 'array',
                'items': {
                    'ref': 'ssdpItem',
                },
            },
        ),
    },
    'amxDdpItem': {
        'type': 'object',
        'fields': {
            'make': {
                'type': 'string',
                'min_len': 1,
            },
            'model_pattern': {
                'type': 'string',
            },
            'cross_vendor': {
                'type': 'boolean',
            },
        },
        'required': ('make',),
        'extra': False,
    },
    'amxDdpField': {
        'any_of': (
            {
                'ref': 'amxDdpItem',
            },
            {
                'type': 'array',
                'items': {
                    'ref': 'amxDdpItem',
                },
            },
        ),
    },
    'extractField': {
        'any_of': (
            {
                'type': 'string',
            },
            {
                'type': 'object',
                'fields': {
                    'regex': {
                        'type': 'string',
                        'min_len': 1,
                    },
                    'group': {
                        'type': 'integer',
                        'min': 0,
                    },
                },
                'required': ('regex',),
                'extra': True,
            },
        ),
    },
    'tcpProbe': {
        'type': 'object',
        'doc': 'TCP probe. Connect-only probes may omit send and expect. If it sends bytes, exactly one matcher is required.',
        'fields': {
            'port': {
                'type': 'integer',
                'min': 1,
                'max': 65535,
            },
            'send_hex': {
                'type': 'string',
            },
            'send_ascii': {
                'type': 'string',
            },
            'expect': {
                'type': 'string',
                'min_len': 1,
            },
            'expect_regex': {
                'type': 'string',
                'min_len': 1,
            },
            'expect_hex': {
                'type': 'string',
            },
            'cross_vendor': {
                'type': 'boolean',
            },
            'tls': {
                'type': 'boolean',
                'doc': 'Wrap the connection in TLS (no cert verification) before send/read, for an HTTPS-only device. Default false. tcp_probe only.',
            },
            'cert_subject': {
                'type': 'string',
                'min_len': 1,
                'doc': "Regex matched against the peer TLS certificate's subject (RFC4514 string + SAN DNS names) to identify a device by its self-signed cert's own name, e.g. 'CN=DM-NVX-'. Requires tls: true. A probe with only cert_subject (no send/expect) matches on the cert alone; `extract` rules also run against the subject. Platform >= 0.24.0.",
            },
            'timeout_ms': {
                'type': 'integer',
                'min': 1,
                'max': 10000,
            },
            'extract': {
                'type': 'object',
                'extra': {
                    'ref': 'extractField',
                },
            },
            'extract_manufacturer': {
                'type': 'string',
                'min_len': 1,
            },
        },
        'required': ('port',),
        'extra': False,
        'all_of': (
            {
                'not_': {"anyOf": [{"required": ["expect", "expect_regex"]}, {"required": ["expect", "expect_hex"]}, {"required": ["expect_regex", "expect_hex"]}]},
            },
            {
                'raw': {"if": {"anyOf": [{"required": ["send_hex"]}, {"required": ["send_ascii"]}]}, "then": {"anyOf": [{"required": ["expect"]}, {"required": ["expect_regex"]}, {"required": ["expect_hex"]}]}},
            },
        ),
        'not_': {"required": ["send_hex", "send_ascii"]},
    },
    'udpProbe': {
        'type': 'object',
        'doc': 'UDP probe. Must declare a send payload (send_hex or send_ascii) and exactly one matcher.',
        'fields': {
            'port': {
                'type': 'integer',
                'min': 1,
                'max': 65535,
            },
            'send_hex': {
                'type': 'string',
            },
            'send_ascii': {
                'type': 'string',
            },
            'expect': {
                'type': 'string',
                'min_len': 1,
            },
            'expect_regex': {
                'type': 'string',
                'min_len': 1,
            },
            'expect_hex': {
                'type': 'string',
            },
            'cross_vendor': {
                'type': 'boolean',
            },
            'timeout_ms': {
                'type': 'integer',
                'min': 1,
                'max': 10000,
            },
            'extract': {
                'type': 'object',
                'extra': {
                    'ref': 'extractField',
                },
            },
            'extract_manufacturer': {
                'type': 'string',
                'min_len': 1,
            },
        },
        'required': ('port',),
        'extra': False,
        'all_of': (
            {
                'any_of': (
                    {
                        'required': ('send_hex',),
                    },
                    {
                        'required': ('send_ascii',),
                    },
                ),
            },
            {
                'any_of': (
                    {
                        'required': ('expect',),
                    },
                    {
                        'required': ('expect_regex',),
                    },
                    {
                        'required': ('expect_hex',),
                    },
                ),
            },
            {
                'not_': {"anyOf": [{"required": ["expect", "expect_regex"]}, {"required": ["expect", "expect_hex"]}, {"required": ["expect_regex", "expect_hex"]}]},
            },
        ),
        'not_': {"required": ["send_hex", "send_ascii"]},
    },
    'pythonProbe': {
        'doc': 'Python discovery escape-hatch. Bare path string or {file, cross_vendor} mapping. The companion .py must ship alongside the driver.',
        'any_of': (
            {
                'type': 'string',
                'min_len': 1,
            },
            {
                'type': 'object',
                'fields': {
                    'file': {
                        'type': 'string',
                        'min_len': 1,
                    },
                    'cross_vendor': {
                        'type': 'boolean',
                    },
                },
                'required': ('file',),
                'extra': False,
            },
        ),
    },
}


def block_has_fixed_keys(node: dict) -> bool:
    """True when a block's declared ``fields`` are its whole legal key set.

    The unknown-key check asks this before flagging anything, so the Python
    validator, the Builder's generated key tables, and the published schema
    all draw the line in the same place.

    Two things disqualify a block. An open one (``extra`` is not False) takes
    any key by design. And one whose combinator branches narrow *properties*
    per value — ``push:``, whose legal keys depend on its ``type:`` — has no
    single key set to check against; the union would accept keys the contract
    refuses for that variant, so those blocks carry their own tailored check.
    Branches that only add ``required`` constrain presence, not vocabulary,
    and leave the key set fixed.
    """
    if node.get("extra") is not False or not isinstance(node.get("fields"), dict):
        return False
    for combinator in ("one_of", "any_of", "all_of"):
        for branch in node.get(combinator, ()):
            if set(branch) - {"required"}:
                return False
    return True


def node_doc(node: dict) -> str | None:
    """A registry node's documentation, its platform floor appended.

    The floor is written once as ``since`` and rendered from there into
    every published surface (both JSON Schemas, the Builder's types), so
    the sentence an author reads and the version the catalog enforces are
    the same annotation and cannot drift apart.
    """
    parts = [node["doc"]] if node.get("doc") else []
    if node.get("since"):
        parts.append(f"Requires platform {node['since']}.")
    for value, version in sorted((node.get("since_values") or {}).items()):
        parts.append(f'Value "{value}" requires platform {version}.')
    return " ".join(parts) or None


def parse_version(value: object) -> tuple[int, ...] | None:
    """Parse ``X.Y.Z`` into a comparable tuple; None if it isn't one.

    A pre-release or build suffix is dropped — a driver declaring
    ``0.23.0-rc1`` is asking for the 0.23.0 contract.
    """
    if not isinstance(value, str):
        return None
    core = re.split(r"[-+]", value.strip(), maxsplit=1)[0]
    parts = core.split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _fits(node: dict, value: object) -> bool:
    """Whether a combinator branch is plausibly the one this value satisfies.

    A ``one_of``/``any_of`` list holds alternative shapes, and only the one
    the value actually matches imposes its floors — an ``on_connect`` entry
    written as ``{send, when}`` is a plain query, not the per-child template
    that happens to sit in the same list. Deciding that exactly would mean
    running a JSON Schema validator here; requiredness plus the declared key
    set discriminates every branch the registry actually has, and a branch
    this misjudges is caught by the min() in the caller.
    """
    if "ref" in node:
        node = DEFS.get(node["ref"], {})
    declared = node.get("type")
    if declared is not None:
        names = (declared,) if isinstance(declared, str) else tuple(declared)
        actual = (
            "object" if isinstance(value, dict)
            else "array" if isinstance(value, list)
            else "boolean" if isinstance(value, bool)
            else "string" if isinstance(value, str)
            else "number" if isinstance(value, (int, float))
            else "null" if value is None
            else None
        )
        if actual not in names and not (
            actual == "number" and "integer" in names
        ):
            return False
    if not isinstance(value, dict):
        return True
    fields = node.get("fields")
    if isinstance(fields, dict):
        if any(key not in value for key in node.get("required", ())):
            return False
        if node.get("extra") is False and set(value) - set(fields):
            return False
    return True


def platform_requirements(driver_def: object) -> list[tuple[str, str]]:
    """Platform-version floors this definition's own fields impose.

    Returns ``(location, version)`` pairs — ``("commands.set_input.query_for",
    "0.24.0")`` — one per field or enum value carrying a ``since``. The
    highest of them is the lowest platform release that understands the
    driver; declaring ``min_platform_version`` below it ships a driver that
    installs on releases which silently ignore part of it.

    Only what the definition *declares* is visible here. A Python driver
    that reaches for a platform API in code, or builds part of its
    DRIVER_INFO at runtime, can need a newer platform than this can see —
    the result is a floor, never a ceiling.
    """
    found: dict[str, tuple[int, ...]] = {}
    text: dict[str, str] = {}

    def record(location: str, version: str) -> None:
        parsed = parse_version(version)
        if parsed is None:
            return
        # min(): where several branches fit, the definition only has to be
        # valid under one of them, so the lowest floor is the honest answer.
        if location not in found or parsed < found[location]:
            found[location] = parsed
            text[location] = version

    def walk(node: object, value: object, loc: str, depth: int = 0) -> None:
        if not isinstance(node, dict) or depth > 40:
            return
        if "ref" in node:
            if node.get("since"):
                record(loc, node["since"])
            walk(DEFS.get(node["ref"]), value, loc, depth + 1)
            return
        if node.get("since"):
            record(loc, node["since"])
        since_values = node.get("since_values")
        if since_values and isinstance(value, str) and value in since_values:
            record(f"{loc}: {value}", since_values[value])
        if isinstance(value, list):
            item = node.get("items")
            for index, entry in enumerate(value):
                walk(item, entry, f"{loc}[{index}]", depth + 1)
        elif isinstance(value, dict):
            fields = node.get("fields") or {}
            extra = node.get("extra")
            for key, sub in value.items():
                where = f"{loc}.{key}" if loc else str(key)
                if key in fields:
                    walk(fields[key], sub, where, depth + 1)
                elif isinstance(extra, dict):
                    walk(extra, sub, where, depth + 1)
        for combinator in ("one_of", "any_of", "all_of"):
            for branch in node.get(combinator, ()):
                if combinator == "all_of" or _fits(branch, value):
                    walk(branch, value, loc, depth + 1)

    if isinstance(driver_def, dict):
        for name, value in driver_def.items():
            if name in FIELDS:
                walk(FIELDS[name], value, name)
    # Highest floor first — the caller's answer is requirements[0] — and
    # within one floor by location, so a message that lists several reads
    # forwards rather than backwards.
    ordered = sorted((loc, text[loc]) for loc in found)
    ordered.sort(key=lambda item: found[item[0]], reverse=True)
    return ordered


def required_platform_version(driver_def: object) -> str | None:
    """The highest platform floor in a definition, or None if it has none."""
    requirements = platform_requirements(driver_def)
    return requirements[0][1] if requirements else None


# Cross-field rules that live at the document root (JSON Schema allOf):
# a deprecated driver must name its replacement, and naming a replacement
# implies deprecation.
CROSS_FIELD_RULES: tuple[dict, ...] = (
    {
        "if": {
            "properties": {"deprecated": {"const": True}},
            "required": ["deprecated"],
        },
        "then": {"required": ["replacement_id"]},
    },
    {
        "if": {"required": ["replacement_id"]},
        "then": {
            "properties": {"deprecated": {"const": True}},
            "required": ["deprecated"],
        },
    },
)
