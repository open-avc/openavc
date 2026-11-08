"""A command's window on a device audit, case by case, on a fake connection.

The end-to-end command tests drive a loopback device; these drive the same
commands step through its own doors (Send, Stop, "Wait longer", "Run all
status queries") against a connection whose driver call, traffic and status
values the test writes itself, for the cases a real device cannot be made to
hit on cue: the audit stopped while the driver's call is still running, a
refusal whose bytes are stamped after the call returned, the driver stopping
mid-window, a drop that never ends, a refusal written twice after the
driver's own request.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace as NS

from openavc.audit.commands import DONE, commands_for
from openavc.audit.driver_choice import DriverChoice
from openavc.audit.observe import AuditObserver
from openavc.audit.passes import DriverRun
from openavc.audit.sandbox import AuditStateStore, DriverSandbox
from openavc.audit.session import AuditOptions, AuditSession, AuditTarget
from openavc.core.device_traffic import RX, TX, TrafficEntry
from openavc.core.event_bus import EventBus

INFO = {
    "name": "Acme Window",
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "title": {"type": "string", "label": "Title"},
        "last_error": {"type": "string", "label": "Last error"},
    },
    "commands": {
        "power_on": {"label": "Power On", "sets": {"power": True}},
        "query_power": {"label": "Query Power", "query_for": "power"},
        "set_title": {
            "label": "Set Title", "sets": {"title": "{title}"},
            "params": {"title": {"type": "string", "required": True}},
        },
        "reboot": {"label": "Reboot", "restarts_device_for": 5},
        "power_cycle": {"label": "Power Cycle", "sets": {"power": True}, "restarts_device_for": 1},
        # A status query that also declares a value.
        "query_title": {"label": "Query Title", "query_for": "title", "sets": {"title": "Lobby"}},
    },
}


class _Connection:
    """One connect-and-listen attempt and its sandbox, whose driver call
    (``call``), traffic and status values the test writes."""

    def __init__(self, *, poll_interval: float = 0.0) -> None:
        self.device_id = "audit-acme"
        self.state = AuditStateStore()
        self.events = EventBus()
        self.observer = AuditObserver(self.device_id)
        self.state.position = lambda: len(self.observer.traffic)
        self.started = True
        self.driver = NS(DRIVER_INFO=INFO, config={}, _definition={})
        self.sandbox = self
        self.manager = self
        self.poll_interval = poll_interval
        self.ended: list[str] = []
        self.call = self._answers

    async def _answers(self, command, params):
        self.sent(f"{command}\r")
        return None

    # -- the sandbox ----------------------------------------------------------

    async def send_command(self, device_id, command, params, *, files_allowed=False):
        return await self.call(command, params)

    def device_state(self):
        return self.state.get_namespace(f"device.{self.device_id}.")

    def written_after(self, seq):
        return DriverSandbox.written_after(self, seq)

    # -- the listen attempt ---------------------------------------------------

    def end_window_now(self, why):
        self.ended.append(why)

    def status_table(self):
        return {}

    # -- what the device does -------------------------------------------------

    def _wire(self, direction, text, t):
        self.observer.traffic.append(TrafficEntry(
            t=time.time() if t is None else t, device_id=self.device_id,
            direction=direction, channel="tcp", data=text.encode(),
        ))

    def sent(self, text, t=None):
        self._wire(TX, text, t)

    def heard(self, text, t=None):
        self._wire(RX, text, t)

    def report(self, key, value):
        self.state.set(f"device.{self.device_id}.{key}", value)

    def emit(self, event, payload=None):
        return self.events.emit(f"{event}.{self.device_id}", payload)


def _pass(conn, **timings):
    session = AuditSession("win1", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(driver_id="acme_window", identity={"name": "Acme"}))
    run.listen = conn
    run.listens = [conn]
    session.runs.append(run)
    return commands_for(session, run, **{"window_seconds": 0.3, "flush_seconds": 0.02, **timings})


async def _until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def _done(trial, timeout: float = 5.0):
    await _until(lambda: trial.status == DONE, timeout)
    return trial


# -- the driver's call ---------------------------------------------------------


async def test_the_audit_stopped_while_the_call_runs_says_so():
    conn = _Connection()
    never = asyncio.Event()

    async def hang(command, params):
        await never.wait()

    conn.call = hang
    commands = _pass(conn)
    sending = asyncio.create_task(commands.send("power_on"))
    await _until(lambda: commands.trials)
    await asyncio.sleep(0.02)
    await commands.stop()
    trial = await sending
    assert trial.status == DONE
    assert trial.error == "The audit stopped before the command finished."
    assert trial.error_type == ""
    assert trial.returned_at is not None


async def test_a_refusal_counts_only_the_bytes_sent_before_it_returned():
    conn = _Connection()
    commands = _pass(conn)

    async def late_bytes(command, params):
        # Stamped after the call returns: not a byte sent before the refusal.
        conn.sent("PWR 1\r", t=time.time() + 0.2)
        raise ValueError("refused by the driver")

    conn.call = late_bytes
    late = await _done(await commands.send("power_on"))
    assert late.error == "refused by the driver" and late.error_type == "ValueError"
    assert late.ends_at == late.returned_at

    async def bytes_first(command, params):
        conn.sent("PWR 1\r")
        raise ValueError("refused by the driver")

    conn.call = bytes_first
    watched = await _done(await commands.send("power_on"))
    assert watched.ends_at == watched.returned_at + 0.3


# -- how long the window stays open ----------------------------------------------


async def test_wait_longer_keeps_a_window_that_waits_for_a_read_back():
    """A declared value already read back ends the window at the usual time,
    unless "Wait longer" was pressed."""
    conn = _Connection(poll_interval=0.1)  # the read-back wait: 0.1 s + the reply window
    commands = _pass(conn, window_seconds=0.1, max_window_seconds=5.0)

    async def takes_it(command, params):
        conn.sent("PWR 1\r")
        conn.report("power", True)

    conn.call = takes_it
    usual = await _done(await commands.send("power_on"))
    assert usual.finished_at - usual.returned_at < 1.0
    held = await commands.send("power_on")
    commands.extend(0.2)
    await _done(held)
    assert abs(held.extended - 0.2) < 0.001
    assert held.finished_at - held.returned_at > 2.0


async def test_the_window_ends_when_the_driver_stops():
    conn = _Connection()
    commands = _pass(conn, window_seconds=5.0)
    trial = await commands.send("power_on")
    conn.started = False
    await _done(trial, timeout=2.0)
    assert trial.finished_at < trial.ends_at


# -- what the window hears ----------------------------------------------------------


async def test_only_the_devices_own_last_error_is_kept():
    conn = _Connection()
    commands = _pass(conn)

    async def refuses(command, params):
        conn.sent("PWR 1\r")
        conn.report("input.1.last_error", "a child's")
        conn.state.set("device.audit-other.last_error", "another device's")
        conn.report("last_error", "refused")

    conn.call = refuses
    trial = await _done(await commands.send("power_on"))
    assert [(w["error"], w["position"]) for w in trial.error_writes] == [("refused", 1)]


async def test_a_restart_window_is_never_cut_short_by_a_read_back():
    """A command that restarts the device and declares a value is watched for
    the whole window, whatever was read back."""
    conn = _Connection(poll_interval=0.1)
    commands = _pass(conn, window_seconds=0.1, max_window_seconds=5.0, restart_margin_seconds=0.5)

    async def takes_it(command, params):
        conn.sent("CYCLE\r")
        conn.report("power", True)

    conn.call = takes_it
    trial = await _done(await commands.send("power_cycle"))
    assert trial.finished_at - trial.returned_at > 1.4


async def test_the_status_queries_are_each_watched_for_the_query_window():
    """Even one that declares a value on a driver that polls: a status
    query's window is never held for a read-back."""
    conn = _Connection(poll_interval=0.1)
    commands = _pass(conn, window_seconds=2.0, query_window_seconds=0.05)
    await commands.run_queries()
    await _until(lambda: commands.batch["status"] == "done")
    assert [(q.command, q.batch) for q in commands.trials] == [
        ("query_power", True), ("query_title", True),
    ]
    for query in commands.trials:
        assert abs((query.ends_at - query.returned_at) - 0.05) < 0.001, query.command


# -- which signs of a refusal are the command's ---------------------------------------


async def test_the_commands_own_message_is_the_last_sent_before_it_returned():
    """A poll already on the way goes out first: a refusal after the command's
    own message, before the driver's next request, is the command's."""
    conn = _Connection()
    commands = _pass(conn)

    async def behind_a_poll(command, params):
        conn.sent("POLL?\r")
        conn.sent("PWR 1\r")
        conn.report("last_error", "refused")

    conn.call = behind_a_poll
    trial = await commands.send("power_on")
    conn.sent("NEXT?\r")
    await _done(trial)
    assert trial.refusals["last_error"] == "refused"
    assert trial.refusals["last_error_writes"] == 1
    assert trial.refusals["later"] == []


async def test_a_command_sent_after_its_call_returned_still_owns_its_refusal():
    """Nothing went out before the call returned: the first message after it
    is the command's own, and a refusal after that is still the command's."""
    conn = _Connection()
    commands = _pass(conn)

    async def queued(command, params):
        return None

    conn.call = queued
    trial = await commands.send("power_on")
    conn.sent("PWR 1\r")
    conn.report("last_error", "refused")
    await _done(trial)
    assert trial.refusals["last_error"] == "refused"
    assert trial.refusals["later"] == []
