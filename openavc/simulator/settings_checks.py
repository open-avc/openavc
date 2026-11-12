"""What a simulator declares that the validator checks the same way for both
kinds of driver.

A Python simulator's ``SIMULATOR_INFO`` and a YAML driver's ``simulator:``
section are read by one reader, ``BaseSimulator._read_device_settings``: the
seeds (``initial_state``), then the declared ``capacity`` and connection mode
(``single_client``). The validator reaches them through two doors,
``validate.validate_yaml_driver`` and ``python_parity.validate_python_driver``,
and both call these, so a rule written here holds for both and a setting the
reader learns is one ``_SETTINGS`` row. ``_SETTINGS`` holds the settings this
validator checks, not every one the reader takes: ``error_modes`` and
``delays`` go unchecked, and ``state_machines`` are ``state_machine_checks``',
for a ``simulator:`` section only.

- ``check_initial_state_types``: each declared variable's type against the
  value it is seeded with, one ``_SeedRule`` per declared type (an ``enum`` is
  compared against its values). Reported at ``info``, never as a finding: the
  declared type is the value the driver publishes, the seed the value the
  simulator puts on the wire, and the driver's own response rules map one to
  the other. Measured over the shipped corpus on 2026-07-31, 26 such
  disagreements across 9 drivers, every one a correct wire value:
  ``at_atdm_0604`` seeds ``at_link_mode: 0`` because its simulator writes it
  straight into ``g_link 0000 00 NC {state["at_link_mode"]}`` and the driver
  reads it back with ``match: 'g_link 0000 \\S+ NC 0'``; ``lg_webos`` seeds the
  SSAP string ``"Active"`` for a ``[off, standby, on]`` enum. An
  auto-generated seed is built from the declared type and matches by
  construction, so only a hand-written seed can disagree, and an author writes
  one precisely when the wire form differs. The check that can judge the wire
  value is the response parsing check, which matches the seed's reply against
  the driver's own patterns.
- ``check_declared_settings``: the declared capacity, then single_client, each
  when its key is present, against the simulator's own refusal
  (``capacity_problems``, ``single_client_problems``). A value the source
  reader could not resolve is named and passed over. Where the reader skips a
  setting declared as ``None``, this refuses it.

A declared type is matched by equality (``in`` a tuple), never by hashing it:
a ``type:`` written as a list or mapping is passed over, not raised on.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from openavc.drivers.python_info import UNEVALUATED
from openavc.simulator.capacity import capacity_problems
from openavc.simulator.single_client import single_client_problems
from openavc.simulator.validation_result import ValidationResult

#: What every initial-state type line ends with.
_WIRE_FORM = (
    "Expected when the wire form differs from the logical type — confirm the "
    "driver's response rules map it back."
)


@dataclasses.dataclass(frozen=True)
class _SeedRule:
    """The declared types a seed is held to, and the values that pass."""

    types: tuple[str, ...]
    accepts: Callable[[Any], bool]


def _not_bool(kinds: type | tuple[type, ...]) -> Callable[[Any], bool]:
    """A number of ``kinds`` that is not a ``bool``: ``bool`` is a subclass of
    ``int``, so ``True`` would pass an integer declaration without this — a
    false pass on the one confusion this check exists to catch."""
    return lambda value: isinstance(value, kinds) and not isinstance(value, bool)


_SEED_RULES: tuple[_SeedRule, ...] = (
    _SeedRule(("boolean",), lambda value: isinstance(value, bool)),
    _SeedRule(("integer",), _not_bool(int)),
    _SeedRule(("number", "float"), _not_bool((int, float))),
)


@dataclasses.dataclass(frozen=True)
class _Seed:
    """A declared state variable and the value the simulator is seeded with."""

    name: str
    declaration: dict
    value: Any

    @property
    def declared_type(self) -> Any:
        return self.declaration.get("type", "string")


def check_initial_state_types(
    result: ValidationResult, state_vars: Any, sim_initial: Any,
) -> None:
    """Each declared state variable's type against its seed, in declared order."""
    if not isinstance(state_vars, dict) or not isinstance(sim_initial, dict):
        return
    for seed in _seeds(state_vars, sim_initial):
        _check_seed(result, seed)


def _seeds(state_vars: dict, sim_initial: dict):
    """Each declared variable (named by text, declared by a mapping) that has a seed."""
    for var_name, var_def in state_vars.items():
        if not isinstance(var_name, str) or not isinstance(var_def, dict):
            continue
        if var_name in sim_initial:
            yield _Seed(var_name, var_def, sim_initial[var_name])


def _check_seed(result: ValidationResult, seed: _Seed) -> None:
    """One seed against the rule for its declared type, or its enum's values."""
    var_type = seed.declared_type
    for rule in _SEED_RULES:
        if var_type in rule.types:
            if not rule.accepts(seed.value):
                result.info(
                    "type_consistency",
                    f"State variable '{seed.name}' is {var_type} but initial_state "
                    f"value is {type(seed.value).__name__}: {seed.value!r}. {_WIRE_FORM}",
                )
            return
    if var_type == "enum":
        _check_enum_seed(result, seed)


def _check_enum_seed(result: ValidationResult, seed: _Seed) -> None:
    """An enum's seed against its values, compared as text."""
    valid_values = seed.declaration.get("values", [])
    if not isinstance(valid_values, list):
        # Declared, but built from a constant or a call, so there is nothing
        # to compare against. Say so — a silent skip here reads exactly like a
        # pass.
        result.info(
            "type_consistency",
            f"State variable '{seed.name}' is an enum whose values are computed, "
            f"so its initial value {seed.value!r} could not be checked against them.",
        )
    elif valid_values and str(seed.value) not in [str(v) for v in valid_values]:
        result.info(
            "type_consistency",
            f"State variable '{seed.name}' initial value {seed.value!r} not in enum "
            f"values: {valid_values}. {_WIRE_FORM}",
        )


def _single_client_transport(where: str, transport: Any) -> str | None:
    """A connection mode is a TCP device's; any other transport answers its
    one connection whatever the mode says."""
    if transport in (None, "tcp", UNEVALUATED):
        return None
    return f"{where} applies to a TCP device; a {transport} device answers its one connection"


def _no_transport_rule(where: str, transport: Any) -> None:
    return None


@dataclasses.dataclass(frozen=True)
class _Setting:
    """A declared setting: the simulator's refusal of its value (each an error),
    and a warning about the device it is declared on."""

    key: str
    problems: Callable[[Any], list[str]]
    transport_warning: Callable[[str, Any], str | None] = _no_transport_rule


_SETTINGS: tuple[_Setting, ...] = (
    _Setting("capacity", capacity_problems),
    _Setting("single_client", single_client_problems, _single_client_transport),
)


def check_declared_settings(
    result: ValidationResult, settings: dict, where: str, transport: Any,
) -> None:
    """Each declared setting, in ``_SETTINGS`` order; ``where`` names one, with
    ``{}`` for its key (``simulator.{}``, ``SIMULATOR_INFO["{}"]``)."""
    declared = _Declared(result, where, transport)
    for setting in _SETTINGS:
        if setting.key in settings:
            declared.check(setting, settings[setting.key])


@dataclasses.dataclass(frozen=True)
class _Declared:
    """One driver's declared settings: where its format names them, and the
    transport of the device they are declared on."""

    result: ValidationResult
    where: str
    transport: Any

    def check(self, setting: _Setting, value: Any) -> None:
        """A value the simulator would ignore is an error; one the source
        reader could not resolve is named instead."""
        where = self.where.format(setting.key)
        if value is UNEVALUATED:
            self.result.info(setting.key, f"{where} is computed, so it could not be checked.")
            return
        for problem in setting.problems(value):
            self.result.error(setting.key, f"{where}: {problem}")
        warning = setting.transport_warning(where, self.transport)
        if warning:
            self.result.warning(setting.key, warning)
