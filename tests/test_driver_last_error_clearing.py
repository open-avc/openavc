"""``last_error`` is cleared by a poll that reports nothing.

Drivers write a ``last_error`` state variable when the device rejects a
command or answers with something they cannot parse. Nothing ever cleared it,
so one transient failure stayed on screen for as long as the device stayed
connected — "once, three weeks ago" and "happening now" looked identical, and
a field that cannot tell those apart teaches people to ignore it. Then the
real one is ignored too.

The rule is deliberately narrow: a poll that finishes cleanly AND writes
nothing to the property clears it. A fault that is still happening rewrites
the value on its way past, which is why the loop counts writes instead of
comparing values — a persistent fault writes the identical string every cycle
and would look untouched.
"""

from __future__ import annotations

import asyncio

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


class _AcmeWidget(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {
            "last_error": {"type": "string", "label": "Last Error"},
            "level": {"type": "integer", "label": "Level"},
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.polls = 0
        self.error_each_poll: str | None = None
        self.raise_each_poll: BaseException | None = None

    async def connect(self) -> bool:
        return True

    async def send_command(self, command: str, params: dict | None = None):
        return None

    async def poll(self) -> None:
        self.polls += 1
        if self.raise_each_poll is not None:
            raise self.raise_each_poll
        if self.error_each_poll is not None:
            self.set_state("last_error", self.error_each_poll)
        self.set_state("level", self.polls)


def _driver(driver_id: str = "acme1") -> _AcmeWidget:
    return _AcmeWidget(driver_id, {}, StateStore(), EventBus())


async def _one_poll_cycle(driver: _AcmeWidget) -> None:
    """Run the real poll loop for exactly one iteration."""
    task = asyncio.create_task(driver._poll_loop(0.01))
    for _ in range(200):
        await asyncio.sleep(0.005)
        if driver.polls >= 1:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_a_clean_poll_clears_a_stale_error():
    driver = _driver()
    driver.set_state("last_error", "cannot parse - ' /amp/channels/2'")
    assert driver.get_state("last_error")

    await _one_poll_cycle(driver)

    assert driver.get_state("last_error") == ""


@pytest.mark.asyncio
async def test_an_error_the_poll_reports_again_survives():
    # The failure this rule must not cause: a fault that is still happening
    # writes the same string every cycle, so a value comparison would read it
    # as untouched and clear a live error.
    driver = _driver()
    driver.error_each_poll = "cannot parse - ' /amp/channels/2'"
    # Seeded with the SAME string the poll is about to write again. Without
    # this the value changes from None on the first poll, and a value
    # comparison would look correct while being wrong for every poll after.
    driver.set_state("last_error", "cannot parse - ' /amp/channels/2'")

    await _one_poll_cycle(driver)

    assert driver.get_state("last_error") == "cannot parse - ' /amp/channels/2'"


@pytest.mark.asyncio
async def test_a_failed_poll_leaves_the_error_alone():
    # The device stopped answering. Whatever it last told us is now the most
    # informative thing on the card, and nothing has contradicted it.
    driver = _driver()
    driver.set_state("last_error", "connection reset")
    driver.raise_each_poll = ConnectionError("still down")

    await _one_poll_cycle(driver)

    assert driver.get_state("last_error") == "connection reset"


@pytest.mark.asyncio
async def test_a_driver_that_never_declared_the_property_is_left_alone(monkeypatch):
    """The platform does not invent the convention for a driver without it."""

    class _NoErrorVar(_AcmeWidget):
        DRIVER_INFO = {
            "id": "acme_quiet",
            "name": "Acme Quiet",
            "transport": "tcp",
            "state_variables": {"level": {"type": "integer"}},
        }

    driver = _NoErrorVar("acme2", {}, StateStore(), EventBus())
    # Written directly to the store: an undeclared key still lands (the
    # platform reports it elsewhere), and clearing it is not this rule's job.
    driver.state.set("device.acme2.last_error", "left by something else")
    # Strict mode off on purpose. With it on, an undeclared write RAISES, and
    # this test would pass whether or not the rule checks the declaration —
    # the exception would do the work and the guard could be deleted unnoticed.
    monkeypatch.delenv("OPENAVC_STRICT_DRIVER_STATE", raising=False)

    await _one_poll_cycle(driver)

    assert driver.get_state("last_error") == "left by something else"


@pytest.mark.asyncio
async def test_clearing_uses_the_shared_seed_rule_not_a_guess():
    """Cleared means "what this variable holds before anything is read".

    That is one rule with one home (``compiled_protocol.state_var_default``),
    shared with the runtime's own seeding and the simulator's. Clearing to a
    hand-picked ``""`` or ``None`` here would be a second answer to a question
    already settled, and a numeric ``last_error`` (unusual, but declarable)
    would come back a string.
    """
    from openavc.drivers.compiled_protocol import state_var_default

    driver = _driver()
    var_def = driver.DRIVER_INFO["state_variables"]["last_error"]
    driver.set_state("last_error", "something went wrong")

    await _one_poll_cycle(driver)

    assert driver.get_state("last_error") == state_var_default(var_def)


@pytest.mark.asyncio
async def test_a_batch_write_counts_as_the_poll_reporting_it():
    # set_states is the other write door, and a driver that reports its error
    # alongside the rest of a poll's values uses it.
    driver = _driver()

    async def _poll_with_batch():
        driver.polls += 1
        driver.set_states({"level": driver.polls, "last_error": "still bad"})

    driver.poll = _poll_with_batch  # type: ignore[method-assign]
    driver.set_state("last_error", "still bad")

    await _one_poll_cycle(driver)

    assert driver.get_state("last_error") == "still bad"
