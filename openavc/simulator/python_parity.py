"""Whether a Python driver and its companion ``_sim.py`` agree: the Python
half of ``python -m openavc.simulator.validate``.

Both files are read from their source, never imported (a driver may need
dependencies this tool does not have, and importing runs its code): the
driver's ``DRIVER_INFO`` through ``scaffold.extract_driver_info``, the
platform's own reader, which refuses a file it cannot honestly read; the
simulator's ``SIMULATOR_INFO`` off the first class body that assigns it, through
the same lenient evaluator (``python_info.lenient_ast_value``). A value either
reader cannot resolve arrives as the ``UNEVALUATED`` marker, and a check that
meets one says it could not check it rather than checking an invention.

The pair's checks, in the order they report: the simulator's required fields;
its declared capacity and single_client (``settings_checks``, as for a YAML
driver); the two ids; state coverage, counted; each seed's type
(``settings_checks``, as for a YAML driver); the two transports; whether each
command's name appears in the simulator's source.

A Python simulator's ``state_machines`` are not checked here, though
``BaseSimulator._read_device_settings`` builds a Python simulator's machines as
it does a YAML driver's (``state_machine_checks`` reads a ``simulator:``
section only).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from openavc.drivers.python_info import ExtractError, lenient_ast_value
from openavc.simulator.settings_checks import check_declared_settings, check_initial_state_types
from openavc.simulator.validation_result import ValidationResult

#: The fields a ``SIMULATOR_INFO`` must declare, in the order they are reported.
_REQUIRED_FIELDS = ("driver_id", "name", "initial_state")

# Transports the simulator serves with a server of its own — one module each
# under openavc/simulator/ (tcp_simulator, http_simulator, udp_simulator, osc_simulator,
# mqtt_simulator, websocket_simulator, snmp_simulator). Anything else is a raw byte pipe with no
# simulator server, so it is simulated over TCP instead. That substitution is
# the platform's own: SimulationManager._apply_sim_redirect flips a serial
# device's transport to tcp for the duration of a simulation run precisely
# because "the simulator has no serial server"
# (openavc/core/simulation.py::_driver_transport_is_serial). A driver declaring
# `serial` or `ssh` beside a `tcp` simulator is therefore correct, and erroring
# on it asks the author to break a working simulator to satisfy a string
# comparison.
_SIMULATOR_SERVED_TRANSPORTS = frozenset(
    {"tcp", "http", "udp", "osc", "mqtt", "websocket", "snmp"}
)

# How many unseeded variable names the grouped state-coverage line lists.
_STATE_EXAMPLES = 6

# How many unmentioned command names the command-coverage line lists.
_COMMAND_EXAMPLES = 5


def validate_python_driver(driver_path: Path) -> ValidationResult:
    """Validate a Python driver and its companion _sim.py simulator."""
    driver_info, refused = _read_driver(driver_path)
    if refused is not None:
        return refused
    result = ValidationResult(str(driver_path), _driver_id(driver_info, driver_path), "python")
    if not driver_info:
        result.error("driver_info", "Could not extract DRIVER_INFO from driver file")
        return result

    sim_path = driver_path.parent / f"{driver_path.stem}_sim.py"
    if not sim_path.exists():
        result.warning("simulator_file", f"No simulator file found at {sim_path.name}")
        return result
    sim_info = read_simulator_info(sim_path)
    if not sim_info:
        result.error("simulator_info", "Could not extract SIMULATOR_INFO from simulator file")
        return result

    _check_pair(result, driver_info, sim_info, sim_path)
    return result


def _read_driver(driver_path: Path) -> tuple[dict | None, ValidationResult | None]:
    """The driver's ``DRIVER_INFO`` (None when it declares no id), or the
    finding that it could not be read.

    The reader refuses a file it cannot honestly read — a syntax error, or a
    DRIVER_INFO that is not a class attribute (the runtime loads it off a
    BaseDriver subclass, so a module-level dict of that name is a driver that
    will never load). That is reported as a finding rather than coming out of a
    validator as a traceback.
    """
    from openavc.simulator.scaffold import extract_driver_info

    try:
        return extract_driver_info(driver_path), None
    except ExtractError as exc:
        return None, _refused(driver_path, str(exc))
    except SyntaxError as exc:
        return None, _refused(driver_path, f"{driver_path.name} does not parse: {exc}")


def _refused(driver_path: Path, message: str) -> ValidationResult:
    result = ValidationResult(str(driver_path), driver_path.stem, "python")
    result.error("driver_info", message)
    return result


def _driver_id(driver_info: dict | None, driver_path: Path) -> str:
    """The driver's id, or its file's name when it declares none as text."""
    driver_id = driver_info.get("id", driver_path.stem) if driver_info else driver_path.stem
    return driver_id if isinstance(driver_id, str) else driver_path.stem


def read_simulator_info(sim_path: Path) -> dict | None:
    """``SIMULATOR_INFO`` from a Python simulator file, or None when there is
    no readable mapping.

    Reads the source rather than importing it, through the same lenient
    evaluator the driver side uses. It used to be a ``literal_eval`` with a
    regex fallback, and that fallback's worst habit was storing a value it
    could not evaluate as **its own source text**. So a simulator seeding
    ``"volume": _DEFAULT_VOLUME`` handed the type check the string
    ``"_DEFAULT_VOLUME"``, which then reported that an integer state variable
    held a str — a finding about the reader, printed as a finding about the
    driver. 16 of the 59 shipped simulators take that path.

    A value that genuinely cannot be resolved now comes back as the
    UNEVALUATED marker.
    """
    try:
        tree = ast.parse(sim_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for item in _class_body_items(tree):
        if any(_names_simulator_info(target) for target in _assigned(item)):
            value = lenient_ast_value(item.value, "SIMULATOR_INFO", [])
            return value if isinstance(value, dict) else None
    return None


def _class_body_items(tree: ast.AST):
    """Every statement in every class body, classes in ``ast.walk`` order."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield from node.body


def _assigned(item: ast.stmt) -> list[ast.expr]:
    """What a statement assigns to: an assignment's targets, or an annotated
    assignment's target when it has a value."""
    if isinstance(item, ast.Assign):
        return item.targets
    if isinstance(item, ast.AnnAssign) and item.value is not None:
        return [item.target]
    return []


def _names_simulator_info(target: ast.expr) -> bool:
    return isinstance(target, ast.Name) and target.id == "SIMULATOR_INFO"


def _check_pair(
    result: ValidationResult, driver_info: dict, sim_info: dict, sim_path: Path,
) -> None:
    """The pair's checks, in the order they report."""
    for field in _REQUIRED_FIELDS:
        if field not in sim_info:
            result.error("simulator_info", f"SIMULATOR_INFO missing required field: '{field}'")
    check_declared_settings(
        result, sim_info, 'SIMULATOR_INFO["{}"]', driver_info.get("transport"),
    )
    _check_ids(result, driver_info.get("id"), sim_info.get("driver_id"))
    _check_state(result, driver_info, sim_info)
    _check_transports(
        result, driver_info.get("transport", "tcp"), sim_info.get("transport", "tcp"),
    )
    _check_command_names(
        result, sim_path.read_text(encoding="utf-8"), driver_info.get("commands", {}),
    )


def _check_ids(result: ValidationResult, driver_id: Any, sim_driver_id: Any) -> None:
    """The driver's id against the one its simulator names, when both name one."""
    if not (driver_id and sim_driver_id):
        return
    if driver_id != sim_driver_id:
        result.error(
            "driver_id_match",
            f"DRIVER_INFO id ({driver_id!r}) != SIMULATOR_INFO driver_id ({sim_driver_id!r})",
        )


def _check_state(result: ValidationResult, driver_info: dict, sim_info: dict) -> None:
    """State coverage and each seed's type, each side named when it is computed.

    A value the reader could not resolve statically arrives as the
    UNEVALUATED marker rather than a dict, and it is named rather than skipped
    — the old reader's regex fallback always produced *something*, so a
    computed block used to be checked against an invention instead of reported
    as unreadable.
    """
    sim_initial = _readable(
        result, sim_info.get("initial_state", {}),
        "SIMULATOR_INFO initial_state is computed, so state coverage and "
        "initial-state types could not be checked.",
    )
    driver_state_vars = _readable(
        result, driver_info.get("state_variables", {}),
        "DRIVER_INFO state_variables are computed, so state coverage and "
        "initial-state types could not be checked.",
    )
    if sim_initial is not None:
        check_state_coverage(result, driver_state_vars or {}, sim_initial)
    check_initial_state_types(result, driver_state_vars or {}, sim_initial or {})


def _readable(result: ValidationResult, value: Any, unreadable: str) -> dict | None:
    """``value`` when it is a mapping; otherwise None, and the ``unreadable``
    line."""
    if isinstance(value, dict):
        return value
    result.info("state_coverage", unreadable)
    return None


def check_state_coverage(
    result: ValidationResult,
    driver_state_vars: dict,
    sim_initial: dict,
) -> None:
    """Compare a Python driver's state variables against its simulator's seeds.

    Reported as one counted line, not as a warning per name, because the two
    sides are **different namespaces** — the same reason
    ``settings_checks.check_initial_state_types`` reports at info, and the
    same reason the YAML half of this check was downgraded years earlier.

    ``SIMULATOR_INFO.initial_state`` seeds what the device puts on the wire.
    ``DRIVER_INFO.state_variables`` are what the driver publishes after
    reading it, and a driver that does any work at all between the two has
    names that cannot match. Measured over the shipped corpus on 2026-07-31,
    this check produced 268 warnings and every one inspected was that:

      viewsonic_cde   sim seeds power_code / mute_code / freeze_code /
                      hub_temp; driver publishes power / mute / freeze /
                      amb_temperature_c. 21 warnings, 21 correct pairs.
      pjlink_class1   sim answers the lamp query from one lamp_hours key;
                      the driver fans it into lamp1_hours..lamp8_hours plus
                      six error_* flags. 17 warnings, nothing to seed.
      epson_escvp     sim seeds the ESC/VP command tokens themselves (PWR,
                      LAMP, ERR); driver publishes power_state, lamp_hours,
                      error_status. 11 warnings, zero name overlap, correct.

    Telling those apart from a simulator that genuinely models nothing needs
    the driver's parsing code, which is not readable here — and every
    threshold that looked promising (name overlap, seeds-per-variable ratio)
    put pjlink_class1 on the wrong side of it. So the numbers are stated and
    the judgement is left with the author, which is the honest shape: a
    simulator seeding 3 keys against 33 state variables is visible in the
    line without the tool having to assert what it cannot know.

    The one case worth a finding needs no threshold: a simulator that seeds
    nothing at all while the driver declares state. There is no naming
    convention under which that models the device.
    """
    unseeded = [
        name
        for name in driver_state_vars
        if isinstance(name, str) and name not in sim_initial
    ]
    if not unseeded:
        return

    if not sim_initial:
        result.warning(
            "state_coverage",
            f"SIMULATOR_INFO seeds no initial_state at all, but the driver "
            f"declares {len(driver_state_vars)} state variable(s) — the "
            f"simulator models none of this device, so nothing it answers "
            f"can exercise the driver's parsing."
        )
        return

    result.info(
        "state_coverage",
        f"SIMULATOR_INFO seeds {len(sim_initial)} state key(s); "
        f"{len(unseeded)} of the driver's {len(driver_state_vars)} state "
        f"variable(s) are not among them BY NAME: "
        f"{_examples(unseeded, _STATE_EXAMPLES, ' (+{} more)')}. Usually correct — "
        f"the simulator seeds what goes on the wire and the driver publishes "
        f"what it computes from that, so the names differ. Worth a look only "
        f"if the counts say the simulator models far less than the driver "
        f"reads."
    )


def _examples(shown: list[str], limit: int, more: str) -> str:
    """The first ``limit`` of ``shown``, then ``more`` with how many were left
    out."""
    text = ", ".join(shown[:limit])
    rest = len(shown) - limit
    if rest > 0:
        text += more.format(rest)
    return text


def _check_transports(result: ValidationResult, driver_transport: Any, sim_transport: Any) -> None:
    """The driver's transport against its simulator's; a TCP stand-in for a
    transport the simulator has no server for is the supported substitution."""
    if driver_transport == sim_transport:
        return
    if driver_transport not in _SIMULATOR_SERVED_TRANSPORTS and sim_transport == "tcp":
        result.info(
            "transport_match",
            f"Driver transport {driver_transport!r} has no simulator server, "
            f"so it is simulated over TCP. That is the supported "
            f"substitution, not a mismatch."
        )
    else:
        result.error(
            "transport_match",
            f"DRIVER_INFO transport ({driver_transport!r}) != "
            f"SIMULATOR_INFO transport ({sim_transport!r})"
        )


def _check_command_names(result: ValidationResult, sim_source: str, driver_commands: Any) -> None:
    """Whether each command's name appears in the simulator's source.

    A Python simulator's dispatch cannot be proven statically — a binary
    protocol answers raw bytes and never writes a logical command name
    anywhere — so this is a heads-up, not a finding, and it reports at
    ``info``.

    It used to be a single warning gated on ``not exact_mentions``: one
    command name appearing anywhere in the file silenced the check for every
    other command. ``verillon_beacon`` validated with zero warnings because
    the string ``set_bitrate`` occurred inside ``"stream.set_bitrate"`` and its
    other three commands were never looked at. Reporting which names were not
    found is the same evidence without the cliff.
    """
    if not isinstance(driver_commands, dict):
        result.info(
            "command_coverage",
            "DRIVER_INFO commands are computed, so command coverage could not "
            "be checked.",
        )
        return
    command_names = [name for name in driver_commands if isinstance(name, str)]
    unmentioned = [name for name in command_names if not _mentioned(name, sim_source)]
    if unmentioned:
        names = [repr(name) for name in unmentioned]
        result.info(
            "command_coverage",
            f"{len(unmentioned)} of {len(command_names)} DRIVER_INFO command "
            f"name(s) do not appear in the simulator source: "
            f"{_examples(names, _COMMAND_EXAMPLES, ', … (+{} more)')}. A "
            "simulator that dispatches on raw protocol bytes will never "
            "mention them, so this is only worth checking when the simulator "
            "does dispatch by name."
        )


def _mentioned(name: str, source: str) -> bool:
    """``name`` as a standalone token, so ``power`` does not satisfy ``power_on``."""
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", source))
