"""Every fault code the platform can raise must be fully furnished.

The offline-reason taxonomy is a set of loose parts that have to agree: a code
constant, the frozenset that makes it raisable, a sentence for the device card,
and a retry policy. Nothing forces them together, and each gap fails quietly
rather than loudly:

* A code missing from ``_DEFAULT_MESSAGES`` still works. ``default_fault_message``
  falls back to the ``transport_disconnected`` wording, so a device that stopped
  reading its socket would tell the integrator "the connection dropped, retrying"
  — the one sentence that sends them to look at the network instead of the
  device. No exception, no log line, just the wrong answer forever.
* A code missing from ``_DRIVER_FAULT_CODES`` cannot be raised at all:
  ``ConnectionFaultError`` rejects it at construction. That one is loud, but only
  if some test actually raises it, and a brand-new code has no callers yet.
* A code whose message is the fallback's by copy-paste is invisible either way.

These are cheap to assert and they are the reason ``write_stalled`` did not ship
half-wired. Add a code to the taxonomy and this file tells you what you still owe.
"""

from __future__ import annotations

import pytest

from openavc.core import connection_fault as cf


# Codes the platform raises for itself and no driver may claim. They are still
# held to the sentence rule below — classify_connection_fault() words most of
# them richly at the branch, but anything calling default_fault_message() on one
# must not get the generic drop wording back.
_INTERNAL_ONLY = frozenset({cf.BRIDGE_OFFLINE, cf.NO_SIMULATOR})


def _device_level_codes() -> set[str]:
    """Every device-level code the module defines, raisable or not."""
    return {
        value
        for name, value in vars(cf).items()
        if name.isupper()
        and isinstance(value, str)
        and not name.startswith("_")
        and name not in {"NO_FAULT_CLAIMED"}
        and not name.startswith("CHILD_")
    }


def test_every_code_has_its_own_sentence():
    """A code with no message silently borrows the generic "connection dropped"
    wording, which is actively misleading for anything that is not a drop — it
    sends the integrator to look at the network when the fault is a
    certificate, a password, or the device itself."""
    fallback = cf._DEFAULT_MESSAGES[cf.TRANSPORT_DISCONNECTED]
    missing = sorted(
        code for code in _device_level_codes() - _INTERNAL_ONLY
        if code != cf.TRANSPORT_DISCONNECTED
        and cf._DEFAULT_MESSAGES.get(code) in (None, fallback)
    )
    assert not missing, (
        "these codes fall back to the transport_disconnected sentence, so a "
        f"device carrying one reports the wrong cause: {missing}. Give each an "
        "entry in _DEFAULT_MESSAGES."
    )


def test_the_codes_a_driver_may_claim_are_the_ones_it_can_detect():
    """Not every code is driver-raisable, and that is deliberate rather than an
    oversight: `tls_cert_untrusted` is read off the TLS error by the classifier
    and a driver is never the one to declare it. Pinning the set here means
    widening it is a decision somebody makes on purpose.
    """
    assert cf.TLS_CERT_UNTRUSTED not in cf._DRIVER_FAULT_CODES
    for internal in _INTERNAL_ONLY:
        assert internal not in cf._DRIVER_FAULT_CODES, (
            f"{internal} is raised by the platform, not claimed by a driver"
        )


@pytest.mark.parametrize("code", sorted(cf._DRIVER_FAULT_CODES))
def test_each_code_answers_both_of_the_questions_asked_of_it(code):
    """The two things the platform asks a code at runtime: what do I tell the
    integrator, and do I keep retrying. Neither may raise or come back empty."""
    message = cf.default_fault_message(code, "the device at 10.0.0.9:1234")
    assert message and message.strip(), f"{code} produced no sentence"
    assert isinstance(cf.is_permanent_fault(code), bool)


@pytest.mark.parametrize("code", sorted(cf._DRIVER_FAULT_CODES))
def test_each_code_can_actually_be_raised(code):
    """ConnectionFaultError validates its code against the frozenset, so this
    is what proves a newly added constant was wired in rather than just named."""
    err = cf.ConnectionFaultError("", code=code)
    assert err.fault_code == code
    # Callers catch ConnectionError; a code that isn't one would be answered 500.
    assert isinstance(err, ConnectionError)


def test_an_unknown_code_is_refused_rather_than_silently_accepted():
    """The guard the above depends on: a typo must fail at construction, not
    classify as something else for the life of the install."""
    with pytest.raises(ValueError):
        cf.ConnectionFaultError("x", code="not_a_real_code")


def test_a_permanent_fault_is_one_a_human_has_to_clear():
    """Retry policy is the one part of the taxonomy that changes behaviour
    rather than wording: marking a healing condition permanent strands a device
    that would have come back by itself."""
    for code in cf._PERMANENT_FAULT_CODES:
        assert code in _device_level_codes(), (
            f"{code} is marked permanent but is not a code this module defines"
        )
    # A device that wedges recovers when somebody power-cycles it, and an
    # unreachable one when the network returns. Neither may stop the loop.
    for healing in (cf.WRITE_STALLED, cf.UNREACHABLE, cf.NO_RESPONSE,
                    cf.CONNECTION_REFUSED, cf.TRANSPORT_DISCONNECTED):
        assert not cf.is_permanent_fault(healing), (
            f"{healing} heals on its own; marking it permanent strands the device"
        )


def test_every_child_fault_code_has_a_sentence_too():
    """The child-entity vocabulary has the same shape and the same trap."""
    missing = sorted(
        code for code in cf.CHILD_FAULT_CODES
        if not cf.default_child_fault_message(code)
    )
    assert not missing, f"child codes with no sentence: {missing}"


# --- The documents this repo's CI cannot see -------------------------------

# The device-level codes as they stood when the documents outside this repo
# were last reconciled. This is a tripwire, not a second source of truth:
# adding a code to the taxonomy fails the test below, whose whole job is to
# say what else that code now owes.
#
# Three documents restate this list in full and NONE of them is in this repo:
# the workspace's `openavc-api-reference.md` and `OpenAVC-Implementation-Design.md`,
# and openavc-cloud's `ai_system_prompt.py` (what the assistant knows). CI here
# cannot open any of them, so without this the list lands green and all three
# go quietly stale — which is exactly what `write_stalled` did.
_RECONCILED_CODES = frozenset({
    "auth_failed",
    "bridge_offline",
    "client_missing",
    "connection_refused",
    "host_key_rejected",
    "invalid_config",
    "no_response",
    "no_simulator",
    "tls_cert_untrusted",
    "transport_disconnected",
    "unreachable",
    "write_stalled",
})


def test_a_new_code_also_has_to_reach_the_documents_outside_this_repo():
    live = _device_level_codes()
    added = sorted(live - _RECONCILED_CODES)
    removed = sorted(_RECONCILED_CODES - live)
    assert not (added or removed), (
        f"the offline-reason vocabulary changed (added: {added or 'none'}, "
        f"removed: {removed or 'none'}).\n\n"
        "Three documents restate this list in full and none of them is in "
        "this repo, so nothing else will catch them going stale:\n"
        "  - openavc-api-reference.md            (workspace repo)\n"
        "  - OpenAVC-Implementation-Design.md    (workspace repo)\n"
        "  - api/services/ai_system_prompt.py    (openavc-cloud)\n\n"
        "From the workspace root, `python tools/fault-code-docs.py --check` "
        "names each one that is missing the code. Update them, then update "
        "_RECONCILED_CODES here to match."
    )
