"""Whether a driver's ``simulator.state_machines`` run the way they are written.

The runtime is ``StateMachine`` in ``openavc/simulator/base.py``. It leaves out
a machine that is not a mapping or lacks ``states``, ``initial`` or
``transitions``, and stores the machine's current state under its name. On a
command, the transition from the current state that names the command wins,
else the first ``"*"`` one; among those naming the same trigger the first
listed wins. A ``reject`` transition answers nothing and goes nowhere. Entering
a state arms every ``after_seconds`` transition from it, and the first to fire
wins.

This module checks a declaration against that before the simulator starts. It
is an error when a machine or a transition is not a mapping, a machine's
``states`` are not a list or are empty, it has no ``initial`` state or one it
does not list, it has no ``transitions`` list, a transition lacks a ``from`` (or
a ``to``, unless it rejects), names a state the machine does not list, or gives
a delay that is not a number. It is a warning when a transition repeats an
earlier one's ``from`` and ``trigger`` (said to be unreachable, which is so for
a command but not for an ``after_seconds`` one, which is still armed), has
neither a trigger nor a delay and does not reject, or names a trigger no
command has. It says nothing of a ``reject``
transition with a delay and no ``to``, which raises in the runtime as soon as
the machine enters that transition's ``from`` state and arms it. A change
to how a transition is chosen changes ``StateMachine._match`` and
``_Transitions`` together.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from openavc.simulator.validation_result import ValidationResult


def check_state_machines(result: ValidationResult, sim: dict, commands: dict) -> None:
    """Each declared machine, in order; nothing when none are declared."""
    machines = sim.get("state_machines")
    if not machines:
        return
    if not isinstance(machines, dict):
        result.error("state_machine", "simulator.state_machines must be a mapping")
        return
    command_names = set(commands.keys())
    for name, machine in machines.items():
        _check_machine(result, name, machine, command_names)


def _check_machine(result: ValidationResult, name: Any, machine: Any, command_names: set) -> None:
    """One machine: a mapping with its states, its initial state and a list
    of transitions, then each transition."""
    if not isinstance(machine, dict):
        result.error("state_machine", f"state_machine '{name}' must be a mapping")
        return
    states = _listed_states(result, name, machine)
    _check_initial(result, name, machine, states)
    transitions = machine.get("transitions")
    if not isinstance(transitions, list):
        result.error("state_machine", f"state_machine '{name}' needs a 'transitions' list")
        return
    checks = _Transitions(result, name, states, command_names)
    for i, transition in enumerate(transitions):
        checks.check(i, transition)


def _listed_states(result: ValidationResult, name: Any, machine: dict) -> list:
    """The machine's states, or an empty list (nothing to compare a state
    with) when they are not a list."""
    states = machine.get("states")
    if not isinstance(states, list) or not states:
        result.error("state_machine", f"state_machine '{name}' needs a non-empty 'states' list")
        return states if isinstance(states, list) else []
    return states


def _check_initial(result: ValidationResult, name: Any, machine: dict, states: list) -> None:
    initial = machine.get("initial")
    if initial is None:
        result.error("state_machine", f"state_machine '{name}' needs an 'initial' state")
    elif states and initial not in states:
        result.error(
            "state_machine",
            f"state_machine '{name}' initial '{initial}' is not one of {states}",
        )


@dataclasses.dataclass
class _Transitions:
    """One machine's transitions, checked in the order listed: each is a
    mapping, then (in this order) not a repeat of an earlier one, its
    ``from``, its ``to``, its delay, whether anything fires it, and whether
    its trigger names a command."""

    result: ValidationResult
    name: Any
    states: list
    command_names: set
    #: The (from, trigger) pairs listed so far: a repeat is never chosen.
    seen: set[tuple] = dataclasses.field(default_factory=set)

    def check(self, i: int, transition: Any) -> None:
        label = f"state_machine '{self.name}' transition {i}"
        if not isinstance(transition, dict):
            self.result.error("state_machine", f"{label} must be a mapping")
            return
        rejects = bool(transition.get("reject"))
        self._repeat(label, transition)
        self._listed(label, transition, "from")
        if not rejects:
            self._listed(label, transition, "to")
        self._delay(label, transition)
        self._fires(label, transition, rejects)
        self._trigger_names_a_command(label, transition)

    def _repeat(self, label: str, transition: dict) -> None:
        if "trigger" not in transition:
            return
        pair = (transition.get("from"), transition.get("trigger"))
        if pair in self.seen:
            self.result.warning(
                "state_machine",
                f"{label} duplicates an earlier transition for "
                f"from '{pair[0]}' trigger '{pair[1]}' — the first "
                f"listed wins, this one is unreachable",
            )
        self.seen.add(pair)

    def _listed(self, label: str, transition: dict, field: str) -> None:
        """``from`` or ``to``: present, and one of the machine's states."""
        state = transition.get(field)
        if state is None:
            self.result.error("state_machine", f"{label} is missing '{field}'")
        elif self.states and state not in self.states:
            self.result.error(
                "state_machine", f"{label} '{field}' state '{state}' is not one of {self.states}"
            )

    def _delay(self, label: str, transition: dict) -> None:
        if "after_seconds" in transition and not isinstance(transition["after_seconds"], (int, float)):
            self.result.error("state_machine", f"{label} 'after_seconds' must be a number")

    def _fires(self, label: str, transition: dict, rejects: bool) -> None:
        """A transition that does not reject needs a trigger or a delay."""
        if rejects:
            return
        if "trigger" in transition or "after_seconds" in transition:
            return
        self.result.warning(
            "state_machine",
            f"{label} has neither 'trigger' nor 'after_seconds' — it can never fire",
        )

    def _trigger_names_a_command(self, label: str, transition: dict) -> None:
        trigger = transition.get("trigger")
        if not trigger or trigger == "*":
            return
        if self.command_names and trigger not in self.command_names:
            self.result.warning(
                "state_machine",
                f"{label} trigger '{trigger}' matches no command name — it will never fire",
            )
