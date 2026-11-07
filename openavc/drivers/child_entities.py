"""Child entities: the sub-units a device owns, and the platform's rules for them.

A child entity is an encoder or decoder on a video matrix controller, a zone on
a DSP, a video wall slot on a presentation switcher. A driver declares the kinds
in ``DRIVER_INFO["child_entity_types"]`` and registers live instances with
``register_child()``. Each child's state lives under the platform's key shape

    device.<parent_id>.<child_type>.<local_id_padded>.<property>

so fnmatch subscribers and the IDE can address children without a driver
inventing its own key shapes.

The rules worth knowing before changing anything here:

* Four keys on every child are the platform's (``drivers/spec.py``'s
  CHILD_RESERVED_PROPS): ``online``, ``label``, ``offline_reason`` and
  ``offline_detail``. They are statements about the child, never readings off
  it, so registration seeds each one explicitly (``_child_seed``).
* The platform has one opinion about a child's presence, its parent's
  reachability. A child under an unreachable parent reads ``parent_offline``,
  and coming back undoes that code and nothing else (``_children_follow_parent``).
* A write to a child that was never registered is skipped and reported, never
  written: the key would be an orphan nothing lists.

``ChildEntities`` is a mixin that ``BaseDriver`` inherits, so every name stays on
the driver object: drivers read ``self._project_child_entities`` directly, and
tests reach ``BaseDriver._CHILD_RESERVED_PROPS`` on the class. ``BaseDriver``
calls ``_init_child_entities()`` before anything writes state, and supplies what
this needs from its host: ``DRIVER_INFO``, ``device_id``, ``state``,
``_observe()``, ``_warn_on_type_mismatch()`` and ``_default_for_var_def()``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from openavc.core.connection_fault import (
    CHILD_DRIVER_FAULT_CODES,
    CHILD_NOT_FITTED,
    CHILD_PARENT_OFFLINE,
    NO_FAULT_CLAIMED,
    default_child_fault_message,
    normalize_child_fault_claim,
)
from openavc.core.state_store import StateStore
from openavc.drivers.contract_events import CHILD_UNREGISTERED
from openavc.drivers.spec import (
    CHILD_PRESENCE_MODES,
    CHILD_RESERVED_PROP_SCHEMA,
    CHILD_RESERVED_PROPS,
)
from openavc.utils.logger import get_logger

# These lines have always logged under the driver runtime's name, which the
# IDE's log view shows as their source.
log = get_logger("openavc.drivers.base")

# String child local IDs are embedded directly in a flat state key
# (device.<id>.<type>.<local_id>.<prop>) and matched by fnmatch glob
# subscriptions, so they're restricted to this charset — no dots (the key
# separator), whitespace, or glob metacharacters. A driver that keys children
# by a device-native name (a Q-SYS Code Name, an MQTT topic leaf) must
# sanitize to this set and keep the original in the child's `label`.
_CHILD_STRING_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CHILD_STRING_ID_MAX_LEN = 128

# A child is (child_type, local_id) everywhere the registry keys one.
_Child = tuple[str, "int | str"]


def _in_service() -> dict[str, Any]:
    """The three presence keys of a child in service with nothing claimed."""
    return {
        "online": True,
        "offline_reason": NO_FAULT_CLAIMED,
        "offline_detail": NO_FAULT_CLAIMED,
    }


def _out_of_service(code: str, message: str = "") -> dict[str, Any]:
    """The three presence keys of a child out of service for ``code``."""
    return {
        "online": False,
        "offline_reason": code,
        "offline_detail": message or default_child_fault_message(code),
    }


def _string_child_id(child_type: str, local_id: Any, id_format: dict[str, Any]) -> str:
    """A ``string`` id checked: a str, safe characters, within ``max_length``."""
    if not isinstance(local_id, str):
        raise TypeError(
            f"Child {child_type} local_id must be str (id_format.type "
            f"is 'string'), got {type(local_id).__name__}: {local_id!r}"
        )
    if not _CHILD_STRING_ID_RE.match(local_id):
        raise ValueError(
            f"Child {child_type} local_id {local_id!r} is not a valid "
            f"string id (allowed characters: letters, digits, '_', '-')"
        )
    max_len = id_format.get("max_length", _CHILD_STRING_ID_MAX_LEN)
    if max_len and len(local_id) > max_len:
        raise ValueError(
            f"Child {child_type} local_id {local_id!r} exceeds "
            f"id_format.max_length {max_len}"
        )
    return local_id


def _integer_child_id(child_type: str, local_id: Any, id_format: dict[str, Any]) -> str:
    """An ``integer`` id checked against its range, then padded."""
    # bool is a subclass of int in Python; reject it so that
    # register_child("encoder", True) doesn't silently land at ID 1.
    if not isinstance(local_id, int) or isinstance(local_id, bool):
        raise TypeError(
            f"Child {child_type} local_id must be int, got "
            f"{type(local_id).__name__}: {local_id!r}"
        )
    _check_integer_range(child_type, local_id, id_format)
    pad = id_format.get("pad_width", 0)
    return f"{local_id:0{pad}d}" if pad else str(local_id)


def _check_integer_range(child_type: str, local_id: int, id_format: dict[str, Any]) -> None:
    """Raise unless ``local_id`` lies in [min (default 1), max (unbounded
    when undeclared)]."""
    min_id = id_format.get("min", 1)
    max_id = id_format.get("max")
    if local_id < min_id:
        raise ValueError(
            f"Child {child_type} local_id {local_id} < min {min_id}"
        )
    if max_id is not None and local_id > max_id:
        raise ValueError(
            f"Child {child_type} local_id {local_id} > max {max_id}"
        )


def _refuse_bad_child_schema(
    child_type: str, type_def: dict[str, Any], schema: Any,
) -> None:
    """Raise unless ``schema`` is a per-child schema this type may take."""
    if not type_def.get("dynamic"):
        raise ValueError(
            f"Child type {child_type!r} is not declared "
            f"`dynamic: true`; a per-child schema is only allowed for "
            f"dynamic child types"
        )
    if not isinstance(schema, dict):
        raise TypeError(
            f"Child {child_type} schema must be a dict, got "
            f"{type(schema).__name__}"
        )
    bad = next(
        ((prop, var_def) for prop, var_def in schema.items()
         if not isinstance(var_def, dict)),
        None,
    )
    if bad is not None:
        raise TypeError(
            f"Child {child_type} schema property {bad[0]!r} must map "
            f"to a var-def dict, got {type(bad[1]).__name__}"
        )


def _undeclared_prop_error(
    child_type: str, what: str, prop: Any, dynamic: Any,
) -> ValueError:
    """The refusal of a prop the child's schema does not declare."""
    return ValueError(
        f"Child {child_type} {what} {prop!r} not declared in "
        f"child_entity_types[{child_type!r}].state_variables"
        + (" (or this child's dynamic schema)" if dynamic else "")
    )


async def _fetch_in_batches(
    ids: list[Any],
    fetch: Callable[[list[Any]], Awaitable[dict[Any, dict[str, Any]]]],
    batch_size: int,
    pause: float,
) -> list[tuple[Any, dict[str, Any]]]:
    """Every ``fetch`` result for ``ids``, asked ``batch_size`` at a time with
    ``pause`` seconds between batches (none after the last)."""
    collected: list[tuple[Any, dict[str, Any]]] = []
    last_start = (len(ids) - 1) // max(batch_size, 1) * max(batch_size, 1)
    for i in range(0, len(ids), batch_size):
        results = await fetch(ids[i:i + batch_size])
        collected.extend(results.items())
        if i != last_start and pause > 0:
            await asyncio.sleep(pause)
    return collected


class ChildEntities:
    """The child-entity half of ``BaseDriver``: the registry, the state keys
    and the presence rules (see the module docstring)."""

    # Supplied by the host class.
    DRIVER_INFO: dict[str, Any]
    device_id: str
    state: StateStore

    # State keys the platform always provides for every registered child, in
    # addition to whatever the driver declares in
    # child_entity_types[<type>].state_variables. All four are driver-managed
    # — the platform seeds them and then has one opinion, about reachability,
    # because that is the one thing about a sub-unit it can see for itself:
    #
    #   online          presence, seeded from the roster's presence mode (in
    #                   service on an `assumed` roster, not_fitted on a
    #                   `reported` one) and forced down while the parent device
    #                   is unreachable (_children_follow_parent)
    #   label           the user-set friendly name, sourced from the project
    #                   file on registration and writable through the IDE/REST
    #   offline_reason  a stable code from the child fault taxonomy that
    #                   automation can match on, or None for nothing claimed —
    #                   the same value the device pair uses, and the only one
    #                   these keys ever hold for it (connection_fault's
    #                   NO_FAULT_CLAIMED, folded in at every write door)
    #   offline_detail  the sentence a person reads on the device page
    #
    # The last two are the child-level pair of device.<id>.offline_reason /
    # offline_detail, and exist because one boolean cannot tell an endpoint
    # somebody carried out of the rack from one sitting right there with a
    # wedged service — remedies that are nothing alike.
    #
    # The names are the contract, so they live in drivers/spec.py (which
    # avcdriver_semantic.py can see and openavc-drivers vendors); this is the
    # runtime's handle on them, and the list _effective_child_schema injects.
    _CHILD_RESERVED_PROPS: tuple[str, ...] = CHILD_RESERVED_PROPS

    # The definition each reserved prop gets when the driver has not declared
    # it itself. A driver that DOES declare one keeps its own (its label, help
    # and cloud_priority), which is how a driver adds wording without taking
    # the key over. Lives in spec.py because the child_set: compiler needs the
    # same types -- see CHILD_RESERVED_PROP_SCHEMA there.
    _CHILD_RESERVED_PROP_SCHEMA: dict[str, dict[str, Any]] = (
        CHILD_RESERVED_PROP_SCHEMA
    )

    def _init_child_entities(self) -> None:
        """The empty registry, set up by ``BaseDriver.__init__``."""
        # Registered child entities: {child_type: {local_id: register_epoch}}.
        # The inner mapping is a dict (not a set) so it preserves insertion
        # order, which makes list_children() output stable for tests and IDE
        # displays. The value is a monotonic registration epoch (always
        # truthy) used by poll_children to detect a child that was
        # deregistered+re-registered mid-poll (its state was reset) so a stale
        # write doesn't clobber the reset (ABA guard).
        self._children: dict[str, dict[int | str, int]] = {}
        self._child_register_seq = 0
        # Per-child dynamic state-variable schemas, for child types declared
        # `dynamic: true`. {(child_type, local_id): {prop: var_def}}. Populated
        # at register_child when a `schema=` is supplied, and consulted by
        # _effective_child_schema so a dynamic type's controls can be discovered
        # at runtime (e.g. a Q-SYS component's controls, an MQTT topic's fields)
        # instead of declared statically up-front. Empty for static child types.
        self._child_schemas: dict[
            tuple[str, int | str], dict[str, dict[str, Any]]
        ] = {}
        # Project-side child metadata: {child_type: {local_id_padded: {label, config}}}.
        # Populated by DeviceManager.add_device after construction (via
        # set_project_child_entities) so existing driver subclasses with a
        # fixed __init__ signature don't need to change. register_child
        # consults this to seed the per-child `label` state key without
        # the driver having to plumb the project schema itself.
        self._project_child_entities: dict[str, dict[str, dict[str, Any]]] = {}

    def set_project_child_entities(
        self, child_entities: dict[str, dict[str, dict[str, Any]]] | None,
    ) -> None:
        """Set the project-side child metadata for this driver instance.

        Called by DeviceManager from the project's DeviceConfig.child_entities
        after construction. Keeps the BaseDriver __init__ signature stable
        for existing driver subclasses that define their own __init__.
        """
        self._project_child_entities = dict(child_entities or {})

    # --- Presence -----------------------------------------------------------

    def _child_presence_mode(self, child_type: str) -> str:
        """Who owns this roster's ``online`` — ``"assumed"`` or ``"reported"``.

        ``instances.presence`` on the child type, defaulting to ``assumed``.
        See ``drivers/spec.CHILD_PRESENCE_MODES`` for why the same
        ``count: N`` syntax needs an author to say which kind of roster it is.
        An unknown value reads as the default rather than raising: the schema
        already refuses one at authoring time, and a roster is not worth taking
        a device offline over at runtime.
        """
        try:
            inst = self._child_type_def(child_type).get("instances")
        except ValueError:
            return "assumed"
        mode = inst.get("presence") if isinstance(inst, dict) else None
        return mode if mode in CHILD_PRESENCE_MODES else "assumed"

    def _child_unknown_presence(self, child_type: str) -> dict[str, Any]:
        """The three presence keys a child of this type carries before anything
        is known about it — at registration, and again when its parent comes
        back and the driver has not yet had a chance to say.

        On an ``assumed`` roster that is "in service, nothing claimed": the ids
        are real hardware and the device being reachable is the whole of the
        question. On a ``reported`` roster it is ``not_fitted``: the ids are
        slots, and a slot is empty until the device says something is in it.
        """
        if self._child_presence_mode(child_type) == "reported":
            return _out_of_service(CHILD_NOT_FITTED)
        return _in_service()

    @staticmethod
    def _child_parent_offline_presence() -> dict[str, Any]:
        """The three presence keys for a child whose parent is unreachable."""
        return _out_of_service(CHILD_PARENT_OFFLINE)

    def _children_follow_parent(self, connected: bool) -> None:
        """Carry every registered child with the parent's reachability.

        Called from :meth:`set_state` / :meth:`set_states` whenever
        ``connected`` is written, which is every lifecycle path there is.

        The rule, both directions, is that the platform only ever undoes what
        it did:

        * **Going offline** — a child that was in service with nothing claimed
          reads ``online: false`` with ``parent_offline``. A child already
          carrying a code keeps it: the driver's ``not_responding`` is more
          specific than ours and stays true while the parent is away, and a
          ``not_fitted`` slot does not become a fault because a cable came out
          of the mixer.
        * **Coming back** — exactly the children carrying ``parent_offline``
          return to :meth:`_child_unknown_presence`; nothing else is touched.
          The driver's next poll corrects anything that changed while it was
          away, which is the only source that can.

        The children stay REGISTERED throughout. Deregistering them would take
        the panel's bindings with them, which is the failure this exists to
        avoid — a fader bound to ``input.1.level`` must survive the cable being
        pulled and go quiet, not point at a key that no longer exists.

        Idempotent by construction: a second write of the same value finds
        nothing left to change, so no transition tracking is needed. Every
        change goes out in one batch, so a listener sees the whole roster move.
        """
        updates: dict[str, Any] = {}
        for child_type, local_id in self._registered_children():
            updates.update(
                self._presence_following_parent(child_type, local_id, connected)
            )
        if updates:
            self.state.set_batch(updates, source=f"device.{self.device_id}")

    def _registered_children(self) -> Iterator[_Child]:
        """Every registered child, type by type, each in registration order."""
        for child_type, bucket in self._children.items():
            for local_id in bucket:
                yield child_type, local_id

    def _presence_following_parent(
        self, child_type: str, local_id: int | str, connected: bool,
    ) -> dict[str, Any]:
        """The state writes one child's presence takes from the parent's
        reachability, or ``{}`` when the rule leaves it alone. A child whose id
        no longer fits its type has no key to write and is left alone too."""
        try:
            prefix = self._child_state_prefix(child_type, local_id)
        except (TypeError, ValueError):
            return {}
        reason = self.state.get(f"{prefix}.offline_reason")
        if connected:
            back = reason == CHILD_PARENT_OFFLINE
            new = self._child_unknown_presence(child_type) if back else {}
        elif reason or self.state.get(f"{prefix}.online") is False:
            new = {}
        else:
            new = self._child_parent_offline_presence()
        return {f"{prefix}.{prop}": value for prop, value in new.items()}

    # --- Types, ids and keys ------------------------------------------------

    def _child_type_def(self, child_type: str) -> dict[str, Any]:
        """Return the DRIVER_INFO child_entity_types[<type>] definition.

        Raises ValueError if the driver didn't declare this child type.
        """
        types = self.DRIVER_INFO.get("child_entity_types", {})
        if child_type not in types:
            raise ValueError(
                f"Driver {self.DRIVER_INFO.get('id', '?')} did not declare "
                f"child_entity_types[{child_type!r}]"
            )
        return types[child_type]

    def _effective_child_schema(
        self, child_type: str, local_id: int | str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Schema for one child instance's state variables, with the
        platform-managed `online` and `label` keys injected if the driver
        didn't already declare them.

        For a child type declared ``dynamic: true``, a per-child schema
        supplied at ``register_child(schema=...)`` takes precedence when
        ``local_id`` is given; otherwise the type-level ``state_variables``
        (which may be empty for a dynamic type) is used.
        """
        type_def = self._child_type_def(child_type)
        declared: dict[str, dict[str, Any]] | None = None
        if type_def.get("dynamic") and local_id is not None:
            declared = self._child_schemas.get((child_type, local_id))
        if declared is None:
            declared = dict(type_def.get("state_variables", {}))
        else:
            declared = dict(declared)
        for prop in self._CHILD_RESERVED_PROPS:
            declared.setdefault(
                prop, dict(self._CHILD_RESERVED_PROP_SCHEMA[prop])
            )
        return declared

    def _format_child_id(self, child_type: str, local_id: int | str) -> str:
        """Validate ``local_id`` against the declared id_format and return
        its string form.

        Two id kinds are supported:
          * ``integer`` (default) — zero-padded to ``id_format.pad_width``;
            must lie inside [id_format.min (default 1), id_format.max
            (optional, unbounded)].
          * ``string`` — used verbatim; must be non-empty, match
            ``[A-Za-z0-9_-]`` only (so it's safe in a flat state key and in
            glob subscriptions), and be at most ``id_format.max_length``
            (default 128) characters. For devices that key children by a
            native name (Q-SYS Code Name, MQTT topic), sanitize to this set
            and keep the original in the child's ``label``.
        """
        id_format = self._child_type_def(child_type).get("id_format", {})
        id_kind = id_format.get("type", "integer")
        if id_kind == "string":
            return _string_child_id(child_type, local_id, id_format)
        if id_kind != "integer":
            raise ValueError(
                f"Child type {child_type!r} id_format.type {id_kind!r} not "
                f"supported (only 'integer' and 'string' are supported)"
            )
        return _integer_child_id(child_type, local_id, id_format)

    def _child_state_key(
        self, child_type: str, local_id: int | str, prop: str,
    ) -> str:
        padded = self._format_child_id(child_type, local_id)
        return f"device.{self.device_id}.{child_type}.{padded}.{prop}"

    def _child_state_prefix(self, child_type: str, local_id: int | str) -> str:
        padded = self._format_child_id(child_type, local_id)
        return f"device.{self.device_id}.{child_type}.{padded}"

    def _validate_child_prop(
        self, child_type: str, local_id: int | str, prop: str,
    ) -> None:
        schema = self._effective_child_schema(child_type, local_id)
        if prop not in schema:
            raise _undeclared_prop_error(
                child_type, "property", prop,
                self._child_type_def(child_type).get("dynamic"),
            )

    def _validate_child_props(
        self, child_type: str, local_id: int | str, props: Any,
    ) -> None:
        """Every prop checked before any is written, so one bad prop aborts
        the whole write."""
        for prop in props:
            self._validate_child_prop(child_type, local_id, prop)

    def _child_writes(
        self, child_type: str, local_id: int | str, updates: dict[str, Any],
    ) -> dict[str, Any]:
        """``updates`` as the state keys and stored values they write."""
        return {
            self._child_state_key(child_type, local_id, prop):
                normalize_child_fault_claim(prop, value)
            for prop, value in updates.items()
        }

    # --- Registration -------------------------------------------------------

    def register_child(
        self,
        child_type: str,
        local_id: int | str,
        initial_state: dict[str, Any] | None = None,
        schema: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Tell the platform a child entity exists. Creates its state keys
        in one atomic batch.

        Subsequent calls with the same (child_type, local_id) are a silent
        no-op so drivers can call this opportunistically from a poll loop
        without re-initializing state. To overwrite a child's state, use
        ``set_child_state`` / ``set_child_state_batch`` instead; to change a
        dynamic child's schema, ``deregister_child`` then register again.

        ``initial_state`` overrides per-prop defaults. The platform-managed
        ``online`` / ``offline_reason`` / ``offline_detail`` keys default to
        the type's :meth:`_child_unknown_presence` — in service on an
        ``assumed`` roster, ``not_fitted`` on a ``reported`` one. Unknown props
        in ``initial_state`` raise ValueError.

        ``schema`` supplies a per-child state-variable map for child types
        declared ``dynamic: true`` — used when the child's controls are
        discovered at runtime (a Q-SYS component's controls, an MQTT topic's
        fields) rather than declared statically. Each value is a var-def dict
        (``{"type": "number"|"boolean"|"integer"|"string"|"enum", "label":
        ..., ...}``), the same shape as a static ``state_variables`` entry.
        Passing ``schema`` for a non-dynamic type raises ValueError.
        """
        self._format_child_id(child_type, local_id)   # validates id range
        type_def = self._child_type_def(child_type)
        if schema is not None:
            _refuse_bad_child_schema(child_type, type_def, schema)

        bucket = self._children.setdefault(child_type, {})
        if local_id in bucket:
            self._warn_if_schema_collides(child_type, local_id, schema)
            return
        # Stamp a fresh registration epoch. A deregister+re-register (which
        # resets the child's state) bumps it, so poll_children can tell a
        # re-registered child from the one it snapshotted (ABA guard).
        self._child_register_seq += 1
        bucket[local_id] = self._child_register_seq
        # Stored before the effective schema is computed, so the validation
        # and defaults below use the discovered controls.
        if schema is not None:
            self._child_schemas[(child_type, local_id)] = dict(schema)

        eff_schema = self._effective_child_schema(child_type, local_id)
        overrides = dict(initial_state or {})
        unknown = [prop for prop in overrides if prop not in eff_schema]
        if unknown:
            # Rolled back (the epoch stays spent) so a retry can succeed once
            # the driver fixes the call; refused before the store is touched.
            self._forget_child(child_type, local_id)
            raise _undeclared_prop_error(
                child_type, "initial_state property", unknown[0],
                type_def.get("dynamic"),
            )
        self.state.set_batch(
            self._child_seed(child_type, local_id, eff_schema, overrides),
            source=f"device.{self.device_id}",
        )

    def _warn_if_schema_collides(
        self, child_type: str, local_id: int | str,
        schema: dict[str, dict[str, Any]] | None,
    ) -> None:
        """Say so when a registered id arrives again with a different schema.

        That is the signature of a sanitized-id collision (two device-native
        names mapping to one local id) or a schema change without
        deregister_child(), both otherwise invisible: the second child just
        never appears.
        """
        if schema is not None and dict(schema) != self._child_schemas.get(
            (child_type, local_id)
        ):
            log.warning(
                f"[{self.device_id}] register_child({child_type!r}, "
                f"{local_id!r}) ignored: id already registered with a "
                f"different schema — likely an id collision after "
                f"sanitization, or a schema change without "
                f"deregister_child() first"
            )

    def _child_seed(
        self,
        child_type: str,
        local_id: int | str,
        eff_schema: dict[str, dict[str, Any]],
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """The state a newly registered child starts with, key by key.

        A prop the driver passed in ``initial_state`` takes that value. The
        reserved props are the platform's own statements about the child, not
        readings from it, so each has a value of its own rather than
        ``_default_for_var_def``'s "declared, never reported": the presence
        keys start at whatever the roster says "nothing known yet" looks like
        (in service on an ``assumed`` roster, ``not_fitted`` on a ``reported``
        one), and ``label`` at what the project calls the child. ``online``
        unknown would leave every Child Entities row, the ``N down`` count and
        the device banner unable to say anything; ``online`` seeded True on a
        ``reported`` roster drew a green dot for each of seven empty extension
        slots on a mixer. Every other prop has no reading yet.
        """
        seeded = {
            **self._seed_presence(child_type, overrides),
            "label": self._project_child_label(child_type, local_id),
        }
        values: dict[str, Any] = {}
        for prop, var_def in eff_schema.items():
            if prop in overrides:
                values[prop] = overrides[prop]
            elif prop in seeded:
                values[prop] = seeded[prop]
            else:
                values[prop] = self._default_for_var_def(var_def)
        return self._child_writes(child_type, local_id, values)

    def _seed_presence(
        self, child_type: str, overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """The presence a new child starts with.

        A driver saying "this slot IS populated" (``online: True`` and no
        reason of its own) clears the roster's default claim with it.
        Otherwise a ``reported`` roster would hold ``online: True`` beside
        ``not_fitted``, a contradiction the store cannot resolve and every
        reader would resolve differently.
        """
        if overrides.get("online") is True and "offline_reason" not in overrides:
            return _in_service()
        return self._child_unknown_presence(child_type)

    def _project_child_label(self, child_type: str, local_id: int | str) -> str:
        """The label the project file gives this child, or ``""``.

        Seeded at registration so listeners see the user's name at once,
        not ``""`` and then a delayed update once the IDE re-pushes it.
        """
        padded = self._format_child_id(child_type, local_id)
        project_entry = self._project_child_entities.get(child_type, {}).get(padded)
        return project_entry.get("label", "") if project_entry else ""

    def deregister_child(self, child_type: str, local_id: int | str) -> None:
        """Remove a child entity and delete all of its state keys.

        Silent no-op if the child isn't registered (so drivers can call this
        eagerly during reconciliation without first checking).
        """
        if not self.is_child_registered(child_type, local_id):
            return

        prefix_dot = self._child_state_prefix(child_type, local_id) + "."
        # Snapshot before mutating; iterating the live store while deleting
        # would be unsafe. The snapshot is cheap (dict copy) at the scale
        # we expect per child (~10-30 keys).
        keys_to_delete = [
            k for k in self.state.snapshot().keys() if k.startswith(prefix_dot)
        ]
        for k in keys_to_delete:
            self.state.delete(k, source=f"device.{self.device_id}")
        self._forget_child(child_type, local_id)

    def _forget_child(self, child_type: str, local_id: int | str) -> None:
        """Take one child out of the registry: its id, its type once that is
        empty, and its dynamic schema, so a later registration can supply a
        fresh one (e.g. after the device's topology changed)."""
        bucket = self._children[child_type]
        del bucket[local_id]
        if not bucket:
            del self._children[child_type]
        self._child_schemas.pop((child_type, local_id), None)

    def list_children(self, child_type: str) -> list[int | str]:
        """Local IDs of currently-registered children of ``child_type``,
        in insertion order. Returns an empty list if none are registered or
        the type is unknown to the platform tracker.
        """
        bucket = self._children.get(child_type)
        if bucket is None:
            return []
        return list(bucket.keys())

    def is_child_registered(self, child_type: str, local_id: int | str) -> bool:
        """True if ``register_child(child_type, local_id)`` has been called
        and ``deregister_child`` hasn't been called since.
        """
        return local_id in self._children.get(child_type, {})

    # --- Writing a child's state --------------------------------------------

    def _report_unregistered(
        self, door: str, child: _Child, props: Any, note: str = "",
    ) -> None:
        """Warn about, and tell the contract observer of, a write skipped
        because ``child`` was never registered (see set_child_state)."""
        child_type, local_id = child
        log.warning(
            f"[{self.device_id}] {door} unregistered child "
            f"{child_type}/{local_id}{note} skipped — call register_child first"
        )
        self._observe(
            CHILD_UNREGISTERED, child_type=child_type, local_id=local_id,
            props=sorted(props),
        )

    def set_child_state(
        self, child_type: str, local_id: int | str, prop: str, value: Any
    ) -> None:
        """Set one state key on a child entity, validated against its
        declared (or, for dynamic types, per-child) schema.

        Raises ValueError if ``prop`` is not in the child's effective schema
        (the synthetic ``online`` / ``label`` keys are always allowed).
        Writes to an unregistered child are skipped with a warning — they
        used to create orphan state keys visible in Live State and binding
        pickers but absent from the children listing.
        """
        if not self.is_child_registered(child_type, local_id):
            self._report_unregistered(
                "set_child_state for", (child_type, local_id), [prop],
                f" (prop {prop!r})",
            )
            return
        self._validate_child_prop(child_type, local_id, prop)
        schema = self._effective_child_schema(child_type, local_id)
        self._warn_on_type_mismatch(
            f"{child_type}.{self._format_child_id(child_type, local_id)}.{prop}",
            value,
            schema[prop],
        )
        self.state.set(
            self._child_state_key(child_type, local_id, prop),
            normalize_child_fault_claim(prop, value),
            source=f"device.{self.device_id}",
        )

    @staticmethod
    def child_fault(code: str = "", message: str = "") -> dict[str, Any]:
        """The three reserved keys that say what shape a child is in.

        Returns a fragment to merge into whatever a driver is already writing
        for that child, rather than writing anything itself. That is
        deliberate: presence usually arrives in the same poll reply as
        everything else about the child, and a driver that has built one batch
        should not have to break it in two (and make a panel flicker through a
        half-applied state) to say why an endpoint is down.

            self.set_children_state_batch([
                (ctype, mac, {**common, **self.child_fault()}),          # fine
                (ctype, mac, {**common, **self.child_fault(              # down
                    CHILD_NOT_RESPONDING)}),
            ])

        Called with no code it means "in service, nothing claimed" and clears
        both fault keys to ``None`` — the same value the device-level pair
        clears to, and the same one registration seeds, so a child that has
        recovered and a child that was never in trouble compare equal to a
        trigger. Clearing matters as much as setting, because a fault
        nothing ever clears makes one transient failure look permanent for as
        long as the device stays up.

        ``online`` is False whenever a code is set. That coupling is the point:
        it is the one glanceable signal, so a wedged endpoint has to read as
        not-in-service rather than as green with an explanation in a column
        somebody has to know to read. Which KIND of trouble is the reason's
        job, not the boolean's.

        An unrecognised code is refused rather than written. It would otherwise
        reach a panel as a bare token with no sentence behind it, and a typo in
        a driver would be indistinguishable from a code the frontend has not
        learned yet. ``parent_offline`` is refused too, though it is a real
        code: it is the platform's statement about a connection the driver no
        longer has (see _children_follow_parent), and a driver claiming it
        would only be overwritten on the next reconnect.
        """
        if not code:
            return _in_service()
        if code not in CHILD_DRIVER_FAULT_CODES:
            raise ValueError(
                f"{code!r} is not a child fault code a driver may assert "
                f"(expected one of "
                f"{', '.join(sorted(CHILD_DRIVER_FAULT_CODES))})"
            )
        return _out_of_service(code, message)

    def set_child_state_batch(
        self, child_type: str, local_id: int | str, updates: dict[str, Any]
    ) -> None:
        """Atomically set several state keys on one child entity.

        Validates every prop in ``updates`` before any write, so a single
        bad prop causes the entire batch to abort. Unregistered children are
        skipped with a warning (see set_child_state).
        """
        if not self.is_child_registered(child_type, local_id):
            self._report_unregistered(
                "set_child_state_batch for", (child_type, local_id), updates,
            )
            return
        self._validate_child_props(child_type, local_id, updates)
        self.state.set_batch(
            self._child_writes(child_type, local_id, updates),
            source=f"device.{self.device_id}",
        )

    def set_children_state_batch(
        self, updates: list[tuple[str, int | str, dict[str, Any]]]
    ) -> None:
        """Atomically set state keys across many children in one transaction.

        Each entry is ``(child_type, local_id, {prop: value, ...})``. Listeners
        and the cloud relay see the complete delta, not a half-applied state.
        Use this for poll responses that touch dozens or hundreds of children
        at once. Entries for unregistered children are skipped with a warning
        (see set_child_state); the rest of the batch still applies.
        """
        live: list[tuple[str, int | str, dict[str, Any]]] = []
        for child_type, local_id, child_updates in updates:
            if self.is_child_registered(child_type, local_id):
                live.append((child_type, local_id, child_updates))
            else:
                self._report_unregistered(
                    "set_children_state_batch entry for", (child_type, local_id),
                    child_updates,
                )
        for child_type, local_id, child_updates in live:
            self._validate_child_props(child_type, local_id, child_updates)
        namespaced: dict[str, Any] = {}
        for child_type, local_id, child_updates in live:
            namespaced.update(self._child_writes(child_type, local_id, child_updates))
        if namespaced:
            self.state.set_batch(namespaced, source=f"device.{self.device_id}")

    # --- Reading children ---------------------------------------------------

    def get_child_entity_types(self) -> dict[str, dict[str, Any]]:
        """Return the driver's declared child entity types as
        ``{type_name: definition}``, with each definition's
        ``state_variables`` replaced by the *effective* schema (the
        platform-managed ``online`` and ``label`` keys injected if the
        driver didn't already declare them).

        Returns ``{}`` if the driver doesn't declare ``child_entity_types``.
        Used by the REST API and IDE to expose the per-type schema to
        clients without leaking driver-private helpers.

        For a ``dynamic: true`` type, ``state_variables`` here is the
        type-level schema (often just the platform-managed ``online`` /
        ``label``); each child's discovered controls are exposed per-instance
        via :meth:`get_child_schema` and the children listing, since they vary
        from one child to the next.
        """
        raw = self.DRIVER_INFO.get("child_entity_types", {})
        result: dict[str, dict[str, Any]] = {}
        for ctype, definition in raw.items():
            merged_def = dict(definition)
            merged_def["state_variables"] = self._effective_child_schema(ctype)
            result[ctype] = merged_def
        return result

    def get_child_schema(
        self, child_type: str, local_id: int | str,
    ) -> dict[str, dict[str, Any]]:
        """Effective per-child state-variable schema (with ``online`` /
        ``label`` injected). For a ``dynamic: true`` type this reflects the
        schema supplied at ``register_child(schema=...)``; for a static type
        it's the declared type schema. Used by the REST API to render
        heterogeneous (dynamic) children, where each instance has its own
        control set.
        """
        return self._effective_child_schema(child_type, local_id)

    def is_child_type_dynamic(self, child_type: str) -> bool:
        """True if ``child_type`` is declared ``dynamic: true`` (its children
        carry per-instance schemas). False for unknown or static types.
        """
        return bool(
            self.DRIVER_INFO.get("child_entity_types", {})
            .get(child_type, {})
            .get("dynamic")
        )

    def format_child_id(self, child_type: str, local_id: int | str) -> str:
        """Validate ``local_id`` against the declared id_format and return
        its padded string form. Public wrapper around the internal helper.
        """
        return self._format_child_id(child_type, local_id)

    def get_child_state(
        self, child_type: str, local_id: int | str,
    ) -> dict[str, Any]:
        """Return the live state for one registered child as
        ``{property: value}``. Returns an empty dict if the child isn't
        currently registered.
        """
        if not self.is_child_registered(child_type, local_id):
            return {}
        return self.state.get_namespace(
            self._child_state_prefix(child_type, local_id)
        )

    # --- Discovery and polling ----------------------------------------------

    async def refresh_children(self) -> Any:
        """Re-discover child entities from the device.

        Default implementation raises ``NotImplementedError``. Drivers that
        can re-poll their controller's child list override this to reconcile
        ``register_child`` / ``deregister_child`` calls against the latest
        device state. Called from the REST API
        ``POST /api/devices/{id}/children/refresh`` endpoint.
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement "
            f"refresh_children"
        )

    async def poll_children(
        self,
        child_type: str,
        fetch: Callable[[list[int]], Awaitable[dict[int, dict[str, Any]]]],
        batch_size: int = 50,
        inter_batch_delay: float = 0.1,
    ) -> None:
        """Paginated polling helper.

        Splits the currently-registered ``child_type`` IDs into batches of
        ``batch_size``, awaits ``fetch(batch_ids)`` for each (pausing
        ``inter_batch_delay`` seconds between batches so polls don't saturate
        slow controllers), then applies the whole poll's results in ONE atomic
        ``set_children_state_batch``. Applying per-poll rather than per-batch
        means a subscriber never observes a half-applied multi-batch snapshot
        (some children on this poll's values, the rest still on the last
        poll's). The coalescing delay is bounded by the fetch time and is
        negligible against typical poll intervals.

        ``fetch`` returns ``{local_id: {prop: value, ...}}``. A result for a
        child that isn't registered, or that was deregistered (or
        deregistered+re-registered, which resets its state) since the poll
        started, is dropped — so a concurrent ``refresh_children`` can't have
        its reset clobbered by a stale write (ABA guard).
        """
        ids = self.list_children(child_type)
        if not ids:
            return
        # Each child's registration epoch at poll start. A child whose live
        # epoch later differs (re-registered) or is gone (deregistered) is
        # dropped at apply time.
        start_bucket = self._children.get(child_type, {})
        epochs = {lid: start_bucket.get(lid) for lid in ids}
        collected = await _fetch_in_batches(ids, fetch, batch_size, inter_batch_delay)
        updates = self._results_still_registered(child_type, epochs, collected)
        if updates:
            self.set_children_state_batch(updates)

    def _results_still_registered(
        self,
        child_type: str,
        epochs: dict[Any, int | None],
        collected: list[tuple[Any, dict[str, Any]]],
    ) -> list[tuple[str, Any, dict[str, Any]]]:
        """The poll results for children still registered under the epoch
        they had when the poll started (drops ghosts, removed and
        re-registered children)."""
        live = self._children.get(child_type, {})
        updates: list[tuple[str, Any, dict[str, Any]]] = []
        for lid, props in collected:
            expected = epochs.get(lid)
            if expected is not None and live.get(lid) == expected:
                updates.append((child_type, lid, props))
        return updates
