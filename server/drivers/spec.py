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
runtime, and it must stay import-cycle-free (nothing in server/ is above
it).
"""
from __future__ import annotations

import ipaddress

# --- top-level contract ------------------------------------------------------

# Required top-level fields in a driver definition. Ordered (not a set) so
# missing-field errors always report in the same order run to run.
REQUIRED_FIELDS: tuple[str, ...] = ("id", "name", "transport")

# Transports a YAML (.avcdriver) definition may declare. "bridge" is the
# sentinel for a device that emits through a live bridge instance (an IR
# device on an emitter port) rather than dialing a host of its own.
YAML_TRANSPORTS: tuple[str, ...] = ("tcp", "serial", "udp", "http", "osc", "bridge")

# Transports only a Python driver can use — they need driver code (message
# routing hooks, session handling) that the declarative runtime doesn't model.
PYTHON_ONLY_TRANSPORTS: tuple[str, ...] = ("ssh", "mqtt")

# Driver ids with these prefixes are authoring templates (the built-in
# generic devices). They are exempt from discovery validation — templates
# don't participate in device matching.
GENERIC_ID_PREFIXES: tuple[str, ...] = ("generic_",)

# Value types for state variables, child state variables, and device settings.
VALUE_TYPES: tuple[str, ...] = ("string", "integer", "number", "boolean", "enum", "float")

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

# --- children ----------------------------------------------------------------

# child_entity_types.*.id_format.type values.
CHILD_ID_TYPES: tuple[str, ...] = ("integer", "string")

# The mutually exclusive roster sources an instances: block may declare.
INSTANCE_SOURCES: tuple[str, ...] = ("count", "count_from", "ids_from", "ids")

# --- OSC ---------------------------------------------------------------------

# OSC argument type tags the ConfigurableDriver runtime can encode from a YAML
# value. 'b' (blob/bytes) is intentionally excluded — there's no unambiguous way
# to express raw bytes in a YAML arg value, so it isn't a declarative type (the
# Driver Builder UI and avcdriver.schema.json omit it too). An unsupported tag
# is dropped silently at send time, yielding a malformed OSC message — catch it
# at load instead.
OSC_ARG_TYPES: frozenset[str] = frozenset({"f", "i", "s", "h", "d", "T", "F", "N"})

# --- command params ----------------------------------------------------------

# Sources a param's option list can cascade from (`options_from.source`).
PARAM_OPTIONS_FROM_SOURCES: frozenset[str] = frozenset({"child_schema"})

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
# evaluator (server/core/condition_eval.py) and the panel / Stream Deck (§38)
# JS evaluator so an action condition behaves identically everywhere.
VISIBLE_WHEN_OPERATORS: frozenset[str] = frozenset({
    "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy",
    "equals", "not_equals", "==", "!=", ">", "<", ">=", "<=",
})

# --- shared predicates -------------------------------------------------------


def is_multicast_group(value: str) -> bool:
    """True when ``value`` is an IPv4 multicast group literal (224.0.0.0/4)."""
    try:
        return ipaddress.IPv4Address(value).is_multicast
    except (ipaddress.AddressValueError, ValueError):
        return False
