"""Power cycle and cable pull: how the driver copes when the device goes away.

Two tests, one at a time, each started by the person and then driven by what
they do at the device:

- **Power cycle**: turn the device off at its power switch or unplug it, wait
  ten seconds, turn it back on.
- **Cable pull**: unplug the device's network cable, wait for OpenAVC to
  notice, plug it back in. A device that simply stops answering, with nothing
  closing the connection, is noticed only by a driver that checks: a liveness
  probe, or a send that fails.

Everything is measured through production's own machinery: the sandbox
manager's detection, its offline reasons, and its reconnect loop with
production's backoff. Three clocks run beside it:

- **The person's marks**: "I turned it off" / "I unplugged it", then "I
  turned it back on" / "I plugged it back in".
- **The device's own answers.** When the network check saw the device answer
  ping, the audit pings it once a second through the test, so when it stopped
  answering and when it answered again are measured, not guessed. ICMP only:
  a connection to the device's control port would take the one session some
  devices allow away from the driver under test.
- **The driver**: when OpenAVC noticed (its disconnect, with the offline
  reason the manager gave), and when the driver was connected again.

After the driver is back the test watches the status values be reported again
(``REPOPULATE_SECONDS`` and at least ``REPOPULATE_CYCLES`` poll cycles),
and a connection that drops again meanwhile (a device that accepts one as it
boots, then resets it) is recorded and waited out, so the test ends only
once the driver has stayed connected;
through the sandbox's own state store, which notes a value written again even
when it did not change. What the device announced meanwhile (an SSDP byebye as
it went, a NOTIFY, an mDNS announcement or an AMX beacon as it booted) comes
from the session's listeners.

**The ceiling.** OpenAVC has ``NOTICE_CEILING_SECONDS`` from the moment the
device went away to notice. Past it the result is "OpenAVC did not notice",
a finding about the driver, said plainly: a driver with no liveness probe
notices a device that went quiet only when something it sends fails. When the
device is back and OpenAVC has not noticed it was gone, the test ends
``BACK_UNNOTICED_SECONDS`` later, or for a driver with a liveness probe once
its probes have had time to fail (``watch.notice_within`` from when the device
went): a device that reboots leaves the old connection dead, and the probe
can notice after the device is back. Noticing then clears that deadline and
the test waits for the reconnect.

The test sends nothing but the pings (which the network check already sent).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from openavc.audit.session import AuditError
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.audit.passes import DriverRun
    from openavc.audit.session import AuditSession

log = get_logger(__name__)

POWER_CYCLE = "power_cycle"
CABLE_PULL = "cable_pull"
KINDS = (POWER_CYCLE, CABLE_PULL)

# OpenAVC has this long, from the moment the device went away, to notice.
NOTICE_CEILING_SECONDS = 300.0
# After the driver is back: how long to watch the status values come back.
REPOPULATE_SECONDS = 20.0
REPOPULATE_CYCLES = 3
REPOPULATE_MAX_SECONDS = 120.0
# The device is back and OpenAVC never noticed it went: end this long after.
BACK_UNNOTICED_SECONDS = 30.0
# A test that goes nowhere ends after this long.
MAX_SECONDS = 15 * 60.0
# How often the device is pinged, and how long each ping waits.
PING_SECONDS = 1.0
# One ping says little: a ping can be lost, and a device powering down can
# answer once more. The device is gone after this many misses in a row and
# back after this many replies in a row, each dated from the first of its run.
MISSES_GONE = 2
REPLIES_BACK = 3
PING_TIMEOUT = 0.8
# How often the wizard hears about a test.
FLUSH_SECONDS = 0.5

RUNNING = "running"
DONE = "done"
STOPPED = "stopped"

MARKS = ("off", "on")

NOT_CONNECTED = (
    "The driver is not connected to the device, so these tests cannot run. "
    "Connect it on the Connect and listen step first."
)
BUSY = "Wait for {label} to finish first."
TEST_RUNNING = "Finish the test that is running first."
NOT_RUNNING = "No power or cable test is running."


# The words for each test: its name, the person's two marks, and how the
# sentence says the device came back.
WORDS = {
    POWER_CYCLE: {
        "name": "Power cycle",
        "off": "The person said they turned the device off.",
        "on": "The person said they turned the device back on.",
        "back": "it was turned back on",
    },
    CABLE_PULL: {
        "name": "Cable pull",
        "off": "The person said they unplugged the network cable.",
        "on": "The person said they plugged the network cable back in.",
        "back": "the cable was plugged back in",
    },
}
# Back on before off: the button to press first.
MARK_ORDER = {
    POWER_CYCLE: "Press I turned it off first.",
    CABLE_PULL: "Press I unplugged it first.",
}

# Pings the audited device once; True when it answered.
Pinger = Callable[[], Awaitable[bool]]


def span_text(seconds: float) -> str:
    """"5 minutes", "1 minute", "40 seconds"."""
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    whole = round(seconds)
    return f"{whole} second{'' if whole == 1 else 's'}"


def _since(t: float | None, start: float | None) -> float | None:
    if t is None or start is None:
        return None
    return round(t - start, 1)


class OutageTest:
    """One power cycle or cable pull, for one driver run."""

    def __init__(
        self,
        session: "AuditSession",
        run: "DriverRun",
        kind: str,
        *,
        pinger: Pinger | None = None,
        notice_ceiling_seconds: float = NOTICE_CEILING_SECONDS,
        repopulate_seconds: float = REPOPULATE_SECONDS,
        back_unnoticed_seconds: float = BACK_UNNOTICED_SECONDS,
        max_seconds: float = MAX_SECONDS,
        ping_seconds: float = PING_SECONDS,
        flush_seconds: float = FLUSH_SECONDS,
    ) -> None:
        self.session = session
        self.run = run
        self.kind = kind
        self.number = len(getattr(run, "outages", []) or []) + 1
        self.connect_attempt = max(0, len(run.listens) - 1)
        self.notice_ceiling_seconds = notice_ceiling_seconds
        self.repopulate_seconds = repopulate_seconds
        self.back_unnoticed_seconds = back_unnoticed_seconds
        self.max_seconds = max_seconds
        self.ping_seconds = ping_seconds
        self.flush_seconds = flush_seconds
        self._pinger = pinger
        self.status = RUNNING
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.end_reason = ""
        # The person's marks.
        self.off_at: float | None = None
        self.on_at: float | None = None
        # The device's answers to ping (None: not measured, see ``ping``).
        self.unreachable_at: float | None = None
        self.reachable_at: float | None = None
        self.ping: dict[str, Any] = {"used": False, "why": ""}
        # The driver.
        self.noticed_at: float | None = None
        self.reason: dict[str, str] | None = None
        self.reasons: list[dict[str, Any]] = []
        self.reconnected_at: float | None = None
        # The connection dropping again after the reconnect, and coming back.
        self.dropped_again_at: float | None = None
        self.reconnected_again_at: float | None = None
        self.not_noticed_at: float | None = None
        self.ends_at: float | None = None
        # What set ``ends_at``: "settled" (the watch after the reconnect) or
        # "back_unnoticed" (back, and not noticed yet).
        self._ends_by = ""
        # Why the test stopped early: "person", "ceiling", or "" when it ended
        # on its own.
        self.end_code = ""
        # What came back after the reconnect, kept when the test ends.
        self._final: dict[str, Any] | None = None
        sandbox = run.listen.sandbox
        self._sandbox = sandbox
        state = sandbox.device_state()
        declared = [v["name"] for v in run.listen.status_table()["variables"]]
        # The status values that had a value going in: the ones that should
        # come back.
        self.before = {name: state.get(name) for name in declared if state.get(name) is not None}
        # How this driver would notice a device that went quiet: a liveness
        # probe (and how often), or only its polling and its sends failing.
        driver = sandbox.driver
        probe = bool(driver is not None and getattr(driver, "_health_enabled", lambda: False)())
        every = float(getattr(driver, "HEALTH_INTERVAL_S", 0) or 0) if probe else 0.0
        misses = max(int(getattr(driver, "HEALTH_MAX_FAILURES", 1) or 1), 1) if probe else 0
        wait = float(getattr(driver, "HEALTH_TIMEOUT_S", 0) or 0) if probe else 0.0
        self.watch = {
            "liveness_probe": probe,
            "probe_every": every,
            # The longest the probe takes to give up on a device that went:
            # each missed probe's interval and its reply deadline.
            "notice_within": round(misses * (every + wait), 1) if probe else 0.0,
            "poll_interval": float(run.listen.poll_interval or 0),
        }
        self._handles: tuple[str, list[str]] | None = None
        self.task: asyncio.Task | None = None

    # -- the clocks -------------------------------------------------------------

    def gone_at(self) -> float | None:
        """When the device went away: when it stopped answering ping, else the
        person's mark."""
        return self.unreachable_at or self.off_at

    def back_at(self) -> float | None:
        """When the device was back: when it answered ping again, else the
        person's mark."""
        if self.reachable_at is not None:
            return self.reachable_at
        return self.on_at

    def settled_at(self) -> float | None:
        """When the driver was connected for good: its reconnect, or its
        reconnect after the connection dropped again (None while it is
        still down)."""
        if self.dropped_again_at is not None:
            return self.reconnected_again_at
        return self.reconnected_at

    # -- running ------------------------------------------------------------------

    def start(self) -> None:
        sandbox = self._sandbox
        device_id = sandbox.device_id
        prefix = f"device.{device_id}."

        def on_gone(_event: str, _payload: Any = None) -> None:
            if self.status != RUNNING:
                return
            if self.noticed_at is None:
                self.noticed_at = time.time()
                if self._ends_by == "back_unnoticed":
                    # Noticed after all: wait for the reconnect instead.
                    self.ends_at = None
                    self._ends_by = ""
                gone = self.gone_at()
                after = f", {self.noticed_at - gone:.1f} s after the device went away" if gone else ""
                self._timeline("outage.noticed", f"OpenAVC noticed the device was gone{after}.")
            elif self.reconnected_at is not None and self.dropped_again_at is None:
                self.dropped_again_at = time.time()
                self.ends_at = None  # wait for the driver to come back again
                self._ends_by = ""
                self._timeline(
                    "outage.dropped_again",
                    f"The connection dropped again, "
                    f"{self.dropped_again_at - self.reconnected_at:.1f} s after the driver "
                    f"reconnected.",
                )

        def on_back(_event: str, _payload: Any = None) -> None:
            if self.status != RUNNING:
                return
            if self.noticed_at is not None and self.reconnected_at is None:
                self.reconnected_at = time.time()
                self._timeline("outage.reconnected", "The driver connected to the device again.")
            elif self.dropped_again_at is not None and self.reconnected_again_at is None:
                self.reconnected_again_at = time.time()
                self._timeline("outage.reconnected_again", "The driver connected to the device again.")

        def on_state(key: str, _old: Any, new: Any, _source: str = "") -> None:
            if self.status != RUNNING or key != f"{prefix}offline_reason" or not new:
                return
            detail = str(sandbox.state.get(f"{prefix}offline_detail") or "")
            entry = {"t": time.time(), "code": str(new), "detail": detail}
            self.reasons.append(entry)
            # The driver was connected when the test began, so the first
            # offline reason since is the one it gave for this outage (the
            # manager may write it just before the disconnect reaches us).
            if self.reason is None:
                self.reason = {"code": entry["code"], "detail": detail}

        self._handles = (
            sandbox.state.subscribe(f"{prefix}offline_reason", on_state),
            [
                sandbox.events.on(f"device.disconnected.{device_id}", on_gone),
                sandbox.events.on(f"device.connected.{device_id}", on_back),
            ],
        )
        self._prepare_ping()
        self._timeline(
            "outage.started",
            f"{WORDS[self.kind]['name']} test started"
            + ("; the audit pings the device every second" if self.ping["used"] else "")
            + ".",
        )
        self.task = self.session.track_task(asyncio.create_task(self._loop()))

    def _prepare_ping(self) -> None:
        if self._pinger is not None:
            self.ping = {"used": True, "why": ""}
            return
        from openavc.discovery import icmp

        ping = getattr(self.session.footprint, "ping", None) or {}
        if ping.get("result") != icmp.RESULT_ALIVE:
            self.ping = {
                "used": False,
                "why": "The device did not answer ping in the network check, so the times "
                       "come from the buttons pressed during the test.",
            }
            return
        method = ping.get("method") or icmp.METHOD_EXEC
        ip = self.session.target.ip
        source = getattr(self.session.check, "source_ip", "") or ""

        async def pinger() -> bool:
            result = await icmp.ping_host(ip, timeout=PING_TIMEOUT, source_ip=source, method=method)
            return result == icmp.RESULT_ALIVE

        self._pinger = pinger
        self.ping = {"used": True, "why": ""}

    async def _loop(self) -> None:
        next_ping = 0.0
        next_flush = 0.0
        answering: bool | None = None
        # The current run of identical answers: what, how many, since when.
        run_alive: bool | None = None
        run_length = 0
        run_from = 0.0
        try:
            while self.status == RUNNING:
                now = time.time()
                if self._pinger is not None and now >= next_ping:
                    next_ping = now + self.ping_seconds
                    try:
                        alive = await self._pinger()
                    except Exception:
                        log.debug("Audit ping failed", exc_info=True)
                        alive = None
                    now = time.time()
                    if alive is not None:
                        if alive is run_alive:
                            run_length += 1
                        else:
                            run_alive, run_length, run_from = alive, 1, now
                    if alive is False and run_length >= MISSES_GONE and answering is not False:
                        answering = False
                        if self.unreachable_at is None:
                            self.unreachable_at = run_from
                            self._timeline(
                                "outage.unreachable", "The device stopped answering ping.",
                            )
                        elif self.reachable_at is not None and self.reconnected_at is None:
                            # It answered for a while and went quiet again before
                            # the driver was back: it was not back yet.
                            self.reachable_at = None
                            if self._ends_by == "back_unnoticed":
                                self.ends_at = None
                                self._ends_by = ""
                            self._timeline(
                                "outage.unreachable_again",
                                "The device stopped answering ping again.",
                            )
                    elif alive and run_length >= REPLIES_BACK and answering is not True:
                        if answering is False and self.unreachable_at is not None \
                                and self.reachable_at is None:
                            self.reachable_at = run_from
                            self._timeline("outage.reachable", "The device answers ping again.")
                        answering = True
                self._step(now)
                if now >= next_flush:
                    next_flush = now + self.flush_seconds
                    self._publish()
                await asyncio.sleep(min(self.flush_seconds, self.ping_seconds) / 2)
        finally:
            if self.status == RUNNING:
                self._finish(STOPPED, "The test was stopped.")

    def _step(self, now: float) -> None:
        """Move the test on: the ceiling, the watch after reconnecting, the end."""
        gone = self.gone_at()
        if (
            gone is not None and self.noticed_at is None and self.not_noticed_at is None
            and now - gone >= self.notice_ceiling_seconds
        ):
            self.not_noticed_at = now
            self._timeline(
                "outage.not_noticed",
                f"OpenAVC did not notice the device was gone within "
                f"{span_text(self.notice_ceiling_seconds)}.",
            )
        settled = self.settled_at()
        if self._ends_by != "settled" and settled is not None:
            poll = float(self.run.listen.poll_interval or 0)
            span = min(
                max(self.repopulate_seconds, REPOPULATE_CYCLES * poll), REPOPULATE_MAX_SECONDS,
            )
            self.ends_at = settled + span
            self._ends_by = "settled"
        back = self.back_at()
        if self.ends_at is None and self.noticed_at is None and back is not None \
                and gone is not None and back >= gone:
            # A liveness probe that notices after the device is back (the old
            # connection died with the reboot) gets its full time to do so.
            self.ends_at = max(
                back + self.back_unnoticed_seconds,
                gone + self.watch["notice_within"] + self.back_unnoticed_seconds / 3,
            )
            self._ends_by = "back_unnoticed"
        if self.ends_at is not None and now >= self.ends_at:
            self._finish(DONE, "")
        elif now - self.started_at >= self.max_seconds:
            self.end_code = "ceiling"
            self._finish(STOPPED, f"The test stopped after {span_text(self.max_seconds)}.")

    def mark(self, which: str) -> None:
        """The person's mark: the device went off, or it is back on."""
        if self.status != RUNNING:
            raise AuditError(NOT_RUNNING)
        now = time.time()
        if which == "off":
            if self.off_at is None:
                self.off_at = now
                self._timeline("outage.off", WORDS[self.kind]["off"])
        else:
            if self.off_at is None and self.unreachable_at is None:
                raise AuditError(MARK_ORDER[self.kind])
            if self.on_at is None:
                self.on_at = now
                self._timeline("outage.on", WORDS[self.kind]["on"])
        self._publish()

    async def stop(self, reason: str = "The test was stopped.") -> None:
        task = self.task
        if self.status == RUNNING:
            self.end_code = self.end_code or "person"
            self._finish(STOPPED, reason)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _finish(self, status: str, reason: str) -> None:
        if self.status != RUNNING:
            return
        self.status = status
        self.end_reason = reason
        self.finished_at = time.time()
        if self._handles is not None:
            sub, handlers = self._handles
            self._sandbox.state.unsubscribe(sub)
            for handler in handlers:
                self._sandbox.events.off(handler)
            self._handles = None
        self._final = self._repopulation()
        self._timeline("outage.done", outage_sentence(self.to_dict()))
        self._publish()

    # -- reading ------------------------------------------------------------------

    def _repopulation(self) -> dict[str, Any]:
        """Which of the values the device had going in it reported again after
        the driver reconnected (a write counts, changed or not)."""
        if self._final is not None:
            return self._final
        since = self.settled_at()
        if since is None:
            return {"reported_again": [], "not_reported_again": sorted(self.before)}
        written = self._sandbox.written_since(since) if self._sandbox.started else set()
        again = sorted(name for name in self.before if name in written)
        return {
            "reported_again": again,
            "not_reported_again": sorted(name for name in self.before if name not in written),
        }

    def to_dict(self) -> dict[str, Any]:
        gone, back = self.gone_at(), self.back_at()
        repopulated = self._repopulation()
        heard: list[dict[str, Any]] = []
        check = self.session.check
        if check is not None and hasattr(check, "heard_since"):
            heard = check.heard_since(self.started_at, self.finished_at)
        return {
            "number": self.number,
            "kind": self.kind,
            "status": self.status,
            "end_reason": self.end_reason,
            "end_code": self.end_code,
            "connect_attempt": self.connect_attempt,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "off_at": self.off_at,
            "on_at": self.on_at,
            "unreachable_at": self.unreachable_at,
            "reachable_at": self.reachable_at,
            "noticed_at": self.noticed_at,
            "reconnected_at": self.reconnected_at,
            "dropped_again_at": self.dropped_again_at,
            "reconnected_again_at": self.reconnected_again_at,
            "not_noticed_at": self.not_noticed_at,
            "ends_at": self.ends_at,
            "notice_ceiling_seconds": self.notice_ceiling_seconds,
            "ping": dict(self.ping),
            "watch": dict(self.watch),
            "reason": dict(self.reason) if self.reason else None,
            "reasons": [dict(r) for r in self.reasons],
            "measured": {
                # Seconds: how long OpenAVC took to notice the device had gone,
                # how long the device was away, how long it took to answer
                # again after being turned back on, and how long the driver
                # took to reconnect once the device was back.
                "noticed_after": _since(self.noticed_at, gone),
                "away_for": _since(back, gone) if back is not None and gone is not None
                and back >= gone else None,
                "answered_after_on": _since(self.reachable_at, self.on_at)
                if self.reachable_at is not None and self.on_at is not None
                and self.reachable_at >= self.on_at else None,
                "reconnected_after_back": _since(self.reconnected_at, back)
                if self.reconnected_at is not None and back is not None
                and self.reconnected_at >= back else None,
                # How long the connection held after the reconnect before it
                # dropped again, and how long the driver took to be back.
                "dropped_again_after": _since(self.dropped_again_at, self.reconnected_at),
                "reconnected_again_after": _since(self.reconnected_again_at, self.dropped_again_at),
            },
            "before": sorted(self.before),
            "repopulated": repopulated,
            "announcements": heard,
        }

    def record(self) -> dict[str, Any]:
        """The test as the report and the wizard keep it, with its sentence
        once it has ended."""
        out = self.to_dict()
        out["summary"] = outage_sentence(out) if self.status != RUNNING else ""
        return out

    def _timeline(self, kind: str, text: str) -> None:
        self.session.add_timeline(kind, text, run=self.run.index, outage=self.number,
                                  test=self.kind)

    def _publish(self) -> None:
        self.session.publish({"type": "audit.outage", "run": self.run.index,
                              "outage": self.record()})


def outage_sentence(record: dict[str, Any]) -> str:
    """What one power cycle or cable pull showed, as the timeline, the wizard
    and the summary say it."""
    measured = record.get("measured") or {}
    parts: list[str] = []
    # How the sentence says the test ended: its own end, the person's Stop,
    # or the time limit, said once, where the driver's state is.
    code = record.get("end_code") or ""
    end_text = (record.get("end_reason") or "").rstrip(".")
    ended = (
        "when " + end_text[:1].lower() + end_text[1:] if code == "ceiling" and end_text
        else "when the test was stopped" if code == "person"
        else "when the test ended"
    )
    said_end = False
    if record.get("noticed_at"):
        reason = record.get("reason") or {}
        why = (reason.get("detail") or reason.get("code") or "").rstrip(".")
        after = measured.get("noticed_after")
        if after is not None and after < 0:
            # The control connection went before the device stopped
            # answering ping (a service that stops before the network does).
            when = f" {-after} s before it stopped answering ping"
        elif after is not None:
            when = f" {after} s after it went away"
        else:
            when = ""
        parts.append("OpenAVC noticed the device was gone" + when + (f" ({why})" if why else ""))
    elif record.get("not_noticed_at") or record.get("status") == DONE:
        ceiling = span_text(record.get("notice_ceiling_seconds") or NOTICE_CEILING_SECONDS)
        text = (
            f"OpenAVC did not notice the device was gone within {ceiling}"
            if record.get("not_noticed_at")
            else "OpenAVC did not notice the device was gone"
        )
        if not (record.get("watch") or {}).get("liveness_probe"):
            text += (
                ". This driver does not check on its own whether the device is still "
                "there, so it notices only when something it sends fails"
            )
        parts.append(text)
    if measured.get("answered_after_on") is not None:
        back = WORDS.get(record.get("kind"), WORDS[POWER_CYCLE])["back"]
        parts.append(f"the device answered again {measured['answered_after_on']} s after {back}")
    if record.get("reconnected_at"):
        after = measured.get("reconnected_after_back")
        parts.append(
            "the driver reconnected"
            + (f" {after} s after the device was back" if after is not None else "")
        )
        if record.get("dropped_again_at"):
            held = measured.get("dropped_again_after")
            dropped = "the connection dropped again" + (f" {held} s later" if held is not None else "")
            if record.get("reconnected_again_at"):
                again = measured.get("reconnected_again_after")
                dropped += ", and the driver reconnected" + (
                    f" {again} s after that" if again is not None else ""
                )
            else:
                dropped += f", and the driver had not reconnected {ended}"
                said_end = True
            parts.append(dropped)
        repopulated = record.get("repopulated") or {}
        again = len(repopulated.get("reported_again") or [])
        total = again + len(repopulated.get("not_reported_again") or [])
        if total:
            parts.append(f"{again} of {total} status values were reported again")
    elif record.get("noticed_at"):
        parts.append(f"the driver had not reconnected {ended}")
        said_end = True
    announced = len(record.get("announcements") or [])
    if announced:
        parts.append(f"the device announced itself {announced} "
                     f"{'time' if announced == 1 else 'times'} meanwhile")
    if end_text and not said_end:
        parts.append(end_text[:1].lower() + end_text[1:])
    if not parts:
        return "Nothing was measured."
    text = "; ".join(parts) + "."
    return text[:1].upper() + text[1:]


def start_outage(
    session: "AuditSession", run: "DriverRun", kind: str, **options: Any,
) -> OutageTest:
    """Start a power cycle or cable pull for ``run`` (the driver must be
    connected, and nothing else running against the device)."""
    listen = run.listen
    if listen is None or not listen.sandbox.started or not listen.sandbox.connected():
        raise AuditError(NOT_CONNECTED)
    current = current_outage(run)
    if current is not None:
        raise AuditError(TEST_RUNNING)
    commands = run.commands
    if commands is not None:
        if commands.batch is not None and commands.batch.get("status") == "running":
            raise AuditError(BUSY.format(label="the status queries"))
        if commands.current() is not None:
            raise AuditError(BUSY.format(label=commands.current().label))
    settings = run.settings
    if settings is not None and settings.current() is not None:
        raise AuditError(BUSY.format(label=settings.current().label))
    test = OutageTest(session, run, kind, **options)
    run.outages.append(test)
    run.extra["outages"] = lambda: [t.record() for t in run.outages]
    session.enter_step("outage")
    test.start()
    return test


def current_outage(run: "DriverRun") -> OutageTest | None:
    for test in getattr(run, "outages", None) or []:
        if test.status == RUNNING:
            return test
    return None
