"""THE child registration rule: work deferred for a child is for the
registration the child held when the work was taken, and ends with it.

A child's registration is the epoch the registry (drivers/child_entities.py)
stamps it with when it registers. A child removed and registered again holds
a new one, its state reset, and is a different child to anything kept for
the old one. Work a driver keeps for later on a child's behalf (a YAML reply
rule's follow-up round, its throttle window and the write the window holds)
is for the children the reply wrote, as each was registered then:

* It does a child's part only while the child still holds the
  registration it held when the work was taken (``_child_registrations``,
  ``_holds_registration``), judged when the part is done, not when it was
  taken.
* A hold (a throttle window, a round's settle hold) holds back only work
  for children registered when it was taken: it notes how many
  registrations the registry had made then (``_registrations_made``), and a
  child registered since (one that left and came back, one not on the
  roster then) is not held back by it (``registered_since``). Another child
  that was on the roster then is, whether or not the work that took the
  hold wrote to it.
* A child leaving the registry ends its part at once: ``_forget_child``
  calls the host's ``_end_child_work``. What the work does for the other
  children it was taken for goes on.

``poll_children``'s guard (a result for a child removed or registered again
since the poll started is dropped) asks the same question.

``ChildRegistration`` is a mixin ``ChildEntities`` inherits, so every name
stays on the driver object. From its host it needs the registry,
``_children`` (``{child_type: {local_id: epoch}}``), and its count of
registrations made, ``_child_register_seq`` (each epoch is that count when
the child registered). Pure stdlib.
"""

from __future__ import annotations

from typing import Any

# A child is (child_type, local_id), as the registry keys one.
Child = tuple[str, "int | str"]

# What deferred work was taken for: each child it is for, with the
# registration (epoch) the child held then, None for one not registered.
ChildRegistrations = dict[Child, "int | None"]


def registered_since(registrations: ChildRegistrations, taken_at: int | None) -> bool:
    """Whether a child these registrations name registered after a hold was
    taken (``taken_at``, the registrations made then), so the hold does not
    hold its work back. A hold that noted nothing holds back everything."""
    if taken_at is None:
        return False
    return any(reg is not None and reg > taken_at for reg in registrations.values())


class ChildRegistration:
    """What a child's deferred work was taken for, and whether it still
    holds."""

    _children: dict[str, dict[Any, int]]
    _child_register_seq: int

    def _child_registrations(
        self, writes: list[tuple[str, Any, dict[str, Any]]],
    ) -> ChildRegistrations:
        """The registration each child these writes name holds now, in the
        order they name them: what a reply's deferred work is taken for."""
        return {
            (child_type, local_id): self._children.get(child_type, {}).get(local_id)
            for child_type, local_id, _updates in writes
        }

    def _holds_registration(self, child: Child, registration: int | None) -> bool:
        """Whether ``child`` still holds the registration work was taken
        for: neither removed nor registered again since."""
        child_type, local_id = child
        return self._children.get(child_type, {}).get(local_id) == registration

    def _registrations_made(self) -> int:
        """How many registrations the registry has made: what a hold notes
        when it is taken (``registered_since``)."""
        return self._child_register_seq

    def _end_child_work(self, child: Child) -> None:
        """End the work a host deferred for ``child``, which has just left
        the registry (``_forget_child``). Nothing here: the YAML runtime ends
        its follow-up rounds and throttle holds (drivers/configurable.py)."""
