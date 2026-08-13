"""A YAML field the contract declares must reach the driver it configures.

``create_configurable_driver_class`` turns a ``.avcdriver`` definition into a
live class, and the way it does that is a hand-written list of copies:

    if "discovery" in driver_def:
        driver_info["discovery"] = driver_def["discovery"]

Everything else about a contract field is generated or checked. The registry in
``spec.py`` renders into both published schemas and the Builder's types; the
semantic rules validate it; ``test_driver_contract_field_implemented.py``
proves some code somewhere mentions it; ``test_prose_documents_the_contract.py``
proves the guide describes it. **None of that reaches this list**, and a field
missing from it is a field an author can set, validate, publish and read about,
which then does nothing at all.

That is not hypothetical. ``routing:`` shipped exactly that way: declared in the
registry, generated into both schemas, validated with its own rule set, given a
Driver Builder section, documented, and consumed by the matrix picker -- with no
line here, so all eleven shipped YAML drivers that declared one were read as if
they had not. The contract-field guard was green throughout, and says why in its
own docstring: *"'Mentioned' is not 'implemented correctly'. This catches the
field nobody wired up; it cannot catch one wired up wrong."* This is the narrow
case it cannot see, and the one that costs the most, because the failure is
silent on both sides -- the driver looks fine and the platform looks fine.

So: build a driver declaring each top-level field and ask the class it produced
whether the field arrived. A field that does not is either a copy somebody
forgot or one of the twenty that legitimately go elsewhere, and the difference
is not derivable -- so the twenty are listed below **with the reason**, and a
new field fails until somebody classifies it. That is the point: the failure
lands on whoever adds the field, in the change that adds it.

Deliberately NOT a manifest of where each field is implemented. This asks one
decidable question of the real function -- did the value survive the trip -- and
the opt-out list holds a reason, not a claim about where code lives. The
existing contract-field guard warns against growing into a per-field surface
table, and this stays on the other side of that line.
"""

from __future__ import annotations

from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.spec import FIELDS

#: The identity fields every definition needs, which are not under test.
_BASE = {"id": "acme_probe", "name": "Acme Probe", "transport": "tcp"}

#: Fields that deliberately do not land on DRIVER_INFO, and where they go
#: instead. Each entry is a reason a person can check, not a file path that
#: goes stale. Keep it short: an entry here says "this value is consumed
#: somewhere else", and a wrong one shows up as a field that quietly does
#: nothing -- exactly what this test exists to catch.
NOT_ON_DRIVER_INFO = {
    # Compiled into the protocol tables the runtime interprets, rather than
    # carried as metadata. compile_driver() owns these.
    "responses": "compiled into the response table",
    "polling": "compiled into the poll schedule",
    "on_connect": "compiled into the connect sequence",
    "command_prefix": "folded into each compiled command",
    "command_suffix": "folded into each compiled command",
    "frame_parser": "builds the transport's frame parser",
    "send_frame": "builds the outbound framing",
    "config_derived": "resolved per device at config time",
    # Consumed by a subsystem that reads the definition directly.
    "auth": "read by the transport when it connects",
    "liveness": "read by the device manager's watchdog",
    "simulator": "read by the simulator process, not the driver",
    # Catalog metadata: describes the driver to the library, and is never
    # asked of a running one.
    "min_platform_version": "catalog gate, checked before install",
    "compatible_models": "catalog metadata",
    "deprecated": "catalog metadata",
    "replacement_id": "catalog metadata",
    "ports": "catalog metadata",
    "simulated": "catalog metadata",
    "source_url": "catalog metadata",
    "tags": "catalog metadata",
    "verified": "catalog metadata",
}


def _reaches_driver_info(field: str) -> bool:
    """Declare one field on an otherwise bare driver; ask the class for it."""
    definition = {**_BASE, field: {"__sentinel__": True}}
    driver_class = create_configurable_driver_class(definition)
    return field in (getattr(driver_class, "DRIVER_INFO", None) or {})


def _absent() -> set[str]:
    """Every declared field the built class did not end up carrying."""
    return {
        field for field in set(FIELDS) - set(_BASE)
        if not _reaches_driver_info(field)
    }


def test_every_declared_field_reaches_the_driver_or_says_where_it_went() -> None:
    """One assertion over the whole contract rather than a case per field.

    Deliberately not parametrized: the twenty legitimate opt-outs would each
    report as a skip, and twenty permanent skips is exactly the noise that
    makes a real one invisible. This way the suite's skip count keeps meaning
    "something did not run".
    """
    absent = _absent()
    unclassified = sorted(absent - set(NOT_ON_DRIVER_INFO))
    assert unclassified == [], (
        f"The contract declares {unclassified} and create_configurable_driver_class "
        f"does not copy them onto DRIVER_INFO, so a YAML driver that sets one is "
        f"read as if it had not. Add the copy in configurable.py, or -- if the "
        f"field is consumed somewhere else -- add it to NOT_ON_DRIVER_INFO in "
        f"this file with the reason."
    )


def test_the_opt_out_list_holds_no_field_that_actually_arrives() -> None:
    """A stale excuse is worse than none: it says a field is handled elsewhere
    while the copy exists, so the next reader trusts a reason that is wrong."""
    arriving = sorted(set(NOT_ON_DRIVER_INFO) & (set(FIELDS) - _absent()))
    assert arriving == [], (
        f"{arriving} reach DRIVER_INFO after all. Drop them from "
        f"NOT_ON_DRIVER_INFO -- the list is for fields that go somewhere else."
    )


def test_the_opt_out_list_holds_no_field_the_contract_dropped() -> None:
    """And no excuse outlives the field it was written for."""
    gone = sorted(set(NOT_ON_DRIVER_INFO) - set(FIELDS))
    assert gone == [], (
        f"{gone} are no longer contract fields. Remove them from "
        f"NOT_ON_DRIVER_INFO."
    )
