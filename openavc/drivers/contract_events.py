"""What a contract observer (``BaseDriver.contract_observer``) can be told.

Each name is something the driver runtime already notices and then keeps to
itself: a debug line, a warning once per key, a value stored as it came. A
device audit and the Driver Builder's test set an observer on the one driver
they run; in production there is none.

``openavc.drivers.base`` re-exports every name here, and that is the path
drivers and the rest of the platform import them from. They live in a module
of their own so the runtime's parts (``drivers/child_entities.py``) can report
without importing ``base``.
"""

from __future__ import annotations

UNMATCHED_RESPONSE = "unmatched_response"   # a reply no response rule matched
UNDECLARED_STATE = "undeclared_state"       # a write to an undeclared variable
TYPE_MISMATCH = "type_mismatch"             # a value not of its declared type
COERCION_FAILURE = "coercion_failure"       # a reply that would not convert
UNKNOWN_COMMAND = "unknown_command"         # a command the driver does not have
CHILD_UNREGISTERED = "child_unregistered"   # state for a child never registered
CONTRACT_EVENTS: tuple[str, ...] = (
    UNMATCHED_RESPONSE, UNDECLARED_STATE, TYPE_MISMATCH, COERCION_FAILURE,
    UNKNOWN_COMMAND, CHILD_UNREGISTERED,
)
