"""The device audit report: one zip a person can email.

::

    openavc-device-audit-<manufacturer>-<model>-<YYYYMMDD-HHMM>.zip
      summary.html     readable summary, self-contained, no scripts
      report.json      the complete record
      timeline.txt     every event in order, the driver's traffic included
      driver/<files>   the exact driver file(s) that ran
      log.txt          OpenAVC's own log lines about the audit, INFO and up
                       (``session.SessionLog``)

``report.json`` (``report_version`` 1) holds:

- ``report_version``; ``generator``: ``openavc_version``, ``os``,
  ``python`` and ``deployment_type`` (how OpenAVC was installed).
- ``session``: ``id``, ``started_at`` and ``ended_at``, ``status``, the
  ``steps`` run, the project device whose page started the audit (``origin``:
  id, name and driver, or null), ``no_driver`` (the person said no driver
  exists yet), and the ``tester``'s name, company, email and notes (all
  optional).
- ``target``: the ``address`` as typed, the ``ip`` it resolved to, the reverse
  DNS ``hostname``, ``same_subnet`` (on one of this computer's subnets), the
  ``local_ip`` and ``interface`` the check used, and ``serial_port`` (none
  yet).
- ``device``: manufacturer, model and firmware as the person entered them on
  "Which driver?" (``entered``, null where left empty), and the identity the
  device reported to the network check (``reported``: ``manufacturer``,
  ``model``, ``model_number``, ``firmware``, ``serial_number``,
  ``device_name``, ``hostname``, ``mac``). An SSDP description's modelName is
  ``model`` and its modelNumber ``model_number``, never joined.
- ``catalog``: where the driver catalog came from, when it was fetched
  (``fetched_at``, ``last_attempt``), the ``sha256`` of its ``index.json``, its
  ``driver_count``, whether it was ``reachable``, and ``used``: ``fresh``,
  ``cached`` (from an earlier fetch) or ``none``.
- ``footprint``: every raw observation of the network check. Bytes appear as
  ``{"hex", "text"}`` with the text decoded latin-1, so every byte survives.
  A ``hex`` field is the bytes unspaced (``aa0b01``); every sentence, in
  ``report.json``, ``summary.html`` and ``timeline.txt`` alike, writes bytes
  ``hex aa 0b 01`` (``hex_pairs``), as the wizard shows them.
  Each web page carries ``tries``: 2 when it did not answer the first GET
  and was asked once more (the exchange kept is the second).
- ``evidence``: the discovery Evidence records built from those observations.
- ``verdict``: the matcher's identification, every driver each signal points
  at, and each named driver's declared signals judged against the device.
- ``drivers``: one entry per driver chosen, in order:

  - ``run``, ``started_at``, ``finished_at``.
  - ``driver``: exactly which driver: id, name, manufacturer, version, format
    (``avcdriver`` or ``python``), transport, source (``catalog``,
    ``imported``, ``built_in``), and ``files``, each with its SHA-256, the
    catalog's (``catalog_sha256``, ``matches_catalog``), where the zip holds
    it (``in_report``, under ``driver/``) and whether redaction changed that
    copy (``redacted_in_report``); ``modified`` is true when a file differs
    from the catalog's; ``catalog`` says whether and at what version the
    catalog lists it, and whether it is ``verified``; ``catalog_files_missing``
    names any file the catalog lists that the installed copy lacks.
  - ``entered`` (the make, model and firmware given with this choice),
    ``model_listing`` (``listed``: whether the driver lists that model, null
    when none was typed; ``confidence``), ``verdict_agreement`` (``agrees``,
    ``candidate``, ``differs``, ``no_verdict``).
  - ``connection``: ``config`` with every credential shown as ``***``,
    ``transport``, ``saved_from`` (the project device whose saved settings
    were used, or empty) and ``preview`` (what connecting was shown to send:
    each ``steps`` entry with its ``stage``, and the ``keep_alive_interval``).
  - ``attempts``: every connect and listen, in order: ``status``
    (``listening``, ``not_connected``, ``done``, ``failed``, ``stopped``),
    ``active``, ``error``, the times (``started_at``, ``first_tx_at``,
    ``first_rx_at``, ``connected_at``, ``ends_at``, ``max_ends_at`` (how far
    "Keep listening" can take it), ``finished_at``), ``poll_interval``,
    ``reconnects``, ``drops``, ``offline`` (``code``, ``detail``,
    ``next_step``), ``declared`` and ``reported`` counts, ``status_table``
    (every declared value with ``value``, ``reported``,
    ``first_reported_at``, ``problem`` and ``sources``, the response rules
    that would set it; ``children``; ``child_labels``, each child type's ``one``
    and ``many`` label; ``settings``, each with ``populated``),
    ``contract`` (``counts``
    by kind and the ``events`` kept, the first 50 of each kind),
    ``unprompted_replies`` (a hint: the ``seq`` of each reply with no request
    in the ``window_seconds`` before it), ``state_changes`` (``t``, ``key``,
    ``old``, ``new``), ``front_panel`` (``answer``, ``note``, ``changes``),
    ``push_callbacks`` (each URL the driver asked the device to send its events
    to, through OpenAVC's HTTP listener) and ``traffic``: ``count``, ``sent``, ``received``, ``bytes``,
    ``truncated_at``, ``dropped_entries``, ``not_captured`` (values changed
    while no traffic was recorded: a driver that manages its own connection)
    and ``entries``, each ``{"seq", "t", "direction", "channel", "hex",
    "text", "chunk"?, "meta"?}``, ``chunk`` marking a raw receive chunk
    before framing and ``meta`` what the bytes do not say (an HTTP method,
    target, status and headers; a peer; a topic).
  - ``commands`` (null when the driver never connected): ``catalog``, the
    driver's commands as it declared them while connected (``name``,
    ``label``, ``help``, ``params``, ``query`` with ``query_for`` and
    ``polled``, ``sets``, ``available_offline``, ``restarts_device_for``,
    ``needs_input``, ``confirm``, ``suggested``); ``batch``, the status queries run together
    (``total``, ``sent``, ``skipped``); and ``trials``, every command sent,
    in order: ``number``, ``command``, ``label``, ``params`` (secret ones
    ``***``), ``attempt`` (the how-manyth time this command was sent),
    ``batch``, ``connect_attempt`` (the index into ``attempts`` whose
    connection carried it), the times (``sent_at``, ``returned_at``,
    ``ends_at``, ``finished_at``), ``result`` (what the driver's
    ``send_command`` returned), ``error`` and ``error_type`` (what it
    raised, in words and by class) and ``traffic`` (``sent``,
    ``received``, and every entry from the send to the end of its window,
    in the ``attempts`` traffic form); ``changed``, every status value that
    read differently when the report was taken from before the first command
    or setting (``key``, ``label``, ``before``, ``now``, ``by``, the command
    whose window saw it move: ``number`` and ``label``, or null, and
    ``on_its_own``, true when it changed while nothing was being watched
    after that command: a meter, a clock, a change at the device). Each trial
    also keeps what its window showed:
    ``since_previous`` (the command sent before it and how many seconds
    before), ``extended`` (seconds "Wait longer" added), ``stopped_early``
    ("Stop watching"), ``changes`` (every status value that moved:
    ``t``, ``key``, ``old``, ``new``), ``already_moving`` (the values that
    were already changing when it was sent), ``device_errors`` (errors the driver
    published for the device), ``effects`` (each declared ``sets`` entry:
    ``state``, ``state_key``, ``label``, ``expected``, ``has_value``, ``value`` and
    ``outcome``, one of ``confirmed``, ``already``, ``different``,
    ``unchanged``, ``not_reported``, ``no_value``), ``query`` (a status
    query's ``state``, ``state_key``, ``label``, ``value``, ``changed`` and
    ``outcome``: ``reported``, ``not_reported`` or ``no_reply``),
    ``refusals`` (``device_errors``, ``last_error``, ``last_error_writes``,
    ``unmatched`` and ``unmatched_examples``), ``sent_nothing`` (true when
    the driver returned success while nothing left for the device; null when
    its traffic is not captured at all), ``restart``, for a command that
    declares ``restarts_device_for`` (``declared_seconds``,
    ``went_away_after``, ``back_after`` and ``away_for`` in seconds, and
    ``within_declared``), and ``drops``, every time the connection dropped
    inside the window of any other command, in order (``after`` the send and
    ``back_after`` the drop, in seconds, null when it had not come back;
    empty when it stayed up). ``moved`` lists
    each value the window saw change once, ``went_back`` true when it ended
    where it began; ``summary`` says it in a sentence. ``answer`` is
    the person's own: ``{"answer", "note", "at"}``, the answer ``yes``,
    ``no``, ``partly`` or ``cant_tell`` ("Did it happen?"), or null
    when they did not say.
  - ``settings`` (null when the driver never connected): ``catalog``, each
    device setting the driver declares (``key``, ``label``, ``help``,
    ``definition``, ``state_key``, ``value`` as last read, ``can_write`` and
    the ``reason`` it cannot be tested: its value cannot be read back, so it
    could not be put back), and ``trials``, each setting written: ``number``,
    ``key``, ``label``, ``original``, ``value``, ``started_at``, ``status``,
    ``write`` and ``restore`` (each ``{"at", "error", "confirmed", "value",
    "after"}``, the value read back and how many seconds after the write;
    ``sent`` when a call that failed had already sent bytes, so the setting
    was put back anyway; ``interrupted`` when the audit ended before the
    read-back did; ``restore.automatic`` when the audit put it back as the
    driver stopped) and ``summary``.
  - ``suggested_confidence``: ``level`` (``full``, ``partial`` or null) and
    the ``reasons`` it rests on, each ``{"held", "text"}`` (the rule is
    ``suggested_confidence``'s docstring).
  - ``test_report``: the catalog's Driver test report form filled in from
    this run (``fields`` by the form's ids, and the ``url`` that opens it),
    or null for a driver the catalog does not carry.
  - ``outages``: each power cycle and cable pull, in order: ``number``,
    ``kind`` (``power_cycle``, ``cable_pull``), ``status`` (``done`` or
    ``stopped``, with ``end_reason`` and ``end_code``: ``person`` when Stop
    was pressed, ``ceiling`` when the 15-minute limit stopped it),
    ``connect_attempt``; the clocks, each a time or null: ``started_at``, the
    person's marks ``off_at`` and ``on_at``, the device's own answers
    ``unreachable_at`` and ``reachable_at`` (from a ping once a second, when
    ``ping.used``; ``ping.why`` says why not), ``noticed_at`` (the driver's
    disconnect), ``reconnected_at``, ``dropped_again_at`` and
    ``reconnected_again_at`` (the connection dropping again after the
    reconnect, as a device still booting can do, and the reconnect that
    held), ``not_noticed_at`` (the ``notice_ceiling_seconds`` passed first),
    ``ends_at``, ``finished_at``; ``watch`` (``liveness_probe``,
    ``probe_every``, ``notice_within``: the longest its probe can take to
    notice, ``poll_interval``: how the driver notices a device that went
    quiet); ``reason`` (the first offline reason after it noticed: ``code``,
    ``detail``) and every one in ``reasons``; ``measured``, in seconds
    (``noticed_after``, negative when OpenAVC noticed before the device
    stopped answering ping; ``away_for``; ``answered_after_on``;
    ``reconnected_after_back``; ``dropped_again_after``,
    ``reconnected_again_after``); ``before`` (the status values that had a
    value going in) and ``repopulated`` (``reported_again``,
    ``not_reported_again``: written after the reconnect, changed or not);
    ``announcements`` (what the device announced during the test:
    ``t``, ``protocol``, ``detail``); ``summary`` says it in a sentence.

- ``timeline``: every session event in order, typed and timestamped.
- ``limits``: what the audit could not see, and why.
- ``complete``: false when the report was taken before the network check
  finished.

**Redaction.** Every secret the person typed (a read community other than
``public``, a driver credential) and every credential the driver's config
registered is replaced in the record, string by string, in its plain, JSON,
HTML and hex forms (unspaced and in pairs), before anything is written; a
serial number the person asked to leave out is replaced the same way. The
record is redacted as a tree and never as JSON text, so a number, a ``true``
or a key the secret happens to equal is left alone and ``report.json``
always parses; ``summary.html`` and ``timeline.txt`` are drawn from the
redacted record, and ``log.txt`` (the session's own log lines, not in the
record) is the one file redacted as text. Traffic is masked (``***``) as it
is written out, so the hex of a masked entry still decodes. The report never
reads the server's own configuration.

What is already public is left as it is: a driver file whose bytes match the
catalog's is copied unredacted, and a string that is exactly one of the
names the catalog and the drivers that ran publish (a driver's id, name and
manufacturer, and every string a published driver declares: its labels,
defaults, command and value names) is never changed, and the catalog's names
are left as they are inside a longer string too ("Driver chosen: ..."). Otherwise a password
that is the driver's published default, or a word a driver uses, would be
cut out of the driver's own text and say what it was. The wizard tells the
person before the download when a credential they typed is the driver's
published default (``DriverRun.published_secrets``); the report says nothing
of it. A driver file that does not match the catalog (imported, or changed
here) is redacted, and its entry says whether that changed it.

A secret shorter than ``observe.MIN_SECRET_LENGTH`` is not searched for,
since it would match ordinary text everywhere; the report never writes a
typed secret into a field of its own, so such a value could only appear if
the device repeated it. A secret made only of hex digits (a numeric PIN,
say) is replaced in its plain forms only where it stands alone, never inside
a longer run of hex digits or after a decimal point: otherwise a PIN would
be cut out of a timestamp, a SHA-256 or a hex dump. Its hex forms are
replaced wherever they appear, since that is where its own bytes show in a
hex dump.

**Recent reports** are kept in ``{data_dir}/audit_reports/``, the newest
``REPORTS_KEPT``, so closing a browser tab does not lose one.
"""

from __future__ import annotations

import html
import io
import json
import logging
import platform
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openavc.audit.session import AuditSession

log = logging.getLogger("audit.report")

REPORT_VERSION = 1
REPORTS_KEPT = 10
REDACTED = "[redacted]"
SERIAL_REMOVED = "[serial number removed]"
_NAME_RE = re.compile(r"^openavc-device-audit-[a-z0-9-]+\.zip$")


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Redaction:
    """One value to take out of a report, and what to put in its place."""

    value: str
    replacement: str = REDACTED


_HEX_DIGITS = re.compile(r"[0-9A-Fa-f]+")


def _forms(redaction: Redaction) -> list[tuple[str, str, bool]]:
    """Each way ``value`` can be written in a report, with its replacement,
    and whether it is replaced only where it stands alone (see the module
    docstring: a hex-digit secret's plain forms)."""
    value, repl = redaction.value, redaction.replacement
    alone = bool(_HEX_DIGITS.fullmatch(value))
    triples = [
        (value, repl, alone),
        (json.dumps(value)[1:-1], json.dumps(repl)[1:-1], alone),
        (html.escape(value), html.escape(repl), alone),
        (value.encode("utf-8").hex(), repl.encode("utf-8").hex(), False),
        (value.encode("utf-8").hex().upper(), repl.encode("utf-8").hex().upper(), False),
        # The spaced form a sentence writes bytes in (``hex_pairs``).
        (value.encode("utf-8").hex(" "), repl.encode("utf-8").hex(" "), False),
        (value.encode("utf-8").hex(" ").upper(), repl.encode("utf-8").hex(" ").upper(), False),
    ]
    seen: dict[str, tuple[str, bool]] = {}
    for form, replacement, bounded in triples:
        if form and form not in seen:
            seen[form] = (replacement, bounded)
    return sorted(
        ((form, replacement, bounded) for form, (replacement, bounded) in seen.items()),
        key=lambda triple: -len(triple[0]),
    )


class Redactor:
    """Replaces each secret in a text, whatever form it was written in.

    ``public``: strings published already (the catalog's and the drivers'
    own), left as they are wherever a whole string is one of them. ``names``:
    the catalog's names (driver ids, names, manufacturers, file names), left
    as they are inside a longer string too.
    """

    def __init__(
        self, redactions: list[Redaction], public: Any = (), names: Any = (),
    ) -> None:
        from openavc.audit.observe import MIN_SECRET_LENGTH

        self._public = frozenset(public) | frozenset(names)
        self._shield: re.Pattern[str] | None = None
        self._pairs: list[tuple[str, str, re.Pattern[str] | None]] = []
        for redaction in redactions:
            if len(redaction.value) < MIN_SECRET_LENGTH:
                continue
            for form, replacement, bounded in _forms(redaction):
                pattern = (
                    re.compile(rf"(?<![0-9A-Fa-f.]){re.escape(form)}(?![0-9A-Fa-f])")
                    if bounded else None
                )
                self._pairs.append((form, replacement, pattern))
        self._pairs.sort(key=lambda triple: -len(triple[0]))
        # Only a name a secret would cut into needs shielding.
        shielded = sorted(
            {n for n in names if n and any(form in n for form, _, _ in self._pairs)},
            key=len, reverse=True,
        )
        if shielded:
            self._shield = re.compile("(" + "|".join(re.escape(n) for n in shielded) + ")")

    def text(self, value: str) -> str:
        if self._shield is not None and self._shield.search(value):
            parts = self._shield.split(value)
            # Odd parts are the names the split kept.
            return "".join(
                part if i % 2 else self._replace(part) for i, part in enumerate(parts)
            )
        return self._replace(value)

    def _replace(self, value: str) -> str:
        for form, replacement, pattern in self._pairs:
            if form in value:
                value = (
                    pattern.sub(lambda _m, r=replacement: r, value) if pattern is not None
                    else value.replace(form, replacement)
                )
        return value

    def tree(self, value: Any) -> Any:
        """``value`` with every string in it redacted (dict keys included),
        except a string that is exactly a published one."""
        if isinstance(value, str):
            return value if value in self._public else self.text(value)
        if isinstance(value, dict):
            return {self.tree(k) if isinstance(k, str) else k: self.tree(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.tree(v) for v in value]
        return value


def redactions_for(session: "AuditSession") -> list[Redaction]:
    """What this session's report must not contain."""
    out = [
        Redaction(c) for c in session.options.snmp_communities
        if c and c != "public"
    ]
    tester = getattr(session, "tester", None) or {}
    footprint = _footprint_of(session)
    serial = footprint.device.serial_number if footprint and footprint.device else None
    if tester.get("leave_out_serial") and serial:
        out.append(Redaction(str(serial), SERIAL_REMOVED))
    # A driver run's credentials: what the person typed, and what the
    # driver registered as secret while it ran.
    values: set[str] = set()
    for run in getattr(session, "runs", None) or []:
        values |= set(getattr(run, "secrets", None) or ())
        for attempt in getattr(run, "listens", None) or []:
            values |= attempt.sandbox.observer.secrets
    out.extend(Redaction(v) for v in sorted(values) if isinstance(v, str) and v)
    return out


def _strings_in(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                out.add(key)
            _strings_in(item, out)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _strings_in(item, out)


def _catalog_names(value: Any, out: set[str]) -> None:
    """The driver ids, names and manufacturers anything in ``value`` names."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("driver_id", "driver_name", "manufacturer") and isinstance(item, str):
                out.add(item)
            elif key == "drivers" and isinstance(item, list):
                out.update(d for d in item if isinstance(d, str))
            _catalog_names(item, out)
    elif isinstance(value, list):
        for item in value:
            _catalog_names(item, out)


def catalog_names(session: "AuditSession") -> set[str]:
    """The names the catalog publishes for the drivers this audit met: each
    tested driver's id, name, manufacturer and file names (and where the zip
    puts them), and every driver the verdict names."""
    out: set[str] = set()
    for run in getattr(session, "runs", None) or []:
        identity = run.choice.identity or {}
        for key in ("id", "name", "manufacturer"):
            if isinstance(identity.get(key), str):
                out.add(identity[key])
        for f in identity.get("files") or []:
            name = f.get("name")
            if isinstance(name, str) and name:
                out |= {name, f"driver/{name}", f"driver/run-{run.index + 1}/{name}"}
    footprint = _footprint_of(session)
    if footprint is not None:
        verdict = (footprint.to_dict() or {}).get("verdict") or {}
        _catalog_names({k: v for k, v in verdict.items() if k != "catalog"}, out)
    out.discard("")
    return out


def public_strings(session: "AuditSession") -> set[str]:
    """Every string a published driver that ran declares (see the module
    docstring): never redacted where a whole string is one of them."""
    from openavc.drivers.registry import get_driver_class

    out: set[str] = set()
    for run in getattr(session, "runs", None) or []:
        files = (run.choice.identity or {}).get("files") or []
        if files and all(f.get("matches_catalog") is True for f in files):
            cls = get_driver_class(run.choice.driver_id)
            _strings_in(getattr(cls, "DRIVER_INFO", None) or {}, out)
    out.discard("")
    return out


def session_redactor(session: "AuditSession") -> "Redactor":
    """The redactor for ``session``'s report."""
    return Redactor(redactions_for(session), public_strings(session), catalog_names(session))


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def _footprint_of(session: "AuditSession"):
    """The finished footprint, or the one in progress, or None before the
    check has started (its listeners run from the session's start, but there
    is nothing to report until the check does)."""
    if session.footprint is not None:
        return session.footprint
    check = getattr(session, "check", None)
    if check is None or getattr(check, "status", "idle") == "idle":
        return None
    return check.footprint


@dataclass(frozen=True)
class PlacedFile:
    """One driver file as the zip holds it."""

    run: int
    name: str
    path: str
    data: bytes
    redacted: bool


def _redact_file(raw: bytes, redactor: "Redactor") -> tuple[bytes, bool]:
    """A driver file the catalog does not publish, redacted as text."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw, False
    cleaned = redactor.text(text)
    return (cleaned.encode("utf-8"), True) if cleaned != text else (raw, False)


def place_driver_files(session: "AuditSession", redactor: "Redactor") -> list[PlacedFile]:
    """Where each driver that ran puts its files in the zip.

    ``driver/<name>``; a second copy of a name with different bytes (the
    driver updated between two runs) goes under ``driver/run-<n>/``. A driver
    chosen but never connected did not run, so its files are not included
    (its hashes are still in its section).
    """
    placed: list[PlacedFile] = []
    used: dict[str, bytes] = {}
    for run in getattr(session, "runs", None) or []:
        if not getattr(run, "listens", None):
            continue
        published = {
            f.get("name") for f in (getattr(run.choice, "identity", None) or {}).get("files") or []
            if f.get("matches_catalog") is True
        }
        for name, raw in sorted(run.choice.file_contents.items()):
            # The catalog's own bytes are public: never changed.
            data, redacted = (raw, False) if name in published else _redact_file(raw, redactor)
            path = f"driver/{name}"
            if path in used and used[path] != data:
                path = f"driver/run-{run.index + 1}/{name}"
            used.setdefault(path, data)
            placed.append(PlacedFile(run.index, name, path, data, redacted))
    return placed


def _reported(device: dict[str, Any], ssdp: dict[str, Any]) -> dict[str, Any]:
    """The identity the network check heard, each part as the device gave it.

    ``model_number`` is the SSDP description's modelNumber, kept apart from
    ``model`` (its modelName, which is what a driver's SSDP ``model:`` filter
    reads); null when the description gave none or the model already holds it.
    """
    out = {
        key: device.get(key)
        for key in ("manufacturer", "model", "firmware", "serial_number",
                    "device_name", "hostname", "mac")
    }
    number = ssdp.get("model_number")
    model = str(out.get("model") or "")
    out["model_number"] = number if number and number not in model else None
    return out


def _entered(values: dict[str, Any]) -> dict[str, Any]:
    return {key: (values.get(key) or None) for key in ("manufacturer", "model", "firmware")}


def _driver_section(run: Any, placed: list[PlacedFile]) -> dict[str, Any]:
    choice = run.choice.to_dict()
    identity = dict(choice["identity"])
    where = {p.name: p for p in placed if p.run == run.index}
    identity["files"] = [
        {
            **f,
            "in_report": where[f["name"]].path if f["name"] in where else None,
            "redacted_in_report": where[f["name"]].redacted if f["name"] in where else False,
        }
        for f in identity.get("files", [])
    ]
    return {
        "run": run.index,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "driver": identity,
        "entered": _entered(choice),
        "model_listing": choice["model_listing"],
        "verdict_agreement": choice["verdict_agreement"],
        "connection": json.loads(json.dumps(run.connection, default=str))
        if run.connection else None,
        "attempts": [attempt.report_record() for attempt in run.listens],
        "commands": run.commands.report_record() if getattr(run, "commands", None) else None,
        "settings": run.settings.report_record() if getattr(run, "settings", None) else None,
        "outages": [test.record() for test in getattr(run, "outages", None) or []],
    }


def _driver_limits(session: "AuditSession") -> list[dict[str, Any]]:
    """What the driver test could not see, per run."""
    from openavc.audit.observe import EVENT_DETAIL_KEPT, TRAFFIC_CAP_BYTES

    runs = list(getattr(session, "runs", None) or [])
    limits: list[dict[str, Any]] = []
    if not any(run.listens for run in runs):
        text = (
            "The person said there is no driver for this device yet, so none was tested; "
            "this report covers the network check only."
            if getattr(session, "no_driver", False)
            else "No driver was tested; this report covers the network check only."
        )
        limits.append({"id": "no_driver_tested", "text": text})
    cap_mb = TRAFFIC_CAP_BYTES // (1024 * 1024)
    for run in runs:
        name = run.choice.identity.get("name") or run.choice.driver_id
        if not run.listens:
            limits.append({
                "id": "driver_not_connected", "run": run.index,
                "text": f"{name} was chosen, but the audit did not connect it.",
            })
            continue
        views = [attempt.to_dict()["traffic"] for attempt in run.listens]
        if any(v["not_captured"] for v in views):
            limits.append({
                "id": "traffic_not_captured", "run": run.index,
                "text": f"{name} manages its own connection, so its traffic was not captured.",
            })
        for test in getattr(run, "outages", None) or []:
            if not test.ping.get("used"):
                limits.append({
                    "id": "outage_by_marks", "run": run.index,
                    "text": f"The device does not answer ping, so the times in {name}'s "
                            f"{test.to_dict()['kind'].replace('_', ' ')} test come from the "
                            "buttons pressed during it.",
                })
                break
        if any(attempt.sandbox.observer.truncated_at is not None for attempt in run.listens):
            limits.append({
                "id": "traffic_truncated", "run": run.index,
                "text": f"{name}'s traffic passed {cap_mb} MB, so the report keeps the "
                        f"first {cap_mb} MB of it.",
            })
        for attempt in run.listens:
            urls = attempt.sandbox.push_callbacks()
            heard = any(
                e.channel == "http_listener" and e.direction == "rx"
                for e in attempt.sandbox.observer.traffic
            )
            if urls and not heard:
                limits.append({
                    "id": "push_never_arrived", "run": run.index, "callbacks": urls,
                    "text": _push_never_arrived(name, urls),
                })
                break
        if any(
            count > EVENT_DETAIL_KEPT
            for attempt in run.listens
            for count in attempt.sandbox.observer.event_counts.values()
        ):
            limits.append({
                "id": "events_capped", "run": run.index,
                "text": f"Where {name} did the same thing wrong more than {EVENT_DETAIL_KEPT} "
                        "times, the report keeps the first "
                        f"{EVENT_DETAIL_KEPT} in full and counts the rest.",
            })
    log_lines = getattr(session, "log", None)
    if log_lines is not None and log_lines.dropped:
        limits.append({
            "id": "log_capped",
            "text": f"OpenAVC wrote more than {log_lines.keep} log lines about this audit; "
                    f"log.txt keeps the first {log_lines.keep}.",
        })
    return limits


def _push_never_arrived(name: str, urls: list[str]) -> str:
    """The limit for a driver that asked the device to send its events to
    OpenAVC and heard none: where it asked, and the likeliest reason."""
    from openavc import config

    where = urls[0] if len(urls) == 1 else f"{urls[0]} and {len(urls) - 1} more"
    text = (
        f"{name} asked the device to send its events to {where}, and none arrived while "
        "the audit ran, so the driver saw only what it asked for itself."
    )
    if config.loopback_only():
        text += (
            f" OpenAVC is listening on {config.BIND_ADDRESS} only, so the device cannot "
            "reach it: set the Bind address in Settings > Network to 0.0.0.0, restart, "
            "and run the audit again."
        )
    else:
        text += (
            " A firewall on this computer, or between it and the device, may be blocking "
            "the device's connection."
        )
    return text


def generator_info() -> dict[str, Any]:
    from openavc.version import __version__

    try:
        from openavc.updater.platform import detect_deployment_type

        deployment = detect_deployment_type()
        deployment = getattr(deployment, "value", str(deployment))
    except Exception:
        deployment = "unknown"
    return {
        "openavc_version": __version__,
        "os": platform.platform(),
        "python": platform.python_version(),
        "deployment_type": deployment,
    }


def build_report(session: "AuditSession") -> dict[str, Any]:
    """The whole record for ``session``, redacted."""
    redactor = session_redactor(session)
    placed = place_driver_files(session, redactor)
    footprint = _footprint_of(session)
    fp = footprint.to_dict() if footprint is not None else {}
    complete = session.footprint is not None
    device = fp.get("device") or {}
    tester = dict(getattr(session, "tester", None) or {})
    tester.pop("leave_out_serial", None)

    limits = list(fp.get("limits", []))
    if footprint is not None and not complete:
        limits.insert(0, {
            "id": "check_unfinished",
            "text": "The network check had not finished when this report was taken.",
        })
    if footprint is None:
        limits.insert(0, {"id": "check_not_run", "text": "The network check did not run."})
    limits.extend(_driver_limits(session))

    network = fp.get("network") or {}
    names = fp.get("names") or {}
    raw_footprint = {
        key: value for key, value in fp.items()
        if key not in ("evidence", "verdict", "device", "limits")
    }
    report = {
        "report_version": REPORT_VERSION,
        "generator": generator_info(),
        "complete": complete,
        "session": {
            "id": session.id,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "status": session.status,
            "steps": list(session.steps),
            "origin": dict(session.origin) if getattr(session, "origin", None) else None,
            "no_driver": bool(getattr(session, "no_driver", False)),
            "tester": tester,
        },
        "target": {
            "address": session.target.address,
            "ip": session.target.ip,
            "hostname": names.get("reverse_dns"),
            "same_subnet": network.get("same_subnet"),
            "local_ip": network.get("local_ip"),
            "interface": network.get("interface"),
            "serial_port": None,
        },
        "device": {
            "entered": _entered(getattr(session, "device_entered", None) or {}),
            "reported": _reported(device, fp.get("ssdp") or {}),
        },
        "catalog": (fp.get("verdict") or {}).get("catalog", {}),
        "footprint": raw_footprint,
        "evidence": fp.get("evidence", []),
        "verdict": {k: v for k, v in (fp.get("verdict") or {}).items() if k != "catalog"},
        "drivers": [
            _driver_section(run, placed) for run in getattr(session, "runs", None) or []
        ],
        "timeline": [entry.to_dict() for entry in session.timeline],
        "limits": limits,
    }
    report = redactor.tree(report)
    # Read off the redacted record, so neither can carry a secret.
    for section in report["drivers"]:
        section["suggested_confidence"] = suggested_confidence(section)
        section["test_report"] = driver_test_report(report, section)
    return report


# ---------------------------------------------------------------------------
# Suggested confidence, and the driver test report
# ---------------------------------------------------------------------------

# The catalog's issue form for a driver test report, and the most a link may
# carry (GitHub answers "URI Too Long" well before a browser gives up).
TEST_REPORT_FORM = (
    "https://github.com/open-avc/openavc-drivers/issues/new?template=driver-test-report.yml"
)
TEST_REPORT_URL_MAX = 6000


def _command_confirmed(trial: dict[str, Any]) -> bool:
    """The person said the device did it, or every value the command
    declares it sets read back as it should."""
    if (trial.get("answer") or {}).get("answer") == "yes":
        return True
    effects = trial.get("effects") or []
    return bool(effects) and all(e.get("outcome") in ("confirmed", "already") for e in effects)


def suggested_confidence(section: dict[str, Any]) -> dict[str, Any]:
    """A suggestion for the catalog's per-model confidence, with every
    condition it rests on: ``level`` ``full`` (connected, every status value
    reported, nothing the driver could not handle, every command tried
    confirmed, every setting written read back and put back), ``partial``
    (connected and at least one command confirmed) or null; ``reasons``, each
    ``{"held", "text"}``. A command is confirmed when the person said the
    device did it, or every value it declares it sets read back."""
    attempts = section.get("attempts") or []
    connected = [a for a in attempts if a.get("connected_at")]
    reasons: list[dict[str, Any]] = []

    def say(held: bool, text: str) -> bool:
        reasons.append({"held": held, "text": text})
        return held

    ok = say(bool(connected), "Connected." if connected else "Did not connect.")
    if not connected:
        return {"level": None, "reasons": reasons}
    last = connected[-1]
    traffic = last.get("traffic") or {}
    silent = not traffic.get("not_captured") and not traffic.get("received")
    if not traffic.get("not_captured"):
        received = traffic.get("received", 0)
        ok &= say(
            received > 0,
            f"The device answered ({received} received)." if received
            else f"The device sent nothing back to {traffic.get('sent', 0)} messages.",
        )
    # last_error is written only when something goes wrong, so an empty one
    # is the good case, not a value missing.
    values = [
        v for v in (last.get("status_table") or {}).get("variables") or []
        if v.get("name") != "last_error"
    ]
    missing = [str(v.get("label") or v.get("name")) for v in values if not v.get("reported")]
    if silent:
        ok &= say(False, "None of the status values came from the device; any it shows are "
                         "the driver's own.")
    else:
        ok &= say(
            not missing,
            f"Every status value was reported ({len(values)})." if not missing
            else f"{len(missing)} of {len(values)} status values were never reported: "
                 f"{', '.join(missing[:5])}{' and more' if len(missing) > 5 else ''}.",
        )
    problems = sum(sum((a.get("contract") or {}).get("counts", {}).values()) for a in attempts)
    ok &= say(
        problems == 0,
        "Nothing came up that the driver could not handle." if problems == 0
        else f"The driver could not handle something {problems} times (a reply that "
             "matched none of its rules, a status value it does not list).",
    )
    trials = (section.get("commands") or {}).get("trials") or []
    tried: dict[str, bool] = {}
    for trial in trials:
        if trial.get("error"):
            tried.setdefault(trial.get("label") or trial.get("command"), False)
            continue
        name = trial.get("label") or trial.get("command")
        tried[name] = tried.get(name, False) or _command_confirmed(trial)
    confirmed = [name for name, yes in tried.items() if yes]
    unconfirmed = [name for name, yes in tried.items() if not yes]
    if not tried:
        ok &= say(False, "No command was tried.")
    elif unconfirmed:
        ok &= say(False, (
            f"{len(confirmed)} of {len(tried)} commands tried were confirmed. Not confirmed: "
            f"{', '.join(unconfirmed[:5])}{' and more' if len(unconfirmed) > 5 else ''}. "
            "A command counts as confirmed when the person answered Yes, or the values it "
            "should set read back."
        ))
    else:
        say(True, f"Every command tried was confirmed ({len(tried)}).")
    written = (section.get("settings") or {}).get("trials") or []
    if written:
        round_trip = [
            t for t in written
            if (t.get("write") or {}).get("confirmed") and (t.get("restore") or {}).get("confirmed")
        ]
        ok &= say(
            len(round_trip) == len(written),
            f"Every setting written was read back and put back ({len(written)})."
            if len(round_trip) == len(written)
            else f"{len(written) - len(round_trip)} of {len(written)} settings written were not "
                 "both read back and put back.",
        )
    level = "full" if ok else ("partial" if confirmed else None)
    return {"level": level, "reasons": reasons}


def driver_test_report(report: dict[str, Any], section: dict[str, Any]) -> dict[str, Any] | None:
    """The catalog's Driver test report, filled in from this audit: ``fields``
    by the issue form's ids and ``url``, the form opened with them. None for
    a driver the catalog does not carry (imported or built in)."""
    from urllib.parse import quote

    driver = section.get("driver") or {}
    if driver.get("source") != "catalog":
        return None
    entered = section.get("entered") or {}
    reported = (report.get("device") or {}).get("reported") or {}
    connection = section.get("connection") or {}
    config = connection.get("config") or {}
    files = ", ".join(f.get("name", "") for f in driver.get("files") or [] if f.get("name"))

    worked: list[str] = []
    didnt: list[str] = []
    for reason in (section.get("suggested_confidence") or {}).get("reasons") or []:
        (worked if reason["held"] else didnt).append(reason["text"])
    for trial in (section.get("commands") or {}).get("trials") or []:
        line = f"{trial.get('label')}: {_trial_outcome(trial)}"
        answer = (trial.get("answer") or {}).get("answer")
        if trial.get("error") or answer in ("no", "partly"):
            didnt.append(line + (f" ({ANSWER_WORDS[answer]})" if answer in ANSWER_WORDS else ""))
        elif _command_confirmed(trial):
            worked.append(line)
    for trial in (section.get("settings") or {}).get("trials") or []:
        (worked if (trial.get("write") or {}).get("confirmed") else didnt).append(
            f"Setting {trial.get('label') or trial.get('key')}: {trial.get('summary')}"
        )
    for test in section.get("outages") or []:
        if test.get("summary"):
            worked.append(f"{str(test.get('kind', '')).replace('_', ' ').capitalize()}: {test['summary']}")

    level = (section.get("suggested_confidence") or {}).get("level")
    version = (report.get("generator") or {}).get("openavc_version")
    model = entered.get("model") or reported.get("model") or ""
    fields = {
        "title": f"[Test report] {driver.get('id')} on {model or 'unknown model'}",
        "driver": f"{driver.get('id')} {driver.get('version') or ''}".strip()
                  + (f"  ({files})" if files else ""),
        "models": model,
        "firmware": entered.get("firmware") or reported.get("firmware") or "",
        "transport": ", ".join(
            bit for bit in (connection.get("transport"),
                            f"port {config['port']}" if config.get("port") else "") if bit
        ),
        "worked": "\n".join(worked),
        "didnt": "\n".join(didnt),
        # A link fills the form's text fields but not its dropdowns (seen on
        # github.com 2026-10-01), so the suggested confidence rides in the notes.
        "notes": (
            f"From an OpenAVC device audit (OpenAVC {version}). The audit report file is attached."
            + (f" The audit suggests confidence: {level}." if level else "")
        ),
    }

    def build(values: dict[str, str]) -> str:
        return TEST_REPORT_FORM + "".join(
            f"&{key}={quote(value, safe='')}" for key, value in values.items() if value
        )

    # The detail lives in the attached report; the link carries what fits,
    # cutting what worked before what did not.
    sent = dict(fields)
    for key in ("worked", "didnt"):
        lines = sent[key].split("\n") if sent[key] else []
        while lines and len(build(sent)) > TEST_REPORT_URL_MAX:
            lines.pop()
            sent[key] = "\n".join(lines + [_MORE_IN_REPORT])
    return {"url": build(sent), "fields": fields}


_MORE_IN_REPORT = "(more in the attached report)"


ANSWER_WORDS = {"no": "the person said it did not happen", "partly": "the person said it partly happened"}


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------


def _slug(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (text[:40].strip("-")) or fallback


def report_filename(report: dict[str, Any], when: float | None = None) -> str:
    """``openavc-device-audit-<manufacturer>-<model>-<YYYYMMDD-HHMM>.zip``."""
    entered = report.get("device", {}).get("entered", {})
    reported = report.get("device", {}).get("reported", {})
    manufacturer = entered.get("manufacturer") or reported.get("manufacturer")
    model = entered.get("model") or reported.get("model")
    if not manufacturer and not model:
        manufacturer = report.get("target", {}).get("ip") or report.get("target", {}).get("address")
        model = "unidentified"
    stamp = datetime.fromtimestamp(when or report["session"]["started_at"]).strftime(
        "%Y%m%d-%H%M"
    )
    return (
        f"openavc-device-audit-{_slug(manufacturer, 'unknown')}-"
        f"{_slug(model, 'unknown')}-{stamp}.zip"
    )


def _clock(t: float | None) -> str:
    if not t:
        return "--:--:--.---"
    dt = datetime.fromtimestamp(t)
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def hex_pairs(hex_text: str) -> str:
    """Bytes as every sentence in a report writes them, ``aa 0b 01``: two
    digits a byte, spaced (a ``hex`` field holds the same bytes unspaced)."""
    return " ".join(hex_text[i:i + 2] for i in range(0, len(hex_text), 2))


def _quote(view: dict[str, str] | None, limit: int = 160) -> str:
    """A byte view as a reader sees it: text when printable, else hex pairs
    (the first ``limit / 2`` bytes)."""
    if not view:
        return "nothing"
    text = view.get("text", "")
    if not text:
        return "nothing"
    printable = all(c.isprintable() or c in "\r\n\t" for c in text)
    if not printable:
        raw = view.get("hex", "")
        return "hex " + hex_pairs(raw[:limit]) + ("..." if len(raw) > limit else "")
    shown = text.encode("unicode_escape").decode("ascii")
    if len(shown) > limit:
        shown = shown[:limit] + "..."
    return f'"{shown}"'


def _traffic_text(entry: dict[str, Any], limit: int = 400) -> str:
    """One traffic entry as a timeline line reads it."""
    meta = entry.get("meta") or {}
    body = _quote(entry, limit) if entry.get("text") else ""
    if entry.get("channel") in ("http", "http_listener"):
        request = f"{meta.get('method', '')} {meta.get('target', '')}".strip()
        if entry.get("direction") == "tx":
            head = request
        elif meta.get("error"):
            head = f"no response to {request}: {meta['error']}"
        elif meta.get("status") is not None:
            head = f"{meta.get('status')} {meta.get('reason', '')}".strip() + f" for {request}"
        else:
            head = request or "request"
        return f"{head}, body {body}" if body else head
    where = ""
    if meta.get("topic"):
        where = f"topic {meta['topic']}: "
    elif meta.get("peer"):
        where = f"{meta['peer']}: "
    return where + (body or "nothing")


def render_timeline(report: dict[str, Any]) -> str:
    """``timeline.txt``: the session's events, the check's exchanges and the
    driver's traffic, in order."""
    rows: list[tuple[float, str, str]] = []
    for entry in report.get("timeline", []):
        rows.append((entry.get("t") or 0.0, entry.get("kind", ""), entry.get("text", "")))
    for probe in report.get("footprint", {}).get("probes", []):
        proto = "TCP" if probe.get("kind") == "tcp" else "UDP"
        outcome = "matched" if probe.get("matched") else (probe.get("miss") or "no match")
        text = (
            f"{probe.get('probe_id')} {proto} port {probe.get('port')}: "
            f"sent {_quote(probe.get('sent'))}, reply {_quote(probe.get('reply'))}"
            f"{', ' + probe['error'] if probe.get('error') else ''} ({outcome})"
        )
        rows.append((probe.get("started_at") or 0.0, "probe", text))
    drivers = report.get("drivers", [])
    for section in drivers:
        label = f"[{section.get('driver', {}).get('name')}] " if len(drivers) > 1 else ""
        for attempt in section.get("attempts", []):
            for entry in attempt.get("traffic", {}).get("entries", []):
                if entry.get("chunk"):
                    continue
                rows.append((
                    entry.get("t") or 0.0,
                    f"{entry.get('direction')} {entry.get('channel')}",
                    label + _traffic_text(entry),
                ))
    rows.sort(key=lambda row: row[0])
    target = report.get("target", {})
    started = report.get("session", {}).get("started_at")
    header = [
        f"OpenAVC device audit of {target.get('address')} ({target.get('ip') or 'unresolved'})",
        f"Started {datetime.fromtimestamp(started).isoformat(timespec='seconds') if started else '?'}"
        f", OpenAVC {report.get('generator', {}).get('openavc_version')}",
        "Traffic lines are what the driver sent (tx) and handled (rx); report.json holds "
        "every byte, and the raw receive chunks before framing.",
        "",
    ]
    width = max((len(kind) for _, kind, _ in rows), default=4)
    lines = [f"{_clock(t)}  {kind.ljust(width)}  {text}" for t, kind, text in rows]
    return "\n".join(header + lines) + "\n"


_CSS = """
:root { --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --bg:#ffffff; --panel:#f6f8fa;
  --ok:#1a7f37; --warn:#9a6700; --bad:#cf222e; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6edf3; --muted:#9198a1; --line:#3d444d;
  --bg:#0d1117; --panel:#151b23; --ok:#3fb950; --warn:#d29922; --bad:#f85149; } }
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
  font:15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 920px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 28px 0 8px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
h3 { font-size: 15px; margin: 16px 0 4px; }
.meta { color: var(--muted); margin: 0 0 16px; }
.verdict { background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
  padding: 12px 16px; font-size: 17px; }
table { border-collapse: collapse; width: 100%; margin: 4px 0 8px; }
th, td { text-align: left; vertical-align: top; padding: 4px 8px; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; width: 28%; }
code, pre { font-family: ui-monospace, Consolas, "SFMono-Regular", monospace; font-size: 13px; }
pre { white-space: pre-wrap; word-break: break-all; background: var(--panel); padding: 8px;
  border-radius: 6px; margin: 4px 0; }
.matched { color: var(--ok); } .not_matched { color: var(--bad); } .not_observed { color: var(--muted); }
ul { margin: 4px 0; padding-left: 20px; }
footer { color: var(--muted); margin-top: 32px; font-size: 13px; }
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _row(label: str, value_html: str) -> str:
    return f"<tr><th>{_e(label)}</th><td>{value_html}</td></tr>"


def _join(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def render_summary(report: dict[str, Any]) -> str:
    """``summary.html``: the report for a person, self-contained, no scripts."""
    target = report.get("target", {})
    fp = report.get("footprint", {})
    verdict = report.get("verdict", {})
    reported = report.get("device", {}).get("reported", {})
    session = report.get("session", {})
    catalog = report.get("catalog", {})
    generator = report.get("generator", {})

    entered = report.get("device", {}).get("entered", {})
    title_bits = [
        entered.get("manufacturer") or reported.get("manufacturer"),
        entered.get("model") or reported.get("model"),
    ]
    title = " ".join(str(b) for b in title_bits if b) or target.get("address") or "Device"
    started = session.get("started_at")
    when = datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M") if started else ""
    parts: list[str] = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>Device audit: {_e(title)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>Device audit: {_e(title)}</h1>",
        f"<p class=\"meta\">{_e(target.get('address'))}"
        f"{' (' + _e(target.get('ip')) + ')' if target.get('ip') and target.get('ip') != target.get('address') else ''}"
        f" &middot; {_e(when)} &middot; OpenAVC {_e(generator.get('openavc_version'))}"
        f" on {_e(generator.get('os'))}</p>",
    ]
    if not report.get("complete"):
        parts.append("<p class=\"meta\">The network check had not finished when this report was taken.</p>")

    # Verdict and why.
    parts.append(f"<div class=\"verdict\">{_e(verdict.get('sentence', ''))}</div>")
    signals = (verdict.get("explanation") or {}).get("signals", [])
    names = verdict.get("drivers", {})
    pointing = [sig for sig in signals if sig.get("drivers")]
    unused = [sig for sig in signals if not sig.get("drivers")]
    if pointing:
        items = []
        for sig in pointing:
            drivers = [str(names.get(d, {}).get("name") or d) for d in sig["drivers"]]
            verb = "identifies" if sig.get("strong") else "suggests"
            items.append(f"<li>{_e(_signal_text(sig))} {verb} {_e(_join(drivers))}</li>")
        parts.append("<h3>Why</h3><ul>" + "".join(items) + "</ul>")
    if unused:
        parts.append(
            "<p class=\"meta\">Also seen, and no catalog driver uses "
            f"{'it' if len(unused) == 1 else 'them'}: "
            + "; ".join(_e(_signal_text(sig)) for sig in unused) + ".</p>"
        )
    parts.append(
        f"<p class=\"meta\">Checked against {_e(catalog.get('driver_count'))} catalog drivers"
        f" ({_e({'fresh': 'fetched for this audit', 'cached': 'from an earlier fetch', 'none': 'catalog unavailable'}.get(catalog.get('used'), ''))})"
        f"{', index.json SHA-256 ' + _e(catalog.get('sha256')) if catalog.get('sha256') else ''}.</p>"
    )

    # Identity.
    parts.append("<h2>What the device reported</h2><table>")
    for key, label in (
        ("manufacturer", "Manufacturer"), ("model", "Model"), ("model_number", "Model number"),
        ("firmware", "Firmware"),
        ("serial_number", "Serial number"), ("device_name", "Name"), ("hostname", "Host name"),
        ("mac", "MAC address"),
    ):
        if reported.get(key):
            parts.append(_row(label, _e(reported[key])))
    parts.append("</table>")

    # Network.
    ping = fp.get("ping", {})
    ports = fp.get("ports", {})
    mac = fp.get("mac", {})
    netbios = (fp.get("names") or {}).get("netbios") or {}
    parts.append("<h2>On the network</h2><table>")
    parts.append(_row("Ping", _e(
        {"alive": "answers", "timeout": "no answer", "error": "could not be sent"}.get(
            ping.get("result"), ping.get("result"))
    )))
    parts.append(_row("MAC address", _e(
        f"{mac.get('address')} (from {_MAC_SOURCE_TEXT.get(mac.get('source'), mac.get('source'))})"
        if mac.get("address") else "not read"
    )))
    if (fp.get("names") or {}).get("reverse_dns"):
        parts.append(_row("Reverse DNS", _e(fp["names"]["reverse_dns"])))
    if netbios:
        names_list = [n.get("name", "") for n in netbios.get("names", [])]
        parts.append(_row("NetBIOS", _e(
            f"{netbios.get('hostname')}"
            f"{', workgroup ' + netbios['workgroup'] if netbios.get('workgroup') else ''}"
            f" ({_join(names_list)})"
        )))
    parts.append(_row(
        f"TCP ports ({ports.get('checked', 0)} checked, {ports.get('range', '')})",
        _e(
            f"open: {_join([str(p) for p in ports.get('open', [])])}; "
            f"refused: {len(ports.get('refused', []))}; "
            f"no answer: {len(ports.get('filtered', []))}"
        ),
    ))
    parts.append("</table>")

    greetings = fp.get("greetings", {})
    spoke = {p: g for p, g in greetings.items() if g.get("text")}
    if greetings:
        parts.append("<h3>What each open port sent when OpenAVC connected</h3>")
        if spoke:
            for port, g in spoke.items():
                parts.append(f"<p>Port {_e(port)}:</p><pre>{_e(_quote(g, 600))}</pre>")
        else:
            parts.append("<p>No open port sent a greeting when OpenAVC connected.</p>")

    web = fp.get("web", {})
    if web:
        parts.append("<h3>Web pages</h3><table>")
        for port, page in web.items():
            bits = [page.get("status_line") or page.get("error") or "no answer"]
            if (page.get("tries") or 1) > 1:
                bits.append("asked twice" if not page.get("status_line") else "answered when asked again")
            if page.get("title"):
                bits.append(f"title \"{page['title']}\"")
            if page.get("server"):
                bits.append(f"server {page['server']}")
            if page.get("www_authenticate"):
                bits.append(f"asks for sign-in: {page['www_authenticate']}")
            if page.get("location"):
                bits.append(f"redirects to {page['location']}")
            if str(port) not in {str(p) for p in ports.get("open", [])}:
                bits.append("a port the device named in an announcement, not one the port check covers")
            parts.append(_row(f"Port {port} ({page.get('url', '')})", _e("; ".join(bits))))
        parts.append("</table>")
    certs = fp.get("certificates", {})
    if certs:
        parts.append("<h3>Certificates</h3><table>")
        for port, cert in certs.items():
            parts.append(_row(f"Port {port}", _e(
                f"subject {cert.get('subject')}; issuer {cert.get('issuer')}"
                f"{' (self-signed)' if cert.get('self_signed') else ''}; "
                f"names {_join(cert.get('san_dns', []) + cert.get('san_ip', []))}; "
                f"valid {cert.get('not_before')} to {cert.get('not_after')}; "
                f"SHA-256 {cert.get('sha256')}"
            )))
        parts.append("</table>")

    parts.append("<h2>Announcements</h2>")
    if fp.get("started_at") and fp.get("finished_at"):
        listened = round(fp["finished_at"] - fp["started_at"])
        parts.append(
            f"<p class=\"meta\">The network check listened for {listened} seconds; the "
            "listeners kept running while the audit did.</p>"
        )
    mdns = fp.get("mdns")
    if not mdns and (fp.get("ssdp") or fp.get("amx_ddp")):
        parts.append("<p>mDNS: none heard.</p>")
    if mdns:
        rows = []
        for svc in mdns.get("services", []):
            txt = ", ".join(f"{k}={v}" for k, v in (svc.get("txt") or {}).items())
            rows.append(
                f"<li><code>{_e(svc.get('service_type'))}</code> "
                f"{_e(svc.get('instance_name') or '')} port {_e(svc.get('port'))}"
                f"{' &middot; TXT ' + _e(txt) if txt else ''}</li>"
            )
        parts.append("<h3>mDNS</h3><ul>" + "".join(rows) + "</ul>")
        if mdns.get("enumerated_types"):
            parts.append(f"<p>Lists these service types: {_e(_join(mdns['enumerated_types']))}</p>")
    ssdp = fp.get("ssdp")
    if ssdp:
        parts.append("<h3>SSDP</h3><table>")
        parts.append(_row("Types", _e(_join(ssdp.get("device_types", [])))))
        for key, label in (
            ("friendly_name", "Name"), ("manufacturer", "Manufacturer"),
            ("model_name", "Model"), ("model_number", "Model number"),
            ("server", "Server header"),
        ):
            if ssdp.get(key):
                parts.append(_row(label, _e(ssdp[key])))
        # A device can announce several root devices, each with its own
        # description; the check reads one (the last announced).
        locations = list(dict.fromkeys(
            [h.get("location") for h in ssdp.get("raw_headers") or [] if isinstance(h, dict)]
            + [ssdp.get("location")]
        ))
        locations = [loc for loc in locations if loc]
        if locations:
            read = ssdp.get("location")
            parts.append(_row(
                "Descriptions at" if len(locations) > 1 else "Description at",
                _e("; ".join(
                    loc + (" (the one read)" if len(locations) > 1 and loc == read else "")
                    for loc in locations
                )),
            ))
        parts.append("</table>")
    amx = fp.get("amx_ddp")
    if amx:
        parts.append("<h3>AMX DDP beacon</h3><table>")
        for key, value in (amx.get("fields") or {}).items():
            parts.append(_row(key, _e(value)))
        parts.append("</table>")
    if not (mdns or ssdp or amx):
        parts.append("<p>The device announced nothing during the check.</p>")

    snmp = fp.get("snmp", {})
    parts.append("<h2>SNMP</h2>")
    if snmp.get("answered"):
        parts.append(f"<p>Answered with {_e(snmp.get('community'))}.</p><table>")
        for key, value in (snmp.get("values") or {}).items():
            parts.append(_row(key, _e(value)))
        parts.append("</table>")
        if snmp.get("walk"):
            parts.append(
                f"<p>{len(snmp['walk'])} values read in the full walk"
                f"{'' if snmp.get('walk_complete') else ' (stopped at the limit)'}; "
                "they are in report.json.</p>"
            )
    else:
        parts.append(f"<p>No answer ({_e(snmp.get('communities_tried', 0))} communities tried).</p>")

    checks = verdict.get("checks", {})
    if checks:
        parts.append("<h2>Each driver's discovery signals against this device</h2>")
        for driver_id, rows in checks.items():
            name = names.get(driver_id, {}).get("name") or driver_id
            parts.append(f"<h3>{_e(name)} <code>{_e(driver_id)}</code></h3><table>")
            for check in rows:
                parts.append(
                    f"<tr><th>{_e(check.get('declared'))}</th><td>"
                    f"<span class=\"{_e(check.get('status'))}\">{_e(check.get('status', '').replace('_', ' '))}</span>"
                    f": {_e(check.get('detail'))}</td></tr>"
                )
            parts.append("</table>")

    drivers = report.get("drivers", [])
    for section in drivers:
        parts.extend(_render_driver(section))

    limits = report.get("limits", [])
    if limits:
        parts.append("<h2>What the audit could not see</h2><ul>")
        parts.extend(f"<li>{_e(limit.get('text'))}</li>" for limit in limits)
        parts.append("</ul>")

    tester = session.get("tester") or {}
    if any(tester.get(k) for k in ("name", "company", "email", "notes")):
        parts.append("<h2>About the person who ran it</h2><table>")
        for key, label in (("name", "Name"), ("company", "Company"), ("email", "Email"),
                           ("notes", "Notes")):
            if tester.get(key):
                parts.append(_row(label, _e(tester[key])))
        parts.append("</table>")

    parts.append(
        "<footer>The complete record is report.json, every event in order is "
        "timeline.txt, and OpenAVC's own log lines about the audit are log.txt, all in "
        "the same file as this page.</footer></main></body></html>"
    )
    return "".join(parts)


_SOURCE_TEXT = {
    "catalog": "from the driver catalog",
    "imported": "imported on this system",
    "built_in": "built into OpenAVC",
}
_AGREEMENT_TEXT = {
    "agrees": "The network check identified this driver too.",
    "candidate": "The network check named this driver as one that might fit.",
    "differs": "The network check identified a different driver.",
    "no_verdict": "The network check did not identify a driver.",
}


def _seconds_after(t: float | None, start: float | None) -> str:
    if not t or not start:
        return "never"
    return f"{max(0.0, t - start):.1f} s after starting"


def _attempt_sentence(attempt: dict[str, Any]) -> str:
    status = attempt.get("status")
    offline = attempt.get("offline") or {}
    started = attempt.get("started_at")
    if attempt.get("connected_at"):
        listened = (attempt.get("finished_at") or attempt.get("ends_at") or 0) - attempt["connected_at"]
        traffic = attempt.get("traffic") or {}
        # A device that never answers can still show values: the driver's own
        # bookkeeping. Say what came back, and whose the values are.
        silent = not traffic.get("not_captured") and not traffic.get("received")
        reported = attempt.get("reported", 0)
        text = f"Connected {_seconds_after(attempt['connected_at'], started)}; "
        if silent:
            sent = traffic.get("sent", 0)
            text += f"the device sent nothing back to {sent} {'message' if sent == 1 else 'messages'}; "
        text += f"{reported} of {attempt.get('declared', 0)} status values reported"
        if listened > 0:
            secs = round(listened)
            text += f" in {secs} {'second' if secs == 1 else 'seconds'} of listening"
        if silent and reported:
            text += ", none of them by the device"
        drops = attempt.get("drops") or 0
        if drops:
            text += f"; the connection dropped {drops} {'time' if drops == 1 else 'times'}"
        if status == "stopped":
            text += "; stopped before the listening window ended"
        return text + "."
    if attempt.get("error"):
        return str(attempt["error"])
    reason = offline.get("detail") or offline.get("code")
    text = "Did not connect" + (f": {reason}" if reason else ".")
    if reason and not text.endswith("."):
        text += "."
    if offline.get("next_step"):
        text += " " + offline["next_step"]
    return text


def _status_rows(table: dict[str, Any]) -> list[str]:
    rows = []
    for var in table.get("variables", []):
        if var.get("reported") and var.get("value") is not None:
            value = var["value"]
            shown = ("true" if value else "false") if isinstance(value, bool) else str(value)
            cell = f"<code>{_e(shown)}</code>"
        else:
            sources = var.get("sources") or []
            cell = "not reported" + (
                f" (would be set by {_e(_join(sources))})" if sources else ""
            )
        if var.get("problem"):
            cell += f" <span class=\"not_matched\">{_e(var['problem'])}</span>"
        rows.append(_row(str(var.get("label") or var.get("name")), cell))
    return rows


def _render_driver(section: dict[str, Any]) -> list[str]:
    """One driver's test, for summary.html."""
    from openavc.audit.listen import transport_name
    from openavc.audit.observe import CONTRACT_TEXT

    d = section.get("driver", {})
    parts = [f"<h2>Driver test: {_e(d.get('name'))} {_e(d.get('version'))}</h2><table>"]
    parts.append(_row("Driver", (
        f"<code>{_e(d.get('id'))}</code>, {_e(_FORMAT_TEXT.get(d.get('format'), d.get('format')))}, "
        f"{_e(_SOURCE_TEXT.get(d.get('source'), d.get('source')))}"
    )))
    for f in d.get("files", []):
        if f.get("matches_catalog") is True:
            note = "matches the catalog's"
        elif f.get("matches_catalog") is False:
            note = f"differs from the catalog's ({f.get('catalog_sha256')})"
        else:
            note = "not in the catalog"
        where = f"; in this file as {f['in_report']}" if f.get("in_report") else ""
        parts.append(_row(f.get("name", ""), _e(f"SHA-256 {f.get('sha256')}, {note}{where}")))
    entered = section.get("entered") or {}
    said = " ".join(str(v) for v in (entered.get("manufacturer"), entered.get("model")) if v)
    if said:
        listing = (section.get("model_listing") or {}).get("listed")
        listed = {True: "the driver lists this model", False: "the driver does not list this model"}
        parts.append(_row("Device, as entered", _e(
            said + (f", firmware {entered['firmware']}" if entered.get("firmware") else "")
            + (f" ({listed[listing]})" if listing in listed else "")
        )))
    parts.append(_row("Network check", _e(
        _AGREEMENT_TEXT.get(section.get("verdict_agreement"), "")
    )))
    connection = section.get("connection") or {}
    if connection:
        config = ", ".join(f"{k}={v}" for k, v in (connection.get("config") or {}).items())
        parts.append(_row("Connection", _e(
            f"{transport_name(str(connection.get('transport') or ''))}: {config}"
            + (f" (from {connection['saved_from']}'s saved settings)"
               if connection.get("saved_from") else "")
        )))
    parts.append("</table>")

    attempts = section.get("attempts", [])
    if not attempts:
        parts.append("<p>The audit did not connect this driver.</p>")
    for number, attempt in enumerate(attempts, 1):
        heading = "Connect and listen" + (f", attempt {number}" if len(attempts) > 1 else "")
        parts.append(f"<h3>{heading}</h3><p>{_e(_attempt_sentence(attempt))}</p><table>")
        started = attempt.get("started_at")
        parts.append(_row("First bytes sent", _e(_seconds_after(attempt.get("first_tx_at"), started))))
        parts.append(_row("First reply", _e(_seconds_after(attempt.get("first_rx_at"), started))))
        if attempt.get("poll_interval"):
            parts.append(_row("Polls every", _e(f"{attempt['poll_interval']} seconds")))
        if attempt.get("drops") or attempt.get("reconnects"):
            drops, again = attempt.get("drops", 0), attempt.get("reconnects", 0)
            parts.append(_row("Dropped", _e(
                f"{drops} {'time' if drops == 1 else 'times'}, reconnected {again} "
                f"{'time' if again == 1 else 'times'}"
            )))
        traffic = attempt.get("traffic") or {}
        if traffic.get("not_captured"):
            traffic_text = "not captured: this driver manages its own connection"
        else:
            traffic_text = (
                f"{traffic.get('count', 0)} entries ({traffic.get('sent', 0)} sent, "
                f"{traffic.get('received', 0)} received, {traffic.get('bytes', 0)} bytes); "
                "every one is in timeline.txt and report.json"
            )
            if traffic.get("truncated_at"):
                traffic_text += "; stopped keeping traffic at the size limit"
        parts.append(_row("Traffic", _e(traffic_text)))
        unprompted = (attempt.get("unprompted_replies") or {}).get("count", 0)
        if unprompted:
            parts.append(_row("Unprompted replies", _e(
                f"{unprompted} arrived with no request in the "
                f"{attempt['unprompted_replies'].get('window_seconds')} seconds before them. "
                "The device may announce changes on its own; a driver that takes one as the "
                "answer to its next request misreads the replies after it."
            )))
        front = attempt.get("front_panel")
        if front:
            answer = "OpenAVC showed the change" if front.get("answer") == "showed" \
                else "OpenAVC did not show the change"
            parts.append(_row("Front-panel check", _e(
                answer + (f" ({front['note']})" if front.get("note") else "")
            )))
        parts.append("</table>")

        table = attempt.get("status_table") or {}
        rows = _status_rows(table)
        if rows:
            parts.append("<h3>Status values</h3><table>" + "".join(rows) + "</table>")
        children = table.get("children") or {}
        if children:
            names = table.get("child_labels") or {}
            counts = ", ".join(
                f"{len(ids)} {(names.get(ctype) or {}).get('one' if len(ids) == 1 else 'many') or ctype}"
                for ctype, ids in children.items()
            )
            parts.append(f"<p>Found: {_e(counts)} (values in report.json).</p>")
        settings = table.get("settings") or []
        if settings:
            parts.append("<h3>Device settings</h3><table>")
            for setting in settings:
                value = setting.get("value")
                parts.append(_row(setting.get("label") or setting.get("key"), _e(
                    _value_text(value) if setting.get("populated") else "not read back"
                )))
            parts.append("</table>")
        counts = (attempt.get("contract") or {}).get("counts") or {}
        if counts:
            items = []
            events = (attempt.get("contract") or {}).get("events") or []
            for kind, count in counts.items():
                example = next((e.get("detail") for e in events if e.get("kind") == kind), None)
                shown = ""
                if isinstance(example, dict):
                    shown = example.get("text") or example.get("state") or \
                        example.get("command") or example.get("address") or ""
                items.append(
                    f"<li>{_e(CONTRACT_TEXT.get(kind, kind))}: {count}"
                    f"{' (first: <code>' + _e(str(shown)[:200]) + '</code>)' if shown else ''}</li>"
                )
            parts.append("<h3>What the driver could not handle</h3><ul>" + "".join(items) + "</ul>")
    parts.extend(_render_commands(section.get("commands")))
    parts.extend(_render_settings(section.get("settings")))
    parts.extend(_render_outages(section.get("outages")))
    parts.extend(_render_confidence(section))
    return parts


_SIGNAL_PREFIXES = {
    "mdns": "mDNS announcement on {}",
    "ssdp": "SSDP announcement for {}",
    "oui": "MAC address prefix {} seen",
    "snmp_pen": "SNMP enterprise number {}",
    "hostname": "Hostname {} observed",
    "port_open": "Port {} is open",
}


def _signal_text(sig: dict[str, Any]) -> str:
    """A signal as the wizard's evidence lines say it (``describeEvidence`` in
    the IDE): what was seen, never a probe's own id."""
    data = (sig.get("evidence") or {}).get("data") or {}
    kind = data.get("kind")
    sid = data.get("source_id")
    port = data.get("port") if isinstance(data.get("port"), int) else None
    on = f" on port {port}" if port is not None else ""
    pattern = data.get("matched_pattern")
    value = data.get("value")
    if kind == "mdns":
        return f"mDNS announcement on {sid or 'an unknown service'}"
    if kind == "ssdp":
        return f"SSDP announcement for {sid or 'an unknown device type'}"
    if kind == "amx_ddp":
        named = " ".join(str(v) for v in (data.get("make"), data.get("model")) if v)
        return f"AMX DDP beacon: {named}" if named else "AMX DDP beacon"
    if kind == "broadcast":
        return f"UDP probe{on} matched" + (f" {pattern}" if pattern else "")
    if kind == "probe":
        text = " ".join(str((data.get("response") or {}).get("text") or "").split())[:80]
        if text:
            return f'TCP probe{on} returned "{text}"'
        return f"TCP probe{on} matched {pattern}" if pattern else f"TCP probe{on} answered"
    if kind == "oui":
        vendor = data.get("vendor")
        return (f"MAC address prefix {value} belongs to {vendor}" if vendor
                else f"MAC address prefix {value} seen")
    if kind == "hostname":
        return (f"Hostname pattern {pattern} matched {value}" if pattern
                else f"Hostname {value} observed")
    if kind == "snmp_pen":
        return f"SNMP enterprise number {value}"
    if kind == "vendor_string":
        return f'Manufacturer "{value}" named {_vendor_where(data)}'
    if kind == "open_port":
        return f"Port {value} is open"
    source = str(sig.get("source") or "")
    prefix, _, rest = source.partition(":")
    if prefix in _SIGNAL_PREFIXES and rest:
        return _SIGNAL_PREFIXES[prefix].format(rest)
    if prefix == "probe":
        return "A TCP identification check answered"
    if prefix == "broadcast":
        return "A UDP identification check answered"
    return source or "A signal"


# A manufacturer the driver's own probe supplies when it matches
# (``extract_manufacturer``): the device did not say it.
DRIVER_NAMED = "by the driver when its probe matched"


def _vendor_where(data: dict[str, Any]) -> str:
    """Where a manufacturer string came from (``vendorStringWhere`` in the
    IDE). A probe's own id is internal and never shown."""
    if data.get("from_driver"):
        return DRIVER_NAMED
    source = str(data.get("source_probe_id") or "")
    kind = str(data.get("from_kind") or "")
    what, _, port = source.partition(":")
    if what == "greeting" and port:
        return f"in the greeting on port {port}"
    if what == "http_server" and port:
        return f"by the web server on port {port}"
    if source == "ssdp_server":
        return "in the SSDP server name"
    if kind == "ssdp":
        return "in the SSDP description"
    if kind == "mdns":
        return "in an mDNS announcement"
    if kind == "amx_ddp":
        return "in the AMX DDP beacon"
    return "in a probe reply"


_MAC_SOURCE_TEXT = {
    "arp": "this computer's address table",
    "netbios": "NetBIOS",
    "snmp": "SNMP",
}

_FORMAT_TEXT = {"avcdriver": "YAML driver", "python": "Python driver"}


def _render_confidence(section: dict[str, Any]) -> list[str]:
    """The suggested confidence with what it rests on, and the test report."""
    confidence = section.get("suggested_confidence") or {}
    if not confidence:
        return []
    level = confidence.get("level")
    words = {"full": "Full support", "partial": "Partial support"}
    parts = [
        "<h3>Suggested confidence for this model</h3>",
        f"<p>{_e(words.get(level, level)) if level else 'Not enough to suggest one'}</p><ul>",
    ]
    for reason in confidence.get("reasons") or []:
        css = "matched" if reason.get("held") else "not_matched"
        mark = "Yes" if reason.get("held") else "No"
        parts.append(f"<li><span class=\"{css}\">{mark}</span>: {_e(reason.get('text'))}</li>")
    parts.append("</ul>")
    report = section.get("test_report")
    if report and report.get("url"):
        parts.append(
            f"<p><a href=\"{_e(report['url'])}\">Open a driver test report on GitHub</a>, "
            "with these results filled in. Attach this report file to it.</p>"
        )
    return parts


def _render_outages(outages: list[dict[str, Any]] | None) -> list[str]:
    """Each power cycle and cable pull, for summary.html."""
    from openavc.audit.outage import WORDS, outage_sentence

    if not outages:
        return []
    parts = ["<h3>Power and cable</h3><table>"]
    for test in outages:
        name = WORDS.get(test.get("kind"), {}).get("name") or str(test.get("kind"))
        parts.append(_row(
            f"{test.get('number')}. {name}", _e(test.get("summary") or outage_sentence(test)),
        ))
    parts.append("</table>")
    return parts


def _render_settings(settings: dict[str, Any] | None) -> list[str]:
    """The device settings written, for summary.html."""
    if not settings or not settings.get("trials"):
        return []
    parts = ["<h3>Device settings written</h3><table>"]
    for trial in settings["trials"]:
        parts.append(_row(str(trial.get("label") or trial.get("key")), _e(trial.get("summary"))))
    parts.append("</table>")
    return parts


def _trial_outcome(trial: dict[str, Any]) -> str:
    """One command's outcome in a sentence (the timeline's words)."""
    from openavc.audit.commands import trial_sentence

    text = trial.get("summary") or trial_sentence(trial)
    return text[:1].upper() + text[1:]


def _render_commands(commands: dict[str, Any] | None) -> list[str]:
    """The commands sent, and what they changed, for summary.html."""
    from openavc.audit.commands import ANSWERS

    if not commands or not commands.get("trials"):
        return []
    changed = commands.get("changed") or []
    parts = ["<h3>Commands sent</h3><table>"]
    for trial in commands["trials"]:
        params = trial.get("params") or {}
        label = f"{trial.get('number')}. {trial.get('label')}"
        if params:
            label += " (" + ", ".join(f"{k} {v}" for k, v in params.items()) + ")"
        if trial.get("batch"):
            label += ", with the status queries"
        before = trial.get("since_previous")
        if trial.get("attempt", 1) > 1 and before:
            label += f", again {before['seconds']:.1f} s after {before['label']}"
        said = trial.get("answer") or {}
        outcome = _trial_outcome(trial)
        if said.get("answer"):
            outcome += " " + ANSWERS[said["answer"]]
            if said.get("note"):
                outcome += f" ({said['note']})"
        parts.append(_row(label, _e(outcome)))
    parts.append("</table>")
    left = [item for item in changed if not item.get("on_its_own")]
    moving = [item for item in changed if item.get("on_its_own")]
    if left:
        parts.append("<h3>What the audit changed</h3><table>")
        for item in left:
            by = item.get("by")
            parts.append(_row(str(item.get("label") or item.get("key")), _e(
                f"{_value_text(item.get('before'))} before, {_value_text(item.get('now'))} now"
                + (f" (after {by['number']}. {by['label']})" if by else "")
            )))
        parts.append("</table>")
    if moving:
        parts.append("<p>" + _e(
            "Also different now, but changing without the audit: "
            + ", ".join(str(item.get("label") or item.get("key")) for item in moving) + "."
        ) + "</p>")
    return parts


def _value_text(value: Any) -> str:
    if value is None:
        return "not reported"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_zip(
    report: dict[str, Any],
    redactor: Redactor | None = None,
    driver_files: list[PlacedFile] | None = None,
    log_text: str = "",
) -> bytes:
    """The report zip. ``report`` is redacted already (``build_report``), and
    the other files are drawn from it; ``redactor`` runs over ``log_text``,
    the session's own log lines (``SessionLog``), the one file that is not;
    ``driver_files`` are placed already (``place_driver_files``)."""
    log_text = log_text or "OpenAVC wrote no log lines about this audit.\n"
    files = {
        "summary.html": render_summary(report),
        "report.json": json.dumps(report, indent=2, ensure_ascii=False, default=str),
        "timeline.txt": render_timeline(report),
        "log.txt": redactor.text(log_text) if redactor is not None else log_text,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text.encode("utf-8"))
        written: set[str] = set()
        for placed in driver_files or []:
            if placed.path not in written:
                written.add(placed.path)
                zf.writestr(placed.path, placed.data)
    return buf.getvalue()


def report_zip(session: "AuditSession") -> tuple[str, bytes]:
    """(file name, zip bytes) for ``session`` as it stands now."""
    report = build_report(session)
    name = report_filename(report)
    redactor = session_redactor(session)
    log_lines = getattr(session, "log", None)
    return name, build_zip(
        report, redactor, place_driver_files(session, redactor),
        log_text=log_lines.text() if log_lines is not None else "",
    )


# ---------------------------------------------------------------------------
# Recent reports on disk
# ---------------------------------------------------------------------------


class ReportStore:
    """The newest ``keep`` report files, in one directory."""

    def __init__(self, directory: Path, keep: int = REPORTS_KEPT) -> None:
        self.directory = Path(directory)
        self.keep = keep

    def save(self, name: str, data: bytes) -> str:
        """Write ``data`` as ``name`` (made unique), prune, return the name used."""
        if not _NAME_RE.match(name):
            raise ValueError(f"Not a report file name: {name}")
        self.directory.mkdir(parents=True, exist_ok=True)
        final = name
        counter = 2
        while (self.directory / final).exists():
            final = name[: -len(".zip")] + f"-{counter}.zip"
            counter += 1
        tmp = self.directory / (final + ".part")
        tmp.write_bytes(data)
        tmp.replace(self.directory / final)
        self._prune()
        return final

    def replace(self, name: str, data: bytes) -> str:
        """Overwrite an existing report (a session saving itself again)."""
        path = self.path(name)
        if path is None:
            return self.save(name, data)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return name

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        out = []
        for path in self.directory.iterdir():
            if path.is_file() and _NAME_RE.match(path.name):
                stat = path.stat()
                out.append({"name": path.name, "size": stat.st_size, "modified": stat.st_mtime})
        out.sort(key=lambda r: r["modified"], reverse=True)
        return out

    def path(self, name: str) -> Path | None:
        """The report's path, or None when there is no such report."""
        if not _NAME_RE.match(name):
            return None
        path = self.directory / name
        return path if path.is_file() else None

    def delete(self, name: str) -> bool:
        path = self.path(name)
        if path is None:
            return False
        path.unlink()
        return True

    def _prune(self) -> None:
        for stale in self.list()[self.keep:]:
            try:
                (self.directory / stale["name"]).unlink()
            except OSError:
                log.debug("Could not remove old report %s", stale["name"], exc_info=True)


def default_store() -> ReportStore:
    from openavc.system_config import get_data_dir

    return ReportStore(get_data_dir() / "audit_reports")


async def save_session_report(session: "AuditSession", store: ReportStore) -> str | None:
    """Save ``session``'s report to ``store``; the same file again on a resave.

    Nothing is saved for a session whose network check never started.
    """
    if _footprint_of(session) is None:
        return None
    name, data = report_zip(session)
    if session.report_name and store.path(session.report_name) is not None:
        saved = store.replace(session.report_name, data)
    else:
        saved = store.save(name, data)
    session.report_name = saved
    session.report_saved_at = time.time()
    return saved
