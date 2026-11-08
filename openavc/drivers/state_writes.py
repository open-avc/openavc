"""A driver's state: writing it under the device's name, and checking each
write against what the driver declares.

Every state value a driver reports goes through ``set_state`` or
``set_states`` to ``device.<id>.<property>`` in the state store. The
declaration is ``DRIVER_INFO["state_variables"]``: a key for every declared
variable exists from construction, its value ``None`` until the device
reports one.

The rules worth knowing before changing anything here:

* A write the driver never declared still lands, and is reported: a warning
  once per key at runtime, a raise (``UndeclaredStateError``) in strict mode,
  which the environment variable turns on for a test run and
  ``strict_state`` for one driver alone. ``set_states`` checks every key
  before writing any, so strict mode fails a batch whole.
* A value that is not of its declared type is never refused: the contract
  observer is told, and the debug log says so (Python's ``bool`` passes as a
  number there, not for the observer).
* Writing ``connected`` carries every registered child entity with it
  (drivers/child_entities.py), whichever of the two doors wrote it.

``StateWrites`` is a mixin ``BaseDriver`` inherits, so every name stays on
the driver object. From its host this needs ``device_id``, ``state``,
``DRIVER_INFO``, ``LAST_ERROR_PROPERTY``, ``_observe()`` and the child
entities. ``UndeclaredStateError``, ``strict_driver_state`` and
``STRICT_DRIVER_STATE_ENV`` stay importable from ``openavc.drivers.base``.
"""

from __future__ import annotations

import os
from typing import Any

from openavc.core.state_store import StateStore, is_flat_primitive
from openavc.drivers.contract_events import TYPE_MISMATCH, UNDECLARED_STATE
from openavc.utils.logger import get_logger

# These lines have always logged under the driver runtime's name, which the
# IDE's log view shows as their source.
log = get_logger("openavc.drivers.base")


# Env var that turns driver-contract violations from a warning into a raise.
# Set in openavc's own tests/conftest.py and in a driver author's test harness,
# so a driver whose code writes something its DRIVER_INFO never declared fails
# the suite the author is already running instead of shipping silently.
STRICT_DRIVER_STATE_ENV = "OPENAVC_STRICT_DRIVER_STATE"

def strict_driver_state() -> bool:
    """True when the platform should hard-fail on a driver contract violation
    rather than warn.

    Read from the environment on every call rather than cached at import: the
    flag exists to be turned on around a single driver (a test, the Builder's
    Test tab), and an import-time constant cannot be. It costs nothing on a
    correct driver, because the only caller is the violation path itself.
    """
    raw = os.environ.get(STRICT_DRIVER_STATE_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


class UndeclaredStateError(ValueError):
    """A driver wrote a state variable it never declared in
    ``DRIVER_INFO["state_variables"]``, under strict mode.

    Only raised when :func:`strict_driver_state` is on — see
    ``BaseDriver._check_undeclared_state`` for why this is a warning at
    runtime and a raise where the author is iterating.
    """


# The Python types a numeric state variable's value may be.
_NUMERIC_TYPES: dict[str, type | tuple[type, ...]] = {
    "integer": int,
    "number": (int, float),
    "float": (int, float),
}


def _type_mismatch(
    declared: str, value: Any, var_def: dict[str, Any], *, bool_is_number: bool = False,
) -> bool:
    """True when ``value`` is not of its ``declared`` type. A type this does
    not check (string, a type it does not know) never mismatches, and nor
    does an enum that declares no values.

    Python's ``bool`` is an ``int``. The contract observer reports ``True``
    written to a numeric variable; the debug log has always accepted it
    (``bool_is_number``).

    A declaration that is not a type name at all (a list, a dict) checks
    nothing either: the validator reports it, and the write still lands.
    """
    if not isinstance(declared, str):
        return False
    if declared == "enum":
        return "values" in var_def and str(value) not in [str(v) for v in var_def["values"]]
    if declared == "boolean":
        return not isinstance(value, bool)
    numeric = _NUMERIC_TYPES.get(declared)
    if numeric is None:
        return False
    if isinstance(value, bool):
        return not bool_is_number
    return not isinstance(value, numeric)


def _type_problem(declared: str, value: Any, var_def: dict[str, Any]) -> str:
    """Why ``value`` is not of its ``declared`` type, for the contract
    observer, or "" when it is."""
    if not _type_mismatch(declared, value, var_def):
        return ""
    if declared == "enum":
        return "not one of the declared values"
    return f"declared {declared}, got {type(value).__name__}"


class StateWrites:
    """A driver's state and the checks on each write, for ``BaseDriver``
    (see the module docstring)."""

    # Supplied by BaseDriver; declared here for the methods.
    DRIVER_INFO: dict[str, Any]
    LAST_ERROR_PROPERTY: str
    device_id: str
    state: StateStore
    _last_error_writes: int

    def _init_state_writes(self) -> None:
        # State keys this driver wrote without declaring them. Used to warn
        # once per key instead of once per poll — a driver that writes an
        # undeclared key writes it every cycle, and a per-poll warning is a
        # log flood that trains everyone to ignore the channel.
        self._undeclared_state_seen: set[str] = set()
        # Per-instance strict mode. None means "follow the process-wide env
        # var"; True/False override it for this driver alone. See
        # `strict_state` below for why one driver has to be able to differ.
        self._strict_state_override: bool | None = None

        # Initialize state variables from DRIVER_INFO
        self._init_state_variables()

    def _init_state_variables(self) -> None:
        """Create a state key for every variable this driver declares.

        The keys exist from construction so a binding picker, the IDE's live
        state list and a ``$device.…`` reference all know the reading is on
        offer. The values do not: each starts at ``None`` — "declared, never
        reported" — until the device says otherwise. See
        :meth:`_default_for_var_def` for why the platform does not invent one.

        ``connected`` is the exception, and it is not a reading: the platform
        knows for a fact that a driver being constructed is not connected yet,
        so ``False`` is a statement rather than a guess.
        """
        state_vars = self.DRIVER_INFO.get("state_variables", {})
        for prop_name, prop_info in state_vars.items():
            self.set_state(prop_name, self._default_for_var_def(prop_info))
        # Always set a connected state
        self.set_state("connected", False)

    @staticmethod
    def _default_for_var_def(var_def: dict[str, Any]) -> Any:
        """What a declared state variable holds before the device has spoken —
        per-device and per-child seeding both come through here, and the answer
        is always ``None``.

        The key is created; the value is not invented. Those are two different
        facts and the store already keeps them apart: ``has()`` says the driver
        declares this reading, ``get()`` returning ``None`` says nobody has
        reported it. A binding picker, the IDE's live-state list and a
        ``$device.…`` reference all read the first; every consumer that judges a
        reading reads the second.

        It used to seed a *typed* value — a numeric at its declared ``min``, an
        enum at its first value, a string at ``""`` — and downstream nothing
        could tell that from a reading. A projector that had never been reached
        published ``lamp_hours = 0`` against a true 450 and ``power = "off"``
        for a projector that might be on; the relay shipped both to the cloud as
        fresh readings, and a monitor declaring ``normal_min`` above zero fired
        on the invented one.

        ``None`` is not a new convention invented here — it is the one the rest
        of the platform already documents and was being denied its input:

        * ``core/monitors.py`` — ``None`` is ``NO_VALUE``, drawn "--", never 0.
        * ``cloud/alert_monitor.py`` — a ``None`` reading matches no threshold
          and is "neither in the set nor out of it", so nothing fires on it.
        * ``core/condition_eval.py`` — ``None`` is "no decision", so
          ``power != "on"`` cannot fire a shutdown for a device that simply has
          not polled yet.
        * ``configurable.py``'s ``count_from_state`` — ``None`` means "not
          reported yet", and the roster waits rather than building 0 children.

        ``var_def`` is unused and stays in the signature deliberately: it is
        what a caller has in hand, and taking it away would say the answer might
        one day depend on the declaration again. It must not. A driver that
        wants a value on a child at registration passes ``initial_state``.

        The old rule still exists as ``compiled_protocol.state_var_default`` and
        is still the right answer for the two consumers that must produce a
        value rather than report one — the auto-generated simulator seeding the
        wire values it will serve, and the validator working out what a variable
        would hold.
        """
        return None

    # Flat state keys the platform writes on every device regardless of what
    # the driver declares, so an author is never asked to declare them.
    # `connected` is seeded by _init_state_variables() and thereafter owned by
    # the DeviceManager; a driver that also declares it is fine either way.
    _PLATFORM_STATE_PROPS: frozenset[str] = frozenset({"connected"})

    def set_state(self, property_name: str, value: Any) -> None:
        """Set a state value under this device's namespace.

        The property should be declared in ``DRIVER_INFO["state_variables"]``;
        an undeclared one still writes, but is reported (see
        :meth:`_check_undeclared_state`).

        Writing ``connected`` also carries every registered child with it (see
        :meth:`_children_follow_parent`). This method is the hook rather than
        the half-dozen lifecycle sites that flip the flag, because a driver
        overriding ``connect`` or ``disconnect`` still writes the key through
        here — and a child left claiming presence under an unreachable parent
        is the one failure this platform cannot ship.
        """
        var_def = self.DRIVER_INFO.get("state_variables", {}).get(property_name)
        if var_def is None:
            self._check_undeclared_state(property_name)
        self._warn_on_type_mismatch(property_name, value, var_def)
        if property_name == self.LAST_ERROR_PROPERTY:
            self._last_error_writes += 1
        self.state.set(
            f"device.{self.device_id}.{property_name}",
            value,
            source=f"device.{self.device_id}",
        )
        if property_name == "connected" and self._children:
            self._children_follow_parent(bool(value))

    @property
    def strict_state(self) -> bool:
        """Whether a contract violation raises for *this* driver.

        ``OPENAVC_STRICT_DRIVER_STATE`` is the default and is read per call, so
        it can be turned on around a test run. But it is a *process* variable,
        and the surface that wants strictness most — the Builder's Test tab —
        runs inside a live server that is simultaneously polling real devices.
        Flipping the env var there would make every unrelated driver raise
        mid-poll for as long as somebody has the panel open: a device could go
        offline because a colleague was testing a different driver.

        So the flag is scoped to the instance instead. This is the same check,
        the same exception and the same message as the env var produces — only
        the question "is it on *here*" has a narrower answer. Setting it is the
        caller's job, and the only caller that does is the one-shot driver the
        live test builds, which is attached to nothing.
        """
        if self._strict_state_override is not None:
            return self._strict_state_override
        return strict_driver_state()

    @strict_state.setter
    def strict_state(self, value: bool | None) -> None:
        self._strict_state_override = value

    def _check_undeclared_state(self, property_name: str) -> None:
        """Report a write to a state variable the driver never declared.

        Deliberately *not* the same posture as the child-entity equivalent.
        ``_validate_child_prop`` raises, and ``set_child_state`` skips writes to
        an unregistered child, because those writes have nowhere to go — the
        key they would create is an orphan nothing lists. A flat key does have
        somewhere to go: the write lands, the value is live and correct, and
        the only thing missing is the driver's declaration of it. Raising here
        at runtime would take a working device offline over an author's
        omission and make the end user pay for it.

        Silence is not the alternative — silent inert state is the failure mode
        this platform exists to kill. Undeclared state is real state that no
        binding picker offers, no type is known for, and nothing in the project
        can be built against. So:

        * **Runtime** — ``log.warning`` once per key, which is what someone
          tailing the server log during bring-up sees.
        * **Strict mode** — raise, so the author's own test suite fails on it.
          That is where the author is iterating and where this is actionable.
          See :attr:`strict_state` for why that is per-driver and not only the
          process-wide env var.
        """
        if property_name in self._PLATFORM_STATE_PROPS:
            return
        # Every write, not once per key: an observer counts them.
        self._observe(UNDECLARED_STATE, state=property_name)
        driver_id = self.DRIVER_INFO.get("id", "?")
        summary = (
            f"[{self.device_id}] driver '{driver_id}' wrote state "
            f"'{property_name}', which it does not declare in "
            f'DRIVER_INFO["state_variables"]'
        )
        if self.strict_state:
            # Before the seen-set, so every write raises, not just the first.
            raise UndeclaredStateError(
                f"{summary}. Declare it, or stop writing it. (Reported as a "
                f"warning when {STRICT_DRIVER_STATE_ENV} is not set.)"
            )
        if property_name in self._undeclared_state_seen:
            return
        self._undeclared_state_seen.add(property_name)
        log.warning(
            f"{summary} — the value is live, but nothing knows its type and "
            f"no binding picker will offer it"
        )

    def _warn_on_type_mismatch(
        self, prop_label: str, value: Any, var_def: dict | None
    ) -> None:
        """Report a state value that doesn't match its declared type: to the
        contract observer when there is one, and in a debug message. Helps
        catch driver bugs without being load-bearing — listeners still
        receive the value as written.
        """
        if not var_def or value is None:
            return
        declared = var_def.get("type", "string")
        if self.contract_observer is not None:
            self._observe_type_mismatch(prop_label, value, var_def, declared)
        if not _type_mismatch(declared, value, var_def, bool_is_number=True):
            return
        if declared == "enum":
            log.debug(
                f"[{self.device_id}] State '{prop_label}' value "
                f"{value!r} not in declared enum values {var_def['values']}"
            )
        else:
            log.debug(
                f"[{self.device_id}] State '{prop_label}' declared as "
                f"{declared} but got {type(value).__name__}: {value!r}"
            )

    def _observe_type_mismatch(
        self, prop_label: str, value: Any, var_def: dict[str, Any], declared: str,
    ) -> None:
        """Tell the contract observer about a value not of its declared type."""
        problem = _type_problem(declared, value, var_def)
        if problem:
            self._observe(
                TYPE_MISMATCH, state=prop_label, declared=declared,
                value=value if is_flat_primitive(value) else repr(value),
                problem=problem,
            )

    def set_states(self, updates: dict[str, Any]) -> None:
        """Set multiple state values atomically (listeners see all changes at once)."""
        declared = self.DRIVER_INFO.get("state_variables", {})
        # Check every key before writing any, so strict mode fails the batch
        # whole rather than half-applied — the same rule set_child_state_batch
        # follows for an undeclared child prop.
        for k in updates:
            if k not in declared:
                self._check_undeclared_state(k)
        if self.LAST_ERROR_PROPERTY in updates:
            self._last_error_writes += 1
        namespaced = {
            f"device.{self.device_id}.{k}": v for k, v in updates.items()
        }
        self.state.set_batch(namespaced, source=f"device.{self.device_id}")
        # Same rule as set_state: reachability carries the children with it,
        # whichever door the driver used to write it.
        if "connected" in updates and self._children:
            self._children_follow_parent(bool(updates["connected"]))

    def get_state(self, property_name: str) -> Any:
        """Get a state value from this device's namespace."""
        return self.state.get(f"device.{self.device_id}.{property_name}")

    def delete_state(self, property_name: str) -> None:
        """Remove a state key from this device's namespace entirely.

        Unlike set_state(prop, None), the key disappears from the store (and
        from every consumer — the IDE's live state table, the cloud relay,
        WS clients — which are all notified of the removal). For drivers that
        adapt their surface at runtime: after narrowing the instance
        DRIVER_INFO to what the connected hardware actually supports, delete
        the state keys _init_state_variables() seeded for the dropped
        variables so they don't linger as phantom values.
        """
        self.state.delete(
            f"device.{self.device_id}.{property_name}",
            source=f"device.{self.device_id}",
        )
