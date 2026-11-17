"""What a value in a device's reply becomes, and which child it belongs to.

A YAML driver reads its device's replies three ways (a regex over a text
line, an OSC address pattern, a key in a JSON body), and every one of them
ends in the same two questions, answered here once:

* **What does this value become?** A mapping's ``map:`` names the value
  first (``"1": "on"``); otherwise its state variable's conversion
  (``convert:``: scale, offset, unknown) is asked, and then the shared
  coercer for the wire form reads it as the declared type. A value the
  definition wrote itself (a ``value:`` or a ``map:`` target) is read as
  text, except that ``null`` is no reading at all.
* **Which child does a ``child_set:`` entry write?** The id the reply
  carries goes through the entry's ``id_map:``, is coerced to the child
  type's ``id_format``, and has to be a registered child. The answer is one
  ``child_placement.Placement`` (placed, unregistered, or unplaceable with
  a reason), whose rules that module states; ``_place_child`` decides it and
  tells a contract observer what that module says is told.

When a value does not convert, the coercer keeps it as it came or reads it
as False, which is right for the room and invisible to the author, so a
contract observer (a device audit, the Driver Builder's test) is told.

``ReplyValues`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. From its host it needs ``device_id``,
``_definition``, ``contract_observer``, ``_observe`` and
``is_child_registered``.
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.core.state_store import is_flat_primitive
from openavc.drivers import compiled_protocol
from openavc.drivers.child_ids import coerce_child_local_id
from openavc.drivers.child_placement import (
    BAD_ID,
    GAP,
    NAMED,
    UNMAPPED,
    UNREGISTERED,
    Placement,
    placed,
    placement_detail,
    unplaceable,
    unregistered,
)
from openavc.drivers.contract_events import (
    CHILD_UNPLACED,
    CHILD_UNREGISTERED,
    COERCION_FAILURE,
)
from openavc.utils.logger import get_logger

log = get_logger("openavc.drivers.configurable")


def _boolean_unread(raw: Any) -> bool:
    return not isinstance(raw, bool) and not compiled_protocol.is_bool_token(raw)


# Per declared type: did the coercer fail to read the raw value? A type not
# listed here (string) always reads.
_COERCION_FAILED: dict[str, Callable[[Any, Any], bool]] = {
    "integer": lambda raw, coerced: not isinstance(coerced, int),
    "float": lambda raw, coerced: not isinstance(coerced, (int, float)),
    "number": lambda raw, coerced: not isinstance(coerced, (int, float)),
    "boolean": lambda raw, coerced: _boolean_unread(raw),
}


def _as_reported(value: Any) -> Any:
    """A value as a contract observer is told it: flat, or its repr."""
    return value if is_flat_primitive(value) else repr(value)


class ReplyValues:
    """What a reply's values become, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver; declared here for the methods.
    device_id: str
    _definition: dict[str, Any]
    contract_observer: Any
    _observe: Callable[..., None]
    is_child_registered: Callable[[str, Any], bool]

    # The shared coercers (see compiled_protocol's module docs). Non-primitive
    # JSON values have already been collapsed to a length by the json path
    # walk before ``_coerce_json_value`` sees them.
    _coerce_value = staticmethod(compiled_protocol.coerce_value)
    _coerce_json_value = staticmethod(compiled_protocol.coerce_json_value)
    _coerce_osc_value = staticmethod(compiled_protocol.coerce_osc_value)

    def _convert_value(self, raw: str, value_type: str) -> Any:
        coerced = self._coerce_value(raw, value_type)
        if self.contract_observer is not None:
            self._note_coercion(raw, value_type, coerced)
        return coerced

    def _convert_json_value(self, value: Any, value_type: str) -> Any:
        coerced = self._coerce_json_value(value, value_type)
        if self.contract_observer is not None:
            self._note_coercion(value, value_type, coerced)
        return coerced

    def _convert_osc_value(self, value: Any, value_type: str) -> Any:
        coerced = self._coerce_osc_value(value, value_type)
        if self.contract_observer is not None:
            self._note_coercion(value, value_type, coerced)
        return coerced

    def _convert_declared(self, literal: Any, value_type: str) -> Any:
        """A value the definition wrote itself: a rule's ``value:`` (or a
        ``set:`` literal) or a ``map:`` target, rather than one the device sent.

        ``null`` there means no reading, so it is empty (None) whatever the
        declared type; read as text it would be the string "None", or False
        on a boolean. Anything else is read as text first, as a capture is,
        which also collapses a list or dict target to one flat value before
        it reaches the state store.
        """
        if literal is None:
            return None
        return self._convert_value(str(literal), value_type)

    def _note_coercion(self, raw: Any, value_type: str, coerced: Any) -> None:
        if raw is None:
            return
        # A type that is not a name (a list or map written by hand) checks
        # nothing, as an unknown name does.
        failed = _COERCION_FAILED.get(value_type) if isinstance(value_type, str) else None
        if failed is not None and failed(raw, coerced):
            self._observe(
                COERCION_FAILURE, raw=_as_reported(raw),
                type=value_type, stored=_as_reported(coerced),
            )

    def _read_converted(
        self, raw: Any, value_type: str, mapping: dict[str, Any], coerce: Any
    ) -> Any:
        """``coerce(raw, value_type)``, unless the mapping carries its state
        variable's conversion (``convert``: scale / offset / unknown, attached
        at compile time) and that decides the value."""
        conversion = mapping.get("convert")
        if conversion:
            handled, value = compiled_protocol.reading_from_wire(
                raw, conversion, value_type
            )
            if handled:
                return value
        return coerce(raw, value_type)

    def _mapped_reading(
        self, raw: Any, mapping: dict[str, Any], coerce: Any
    ) -> Any:
        """What one value the device sent becomes under its mapping: the
        ``map:`` entry it names, else the conversion and the coercer for its
        wire form (``coerce``: _convert_value, _convert_osc_value or
        _convert_json_value)."""
        value_type = mapping.get("type", "string")
        value_map = mapping.get("map")
        if value_map and str(raw) in value_map:
            return self._convert_declared(value_map[str(raw)], value_type)
        return self._read_converted(raw, value_type, mapping, coerce)

    def _coerce_child_local_id(self, child_type: str, raw: Any) -> int | str | None:
        """Coerce a routed child id (regex capture or literal) to the type's
        declared ``id_format``. Returns None when it can't be coerced — the
        callers here skip such an update rather than raise, as a REST route
        would 404 on the same input."""
        child_types = self._definition.get("child_entity_types") or {}
        return coerce_child_local_id(child_types.get(child_type), raw)

    def _place_child(self, raw_id: Any, cm: dict[str, Any]) -> Placement:
        """Where a reply's wire id puts its value under a ``child_set`` or
        ``child_list`` entry ``cm`` (``child_placement``). An item of a list
        that names its children by place, unregistered, is a gap the page
        covers rather than a child the device named."""
        ctype = cm["type"]
        form = "child_list" if "list" in cm else "child_set"
        id_map = cm.get("id_map")
        if id_map is not None:
            mapped_id = id_map.get(str(raw_id).strip())
            if mapped_id is None:
                log.debug(
                    f"[{self.device_id}] {form}: {ctype} wire id "
                    f"{raw_id!r} not in the id map — skipping"
                )
                return unregistered(ctype, None, UNMAPPED)
            raw_id = mapped_id
        local_id = self._coerce_child_local_id(ctype, raw_id)
        if local_id is None:
            return self._told(unplaceable(ctype, BAD_ID, _as_reported(raw_id)))
        if not self.is_child_registered(ctype, local_id):
            by_place = form == "child_list" and cm["list"].get("id_group") is None
            return self._told(unregistered(ctype, local_id, GAP if by_place else NAMED))
        return placed(ctype, local_id)

    def _told(self, placement: Placement) -> Placement:
        """A placement that wrote nothing, in the debug log and, when
        ``child_placement`` says so, to a contract observer."""
        log.debug(
            f"[{self.device_id}] reply: {placement.child_type} "
            f"{placement.outcome} ({placement.why}"
            f"{'' if placement.local_id is None else f', {placement.local_id!r}'}) "
            f"— skipping"
        )
        detail = placement_detail(placement)
        if detail is not None:
            kind = CHILD_UNREGISTERED if placement.outcome == UNREGISTERED else CHILD_UNPLACED
            self._observe(kind, **detail)
        return placement
