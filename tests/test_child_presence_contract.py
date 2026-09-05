"""Who owns a child entity's `online`, and what it says when nobody can see it.

Two failures on real hardware forced this, and they are opposite ends of the
same missing rule:

* A mixer with its LAN cable pulled kept all six inputs reading `online: true`
  with their last levels frozen, under a banner saying the device was offline.
  A panel LED bound to a child's presence drew green for gear nobody could
  reach.
* The same mixer's seven AT-LINK extension slots — positions where another
  unit MAY be chained, empty on a standalone box — drew seven green dots for
  hardware that does not exist.

Both are `online` being asked to carry more than one meaning. The rule these
tests pin is that it carries exactly one — *in service right now* — and the
other two facts live in the reason code:

* `not_fitted`   — the slot is empty. Declared per roster, because only the
                   driver author knows whether `count: 7` means seven fitted
                   inputs or seven places something could go.
* `parent_offline` — the platform cannot see anything under this device. The
                   one code the platform asserts rather than the driver, and
                   the only one it clears.

The children stay REGISTERED through all of it: deregistering them would take
a panel's bindings with them, which is worse than the fault being reported.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core.connection_fault import (
    CHILD_NOT_FITTED,
    CHILD_NOT_RESPONDING,
    CHILD_PARENT_OFFLINE,
    CHILD_SERVICE_FAULT,
    default_child_fault_message,
    is_child_trouble_code,
)
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.child_ids import child_display_name


class _SlotDriver(BaseDriver):
    """A mixer shaped like the one that produced both failures.

    `input` is six always-fitted mic channels (an `assumed` roster, the
    default). `slot` is seven chain positions that are empty until something is
    plugged in (`reported`). Same `count:` syntax, opposite meanings — which is
    exactly why the roster has to say which it is.
    """

    DRIVER_INFO: dict[str, Any] = {
        "id": "slot_mixer",
        "name": "Slot Mixer",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "child_entity_types": {
            "input": {
                "label": "Input",
                "id_format": {"type": "integer", "min": 1, "max": 6},
                "state_variables": {"level": {"type": "integer"}},
                "instances": {"count": 6},
            },
            "slot": {
                "label": "Extension",
                "label_field": "device_name",
                "id_format": {"type": "integer", "min": 1, "max": 7},
                "state_variables": {"device_name": {"type": "string"}},
                "instances": {"count": 7, "presence": "reported"},
            },
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _driver() -> _SlotDriver:
    return _SlotDriver(
        device_id="mix", config={}, state=StateStore(), events=EventBus(),
    )


def _presence(drv: BaseDriver, ctype: str, lid: int) -> tuple[Any, Any, Any]:
    p = f"device.{drv.device_id}.{ctype}.{lid}"
    return (
        drv.state.get(f"{p}.online"),
        drv.state.get(f"{p}.offline_reason"),
        drv.state.get(f"{p}.offline_detail"),
    )


# ---------------------------------------------------------------------------
# An empty slot is not a fault, and a fitted channel is not a claim
# ---------------------------------------------------------------------------


def test_a_reported_roster_registers_its_slots_empty():
    # The failure this replaces: seven green dots on a standalone mixer.
    drv = _driver()
    drv.register_child("slot", 3)

    online, reason, detail = _presence(drv, "slot", 3)
    assert online is False
    assert reason == CHILD_NOT_FITTED
    assert detail == "Nothing is connected here."


def test_an_assumed_roster_still_registers_in_service():
    # The default has to stay put: flipping it would take every matrix output
    # and mixer input in the corpus offline until its driver learned to speak.
    drv = _driver()
    drv.register_child("input", 1)

    assert _presence(drv, "input", 1) == (True, "", "")


def test_an_empty_slot_is_not_counted_as_trouble():
    assert is_child_trouble_code(CHILD_NOT_FITTED) is False
    assert is_child_trouble_code(CHILD_PARENT_OFFLINE) is True
    assert is_child_trouble_code(CHILD_NOT_RESPONDING) is True
    assert is_child_trouble_code("") is False
    # A code nobody defined is a driver bug; "I don't know this one" must not
    # read as "everything is fine".
    assert is_child_trouble_code("gremlins") is True


def test_a_driver_may_still_say_a_slot_is_populated():
    drv = _driver()
    drv.register_child("slot", 2, initial_state={"online": True})
    assert drv.state.get("device.mix.slot.2.online") is True


# ---------------------------------------------------------------------------
# The parent going away takes every child with it
# ---------------------------------------------------------------------------


def test_children_go_offline_with_their_parent():
    drv = _driver()
    for i in (1, 2):
        drv.register_child("input", i)
    drv.set_state("connected", True)

    drv.set_state("connected", False)

    for i in (1, 2):
        online, reason, detail = _presence(drv, "input", i)
        assert online is False, i
        assert reason == CHILD_PARENT_OFFLINE, i
        assert detail == default_child_fault_message(CHILD_PARENT_OFFLINE), i


def test_children_stay_registered_so_bindings_survive():
    # The whole reason the platform marks rather than deregisters: a fader
    # bound to input.1.level must go quiet, not point at a key that is gone.
    drv = _driver()
    drv.register_child("input", 1)
    drv.set_child_state("input", 1, "level", 411)
    drv.set_state("connected", True)
    drv.set_state("connected", False)

    assert _presence(drv, "input", 1)[1] == CHILD_PARENT_OFFLINE
    assert drv.list_children("input") == [1]
    assert drv.state.has("device.mix.input.1.level")


def test_a_driver_asserted_fault_survives_the_parent_going_offline():
    # `not_responding` is more specific than ours and is still true while the
    # parent is away. Overwriting it would lose the only actionable thing
    # anybody knew about that endpoint. Its neighbour pins the other half:
    # a build that simply never marks anything would pass on the claim alone.
    drv = _driver()
    drv.register_child("input", 1)
    drv.register_child("input", 2)
    drv.set_state("connected", True)
    drv.set_child_state_batch("input", 1, drv.child_fault(CHILD_NOT_RESPONDING))

    drv.set_state("connected", False)

    assert _presence(drv, "input", 1)[1] == CHILD_NOT_RESPONDING
    assert _presence(drv, "input", 2)[1] == CHILD_PARENT_OFFLINE


def test_an_empty_slot_does_not_become_a_fault_when_the_cable_is_pulled():
    drv = _driver()
    drv.register_child("slot", 4)
    drv.set_state("connected", True)

    drv.set_state("connected", False)

    assert _presence(drv, "slot", 4)[1] == CHILD_NOT_FITTED


def test_a_child_that_was_already_down_with_no_reason_is_left_alone():
    # Every driver written before the vocabulary sets online False and says
    # nothing. Stamping parent_offline over it would claim we know why, and
    # would then hand it back online on reconnect against what the driver said.
    drv = _driver()
    drv.register_child("input", 1)
    drv.register_child("input", 2)
    drv.set_state("connected", True)
    drv.set_child_state("input", 1, "online", False)

    drv.set_state("connected", False)

    assert _presence(drv, "input", 1) == (False, "", "")
    assert _presence(drv, "input", 2)[1] == CHILD_PARENT_OFFLINE

    drv.set_state("connected", True)
    assert _presence(drv, "input", 1)[0] is False


# ---------------------------------------------------------------------------
# Coming back undoes exactly what going away did
# ---------------------------------------------------------------------------


def test_reconnecting_returns_the_children_the_platform_took_down():
    drv = _driver()
    drv.register_child("input", 1)
    drv.set_state("connected", True)
    drv.set_state("connected", False)
    # Pinned on the way through, or this passes against a build that never
    # took the child down in the first place.
    assert _presence(drv, "input", 1)[1] == CHILD_PARENT_OFFLINE

    drv.set_state("connected", True)

    assert _presence(drv, "input", 1) == (True, "", "")


def test_reconnecting_returns_a_reported_slot_to_empty_not_to_present():
    # On a roster whose driver reports presence, "we do not know yet" is an
    # empty slot -- claiming it is populated would invent hardware.
    drv = _driver()
    drv.register_child("slot", 1, initial_state={"online": True})
    drv.set_state("connected", True)
    drv.set_state("connected", False)
    assert _presence(drv, "slot", 1)[1] == CHILD_PARENT_OFFLINE

    drv.set_state("connected", True)

    assert _presence(drv, "slot", 1)[:2] == (False, CHILD_NOT_FITTED)


def test_reconnecting_does_not_clear_a_fault_the_driver_asserted():
    # The claim was made BEFORE the drop, so the platform never wrote over it
    # and must not undo it either. Its neighbour pins the other half.
    drv = _driver()
    drv.register_child("input", 1)
    drv.register_child("input", 2)
    drv.set_state("connected", True)
    drv.set_child_state_batch("input", 1, drv.child_fault(CHILD_SERVICE_FAULT))
    drv.set_state("connected", False)

    drv.set_state("connected", True)

    assert _presence(drv, "input", 1)[1] == CHILD_SERVICE_FAULT
    assert _presence(drv, "input", 2) == (True, "", "")


def test_the_flip_is_idempotent_in_both_directions():
    drv = _driver()
    drv.register_child("input", 1)
    drv.set_state("connected", True)
    drv.set_state("connected", False)
    drv.set_state("connected", False)
    assert _presence(drv, "input", 1)[1] == CHILD_PARENT_OFFLINE
    drv.set_state("connected", True)
    drv.set_state("connected", True)
    assert _presence(drv, "input", 1) == (True, "", "")


def test_the_batch_door_carries_the_children_too():
    # A driver writing `connected` through set_states must not be a way round
    # the rule; there is no honest reason for the two doors to disagree.
    drv = _driver()
    drv.register_child("input", 1)
    drv.set_states({"connected": True})
    drv.set_states({"connected": False})

    assert _presence(drv, "input", 1)[1] == CHILD_PARENT_OFFLINE


def test_the_whole_flip_lands_as_one_batch():
    # A panel must not see half a roster go offline: six inputs arriving in
    # six transactions is six renders and a visible sweep down the page.
    drv = _driver()
    for i in range(1, 7):
        drv.register_child("input", i)
    drv.set_state("connected", True)

    transactions: list[list] = []
    drv.state.subscribe_bulk(
        "device.mix.input.*", lambda changes: transactions.append(changes),
    )
    drv.set_state("connected", False)

    assert len(transactions) == 1
    assert len(transactions[0]) == 6 * 3  # online + reason + detail, per child


# ---------------------------------------------------------------------------
# parent_offline is the platform's word, not a driver's
# ---------------------------------------------------------------------------


def test_a_driver_cannot_claim_the_parent_is_offline():
    # Its transport is gone; it cannot see this, and the platform would clear
    # the claim on the next reconnect anyway.
    with pytest.raises(ValueError, match="parent_offline"):
        BaseDriver.child_fault(CHILD_PARENT_OFFLINE)


def test_a_driver_may_report_an_empty_slot_itself():
    frag = BaseDriver.child_fault(CHILD_NOT_FITTED)
    assert frag["online"] is False
    assert frag["offline_reason"] == CHILD_NOT_FITTED


# ---------------------------------------------------------------------------
# A declared roster exists before the device has ever answered
# ---------------------------------------------------------------------------


_ROSTER_DEF: dict[str, Any] = {
    "id": "roster_mixer",
    "name": "Roster Mixer",
    "transport": "tcp",
    "child_entity_types": {
        "input": {
            "label": "Input",
            "id_format": {"type": "integer", "min": 1, "max": 7},
            "state_variables": {"level": {"type": "integer"}},
            "instances": {"count": 7, "label": "Input {id}"},
        },
    },
}


def _configurable(child_entities: dict | None = None):
    from openavc.drivers.configurable import create_configurable_driver_class

    cls = create_configurable_driver_class(_ROSTER_DEF)
    drv = cls("mic", {}, StateStore(), EventBus())
    drv.set_project_child_entities(child_entities or {})
    return drv


def test_a_declared_roster_is_there_before_the_first_connect():
    # A microphone whose port was wrong showed "Inputs 0 / Outputs 0 -- no
    # inputs registered yet", so every binding a panel had against input.1.*
    # pointed at nothing. The roster is `count: 7` whether it answers or not.
    drv = _configurable()

    assert drv.list_children("input") == list(range(1, 8))
    assert drv.state.has("device.mic.input.1.level")


def test_a_roster_registered_before_any_connect_claims_no_presence():
    drv = _configurable()

    online, reason, _ = _presence(drv, "input", 1)
    assert online is False
    assert reason == CHILD_PARENT_OFFLINE


def test_the_roster_comes_into_service_when_the_device_does():
    drv = _configurable()
    drv.set_state("connected", True)

    assert _presence(drv, "input", 1) == (True, "", "")


def test_the_integrator_label_still_wins_over_the_roster_template():
    # Registering earlier must not outrun the project's own labels: they arrive
    # immediately after construction, and register_child is idempotent, so a
    # roster built before them would keep "Input 1" forever.
    drv = _configurable({"input": {"1": {"label": "Lectern Mic"}}})

    assert drv.state.get("device.mic.input.1.label") == "Lectern Mic"
    assert drv.state.get("device.mic.input.2.label") == "Input 2"


# ---------------------------------------------------------------------------
# What a slot is CALLED when nobody has named it
# ---------------------------------------------------------------------------


def test_a_roster_label_is_used_when_nothing_else_names_the_child():
    # The mixer seeds "Extension 1".."Extension 7" from its instances.label
    # template and reports no device name until something is chained on. The
    # column read "(no label)" for all seven while the driver had named them.
    type_def = _SlotDriver.DRIVER_INFO["child_entity_types"]["slot"]
    assert child_display_name(
        "", {"label": "Extension 3", "device_name": ""}, type_def,
    ) == "Extension 3"


def test_the_device_name_still_beats_the_roster_template():
    type_def = _SlotDriver.DRIVER_INFO["child_entity_types"]["slot"]
    assert child_display_name(
        "", {"label": "Extension 3", "device_name": "ATND1061DAN"}, type_def,
    ) == "ATND1061DAN"


def test_the_integrator_still_beats_both():
    type_def = _SlotDriver.DRIVER_INFO["child_entity_types"]["slot"]
    assert child_display_name(
        "Ceiling Mic", {"label": "Extension 3", "device_name": "ATND1061DAN"},
        type_def,
    ) == "Ceiling Mic"
