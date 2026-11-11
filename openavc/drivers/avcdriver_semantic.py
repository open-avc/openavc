"""Driver-definition validation rules (the .avcdriver contract).

Every cross-field rule that decides whether a driver definition is valid
is reached through this module: ``validate_driver_definition`` walks the
definition section by section and asks a check module about each command
(``command_checks``), response rule (``response_rule_checks``), query entry
(``query_entry_checks``), child type and the references into it
(``child_type_checks``), state variable (``state_variable_checks``), device
setting and learned config field (``setting_checks``), the push block
(``push_block_checks``), the Quick Action blocks (``action_checks``), the
framing each way (``framing_checks``), the sign-in (``sign_in_checks``), the
watchdog (``liveness_checks``), the ``routing:`` block
(``routing_checks``), where a value conversion may be declared
(``conversion_checks``) and the ``{name}`` placeholders in its wire templates
(``substitution_checks``). ``validate_driver_warnings`` walks it the same
way for the rules that only warn (``driver_warnings``). The runtime
loader wraps it (adding the discovery-block check, which needs the
discovery engine's parser), and the community driver catalog runs a copy of
the same rules in its CI, so a definition that passes catalog review is
exactly a definition the platform will load.

Purity contract: standard library plus the sibling contract modules (the
``spec`` constants table and the check modules). Nothing here may import
the server runtime, transports, or discovery machinery; checks that need
them are passed in (see ``validate_driver_definition``'s
``discovery_validator`` hook). An import-guard test enforces this.
"""

from __future__ import annotations

import difflib as _difflib
from functools import partial
from typing import Any, Callable

from openavc.drivers.action_checks import (
    validate_actions as validate_actions,
    yaml_action_errors,
)
from openavc.drivers.child_type_checks import (
    RosterNames,
    child_param_reference_errors,
    child_type_errors,
    undeclared_child_type_reason as undeclared_child_type_reason,
)
from openavc.drivers.command_checks import CommandSection
from openavc.drivers.conversion_checks import (
    value_conversion_errors as value_conversion_errors,
)
from openavc.drivers.declared_names import (
    UNEVALUATED_KEY as UNEVALUATED_KEY,
    DeclaredSections,
)
from openavc.drivers.driver_warnings import (
    masked_default_warnings,
    poll_length_warnings,
    port_open_warnings,
    unlabeled_param_warnings,
    unwatched_silence_warnings,
)
from openavc.drivers.framing_checks import (
    COMMAND_AFFIXES,
    command_affix_errors,
    frame_parser_errors,
    send_frame_errors,
)
from openavc.drivers.liveness_checks import liveness_errors
from openavc.drivers.push_block_checks import push_block_errors
from openavc.drivers.query_entry_checks import (
    FollowUp,
    QueryEntries,
    then_query_errors,
)
from openavc.drivers.response_rule_checks import response_rule_errors
from openavc.drivers.routing_checks import (
    routing_block_errors as routing_block_errors,
)
from openavc.drivers.setting_checks import (
    config_learned_from_errors as config_learned_from_errors,
    device_setting_errors,
    device_setting_map_errors as device_setting_map_errors,
    device_setting_state_key_errors as device_setting_state_key_errors,
)
from openavc.drivers.sign_in_checks import auth_errors
from openavc.drivers.spec import (
    DEFS,
    FIELDS,
    GENERIC_ID_PREFIXES,
    REQUIRED_FIELDS,
    YAML_TRANSPORTS,
    block_has_fixed_keys,
    parse_version,
    platform_requirements,
)
from openavc.drivers.state_variable_checks import (
    DEVICE_VARIABLE,
    state_variable_errors,
)
from openavc.drivers.substitution_checks import (
    validate_substitutions as validate_substitutions,
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
    walk = _KeyWalk()
    walk.walk({"fields": FIELDS, "extra": False}, driver_def, "")
    return walk.errors


class _KeyWalk:
    """``unknown_key_errors``' descent: a definition's value read against the
    registry node that declares it, one method per kind of node. A reference
    is followed, a list's entries are read against its ``items``, a block of
    author-chosen names against its ``extra`` and a block of declared
    ``fields`` against those fields; anything else is not descended. Each
    message is tagged with the place the walk last set, which is why a name
    that is not text is tagged with the place its map's previous entry left.
    Both places are set through the local name ``errors``, the form the
    section-tag scan in ``tests/test_driver_definition_validate.py`` reads.
    """

    def __init__(self) -> None:
        self.errors = _TaggedErrors()

    def walk(self, node: Any, value: Any, loc: str) -> None:
        if not isinstance(node, dict):
            return
        if "ref" in node:
            self.walk(DEFS.get(node["ref"]), value, loc)
        elif isinstance(value, list):
            self._entries(node.get("items"), value, loc)
        elif isinstance(value, dict):
            self._block(node, value, loc)

    def _entries(self, item: Any, value: list[Any], loc: str) -> None:
        if item is None:
            return
        for i, entry in enumerate(value):
            self.walk(item, entry, f"{loc}[{i}]")

    def _block(self, node: dict[str, Any], value: dict[Any, Any], loc: str) -> None:
        fields = node.get("fields")
        extra = node.get("extra")
        # A block whose keys are author-chosen names (commands, state
        # variables, a config map) declares its value shape in `extra`. The
        # names are data, not contract keys: descend, don't judge them.
        if isinstance(extra, dict) and not fields:
            self._names(extra, value, loc)
        elif isinstance(fields, dict):
            self._fields(node, fields, value, loc)

    def _names(self, extra: dict[str, Any], value: dict[Any, Any], loc: str) -> None:
        """A block of names the author chose. The one thing worth judging is
        whether a name is text at all: YAML reads a bare ``on``, ``off``,
        ``yes`` or ``no`` as a boolean and bare digits as a number, so a
        parameter an author reasonably calls ``on`` arrives as the key
        ``True``. Nothing downstream can substitute it, and the failure
        surfaces far from the cause."""
        errors = self.errors
        errors.ctx = loc
        for key, sub in value.items():
            if not isinstance(key, str):
                errors.append(
                    f"{_at(loc)}name '{key}' is not text. YAML reads bare "
                    f"on/off/yes/no/true/false as booleans and bare "
                    f"digits as numbers. Put it in quotes."
                )
            self.walk(extra, sub, _inside(loc, key))

    def _fields(
        self, node: dict[str, Any], fields: dict[str, Any], value: dict[Any, Any], loc: str,
    ) -> None:
        """A block of declared fields: a key it does not declare is reported
        only where the registry says the key set is fixed, so this and the
        schema always agree; an open or type-conditional block is still
        descended, just never judged here."""
        if block_has_fixed_keys(node):
            self._undeclared(set(fields), value, loc)
        for key, sub_node in fields.items():
            if key in value:
                self.walk(sub_node, value[key], _inside(loc, key))

    def _undeclared(self, known: set[str], value: dict[Any, Any], loc: str) -> None:
        # This walk knows exactly where it is, so it tags its own entries
        # rather than inheriting the caller's section.
        errors = self.errors
        errors.ctx = loc
        for key in sorted(set(value) - known):
            close = _difflib.get_close_matches(
                str(key), sorted(known), n=1, cutoff=0.7
            )
            hint = f" (did you mean '{close[0]}'?)" if close else ""
            errors.append(f"{_at(loc)}unknown key '{key}'{hint}")


def _at(loc: str) -> str:
    """A message's opening: the place it names, or nothing at the top."""
    return f"{loc}: " if loc else ""


def _inside(loc: str, key: Any) -> str:
    """The place of ``key`` inside ``loc``."""
    return f"{loc}.{key}" if loc else str(key)


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
    it is asked after the device settings and before the push block. When
    None, the discovery block is not validated.

    ``strict`` additionally rejects keys the contract doesn't declare (a
    misspelled ``state_varibles`` silently loads a driver with no state
    variables otherwise). Pass it where a driver is being *created* — the
    Builder's save, an import, the AI tool, catalog CI — and leave it off
    where one is being *loaded*: see ``unknown_key_errors``.

    The returned list is a ``_TaggedErrors`` — still a plain ``list[str]`` to
    every caller, but it also carries the section each message came from, so
    ``validate_driver_issues`` can hand the Driver Builder something to anchor
    each issue to. ``_DefinitionWalk`` sets the tag as it enters each part of
    the definition; the parts are asked here in the order they report.
    """
    # A file can parse to something other than a mapping. It is reported, not
    # raised on, so one bad file never stops the others loading.
    if not isinstance(driver_def, dict):
        return _TaggedErrors(["Driver definition must be a mapping"])
    walk = _DefinitionWalk(driver_def)
    walk.driver_fields()
    walk.response_rules()
    walk.commands()
    walk.config_fields()
    walk.device_settings()
    walk.discovery(discovery_validator)
    walk.connection_blocks()
    walk.actions()
    walk.state_variables()
    walk.framing()
    walk.child_types()
    walk.query_entries()
    walk.routing()
    walk.placeholders()
    if strict:
        walk.undeclared()
    return walk.errors


class _DefinitionWalk:
    """``validate_driver_definition``'s walk over one definition: a method per
    part of the definition, asked in the order they report.

    Each part sets the tag its messages carry before it says anything,
    refuses its section when the section is not the shape it should be, and
    asks that part's check module. The tag is set through the local name
    ``errors``, the form the section-tag scan in
    ``tests/test_driver_definition_validate.py`` reads, so every method that
    sets one names the list that way. The sections other parts name into are
    read once (``declared``, which also names those that are there in another
    shape) and handed to the checks that follow a name into them. Whether a
    section of the wrong shape is refused is written where the section is
    checked, and the sections differ: the rules, the commands and the state
    variables refuse a null, the device settings and the poll let one pass,
    the child types let any empty value pass.
    """

    def __init__(self, driver_def: dict[str, Any]) -> None:
        self.driver_def = driver_def
        self.errors = _TaggedErrors()
        self.declared = DeclaredSections(driver_def)

    def driver_fields(self) -> None:
        """The driver's own fields, tagged with no section."""
        errors, driver_def = self.errors, self.driver_def
        errors.ctx = ""
        for field in REQUIRED_FIELDS:
            if field not in driver_def:
                errors.append(f"Missing required field: {field}")
        transport = driver_def.get("transport", "")
        # "bridge" is the sentinel transport for a device that emits through a
        # live bridge instance (an IR device on an emitter port) rather than
        # dialing a host of its own: it opens no socket.
        if transport and transport not in YAML_TRANSPORTS:
            errors.append(f"Unsupported transport: {transport}")
        # The IR code-set opt-in is a flag that turns on the device page's IR
        # Codes editor; the codes live in the device config / default_config.
        ir_codes_flag = driver_def.get("ir_codes")
        if ir_codes_flag is not None and not isinstance(ir_codes_flag, bool):
            errors.append(
                "ir_codes: must be a boolean (the code-set lives in "
                "default_config.ir_codes / the device config)"
            )

    def response_rules(self) -> None:
        """What each rule says about itself, checked in response_rule_checks.
        A rule's then_query and its child_list's paged reads are checked with
        the query entries."""
        errors = self.errors
        errors.ctx = "responses"
        if "responses" in self.declared.misshapen:
            errors.append("responses: must be a list")
        rules = self.declared.responses
        # `after_json:` only means something where a json rule can consume the
        # frame first, so the whole list decides whether it is inert.
        any_json_rule = any(isinstance(r, dict) and r.get("json") for r in rules)
        for i, rule in enumerate(rules):
            errors.ctx = f"responses[{i}]"
            errors.extend(
                response_rule_errors(i, rule, self.declared.child_types, any_json_rule)
            )

    def commands(self) -> None:
        """Each command, checked in command_checks; then the references its
        parameters make into child_entity_types (child_type_checks, which a
        Python driver reaches through python_info), and the constant text
        wrapped around every byte-stream command (framing_checks, with the
        rest of the send side)."""
        errors, driver_def = self.errors, self.driver_def
        errors.ctx = "commands"
        if "commands" in self.declared.misshapen:
            errors.append("commands: must be a mapping")
        section = CommandSection(driver_def, self.declared)
        for cmd_name, cmd_def in self.declared.commands.items():
            errors.ctx = f"commands.{cmd_name}"
            errors.extend(section.command_errors(cmd_name, cmd_def))
        errors.ctx = "commands"
        child_ref_errors, _ = child_param_reference_errors(driver_def)
        errors.extend(child_ref_errors)
        for frame_key in COMMAND_AFFIXES:
            errors.ctx = frame_key
            errors.extend(command_affix_errors(driver_def, frame_key))

    def config_fields(self) -> None:
        """A config field's learned_from, checked in setting_checks against
        the driver's own state variables. A config_schema that is not a
        mapping has no fields to check here."""
        errors = self.errors
        schema = self.driver_def.get("config_schema")
        fields = schema.items() if isinstance(schema, dict) else ()
        for field_name, field_def in fields:
            if isinstance(field_def, dict) and "learned_from" in field_def:
                errors.ctx = f"config_schema.{field_name}"
                errors.extend(
                    config_learned_from_errors(
                        field_name, field_def, self.declared.state_variables
                    )
                )

    def device_settings(self) -> None:
        """Each device setting, checked in setting_checks against the driver's
        own state variables."""
        errors = self.errors
        errors.ctx = "device_settings"
        settings = self.driver_def.get("device_settings")
        if settings is not None and not isinstance(settings, dict):
            errors.append("device_settings: must be a mapping")
        if not isinstance(settings, dict):
            return
        for setting_name, setting_def in settings.items():
            errors.ctx = f"device_settings.{setting_name}"
            errors.extend(
                device_setting_errors(setting_name, setting_def, self.declared.state_variables)
            )

    def discovery(
        self, discovery_validator: Callable[[dict[str, Any]], list[str]] | None
    ) -> None:
        """The ``discovery:`` block, through the check the caller hands in
        (the loader's needs the discovery engine's parser). The check reads
        the definition and must not change it: the sections other parts name
        into were read before it runs. A template (``generic_*``) takes no
        part in discovery and is not checked. Any mix of strong and soft
        signals, or none, is allowed here; signal collisions are caught when
        the SignalIndex is built."""
        errors = self.errors
        errors.ctx = "discovery"
        driver_id = self.driver_def.get("id", "") or ""
        is_template = any(driver_id.startswith(p) for p in GENERIC_ID_PREFIXES)
        if not is_template and discovery_validator is not None:
            errors.extend(discovery_validator(self.driver_def))

    def connection_blocks(self) -> None:
        """The blocks the connection itself reads: ``push:``, the
        device-initiated notifications (push_block_checks, every message
        opening with push:); the sign-in the platform types before any other
        traffic (sign_in_checks); the watchdog's probe while the link is up
        (liveness_checks)."""
        errors, driver_def = self.errors, self.driver_def
        errors.ctx = "push"
        errors.extend(push_block_errors(driver_def))
        errors.ctx = "auth"
        errors.extend(auth_errors(driver_def))
        errors.ctx = "liveness"
        errors.extend(liveness_errors(driver_def))

    def actions(self) -> None:
        """The Quick Action blocks, checked in action_checks: the rule a
        Python driver's DRIVER_INFO shares, every message tagged actions, then
        a YAML definition's own, entry by entry."""
        errors = self.errors
        errors.ctx = "actions"
        errors.extend(validate_actions(self.driver_def))
        for i, entry in enumerate(self.driver_def.get("actions") or []):
            errors.ctx = f"actions[{i}]"
            errors.extend(yaml_action_errors(f"actions[{i}]", entry))

    def state_variables(self) -> None:
        """The driver's own state variables, each one's fields checked in
        state_variable_checks (which a child type's variables ask too); then
        where a value conversion may be declared, checked in conversion_checks
        (python_info asks it with python=True), every message tagged
        state_variables."""
        errors = self.errors
        errors.ctx = "state_variables"
        if "state_variables" in self.declared.misshapen:
            errors.append("state_variables: must be a mapping")
        for var_name, var_def in self.declared.state_variables.items():
            errors.ctx = f"state_variables.{var_name}"
            if isinstance(var_def, dict):
                errors.extend(
                    state_variable_errors(f"State variable '{var_name}'", var_def, DEVICE_VARIABLE)
                )
            else:
                errors.append(f"State variable '{var_name}': must be a dict")
        errors.ctx = "state_variables"
        errors.extend(value_conversion_errors(self.driver_def))

    def framing(self) -> None:
        """The framing each way, checked in framing_checks: how frames are cut
        from the stream, and the header each one sent is wrapped in."""
        errors, driver_def = self.errors, self.driver_def
        errors.ctx = "frame_parser"
        errors.extend(frame_parser_errors(driver_def))
        errors.ctx = "send_frame"
        errors.extend(send_frame_errors(driver_def))

    def child_types(self) -> None:
        """What each child type says about itself (its name, id_format, state
        variables and instances: roster), checked in child_type_checks."""
        errors = self.errors
        errors.ctx = "child_entity_types"
        if "child_entity_types" in self.declared.misshapen:
            if self.driver_def["child_entity_types"]:
                errors.append("child_entity_types: must be a mapping")
            return
        roster_names = RosterNames(self.driver_def)
        for child_type, type_def in self.declared.child_types.items():
            errors.ctx = f"child_entity_types.{child_type}"
            errors.extend(child_type_errors(child_type, type_def, roster_names))

    def query_entries(self) -> None:
        """The poll's entries, the start-up's and each text rule's then_query,
        checked in query_entry_checks against what the definition declares,
        read once for all three."""
        queries = QueryEntries(self.driver_def, self.declared)
        self._poll(queries)
        self._each(
            "on_connect", self.driver_def.get("on_connect"),
            partial(queries.entry_errors, osc_form=True),
        )
        for i, rule in enumerate(self.declared.responses):
            self._follow_ups(i, rule, queries)

    def _poll(self, queries: QueryEntries) -> None:
        errors = self.errors
        errors.ctx = "polling"
        polling = self.driver_def.get("polling")
        if polling is not None and not isinstance(polling, dict):
            errors.append("polling: must be a mapping")
        if not isinstance(polling, dict):
            return
        # The runtime sources poll cadence from default_config.poll_interval
        # only; an interval here would silently do nothing.
        if "interval" in polling:
            errors.append(
                "polling.interval is not read by the runtime. Remove the "
                "interval: key and set default_config.poll_interval instead"
            )
        self._each("polling.queries", polling.get("queries"), queries.entry_errors)
        if "when_state" in polling:
            errors.ctx = "polling"
            errors.extend(queries.when_state_errors("polling", polling.get("when_state")))

    def _follow_ups(self, index: int, rule: Any, queries: QueryEntries) -> None:
        """A text rule's ``then_query:`` takes the entries a poll takes, plus
        {child_id} in an entry sent once, naming the child the rule writes. A
        list the rule may not carry is refused whole; otherwise each entry is
        checked against the rule first, then as any entry is."""
        if not isinstance(rule, dict) or "then_query" not in rule:
            return
        errors = self.errors
        errors.ctx = f"responses[{index}]"
        refused = then_query_errors(index, rule)
        errors.extend(refused)
        if refused:
            return
        follow = FollowUp(rule)
        listed = f"responses[{index}].then_query"
        self._each(listed, rule["then_query"], follow.reread_errors)
        self._each(listed, rule["then_query"], partial(queries.entry_errors, follow_up=follow))

    def routing(self) -> None:
        """The ``routing:`` block (where this driver's routing lives, so a
        Matrix can be set up from the device), checked in routing_checks:
        every rule there is a cross-reference into what the driver declares."""
        errors = self.errors
        errors.ctx = "routing"
        errors.extend(routing_block_errors(self.driver_def))

    def placeholders(self) -> None:
        """Every {name} the driver writes into a wire template has to resolve
        (substitution_checks). Each message opens with its own place, and they
        land in several sections at once (a command, a response, the polling
        list), so each is tagged from its own opening rather than one section
        tag; otherwise the Builder would anchor a polling issue to a command."""
        errors = self.errors
        for message in validate_substitutions(self.driver_def):
            substitution_loc = message.split(":", 1)[0]
            errors.ctx = substitution_loc
            errors.append(message)

    def undeclared(self) -> None:
        """Strict mode's two checks, after every other error so each keeps its
        position: keys the contract does not declare (``unknown_key_errors``
        says why loading stays lenient), then the platform release the
        driver's fields need."""
        errors = self.errors
        errors.extend(unknown_key_errors(self.driver_def))
        errors.ctx = "min_platform_version"
        errors.extend(platform_version_errors(self.driver_def))

    def _each(self, name: str, entries: Any, check: Callable[[str, Any], list[str]]) -> None:
        """Ask ``check`` about each entry of a list, tagging what it says with
        the entry's place (``name[i]``). A list that is not a list has no
        entries."""
        errors = self.errors
        errors.ctx = name
        if not isinstance(entries, list):
            return
        for i, entry in enumerate(entries):
            errors.ctx = f"{name}[{i}]"
            errors.extend(check(f"{name}[{i}]", entry))


def validate_driver_warnings(driver_def: dict[str, Any]) -> list[str]:
    """Rules an author should see but that must never block anything.

    Deliberately separate from ``validate_driver_definition``: everything that
    function returns is an *error* to every one of its callers (the loader
    drops the driver, the save routes refuse it, catalog CI fails the build).
    A rule that only wants to say "are you sure?" has no home there; each one
    is a function in ``driver_warnings``, asked here in the order they report.

    ``validate_driver_issues`` is what carries these onward, so an editor can
    show them beside the errors without treating them as one.

    Same tagging as the error walk: ``warnings.ctx`` marks where each message
    belongs. A config field's or a parameter's name is written into the tag
    only when a rule says something about it.
    """
    warnings = _TaggedErrors()
    if not isinstance(driver_def, dict):
        return warnings

    for field_name, found in masked_default_warnings(driver_def):
        warnings.ctx = f"config_schema.{field_name}"
        warnings.extend(found)

    warnings.ctx = "discovery"
    warnings.extend(port_open_warnings(driver_def))

    for cmd_name, param_name, found in unlabeled_param_warnings(driver_def):
        warnings.ctx = f"commands.{cmd_name}.params.{param_name}"
        warnings.extend(found)

    warnings.ctx = "polling.queries"
    warnings.extend(poll_length_warnings(driver_def))
    warnings.ctx = "liveness"
    warnings.extend(unwatched_silence_warnings(driver_def))

    return warnings


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
