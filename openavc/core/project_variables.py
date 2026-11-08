"""The project's user variables at runtime, the part of Engine behind every
``var.*`` key: seeded at a start, kept in step with the project on a
reconcile, re-read after a backup restore, bound to the state key a
variable follows (``source_key``), and checked against its rules
(``validation``).

The rules worth knowing before changing it. A variable that persists
restores its saved value only while it still persists: one whose persist
flag was turned off reverts to its default even when state.json still holds
a value, and ``persistent_var_keys`` is the one place that says which keys
persist. A binding follows one concrete key, never a pattern, and writes
what its ``source_map`` names for the value (looked up by the value's text),
flattened to a primitive; a chain of bindings propagates while a cycle stops
after one hop. A broken rule is a warning in the log and nothing more: the
value is kept, and writes the system makes (seeding, restores) are never
checked.

Mixed into Engine (core/engine.py). Logs under the engine's name, which is
how the log view files these lines.
"""

from __future__ import annotations

from typing import Any

from openavc.core.state_persister import StatePersister
from openavc.core.state_store import GLOB_CHARS, coerce_flat_primitive
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


def persistent_var_keys(variables) -> set[str]:
    """The ``var.*`` keys of the variables that persist."""
    return {f"var.{v.id}" for v in variables if v.persist}


def bound_value(raw: Any, source_map: dict | None, var_key: str) -> Any:
    """What a binding writes for ``raw``: ``source_map``'s entry for its text,
    else ``raw``, flattened to a primitive.

    source_map values are typed Any in the schema, so an author can map to a
    list/dict; flatten those rather than letting a nested value into the
    store and out to WS / ISC / the cloud relay.
    """
    mapped = source_map.get(str(raw), raw) if source_map else raw
    value, was_coerced = coerce_flat_primitive(mapped)
    if was_coerced:
        log.warning(
            "Variable binding %s: mapped value for source %r is not a "
            "flat primitive; coerced to a JSON string", var_key, mapped,
        )
    return value


def follows_one_key(var) -> bool:
    """Whether ``var``'s source_key names one state key. A pattern would
    register a multi-key fan-in (no defined value, last-writer-wins thrash),
    so it is refused with a warning rather than silently bound."""
    if any(c in var.source_key for c in GLOB_CHARS):
        log.warning(
            "Variable '%s' source_key %r contains glob metacharacters; "
            "skipping binding (source_key must be a single state key)",
            var.id, var.source_key,
        )
        return False
    return True


def validation_warnings(var_type: str, rules, value: Any) -> list[str]:
    """What is wrong with ``value`` under a variable's rules, in words, in
    the order the log gives them. Empty when nothing is, and when the rules
    do not apply: limits are for a number variable holding a number (a bool
    counts), allowed values for a string variable holding a string."""
    if var_type == "number" and isinstance(value, (int, float)):
        return _limit_warnings(rules, value)
    if var_type == "string" and isinstance(value, str):
        return _allowed_warnings(rules.allowed, value)
    return []


def _allowed_warnings(allowed: list[str] | None, value: str) -> list[str]:
    # No list, or an empty one, allows anything.
    if allowed and value not in allowed:
        return [f"value '{value}' is not in allowed values: {allowed}"]
    return []


def _limit_warnings(rules, value) -> list[str]:
    warnings = []
    if rules.min is not None and value < rules.min:
        warnings.append(f"value {value} is below minimum {rules.min}")
    if rules.max is not None and value > rules.max:
        warnings.append(f"value {value} exceeds maximum {rules.max}")
    return warnings


def _validation_handler(var_id: str, var_type: str, rules):
    def handler(key: str, old_value: Any, new_value: Any, source: str) -> None:
        # Never on the system's own writes (seeding, restores), nor a clear.
        if source == "system" or new_value is None:
            return
        for warning in validation_warnings(var_type, rules, new_value):
            log.warning(f"Variable validation: var.{var_id} — {warning} (source={source})")
    return handler


class ProjectVariablesMixin:
    """Seeding, reconciling, restoring, binding and checking ``var.*``.

    Its state is set up by ``_init_variable_runtime``. From its host
    (Engine) it reads ``project``, ``project_path``, ``state`` and
    ``persister``.
    """

    def _init_variable_runtime(self) -> None:
        # Variable-to-state binding subscriptions
        self._var_binding_subs: list[str] = []
        self._var_validation_subs: list[str] = []
        # Keys currently mid-propagation in a variable-binding cascade. A
        # re-entrancy guard so chained bindings (var bound to another var's
        # key) propagate while genuine cycles (A<->B) terminate.
        self._var_binding_active: set[str] = set()

    def _start_variables(self) -> None:
        """At a start: the persister reads state.json, every variable is
        seeded from it or its default, the persistent ones are watched for
        changes, and the bindings and rules are set up."""
        state_file = self.project_path.parent / "state.json"
        self.persister = StatePersister(state_file, self.state)
        persisted_values = self.persister.load()
        persistent_keys = self._init_variable_values(persisted_values)
        self.persister.start(persistent_keys)
        self._rebind_variables()

    def _init_variable_values(self, persisted_values: dict[str, Any]) -> set[str]:
        """Seed ``var.*`` state from defaults / persisted values.

        Returns the set of currently-persistent var keys (for the persister to
        watch). A still-persistent variable's saved value wins over its
        default; a variable whose persist flag was turned OFF reverts to its
        default even if state.json still holds a stale value — the restore is
        gated on the *current* persist flag, not merely on the key's presence
        in the file.
        """
        if not self.project:
            return set()
        for var in self.project.variables:
            key = f"var.{var.id}"
            if var.persist and key in persisted_values:
                self.state.set(key, persisted_values[key], source="system")
            else:
                self.state.set(key, var.default, source="system")
        return persistent_var_keys(self.project.variables)

    def _reconcile_variables(self) -> None:
        """After a variables edit: seed the new variables' defaults, delete
        the ``var.*`` keys of variables that left, and hand the persister the
        keys that persist now. A variable that stayed keeps its value."""
        for var in self.project.variables:
            key = f"var.{var.id}"
            if self.state.get(key) is None:
                self.state.set(key, var.default, source="system")
        project_var_ids = {v.id for v in self.project.variables}
        orphaned_vars = [
            vid for vid in self.state.get_namespace("var.") if vid not in project_var_ids
        ]
        for vid in orphaned_vars:
            self.state.delete(f"var.{vid}")
        if orphaned_vars:
            log.info(f"Cleaned up {len(orphaned_vars)} orphaned variable state key(s)")
        if self.persister:
            self.persister.update_keys(persistent_var_keys(self.project.variables))

    def reload_persisted_state(self) -> None:
        """Re-apply state.json to the store and restart the persister.

        Called after a backup restore. A plain reload only re-subscribes the
        persister (via ``update_keys``); it never re-reads state.json, so the
        running store would keep the stale pre-restore values and the persister
        would write them straight back over the just-restored file. This reloads
        the restored values into the store — falling back to each persistent
        variable's default when the backup carried no value for it — then
        restarts the persister so it tracks the restored state cleanly.
        """
        if not self.persister or not self.project:
            return
        persisted = self.persister.load()
        for var in self.project.variables:
            if not var.persist:
                continue
            key = f"var.{var.id}"
            self.state.set(key, persisted.get(key, var.default), source="system")
        # stop() clears any prior subscriptions + the _stopped flag the caller's
        # pre-restore stop() set; start() re-subscribes with _stopped cleared.
        self.persister.stop()
        self.persister.start(persistent_var_keys(self.project.variables))

    def _rebind_variables(self) -> None:
        """The bindings, then the rules, for the variables as they are now."""
        self._bind_variable_sources()
        self._register_variable_validation()

    def _bind_variable_sources(self) -> None:
        """
        Set up auto-sync subscriptions for variables with source_key.
        When the source state key changes, the variable's value is updated
        automatically, optionally mapped through source_map.
        """
        for sub_id in self._var_binding_subs:
            self.state.unsubscribe(sub_id)
        self._var_binding_subs.clear()
        self._var_binding_active.clear()
        if not self.project:
            return
        for var in self.project.variables:
            if var.source_key and follows_one_key(var):
                self._bind_variable(var)

    def _bind_variable(self, var) -> None:
        """One binding: the source's current value now, then every change."""
        var_key = f"var.{var.id}"
        current = self.state.get(var.source_key)
        if current is not None:
            self.state.set(var_key, bound_value(current, var.source_map, var_key),
                           source="variable_binding")
        sub_id = self.state.subscribe(
            var.source_key, self._binding_handler(var_key, var.source_map)
        )
        self._var_binding_subs.append(sub_id)
        log.debug(f"Variable binding: {var_key} ← {var.source_key}"
                  f"{' (with map)' if var.source_map else ''}")

    def _binding_handler(self, var_key: str, source_map: dict | None):
        # The re-entrancy guard keys on the variable being written, not on
        # the source string: chained bindings (var B bound to var A's key)
        # propagate when A updates, while a genuine cycle (A<->B) terminates
        # after one hop. The previous blanket "ignore variable_binding source"
        # guard froze every chain.
        def handler(key: str, old_value: Any, new_value: Any, source: str) -> None:
            if var_key in self._var_binding_active:
                return  # cycle — this var is already mid-propagation
            value = bound_value(new_value, source_map, var_key)
            self._var_binding_active.add(var_key)
            try:
                self.state.set(var_key, value, source="variable_binding")
            finally:
                self._var_binding_active.discard(var_key)
        return handler

    def _register_variable_validation(self) -> None:
        """Register state listeners that warn when validation rules are violated."""
        for sub_id in self._var_validation_subs:
            self.state.unsubscribe(sub_id)
        self._var_validation_subs.clear()
        if not self.project:
            return
        for var in self.project.variables:
            if var.validation:
                sub_id = self.state.subscribe(
                    f"var.{var.id}", _validation_handler(var.id, var.type, var.validation)
                )
                self._var_validation_subs.append(sub_id)
