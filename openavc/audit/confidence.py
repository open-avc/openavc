"""What one driver run's results support: the suggested catalog confidence
for the model, and the catalog's Driver test report filled in from them.

Both read a driver section of the record as ``report.json`` holds it, after
redaction, so neither can carry a secret. ``build_report`` sets
``suggested_confidence`` on each section first; the test report then reads
that back from the section, its reasons becoming what worked and what did
not.

A test report is public: no field names the device's address, host name or
MAC, or this computer's (``NO_ADDRESS`` stands in for each), and the two
free-text fields lose any other address too. Its link carries what fits in
``TEST_REPORT_URL_MAX``, cutting what worked before what did not; the detail
lives in the report file attached to it.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

# The catalog's issue form for a driver test report, and the most a link may
# carry (GitHub answers "URI Too Long" well before a browser gives up).
TEST_REPORT_FORM = (
    "https://github.com/open-avc/openavc-drivers/issues/new?template=driver-test-report.yml"
)
TEST_REPORT_URL_MAX = 6000

ANSWER_WORDS = {"no": "the person said it did not happen", "partly": "the person said it partly happened"}

_MORE_IN_REPORT = "(more in the attached report)"

# An address in a sentence: IPv4 (with its port), IPv6, a MAC.
_ADDRESS_PATTERNS = (
    re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?![\w.])"),
    re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}(?![\w:])"),
    re.compile(r"(?<![\w:])(?=[0-9A-Fa-f:]*::)[0-9A-Fa-f:]{3,}(?![\w:])"),
    re.compile(r"(?<![\w:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:-])"),
)
NO_ADDRESS = "[address]"

_COUNTS_AS_CONFIRMED = (
    "A command counts as confirmed when the person answered Yes, the values it should set "
    "read back, or, for a status query, its value came back."
)


def device_was_silent(traffic: dict[str, Any]) -> bool:
    """The connection's traffic was captured and nothing came back on it, so
    any status value the driver shows is its own bookkeeping."""
    return not traffic.get("not_captured") and not traffic.get("received")


# ---------------------------------------------------------------------------
# Suggested confidence
# ---------------------------------------------------------------------------


def suggested_confidence(section: dict[str, Any]) -> dict[str, Any]:
    """A suggestion for the catalog's per-model confidence, with every
    condition it rests on: ``level`` ``full`` (connected, every status value
    reported, nothing the driver could not handle, every command tried
    confirmed, every setting written read back and put back), ``partial``
    (connected and at least one command confirmed) or null; ``reasons``, each
    ``{"held", "text"}``. A command is confirmed when the person said the
    device did it, every value it declares it sets read back, or, a status
    query, the value it asks for came back (:func:`_command_confirmed`)."""
    attempts = section.get("attempts") or []
    connected = [a for a in attempts if a.get("connected_at")]
    if not connected:
        return {"level": None, "reasons": [_reason(False, "Did not connect.")]}
    # The last connection is the one the run ended on.
    last = connected[-1]
    traffic = last.get("traffic") or {}
    commands, any_confirmed = _commands_reason(section)
    reasons = [
        reason for reason in (
            _reason(True, "Connected."),
            _answered_reason(traffic),
            _values_reason(last, device_was_silent(traffic)),
            _handled_reason(attempts),
            commands,
            _settings_reason(section),
        )
        if reason is not None
    ]
    return {"level": _level(reasons, any_confirmed), "reasons": reasons}


def _level(reasons: list[dict[str, Any]], any_confirmed: bool) -> str | None:
    """``full`` when every reason held, else ``partial`` when a command was
    confirmed, else none."""
    if all(reason["held"] for reason in reasons):
        return "full"
    return "partial" if any_confirmed else None


def _reason(held: bool, text: str) -> dict[str, Any]:
    return {"held": held, "text": text}


def _some(names: list[str]) -> str:
    """The first five names, and "and more" past them."""
    return ", ".join(names[:5]) + (" and more" if len(names) > 5 else "")


def _in_words(names: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``; past five, as :func:`_some`."""
    if len(names) > 5:
        return _some(names)
    if len(names) == 1:
        return f"{names[0]}"
    return ", ".join(names[:-1]) + " and " + names[-1]


def _answered_reason(traffic: dict[str, Any]) -> dict[str, Any] | None:
    """Whether the device answered at all; nothing to say when the driver
    manages its own connection and its traffic was not captured."""
    if traffic.get("not_captured"):
        return None
    received = traffic.get("received", 0)
    return _reason(
        received > 0,
        f"The device answered ({received} received)." if received
        else f"The device sent nothing back to {traffic.get('sent', 0)} messages.",
    )


def _values_reason(last: dict[str, Any], silent: bool) -> dict[str, Any]:
    """Whether every status value was reported. last_error is written only
    when something goes wrong, so an empty one is the good case, not a value
    missing. The counts are the listen step's ("N of M reported"), so the
    two never disagree."""
    every = (last.get("status_table") or {}).get("variables") or []
    reported = sum(1 for v in every if v.get("reported"))
    aside = _error_aside(every)
    missing = _never_reported(every)
    if silent:
        return _reason(False, "None of the status values came from the device; any it shows "
                              "are the driver's own.")
    if not missing:
        return _reason(True, f"Every status value was reported ({reported} of {len(every)}"
                             f"{', ' + aside if aside else ''}).")
    return _reason(False, (
        f"{reported} of {len(every)} status values were reported. Never reported: "
        f"{_some(missing)}{' (' + aside + ')' if aside else ''}."
    ))


def _never_reported(every: list[dict[str, Any]]) -> list[str]:
    """Each status value never reported, by label, last_error aside."""
    return [
        str(v.get("label") or v.get("name")) for v in every
        if v.get("name") != "last_error" and not v.get("reported")
    ]


def _error_aside(every: list[dict[str, Any]]) -> str:
    """The words that set an unreported last_error aside, or empty."""
    error_var = next((v for v in every if v.get("name") == "last_error"), None)
    if error_var is None or error_var.get("reported"):
        return ""
    return (
        f"{error_var.get('label') or 'last_error'} aside: the driver writes it only when "
        "something goes wrong"
    )


def _handled_reason(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether anything came up the driver could not handle, on any attempt."""
    problems = sum(sum((a.get("contract") or {}).get("counts", {}).values()) for a in attempts)
    return _reason(
        problems == 0,
        "Nothing came up that the driver could not handle." if problems == 0
        else f"The driver could not handle something {problems} times (a reply that "
             "matched none of its rules, a status value it does not list).",
    )


def _commands_reason(section: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Whether every command tried was confirmed, and whether any was."""
    tried, silent_about = _tally(section)
    confirmed = [name for name, yes in tried.items() if yes]
    unconfirmed = [name for name, yes in tried.items() if not yes]
    if not tried:
        return _reason(False, "No command was tried."), False
    if not unconfirmed:
        return _reason(True, f"Every command tried was confirmed ({len(tried)})."), True
    text = (
        f"{len(confirmed)} of {len(tried)} commands tried were confirmed. Not confirmed: "
        f"{_some(unconfirmed)}. {_COUNTS_AS_CONFIRMED}"
        + _only_a_yes([name for name in unconfirmed if name in silent_about])
    )
    return _reason(False, text), bool(confirmed)


def _tally(section: dict[str, Any]) -> tuple[dict[str, bool], set[str]]:
    """Each command tried, by label: confirmed by any one of its sends (a
    send that raised confirms nothing), and the ones whose driver declares
    no value they set."""
    tried: dict[str, bool] = {}
    silent_about: set[str] = set()
    for trial in (section.get("commands") or {}).get("trials") or []:
        name = trial.get("label") or trial.get("command")
        if trial.get("error"):
            tried.setdefault(name, False)
            continue
        tried[name] = tried.get(name, False) or _command_confirmed(trial)
        if not _says_what_it_sets(trial):
            silent_about.add(name)
    return tried, silent_about


def _only_a_yes(only_yes: list[str]) -> str:
    """Why a command the driver says nothing about stayed unconfirmed: only a
    Yes could confirm it, rather than the device having failed."""
    if not only_yes:
        return ""
    one = len(only_yes) == 1
    return (
        f" This driver does not say which value {_in_words(only_yes)} "
        f"{'sets' if one else 'set'}, so only a Yes can confirm {'it' if one else 'them'}."
    )


def _settings_reason(section: dict[str, Any]) -> dict[str, Any] | None:
    """Whether every setting written was read back and put back; nothing to
    say when none was written."""
    written = (section.get("settings") or {}).get("trials") or []
    if not written:
        return None
    round_trip = [
        t for t in written
        if (t.get("write") or {}).get("confirmed") and (t.get("restore") or {}).get("confirmed")
    ]
    every = len(round_trip) == len(written)
    return _reason(
        every,
        f"Every setting written was read back and put back ({len(written)})." if every
        else f"{len(written) - len(round_trip)} of {len(written)} settings written were not "
             "both read back and put back.",
    )


def _command_confirmed(trial: dict[str, Any]) -> bool:
    """The person said the device did it, every value the command declares
    it sets read back as it should, or, for a status query, the value it
    asks for came back."""
    if (trial.get("answer") or {}).get("answer") == "yes":
        return True
    effects = trial.get("effects") or []
    if effects and all(e.get("outcome") in ("confirmed", "already") for e in effects):
        return True
    return (trial.get("query") or {}).get("outcome") == "reported"


def _says_what_it_sets(trial: dict[str, Any]) -> bool:
    """The command declares a value it sets (or, a status query, the value
    it asks for), so something besides a Yes can confirm it."""
    return bool(trial.get("effects") or trial.get("query"))


# ---------------------------------------------------------------------------
# The driver test report
# ---------------------------------------------------------------------------


def driver_test_report(report: dict[str, Any], section: dict[str, Any]) -> dict[str, Any] | None:
    """The catalog's Driver test report, filled in from this audit: ``fields``
    by the issue form's ids and ``url``, the form opened with them. None for
    a driver the catalog does not carry (imported or built in). A test report
    is public, so no field names the device's address, host name or MAC, or
    this computer's."""
    if (section.get("driver") or {}).get("source") != "catalog":
        return None
    names = _private_names(report)
    fields = {
        key: _without_addresses(value, names, patterns=key in ("worked", "didnt"))
        for key, value in _form_fields(report, section).items()
    }
    return {"url": _fitted_link(fields), "fields": fields}


def _form_fields(report: dict[str, Any], section: dict[str, Any]) -> dict[str, str]:
    """The form's fields by its ids, in its order, before any address is
    taken out."""
    driver = section.get("driver") or {}
    model = _identity_field(report, section, "model")
    lines = _result_lines(section)
    return {
        "title": f"[Test report] {driver.get('id')} on {model or 'unknown model'}",
        "driver": _driver_field(driver),
        "models": model,
        "firmware": _identity_field(report, section, "firmware"),
        "transport": _transport_field(section.get("connection") or {}),
        "worked": "\n".join(line for held, line in lines if held),
        "didnt": "\n".join(line for held, line in lines if not held),
        "notes": _notes_field(report, section),
    }


def _identity_field(report: dict[str, Any], section: dict[str, Any], key: str) -> str:
    """The model or firmware as entered with this driver, else as the device
    reported it, else empty."""
    entered = section.get("entered") or {}
    reported = (report.get("device") or {}).get("reported") or {}
    return entered.get(key) or reported.get(key) or ""


def _driver_field(driver: dict[str, Any]) -> str:
    """The driver's id and version, and the files that ran."""
    files = ", ".join(f.get("name", "") for f in driver.get("files") or [] if f.get("name"))
    return f"{driver.get('id')} {driver.get('version') or ''}".strip() + (
        f"  ({files})" if files else ""
    )


def _transport_field(connection: dict[str, Any]) -> str:
    """The transport, and the port it connected to."""
    config = connection.get("config") or {}
    port = f"port {config['port']}" if config.get("port") else ""
    return ", ".join(bit for bit in (connection.get("transport"), port) if bit)


def _notes_field(report: dict[str, Any], section: dict[str, Any]) -> str:
    """Where the report came from, and the suggested confidence: a link
    fills the form's text fields but not its dropdowns (seen on github.com
    2026-10-01), so the level rides in the notes."""
    level = (section.get("suggested_confidence") or {}).get("level")
    version = (report.get("generator") or {}).get("openavc_version")
    return (
        f"From an OpenAVC device audit (OpenAVC {version})."
        + (f" The audit suggests confidence: {level}." if level else "")
    )


def _result_lines(section: dict[str, Any]) -> list[tuple[Any, str]]:
    """Every result as ``(worked, line)``, in order: the confidence's
    reasons, the commands, the settings, the outages."""
    confidence = section.get("suggested_confidence") or {}
    return [
        *((reason["held"], reason["text"]) for reason in confidence.get("reasons") or []),
        *_command_lines(section),
        *_setting_lines(section),
        *_outage_lines(section),
    ]


def _command_lines(section: dict[str, Any]) -> list[tuple[bool, str]]:
    """A command that raised or that the person said did not (or only partly)
    happen did not work; a confirmed one did; any other says nothing."""
    from openavc.audit.command_text import trial_outcome

    lines: list[tuple[bool, str]] = []
    for trial in (section.get("commands") or {}).get("trials") or []:
        line = f"{trial.get('label')}: {trial_outcome(trial)}"
        answer = (trial.get("answer") or {}).get("answer")
        if trial.get("error") or answer in ("no", "partly"):
            lines.append((False, line + (f" ({ANSWER_WORDS[answer]})" if answer in ANSWER_WORDS else "")))
        elif _command_confirmed(trial):
            lines.append((True, line))
    return lines


def _setting_lines(section: dict[str, Any]) -> list[tuple[Any, str]]:
    """Each setting written: it worked when the write read back."""
    return [
        ((trial.get("write") or {}).get("confirmed"),
         f"Setting {trial.get('label') or trial.get('key')}: {trial.get('summary')}")
        for trial in (section.get("settings") or {}).get("trials") or []
    ]


def _outage_lines(section: dict[str, Any]) -> list[tuple[bool, str]]:
    """Each power cycle and cable pull that has a summary, under what worked."""
    return [
        (True, f"{str(test.get('kind', '')).replace('_', ' ').capitalize()}: {test['summary']}")
        for test in section.get("outages") or []
        if test.get("summary")
    ]


def _private_names(report: dict[str, Any]) -> list[str]:
    """The device's address, IP, host name and MAC, and this computer's IP."""
    target = report.get("target") or {}
    reported = (report.get("device") or {}).get("reported") or {}
    return [
        str(target.get(k) or "") for k in ("address", "ip", "hostname", "local_ip")
    ] + [str(reported.get("mac") or "")]


def _without_addresses(text: str, names: list[str], *, patterns: bool) -> str:
    """``text`` with the device's addresses and names taken out (a test
    report is public); with ``patterns``, any other address too (not in a
    field where a version number could read as one)."""
    if patterns:
        for pattern in _ADDRESS_PATTERNS:
            text = pattern.sub(NO_ADDRESS, text)
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        text = text.replace(name, NO_ADDRESS)
    return text


def _link(values: dict[str, str]) -> str:
    """The form opened with every non-empty field filled in."""
    return TEST_REPORT_FORM + "".join(
        f"&{key}={quote(value, safe='')}" for key, value in values.items() if value
    )


def _fitted_link(fields: dict[str, str]) -> str:
    """The link, its last lines of what worked and then of what did not cut
    until it fits, the cut said."""
    sent = dict(fields)
    for key in ("worked", "didnt"):
        lines = sent[key].split("\n") if sent[key] else []
        while lines and len(_link(sent)) > TEST_REPORT_URL_MAX:
            lines.pop()
            sent[key] = "\n".join(lines + [_MORE_IN_REPORT])
    return _link(sent)
