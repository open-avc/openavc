"""The device audit report: one zip a person can email.

::

    openavc-device-audit-<manufacturer>-<model>-<YYYYMMDD-HHMM>.zip
      (device_name_parts: entered, else reported, else the recognized driver)
      summary.html     readable summary, self-contained, no scripts
      report.json      the complete record
      timeline.txt     every event in order, the driver's traffic included
      driver/<files>   the exact driver file(s) that ran
      log.txt          OpenAVC's own log lines about the audit, INFO and up
                       (``session.SessionLog``)
      README.txt       what report.json's fields mean where the name does
                       not say (``FIELD_GUIDE``)

``report.json`` (``report_version`` 1) holds:

- ``report_version``; ``report_kind``, what made the file (``device_audit``);
  ``report_id``, a UUID new with every file built (a resave gets a new one);
  ``generator``: ``openavc_version``, ``os``,
  ``python`` and ``deployment_type`` (how OpenAVC was installed).
- ``session``: ``id``, ``started_at`` and ``ended_at``, ``status``, the
  ``steps`` run, the project device whose page started the audit (``origin``:
  id, name and driver, or null), ``no_driver`` (the person said no driver
  exists yet), the ``tester``'s name, company, email and notes (all
  optional), and ``follows_report``: the problem report (``report_id``,
  ``reference``) sent just before, when the audit was started from its Sent
  screen, else null.
- ``target``: the ``address`` as typed, the ``ip`` it resolved to, the reverse
  DNS ``hostname``, ``same_subnet`` (on one of this computer's subnets, or a
  169.254 device the check reached from a 169.254 address), the
  ``local_ip`` and ``interface`` the check used, and ``serial_port`` (none
  yet).
- ``device``: manufacturer, model and firmware as the person entered them on
  "Which driver?" (``entered``, null where left empty), and the identity the
  device reported to the network check (``reported``: ``manufacturer``,
  ``model``, ``model_number``, ``firmware``, ``serial_number``,
  ``device_name``, ``hostname``, ``mac``). An SSDP description's modelName is
  ``model`` and its modelNumber ``model_number``, never joined.
  ``reported_sources`` says where each of those came from, in words (the
  UPnP description, mDNS, reverse DNS, SNMP, this computer's ARP table), where
  the raw observations show it.
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
  and was asked once more (the exchange kept is the second), and
  ``followed``: when it answered with a redirect to another page on the same
  address and port, that page, read with one more GET (the same fields
  without ``tries`` or ``followed``; never a second redirect); else null. A
  redirect anywhere else is kept in ``location`` and not followed.
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
    ``liveness_every`` (the driver's liveness probe interval, 0 for none),
    ``silence_polls`` (polls in a row with no reply that take it offline,
    0 when the silence check does not watch it), ``reconnects`` and ``drops``
    while listening, ``later_reconnects`` and
    ``later_drops`` after the window closed, ``offline`` (``code``, ``detail``,
    ``next_step``), ``declared``, ``reported`` (by the device: a value
    written at or after its first reply) and ``set_by_driver`` (a value the
    driver wrote before any reply) counts, ``status_table``
    (every declared value with ``value``, ``reported``, ``set_by_driver``,
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
    published for the device, ``t`` and ``error``), ``error_writes`` (every
    ``last_error`` the driver wrote, ``t`` and ``error``, a rewrite of the same
    text included), ``effects`` (each declared ``sets`` entry:
    ``state``, ``state_key``, ``label``, ``expected``, ``has_value``, ``value`` and
    ``outcome``, one of ``confirmed``, ``already``, ``different``,
    ``unchanged``, ``not_reported``, ``no_value``), ``query`` (a status
    query's ``state``, ``state_key``, ``label``, ``value``, ``changed`` and
    ``outcome``: ``reported``, ``not_reported`` or ``no_reply``),
    ``refusals``: the signs of a refusal inside the command's own exchange
    (``device_errors`` and ``last_error_writes`` counted, ``last_error`` the
    text), ``later`` (each refusal of a request the driver sent on its own
    after it, once per text: ``error``, ``after`` seconds from the send,
    ``count``, ``request`` as the timeline writes it), ``unmatched`` and
    ``unmatched_examples``; ``sent_nothing`` (true when
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
    read-back did; ``unchanged`` when the value already showed before that
    write and the device reported nothing after it, so the read-back proves
    nothing; ``restore.automatic`` when the audit put it back as the driver
    stopped) and ``summary``.
  - ``suggested_confidence``: ``level`` (``full``, ``partial`` or null) and
    the ``reasons`` it rests on, each ``{"held", "text"}`` (the rule is
    ``audit.confidence``).
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
    ``probe_every``, ``silence_check`` and ``silence_polls``: the poll loop
    drops it after that many polls in a row with no reply,
    ``notice_within``: the longest the probe or the silence check can take
    to notice, ``poll_interval``: how the driver notices a device that went
    quiet); ``reason`` (the first offline reason after it noticed: ``code``,
    ``detail``) and every one in ``reasons``; ``measured``, in seconds
    (``noticed_after``, negative when OpenAVC noticed before the device
    stopped answering ping; ``away_for``; ``answered_after_on``;
    ``reconnected_after_back``; ``dropped_again_after``,
    ``reconnected_again_after``); ``before`` (the status values that had a
    value going in) and ``repopulated`` (``reported_again``,
    ``not_reported_again``: written on the connection the driver reconnected
    on, from when it began connecting, the driver's start-up reads included,
    changed or not);
    ``announcements`` (what the device announced during the test:
    ``t``, ``protocol``, ``detail``); ``summary`` says it in a sentence.

- ``timeline``: every session event in order, typed and timestamped.
- ``limits``: what the audit could not see, and why.
- ``complete``: false when the report was taken before the network check
  finished.

**Redaction** (the rules are ``audit.redaction``). Every secret the person
typed (a read community other than ``public``, a driver credential) and
every credential the driver's config registered is replaced in the record
before anything is written; a serial number the person asked to leave out
is replaced the same way, every one the audit heard. The
record is redacted as a tree and never as JSON text, so a number, a ``true``
or a key the secret happens to equal is left alone and ``report.json``
always parses; ``summary.html`` and ``timeline.txt`` are drawn from the
redacted record, and ``log.txt`` (the session's own log lines, not in the
record) is the one file redacted as text. Traffic is masked (``***``) as it
is written out, so the hex of a masked entry still decodes. The report never
reads the server's own configuration. A driver file whose bytes match the
catalog's is copied unredacted; one that does not (imported, or changed
here) is redacted, and its entry says whether that changed it.

**Recent reports** are kept in ``{data_dir}/audit_reports/``, the newest
``REPORTS_KEPT``, so closing a browser tab does not lose one.
"""

from __future__ import annotations

import io
import json
import logging
import platform
import re
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openavc.audit.confidence import device_was_silent, driver_test_report, suggested_confidence
from openavc.audit.limits import report_limits
from openavc.audit.redaction import Redactor, session_redactor
from openavc.audit.report_text import (
    REPORT_CSS as _CSS,
    clock as _clock,
    escape_html as _e,
    hex_pairs,
    html_row as _row,
    quote_bytes as _quote,
    slug as _slug,
    traffic_text as _traffic_text,
    utc_offset,
)
from openavc.audit.session import footprint_of
from openavc.utils.boolean_words import value_text

if TYPE_CHECKING:
    from openavc.audit.session import AuditSession

log = logging.getLogger("audit.report")

REPORT_VERSION = 1
REPORT_KIND = "device_audit"
REPORTS_KEPT = 10
_NAME_RE = re.compile(r"^openavc-device-audit-[a-z0-9-]+\.zip$")


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


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


def place_files(
    identity: dict[str, Any] | None,
    file_contents: dict[str, bytes],
    redactor: "Redactor",
    *,
    run: int = 0,
    used: dict[str, bytes] | None = None,
) -> list[PlacedFile]:
    """Where one driver's files go in a report zip: ``driver/<name>``.

    A file the catalog publishes (it matches the catalog) keeps its bytes;
    any other is redacted as text. ``used`` is the paths already placed and
    their bytes, shared across the drivers of one zip: a name already placed
    with different bytes goes under ``driver/run-<run + 1>/`` instead.
    """
    if used is None:
        used = {}
    published = {
        f.get("name") for f in (identity or {}).get("files") or []
        if f.get("matches_catalog") is True
    }
    placed: list[PlacedFile] = []
    for name, raw in sorted(file_contents.items()):
        # The catalog's own bytes are public: never changed.
        data, redacted = (raw, False) if name in published else _redact_file(raw, redactor)
        path = f"driver/{name}"
        if path in used and used[path] != data:
            path = f"driver/run-{run + 1}/{name}"
        used.setdefault(path, data)
        placed.append(PlacedFile(run, name, path, data, redacted))
    return placed


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
        placed += place_files(
            getattr(run.choice, "identity", None), run.choice.file_contents, redactor,
            run=run.index, used=used,
        )
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


# Where a reported identity field can have come from, in the order looked
# for: the raw observation that holds that same value, said in words.
_MAC_SOURCES = {"arp": "this computer's ARP table", "netbios": "NetBIOS", "snmp": "SNMP"}


def identity_sources(fp: dict[str, Any], reported: dict[str, Any]) -> dict[str, str]:
    """For each identity field the network check reported, where it came
    from: the raw observation in ``footprint`` that holds the same value."""
    ssdp = fp.get("ssdp") or {}
    amx = fp.get("amx_ddp") or {}
    names = fp.get("names") or {}
    netbios = names.get("netbios") or {}
    snmp = (fp.get("snmp") or {}).get("values") or {}
    mdns = fp.get("mdns") or {}
    mdns_names = [(svc.get("instance_name"), "mDNS") for svc in mdns.get("services") or []]
    upnp = "the UPnP description"
    places: dict[str, list[tuple[Any, str]]] = {
        "manufacturer": [(ssdp.get("manufacturer"), upnp), (amx.get("make"), "the AMX DDP beacon"),
                         (snmp.get("entPhysicalMfgName"), "SNMP")],
        "model": [(ssdp.get("model_name"), upnp), (amx.get("model"), "the AMX DDP beacon"),
                  (snmp.get("entPhysicalModelName"), "SNMP")],
        "model_number": [(ssdp.get("model_number"), upnp)],
        "serial_number": [(ssdp.get("serial_number"), upnp), (snmp.get("entPhysicalSerialNum"), "SNMP")],
        "firmware": [(snmp.get("entPhysicalFirmwareRev"), "SNMP")],
        "device_name": [(ssdp.get("friendly_name"), upnp), (snmp.get("sysName"), "SNMP"),
                        (netbios.get("hostname"), "NetBIOS"), *mdns_names,
                        (mdns.get("address_name"), "mDNS")],
        "hostname": [(names.get("reverse_dns"), "reverse DNS"), (netbios.get("hostname"), "NetBIOS")],
    }
    out: dict[str, str] = {}
    for key, value in reported.items():
        if not value:
            continue
        if key == "mac":
            source = _MAC_SOURCES.get(str((fp.get("mac") or {}).get("source") or ""))
        else:
            source = next(
                (where for raw, where in places.get(key, [])
                 if raw and str(raw).strip().lower() == str(value).strip().lower()),
                None,
            )
        if source:
            out[key] = source
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
    footprint = footprint_of(session)
    fp = footprint.to_dict() if footprint is not None else {}
    complete = session.footprint is not None
    device = fp.get("device") or {}
    tester = dict(getattr(session, "tester", None) or {})
    tester.pop("leave_out_serial", None)

    limits = report_limits(session, footprint, list(fp.get("limits", [])))

    network = fp.get("network") or {}
    names = fp.get("names") or {}
    raw_footprint = {
        key: value for key, value in fp.items()
        if key not in ("evidence", "verdict", "device", "limits")
    }
    report = {
        "report_version": REPORT_VERSION,
        "report_kind": REPORT_KIND,
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
            "follows_report": dict(session.follows_report)
            if getattr(session, "follows_report", None) else None,
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
            "reported_sources": identity_sources(fp, _reported(device, fp.get("ssdp") or {})),
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
    # A new id for every file built, put in after redaction so no secret can
    # cut into it: the service that receives reports keeps one per id, so a
    # copy re-sent unchanged is never stored twice while a rebuilt one (more
    # results, the tester's details) is a report of its own.
    report = {
        "report_version": REPORT_VERSION,
        "report_kind": REPORT_KIND,
        "report_id": str(uuid.uuid4()),
        **report,
    }
    # Read off the redacted record, so neither can carry a secret.
    for section in report["drivers"]:
        section["suggested_confidence"] = suggested_confidence(section)
        section["test_report"] = driver_test_report(report, section)
    return report


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------


def _recognized_as(report: dict[str, Any]) -> tuple[str, str]:
    """(manufacturer, name) of the driver the network check recognized the
    device as, or empty strings. A driver that only might fit is not one."""
    verdict = report.get("verdict") or {}
    driver_id = (verdict.get("identification") or {}).get("driver_id")
    if verdict.get("state") != "identified" or not driver_id:
        return "", ""
    driver = (verdict.get("drivers") or {}).get(driver_id) or {}
    return str(driver.get("manufacturer") or ""), str(driver.get("name") or driver_id)


def device_name_parts(report: dict[str, Any]) -> list[str | None]:
    """What a report is named for, the file name and summary.html's title:
    ``[manufacturer, model]``, each part what the person entered on "Which
    driver?", else what the device reported about itself. A part both leave
    out comes from the driver the network check recognized the device as: its
    manufacturer, and its name for the model. That name stands alone when it
    already starts with the manufacturer's first word ("Acme Widget
    Controller" from Acme Corp). A missing part is None; nothing to name the
    device by is ``[]``."""
    entered = report.get("device", {}).get("entered") or {}
    reported = report.get("device", {}).get("reported") or {}
    manufacturer = entered.get("manufacturer") or reported.get("manufacturer")
    model = entered.get("model") or reported.get("model")
    maker, name = _recognized_as(report)
    if name and not model:
        words = str(manufacturer or maker).split()
        if words and name.lower().split()[0] == words[0].lower():
            return [name]
        return [manufacturer or maker or None, name]
    if not manufacturer and not model:
        return []
    return [manufacturer or maker or None, model]


def report_filename(report: dict[str, Any], when: float | None = None) -> str:
    """``openavc-device-audit-<manufacturer>-<model>-<YYYYMMDD-HHMM>.zip``
    (``device_name_parts``), the address and ``unidentified`` when nothing
    names the device."""
    parts = device_name_parts(report) or [
        report.get("target", {}).get("ip") or report.get("target", {}).get("address"),
        "unidentified",
    ]
    stamp = datetime.fromtimestamp(when or report["session"]["started_at"]).strftime(
        "%Y%m%d-%H%M"
    )
    name = "-".join(_slug(part, "unknown") for part in parts)
    return f"openavc-device-audit-{name}-{stamp}.zip"


_PROBE_ID = re.compile(r"^custom_(.+?)_(?:companion_)?(?:tcp|udp)$")


def _probe_owner(probe_id: Any) -> str:
    """A probe as a line names it: the driver whose identification check it
    is, never the probe's own id (``custom_<driver>_tcp``)."""
    match = _PROBE_ID.match(str(probe_id or ""))
    return f"{match.group(1)} identification check," if match else "Identification check,"


def render_timeline(report: dict[str, Any]) -> str:
    """``timeline.txt``: the session's events, the check's exchanges and the
    driver's traffic, in order."""
    rows: list[tuple[float, str, str]] = [
        (entry.get("t") or 0.0, entry.get("kind", ""), entry.get("text", ""))
        for entry in report.get("timeline", [])
    ]
    rows += [_probe_row(probe) for probe in report.get("footprint", {}).get("probes", [])]
    rows += _traffic_rows(report.get("drivers", []))
    rows.sort(key=lambda row: row[0])
    width = max((len(kind) for _, kind, _ in rows), default=4)
    lines = [f"{_clock(t)}  {kind.ljust(width)}  {text}" for t, kind, text in rows]
    return "\n".join(_timeline_header(report) + lines) + "\n"


def _timeline_header(report: dict[str, Any]) -> list[str]:
    target = report.get("target", {})
    started = report.get("session", {}).get("started_at")
    return [
        f"OpenAVC device audit of {target.get('address')} ({target.get('ip') or 'unresolved'})",
        f"Started {datetime.fromtimestamp(started).isoformat(timespec='seconds') if started else '?'}"
        f" ({utc_offset(started)}), OpenAVC {report.get('generator', {}).get('openavc_version')}",
        f"Times are this computer's local time, {utc_offset(started)}.",
        "Traffic lines are what the driver sent (tx) and handled (rx); report.json holds "
        "every byte, and the raw receive chunks before framing.",
        "",
    ]


def _probe_row(probe: dict[str, Any]) -> tuple[float, str, str]:
    """One identification check the network check sent, as a timeline row."""
    proto = "TCP" if probe.get("kind") == "tcp" else "UDP"
    outcome = "matched" if probe.get("matched") else (probe.get("miss") or "no match")
    text = (
        f"{_probe_owner(probe.get('probe_id'))} {proto} port {probe.get('port')}: "
        f"{_probe_exchange(probe)}{', ' + probe['error'] if probe.get('error') else ''} ({outcome})"
    )
    return (probe.get("started_at") or 0.0, "probe", text)


def _probe_exchange(probe: dict[str, Any]) -> str:
    if probe.get("miss") == "login_prompt":
        return "not sent, the port asked for a login"
    if probe.get("from_greeting"):
        return f"the port's greeting {_quote(probe.get('reply'))}, no connection of its own"
    return f"sent {_quote(probe.get('sent'))}, reply {_quote(probe.get('reply'))}"


def _traffic_rows(drivers: list[dict[str, Any]]) -> list[tuple[float, str, str]]:
    """Each driver's traffic as it handled it, each line named for the driver
    when more than one ran. The raw receive chunks are in report.json only."""
    rows = []
    for section in drivers:
        label = f"[{section.get('driver', {}).get('name')}] " if len(drivers) > 1 else ""
        rows += [
            (entry.get("t") or 0.0, f"{entry.get('direction')} {entry.get('channel')}",
             label + _traffic_text(entry))
            for attempt in section.get("attempts", [])
            for entry in attempt.get("traffic", {}).get("entries", [])
            if not entry.get("chunk")
        ]
    return rows


def _page_bits(page: dict[str, Any]) -> list[str]:
    """One web page as the summary describes it: its answer, title, server,
    sign-in and redirect."""
    bits = [page.get("status_line") or page.get("error") or "no answer"]
    if page.get("title"):
        bits.append(f"title \"{page['title']}\"")
    if page.get("server"):
        bits.append(f"server {page['server']}")
    if page.get("www_authenticate"):
        bits.append(f"asks for sign-in: {page['www_authenticate']}")
    if page.get("location"):
        bits.append(f"redirects to {page['location']}")
    return bits


def _join(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def render_summary(report: dict[str, Any]) -> str:
    """``summary.html``: the report for a person, self-contained, no scripts."""
    fp = report.get("footprint", {})
    verdict = report.get("verdict", {})
    parts = _summary_head(report) + _summary_verdict(report) + _summary_identity(report)
    for section in (_summary_network, _summary_greetings, _summary_web, _summary_certificates,
                    _summary_announcements, _summary_snmp):
        parts += section(fp)
    parts += _summary_checks(verdict)
    for driver in report.get("drivers", []):
        parts.extend(_render_driver(driver))
    parts += _summary_limits(report.get("limits", []))
    parts += _summary_tester(report.get("session", {}).get("tester") or {})
    parts.append(_SUMMARY_FOOTER)
    return "".join(parts)


_SUMMARY_FOOTER = (
    "<footer>The complete record is report.json, every event in order is "
    "timeline.txt, and OpenAVC's own log lines about the audit are log.txt, all in "
    "the same file as this page.</footer></main></body></html>"
)


def _summary_head(report: dict[str, Any]) -> list[str]:
    """The page's head, title line and, for an unfinished check, the note saying so."""
    target = report.get("target", {})
    generator = report.get("generator", {})
    title = (
        " ".join(str(b) for b in device_name_parts(report) if b)
        or target.get("address") or "Device"
    )
    started = report.get("session", {}).get("started_at")
    when = (
        datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M") + f" {utc_offset(started)}"
        if started else ""
    )
    ip = target.get("ip")
    shown_ip = " (" + _e(ip) + ")" if ip and ip != target.get("address") else ""
    parts = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>Device audit: {_e(title)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>Device audit: {_e(title)}</h1>",
        f"<p class=\"meta\">{_e(target.get('address'))}{shown_ip}"
        f" &middot; {_e(when)} &middot; OpenAVC {_e(generator.get('openavc_version'))}"
        f" on {_e(generator.get('os'))}</p>",
    ]
    if not report.get("complete"):
        parts.append("<p class=\"meta\">The network check had not finished when this report was taken.</p>")
    return parts


_CATALOG_USED_TEXT = {
    "fresh": "fetched for this audit",
    "cached": "from an earlier fetch",
    "none": "catalog unavailable",
}


def _summary_verdict(report: dict[str, Any]) -> list[str]:
    """The verdict, the signals behind it, and the catalog it was checked against."""
    verdict = report.get("verdict", {})
    catalog = report.get("catalog", {})
    signals = (verdict.get("explanation") or {}).get("signals", [])
    names = verdict.get("drivers", {})
    sha = ", index.json SHA-256 " + _e(catalog.get("sha256")) if catalog.get("sha256") else ""
    return [
        f"<div class=\"verdict\">{_e(verdict.get('sentence', ''))}</div>",
        *_summary_why(signals, names),
        f"<p class=\"meta\">Checked against {_e(catalog.get('driver_count'))} catalog drivers"
        f" ({_e(_CATALOG_USED_TEXT.get(catalog.get('used'), ''))}){sha}.</p>",
    ]


def _seen_on_the_device(sig: dict[str, Any]) -> bool:
    """False for a manufacturer the driver's own probe supplied: the device
    was not seen to say it."""
    return not ((sig.get("evidence") or {}).get("data") or {}).get("from_driver")


def _summary_why(signals: list[dict[str, Any]], names: dict[str, Any]) -> list[str]:
    """Each signal that points at a driver, and the ones seen that no driver uses."""
    pointing = [sig for sig in signals if sig.get("drivers")]
    unused = [sig for sig in signals if not sig.get("drivers") and _seen_on_the_device(sig)]
    parts = []
    if pointing:
        items = [_why_item(sig, names) for sig in pointing]
        parts.append("<h3>Why</h3><ul>" + "".join(items) + "</ul>")
    if unused:
        parts.append(
            "<p class=\"meta\">Also seen, and no catalog driver uses "
            f"{'it' if len(unused) == 1 else 'them'}: "
            + "; ".join(_e(_signal_text(sig, names)) for sig in unused) + ".</p>"
        )
    return parts


def _why_item(sig: dict[str, Any], names: dict[str, Any]) -> str:
    drivers = [str(names.get(d, {}).get("name") or d) for d in sig["drivers"]]
    verb = "identifies" if sig.get("strong") else "suggests"
    return f"<li>{_e(_signal_text(sig, names))} {verb} {_e(_join(drivers))}</li>"


_IDENTITY_ROWS = (
    ("manufacturer", "Manufacturer"), ("model", "Model"), ("model_number", "Model number"),
    ("firmware", "Firmware"),
    ("serial_number", "Serial number"), ("device_name", "Name"), ("hostname", "Host name"),
    ("mac", "MAC address"),
)


def _summary_identity(report: dict[str, Any]) -> list[str]:
    """What the device reported about itself, and where each part came from."""
    reported = report.get("device", {}).get("reported", {})
    sources = (report.get("device") or {}).get("reported_sources", {})
    read_name = _driver_read_name(report)
    rows = []
    for key, label in _IDENTITY_ROWS:
        where = sources.get(key)
        if key == "device_name" and read_name:
            rows.append(_row(label, _e(_read_name_text(read_name, reported.get(key), where))))
        elif reported.get(key):
            rows.append(_row(label, _e(reported[key]) + (f" (from {_e(where)})" if where else "")))
    return ["<h2>What the device reported</h2><table>", *rows, "</table>"]


def _driver_read_name(report: dict[str, Any]) -> str | None:
    """The device's own name as a driver read it. It beats a name the
    network check heard, which can be an id (an mDNS instance name)."""
    return next((
        value for section in report.get("drivers") or []
        for key, _, value in _driver_read_identity(section) if key == "device_name"
    ), None)


def _read_name_text(read_name: str, heard: Any, where: str | None) -> str:
    """The name the driver read, with the one the check heard when it differs."""
    also = (
        f"; {heard}" + (f" from {where}" if where else "")
        if heard and str(heard).strip().lower() != read_name.strip().lower() else ""
    )
    return f"{read_name} (as the driver read it{also})"


_PING_TEXT = {"alive": "answers", "timeout": "no answer", "error": "could not be sent"}


def _summary_network(fp: dict[str, Any]) -> list[str]:
    """Ping, the MAC address, the names the network gave, and the TCP ports."""
    ping = fp.get("ping", {})
    ports = fp.get("ports", {})
    names = fp.get("names") or {}
    netbios = names.get("netbios") or {}
    parts = [
        "<h2>On the network</h2><table>",
        _row("Ping", _e(_PING_TEXT.get(ping.get("result"), ping.get("result")))),
        _row("MAC address", _e(_mac_text(fp.get("mac", {})))),
    ]
    if names.get("reverse_dns"):
        parts.append(_row("Reverse DNS", _e(names["reverse_dns"])))
    if netbios:
        parts.append(_row("NetBIOS", _e(_netbios_text(netbios))))
    parts.append(_row(
        f"TCP ports ({ports.get('checked', 0)} checked, {ports.get('range', '')})",
        _e(_ports_text(ports)),
    ))
    parts.append("</table>")
    return parts


def _mac_text(mac: dict[str, Any]) -> str:
    if not mac.get("address"):
        return "not read"
    return f"{mac.get('address')} (from {_MAC_SOURCE_TEXT.get(mac.get('source'), mac.get('source'))})"


def _netbios_text(netbios: dict[str, Any]) -> str:
    names = [n.get("name", "") for n in netbios.get("names", [])]
    workgroup = ", workgroup " + netbios["workgroup"] if netbios.get("workgroup") else ""
    return f"{netbios.get('hostname')}{workgroup} ({_join(names)})"


def _ports_text(ports: dict[str, Any]) -> str:
    announced = (
        f" ({_join([str(p) for p in ports['from_announcements']])} named in the device's "
        "own announcement, outside the list checked)"
        if ports.get("from_announcements") else ""
    )
    return (
        f"open: {_join([str(p) for p in ports.get('open', [])])}{announced}"
        f"; refused: {len(ports.get('refused', []))}; "
        f"no answer: {len(ports.get('filtered', []))}"
    )


def _summary_greetings(fp: dict[str, Any]) -> list[str]:
    """What each open port sent when the check connected."""
    greetings = fp.get("greetings", {})
    spoke = {p: g for p, g in greetings.items() if g.get("text")}
    if not greetings:
        return []
    parts = ["<h3>What each open port sent when OpenAVC connected</h3>"]
    if not spoke:
        return parts + ["<p>No open port sent a greeting when OpenAVC connected.</p>"]
    return parts + [f"<p>Port {_e(port)}:</p><pre>{_e(_quote(g, 600))}</pre>" for port, g in spoke.items()]


def _summary_web(fp: dict[str, Any]) -> list[str]:
    """Each web page the check read, and the page a redirect led to."""
    web = fp.get("web", {})
    if not web:
        return []
    announced = {str(p) for p in fp.get("ports", {}).get("from_announcements", [])}
    parts = ["<h3>Web pages</h3><table>"]
    for port, page in web.items():
        parts += _web_rows(port, page, str(port) in announced)
    parts.append("</table>")
    return parts


def _web_rows(port: Any, page: dict[str, Any], announced: bool) -> list[str]:
    bits = _page_bits(page)
    if (page.get("tries") or 1) > 1:
        bits.insert(1, "asked twice" if not page.get("status_line") else "answered when asked again")
    if announced:
        bits.append("a port the device named in an announcement, not one the port check covers")
    rows = [_row(f"Port {port} ({page.get('url', '')})", _e("; ".join(bits)))]
    followed = page.get("followed")
    if followed:
        rows.append(_row(
            f"Port {port}, the page it redirects to ({followed.get('url', '')})",
            _e("; ".join(_page_bits(followed))),
        ))
    return rows


def _summary_certificates(fp: dict[str, Any]) -> list[str]:
    certs = fp.get("certificates", {})
    if not certs:
        return []
    rows = [_row(f"Port {port}", _e(_certificate_text(cert))) for port, cert in certs.items()]
    return ["<h3>Certificates</h3><table>", *rows, "</table>"]


def _certificate_text(cert: dict[str, Any]) -> str:
    return (
        f"subject {cert.get('subject')}; issuer {cert.get('issuer')}"
        f"{' (self-signed)' if cert.get('self_signed') else ''}; "
        f"names {_join(cert.get('san_dns', []) + cert.get('san_ip', []))}; "
        f"valid {cert.get('not_before')} to {cert.get('not_after')}; "
        f"SHA-256 {cert.get('sha256')}"
    )


def _summary_announcements(fp: dict[str, Any]) -> list[str]:
    """What the device announced (mDNS, SSDP, an AMX beacon), or that it announced nothing."""
    mdns = fp.get("mdns")
    heard = _summary_mdns(mdns) + _summary_ssdp(fp.get("ssdp")) + _summary_amx(fp.get("amx_ddp"))
    if not heard:
        heard = ["<p>The device announced nothing during the check.</p>"]
    elif not mdns:
        heard.insert(0, "<p>mDNS: none heard.</p>")
    return ["<h2>Announcements</h2>", *_listened_line(fp), *heard]


def _listened_line(fp: dict[str, Any]) -> list[str]:
    if not (fp.get("started_at") and fp.get("finished_at")):
        return []
    listened = round(fp["finished_at"] - fp["started_at"])
    return [
        f"<p class=\"meta\">The network check listened for {listened} seconds; the "
        "listeners kept running while the audit did.</p>"
    ]


def _summary_mdns(mdns: dict[str, Any] | None) -> list[str]:
    if not mdns:
        return []
    items = "".join(_mdns_item(svc) for svc in mdns.get("services", []))
    parts = ["<h3>mDNS</h3><ul>" + items + "</ul>"]
    if mdns.get("enumerated_types"):
        parts.append(f"<p>Lists these service types: {_e(_join(mdns['enumerated_types']))}</p>")
    return parts


def _mdns_item(svc: dict[str, Any]) -> str:
    txt = ", ".join(f"{k}={v}" for k, v in (svc.get("txt") or {}).items())
    return (
        f"<li><code>{_e(svc.get('service_type'))}</code> "
        f"{_e(svc.get('instance_name') or '')} port {_e(svc.get('port'))}"
        f"{' &middot; TXT ' + _e(txt) if txt else ''}</li>"
    )


_SSDP_ROWS = (
    ("friendly_name", "Name"), ("manufacturer", "Manufacturer"),
    ("model_name", "Model"), ("model_number", "Model number"),
    ("server", "Server header"),
)


def _summary_ssdp(ssdp: dict[str, Any] | None) -> list[str]:
    if not ssdp:
        return []
    return [
        "<h3>SSDP</h3><table>",
        _row("Types", _e(_join(ssdp.get("device_types", [])))),
        *(_row(label, _e(ssdp[key])) for key, label in _SSDP_ROWS if ssdp.get(key)),
        *_ssdp_locations(ssdp),
        "</table>",
    ]


def _ssdp_locations(ssdp: dict[str, Any]) -> list[str]:
    """Where the descriptions are. A device can announce several root
    devices, each with its own description; the check reads one (the last
    announced)."""
    locations = list(dict.fromkeys(
        [h.get("location") for h in ssdp.get("raw_headers") or [] if isinstance(h, dict)]
        + [ssdp.get("location")]
    ))
    locations = [loc for loc in locations if loc]
    if not locations:
        return []
    read = ssdp.get("location")
    several = len(locations) > 1
    return [_row(
        "Descriptions at" if several else "Description at",
        _e("; ".join(loc + (" (the one read)" if several and loc == read else "") for loc in locations)),
    )]


def _summary_amx(amx: dict[str, Any] | None) -> list[str]:
    if not amx:
        return []
    rows = [_row(key, _e(value)) for key, value in (amx.get("fields") or {}).items()]
    return ["<h3>AMX DDP beacon</h3><table>", *rows, "</table>"]


def _summary_snmp(fp: dict[str, Any]) -> list[str]:
    snmp = fp.get("snmp", {})
    if not snmp.get("answered"):
        tried = snmp.get("communities_tried", 0)
        return [
            "<h2>SNMP</h2>",
            f"<p>No answer ({_e(tried)} {'community' if tried == 1 else 'communities'} tried).</p>",
        ]
    parts = [
        "<h2>SNMP</h2>",
        f"<p>Answered with {_e(snmp.get('community'))}.</p><table>",
        *(_row(key, _e(value)) for key, value in (snmp.get("values") or {}).items()),
        "</table>",
    ]
    if snmp.get("walk"):
        parts.append(
            f"<p>{len(snmp['walk'])} values read in the full walk"
            f"{'' if snmp.get('walk_complete') else ' (stopped at the limit)'}; "
            "they are in report.json.</p>"
        )
    return parts


def _summary_checks(verdict: dict[str, Any]) -> list[str]:
    """Each named driver's declared discovery signals, judged against the device."""
    checks = verdict.get("checks", {})
    if not checks:
        return []
    names = verdict.get("drivers", {})
    parts = ["<h2>Each driver's discovery signals against this device</h2>"]
    for driver_id, rows in checks.items():
        name = names.get(driver_id, {}).get("name") or driver_id
        parts.append(f"<h3>{_e(name)} <code>{_e(driver_id)}</code></h3><table>")
        parts += [_check_row(check) for check in rows]
        parts.append("</table>")
    return parts


def _check_row(check: dict[str, Any]) -> str:
    return (
        f"<tr><th>{_e(check.get('declared'))}</th><td>"
        f"<span class=\"{_e(check.get('status'))}\">{_e(check.get('status', '').replace('_', ' '))}</span>"
        f": {_e(check.get('detail'))}</td></tr>"
    )


def _summary_limits(limits: list[dict[str, Any]]) -> list[str]:
    if not limits:
        return []
    return [
        "<h2>What the audit could not see</h2><ul>",
        *(f"<li>{_e(limit.get('text'))}</li>" for limit in limits),
        "</ul>",
    ]


_TESTER_ROWS = (("name", "Name"), ("company", "Company"), ("email", "Email"), ("notes", "Notes"))


def _summary_tester(tester: dict[str, Any]) -> list[str]:
    rows = [_row(label, _e(tester[key])) for key, label in _TESTER_ROWS if tester.get(key)]
    if not rows:
        return []
    return ["<h2>About the person who ran it</h2><table>", *rows, "</table>"]


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


# Status values that hold the device's own model, firmware and name.
_READ_IDENTITY = (
    ("model", "Model", ("model", "model_name", "model_id")),
    ("firmware", "Firmware", ("firmware", "firmware_version")),
    ("device_name", "Name", ("device_name", "speaker_name", "system_name")),
)


def _driver_read_identity(section: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(key, label, value) for the model, firmware and name the device
    reported to the driver, from the last attempt that connected."""
    attempts = [a for a in section.get("attempts") or [] if a.get("connected_at")]
    if not attempts:
        return []
    values = {
        v.get("name"): v.get("value")
        for v in (attempts[-1].get("status_table") or {}).get("variables") or []
        if v.get("reported") and v.get("value") not in (None, "")
    }
    out = []
    for key, label, names in _READ_IDENTITY:
        read = next((values[n] for n in names if n in values), None)
        if read is not None:
            out.append((key, label, str(read)))
    return out


def _same_name(a: Any, b: Any) -> bool:
    """Two ways of writing one model or version: every word of one appears,
    in order, among the other's, ignoring case and punctuation ("Connect
    Series 352D" in "Connect Series Model 352D"; "352" is not "352D"). The
    rule Which driver? names a model by (``modelNamedIn``)."""
    def words(value: Any) -> list[str]:
        return [w for w in re.split(r"[^a-z0-9]+", str(value).lower()) if w]

    def in_order(want: list[str], have: list[str]) -> bool:
        at = 0
        for w in have:
            if at < len(want) and w == want[at]:
                at += 1
        return at == len(want)

    x, y = words(a), words(b)
    return bool(x and y) and (in_order(x, y) or in_order(y, x))


def _attempt_sentence(attempt: dict[str, Any]) -> str:
    status = attempt.get("status")
    offline = attempt.get("offline") or {}
    started = attempt.get("started_at")
    if attempt.get("connected_at"):
        listened = (attempt.get("finished_at") or attempt.get("ends_at") or 0) - attempt["connected_at"]
        traffic = attempt.get("traffic") or {}
        # A device that never answers can still show values: the driver's own
        # bookkeeping. Say what came back, and whose the values are.
        silent = device_was_silent(traffic)
        reported = attempt.get("reported", 0)
        by_driver = attempt.get("set_by_driver", 0)
        text = f"Connected {_seconds_after(attempt['connected_at'], started)}; "
        if silent:
            sent = traffic.get("sent", 0)
            text += f"the device sent nothing back to {sent} {'message' if sent == 1 else 'messages'}; "
        text += f"{reported} of {attempt.get('declared', 0)} status values reported"
        if listened > 0:
            secs = round(listened)
            text += f" in {secs} {'second' if secs == 1 else 'seconds'} of listening"
        if by_driver:
            text += f", {by_driver} more set by the driver itself"
        drops = attempt.get("drops") or 0
        if drops:
            text += f"; the connection dropped {drops} {'time' if drops == 1 else 'times'} while listening"
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
        if (var.get("reported") or var.get("set_by_driver")) and var.get("value") is not None:
            value = var["value"]
            cell = f"<code>{_e(value_text(value))}</code>"
            if not var.get("reported"):
                cell += " set by the driver, not reported by the device"
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
    return [
        *_driver_header(section),
        *_driver_attempts(section.get("attempts", [])),
        *_render_commands(section.get("commands")),
        *_render_settings(section.get("settings")),
        *_render_outages(section.get("outages")),
        *_render_confidence(section),
    ]


def _driver_header(section: dict[str, Any]) -> list[str]:
    """The driver, its files, the device as entered and as the driver read it,
    and the connection it used."""
    d = section.get("driver", {})
    return [
        f"<h2>Driver test: {_e(d.get('name'))} {_e(d.get('version'))}</h2><table>",
        _row("Driver", (
            f"<code>{_e(d.get('id'))}</code>, {_e(_FORMAT_TEXT.get(d.get('format'), d.get('format')))}, "
            f"{_e(_SOURCE_TEXT.get(d.get('source'), d.get('source')))}"
        )),
        *(_driver_file_row(f) for f in d.get("files", [])),
        *_entered_rows(section),
        _row("Network check", _e(_AGREEMENT_TEXT.get(section.get("verdict_agreement"), ""))),
        *_connection_rows(section.get("connection") or {}),
        "</table>",
    ]


def _driver_file_row(f: dict[str, Any]) -> str:
    if f.get("matches_catalog") is True:
        note = "matches the catalog's"
    elif f.get("matches_catalog") is False:
        note = f"differs from the catalog's ({f.get('catalog_sha256')})"
    else:
        note = "not in the catalog"
    where = f"; in this file as {f['in_report']}" if f.get("in_report") else ""
    return _row(f.get("name", ""), _e(f"SHA-256 {f.get('sha256')}, {note}{where}"))


_LISTED_TEXT = {True: "the driver lists this model", False: "the driver does not list this model"}


def _entered_rows(section: dict[str, Any]) -> list[str]:
    """The device as the person entered it, then as the driver read it."""
    entered = section.get("entered") or {}
    rows = []
    said = " ".join(str(v) for v in (entered.get("manufacturer"), entered.get("model")) if v)
    if said:
        listing = (section.get("model_listing") or {}).get("listed")
        rows.append(_row("Device, as entered", _e(
            said + (f", firmware {entered['firmware']}" if entered.get("firmware") else "")
            + (f" ({_LISTED_TEXT[listing]})" if listing in _LISTED_TEXT else "")
        )))
    for key, what, read in _driver_read_identity(section):
        typed = entered.get(key)
        differs = typed and not _same_name(typed, read)
        rows.append(_row(f"{what}, as the driver read it", _e(
            read + (f" (not the {what.lower()} entered, {typed})" if differs else "")
        )))
    return rows


def _connection_rows(connection: dict[str, Any]) -> list[str]:
    from openavc.audit.listen import transport_name

    if not connection:
        return []
    config = ", ".join(f"{k}={v}" for k, v in (connection.get("config") or {}).items())
    return [_row("Connection", _e(
        f"{transport_name(str(connection.get('transport') or ''))}: {config}"
        + (f" (from {connection['saved_from']}'s saved settings)"
           if connection.get("saved_from") else "")
    ))]


def _driver_attempts(attempts: list[dict[str, Any]]) -> list[str]:
    """Each connect-and-listen: its sentence, its table, and what it found."""
    parts = [] if attempts else ["<p>The audit did not connect this driver.</p>"]
    for number, attempt in enumerate(attempts, 1):
        heading = "Connect and listen" + (f", attempt {number}" if len(attempts) > 1 else "")
        parts.append(f"<h3>{heading}</h3><p>{_e(_attempt_sentence(attempt))}</p><table>")
        parts += _attempt_timing_rows(attempt) + _attempt_drop_rows(attempt)
        parts.append(_row("Traffic", _e(_traffic_summary(attempt.get("traffic") or {}))))
        parts += _unprompted_rows(attempt) + _front_panel_rows(attempt)
        parts.append("</table>")
        parts += _attempt_findings(attempt)
    return parts


def _attempt_timing_rows(attempt: dict[str, Any]) -> list[str]:
    """When the first bytes went and came back, and how the driver keeps watch."""
    started = attempt.get("started_at")
    rows = [
        _row("First bytes sent", _e(_seconds_after(attempt.get("first_tx_at"), started))),
        _row("First reply", _e(_seconds_after(attempt.get("first_rx_at"), started))),
    ]
    if attempt.get("poll_interval"):
        rows.append(_row("Polls every", _e(f"{attempt['poll_interval']} seconds")))
    if attempt.get("liveness_every"):
        rows.append(_row("Liveness check", _e(
            f"every {attempt['liveness_every']} seconds, apart from the polls: how the driver "
            "notices a device that stopped answering"
        )))
    elif attempt.get("silence_polls"):
        rows.append(_row("Silence check", _e(
            f"{attempt['silence_polls']} polls in a row with no reply take the device offline: "
            "how the driver notices a device that stopped answering"
        )))
    return rows


def _times(count: Any) -> str:
    return "time" if count == 1 else "times"


def _attempt_drop_rows(attempt: dict[str, Any]) -> list[str]:
    rows = []
    for label, drops, again in (
        ("Dropped while listening", attempt.get("drops", 0), attempt.get("reconnects", 0)),
        ("Dropped after listening", attempt.get("later_drops", 0),
         attempt.get("later_reconnects", 0)),
    ):
        if drops or again:
            rows.append(_row(label, _e(
                f"{drops} {_times(drops)}, reconnected {again} {_times(again)}"
            )))
    return rows


def _traffic_summary(traffic: dict[str, Any]) -> str:
    if traffic.get("not_captured"):
        return "not captured: this driver manages its own connection"
    text = (
        f"{traffic.get('count', 0)} entries ({traffic.get('sent', 0)} sent, "
        f"{traffic.get('received', 0)} received, {traffic.get('bytes', 0)} bytes); "
        "every one is in timeline.txt and report.json"
    )
    if traffic.get("truncated_at"):
        text += "; stopped keeping traffic at the size limit"
    return text


def _unprompted_rows(attempt: dict[str, Any]) -> list[str]:
    unprompted = (attempt.get("unprompted_replies") or {}).get("count", 0)
    if not unprompted:
        return []
    return [_row("Unprompted replies", _e(
        f"{unprompted} arrived with no request in the "
        f"{attempt['unprompted_replies'].get('window_seconds')} seconds before them. "
        "The device may announce changes on its own; a driver that takes one as the "
        "answer to its next request misreads the replies after it."
    ))]


_FRONT_PANEL_TEXT = {
    "showed": "OpenAVC showed the change",
    "could_not_try": "not tried: nothing could be changed on the device",
}


def _front_panel_rows(attempt: dict[str, Any]) -> list[str]:
    front = attempt.get("front_panel")
    if front:
        answer = _FRONT_PANEL_TEXT.get(front.get("answer"), "OpenAVC did not show the change")
        return [_row("Front-panel check", _e(
            answer + (f" ({front['note']})" if front.get("note") else "")
        ))]
    if attempt.get("connected_at"):
        return [_row("Front-panel check", "not run")]
    return []


def _attempt_findings(attempt: dict[str, Any]) -> list[str]:
    """The status values, the channels and zones found, the device settings
    read, and what the driver could not handle."""
    table = attempt.get("status_table") or {}
    rows = _status_rows(table)
    parts = ["<h3>Status values</h3><table>" + "".join(rows) + "</table>"] if rows else []
    return (
        parts + _children_found(table) + _device_settings_read(table)
        + _contract_problems(attempt.get("contract") or {})
    )


def _children_found(table: dict[str, Any]) -> list[str]:
    children = table.get("children") or {}
    if not children:
        return []
    names = table.get("child_labels") or {}
    counts = ", ".join(
        f"{len(ids)} {(names.get(ctype) or {}).get('one' if len(ids) == 1 else 'many') or ctype}"
        for ctype, ids in children.items()
    )
    return [f"<p>Found: {_e(counts)} (values in report.json).</p>"]


def _device_settings_read(table: dict[str, Any]) -> list[str]:
    settings = table.get("settings") or []
    if not settings:
        return []
    rows = [
        _row(setting.get("label") or setting.get("key"), _e(
            _value_text(setting.get("value")) if setting.get("populated") else "not read back"
        ))
        for setting in settings
    ]
    return ["<h3>Device settings</h3><table>", *rows, "</table>"]


def _contract_problems(contract: dict[str, Any]) -> list[str]:
    """Each kind of thing the driver could not handle, how often, and the first."""
    from openavc.audit.observe import CONTRACT_TEXT

    counts = contract.get("counts") or {}
    if not counts:
        return []
    events = contract.get("events") or []
    items = []
    for kind, count in counts.items():
        shown = _first_example(kind, events)
        items.append(
            f"<li>{_e(CONTRACT_TEXT.get(kind, kind))}: {count}"
            f"{' (first: <code>' + _e(str(shown)[:200]) + '</code>)' if shown else ''}</li>"
        )
    return ["<h3>What the driver could not handle</h3><ul>" + "".join(items) + "</ul>"]


def _first_example(kind: str, events: list[dict[str, Any]]) -> Any:
    example = next((e.get("detail") for e in events if e.get("kind") == kind), None)
    if not isinstance(example, dict):
        return ""
    return (
        example.get("text") or example.get("state")
        or example.get("command") or example.get("address") or ""
    )


_SIGNAL_PREFIXES = {
    "mdns": "mDNS announcement on {}",
    "ssdp": "SSDP announcement for {}",
    "oui": "MAC address prefix {} seen",
    "snmp_pen": "SNMP enterprise number {}",
    "hostname": "Hostname {} observed",
    "port_open": "Port {} is open",
}


def _signal_text(sig: dict[str, Any], names: dict[str, Any] | None = None) -> str:
    """A signal as the wizard's evidence lines say it (``describeEvidence`` in
    the IDE): what was seen, never a probe's own id. ``names`` is the
    verdict's ``drivers``, for naming the driver whose probe supplied a
    manufacturer."""
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
        response = data.get("response") or {}
        raw = str(response.get("text") or "")
        text, noise = _clean_banner(raw)
        readable = len("".join(text.split()))
        if readable >= 2 and noise * 2 <= readable:
            text = text[:80]
            return f"TCP probe{on} returned " + (f"'{text}'" if '"' in text else f'"{text}"')
        if pattern:
            return f"TCP probe{on} matched {pattern}"
        if response.get("hex"):
            return f"TCP probe{on} returned hex {hex_pairs(str(response['hex'])[:80])}"
        return f"TCP probe{on} answered"
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
        return f'Manufacturer "{value}" named {_vendor_where(data, names)}'
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


def _clean_banner(raw: str) -> tuple[str, int]:
    """A reply's readable part and how many unprintable characters sit
    inside it (``cleanBannerText`` in the IDE, the same rule): the preamble
    before the first printable character goes, a run of four unprintable
    ones ends the message, shorter runs read as a space. A reply reads as
    text with two readable characters and no more than half as many
    unprintable ones (``readsAsText``)."""
    def printable(ch: str) -> bool:
        return 0x20 <= ord(ch) <= 0x7E

    start = 0
    while start < len(raw) and not printable(raw[start]):
        start += 1
    end, run_start, run = len(raw), -1, 0
    for i in range(start, len(raw)):
        ch = raw[i]
        if printable(ch):
            run_start, run = -1, 0
            continue
        if ch in "\t\r\n":
            continue
        if run_start < 0:
            run_start = i
        run += 1
        if run >= 4:
            end = run_start
            break
    kept = raw[start:end]
    text = " ".join("".join(ch if printable(ch) else " " for ch in kept).split())
    noise = sum(1 for ch in kept if not printable(ch) and ch not in "\t\r\n")
    return text, noise


# A manufacturer the driver's own probe supplies when it matches
# (``extract_manufacturer``): the device did not say it.
DRIVER_NAMED = "by the driver when its probe matched"


def _vendor_where(data: dict[str, Any], names: dict[str, Any] | None = None) -> str:
    """Where a manufacturer string came from (``vendorStringWhere`` in the
    IDE). A probe's own id is internal and never shown. One a driver's probe
    supplied names that driver when its name is known: the line lists every
    driver the manufacturer points at, and only that one's probe matched."""
    if data.get("from_driver"):
        entry = (names or {}).get(str(data.get("supplied_by") or ""))
        name = entry.get("name") if isinstance(entry, dict) else None
        return f"by the {name} driver when its probe matched" if name else DRIVER_NAMED
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
        return ["<h3>Power and cable</h3><p>The power cycle and cable pull tests were not run.</p>"]
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


def _render_commands(commands: dict[str, Any] | None) -> list[str]:
    """The commands sent, and what they changed, for summary.html."""
    if not commands or not commands.get("trials"):
        return []
    param_defs = _param_defs(commands)
    rows = [
        _row(_command_label(trial, param_defs), _e(_command_outcome(trial)))
        for trial in commands["trials"]
    ]
    return [
        "<h3>Commands sent</h3><table>", *rows, "</table>",
        *_changed_lines(commands.get("changed") or []),
    ]


def _param_defs(commands: dict[str, Any]) -> dict[Any, Any]:
    """Each command's parameters, from the command list the report keeps, so
    each value is named as its field is."""
    return {
        c.get("name"): c.get("params") for c in commands.get("catalog") or [] if isinstance(c, dict)
    }


def _changed_lines(changed: list[dict[str, Any]]) -> list[str]:
    """What the commands left changed, what they were first to report, and
    what changed on its own. A value with no reading before the first command
    is something the audit learned, not something it left changed: there is
    nothing to set back to."""
    by_audit = [item for item in changed if not item.get("on_its_own")]
    return [
        *_left_changed([item for item in by_audit if item.get("before") is not None]),
        *_first_reported([item for item in by_audit if item.get("before") is None]),
        *_changing_on_its_own([item for item in changed if item.get("on_its_own")]),
    ]


def _command_label(trial: dict[str, Any], param_defs: dict[Any, Any]) -> str:
    """The command as its row names it: number, label, the values it was
    sent with, and when it was a repeat."""
    from openavc.audit.commands import params_text

    label = f"{trial.get('number')}. {trial.get('label')}"
    label += params_text(trial.get("params") or {}, param_defs.get(trial.get("command")))
    if trial.get("batch"):
        label += ", with the status queries"
    before = trial.get("since_previous")
    if trial.get("attempt", 1) > 1 and before:
        label += f", again {before['seconds']:.1f} s after {before['label']}"
    return label


def _command_outcome(trial: dict[str, Any]) -> str:
    """What the command did, and what the person said about it."""
    from openavc.audit.commands import ANSWERS, trial_outcome

    said = trial.get("answer") or {}
    outcome = trial_outcome(trial)
    if said.get("answer"):
        outcome += " " + ANSWERS[said["answer"]]
        if said.get("note"):
            outcome += f" ({said['note']})"
    return outcome


def _after_command(by: dict[str, Any] | None) -> str:
    return f" (after {by['number']}. {by['label']})" if by else ""


def _left_changed(left: list[dict[str, Any]]) -> list[str]:
    """Each value the audit's commands moved from an earlier reading."""
    if not left:
        return []
    rows = [
        _row(str(item.get("label") or item.get("key")), _e(
            f"{_value_text(item.get('before'))} before, {_value_text(item.get('now'))} now"
            + _after_command(item.get("by"))
        ))
        for item in left
    ]
    return ["<h3>What the audit changed</h3><table>", *rows, "</table>"]


def _first_reported(first: list[dict[str, Any]]) -> list[str]:
    """The values first reported during the audit, said once per command
    they followed (a Meters On reports a dozen)."""
    if not first:
        return []
    groups: dict[Any, tuple[dict[str, Any] | None, list[str]]] = {}
    for item in first:
        by = item.get("by")
        groups.setdefault((by or {}).get("number"), (by, []))[1].append(
            f"{item.get('label') or item.get('key')}: {_value_text(item.get('now'))}"
        )
    texts = [", ".join(values) + _after_command(by) for by, values in groups.values()]
    return ["<p>" + _e(
        "First reported during the audit, with no earlier reading to compare: "
        + "; ".join(texts) + "."
    ) + "</p>"]


def _changing_on_its_own(moving: list[dict[str, Any]]) -> list[str]:
    if not moving:
        return []
    return ["<p>" + _e(
        "Also different now, but changing without the audit: "
        + ", ".join(str(item.get("label") or item.get("key")) for item in moving) + "."
    ) + "</p>"]


def _value_text(value: Any) -> str:
    return value_text(value, "not reported")


# README.txt in every report: what the fields of report.json mean where the
# name does not say. The full schema is this module's docstring.
FIELD_GUIDE = """\
How to read this report

summary.html is the report for a person. report.json is the complete record,
and this file explains the fields in it that are not plain from their names.

Times
  A time in report.json is seconds since 1970-01-01 UTC. The clock times in
  timeline.txt and log.txt are the local time of the computer that ran the
  audit; their first lines give its offset from UTC.

Bytes
  Bytes are kept as {"hex", "text"}: hex is the bytes unspaced (aa0b01), text
  the same bytes decoded as Latin-1, so every byte survives. A sentence writes
  bytes as "hex aa 0b 01".

Masked values
  "***" is a credential or a secret parameter. "[redacted]" is a value typed
  as a secret, wherever it appeared. "[serial number removed]" is a serial
  number the person asked to leave out.

The network check (footprint)
  ping.method is how ping was sent: exec (the system ping program), dgram or
  raw (an ICMP socket), or none (no way to send one). ping.result is alive,
  timeout or error.
  ports.open lists the open TCP ports. ports.from_announcements lists open
  ports outside the list checked: the device named them in an announcement
  and a web page answered there.
  probes lists each driver's identification check sent to the device. A
  driver's TCP check runs when its port is open, and every UDP check runs.
  Each holds what was sent and the reply, and matched (the reply is what the
  driver looks for) or miss (why not). A port's TCP checks run one at a
  time. A TCP check that sends nothing is compared with what its port said
  when the check connected and listened (greetings), and from_greeting is
  true: it opened no connection of its own. A port that said nothing is
  listened to once more first. When the port's greeting asks for a login, a
  check that sends is sent only if what it looks for is that prompt; the
  others have miss login_prompt and sent nothing.

The verdict
  explanation.signals lists what the check saw that a driver's identification
  rules use. strong is true for a signal that identifies a driver on its own
  (a probe reply, an announcement type), false for one that only suggests it
  (a MAC prefix, a manufacturer name). drivers lists the catalog drivers that
  use the signal.
  signal_index_drivers is how many catalog drivers declare any signal.
  checks holds each named driver's declared signals, judged against the device.

The driver test (drivers)
  attempts[].traffic.count is the messages sent and received as the driver
  handled them; entries also holds the raw receive chunks before framing
  (chunk: true).
  In attempts[].status_table, reported means the device said the value (it
  was written at or after the device's first reply); set_by_driver means the
  driver wrote it before the device replied.
  commands.catalog describes each command as the driver declares it. query
  marks a status query: query_for names the value it asks for, and polled
  says the driver's polling runs it. needs_input means it takes a value
  nobody has given yet. suggested marks a command the driver puts on a device
  page, tried first. confirm is the driver's question before sending it.
  commands.trials holds every command sent and what its window showed; each
  summary says it in a sentence.
  suggested_confidence is the catalog confidence these results support, with
  every condition it rests on.
"""


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
    started = (report.get("session") or {}).get("started_at")
    log_text = f"Times are this computer's local time, {utc_offset(started)}.\n\n" + log_text
    files = {
        "summary.html": render_summary(report),
        "report.json": json.dumps(report, indent=2, ensure_ascii=False, default=str),
        "timeline.txt": render_timeline(report),
        "log.txt": redactor.text(log_text) if redactor is not None else log_text,
        "README.txt": FIELD_GUIDE,
    }
    return write_zip(files, driver_files)


def write_zip(files: dict[str, str], driver_files: list[PlacedFile] | None = None) -> bytes:
    """A report zip: each text file as UTF-8, then each placed driver file
    once, in that order."""
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
    """The newest ``keep`` report files, in one directory.

    Only a name ``name_pattern`` matches is saved, listed or found (the
    audit's own, ``openavc-device-audit-*.zip``, unless another is given), so
    nothing else in the directory is ever served or pruned.
    """

    def __init__(
        self, directory: Path, keep: int = REPORTS_KEPT, name_pattern: re.Pattern[str] = _NAME_RE,
    ) -> None:
        self.directory = Path(directory)
        self.keep = keep
        self.name_pattern = name_pattern

    def save(self, name: str, data: bytes) -> str:
        """Write ``data`` as ``name`` (made unique), prune, return the name used."""
        if not self.name_pattern.match(name):
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
            if path.is_file() and self.name_pattern.match(path.name):
                stat = path.stat()
                out.append({"name": path.name, "size": stat.st_size, "modified": stat.st_mtime})
        out.sort(key=lambda r: r["modified"], reverse=True)
        return out

    def path(self, name: str) -> Path | None:
        """The report's path, or None when there is no such report."""
        if not self.name_pattern.match(name):
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
    if footprint_of(session) is None:
        return None
    name, data = report_zip(session)
    if session.report_name and store.path(session.report_name) is not None:
        saved = store.replace(session.report_name, data)
    else:
        saved = store.save(name, data)
    session.report_name = saved
    session.report_saved_at = time.time()
    return saved
