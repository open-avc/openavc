"""Reading a Python driver's ``DRIVER_INFO`` without importing it.

A ``.py`` driver's contract lives in a ``DRIVER_INFO`` dict on its class. Every
door that wants to check that contract before the driver runs — the standalone
checker, ``simulator.validate``, the community catalog's CI — has to read it
from the source text, because importing a driver executes arbitrary code and
needs its dependencies present.

This module is that reader, plus the structural rules that can be decided from
the dict alone. It exists so there is exactly one of each: the catalog's
``build_index.py`` used to carry its own copy of the AST walk, and the runtime
loader its own copy of the structural rules.

Purity contract, same as ``spec`` and ``avcdriver_semantic``: standard library
only. The community catalog vendors this file and runs it in a CI job that
installs no ``openavc`` package. An import-guard test enforces it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from server.drivers.avcdriver_semantic import (
    UNEVALUATED_KEY,
    child_param_reference_errors,
    validate_actions,
)


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
    unregistered.
    """
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ExtractError(f"{filepath.name}: syntax error — {e}")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if (
                    isinstance(item, ast.Assign)
                    and len(item.targets) == 1
                    and isinstance(item.targets[0], ast.Name)
                    and item.targets[0].id == "DRIVER_INFO"
                    and isinstance(item.value, ast.Dict)
                ):
                    return item.value
    raise ExtractError(
        f"{filepath.name}: no DRIVER_INFO class attribute found. "
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
    """
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return False
    candidates = list(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            candidates.extend(node.body)
    for node in candidates:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(t, ast.Name) and t.id == "DRIVER_INFO"
                for t in node.targets
            ):
                return True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "DRIVER_INFO":
                return True
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
    opaque: list[str] = []
    value = lenient_ast_value(driver_info_ast(filepath), "", opaque)
    if not isinstance(value, dict):  # pragma: no cover - node is an ast.Dict
        raise ExtractError(f"{filepath.name}: DRIVER_INFO is not a mapping")
    return value, sorted(set(opaque))


# Checks that need the loaded driver class, named so a caller working from the
# source text alone can say which ones it could not run. Keep in step with the
# ``overrides_*`` arguments below.
CLASS_DEPENDENT_CHECKS: tuple[str, ...] = (
    "does a kind:'setup' action have a run_setup_action override",
    "does device_settings have a set_device_setting override",
)


def python_driver_info_issues(
    info: dict[str, Any],
    *,
    overrides_run_setup_action: bool | None = None,
    overrides_set_device_setting: bool | None = None,
) -> list[str]:
    """Structural rules for a Python driver's ``DRIVER_INFO``.

    STRUCTURE is static even when content is not: a Python driver may populate
    ``commands`` or state at runtime (the Q-SYS pattern), so cross-references
    against the class-level dict can false-positive — but a malformed entry is
    decidable, and used to be silently skipped by the action resolver (the
    button just never appears) or fail at first write. YAML drivers get the
    equivalent from ``validate_driver_definition``.

    The two ``overrides_*`` arguments carry facts only the loaded class knows.
    Pass ``None`` (the default) from a caller reading the source text, and
    those checks are skipped — see ``CLASS_DEPENDENT_CHECKS``.
    """
    issues: list[str] = []

    # The actions / quick_actions block is validated by the SHARED rule
    # function, not a second copy of it. This module used to carry its own —
    # narrower (it checked shape but never that a kind:"command" action names
    # a command that exists) and worded differently, so the same broken
    # driver was described one way by the catalog and another by the Builder.
    # ``validate_actions`` is surface-neutral: the one YAML-only action rule
    # (kind:"setup" needs a Python driver) lives in its caller, not in it.
    issues.extend(validate_actions(info))

    actions = info.get("actions")
    declares_setup = isinstance(actions, list) and any(
        isinstance(entry, dict) and entry.get("kind") == "setup"
        for entry in actions
    )
    if declares_setup and overrides_run_setup_action is False:
        issues.append(
            "declares a kind:'setup' action but does not override "
            "run_setup_action — the wizard will 501 on launch"
        )

    settings = info.get("device_settings")
    if settings is not None and not isinstance(settings, dict):
        issues.append("device_settings must be a mapping")
    elif isinstance(settings, dict):
        for key, sdef in settings.items():
            if not isinstance(sdef, dict):
                issues.append(f"device_settings['{key}'] must be a mapping")
        if settings and overrides_set_device_setting is False:
            issues.append(
                "declares device_settings but does not override "
                "set_device_setting — every write will 501"
            )

    # References out of a command's params into child_entity_types, from the
    # same shared function the YAML path calls. Its skip list is dropped here
    # (the return type is a plain error list every door already consumes) and
    # reported separately by ``python_driver_reference_skips`` — a skip is
    # coverage, not a verdict, so it must never reach a caller as an issue.
    reference_errors, _ = child_param_reference_errors(info)
    issues.extend(reference_errors)

    return issues


def python_driver_reference_skips(info: dict[str, Any]) -> list[str]:
    """Cross-references that could not be decided, and why.

    A Python driver may build ``commands`` or ``child_entity_types`` at
    runtime — the Q-SYS pattern — and a check whose target set is computed
    cannot tell a dangling reference from one that resolves at connect. Those
    references are skipped and named here, so a caller prints the gap instead
    of implying it checked. The skip is always per reference: nothing here
    ever silences a whole driver.
    """
    _, skips = child_param_reference_errors(info)

    # The actions / quick_actions -> commands reference, skipped by
    # ``validate_actions`` whenever the command set is only partly visible.
    # It skips silently (its YAML callers can never hit the case); naming the
    # gap is this function's job.
    commands = info.get("commands")
    if commands is not None and not isinstance(commands, dict):
        target = "commands is computed"
    elif isinstance(commands, dict) and UNEVALUATED_KEY in commands:
        target = (
            f"commands is only partly visible ({len(commands) - 1} key(s) "
            f"read, the rest merged or built at runtime)"
        )
    else:
        target = ""
    if target:
        named = sum(
            1 for entry in (info.get("actions") or [])
            if isinstance(entry, dict) and entry.get("kind", "command") == "command"
        ) + len(info.get("quick_actions") or [])
        if named:
            skips.append(
                f"{named} action/quick_action reference(s) into commands — "
                f"{target}"
            )

    return skips
