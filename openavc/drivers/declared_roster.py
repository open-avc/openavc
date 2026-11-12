"""A YAML driver's declared child roster: the children an ``instances:``
block names before the device ever answers.

A child type's ``instances:`` says which children exist, one of four ways:
``ids`` (a literal list, for protocol-fixed rosters of string or sparse
ids), ``count`` (1..N), ``count_from`` (an integer config field) with or
without ``count_from_state`` (a state variable the device reports, which
wins once it is a positive number), or ``ids_from`` (a comma-separated
config field). The rules worth knowing before changing anything here:

* **Registered offline, before any connect.** ``count: 7`` is seven whether
  anything answers or not, so the roster is built when the project hands
  its labels over, and a child registered while the device is unreachable
  carries ``parent_offline`` like every other child under an offline
  parent. A ``reported`` roster already says "empty" for itself.
* **Reconcile is the wanted set.** The wanted ids register, anything
  registered and no longer wanted is deregistered. Re-registering is a
  no-op, so it is safe on every connect, a config edit, and every write of
  a state variable a roster sizes from.
* **A declaration that cannot be read leaves the roster untouched**, warned
  where it is the author's mistake (an id of the wrong kind, a count that is
  not a number), silent where it is only not known yet (a count from a
  config field still blank, a reported count still 0). An id the type's
  ``id_format`` refuses is warned and skipped; the rest register.
* **The template never replaces a person's name.** ``instances.label``
  seeds a child's label only where the project has not named it.

Three readers of an ``instances:`` block, on purpose: this one is the
runtime roster; ``child_ids.declared_child_ids`` is the authoring
prediction (no ``count_from_state``, ``None`` rather than an empty roster);
the simulator's ``child_roster.declared_ids`` seeds the simulated device
from config alone. They differ where their jobs do (count 0, ``ids: []``, a
reported count).

``DeclaredRoster`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. From its host it needs ``device_id``,
``_definition``, ``config``, ``_connected``, ``_project_child_entities``,
``get_state`` and the child-entity methods ``BaseDriver`` provides; the host
calls ``_init_declared_roster()`` in ``__init__`` once its definition is
final, and ``_build_declared_roster()`` at connect.
"""

from __future__ import annotations

from typing import Any

from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# is where a device's log view files them.
log = get_logger("openavc.drivers.configurable")

#: The keys that make an ``instances:`` block a counted roster; the
#: simulator's reader (simulator/child_roster.py) reads the same set.
COUNT_KEYS = ("count", "count_from", "count_from_state")


def _count_from_state(tdef: Any) -> str | None:
    """The state variable a child type's roster sizes from, if any."""
    if not isinstance(tdef, dict):
        return None
    inst = tdef.get("instances")
    if not isinstance(inst, dict):
        return None
    key = inst.get("count_from_state")
    return str(key) if key else None


class DeclaredRoster:
    """Build and reconcile the children ``instances:`` blocks declare."""

    def _init_declared_roster(self) -> None:
        """The state keys a roster sizes from: set_state() reconciles the
        roster whenever one of them is written."""
        child_types = self._definition.get("child_entity_types") or {}
        self._roster_state_deps: set[str] = {
            key for tdef in child_types.values() if (key := _count_from_state(tdef))
        }
        self._reconciling_roster = False

    def set_project_child_entities(
        self, child_entities: dict[str, dict[str, dict[str, Any]]] | None,
    ) -> None:
        """Take the project's child metadata, then build the declared roster.

        A declarative roster is knowable without the device — ``count: 7`` is
        seven whether anything answers or not — so it does not have to wait for
        a first successful connect, and waiting is what made a mixer that had
        never been reachable read "Inputs 0 / Outputs 0 — no inputs registered
        yet" with every panel binding under it pointing at a key that did not
        exist. Rooms get commissioned before the gear is racked; a page built
        against ``input.1.level`` should not depend on the device having
        answered once.

        This is the same rule :meth:`BaseDriver._init_state_variables` follows
        one level up: the keys exist so a binding picker, the live-state list
        and a ``$device.…`` reference all know the reading is on offer, while
        the values stay unknown until the device speaks. The children register
        OFFLINE, carrying ``parent_offline`` — nothing has been reached, and
        claiming presence here would be the exact fault this contract exists to
        remove.

        Here rather than in ``__init__`` because this is the moment the roster
        has all of its inputs: the DeviceManager hands the project's labels
        over immediately after construction, and registering before them would
        seed every child with the driver's ``instances.label`` template and
        then never take the integrator's own name (register_child is
        idempotent by design).
        """
        super().set_project_child_entities(child_entities)
        self._build_declared_roster()

    def _build_declared_roster(self) -> None:
        """Register the declared rosters, a failure logged rather than raised:
        a roster is never worth failing a connect or a project load over."""
        try:
            self._register_declared_children()
        except Exception:
            log.exception(
                f"[{self.device_id}] Failed to register declared children"
            )

    def set_state(self, property_name: str, value: Any) -> None:
        """Set device-level state, and reconcile declared child rosters when a
        state var one of them sizes from (``instances.count_from_state``, e.g.
        num_outputs) changes — so the roster tracks what the device reports."""
        super().set_state(property_name, value)
        # getattr guards: set_state runs during super().__init__() (seeding
        # state-var defaults) before these attributes are assigned — those early
        # calls have no registered children to reconcile anyway.
        if (
            property_name in getattr(self, "_roster_state_deps", ())
            and not getattr(self, "_reconciling_roster", False)
        ):
            self._reconciling_roster = True
            try:
                self._register_declared_children()
            except Exception:
                log.exception(
                    f"[{self.device_id}] Failed to reconcile children after "
                    f"'{property_name}' update"
                )
            finally:
                self._reconciling_roster = False

    async def refresh_children(self) -> dict[str, int]:
        """Re-derive declared child rosters from config (the IDE's "Refresh
        from Device" button). Only meaningful when at least one child type
        declares ``instances:``; otherwise defer to BaseDriver (raises
        NotImplementedError so the API reports refresh as unsupported)."""
        child_types = self._definition.get("child_entity_types") or {}
        has_instances = any(
            isinstance(tdef, dict) and isinstance(tdef.get("instances"), dict)
            for tdef in child_types.values()
        )
        if not has_instances:
            return await super().refresh_children()
        return self._register_declared_children()

    def _register_declared_children(self) -> dict[str, int]:
        """Register and reconcile every declared roster; per child type that
        resolved, how many children registered."""
        counts: dict[str, int] = {}
        child_types = self._definition.get("child_entity_types") or {}
        for ctype, tdef in child_types.items():
            inst = tdef.get("instances") if isinstance(tdef, dict) else None
            if not isinstance(inst, dict):
                continue
            ids = self._resolve_instance_ids(ctype, tdef, inst)
            if ids is not None:
                counts[ctype] = self._reconcile_roster(ctype, ids, inst.get("label"))
        return counts

    def _reconcile_roster(
        self, ctype: str, ids: list[int | str], label_template: Any,
    ) -> int:
        """Deregister what is no longer wanted, register the wanted ids;
        how many registered."""
        self._deregister_unwanted(ctype, set(ids))
        offline_seed = self._offline_seed(ctype)
        return sum(
            self._register_declared(ctype, local_id, label_template, offline_seed)
            for local_id in ids
        )

    def _deregister_unwanted(self, ctype: str, wanted: set[int | str]) -> None:
        for local_id in list(self.list_children(ctype)):
            if local_id not in wanted:
                self.deregister_child(ctype, local_id)

    def _offline_seed(self, ctype: str) -> dict[str, Any] | None:
        """The mark a child registered while the device is unreachable takes
        (at construction, or a roster that resized after a drop): it has never
        been seen. A ``reported`` roster says "empty" for itself."""
        if not self._connected and not self._child_unknown_presence(ctype)["offline_reason"]:
            return self._child_parent_offline_presence()
        return None

    def _template_label(
        self, ctype: str, local_id: int | str, label_template: Any,
    ) -> dict[str, Any] | None:
        """The template's label for a child the project has not named."""
        if not isinstance(label_template, str) or not label_template:
            return None
        if self._project_labelled(ctype, local_id):
            return None
        return {"label": label_template.replace("{id}", str(local_id))}

    def _project_labelled(self, ctype: str, local_id: int | str) -> bool:
        try:
            padded = self._format_child_id(ctype, local_id)
        except (TypeError, ValueError):
            return False
        entry = self._project_child_entities.get(ctype, {}).get(padded)
        return bool(entry and entry.get("label"))

    def _register_declared(
        self,
        ctype: str,
        local_id: int | str,
        label_template: Any,
        offline_seed: dict[str, Any] | None,
    ) -> bool:
        """One wanted child, with its template label and the offline mark;
        False when the type's id format refuses it (warned)."""
        initial = self._template_label(ctype, local_id, label_template)
        if offline_seed is not None:
            initial = {**(initial or {}), **offline_seed}
        try:
            self.register_child(ctype, local_id, initial_state=initial)
        except (TypeError, ValueError) as e:
            log.warning(
                f"[{self.device_id}] instances: could not register "
                f"{ctype} {local_id!r}: {e}"
            )
            return False
        return True

    def _resolve_instance_ids(
        self, ctype: str, tdef: dict[str, Any], inst: dict[str, Any]
    ) -> list[int | str] | None:
        """An ``instances:`` block as the wanted local-id list, or None when
        it cannot be resolved (yet)."""
        id_format = tdef.get("id_format") or {}
        id_type = id_format.get("type", "integer")
        if "ids" in inst:
            return self._literal_ids(ctype, inst["ids"], id_type)
        if any(key in inst for key in COUNT_KEYS):
            return self._counted_ids(ctype, inst, id_type)
        return self._ids_from_config(ctype, inst.get("ids_from"), id_type)

    def _literal_ids(
        self, ctype: str, raw_ids: Any, id_type: str,
    ) -> list[int | str] | None:
        """``ids:``, each a scalar of the type's id kind; the rest skipped."""
        if not isinstance(raw_ids, list):
            log.warning(
                f"[{self.device_id}] instances: {ctype} ids must be a list"
            )
            return None
        ids: list[int | str] = []
        for item in raw_ids:
            local_id = self._literal_id(ctype, item, id_type)
            if local_id is not None:
                ids.append(local_id)
        return ids

    def _literal_id(self, ctype: str, item: Any, id_type: str) -> int | str | None:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            log.warning(
                f"[{self.device_id}] instances: {ctype} id {item!r} "
                f"is not a scalar; skipping"
            )
            return None
        if id_type == "integer":
            return self._integer_id(ctype, item, str(item).strip())
        return str(item).strip() or None

    def _integer_id(
        self, ctype: str, shown: Any, text: str, source: str = "",
    ) -> int | None:
        """One id read as an integer, or warned and skipped."""
        try:
            return int(text)
        except ValueError:
            log.warning(
                f"[{self.device_id}] instances: {ctype} id {shown!r}"
                f"{source} is not an integer; skipping"
            )
            return None

    def _counted_ids(
        self, ctype: str, inst: dict[str, Any], id_type: str,
    ) -> list[int | str] | None:
        """``count`` / ``count_from`` / ``count_from_state`` as 1..N."""
        if id_type != "integer":
            log.warning(
                f"[{self.device_id}] instances: count/count_from require "
                f"integer ids ({ctype} declares string ids — use ids_from)"
            )
            return None
        if "count" in inst:
            raw: Any = inst["count"]
        else:
            raw = self._known_count(inst)
            if raw is None:
                # No count known yet: the roster waits for one (the
                # count_from_state var arriving triggers a reconcile).
                return None
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            log.warning(
                f"[{self.device_id}] instances: {ctype} count {raw!r} is "
                f"not an integer"
            )
            return None
        if n < 0:
            return None
        return list(range(1, n + 1))

    def _known_count(self, inst: dict[str, Any]) -> Any:
        """The count the device reports, else the config field's while that
        is still unknown (offline, pre-sync); None when neither is known."""
        reported = self._reported_count(inst.get("count_from_state"))
        if reported is not None:
            return reported
        return self._configured_count(inst.get("count_from"))

    def _reported_count(self, state_key: Any) -> Any:
        """Only a POSITIVE reported count: 0, None or blank means "not
        reported yet" (a state variable's pre-connect default), and building
        0 children from it would empty the roster."""
        if not state_key:
            return None
        sval = self.get_state(str(state_key))
        try:
            if sval is not None and int(str(sval).strip()) > 0:
                return sval
        except (TypeError, ValueError):
            pass
        return None

    def _configured_count(self, field: Any) -> Any:
        """The config field's count, unless the field is blank."""
        if not field:
            return None
        cval = self.config.get(field, "")
        return cval if str(cval).strip() != "" else None

    def _ids_from_config(
        self, ctype: str, field: Any, id_type: str,
    ) -> list[int | str] | None:
        """``ids_from:``, a comma-separated config field (sparse or string
        ids); blank tokens skipped."""
        if not isinstance(field, str) or not field:
            return None
        raw_list = str(self.config.get(field, "") or "")
        ids: list[int | str] = []
        for token in raw_list.split(","):
            token = token.strip()
            if not token:
                continue
            if id_type != "integer":
                ids.append(token)
                continue
            local_id = self._integer_id(ctype, token, token, f" from '{field}'")
            if local_id is not None:
                ids.append(local_id)
        return ids
