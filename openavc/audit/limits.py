"""What a device audit report says the audit could not see, and why: the
``limits`` list of ``report.json``, each ``{"id", "text"}``, in order.

First whether the network check ran and finished, then the check's own
limits (``footprint.Limit``, carried in its footprint), then what each driver
run could not see, read off the live run when the report is built: a driver
chosen and never connected, traffic not captured or cut at the cap, outage
times taken from the person's marks, a push the device never sent, and
events past the kept count. A driver run's limit also carries its ``run``.
Last, the session log when it stopped keeping lines.

Each run says each kind of limit at most once, however many of its attempts
or outages it applies to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openavc.audit.session import AuditSession


def report_limits(
    session: "AuditSession", footprint: Any, footprint_limits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every limit the report states, ``footprint`` being the check's
    footprint as the report read it (None when the check did not run) and
    ``footprint_limits`` the limits it carries."""
    return [*_check_limits(session, footprint), *footprint_limits, *driver_limits(session)]


def _check_limits(session: "AuditSession", footprint: Any) -> list[dict[str, Any]]:
    """Whether the network check ran, and finished before the report."""
    if footprint is None:
        return [{"id": "check_not_run", "text": "The network check did not run."}]
    if session.footprint is None:
        return [{
            "id": "check_unfinished",
            "text": "The network check had not finished when this report was taken.",
        }]
    return []


def driver_limits(session: "AuditSession") -> list[dict[str, Any]]:
    """What the driver test could not see, per run."""
    runs = list(getattr(session, "runs", None) or [])
    limits = _untested_limit(session, runs)
    for run in runs:
        limits.extend(_run_limits(run))
    limits.extend(_log_limit(session))
    return limits


def _untested_limit(session: "AuditSession", runs: list[Any]) -> list[dict[str, Any]]:
    """No driver connected at all, so the report is the network check's."""
    if any(run.listens for run in runs):
        return []
    text = (
        "The person said there is no driver for this device yet, so none was tested; "
        "this report covers the network check only."
        if getattr(session, "no_driver", False)
        else "No driver was tested; this report covers the network check only."
    )
    return [{"id": "no_driver_tested", "text": text}]


def _run_limits(run: Any) -> list[dict[str, Any]]:
    """One run's limits: never connected, or what its connections could not
    keep."""
    name = run.choice.identity.get("name") or run.choice.driver_id
    if not run.listens:
        return [{
            "id": "driver_not_connected", "run": run.index,
            "text": f"{name} was chosen, but the audit did not connect it.",
        }]
    found = (
        _not_captured_limit(run, name),
        _outage_limit(run, name),
        _truncated_limit(run, name),
        _push_limit(run, name),
        _events_limit(run, name),
    )
    return [limit for limit in found if limit is not None]


def _not_captured_limit(run: Any, name: str) -> dict[str, Any] | None:
    views = [attempt.to_dict()["traffic"] for attempt in run.listens]
    if not any(v["not_captured"] for v in views):
        return None
    return {
        "id": "traffic_not_captured", "run": run.index,
        "text": f"{name} manages its own connection, so its traffic was not captured.",
    }


def _outage_limit(run: Any, name: str) -> dict[str, Any] | None:
    """The first outage test whose times come from the person's marks,
    because the device does not answer ping."""
    test = next(
        (test for test in getattr(run, "outages", None) or [] if not test.ping.get("used")),
        None,
    )
    if test is None:
        return None
    return {
        "id": "outage_by_marks", "run": run.index,
        "text": f"The device does not answer ping, so the times in {name}'s "
                f"{test.to_dict()['kind'].replace('_', ' ')} test come from the "
                "buttons pressed during it.",
    }


def _truncated_limit(run: Any, name: str) -> dict[str, Any] | None:
    from openavc.audit.observe import TRAFFIC_CAP_BYTES

    if not any(attempt.sandbox.observer.truncated_at is not None for attempt in run.listens):
        return None
    cap_mb = TRAFFIC_CAP_BYTES // (1024 * 1024)
    return {
        "id": "traffic_truncated", "run": run.index,
        "text": f"{name}'s traffic passed {cap_mb} MB, so the report keeps the "
                f"first {cap_mb} MB of it.",
    }


def _push_limit(run: Any, name: str) -> dict[str, Any] | None:
    """The first connection whose driver asked the device to push its events
    to OpenAVC and heard none."""
    from openavc.audit.listen import push_heard, push_unheard_text

    for attempt in run.listens:
        urls = attempt.sandbox.push_callbacks()
        if urls and not push_heard(attempt.sandbox):
            return {
                "id": "push_never_arrived", "run": run.index, "callbacks": urls,
                "text": push_unheard_text(name, urls),
            }
    return None


def _events_limit(run: Any, name: str) -> dict[str, Any] | None:
    from openavc.audit.observe import EVENT_DETAIL_KEPT

    if not any(
        count > EVENT_DETAIL_KEPT
        for attempt in run.listens
        for count in attempt.sandbox.observer.event_counts.values()
    ):
        return None
    return {
        "id": "events_capped", "run": run.index,
        "text": f"Where {name} did the same thing wrong more than {EVENT_DETAIL_KEPT} "
                "times, the report keeps the first "
                f"{EVENT_DETAIL_KEPT} in full and counts the rest.",
    }


def _log_limit(session: "AuditSession") -> list[dict[str, Any]]:
    log_lines = getattr(session, "log", None)
    if log_lines is None or not log_lines.dropped:
        return []
    return [{
        "id": "log_capped",
        "text": f"OpenAVC wrote more than {log_lines.keep} log lines about this audit; "
                f"log.txt keeps the first {log_lines.keep}.",
    }]
