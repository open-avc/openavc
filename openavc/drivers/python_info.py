"""Reading a Python driver's ``DRIVER_INFO`` without importing it.

A ``.py`` driver's contract lives in a ``DRIVER_INFO`` dict on its class. Every
door that wants to check that contract before the driver runs — the standalone
checker, ``openavc.simulator.validate``, the community catalog's CI — has to read it
from the source text, because importing a driver executes arbitrary code and
needs its dependencies present (read as the import reads it: ``python_source``).

This module is that reader, plus THE verdict on a ``DRIVER_INFO``
(``check_python_driver_info``): the rules a Python driver has always been held
to here, and the section checks both kinds of driver share, asked of the same
check modules the YAML walk asks. It refuses at the author's doors (the
checker, catalog CI, the Code view's save; ``python_driver_info_issues``) and
is a notice where a driver loads or a product connects. It is also THE skip
for what the reader could not see (``_Coverage``): a value built in code is
never judged, nor anything judged against a section not read whole, and
``python_driver_reference_skips`` names each. It exists so there is exactly
one of each: the catalog's ``build_index.py`` used to carry its own copy of
the AST walk, and the runtime loader its own copy of the structural rules.

Purity contract, same as ``spec`` and ``avcdriver_semantic``: standard library
only. The community catalog vendors this file and runs it in a CI job that
installs no ``openavc`` package. An import-guard test enforces it.
"""

from __future__ import annotations

import ast
from functools import partial
from pathlib import Path
from typing import Any, Callable

# The definition walk's guard and tagged list: every part of this walk is
# asked the way each part of that one is, so a check that raises on a value
# of a kind it does not read is a message in both.
from openavc.drivers.avcdriver_semantic import (
    UNEVALUATED_KEY,
    DefinitionVerdict,
    _guarded,
    _TaggedErrors,
    child_param_reference_errors,
    config_learned_from_errors,
    device_setting_state_key_errors,
    platform_version_errors,
    routing_block_errors,
    unknown_key_errors,
    validate_actions,
    value_conversion_errors,
)
from openavc.drivers.child_type_checks import RosterNames, child_type_errors
from openavc.drivers.command_checks import SharedCommandChecks, state_name_errors
from openavc.drivers.declared_names import DeclaredSections
from openavc.drivers.discovery_block import discovery_refusals
from openavc.drivers.param_aid_checks import one_param_aid_errors, sibling_refs
from openavc.drivers.push_block_checks import push_block_errors
from openavc.drivers.python_driver_class import driver_class_info
from openavc.drivers.python_source import SourceDecodeError, python_source
from openavc.drivers.serial_line import SERIAL_LINE_SETTINGS, line_setting_errors
from openavc.drivers.spec import CONFIG_FIELD_SOURCES
from openavc.drivers.state_variable_checks import DEVICE_VARIABLE, state_variable_errors


class ExtractError(Exception):
    """Raised when a driver file's metadata cannot be extracted."""


class _Unevaluated:
    """Stands in for a DRIVER_INFO value that cannot be read from the source.

    A Python driver may build a value from a module constant, a call or a
    comprehension. Substituting a marker (rather than giving up on the file)
    keeps every key AROUND the unevaluated value checkable — the alternative
    turns "we couldn't read this one value" into "we checked none of these
    keys", which is the hole this check exists to close.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unevaluated>"


UNEVALUATED = _Unevaluated()

# ``UNEVALUATED_KEY`` — the placeholder for `**expr` whose own keys can't be
# read — is imported above rather than defined here, and stays importable from
# this module for the callers that already do. Only this reader ever emits it;
# the rules that have to RECOGNISE it are shared with the YAML surface, so it
# is defined with them.


def lenient_ast_value(node: Any, path: str, opaque: list[str]) -> Any:
    """AST -> value, substituting UNEVALUATED where it can't be evaluated.

    Unlike a strict literal evaluation (which rejects a non-literal outright,
    because a catalog index field must be static), this keeps walking so the
    KEYS of a driver's runtime blocks can be checked even when their values are
    computed. Every substitution is recorded in ``opaque`` so the coverage gap
    is reported rather than passing silently.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        result: dict[Any, Any] = {}
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                key = k.value
                result[key] = lenient_ast_value(v, f"{path}.{key}", opaque)
            elif k is None:
                # `**expr` — merge a literal mapping; for a comprehension,
                # keep its value template (its keys are the entry's keys).
                if isinstance(v, ast.Dict):
                    merged = lenient_ast_value(v, path, opaque)
                    if isinstance(merged, dict):
                        result.update(merged)
                elif isinstance(v, ast.DictComp):
                    result[UNEVALUATED_KEY] = lenient_ast_value(
                        v.value, f"{path}.*", opaque
                    )
                else:
                    opaque.append(f"{path}.**" if path else "**")
                    result[UNEVALUATED_KEY] = UNEVALUATED
            else:
                opaque.append(f"{path}.<computed key>".lstrip("."))
                result[UNEVALUATED_KEY] = lenient_ast_value(v, f"{path}.*", opaque)
        return result
    if isinstance(node, (ast.List, ast.Tuple)):
        return [
            lenient_ast_value(item, f"{path}[]", opaque) for item in node.elts
        ]
    if isinstance(node, ast.DictComp):
        # A whole block built by comprehension: its entries all share the
        # value template, so check that.
        return {UNEVALUATED_KEY: lenient_ast_value(node.value, f"{path}.*", opaque)}
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        return -node.operand.value
    opaque.append(path.lstrip(".") or "<root>")
    return UNEVALUATED


def driver_info_ast(filepath: Path) -> ast.Dict:
    """Return the ``DRIVER_INFO = {...}`` dict node from a Python driver.

    Only a class attribute counts, because that is what the runtime loads: it
    looks for a ``BaseDriver`` subclass and reads ``DRIVER_INFO`` off the
    class, so a module-level dict of that name would leave the driver
    unregistered. Which class is THE rule ``python_driver_class`` keeps.

    An annotated assignment (``DRIVER_INFO: dict[str, Any] = {...}``) counts
    too. The runtime cannot tell the difference — an annotation is not part of
    the value — and ``declares_driver_info`` below already accepts one, so
    rejecting it here meant a file was recognised as a driver and then
    reported as having no ``DRIVER_INFO`` at all.
    """
    try:
        source = python_source(filepath)
    except SourceDecodeError as e:
        raise ExtractError(str(e)) from e
    return _driver_info_node(source, filepath.name)


def _driver_info_node(source: str, filename: str) -> ast.Dict:
    """``driver_info_ast`` on source text, named ``filename`` in messages."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ExtractError(f"{filename} has a syntax error: {e}")
    found = driver_class_info(tree)
    if found is not None:
        return found
    raise ExtractError(
        f"{filename}: no DRIVER_INFO class attribute found. "
        "Python drivers must define `DRIVER_INFO = {...}` inside a class body."
    )


def declares_driver_info(filepath: Path) -> bool:
    """True when a ``.py`` file is *trying* to be a driver.

    Deliberately broader than ``driver_info_ast``: it accepts a module-level
    ``DRIVER_INFO`` as well as a class attribute, so a file that declares one
    in the wrong place is reported by the tools rather than silently skipped as
    "not a driver". Function-local assignments are ignored (a helper that
    builds a throwaway dict of that name is not a driver).

    A plain substring match would also catch build scripts, docs and helpers
    that mention the name in a comment or string literal, and the tools would
    then report them as broken drivers. Parsing the AST keeps it honest.

    A file the import cannot decode answers True, so a walk reports it.
    """
    try:
        tree = ast.parse(python_source(filepath))
    except SourceDecodeError:
        return True
    except (SyntaxError, OSError):
        return False
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    statements = [*tree.body, *(item for node in classes for item in node.body)]
    return any(_binds_driver_info(node) for node in statements)


def _binds_driver_info(node: ast.stmt) -> bool:
    """A statement assigning the name ``DRIVER_INFO``, among others or annotated."""
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == "DRIVER_INFO" for t in node.targets)
    if isinstance(node, ast.AnnAssign):
        return isinstance(node.target, ast.Name) and node.target.id == "DRIVER_INFO"
    return False


def extract_python_driver_info_full(
    filepath: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Extract a Python driver's WHOLE DRIVER_INFO, plus unevaluated spots.

    The catalog's index extraction keeps ~20 metadata keys and drops the rest,
    so a driver's behavioral blocks (commands, state_variables, config_schema,
    child_entity_types, ...) never met the contract. This reads all of it so
    unknown-key checking can.
    """
    return _read_driver_info(driver_info_ast(filepath))


def extract_python_driver_info_from_source(
    source: str, filename: str,
) -> tuple[dict[str, Any], list[str]]:
    """``extract_python_driver_info_full`` on source text not yet on disk
    (the Code view's save), named ``filename`` in messages."""
    return _read_driver_info(_driver_info_node(source, filename))


def _read_driver_info(node: ast.Dict) -> tuple[dict[str, Any], list[str]]:
    opaque: list[str] = []
    value = lenient_ast_value(node, "", opaque)
    if not isinstance(value, dict):  # pragma: no cover - node is an ast.Dict
        raise ExtractError("DRIVER_INFO is not a mapping")
    return value, sorted(set(opaque))


# Checks that need the loaded driver class, named so a caller working from the
# source text alone can say which ones it could not run. Keep in step with the
# ``overrides_*`` arguments below.
CLASS_DEPENDENT_CHECKS: tuple[str, ...] = (
    "does a kind:'setup' action have a run_setup_action override",
    "does device_settings have a set_device_setting override",
)


def check_python_driver_info(
    info: dict[str, Any],
    *,
    strict: bool = False,
    overrides_run_setup_action: bool | None = None,
    overrides_set_device_setting: bool | None = None,
) -> DefinitionVerdict:
    """THE verdict on a Python driver's ``DRIVER_INFO``, at either kind of door.

    Two kinds of rule come out. The ``errors`` are the rules a Python driver
    has always been held to here (``_PythonWalk``). The checks of the sections
    both kinds of driver declare alike (``_SHARED_SECTIONS``: the state
    variables, the child types, each command's parameter aids and its
    ``sets:`` and ``query_for:``, the push block, the serial line settings in
    ``default_config``, the ``discovery:`` block; the routing block is one of the first kind) refuse only in ``strict`` mode, where a driver is written
    (``drivers.check``, catalog CI, the Code view's save). Where a driver is
    loaded or a device connects they are the ``notices``, so a driver that
    worked before still does and the door can say what is wrong with it. A
    YAML command's wire rules are never asked: how a command sends, a
    setting's write block, ``kind: setup``, the transport list.

    A value the source reader could not see is never judged, nor anything
    judged against a section it could not read whole (``_Coverage``, the one
    skip); ``python_driver_reference_skips`` names what each skip left
    unchecked. Each part is asked through the definition walk's guard, so a
    value of a kind a check does not read is a message, never an exception.

    The two ``overrides_*`` arguments carry facts only the loaded class knows.
    Pass ``None`` (the default) from a caller reading the source text, and
    those checks are skipped — see ``CLASS_DEPENDENT_CHECKS``.
    """
    walk = _PythonWalk(info, overrides_run_setup_action, overrides_set_device_setting)
    if strict:
        return DefinitionVerdict(walk.errors + walk.shared, [])
    return DefinitionVerdict(walk.errors, walk.shared)


def python_driver_refusals(info: dict[str, Any]) -> list[str]:
    """What the author's doors refuse in a ``DRIVER_INFO`` read from source:
    a key the contract does not declare, then ``python_driver_info_issues``.
    ``drivers.check`` and the Code view's save ask this; catalog CI asks the
    two itself, around its own platform-floor rule."""
    return unknown_key_errors(info) + python_driver_info_issues(info)


def python_driver_info_issues(
    info: dict[str, Any],
    *,
    overrides_run_setup_action: bool | None = None,
    overrides_set_device_setting: bool | None = None,
) -> list[str]:
    """Every rule a Python driver's ``DRIVER_INFO`` is held to where a driver is
    written: ``check_python_driver_info``'s strict verdict, as the plain list
    ``drivers.check``, catalog CI and the Code view's save refuse on."""
    return check_python_driver_info(
        info,
        strict=True,
        overrides_run_setup_action=overrides_run_setup_action,
        overrides_set_device_setting=overrides_set_device_setting,
    ).errors


class _PythonWalk:
    """``check_python_driver_info``'s walk over one ``DRIVER_INFO``: a method
    per rule a Python driver has always been held to, in the order they
    report (``errors``), then each shared section (``shared``). Every part is
    asked through the definition walk's guard, tagged with its section."""

    def __init__(
        self,
        info: dict[str, Any],
        overrides_run_setup_action: bool | None,
        overrides_set_device_setting: bool | None,
    ) -> None:
        self.info = info
        self.coverage = _Coverage(info)
        self.overrides_run_setup_action = overrides_run_setup_action
        self.overrides_set_device_setting = overrides_set_device_setting
        self.errors = _TaggedErrors()
        self.shared = _TaggedErrors()
        for part, section in self.parts():
            _guarded(self.errors, part, section)
        for section, check in _SHARED_SECTIONS:
            _guarded(self.shared, partial(self._shared, check), section)

    def parts(self) -> list[tuple[Callable[[], None], str]]:
        return [
            (self.actions, "actions"),
            (self.platform_floor, "min_platform_version"),
            (self.routing, "routing"),
            (self.setup_override, "actions"),
            (self.learned_config, "config_schema"),
            (self.device_settings, "device_settings"),
            (self.commands, "commands"),
            (self.child_references, "commands"),
            (self.conversions, "state_variables"),
        ]

    def _shared(self, check: Callable[[dict[str, Any], _Coverage], list[str]]) -> None:
        self.shared.extend(check(self.info, self.coverage))

    def actions(self) -> None:
        """The actions / quick_actions blocks, by the SHARED rule function, not
        a second copy of it: this module used to carry its own, narrower and
        worded differently, so the same broken driver was described one way
        by the catalog and another by the Builder. ``validate_actions`` is
        surface-neutral: what a YAML definition's actions are held to on top
        of it (kind:"setup" needs a Python driver, an entry's param aids) is
        ``action_checks.yaml_action_errors``. A block the reader could not
        see at all is not judged."""
        visible = {
            key: value for key, value in self.info.items()
            if key not in _ACTION_BLOCKS or self.coverage.section_seen(key)
        }
        self.errors.extend(validate_actions(visible))

    def platform_floor(self) -> None:
        """The same rule the YAML half gets from the strict pass: a
        min_platform_version that undershoots what DRIVER_INFO uses. A block
        built at runtime is invisible here, which understates the floor and
        never invents one."""
        self.errors.extend(platform_version_errors(self.info, tier="python"))

    def routing(self) -> None:
        """The routing declaration, from the same shared rule the YAML half
        runs: it names child types, properties and commands this driver
        declares, and a wrong name surfaces as a matrix whose crosspoints
        never light, in a room, weeks later. Several shipped drivers build
        their commands and child properties at construction time, so it is
        asked only when the sections it names into were read whole."""
        block = self.info.get("routing")
        if block is not None and self.coverage.judged("routing", block, _routing_reads(block)):
            self.errors.extend(routing_block_errors(self.info))

    def setup_override(self) -> None:
        actions = self.info.get("actions")
        declares_setup = isinstance(actions, list) and any(
            isinstance(entry, dict) and entry.get("kind") == "setup"
            for entry in actions
        )
        if declares_setup and self.overrides_run_setup_action is False:
            self.errors.append(
                "declares a kind:'setup' action but does not override "
                "run_setup_action, so the wizard will 501 on launch"
            )

    def learned_config(self) -> None:
        """A config field's learned_from, from the same shared rule the YAML
        walk runs: the state variable it names must be declared."""
        config_schema = self.info.get("config_schema")
        if not isinstance(config_schema, dict):
            return
        for field_name, field_def in config_schema.items():
            if field_name != UNEVALUATED_KEY and _learns(field_def):
                self.errors.extend(
                    config_learned_from_errors(
                        field_name, field_def, self.info.get("state_variables"),
                    )
                )

    def device_settings(self) -> None:
        """A setting's state_key is a reference into state_variables, by the
        SAME rule the YAML surface runs."""
        settings = self.info.get("device_settings")
        if settings is not None and not isinstance(settings, dict):
            self.errors.append("device_settings must be a mapping")
            return
        for key, sdef in (settings or {}).items():
            if not isinstance(sdef, dict):
                self.errors.append(f"device_settings['{key}'] must be a mapping")
                continue
            self.errors.extend(
                device_setting_state_key_errors(
                    f"Device setting '{key}'", key, sdef, self.info.get("state_variables"),
                )
            )
        if settings and self.overrides_set_device_setting is False:
            self.errors.append(
                "declares device_settings but does not override "
                "set_device_setting, so every write will 501"
            )

    def commands(self) -> None:
        """A command's confirm, sleeps_device, read_back and file rules, from
        the same shared checks the YAML command walk runs. A Python driver is
        the one surface that may declare a file param or returns_file, so it
        gets the shape checks without the refusal, and is refused read_back,
        which only the YAML runtime sends."""
        commands = self.info.get("commands")
        if not isinstance(commands, dict):
            return
        shared = SharedCommandChecks(commands, self.info.get("transport"), python=True)
        for cmd_name, cmd_def in commands.items():
            if cmd_name != UNEVALUATED_KEY and isinstance(cmd_def, dict):
                self.errors.extend(shared.errors(cmd_name, cmd_def))

    def child_references(self) -> None:
        """References out of a command's params into child_entity_types, from
        the same shared function the YAML path calls. Its skip list is named
        by ``python_driver_reference_skips``: a skip is coverage, not a
        verdict, so it never reaches a caller as an issue."""
        reference_errors, _ = child_param_reference_errors(self.info)
        self.errors.extend(reference_errors)

    def conversions(self) -> None:
        """scale / offset / unknown, and a device setting's map, are applied
        by the YAML runtime only; on a Python driver they would silently do
        nothing, so they are refused."""
        self.errors.extend(value_conversion_errors(self.info, python=True))


_ACTION_BLOCKS = ("actions", "quick_actions")


def _learns(field_def: Any) -> bool:
    return isinstance(field_def, dict) and "learned_from" in field_def


# --- the sections both kinds of driver declare --------------------------------
# Each is one function of the DRIVER_INFO and the one skip, asking the check
# module the YAML walk asks about the same section, so a section both forms
# declare is judged in the same words whichever declared it.


def _state_variable_section(info: dict[str, Any], coverage: _Coverage) -> list[str]:
    """Each of the driver's own state variables, by ``state_variable_checks``."""
    if not coverage.section_seen("state_variables"):
        return []
    section = info["state_variables"]
    if not isinstance(section, dict):
        return ["state_variables: must be a mapping"]
    errors: list[str] = []
    for name, var_def in coverage.named(section):
        if not coverage.judged(f"state_variables.{name}", var_def):
            continue
        where = f"State variable '{name}'"
        if isinstance(var_def, dict):
            errors.extend(state_variable_errors(where, var_def, DEVICE_VARIABLE))
        else:
            errors.append(f"{where}: must be a dict")
    return errors


def _child_type_section(info: dict[str, Any], coverage: _Coverage) -> list[str]:
    """Each child type, by ``child_type_checks``: its name, id_format, state
    variables and instances: roster."""
    if not coverage.section_seen("child_entity_types"):
        return []
    section = info["child_entity_types"]
    if not isinstance(section, dict):
        return ["child_entity_types: must be a mapping"] if section else []
    roster = RosterNames(info)
    errors: list[str] = []
    for name, type_def in coverage.named(section):
        # A type's name is a key, which the reader always reads, so it is
        # judged even where the rest of the type is left out.
        judged = coverage.judged(f"child_entity_types.{name}", type_def, _roster_reads(type_def))
        errors.extend(child_type_errors(name, type_def if judged else {}, roster))
    return errors


def _command_section(info: dict[str, Any], coverage: _Coverage) -> list[str]:
    """Each command's parameter aids (``param_aid_checks``), then what its
    ``sets:`` and ``query_for:`` name (``command_checks.state_name_errors``)."""
    if not coverage.section_seen("commands"):
        return []
    section = info["commands"]
    if not isinstance(section, dict):
        return []
    declared = DeclaredSections(info)
    errors: list[str] = []
    for name, cmd_def in coverage.named(section):
        if isinstance(cmd_def, dict):
            errors.extend(_command_errors(name, cmd_def, declared, coverage))
    return errors


def _command_errors(
    name: Any, cmd_def: dict[str, Any], declared: DeclaredSections, coverage: _Coverage,
) -> list[str]:
    where = f"Command '{name}'"
    params = cmd_def.get("params")
    errors = _param_errors(name, params, coverage)
    if _names_state(cmd_def) and coverage.judged(
        f"commands.{name}", _state_naming(cmd_def), _state_naming_reads(params)
    ):
        errors.extend(state_name_errors(where, cmd_def, declared))
    return errors


def _names_state(cmd_def: dict[str, Any]) -> bool:
    return "sets" in cmd_def or "query_for" in cmd_def


def _param_errors(name: Any, params: Any, coverage: _Coverage) -> list[str]:
    """Each parameter's form aids, a parameter at a time: one the reader
    could not see is left out, and so is one whose picker follows a sibling
    it could not see (or one that may be among the parameters merged in)."""
    if _unread_value(params):
        coverage.judged(f"commands.{name}.params", params)
    if not isinstance(params, dict):
        return []
    errors: list[str] = []
    for pname, pdef in coverage.named(params):
        siblings = [params.get(ref, UNEVALUATED if _merged(params) else None) for ref in sibling_refs(pdef)]
        if coverage.judged(f"commands.{name}.params.{pname}", [pdef, *siblings]):
            errors.extend(one_param_aid_errors(f"Command '{name}'", pname, pdef, params))
    return errors


def _state_naming(cmd_def: dict[str, Any]) -> dict[str, Any]:
    """What a command's sets: and query_for: are judged from: the two, and
    of its params only what the check reads, each one's type and child type
    (a sets: value may name a param; a child_id param addresses a child).
    A merged-in param could be the one a sets: value names."""
    params = cmd_def.get("params")
    if isinstance(params, dict):
        params = {
            pname: _typed(pdef) for pname, pdef in params.items()
        } if not _merged(params) else UNEVALUATED
    return {"sets": cmd_def.get("sets"), "query_for": cmd_def.get("query_for"), "params": params}


def _typed(pdef: Any) -> Any:
    if not isinstance(pdef, dict):
        return pdef
    return {key: pdef.get(key) for key in ("type", "child_type")}


def _line_setting_section(info: dict[str, Any], coverage: _Coverage) -> list[str]:
    """Each serial line setting default_config holds, by ``serial_line``."""
    if not coverage.section_seen("default_config"):
        return []
    section = info["default_config"]
    if not isinstance(section, dict):
        return []
    seen = {
        key: value for key, value in coverage.named(section)
        if key in SERIAL_LINE_SETTINGS and coverage.judged(f"default_config.{key}", value)
    }
    return [message for _, message in line_setting_errors(seen)]


# A block judged whole by ``check`` (push_block_checks, discovery_block), unless
# the reader could not see it or a section it names into (``reads``).
def _whole_block(name: str, check: Callable[[dict[str, Any]], list[str]], reads=lambda _: ()):
    def section(info: dict[str, Any], coverage: _Coverage) -> list[str]:
        block = info.get(name)
        judged = block is not None and coverage.judged(name, block, reads(block))
        return check(info) if judged else []
    return section


_SHARED_SECTIONS: tuple[tuple[str, Callable[[dict[str, Any], _Coverage], list[str]]], ...] = (
    ("state_variables", _state_variable_section),
    ("child_entity_types", _child_type_section),
    ("commands", _command_section),
    ("push", _whole_block("push", push_block_errors, lambda block: _push_reads(block))),
    ("default_config", _line_setting_section),
    ("discovery", _whole_block("discovery", discovery_refusals)),
)


# --- the sections a block names into -----------------------------------------


def _roster_reads(type_def: Any) -> tuple[str, ...]:
    """A child type's roster names config fields (count_from, ids_from) and
    a state variable (count_from_state)."""
    instances = type_def.get("instances") if isinstance(type_def, dict) else None
    if not isinstance(instances, dict):
        return ()
    return ("config", "state_variables") if "count_from_state" in instances else ("config",)


def _state_naming_reads(params: Any) -> tuple[str, ...]:
    """sets: and query_for: name the driver's state variables, and a child
    type's too when a child_id parameter addresses one."""
    addressed = isinstance(params, dict) and any(
        isinstance(p, dict) and p.get("type") == "child_id" for p in params.values()
    )
    return ("state_variables", "child_entity_types") if addressed else ("state_variables",)


def _push_reads(block: Any) -> tuple[str, ...]:
    """A push block's templates and when: name config fields, register: and
    unregister: commands, and each_child: a child type."""
    if not isinstance(block, dict):
        return ()
    reads = ["config"]
    if "register" in block or "unregister" in block:
        reads.append("commands")
    register = block.get("register")
    entries = register if isinstance(register, list) else [register]
    if any(isinstance(entry, dict) and "each_child" in entry for entry in entries):
        reads.append("child_entity_types")
    return tuple(reads)


def _routing_reads(block: Any) -> tuple[str, ...]:
    """The routing block names commands, and the child types and properties
    its planes route between; a device that routes itself names none."""
    if not isinstance(block, dict):
        return ()
    planes = block.get("planes")
    named = [block] + (planes if isinstance(planes, list) else [])
    names_a_child = any(
        isinstance(plane, dict)
        and any(isinstance(plane.get(key), str) for key in _ROUTED_CHILD_TYPES)
        for plane in named
    )
    return ("commands", "child_entity_types") if names_a_child else ("commands",)


_ROUTED_CHILD_TYPES = ("destination_child_type", "source_child_type")


# --- the one skip --------------------------------------------------------------


class _Coverage:
    """THE skip: what the source reader could not see is never judged.

    A Python driver may build a value from a module constant, a call or a
    comprehension, and the reader puts its placeholder there (``UNEVALUATED``,
    or the key ``UNEVALUATED_KEY`` for keys merged in or built in code).
    Judging the placeholder as the value refuses a working driver ("must be
    a mapping", "must be an integer (got <unevaluated>)"), and so does reading
    it as absent ("missing 'label'"). So a check is not asked about:

    * a section the reader could not see at all (``section_seen``);
    * an entry or block holding anything it could not see (``judged``);
    * an entry or block naming into a section the reader could not read whole
      (``judged``'s ``reads``): one absent, empty, built in code or holding a
      name built in code, where a missing name proves nothing, since a Python
      driver may fill it at runtime. The config fields are read whole unless
      one of their sources was built in code: none declared is a fact there.

    Each skip is kept with why (``lines``), for
    ``python_driver_reference_skips`` to name. A YAML definition never holds
    the placeholder, so the YAML walk never needs this.
    """

    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info
        self._skipped: dict[str, list[str]] = {}

    def section_seen(self, name: str) -> bool:
        """Whether the section is there to judge: neither absent nor built in
        code (the skip is kept for the second)."""
        value = self.info.get(name)
        if _unread_value(value):
            self._skip(name, "could not be read from the source")
            return False
        return value is not None

    def named(self, section: dict[Any, Any]) -> list[tuple[Any, Any]]:
        """A section's entries the reader saw, the merged-key marker left out."""
        return [(name, entry) for name, entry in section.items() if name != UNEVALUATED_KEY]

    def judged(self, place: str, unit: Any, reads: tuple[str, ...] = ()) -> bool:
        """Whether a check may judge ``unit`` (kept as a skip when not)."""
        if holds_unread(unit):
            self._skip(place, "holds a value that could not be read from the source")
            return False
        for section in reads:
            if not self._read_whole(section):
                self._skip(place, f"names into {section}, which could not be read whole from the source")
                return False
        return True

    def lines(self) -> list[str]:
        """Each skip as the places it left unchecked and why, in the order met."""
        return [f"{', '.join(places)} ({why})" for why, places in self._skipped.items()]

    def _skip(self, place: str, why: str) -> None:
        places = self._skipped.setdefault(why, [])
        if place not in places:
            places.append(place)

    def _read_whole(self, section: str) -> bool:
        if section == "config":
            return self._config_read_whole()
        value = self.info.get(section)
        if not _names_seen(value):
            return False
        if section == "child_entity_types":
            return all(_child_type_read_whole(type_def) for type_def in value.values())
        return True

    def _config_read_whole(self) -> bool:
        """The config field names, in three sources: none of them built in
        code (one left out is a fact there, not a gap)."""
        sources = (self.info.get(name) for name in CONFIG_FIELD_SOURCES)
        return not any(_unread_value(source) or _merged(source) for source in sources)


def _names_seen(value: Any) -> bool:
    """A mapping of names the reader saw whole: at least one, none built in
    code (an empty one may be filled when the driver runs)."""
    return isinstance(value, dict) and bool(value) and not _merged(value)


def _child_type_read_whole(type_def: Any) -> bool:
    """A child type whose properties the reader saw: none, or a mapping of
    them with no name built in code."""
    if not isinstance(type_def, dict):
        return False
    props = type_def.get("state_variables")
    return props is None or (isinstance(props, dict) and not _merged(props))


def _unread_value(value: Any) -> bool:
    return isinstance(value, _Unevaluated)


def _merged(value: Any) -> bool:
    """A mapping holding keys merged in or built in code."""
    return isinstance(value, dict) and UNEVALUATED_KEY in value


def holds_unread(value: Any) -> bool:
    """Anything inside ``value`` the reader could not see: THE test every
    check of a value read from Python source asks before judging it."""
    if _unread_value(value) or _merged(value):
        return True
    if isinstance(value, dict):
        return any(holds_unread(entry) for entry in value.values())
    if isinstance(value, (list, tuple)):
        return any(holds_unread(entry) for entry in value)
    return False


def python_driver_reference_skips(info: dict[str, Any]) -> list[str]:
    """What could not be checked, and why.

    A Python driver may build ``commands``, ``state_variables`` or
    ``child_entity_types`` at runtime, or a single value from a constant, and
    a check cannot judge what the reader could not see: a reference into a
    computed set cannot tell a dangling name from one that resolves at
    connect. Each such reference, entry or section is skipped and named here,
    so a caller prints the gap instead of implying it checked. The skip is
    always per reference, entry or section: nothing here ever silences a
    whole driver.
    """
    _, skips = child_param_reference_errors(info)

    # device_settings -> state_variables, skipped when the target set is
    # computed. Same reason as above: a driver may build its state
    # variables at connect, and "not in a set we could not read" is not a
    # finding.
    settings = info.get("device_settings")
    declared = info.get("state_variables")
    if isinstance(settings, dict) and settings and (
        not isinstance(declared, dict) or UNEVALUATED_KEY in declared
    ):
        skips.append(
            f"{len(settings)} device_settings state_key reference(s), "
            f"because state_variables is computed"
        )

    skips.extend(_action_reference_skips(info))
    skips.extend(_PythonWalk(info, None, None).coverage.lines())
    return skips


def _action_reference_skips(info: dict[str, Any]) -> list[str]:
    """The actions / quick_actions -> commands reference, skipped by
    ``validate_actions`` whenever the command set is only partly visible. It
    skips silently (its YAML callers can never hit the case); naming the gap
    is this function's job. A block the reader could not see names nothing
    it could count."""
    target = _commands_unseen(info.get("commands"))
    named = _named_commands(info)
    if not target or not named:
        return []
    return [f"{named} action/quick_action reference(s) into commands, because {target}"]


def _commands_unseen(commands: Any) -> str:
    """Why the command set an action names into is not whole, or ""."""
    if commands is not None and not isinstance(commands, dict):
        return "commands is computed"
    if isinstance(commands, dict) and UNEVALUATED_KEY in commands:
        return (
            f"commands is only partly visible ({len(commands) - 1} key(s) "
            f"read, the rest merged or built at runtime)"
        )
    return ""


def _named_commands(info: dict[str, Any]) -> int:
    """How many commands the actions and quick_actions blocks name, of the
    blocks the reader saw."""
    actions = info.get("actions")
    quick = info.get("quick_actions")
    promoting = [
        entry for entry in (actions if isinstance(actions, list) else ())
        if isinstance(entry, dict) and entry.get("kind", "command") == "command"
    ]
    return len(promoting) + (len(quick) if isinstance(quick, list) else 0)
