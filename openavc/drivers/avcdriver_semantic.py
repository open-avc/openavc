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

import difflib as _difflib
import re
from typing import Any, Callable

from openavc.drivers.child_ids import declared_child_ids, page_starts
from openavc.drivers.condition_checks import condition_block_errors
from openavc.drivers.declared_names import (
    UNEVALUATED_KEY,
    child_writable_props,
    closest_name,
    config_field_names,
    declared_enum_values,
    declared_id_kind,
    did_you_mean,
    substitution_names,
)
from openavc.drivers.param_aid_checks import param_aid_errors
from openavc.drivers.push_block_checks import push_block_errors
from openavc.drivers.query_entry_checks import (
    FollowUp,
    QueryEntries,
    then_query_errors,
)
from openavc.drivers.response_rule_checks import response_rule_errors
from openavc.drivers.spec import (
    ACTION_KINDS,
    AUTH_TRANSPORTS,
    AUTH_TYPES,
    AVAILABILITIES,
    CHILD_ID_TYPES,
    CLOUD_PRIORITIES,
    CONNECTION_FIELDS,
    DEFAULT_LINE_GAP_S,
    DEFS,
    FIELDS,
    FILE_PARAM_EXCLUDED_KEYS,
    FILE_PARAM_MAX_BYTES,
    GENERIC_ID_PREFIXES,
    INSTANCE_SOURCES,
    LENGTH_ENDIANS,
    LENGTH_HEADER_SIZES,
    LINE_GAP_TRANSPORTS,
    LIVENESS_TRANSPORTS,
    OSC_ARG_TYPES as _OSC_ARG_TYPES,
    PORT_OPEN_TRANSPORTS,
    REQUIRED_FIELDS,
    SEND_FRAME_TYPES,
    SILENCE_CHECK_TRANSPORTS,
    VALUE_TYPES,
    YAML_TRANSPORTS,
    block_has_fixed_keys,
    is_file_command,
    is_secret_setting,
    parse_version,
    platform_requirements,
)
from openavc.utils.regex_safety import regex_safety_error as _regex_redos_error

# One ``{name}`` or ``{name:format_spec}`` token. Deliberately requires an
# identifier, so a literal JSON body — ``{"vol": {level}}`` — matches only on
# the real placeholder and never on the brace that opens the object.
_SUBSTITUTION_TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^{}]*)?\}")

# What each position is allowed to interpolate, phrased for the message.
_CMD_SOURCES = "no parameter of this command and no config field"
_CFG_SOURCES = "no config field (only config values substitute here)"


def _unresolved_substitutions(
    where: str, template: Any, resolvable: set[str], sources: str,
    effect: str = "it would be sent to the device as written",
) -> list[str]:
    """Report ``{name}`` tokens in one template that resolve to nothing.

    ``safe_substitute`` leaves an unknown placeholder verbatim rather than
    raising — deliberately, so a JSON body full of braces cannot crash a send.
    The cost is that a typo travels to the device as literal text and nothing
    says so: the command appears to do nothing, or a response pattern compiles
    to a regex that can never match. The runtime stays lenient; this is the
    authoring gate that makes the typo visible instead.

    ``sources`` names what this template is allowed to interpolate, because
    that differs by position — a command sees its own params and the config, a
    response pattern is matched long after any params are gone. ``effect`` is
    what the unresolved token would do there.
    """
    if not isinstance(template, str):
        return []
    errors: list[str] = []
    for match in _SUBSTITUTION_TOKEN.finditer(template):
        name = match.group(1)
        if name in resolvable:
            continue
        suggestion = closest_name(name, resolvable)
        hint = f" (did you mean '{suggestion}'?)" if suggestion else ""
        errors.append(
            f"{where}: '{{{name}}}' resolves to nothing{hint}. It names "
            f"{sources}, so {effect}"
        )
    return errors


def validate_substitutions(driver_def: dict[str, Any]) -> list[str]:
    """Every ``{name}`` a driver writes into a wire template must resolve.

    Covers the three places a placeholder reaches the device or the matcher:
    a command's send/path/body/address/headers, a response's ``match``
    pattern, and the query lists (polling, on_connect and each response
    rule's then_query).

    Mirrored into the community catalog's validator by the vendored copy of
    this module (``openavc-drivers/scripts/_vendor/``).
    """
    errors: list[str] = []
    config_names = substitution_names(driver_def)

    commands = driver_def.get("commands")
    if isinstance(commands, dict) and UNEVALUATED_KEY not in commands:
        for name, cmd in commands.items():
            if not isinstance(cmd, dict):
                continue
            params = cmd.get("params")
            # A computed params block is a SUBSET of the real one, so "not in
            # it" proves nothing — skip this command rather than report a
            # working driver as broken. Same rule as the actions check.
            if isinstance(params, dict) and UNEVALUATED_KEY in params:
                continue
            # A command that declares no params is making no claim about its
            # placeholders, and the runtime substitutes whatever the caller
            # passed: `send: "IN{input}"` with no params block works, because a
            # macro or script can supply `input` at call time. That is a loose
            # shape — an undeclared param gets no type coercion, no range gate
            # and no UI control — but it is not a wrong one, so this check has
            # no standing to call it an error.
            #
            # Once a command DOES declare params it has named its inputs, and a
            # token outside that set sitting beside a near-identical one inside
            # it is the typo this rule exists to catch.
            if not isinstance(params, dict) or not params:
                continue
            resolvable = config_names | {k for k in params if isinstance(k, str)}
            for field in ("send", "path", "body", "address"):
                errors.extend(
                    _unresolved_substitutions(
                        f"commands.{name}.{field}",
                        cmd.get(field),
                        resolvable,
                        _CMD_SOURCES,
                    )
                )
            headers = cmd.get("headers")
            if isinstance(headers, dict):
                for key, value in headers.items():
                    errors.extend(
                        _unresolved_substitutions(
                            f"commands.{name}.headers.{key}",
                            value,
                            resolvable,
                            _CMD_SOURCES,
                        )
                    )
            # A read-back query is filled from this command's parameters too;
            # one that names a command is that command's to fill.
            read_back = cmd.get("read_back")
            read_backs = (
                [read_back] if isinstance(read_back, str)
                else read_back if isinstance(read_back, list) else []
            )
            for i, entry in enumerate(read_backs):
                if not isinstance(entry, str) or entry in commands:
                    continue
                errors.extend(
                    _unresolved_substitutions(
                        f"commands.{name}.read_back"
                        + (f"[{i}]" if isinstance(read_back, list) else ""),
                        entry,
                        resolvable,
                        _CMD_SOURCES,
                    )
                )
            udp = cmd.get("udp")
            if isinstance(udp, dict):
                for field in ("payload", "host", "port"):
                    if isinstance(udp.get(field), str):
                        errors.extend(
                            _unresolved_substitutions(
                                f"commands.{name}.udp.{field}",
                                udp[field],
                                resolvable,
                                _CMD_SOURCES,
                            )
                        )

    # A response pattern is substituted against the config alone — it is
    # matched long after any command's params are gone.
    responses = driver_def.get("responses")
    if isinstance(responses, list):
        for i, response in enumerate(responses):
            if isinstance(response, dict):
                errors.extend(
                    _unresolved_substitutions(
                        f"responses[{i}].match",
                        response.get("match"),
                        config_names,
                        _CFG_SOURCES,
                        effect="the pattern would look for it as written",
                    )
                )

    polling = driver_def.get("polling")
    query_lists: list[tuple[str, Any]] = [
        ("polling.queries",
         polling.get("queries") if isinstance(polling, dict) else None),
        ("on_connect", driver_def.get("on_connect")),
    ]
    if isinstance(responses, list):
        query_lists += [
            (f"responses[{i}].then_query", response.get("then_query"))
            for i, response in enumerate(responses)
            if isinstance(response, dict)
        ]
    for where, entries in query_lists:
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            template = entry if isinstance(entry, str) else None
            if isinstance(entry, dict):
                template = entry.get("send") or entry.get("address")
            errors.extend(
                _unresolved_substitutions(
                    f"{where}[{i}]", template, config_names, _CFG_SOURCES
                )
            )

    return errors


_UDP_CONFIG_PLACEHOLDER = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _validate_udp_send(
    where: str, udp_def: Any, driver_def: dict[str, Any], errors: list[str]
) -> None:
    """The rules of a command's ``udp:`` block.

    Exactly one of payload / magic_packet; a payload needs a port (a number
    in range, or a {config_field} placeholder); a magic_packet names a state
    variable or config field that can hold the MAC. A block that passes here
    is one the runtime can always turn into a datagram, so the only thing
    left to fail at send time is a MAC the device has not reported yet.
    """
    if not isinstance(udp_def, dict):
        errors.append(f"{where}: 'udp' must be a mapping (host, port, payload or magic_packet)")
        return
    payload = udp_def.get("payload")
    mac_field = udp_def.get("magic_packet")
    if payload and mac_field:
        errors.append(
            f"{where}: 'udp' declares both 'payload' and 'magic_packet'; a "
            f"datagram is one or the other"
        )
    elif not payload and not mac_field:
        errors.append(
            f"{where}: 'udp' needs a 'payload' (the datagram) or a "
            f"'magic_packet' (the field holding the MAC to wake)"
        )
    host = udp_def.get("host")
    if host is not None and not isinstance(host, str):
        errors.append(f"{where}: 'udp.host' must be a string")
    port = udp_def.get("port")
    if port is None or port == "":
        if payload and not mac_field:
            errors.append(
                f"{where}: 'udp.port' is required with a payload (a number, or "
                f"a {{config_field}} placeholder)"
            )
    elif isinstance(port, bool) or not isinstance(port, (int, str)):
        errors.append(f"{where}: 'udp.port' must be a number or a {{config_field}} placeholder")
    elif isinstance(port, int):
        if not 1 <= port <= 65535:
            errors.append(f"{where}: 'udp.port' must be between 1 and 65535, got {port}")
    else:
        m = _UDP_CONFIG_PLACEHOLDER.match(port.strip())
        if m is None:
            if not port.strip().isdigit():
                errors.append(
                    f"{where}: 'udp.port' {port!r} is neither a number nor a "
                    f"{{config_field}} placeholder"
                )
            elif not 1 <= int(port.strip()) <= 65535:
                errors.append(f"{where}: 'udp.port' must be between 1 and 65535, got {port}")
        elif m.group(1) not in substitution_names(driver_def):
            errors.append(
                f"{where}: 'udp.port' names config field '{m.group(1)}', which "
                f"the driver does not declare"
            )
    if mac_field is not None:
        # A config field, so a display that has never connected can still be
        # woken by typing its MAC in (and so the Builder's dry run can build
        # the packet). A state variable of the same name is read first when
        # the device has reported one; on its own it is a wake that cannot
        # work until the first connect, which is the case the wake exists for.
        if not isinstance(mac_field, str) or not mac_field:
            errors.append(f"{where}: 'udp.magic_packet' must name a config field")
        elif mac_field not in substitution_names(driver_def):
            errors.append(
                f"{where}: 'udp.magic_packet' names '{mac_field}', which is not "
                f"a config field. Declare it in config_schema so the MAC can be "
                f"typed in; a state variable of the same name is read first "
                f"once the device reports one"
            )
    if "broadcast" in udp_def and not isinstance(udp_def.get("broadcast"), bool):
        errors.append(f"{where}: 'udp.broadcast' must be true or false")


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


class _TaggedErrors(list):
    """A real ``list[str]`` that also records *where in the definition* each
    entry came from.

    The validation walk below enters one section of a definition at a time and
    every one of its ~200 rules appends to the same ``errors`` list. So the
    walk stamps its current location on the list itself — ``errors.ctx =
    "commands.power_on"`` — instead of every rule site growing a path
    argument. A rule that fires deep inside a helper inherits the enclosing
    section's tag for free, because the helper appends to this same object.

    Two properties make this safe to drop into the existing walk:

    * It **is** a list, so ``validate_driver_definition`` keeps its
      ``list[str]`` contract and no existing caller changes — the loader, the
      save routes, ``build_index.py`` and the vendored catalog copy all see
      exactly what they saw before.
    * ``list.extend`` does not route through a subclass's ``append``, so both
      are overridden. A sub-list that carries its own (finer) tags keeps them;
      anything else inherits the current section.

    ``validate_driver_issues`` is the only reader — it pairs ``tags`` with the
    messages to give the Driver Builder something to anchor each issue to.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.ctx: str = ""
        self.tags: list[str] = [""] * len(self)

    def append(self, item: Any) -> None:
        super().append(item)
        self.tags.append(self.ctx)

    def extend(self, items: Any) -> None:
        sub_tags = getattr(items, "tags", None)
        items = list(items)
        super().extend(items)
        if sub_tags is not None and len(sub_tags) == len(items):
            self.tags.extend(sub_tags)
        else:
            self.tags.extend([self.ctx] * len(items))


def _extend_each(
    errors: _TaggedErrors, name: str, entries: Any,
    check: Callable[..., list[str]], **how: Any,
) -> None:
    """Ask ``check`` about each entry of a list, tagging what it says with the
    entry's place (``name[i]``). A list that is not a list has no entries."""
    errors.ctx = name
    if not isinstance(entries, list):
        return
    for i, entry in enumerate(entries):
        errors.ctx = f"{name}[{i}]"
        errors.extend(check(f"{name}[{i}]", entry, **how))


def unknown_key_errors(driver_def: dict[str, Any]) -> list[str]:
    """Report keys the driver contract doesn't declare, with a spelling hint.

    Driven entirely off the FIELDS/DEFS registry, so it can never disagree
    with the published schema: a block is checked exactly when the registry
    marks it closed (``extra: False``), which is exactly when the generator
    emits ``additionalProperties: false`` for it. Adding a field to the
    registry is all it takes for this to accept it.

    Why this is separate from ``validate_driver_definition``'s default path:
    the runtime loader drops a driver entirely when validation returns any
    error, and nothing gates a driver on ``min_platform_version`` at load. So
    a driver written for a newer platform would vanish at load and take its
    devices offline — worse than the typo. The loader logs these as warnings;
    only the authoring gates treat them as errors.
    """
    errors = _TaggedErrors()

    def walk(node: Any, value: Any, loc: str) -> None:
        if not isinstance(node, dict):
            return
        if "ref" in node:
            walk(DEFS.get(node["ref"]), value, loc)
            return
        if isinstance(value, list):
            item = node.get("items")
            if item is not None:
                for i, entry in enumerate(value):
                    walk(item, entry, f"{loc}[{i}]")
            return
        if not isinstance(value, dict):
            return

        fields = node.get("fields")
        extra = node.get("extra")

        # A block whose keys are author-chosen names (commands, state
        # variables, a config map) declares its value shape in `extra`. The
        # names are data, not contract keys — descend, don't judge.
        #
        # The one thing worth judging is whether the name is text at all.
        # YAML reads a bare `on`, `off`, `yes` or `no` as a boolean and bare
        # digits as a number, so a parameter an author reasonably calls `on`
        # arrives as the key `True`. Nothing downstream can substitute it,
        # and the failure surfaces far from the cause.
        if isinstance(extra, dict) and not fields:
            errors.ctx = loc
            for key, sub in value.items():
                if not isinstance(key, str):
                    where = f"{loc}: " if loc else ""
                    errors.append(
                        f"{where}name '{key}' is not text. YAML reads bare "
                        f"on/off/yes/no/true/false as booleans and bare "
                        f"digits as numbers. Put it in quotes."
                    )
                walk(extra, sub, f"{loc}.{key}" if loc else str(key))
            return

        if isinstance(fields, dict):
            # Flagged only where the registry says the key set is fixed, so
            # this and the schema always agree; an open or type-conditional
            # block is still descended, just never judged here.
            if block_has_fixed_keys(node):
                known = set(fields)
                # This walk already knows exactly where it is, so it tags its
                # own entries rather than inheriting the caller's section.
                errors.ctx = loc
                for key in sorted(set(value) - known):
                    close = _difflib.get_close_matches(
                        str(key), sorted(known), n=1, cutoff=0.7
                    )
                    hint = f" (did you mean '{close[0]}'?)" if close else ""
                    where = f"{loc}: " if loc else ""
                    errors.append(f"{where}unknown key '{key}'{hint}")
            for key, sub_node in fields.items():
                if key in value:
                    walk(sub_node, value[key], f"{loc}.{key}" if loc else str(key))

    walk({"fields": FIELDS, "extra": False}, driver_def, "")
    return errors


# How many offending fields a min_platform_version message names before it
# stops listing. Enough to see the pattern; the author only has to fix the
# declaration once regardless of how many fields forced it.
_MAX_NAMED_REQUIREMENTS = 4


def _requirement_summary(
    requirements: list[tuple[str, str]], floor: str
) -> tuple[str, str]:
    """The offending field list, and how to refer back to it in the sentence."""
    names = [where for where, version in requirements if version == floor]
    shown = ", ".join(names[:_MAX_NAMED_REQUIREMENTS])
    rest = len(names) - _MAX_NAMED_REQUIREMENTS
    if rest > 0:
        return f"{shown} (+{rest} more)", "those fields"
    return shown, "that field" if len(names) == 1 else "those fields"


def platform_version_errors(
    driver_def: dict[str, Any],
    *,
    require_declaration: bool = False,
    tier: str = "yaml",
) -> list[str]:
    """Check ``min_platform_version`` against the fields the driver uses.

    Every field the platform grew carries the release that grew it (``since``
    in the registry), so what a driver needs is computable rather than
    remembered. Declaring less than that is not a lint: the install gate
    honours the declaration, so the driver installs on a release that reads
    the file, ignores the fields it does not know, and runs wrong — a push
    subscription that never arms, a command whose framing is dropped.

    ``require_declaration`` additionally rejects a driver that uses a gated
    field and declares nothing at all. Off by default: a locally authored
    driver has no install gate to satisfy and is being written against the
    platform it is sitting on. On for publishing, where the field is what
    stops the driver reaching someone running an older release.

    ``tier`` is the driver's format ("yaml" or "python"); a few floors bind
    one format only (``spec.platform_requirements``).
    """
    if not isinstance(driver_def, dict):
        return []
    requirements = platform_requirements(driver_def, tier)
    if not requirements:
        return []
    floor = requirements[0][1]
    declared = driver_def.get("min_platform_version")

    named, them = _requirement_summary(requirements, floor)
    if declared is None or declared == "":
        if not require_declaration:
            return []
        return [
            f"min_platform_version is not declared, but this driver uses "
            f"{named}, which needs platform {floor}. Without it the driver "
            f"installs on older releases that ignore {them}. Add "
            f'min_platform_version: "{floor}".'
        ]

    parsed = parse_version(declared)
    if parsed is None:
        return [
            f"min_platform_version must be a version number like "
            f'"{floor}" (got {declared!r})'
        ]
    if parsed >= parse_version(floor):
        return []
    return [
        f'min_platform_version is "{declared}", but this driver uses {named}, '
        f'which needs platform {floor}. Raise it to "{floor}", or drop '
        f"{them} if the driver has to keep installing on {declared}."
    ]


def validate_driver_definition(
    driver_def: dict[str, Any],
    *,
    discovery_validator: Callable[[dict[str, Any]], list[str]] | None = None,
    strict: bool = False,
) -> list[str]:
    """
    Validate a driver definition.

    Returns a list of error strings. Empty list means valid.

    ``discovery_validator`` is the hook for the deep discovery-block check:
    the runtime loader passes the discovery engine's parser through it, and
    it runs exactly where the check always ran so error order is unchanged.
    When None, the discovery block is not validated.

    ``strict`` additionally rejects keys the contract doesn't declare (a
    misspelled ``state_varibles`` silently loads a driver with no state
    variables otherwise). Pass it where a driver is being *created* — the
    Builder's save, an import, the AI tool, catalog CI — and leave it off
    where one is being *loaded*: see ``unknown_key_errors``.

    The returned list is a ``_TaggedErrors`` — still a plain ``list[str]`` to
    every caller, but it also carries the section each message came from, so
    ``validate_driver_issues`` can hand the Driver Builder something to anchor
    each issue to. The ``errors.ctx = ...`` assignments below are what set it;
    each one marks the walk entering a new part of the definition.
    """
    errors = _TaggedErrors()

    # A malformed driver file can yaml-parse to a non-mapping, or carry
    # non-mapping `responses`/`commands`/`state_variables` sections (e.g. a
    # YAML list where a map was expected). Those used to raise uncaught
    # AttributeError/TypeError here, aborting the whole driver-loading pass and
    # taking every other driver down with the one bad file. Validate the shape
    # of each section before iterating it so a bad file is reported and skipped.
    if not isinstance(driver_def, dict):
        errors.append("Driver definition must be a mapping")
        return errors

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

    # What one response rule says about itself is checked in
    # response_rule_checks; its then_query and its child_list's paged reads
    # are checked with the query entries below. Each rule's messages are
    # tagged with its place in the list.
    errors.ctx = "responses"
    responses = driver_def.get("responses", [])
    if not isinstance(responses, list):
        errors.append("responses: must be a list")
        responses = []
    # `after_json:` only means something where a json rule can consume the
    # frame first, so the whole list decides whether it is inert.
    any_json_rule = any(
        isinstance(r, dict) and r.get("json") for r in responses
    )
    for i, resp in enumerate(responses):
        errors.ctx = f"responses[{i}]"
        errors.extend(
            response_rule_errors(i, resp, child_types_map, any_json_rule)
        )

    # Validate commands structure
    errors.ctx = "commands"
    commands = driver_def.get("commands", {})
    if not isinstance(commands, dict):
        errors.append("commands: must be a mapping")
        commands = {}
    declared_vars = driver_def.get("state_variables")
    declared_vars = declared_vars if isinstance(declared_vars, dict) else {}
    for cmd_name, cmd_def in commands.items():
        errors.ctx = f"commands.{cmd_name}"
        if not isinstance(cmd_def, dict):
            errors.append(f"Command '{cmd_name}': must be a dict")
            continue
        # A command sends one way: send (TCP/serial/UDP transport), path /
        # method (HTTP), address (OSC), udp (a datagram beside the transport)
        # or ir (a bridge's emitter). An empty send is the Builder's seed, not
        # a shape.
        has_send = cmd_def.get("send")
        has_http = cmd_def.get("path") or cmd_def.get("method")
        has_osc = cmd_def.get("address") is not None
        has_udp = cmd_def.get("udp") is not None
        has_ir = cmd_def.get("ir") is not None
        shapes = [
            name for name, present in (
                ("send", has_send), ("path/method", has_http),
                ("address", has_osc), ("udp", has_udp), ("ir", has_ir),
            ) if present
        ]
        if not shapes:
            errors.append(
                f"Command '{cmd_name}': must have 'send' (TCP/serial), "
                f"'path'/'method' (HTTP), 'address' (OSC), or 'udp' (a "
                f"datagram beside the main transport)"
            )
        elif len(shapes) > 1:
            errors.append(
                f"Command '{cmd_name}': declares {' and '.join(shapes)}, but a "
                f"command sends one way. Keep one of 'send' (TCP/serial), "
                f"'path'/'method' (HTTP), 'address' (OSC), or 'udp' (a "
                f"datagram beside the main transport)"
            )
        if has_osc:
            _validate_osc_args(f"Command '{cmd_name}'", cmd_def.get("args"), errors)
        if has_udp:
            _validate_udp_send(
                f"Command '{cmd_name}'", cmd_def.get("udp"), driver_def, errors
            )
        errors.extend(
            param_aid_errors(f"Command '{cmd_name}'", cmd_def.get("params"))
        )
        # Declared command semantics: `sets` / `query_for` drive the
        # auto-generated simulator, so a dangling reference would silently
        # declare nothing — enforce that every name they use exists. A command
        # addressed to exactly one child (a single child_id param) may name
        # that child type's state variables instead — the simulator routes
        # the effect to the addressed child's own state.
        cmd_params = cmd_def.get("params")
        cmd_params = cmd_params if isinstance(cmd_params, dict) else {}
        child_param_types = [
            p.get("child_type")
            for p in cmd_params.values()
            if isinstance(p, dict) and p.get("type") == "child_id"
        ]
        # Empty means "this command is not addressed to a single child type",
        # which is what the two error wordings below branch on. Once it IS
        # addressed the set is never empty (the reserved props are always in
        # it), so that branch still reads the way it always did.
        child_target_vars: set[str] = set()
        if len(child_param_types) == 1 and child_param_types[0] in child_types_map:
            _tdef = child_types_map.get(child_param_types[0])
            child_target_vars = child_writable_props(_tdef)
        sets_def = cmd_def.get("sets")
        if sets_def is not None and not isinstance(sets_def, dict):
            errors.append(f"Command '{cmd_name}': 'sets' must be a mapping")
        elif isinstance(sets_def, dict):
            for var_name, set_value in sets_def.items():
                if var_name not in declared_vars and var_name not in child_target_vars:
                    if child_target_vars:
                        errors.append(
                            f"Command '{cmd_name}': sets '{var_name}' which is "
                            f"not a declared state variable (device-level or "
                            f"of the addressed child type)"
                        )
                    else:
                        errors.append(
                            f"Command '{cmd_name}': sets '{var_name}' which is "
                            f"not a declared state variable"
                        )
                if isinstance(set_value, str) and (
                    "{" in set_value or "}" in set_value
                ):
                    ref = re.fullmatch(r"\{([^{}]+)\}", set_value)
                    if not ref or ref.group(1) not in cmd_params:
                        errors.append(
                            f"Command '{cmd_name}': sets.{var_name} value "
                            f"{set_value!r} must be a literal or a bare "
                            f"{{param}} reference to a declared parameter"
                        )
        query_for = cmd_def.get("query_for")
        if query_for is not None:
            if not isinstance(query_for, str) or not query_for:
                errors.append(
                    f"Command '{cmd_name}': 'query_for' must name a state "
                    f"variable"
                )
            elif query_for not in declared_vars and query_for not in child_target_vars:
                if child_target_vars:
                    errors.append(
                        f"Command '{cmd_name}': query_for '{query_for}' is not "
                        f"a declared state variable (device-level or of the "
                        f"addressed child type)"
                    )
                else:
                    errors.append(
                        f"Command '{cmd_name}': query_for '{query_for}' is not "
                        f"a declared state variable"
                    )
        errors.extend(command_confirm_errors(f"Command '{cmd_name}'", cmd_def))
        errors.extend(command_sleep_errors(cmd_name, cmd_def, commands))
        errors.extend(
            command_read_back_errors(
                cmd_name, cmd_def, commands, driver_def.get("transport")
            )
        )
        errors.extend(
            file_command_errors(f"Command '{cmd_name}'", cmd_def, python=False)
        )

    # References out of a command's params into child_entity_types. Shared
    # with the Python surface (a Python driver reaches the same function
    # through python_info), so a dangling child_type reads identically
    # whichever kind of driver declared it.
    errors.ctx = "commands"
    _child_ref_errors, _ = child_param_reference_errors(driver_def)
    errors.extend(_child_ref_errors)

    # Opt-in send-side command framing: a constant prefix/suffix wraps every
    # byte-stream command. Both must be strings when present.
    errors.ctx = ""
    for frame_key in ("command_prefix", "command_suffix"):
        errors.ctx = frame_key
        frame_val = driver_def.get(frame_key)
        if frame_val is not None and not isinstance(frame_val, str):
            errors.append(f"{frame_key}: must be a string")

    # Device settings: each entry must be writable (a `write:` block — the
    # runtime raises NotImplementedError without one) and its state_key must
    # name a declared state variable. A typo'd state_key used to load fine
    # and just show "(not set)" forever while writes silently fired.
    # (`declared_vars` is hoisted above the command loop.)
    valid_setting_types = set(VALUE_TYPES)
    raw_config_schema = driver_def.get("config_schema")
    if isinstance(raw_config_schema, dict):
        for field_name, field_def in raw_config_schema.items():
            if isinstance(field_def, dict) and "learned_from" in field_def:
                errors.ctx = f"config_schema.{field_name}"
                errors.extend(
                    config_learned_from_errors(field_name, field_def, declared_vars)
                )

    errors.ctx = "device_settings"
    device_settings = driver_def.get("device_settings")
    if device_settings is not None and not isinstance(device_settings, dict):
        errors.append("device_settings: must be a mapping")
    elif isinstance(device_settings, dict):
        for setting_name, setting_def in device_settings.items():
            errors.ctx = f"device_settings.{setting_name}"
            where = f"Device setting '{setting_name}'"
            if not isinstance(setting_def, dict):
                errors.append(f"{where}: must be a mapping")
                continue
            stype = setting_def.get("type", "")
            if stype and stype not in valid_setting_types:
                errors.append(f"{where}: unknown type '{stype}'")
            errors.extend(
                device_setting_state_key_errors(
                    where, setting_name, setting_def, declared_vars,
                )
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
                    f"{where}: missing 'write' block (send / path / address); "
                    f"a device setting must be writable"
                )
            elif write.get("address") is not None:
                # OSC writes share the command arg encoder — validate their
                # arg types too so a bad tag fails at load, not write time.
                _validate_osc_args(f"{where} write", write.get("args"), errors)
            errors.extend(device_setting_map_errors(where, setting_def))

    # Validate the Phase 6 ``discovery:`` block. Templates (generic_*)
    # are exempt — they don't participate in discovery. Phase 8 dropped
    # the strong-signal-required rule: a driver may declare any
    # combination of strong + soft signals, or none (load-time warning).
    # Signal collisions are caught later when the SignalIndex is built.
    errors.ctx = "discovery"
    driver_id = driver_def.get("id", "") or ""
    is_template = any(driver_id.startswith(p) for p in GENERIC_ID_PREFIXES)
    if not is_template and discovery_validator is not None:
        errors.extend(discovery_validator(driver_def))

    # The optional `push:` block (device-initiated notifications): its
    # checks live in push_block_checks, and every message opens with push:.
    errors.ctx = "push"
    errors.extend(push_block_errors(driver_def))

    # Validate the optional `auth:` login handshake block. The runtime swaps to
    # raw byte buffering and types credentials before any other traffic — so a
    # misdeclared block silently connects unauthenticated or mangles the
    # transport's data path instead of erroring. Enforce the requirements at
    # load time where the author can see them.
    errors.ctx = "auth"
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
    errors.ctx = "liveness"
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
                    "liveness: missing required 'send' (the probe payload: "
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
    errors.ctx = "actions"
    errors.extend(validate_actions(driver_def))
    # A YAML driver is interpreted by ConfigurableDriver, which has no
    # run_setup_action handler — so kind:"setup" can never do anything here.
    # Reject it at load time rather than render a button that errors on click.
    for i, entry in enumerate(driver_def.get("actions") or []):
        errors.ctx = f"actions[{i}]"
        if isinstance(entry, dict) and entry.get("kind") == "setup":
            errors.append(
                f"actions[{i}]: kind 'setup' requires a Python driver "
                f"(a run_setup_action handler); YAML drivers support "
                f"kind 'command' only"
            )
        if isinstance(entry, dict) and isinstance(entry.get("params"), dict):
            errors.extend(param_aid_errors(f"actions[{i}]", entry.get("params")))

    # Validate state_variables structure
    valid_types = set(VALUE_TYPES)
    errors.ctx = "state_variables"
    state_variables = driver_def.get("state_variables", {})
    if not isinstance(state_variables, dict):
        errors.append("state_variables: must be a mapping")
        state_variables = {}
    for var_name, var_def in state_variables.items():
        errors.ctx = f"state_variables.{var_name}"
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

    # scale / offset / unknown on state variables, child state variables and
    # command params: the shared rule (python_info runs it with python=True).
    errors.ctx = "state_variables"
    errors.extend(value_conversion_errors(driver_def))

    # Validate the optional frame_parser block (binary protocols). The runtime
    # LengthPrefixFrameParser only accepts header_size in {1, 2, 4} and
    # FixedLengthFrameParser needs a positive length; an out-of-range value
    # (authored by hand or by an older Driver Builder) would otherwise raise
    # in connect() and wedge the device in a permanent reconnect loop. Surface
    # it at load instead, with a clear message.
    errors.ctx = "frame_parser"
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
    errors.ctx = "send_frame"
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
    errors.ctx = "child_entity_types"
    child_types = driver_def.get("child_entity_types", {})
    if child_types and not isinstance(child_types, dict):
        errors.append("child_entity_types: must be a mapping")
    elif isinstance(child_types, dict):
        for child_type, type_def in child_types.items():
            errors.ctx = f"child_entity_types.{child_type}"
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
                    f"glob metacharacters ({', '.join(bad)}); they break state-change dispatch"
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
                    # 0 means "don't pad" — it is the runtime's own default
                    # (base.py renders the id bare when pad_width is falsy)
                    # and the field registry declares a minimum of 0. This
                    # rule used to demand 1 or more, which made every child
                    # type the Driver Builder creates unsaveable (it seeds
                    # pad_width 0) and would have made a hand-authored
                    # driver using the schema's own legal value disappear at
                    # load. Only a negative width is meaningless.
                    if pad is not None and (
                        isinstance(pad, bool) or not isinstance(pad, int) or pad < 0
                    ):
                        errors.append(
                            f"{where}.id_format: pad_width must be zero or a "
                            f"positive integer (got {pad!r})"
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
                    config_fields = config_field_names(driver_def)
                    id_fmt = type_def.get("id_format")
                    id_fmt = id_fmt if isinstance(id_fmt, dict) else {}
                    id_type = declared_id_kind(type_def)
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
                                f"ids (id_format.type is 'string'; use "
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
                                f"integer ids (id_format.type is 'string'; "
                                f"use 'ids_from')"
                            )
                    label = instances.get("label")
                    if label is not None and not isinstance(label, str):
                        errors.append(
                            f"{where}.instances: label must be a string"
                        )

    # The query entries: the poll's, the start-up's and each text rule's
    # then_query, checked in query_entry_checks against what the definition
    # declares, read once here.
    queries = QueryEntries(driver_def, responses)
    errors.ctx = "polling"
    polling_def = driver_def.get("polling")
    if polling_def is not None and not isinstance(polling_def, dict):
        errors.append("polling: must be a mapping")
    elif isinstance(polling_def, dict):
        # The runtime sources poll cadence from default_config.poll_interval
        # only; an interval here would silently do nothing.
        if "interval" in polling_def:
            errors.append(
                "polling.interval is not read by the runtime. Remove the "
                "interval: key and set default_config.poll_interval instead"
            )
        _extend_each(
            errors, "polling.queries", polling_def.get("queries"), queries.entry_errors
        )
        if "when_state" in polling_def:
            errors.ctx = "polling"
            errors.extend(
                queries.when_state_errors("polling", polling_def.get("when_state"))
            )
    _extend_each(
        errors, "on_connect", driver_def.get("on_connect"), queries.entry_errors,
        osc_form=True,
    )

    # A text rule's `then_query:` takes the entries a poll takes, plus
    # {child_id} in an entry sent once, naming the child the rule writes. Each
    # entry is checked against the rule first, then as any entry is.
    for i, resp in enumerate(responses):
        if not isinstance(resp, dict) or "then_query" not in resp:
            continue
        errors.ctx = f"responses[{i}]"
        refused = then_query_errors(i, resp)
        errors.extend(refused)
        if refused:
            continue
        follow = FollowUp(resp)
        listed = f"responses[{i}].then_query"
        _extend_each(errors, listed, resp["then_query"], follow.reread_errors)
        _extend_each(
            errors, listed, resp["then_query"], queries.entry_errors, follow_up=follow
        )

    # Validate the optional `routing:` block (where this driver's routing
    # lives, so a Matrix can be set up from the device). Every rule here is a
    # cross-reference: a plane naming a child type, a property or a command the
    # driver does not declare produces a Matrix that draws nothing or sends a
    # command the device refuses, and it does it at the moment somebody is
    # commissioning a space rather than here.
    errors.ctx = "routing"
    errors.extend(routing_block_errors(driver_def))

    # Every {name} the driver writes into a wire template has to resolve.
    # Sits with the other cross-reference rules in spirit: it is the same
    # question — does this name point at something the driver declares — asked
    # of the substitution surface rather than of commands and child types.
    #
    # These messages already open with their own location, and they land in
    # several sections at once (a command, a response, the polling list), so
    # each is tagged from its own prefix rather than sharing one section tag —
    # otherwise the Builder would anchor a polling issue to a command.
    for message in validate_substitutions(driver_def):
        substitution_loc = message.split(":", 1)[0]
        errors.ctx = substitution_loc
        errors.append(message)

    # Strict mode: reject keys the contract doesn't declare. Appended last so
    # every existing error keeps its position. Only the authoring gates ask for
    # this — see unknown_key_errors for why loading stays lenient.
    if strict:
        errors.extend(unknown_key_errors(driver_def))
        errors.ctx = "min_platform_version"
        errors.extend(platform_version_errors(driver_def))

    return errors


#: The keys a plane may inherit from the block above it.
_ROUTING_INHERITED: tuple[str, ...] = (
    "destination_child_type",
    "source_child_type",
    "command",
    "destination_param",
    "source_param",
)


def routing_block_errors(driver_def: dict[str, Any]) -> list[str]:
    """Check the optional ``routing:`` block against what the driver declares.

    The block is a set of names pointing at other parts of the same file —
    a child type, a property on it, a command, that command's parameters — and
    every one of them is a place a typo hides. It hides *well*: nothing here
    runs when the driver runs. A wrong `route_property` is a Matrix whose
    crosspoints never light, a wrong `command` is a tap that sends nothing, and
    both surface weeks later in a commissioned space rather than at the moment
    the driver was written.

    Declaring the block REPLACES the guess (``matrix_inference``), so an
    unusable declaration is worse than none: it silently takes the place of an
    inference that would have worked.
    """
    block = driver_def.get("routing")
    if block is None:
        return []
    errors: list[str] = []
    if not isinstance(block, dict):
        return ["routing: must be a mapping with a 'planes' list"]

    child_types = driver_def.get("child_entity_types")
    child_types = child_types if isinstance(child_types, dict) else {}
    commands = driver_def.get("commands")
    commands = commands if isinstance(commands, dict) else {}
    device_vars = driver_def.get("state_variables")
    device_vars = device_vars if isinstance(device_vars, dict) else {}

    def child_state_vars(child_type: str) -> dict[str, Any] | None:
        schema = child_types.get(child_type)
        if not isinstance(schema, dict):
            return None
        declared = schema.get("state_variables")
        # A Python driver that builds its child properties at construction time
        # reads back as UNEVALUATED here. That is not a missing property, so
        # the check has nothing to say about it.
        if declared == UNEVALUATED_KEY:
            return None
        return declared if isinstance(declared, dict) else {}

    def command_params(command: str) -> dict[str, Any] | None:
        entry = commands.get(command)
        if not isinstance(entry, dict):
            return None
        params = entry.get("params")
        if params == UNEVALUATED_KEY:
            return None
        return params if isinstance(params, dict) else {}

    planes = block.get("planes")
    if not isinstance(planes, list) or not planes:
        errors.append(
            "routing: needs a non-empty 'planes' list, the independent things "
            "this device routes (usually one; a decoder that routes video, "
            "audio and USB separately declares three)"
        )
        planes = []

    for key in _ROUTING_INHERITED:
        value = block.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            errors.append(f"routing.{key}: must be a non-empty string")

    seen: dict[tuple[str, str, str, tuple[tuple[str, str], ...]], int] = {}
    for index, raw in enumerate(planes):
        where = f"routing.planes[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        plane = {**{k: block.get(k) for k in _ROUTING_INHERITED}, **raw}

        route_property = raw.get("route_property")
        if not isinstance(route_property, str) or not route_property:
            errors.append(
                f"{where}: missing required 'route_property' (the property "
                f"reporting what is routed here; without it a Matrix can "
                f"switch and can never show what is on)"
            )
            route_property = ""

        dest_type = plane.get("destination_child_type")
        if dest_type is not None and not isinstance(dest_type, str):
            dest_type = None
        if dest_type and dest_type not in child_types:
            errors.append(
                f"{where}: destination_child_type '{dest_type}' is not a "
                f"declared child_entity_type"
                + did_you_mean(dest_type, set(child_types))
            )
        elif route_property:
            # Where the routed property has to live: on the destination child
            # when there is one, on the device itself when the device IS the
            # destination.
            if dest_type:
                declared_vars = child_state_vars(dest_type)
                origin = f"child type '{dest_type}'"
            else:
                declared_vars = device_vars
                origin = "this driver"
            if declared_vars is not None and route_property not in declared_vars:
                errors.append(
                    f"{where}: route_property '{route_property}' is not a "
                    f"state variable of {origin}"
                    + did_you_mean(route_property, set(declared_vars))
                )

        source_type = plane.get("source_child_type")
        if isinstance(source_type, str) and source_type and source_type not in child_types:
            errors.append(
                f"{where}: source_child_type '{source_type}' is not a "
                f"declared child_entity_type"
                + did_you_mean(source_type, set(child_types))
            )

        command = plane.get("command")
        if command is not None and not isinstance(command, str):
            command = None
        params: dict[str, Any] | None = None
        if command:
            if command not in commands:
                errors.append(
                    f"{where}: command '{command}' is not a declared command"
                    + did_you_mean(command, set(commands))
                )
            else:
                params = command_params(command)

        # A parameter name, or a fixed extra, without a command to send it on.
        # Nothing would carry it, so it is a line that reads as configured and
        # does nothing.
        named = [
            key for key in ("destination_param", "source_param", "params")
            if raw.get(key) is not None or (key != "params" and block.get(key) is not None)
        ]
        if not command and raw.get("params") is not None:
            errors.append(
                f"{where}: 'params' needs a 'command' to send them on. "
                f"Declare the routing command here or on the routing block"
            )
        elif not command and any(k in named for k in ("destination_param", "source_param")):
            errors.append(
                f"{where}: names a routing parameter but no 'command'. "
                f"Declare the routing command here or on the routing block"
            )

        taken: list[str] = []
        for key in ("destination_param", "source_param"):
            value = plane.get(key)
            if not isinstance(value, str) or not value:
                continue
            taken.append(value)
            if params is not None and value not in params:
                errors.append(
                    f"{where}: {key} '{value}' is not a parameter of "
                    f"command '{command}'" + did_you_mean(value, set(params))
                )

        extra = raw.get("params")
        if extra is not None and not isinstance(extra, dict):
            errors.append(f"{where}: 'params' must be a mapping of parameter name to value")
        elif isinstance(extra, dict):
            for name in extra:
                if name in taken:
                    errors.append(
                        f"{where}: params.{name} is already the route's "
                        f"{'destination' if name == plane.get('destination_param') else 'source'}"
                        f"; a fixed value here would overwrite what the panel routes"
                    )
                elif params is not None and name not in params:
                    errors.append(
                        f"{where}: params.{name} is not a parameter of "
                        f"command '{command}'" + did_you_mean(name, set(params))
                    )

        if route_property:
            # What makes two planes the same matrix is showing one property AND
            # routing it the same way. Two that share a property and send
            # different fixed params are two different controls: an AVoIP decoder
            # that switches every stream with one command and tells them apart
            # with `stream` / `signal` also accepts a combined value on that
            # parameter, so "All streams" and "Video" both report `source_video`
            # and route nothing alike. Keying on the property alone refused the
            # combined plane -- which is the one an integrator wants first, since
            # a video-only matrix on that gear leaves audio, USB and serial on
            # the previous source with nothing to say so.
            fixed = raw.get("params")
            key = (
                dest_type or "",
                route_property,
                str(plane.get("command") or ""),
                tuple(sorted(
                    (str(k), str(v)) for k, v in fixed.items()
                )) if isinstance(fixed, dict) else (),
            )
            if key in seen:
                errors.append(
                    f"{where}: already declared by routing.planes[{seen[key]}] "
                    f"; two planes watching one property and routing it the same "
                    f"way are the same matrix twice"
                )
            else:
                seen[key] = index

    return errors


def validate_driver_warnings(driver_def: dict[str, Any]) -> list[str]:
    """Rules an author should see but that must never block anything.

    Deliberately separate from ``validate_driver_definition``: everything that
    function returns is an *error* to every one of its callers — the loader
    drops the driver, the save routes refuse it, catalog CI fails the build.
    A rule that only wants to say "are you sure?" has no home there.

    ``validate_driver_issues`` is what carries these onward, so an editor can
    show them beside the errors without treating them as one.

    Same tagging as the error walk: ``warnings.ctx`` marks where each message
    belongs.
    """
    warnings = _TaggedErrors()
    if not isinstance(driver_def, dict):
        return warnings

    def has_value(value: Any) -> bool:
        return value is not None and value != ""

    # A masked config field with a default stores that default in plain text
    # inside the driver file. Worth a nudge, never a refusal: the value an
    # author puts there is nearly always the factory default the manufacturer
    # prints in its own manual — a starting point for whoever commissions the
    # device rather than a secret. The rule worth having is "don't ship a site
    # password", and a driver file cannot tell the two apart, so the nudge is
    # the honest ceiling. (The same stance is declared on the `secret` field
    # in spec.py, which is what the published schema and every editor read.)
    config_schema = driver_def.get("config_schema")
    raw_defaults = driver_def.get("default_config")
    defaults = raw_defaults if isinstance(raw_defaults, dict) else {}
    if isinstance(config_schema, dict):
        for field_name, field_def in config_schema.items():
            if not isinstance(field_def, dict):
                continue
            if not is_secret_setting(field_name, field_def):
                continue
            if has_value(field_def.get("default")) or has_value(
                defaults.get(field_name)
            ):
                warnings.ctx = f"config_schema.{field_name}"
                warnings.append(
                    f"Config field '{field_name}' is masked but has a default "
                    f"value, which is stored in plain text inside the driver "
                    f"file. That is fine for a published factory default, but "
                    f"never put a real site password here."
                )

    # A `port_open` hint is matched against a TCP connect sweep, so on a driver
    # that only speaks UDP or OSC it can never fire. The existing rule on this
    # field only rejects ports that are too *generic* — nothing notices a port
    # on the wrong protocol, so the hint ships looking declared and does
    # nothing. A nudge rather than a refusal because the device may genuinely
    # also listen on TCP; it is the driver that has no way to be found that way.
    discovery = driver_def.get("discovery")
    if isinstance(discovery, dict) and discovery.get("port_open"):
        declared = [driver_def.get("transport")]
        extra = driver_def.get("transports")
        if isinstance(extra, list):
            declared.extend(extra)
        transports = {t for t in declared if isinstance(t, str) and t}
        if transports and not transports & set(PORT_OPEN_TRANSPORTS):
            warnings.ctx = "discovery"
            warnings.append(
                f"discovery.port_open is matched against a TCP port scan, but "
                f"this driver's transport is "
                f"{', '.join(sorted(transports))}, so the hint can never fire. "
                f"Drop it, or add a transport that listens on TCP."
            )

    # A command parameter with no label is named on every form by its key
    # made readable (openavc/drivers/param_labels.py), which often says too
    # little ("Value", "Src"). A nudge per field, never a refusal: the field
    # works either way.
    commands = driver_def.get("commands")
    if isinstance(commands, dict):
        for cmd_name, cmd_def in commands.items():
            params = cmd_def.get("params") if isinstance(cmd_def, dict) else None
            if not isinstance(params, dict):
                continue
            for param_name, param_def in params.items():
                if not isinstance(param_def, dict):
                    continue
                label = param_def.get("label")
                if isinstance(label, str) and label.strip():
                    continue
                warnings.ctx = f"commands.{cmd_name}.params.{param_name}"
                warnings.append(
                    f"Command '{cmd_name}' parameter '{param_name}' has no label, so "
                    f"forms show its key made readable. Give it a label that says "
                    f"what to enter."
                )

    _poll_length_warnings(driver_def, warnings)
    _unwatched_silence_warnings(driver_def, warnings)

    return warnings


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _poll_lines(
    queries: list[Any], child_types: dict[str, Any], config: dict[str, Any]
) -> int:
    """How many lines one poll cycle sends with this config.

    Counted the way ConfigurableDriver._expand_query expands them: a string
    is one line, a ``{send}`` entry one line while its ``when:`` field is
    truthy, an ``each_child`` entry one line per child the declaration
    registers (none while the roster is empty), or one per page of
    ``page_size`` children. A ``when_state:`` gate, on an entry or on the
    whole poll, is counted as sent: the device's state is not known here, and
    the poll is as long as it gets while the gate is open. A roster the device resizes
    (``count_from_state``) is counted at its config field, which is what the
    runtime builds before the first report and what a matrix of that size
    reports anyway.
    """
    lines = 0
    for entry in queries:
        if isinstance(entry, str):
            lines += 1
            continue
        if not isinstance(entry, dict):
            continue
        when = entry.get("when")
        if isinstance(when, str) and when and not config.get(when):
            continue
        child_type = entry.get("each_child")
        if child_type is None:
            lines += 1 if isinstance(entry.get("send"), str) else 0
            continue
        ids = declared_child_ids(_config_sized(child_types.get(child_type)), config)
        lines += len(page_starts(ids, entry.get("page_size"))) if ids else 0
    return lines


def _config_sized(type_def: Any) -> Any:
    """A child type with ``count_from_state`` dropped, so its roster is the
    one its config field declares."""
    if not isinstance(type_def, dict):
        return type_def
    instances = type_def.get("instances")
    if not isinstance(instances, dict) or not instances.get("count_from_state"):
        return type_def
    trimmed = {k: v for k, v in instances.items() if k != "count_from_state"}
    return {**type_def, "instances": trimmed}


def _poll_length_warnings(driver_def: dict[str, Any], warnings: list[str]) -> None:
    """A poll cycle that takes longer than the poll interval.

    Each line of a poll waits ``inter_command_delay`` after it, or
    DEFAULT_LINE_GAP_S over LINE_GAP_TRANSPORTS when no delay is set, and the
    next cycle starts one poll interval after this one ends. So a cycle longer
    than the interval keeps the device answering polls more than half the time
    and refreshes every reading less than half as often as the interval says.
    Checked at the driver's default config and, when a child roster is sized
    by a config field, with every such field at the largest value the driver
    accepts (the field's ``max``, else the child id's ``max``). OSC lines go
    out differently and are not counted here.
    """
    defaults = driver_def.get("default_config")
    config = dict(defaults) if isinstance(defaults, dict) else {}
    transport = config.get("transport") or driver_def.get("transport")
    delay = _number(config.get("inter_command_delay")) or 0.0
    if transport in LINE_GAP_TRANSPORTS:
        gap = delay if delay > 0 else DEFAULT_LINE_GAP_S
    elif transport in ("udp", "http"):
        gap = delay
    else:
        return
    interval = _number(config.get("poll_interval"))
    polling = driver_def.get("polling")
    queries = polling.get("queries") if isinstance(polling, dict) else None
    if gap <= 0 or not interval or interval <= 0 or not isinstance(queries, list):
        return
    raw_types = driver_def.get("child_entity_types")
    child_types = raw_types if isinstance(raw_types, dict) else {}
    raw_schema = driver_def.get("config_schema")
    schema = raw_schema if isinstance(raw_schema, dict) else {}
    spacing = f"{round(gap * 1000)} ms apart"
    if delay <= 0:
        spacing += " (the spacing used when no inter_command_delay is set)"

    def verdict(lines: int) -> str | None:
        cycle = lines * gap
        if cycle <= interval:
            return None
        return (
            f"{lines} lines, {_seconds(cycle)} s at {spacing}, longer than the "
            f"{_seconds(interval)} s poll interval: the device answers polls more "
            f"than half the time, and each reading refreshes every "
            f"{_seconds(cycle + interval)} s"
        )

    warnings.ctx = "polling.queries"
    at_default = verdict(_poll_lines(queries, child_types, config))
    if at_default:
        warnings.append(
            f"Each poll sends {at_default}. Poll less per cycle, or set a longer "
            f"poll_interval in default_config."
        )
        return

    largest = dict(config)
    raised: list[str] = []
    for type_def in child_types.values():
        if not isinstance(type_def, dict):
            continue
        instances = type_def.get("instances")
        field = instances.get("count_from") if isinstance(instances, dict) else None
        if not isinstance(field, str) or not field:
            continue
        field_def = schema.get(field)
        field_def = field_def if isinstance(field_def, dict) else {}
        top = field_def.get("max")
        id_format = type_def.get("id_format")
        if top is None and isinstance(id_format, dict):
            top = id_format.get("max")
        if isinstance(top, bool) or not isinstance(top, int) or top < 1:
            continue
        current = _number(largest.get(field))
        if current is not None and current >= top:
            continue
        largest[field] = top
        label = field_def.get("label")
        name = label if isinstance(label, str) and label else field
        raised.append(f"{name} {top}")
    if not raised:
        return
    at_largest = verdict(_poll_lines(queries, child_types, largest))
    if at_largest:
        warnings.append(
            f"At the largest roster this driver accepts ({', '.join(raised)}), "
            f"each poll sends {at_largest}. Poll less per child, set a longer "
            f"poll_interval, or lower the roster's max to the largest unit the "
            f"driver supports."
        )


def _unwatched_silence_warnings(
    driver_def: dict[str, Any], warnings: list[str]
) -> None:
    """A device that stops answering would keep reading connected.

    The poll loop drops a connection whose polls draw nothing, but only on
    SILENCE_CHECK_TRANSPORTS and only for a driver that sends polls; a
    ``liveness:`` block covers every LIVENESS_TRANSPORTS link. A driver with no
    response rules reads nothing, so it has nothing to watch.
    """
    if driver_def.get("liveness"):
        return
    responses = driver_def.get("responses")
    if not isinstance(responses, list) or not responses:
        return
    defaults = driver_def.get("default_config")
    config = defaults if isinstance(defaults, dict) else {}
    declared = [config.get("transport") or driver_def.get("transport")]
    extra = driver_def.get("transports")
    if isinstance(extra, list):
        declared.extend(extra)
    transports = [t for t in dict.fromkeys(declared) if t in LIVENESS_TRANSPORTS]
    polling = driver_def.get("polling")
    queries = polling.get("queries") if isinstance(polling, dict) else None
    interval = _number(config.get("poll_interval"))
    polls = bool(queries) and interval is not None and interval > 0
    unwatched = [
        t for t in transports if not polls or t not in SILENCE_CHECK_TRANSPORTS
    ]
    if not unwatched:
        return
    over = " or ".join(unwatched)
    if polls:
        reason = f"polls over {over} are not checked for a reply"
    else:
        reason = "it sends no poll at its default config"
    warnings.ctx = "liveness"
    warnings.append(
        f"Nothing notices if the device stops answering over {over}: {reason}, "
        f"and there is no liveness block, so the device reads connected for as "
        f"long as the connection stays open. Add a liveness block that asks "
        f"something the device answers in every state."
    )


def _as_issues(messages: list[str], severity: str) -> list[dict[str, str]]:
    """Pair each message with the section tag the walk recorded for it."""
    tags = getattr(messages, "tags", None)
    if tags is None or len(tags) != len(messages):
        tags = [""] * len(messages)
    return [
        {"severity": severity, "message": message, "path": tag}
        for message, tag in zip(messages, tags)
    ]


def validate_driver_issues(
    driver_def: dict[str, Any],
    *,
    discovery_validator: Callable[[dict[str, Any]], list[str]] | None = None,
    strict: bool = False,
) -> list[dict[str, str]]:
    """The same rules as ``validate_driver_definition``, as issue records.

    Each entry is ``{"severity": "error" | "warning", "message", "path"}``.
    ``path`` says where in the definition the message belongs —
    ``commands.mute``, ``state_variables.volume``, ``config_schema.password``,
    ``responses[2]``, or ``""`` for a whole-driver rule. That is what lets an
    editor put an issue on the right tab and beside the right control without
    knowing any of the rules itself.

    This exists so the driver contract has exactly one implementation. The
    Driver Builder asks this (through ``POST /api/driver-definitions/validate``)
    instead of carrying its own copy of the rules in another language.
    """
    errors = validate_driver_definition(
        driver_def, discovery_validator=discovery_validator, strict=strict
    )
    warnings = validate_driver_warnings(driver_def)
    return _as_issues(errors, "error") + _as_issues(warnings, "warning")


def _unevaluated(value: Any) -> bool:
    """True for ``python_info``'s placeholder for a value read from a constant
    or built at runtime (it is defined there, and that module imports this
    one), which no rule can judge from the source."""
    return type(value).__name__ == "_Unevaluated"


def command_sleep_errors(
    cmd_name: str, cmd_def: dict[str, Any], commands: dict[str, Any]
) -> list[str]:
    """A command's ``sleeps_device`` names a command that can wake the device.

    Shared with the Python surface (``python_info``). The wake command has to
    work with the device off the network (``available_offline``) and say how
    long the device takes to come back (``restarts_device_for``), because the
    wait after it is sent is the driver's measured number and the device that
    has not returned by then reads as the real fault.
    """
    wake = cmd_def.get("sleeps_device")
    if wake is None or _unevaluated(wake):
        return []
    where = f"Command '{cmd_name}'"
    if not isinstance(wake, str) or not wake.strip():
        return [f"{where}: 'sleeps_device' must name the command that wakes the device"]
    if wake == cmd_name:
        return [
            f"{where}: 'sleeps_device' names this command itself; name the "
            f"command that wakes the device"
        ]
    names = {n for n in commands if isinstance(n, str) and n != UNEVALUATED_KEY}
    if wake not in names:
        if UNEVALUATED_KEY in commands:
            return []
        return [
            f"{where}: 'sleeps_device' names '{wake}', which is not a declared "
            f"command{did_you_mean(wake, names)}"
        ]
    wake_def = commands.get(wake)
    if not isinstance(wake_def, dict):
        return []
    errors: list[str] = []
    offline = wake_def.get("available_offline")
    if not _unevaluated(offline) and offline is not True:
        errors.append(
            f"{where}: 'sleeps_device' names '{wake}', which does not set "
            f"available_offline: true, so it cannot wake a device that is off "
            f"the network"
        )
    restarts = wake_def.get("restarts_device_for")
    if not _unevaluated(restarts) and not restarts:
        errors.append(
            f"{where}: 'sleeps_device' names '{wake}', which declares no "
            f"restarts_device_for: give it the measured seconds the device takes "
            f"to come back after '{wake}'"
        )
    return errors


def command_read_back_errors(
    cmd_name: str,
    cmd_def: dict[str, Any],
    commands: dict[str, Any],
    transport: Any,
    python: bool = False,
) -> list[str]:
    """A command's ``read_back`` is something the runtime can send after it.

    ``true`` reads the command's own OSC address or HTTP path; a string or a
    list of them names a command, or is a query sent as a poll query is. A
    named command gets the parameters this one shares with it by name, so
    each parameter it declares has to be one of this command's; and it may
    not read back itself, or one press would read back without end. Shared with the Python surface
    (``python_info``), which refuses the field outright: a Python driver
    reads a value back in its own send_command.
    """
    raw = cmd_def.get("read_back")
    if raw is None or _unevaluated(raw):
        return []
    where = f"Command '{cmd_name}'"
    if python:
        return [
            f"{where}: 'read_back' is read by YAML drivers; a Python driver "
            f"reads the value back in its own send_command"
        ]
    if cmd_def.get("udp") is not None or cmd_def.get("ir") is not None:
        how = "udp" if cmd_def.get("udp") is not None else "ir"
        return [
            f"{where}: 'read_back' reads a value back over the device's "
            f"connection, and a '{how}' command is sent beside it, so nothing "
            f"would answer"
        ]
    shape = (
        "'read_back' must be true, a query, or a list of queries"
    )
    if raw is True:
        if cmd_def.get("address") is None and not (
            cmd_def.get("path") or cmd_def.get("method")
        ):
            return [
                f"{where}: 'read_back: true' reads back from an OSC command's "
                f"own address or an HTTP command's own path. Name the query "
                f"that reads this command's value instead"
            ]
        return []
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, list) and raw:
        entries = raw
    else:
        return [f"{where}: {shape}"]
    if any(not isinstance(e, str) or not e.strip() for e in entries):
        return [f"{where}: {shape}"]

    errors: list[str] = []
    own_params = cmd_def.get("params")
    own_params = own_params if isinstance(own_params, dict) else {}
    for i, entry in enumerate(entries):
        at = f"{where}: read_back" + (f"[{i}]" if isinstance(raw, list) else "")
        if entry == cmd_name:
            errors.append(
                f"{at} names this command itself; name the query that reads "
                f"its value"
            )
            continue
        named = commands.get(entry) if isinstance(commands, dict) else None
        if isinstance(named, dict):
            if named.get("read_back") is not None:
                errors.append(
                    f"{at} names '{entry}', which has a read_back of its own. "
                    f"Name a command that only reads"
                )
            if named.get("udp") is not None or named.get("ir") is not None:
                errors.append(
                    f"{at} names '{entry}', which is sent beside the device's "
                    f"connection, so nothing would answer it"
                )
            declared = named.get("params")
            if isinstance(declared, dict) and UNEVALUATED_KEY not in declared:
                for pname in declared:
                    if pname not in own_params:
                        errors.append(
                            f"{at} names '{entry}', whose parameter '{pname}' "
                            f"is not a parameter of this command, so it would "
                            f"be sent without it"
                        )
            continue
        if transport == "osc" and not entry.startswith("/"):
            errors.append(
                f"{at} '{entry}' is neither a command nor an OSC address "
                f"(an address starts with /)"
            )
    return errors


def command_confirm_errors(where: str, cmd_def: dict[str, Any]) -> list[str]:
    """A command's ``confirm`` is a flag or the sentence to ask.

    Shared with the Python surface (``python_info``), so a Python driver's
    ``confirm: 5`` is refused in the same words as a YAML one. Anything else
    is dropped by every door that asks, and the command is sent unasked.
    """
    confirm = cmd_def.get("confirm")
    if confirm is None or isinstance(confirm, (bool, str)):
        return []
    return [f"{where}: 'confirm' must be a boolean or a message string"]


_CONVERTIBLE_TYPES = ("integer", "number", "float")


def value_conversion_errors(
    driver_def: dict[str, Any], *, python: bool = False
) -> list[str]:
    """Where ``scale`` / ``offset`` / ``unknown`` may appear, and on what.

    The platform converts a YAML driver's values; a Python driver converts in
    its own code, so on the Python surface (``python=True``) every one is
    refused rather than silently doing nothing, and so is a device setting's
    ``map``, which only the YAML write applies. On a YAML driver all three
    need a numeric type, a scale of 0 cannot be converted back, and an
    action's params are never converted (the command's own params are).
    """
    errors: list[str] = []

    def check(where: str, decl: Any, *, in_action: bool = False) -> None:
        if not isinstance(decl, dict):
            return
        present = [k for k in ("scale", "offset", "unknown") if k in decl]
        if not present:
            return
        if python:
            names = ", ".join(present)
            errors.append(
                f"{where}: remove {names}; the platform converts values only "
                f"for YAML drivers, and a Python driver converts in its own code"
            )
            return
        if in_action:
            errors.append(
                f"{where}: put scale and offset on the command's own "
                f"parameter; an action's parameters are not converted"
            )
            return
        if decl.get("type", "string") not in _CONVERTIBLE_TYPES:
            errors.append(
                f"{where}: the type must be integer, number or float to use "
                f"{', '.join(present)}"
            )
        scale = decl.get("scale")
        if isinstance(scale, (int, float)) and not isinstance(scale, bool) and scale == 0:
            errors.append(f"{where}: scale cannot be 0")

    def entries(block: Any) -> list[tuple[Any, Any]]:
        return list(block.items()) if isinstance(block, dict) else []

    for name, decl in entries(driver_def.get("state_variables")):
        check(f"State variable '{name}'", decl)
    for ctype, type_def in entries(driver_def.get("child_entity_types")):
        if isinstance(type_def, dict):
            for name, decl in entries(type_def.get("state_variables")):
                check(f"child_entity_types.{ctype}.state_variables.{name}", decl)
    for cmd_name, cmd_def in entries(driver_def.get("commands")):
        if isinstance(cmd_def, dict):
            for pname, pdef in entries(cmd_def.get("params")):
                check(f"Command '{cmd_name}' param '{pname}'", pdef)
    actions = driver_def.get("actions")
    for action in actions if isinstance(actions, list) else []:
        if isinstance(action, dict):
            for pname, pdef in entries(action.get("params")):
                check(
                    f"Action '{action.get('id')}' param '{pname}'",
                    pdef,
                    in_action=True,
                )
    # A device setting's map is applied by the YAML runtime's write; a Python
    # driver writes its settings in code (on YAML the key is checked with the
    # rest of the setting, device_setting_map_errors).
    if python:
        for name, sdef in entries(driver_def.get("device_settings")):
            if isinstance(sdef, dict) and "map" in sdef:
                errors.append(
                    f"Device setting '{name}': remove map; the platform maps "
                    f"a setting's value only for YAML drivers, and a Python "
                    f"driver writes its settings in its own code"
                )
    return errors


def device_setting_state_key_errors(
    where: str,
    setting_name: str,
    setting_def: dict[str, Any],
    declared_vars: Any,
) -> list[str]:
    """A device setting's ``state_key`` must name a declared state variable.

    Lifted out of the YAML walk so the Python surface can reach the same rule
    rather than grow a second one. It is called from exactly where it always
    ran, with the same message, so YAML error order is unchanged.

    A typo'd ``state_key`` loads fine and shows "(not set)" forever while
    writes fire into nothing — a setting that appears to work and never reads
    back.

    ``declared_vars`` may be an unreadable block on the Python surface (a
    driver that builds its state variables at runtime). "Not in a set we could
    not read" proves nothing, so that case is left to the caller's skip
    reporting rather than turned into an error.
    """
    if not isinstance(declared_vars, dict) or UNEVALUATED_KEY in declared_vars:
        return []
    state_key = setting_def.get("state_key", setting_name)
    if not isinstance(state_key, str):
        return []
    if state_key in declared_vars:
        return []
    return [
        f"{where}: state_key '{state_key}' is not a declared "
        f"state variable, so the setting would never read back"
    ]


def config_learned_from_errors(
    field_name: str,
    field_def: dict[str, Any],
    declared_vars: Any,
) -> list[str]:
    """A config field's ``learned_from`` names the state variable the device
    reports the value in, and the platform saves that report into the field.

    One rule for both surfaces, like ``device_setting_state_key_errors``. A
    name that is not a declared state variable is never reported, so the
    field is silently never filled in. A secret is never learned (the value
    would have to sit in visible state first), a table has no single value
    to learn, and a connection field is saved to the connections table, where
    a reported value would move where the device dials. A state-variable
    block built at runtime cannot be judged from the source and is skipped,
    not failed.
    """
    learned_from = field_def.get("learned_from")
    if learned_from is None:
        return []
    where = f"Config field '{field_name}'"
    if not isinstance(learned_from, str) or not learned_from.strip():
        return [f"{where}: learned_from must name a state variable"]
    errors: list[str] = []
    if is_secret_setting(field_name, field_def):
        errors.append(
            f"{where}: a secret field cannot be learned_from a state variable, "
            f"because the value would have to be shown as state first"
        )
    if field_def.get("type") == "table":
        errors.append(
            f"{where}: a table field cannot be learned_from a state variable, "
            f"because it has no single value to fill in"
        )
    if field_name in CONNECTION_FIELDS:
        errors.append(
            f"{where}: a connection field cannot be learned_from a state "
            f"variable, because a reported value would change how the device "
            f"is reached"
        )
    if isinstance(declared_vars, dict) and UNEVALUATED_KEY not in declared_vars:
        if learned_from not in declared_vars:
            errors.append(
                f"{where}: learned_from '{learned_from}' is not a declared "
                f"state variable, so the field would never be filled in"
            )
    return errors


def device_setting_map_errors(where: str, setting_def: dict[str, Any]) -> list[str]:
    """A device setting's ``map``: the device's own word for each value.

    The keys are values the setting can take, so a key the editor can never
    write is a word that is never sent: on a ``boolean`` setting only true and
    false, on an ``enum`` only its declared values. Other types take any key.

    A bare YAML ``true`` / ``on`` / ``yes`` (key or word) loads as a boolean,
    which the published schema refuses, so it is refused here with the remedy
    rather than left to fail in catalog CI. Same shape rule as a command
    param's ``map``.
    """
    value_map = setting_def.get("map")
    if value_map is None:
        return []
    if not isinstance(value_map, dict) or not value_map:
        return [
            f"{where}: map must be a non-empty mapping of setting value -> "
            f"device word"
        ]
    errors: list[str] = []
    stype = setting_def.get("type", "string")
    allowed = declared_enum_values(setting_def) if stype == "enum" else None
    for key, word in value_map.items():
        if isinstance(key, bool):
            errors.append(
                f"{where}: map key '{key}' is not text; YAML reads a bare "
                f"true, false, on, off, yes or no as a boolean. Put it in quotes."
            )
            continue
        if isinstance(word, bool):
            errors.append(
                f"{where}: map word for '{key}' is not text; YAML reads a bare "
                f"ON, OFF, YES or NO as a boolean. Put it in quotes."
            )
            continue
        if not (
            isinstance(key, (str, int, float))
            and isinstance(word, (str, int, float))
        ):
            errors.append(
                f"{where}: map entries must be setting value -> device word "
                f"pairs of text or numbers"
            )
            continue
        if stype == "boolean" and str(key).strip().lower() not in ("true", "false"):
            errors.append(
                f"{where}: map key '{key}' is not a value a boolean setting "
                f"takes; use \"true\" and \"false\""
            )
        elif allowed is not None and str(key) not in allowed:
            errors.append(
                f"{where}: map key '{key}' is not one of the setting's values "
                f"({', '.join(allowed)})"
            )
    return errors


def undeclared_child_type_reason(
    child_type: str, child_types: dict[str, Any],
) -> str:
    """Why a ``child_type`` reference does not resolve, as one reason fragment.

    Shared with the runtime, which is the point. The static rule below rejects
    this before a driver is published; a driver copied straight into
    ``driver_repo/`` only warns at load, so the dispatch gate meets the same
    fault again at command time and has to describe it the same way — the
    alternative was the gate falling through to integer coercion and blaming
    the value the user typed. A fragment rather than a sentence, so each
    surface frames it: the static rule prefixes the dotted path, the gate
    prefixes the command and parameter name.
    """
    declared = sorted(k for k in child_types if k != UNEVALUATED_KEY)
    return (
        f"child_type '{child_type}' is not a declared child_entity_type"
        + (
            f" (declared: {', '.join(declared)})"
            if declared
            else " (the driver declares none)"
        )
    )


def child_param_reference_errors(
    driver_def: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Every reference a command parameter makes into ``child_entity_types``.

    Two references, both decidable from the definition alone, and both missing
    on *every* surface until now — a YAML driver was no better covered than a
    Python one:

      * ``commands.*.params.*.child_type`` must name a declared child type.
        A typo does not fail; it falls through to plain integer coercion at
        the dispatch gate, so the user is told the value they typed is wrong
        when it is the driver that is wrong.
      * a ``child_id`` param's own ``min``/``max`` must sit inside that type's
        ``id_format`` range, so the two declarations cannot contradict each
        other and leave the runtime to pick a winner.

    Returns ``(errors, skipped)``. ``skipped`` names each reference that could
    not be decided because its *target set* is computed rather than declared:
    a Python driver may build ``commands`` or ``child_entity_types`` at
    runtime, and a check whose target set is unknown has to say so rather than
    report a dangling reference it cannot see the target of. The skip is per
    reference, never per driver — an unreadable ``child_entity_types`` silences
    only the references that resolve against it, and every other rule still
    runs. Callers print the skips; none of them change a verdict.
    """
    errors: list[str] = []
    skipped: list[str] = []

    commands = driver_def.get("commands")
    if commands is None:
        return errors, skipped
    if not isinstance(commands, dict):
        skipped.append("commands is computed, so no command params were checked")
        return errors, skipped

    raw_types = driver_def.get("child_entity_types")
    if raw_types is not None and not isinstance(raw_types, dict):
        skipped.append(
            "child_entity_types is computed, so no child_type reference was "
            "checked"
        )
        return errors, skipped
    if UNEVALUATED_KEY in commands:
        skipped.append(
            f"{len(commands) - 1} of an unknown number of commands were "
            f"readable; params of the rest were not checked"
        )
    child_types: dict[str, Any] = raw_types if isinstance(raw_types, dict) else {}
    # The type NAMES themselves were built at runtime, so "not in this map"
    # proves nothing about whether the reference resolves.
    types_computed = UNEVALUATED_KEY in child_types

    for cmd_name, cmd_def in commands.items():
        if cmd_name == UNEVALUATED_KEY or not isinstance(cmd_def, dict):
            continue
        params = cmd_def.get("params")
        if not isinstance(params, dict):
            continue
        for param_name, param_def in params.items():
            if not isinstance(param_def, dict):
                continue
            if param_def.get("type") != "child_id":
                continue
            where = f"commands.{cmd_name}.params.{param_name}"
            child_type = param_def.get("child_type")

            if child_type is None:
                errors.append(
                    f"{where}: a child_id param must declare 'child_type' "
                    f"(without it the id cannot be resolved to a child type "
                    f"and is coerced as a plain integer)"
                )
                continue
            if not isinstance(child_type, str):
                skipped.append(f"{where}: child_type is computed")
                continue
            if child_type not in child_types:
                if types_computed:
                    skipped.append(
                        f"{where}: child_type '{child_type}', because "
                        f"child_entity_types keys are computed"
                    )
                    continue
                errors.append(
                    f"{where}: "
                    f"{undeclared_child_type_reason(child_type, child_types)}"
                )
                continue

            type_def = child_types.get(child_type)
            id_format = type_def.get("id_format") if isinstance(type_def, dict) else None
            if not isinstance(id_format, dict):
                continue
            errors.extend(
                _child_id_bound_errors(where, child_type, param_def, id_format)
            )

    return errors, skipped


def _child_id_bound_errors(
    where: str, child_type: str, param_def: dict[str, Any], id_format: dict[str, Any],
) -> list[str]:
    """A child_id param's declared bounds against its type's ``id_format``."""
    errors: list[str] = []

    def _num(value: Any) -> int | float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    fmt_min, fmt_max = _num(id_format.get("min")), _num(id_format.get("max"))
    param_min, param_max = _num(param_def.get("min")), _num(param_def.get("max"))

    if param_min is not None and fmt_min is not None and param_min < fmt_min:
        errors.append(
            f"{where}: min {param_min:g} is below "
            f"child_entity_types.{child_type}.id_format.min ({fmt_min:g}), so "
            f"the gate would accept an id the child type cannot have"
        )
    if param_max is not None and fmt_max is not None and param_max > fmt_max:
        errors.append(
            f"{where}: max {param_max:g} is above "
            f"child_entity_types.{child_type}.id_format.max ({fmt_max:g}), so "
            f"the gate would accept an id the child type cannot have"
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
    # A Python driver may merge commands in from a module constant or build
    # them at runtime, and the reader marks the block when it could not see
    # every key. The set is then a SUBSET of the real one, so "not in it"
    # proves nothing — checking anyway reports a working driver as broken.
    # (`sony_bravia` merges `**_IRCC_COMMANDS`: 15 keys visible, the action's
    # target among the ones that are not.) The skip is named by
    # ``python_info.python_driver_reference_skips`` rather than swallowed. A
    # YAML definition can never contain the marker, so this is a no-op there.
    commands_partial = UNEVALUATED_KEY in command_ids

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
                elif command_ids and not commands_partial and cid not in command_ids:
                    errors.append(
                        f"quick_actions[{i}]: '{cid}' is not a declared command"
                    )
                elif _is_file_command_id(commands, cid):
                    errors.append(
                        f"quick_actions[{i}]: '{cid}' {_FILE_ACTION_REASON}"
                    )

    actions = driver_def.get("actions")
    if actions is not None:
        if not isinstance(actions, list):
            errors.append("actions: must be a list")
        else:
            seen: set[str] = set()
            for i, entry in enumerate(actions):
                errors.extend(
                    _validate_action_entry(
                        i,
                        entry,
                        set() if commands_partial else command_ids,
                        seen,
                    )
                )
                if (
                    isinstance(entry, dict)
                    and entry.get("kind", "command") == "command"
                    and isinstance(entry.get("id"), str)
                ):
                    target = entry.get("command") or entry["id"]
                    if isinstance(target, str) and _is_file_command_id(commands, target):
                        errors.append(
                            f"actions[{i}]: command '{target}' {_FILE_ACTION_REASON}"
                        )

    return errors


# A promoted button has no file to give and nowhere to put one that comes
# back: the command runs from Send Command on the device page instead.
_FILE_ACTION_REASON = (
    "takes or hands back a file, so it cannot be a Quick Action; it runs "
    "from Send Command on the device page"
)


def _is_file_command_id(commands: Any, command_id: str) -> bool:
    return isinstance(commands, dict) and is_file_command(commands.get(command_id))


# The kinds of value the Python reader hands back as themselves; anything else
# is its placeholder for an expression it could not evaluate.
_PLAIN_VALUE_TYPES = frozenset({str, int, float, bool, list, tuple, dict})


def _judgeable(value: Any) -> bool:
    """A value read from source as itself, not a placeholder for an
    expression the Python reader could not evaluate."""
    return value is None or type(value) in _PLAIN_VALUE_TYPES


_ACCEPT_RE = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9._-]*")


def file_command_errors(
    where: str, cmd_def: dict[str, Any], *, python: bool,
) -> list[str]:
    """The rules for a command that takes a file or hands one back.

    Shared with the Python surface (``python_info``), which is the only one
    that may declare either: a YAML driver's runtime has no way to put a file
    on the wire, so a ``.avcdriver`` declaring one is refused here (the
    platform never runs the published JSON Schema at load, so its enum alone
    would not stop it). A value the Python reader could not evaluate is not
    judged.
    """
    errors: list[str] = []
    params = cmd_def.get("params")
    if isinstance(params, dict):
        for pname, pdef in params.items():
            if not isinstance(pdef, dict) or not _judgeable(pdef.get("type")):
                continue
            label = f"{where} param '{pname}'"
            if pdef.get("type") != "file":
                stray = [key for key in ("accept", "max_bytes") if key in pdef]
                if stray:
                    errors.append(
                        f"{label}: {' and '.join(stray)} "
                        f"{'applies' if len(stray) == 1 else 'apply'} only to "
                        f"a file param (type: file)"
                    )
                continue
            if not python:
                errors.append(f"{label}: type 'file' requires a Python driver")
                continue
            excluded = [key for key in FILE_PARAM_EXCLUDED_KEYS if key in pdef]
            if excluded:
                errors.append(
                    f"{label}: a file param takes no {', '.join(excluded)}"
                )
            accept = pdef.get("accept")
            if accept is not None and _judgeable(accept) and not (
                isinstance(accept, (list, tuple)) and accept and all(
                    not _judgeable(item)
                    or (isinstance(item, str) and _ACCEPT_RE.fullmatch(item))
                    for item in accept
                )
            ):
                errors.append(
                    f"{label}: accept must list file-name endings, "
                    f"e.g. [\".bin\"]"
                )
            max_bytes = pdef.get("max_bytes")
            if max_bytes is not None and _judgeable(max_bytes) and (
                isinstance(max_bytes, bool)
                or not isinstance(max_bytes, int)
                or not 1 <= max_bytes <= FILE_PARAM_MAX_BYTES
            ):
                errors.append(
                    f"{label}: max_bytes must be a whole number of bytes from 1 "
                    f"to {FILE_PARAM_MAX_BYTES} (1 GiB)"
                )
    returns_file = cmd_def.get("returns_file")
    if returns_file is not None and _judgeable(returns_file):
        if not isinstance(returns_file, bool):
            errors.append(f"{where}: 'returns_file' must be true or false")
        elif returns_file and not python:
            errors.append(f"{where}: 'returns_file' requires a Python driver")
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

    errors.extend(condition_block_errors(where, entry.get("visible_when")))

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
