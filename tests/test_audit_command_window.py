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

from openavc.audit.commands import DONE, SENDING, CommandTrial, commands_for
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
        "set_code": {
            "label": "Set Code", "params": {"pin": {"type": "string", "secret": True}},
        },
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


def _window(conn, *, device_errors=(), error_writes=()):
    """Command 1, sent at 100.0 and returned at 100.5, its window already
    over: its own message went out before the return (traffic position 0),
    the driver's next request after it (position 2), a poll later on (4)."""
    for t, direction, text in (
        (100.1, TX, "PWR 1\r"), (100.2, RX, "OK"),
        (101.0, TX, "VOLUME LEVEL QUERY NUMBER ONE?\r"), (101.1, RX, "VOL=3"),
        (101.5, TX, "POLL?\r"),
    ):
        conn._wire(direction, text, t)
    return CommandTrial(
        number=1, command="reboot", label="Reboot", params={}, attempt=1, batch=False,
        connect_attempt=0, sent_at=100.0, returned_at=100.5, status=DONE,
        marks={"traffic": 0, "events": 0, "returned": 2},
        device_errors=list(device_errors), error_writes=list(error_writes),
    )


def test_which_signs_of_a_refusal_are_the_commands_and_which_came_later():
    conn = _Connection()
    trial = _window(
        conn,
        device_errors=[
            {"t": 100.3, "error": "", "position": 1},
            # Written as the next request went out: still the command's.
            {"t": 100.4, "error": "first", "position": 2},
            # Before the next request, but after the reply could still come.
            {"t": 103.0, "error": "too late", "position": 2},
            {"t": 101.2, "error": "busy", "position": 3},
        ],
        error_writes=[
            {"t": 100.35, "error": "refused", "position": 1},
            {"t": 100.45, "error": "", "position": 2},
            {"t": 100.46, "error": "refused again", "position": 2},
            {"t": 101.3, "error": "busy", "position": 3},
            {"t": 101.4, "error": "", "position": 4},
            # Placed after the poll though its clock reads earlier.
            {"t": 101.06, "error": "other", "position": 5},
        ],
    )
    _pass(conn)._conclude(trial)
    assert trial.refusals == {
        "device_errors": 2,
        "last_error_writes": 2,
        "last_error": "refused again",
        "later": [
            {"error": "too late", "after": 3.0, "count": 1, "request": '"PWR 1\\r"'},
            {"error": "busy", "after": 1.2, "count": 2,
             "request": '"VOLUME LEVEL QUERY NUMBER ONE?\\r"'},
            {"error": "other", "after": 1.1, "count": 1, "request": '"POLL?\\r"'},
        ],
        "unmatched": 0,
        "unmatched_examples": [],
    }


def test_with_no_last_error_the_refusal_is_the_last_error_published_with_words():
    conn = _Connection()
    trial = _window(conn, device_errors=[
        {"t": 100.3, "error": "no input", "position": 1},
        {"t": 100.31, "error": "no signal", "position": 1},
        {"t": 100.32, "error": "", "position": 1},
    ])
    _pass(conn)._conclude(trial)
    assert (trial.refusals["device_errors"], trial.refusals["last_error"]) == (3, "no signal")
    assert trial.refusals["later"] == []


async def test_what_the_window_hears_while_it_is_open():
    conn = _Connection()
    commands = _pass(conn, window_seconds=0.5)

    async def busy(command, params):
        conn.sent("PWR 1\r")
        for i in range(510):
            conn.report("meter", i)

    conn.call = busy
    trial = await commands.send("power_on")
    await conn.emit("device.error", {"error": "lamp out"})
    await conn.emit("device.error", "plain words")
    await conn.emit("device.error")
    await conn.emit("device.disconnected")
    first_drop = trial.went_away_at
    await conn.emit("device.disconnected")  # still away: the same drop
    await conn.emit("device.connected")
    await conn.emit("device.connected")  # back with no drop between: nothing new
    await conn.emit("device.disconnected")
    await _done(trial)
    assert len(trial.changes) == 500
    assert {k: trial.changes[0][k] for k in ("key", "old", "new")} == {"key": "meter", "old": None, "new": 0}
    assert [(e["error"], e["position"]) for e in trial.device_errors] == [
        ("lamp out", 1), ("plain words", 1), ("", 1),
    ]
    assert trial.went_away_at == first_drop
    [(went, back), (again, still_away)] = trial.gone_and_back
    assert went == first_drop and back is not None and again > back and still_away is None
    assert back <= trial.back_at < again
    assert trial.drops[1]["back_after"] is None


async def test_nothing_is_heard_once_the_window_has_closed():
    """Stopping the audit can close a window before its watch unwinds."""
    conn = _Connection()
    commands = _pass(conn, window_seconds=5.0)
    trial = await commands.send("power_on")
    await conn.emit("device.disconnected")
    [[went, _]] = trial.gone_and_back
    commands._finish(trial)
    conn.report("meter", 1)
    await conn.emit("device.error", {"error": "late"})
    await conn.emit("device.connected")
    await conn.emit("device.disconnected")
    assert (trial.changes, trial.device_errors, trial.gone_and_back, trial.back_at) == (
        [], [], [[went, None]], None,
    )
    await commands.stop()


async def test_a_restart_window_ends_soon_after_the_driver_is_back():
    conn = _Connection()
    commands = _pass(conn, restart_margin_seconds=10.0, after_reconnect_seconds=0.1)
    restart = await commands.send("reboot")
    assert restart.ends_at - restart.returned_at == 15.0
    await conn.emit("device.disconnected")
    await conn.emit("device.connected")
    assert abs(restart.ends_at - (restart.back_at + 0.1)) < 0.001
    await _done(restart, timeout=2.0)
    # A command that declares no restart keeps its window when the link comes back.
    blip = await commands.send("power_on")
    ends = blip.ends_at
    await conn.emit("device.disconnected")
    await conn.emit("device.connected")
    assert blip.ends_at == ends
    await _done(blip)


async def test_the_window_hears_no_return_before_a_drop():
    conn = _Connection()
    commands = _pass(conn)
    trial = await commands.send("power_on")
    await conn.emit("device.connected")
    assert (trial.back_at, trial.gone_and_back) == (None, [])
    await _done(trial)


# -- what the wizard is shown ------------------------------------------------------


async def test_a_known_secret_is_masked_wherever_the_live_view_shows_it():
    conn = _Connection()
    conn.observer.add_secrets({"tulip-7391"})
    commands = _pass(conn)

    async def leaks(command, params):
        conn.sent("TITLE tulip-7391\r")
        conn.report("title", "tulip-7391")
        await conn.emit("device.error", {"error": "bad tulip-7391"})
        conn.report("last_error", "no tulip-7391")

    conn.call = leaks
    trial = await _done(await commands.send("set_title", {"title": "tulip-7391"}))
    view = commands.to_dict()["trials"][0]
    assert view["params"] == {"title": "***"}
    assert [c["new"] for c in view["changes"] if c["key"] == "title"] == ["***"]
    assert [e["error"] for e in view["device_errors"]] == ["bad ***"]
    assert view["refusals"]["last_error"] == "no ***"
    # The record keeps what the device did; only what is shown is masked.
    assert trial.refusals["last_error"] == "no tulip-7391"


async def test_the_live_view_keeps_the_newest_and_the_report_keeps_every_one():
    conn = _Connection()
    commands = _pass(conn, window_seconds=5.0)

    async def chatty(command, params):
        conn.sent("PWR 1\r")
        for i in range(25):
            conn.report("meter", i)
            await conn.emit("device.error", {"error": f"e{i}"})

    conn.call = chatty
    trial = await commands.send("power_on")
    watching = commands.to_dict()["trials"][0]
    assert watching["summary"] == ""
    assert [c["new"] for c in watching["changes"]] == list(range(5, 25))
    assert [e["error"] for e in watching["device_errors"]] == [f"e{i}" for i in range(5, 25)]
    commands.end_now()
    await _done(trial)
    assert commands.to_dict()["trials"][0]["summary"]
    kept = commands.report_record()["trials"][0]
    assert len(kept["changes"]) == 25 and len(kept["device_errors"]) == 25


# -- sending -----------------------------------------------------------------------


async def test_a_refused_send_still_lets_its_staged_files_go():
    conn = _Connection()
    commands = _pass(conn)
    released = []
    conn.started = False
    try:
        await commands.send("power_on", on_finished=lambda: released.append("gone"))
    except Exception as exc:
        assert "Connect the driver first" in str(exc)
    assert released == ["gone"] and commands.trials == []


async def test_a_name_the_driver_does_not_declare_is_sent_under_that_name():
    conn = _Connection()
    commands = _pass(conn)
    trial = await _done(await commands.send("warp_drive"))
    assert (trial.command, trial.label, trial.declared) == ("warp_drive", "warp_drive", {})


async def test_a_file_command_is_answered_as_soon_as_it_starts():
    conn = _Connection()
    never = asyncio.Event()

    async def hang(command, params):
        await never.wait()

    conn.call = hang
    commands = _pass(conn, window_seconds=5.0)
    trial = await commands.send("power_on", files={"image": "fw.bin"})
    assert trial.file_command and trial.status == SENDING
    started = time.time()
    await commands.stop()
    assert time.time() - started < 1.0
    assert trial.status == DONE
    assert trial.returned_at is not None and trial.finished_at >= trial.returned_at


async def test_stopping_ends_the_status_queries_and_keeps_the_command_list():
    conn = _Connection()
    never = asyncio.Event()

    async def hang(command, params):
        await never.wait()

    conn.call = hang
    commands = _pass(conn)
    await commands.run_queries()
    await _until(lambda: commands.trials)
    await commands.stop()
    assert commands.batch["status"] == "done"
    names = [c["name"] for c in commands.catalog()]
    conn.started = False
    conn.driver = NS(DRIVER_INFO={"commands": {}}, config={}, _definition={})
    assert [c["name"] for c in commands.catalog()] == names


async def test_what_a_send_records_as_it_goes_out():
    conn = _Connection()
    commands = _pass(conn)
    blank = await _done(await commands.send("set_code", {"pin": ""}))
    assert commands.run.secrets == set() and conn.observer.secrets == set()
    assert blank.secret_params == {"pin"}
    typed = await _done(await commands.send("set_code", {"pin": "tulip-7391"}))
    assert commands.run.secrets == {"tulip-7391"} and conn.observer.secrets == {"tulip-7391"}
    power = await _done(await commands.send("power_on"))
    assert power.declared == {"sets": {"power": True}}
    assert typed.since_previous == {
        "number": 1, "command": "set_code", "label": "Set Code",
        "seconds": round(typed.sent_at - blank.sent_at, 3),
    }
    assert (blank.attempt, typed.attempt, power.attempt) == (1, 2, 1)
