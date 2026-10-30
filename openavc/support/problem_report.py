"""A problem report: one zip about one device, built when someone asks.

::

    openavc-problem-report-<driver id>-<YYYYMMDD-HHMM>.zip
      summary.html     readable summary, self-contained, no scripts
      report.json      the complete record
      traffic.txt      the device's recent messages, oldest first
      log.txt          OpenAVC's recent log lines about the device
      README.txt       what report.json's fields mean (``FIELD_GUIDE``)
      driver/<files>   the driver file(s) the device runs

It is a snapshot of what the platform already recorded: building one sends
the device nothing and never pauses it. It reads only this one device's
records, so nothing about any other device, the project, its macros, scripts
or variables, the system's settings or its credentials is in it.

``report.json`` (``report_version`` 1, ``report_kind`` ``problem_report``):

- ``report_id`` (a UUID, one per file built), ``created_at`` (seconds since
  1970 UTC), ``generator`` (OpenAVC version, OS, Python, how OpenAVC was
  installed).
- ``sender``: ``message``, ``name``, ``email`` as typed (all optional), and
  ``leave_out_serial``.
- ``context``: where the press came from (``source``: ``driver_line``,
  ``offline_banner`` or ``command_failure``), the ``error`` the page showed,
  and for a command its ``command``, ``params`` (secrets masked), HTTP
  ``status`` and ``code``. The page sends this: neither a command's failure
  nor a connection test is kept on the server.
- ``device``: ``id``, ``name``, ``enabled``, ``connected``, the
  ``manufacturer`` and ``model`` when the device reported them,
  ``driver_identity`` (the driver's id, name, version, format and source, each
  file's hashes against the driver library's, ``modified``, the library's
  listing), ``settings`` (credentials masked), ``address`` (where it really
  connects, from the saved record: a simulation's 127.0.0.1 never shows),
  ``transport``, ``simulated`` and ``simulator_gap``, ``orphaned`` and
  ``orphan_reason``, and ``installed`` for product hardware.
- ``state``: every ``device.<id>.*`` value now (children included) and three
  ``system.*`` values; ``history``: this device's rows of the state history.
- ``traffic``: the device's recent messages (``entries``), ``count`` and
  ``window_start``, the oldest message's time.
- ``log``: OpenAVC's recent log lines about the device.
- ``limits``: what the report could not include, and why.

The window: the traffic recorder and the device's values are emptied each
time the device is rebuilt (a saved settings change, a driver reload), so a
report covers what happened since then; ``window_start`` and the summary say
from when.

Masking, one redactor over the whole record and every file
(``audit.report.Redactor``): the device's registered secrets, the value of
every setting its driver marks secret or that is named like a credential, a
value the driver wrote to state under a credential's name (state is the one
source the platform never masks), a command parameter of type ``password``,
and the serial numbers it reported
when the person asks to leave them out. IP addresses, MAC addresses and host
names stay: they are what tells a network fault from a driver fault, and the
dialog shows the address before anything is sent.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from typing import Any

from openavc.audit.driver_choice import driver_identity
from openavc.audit.observe import MIN_SECRET_LENGTH, audit_traffic_redactor
from openavc.audit.report import (
    SERIAL_REMOVED,
    Redaction,
    Redactor,
    ReportStore,
    _clock,
    _CSS,
    _e,
    _row,
    _serials_in,
    _slug,
    _traffic_text,
    driver_public_strings,
    generator_info,
    place_files,
    utc_offset,
    write_zip,
)
from openavc.core.device_config import connection_of, resolve_device_config
from openavc.core.device_traffic import RING_ENTRIES, get_traffic_recorder, serialize_entry
from openavc.drivers.registry import get_driver_class
from openavc.utils.log_buffer import get_log_buffer
from openavc.utils.log_redaction import (
    REDACTED,
    get_secret_registry,
    is_secret_key,
    mask_secret_fields,
    secret_config_keys,
)
from openavc.utils.logger import get_logger

log = get_logger(__name__)

REPORT_KIND = "problem_report"
REPORT_VERSION = 1
NAME_RE = re.compile(r"^openavc-problem-report-[a-z0-9-]+\.zip$")
HISTORY_ROWS = 1000
LOG_LINES = 200
SYSTEM_KEYS = ("system.version", "system.simulation_active", "system.simulation_error")
SOURCES = ("driver_line", "offline_banner", "command_failure")


def default_store() -> ReportStore:
    from openavc.system_config import get_data_dir

    return ReportStore(get_data_dir() / "problem_reports", name_pattern=NAME_RE)


class DeviceMissing(LookupError):
    """The project has no such device."""


# ---------------------------------------------------------------------------
# What the platform holds about the device now
# ---------------------------------------------------------------------------


class Snapshot:
    """Every source a report reads, taken once."""

    def __init__(self, engine: Any, device_id: str, catalog: list[dict[str, Any]] | None) -> None:
        project = getattr(engine, "project", None)
        device = next(
            (d for d in getattr(project, "devices", None) or [] if d.id == device_id), None,
        )
        if device is None:
            raise DeviceMissing(device_id)
        self.device_id = device_id
        self.name = device.name or device_id
        self.enabled = bool(getattr(device, "enabled", True))
        self.driver_id = device.driver or ""
        try:
            self.record = resolve_device_config(device, project)
        except Exception:
            log.debug("Could not resolve %s for a problem report", device_id, exc_info=True)
            self.record = {"config": dict(getattr(device, "config", None) or {})}
        self.config = dict(self.record.get("config") or {})
        try:
            self.address = connection_of(self.record).address()
        except Exception:
            self.address = {}

        devices = engine.devices
        try:
            self.info = devices.get_device_info(device_id)
        except Exception:
            self.info = {}
        cls = get_driver_class(self.driver_id) if self.driver_id else None
        self.driver_loaded = cls is not None
        info = dict(getattr(cls, "DRIVER_INFO", None) or {})
        self.schema = info.get("config_schema") or {}
        self.commands = info.get("commands") or {}

        identity = driver_identity(self.driver_id, catalog) if self.driver_id else None
        self.contents: dict[str, bytes] = dict((identity or {}).pop("_contents", {}) or {})
        if identity is not None:
            identity.pop("_paths", None)
            entry = next((d for d in catalog or [] if d.get("id") == self.driver_id), None)
            identity["catalog"]["confidence"] = entry.get("confidence") if entry else None
        self.identity = identity

        state = engine.state
        self.state = state.get_namespace(f"device.{device_id}")
        self.system = {key: state.get(key) for key in SYSTEM_KEYS}
        self.history = state.get_history(HISTORY_ROWS, f"device.{device_id}.")
        self.traffic = get_traffic_recorder().get_recent(device_id)
        self.log = get_log_buffer().get_recent(count=LOG_LINES, device=device_id)

        simulation = getattr(engine, "simulation", None)
        self.simulated = bool(simulation is not None and simulation.is_redirected(device_id))
        gap_fn = getattr(devices, "unsimulated_driver", None)
        self.simulator_gap = gap_fn(device_id) if (gap_fn and self.simulated) else None
        installed = getattr(devices, "installed_equipment", None)
        self.installed: dict[str, Any] | None = None
        if installed is not None and installed.owns(device_id):
            details = installed.information(device_id)
            self.installed = {
                "physical_identity": details.get("physical_identity"),
                "integration": details.get("integration"),
            }

    # -- reading ---------------------------------------------------------------

    def serial_numbers(self) -> set[str]:
        """Every serial number this device reported: a value under a name
        with "serial" in it, now or in its history, and product hardware's own."""
        out: set[str] = set()
        _serials_in(self.state, out)
        for row in self.history:
            short = str(row.get("key", ""))[len(f"device.{self.device_id}."):]
            _serials_in({short: row.get("old_value")}, out)
            _serials_in({short: row.get("new_value")}, out)
        if self.installed:
            _serials_in(self.installed, out)
        return {s for s in out if len(s.strip()) >= MIN_SECRET_LENGTH}

    def state_secrets(self) -> set[str]:
        """Values the driver wrote under a credential's name (``auth_token``,
        ``password``), now or in the history: state is the one source the
        platform never masks."""
        out: set[str] = set()
        pairs = [(k, v) for k, v in self.state.items()]
        for row in self.history:
            pairs += [(row.get("key"), row.get("old_value")), (row.get("key"), row.get("new_value"))]
        for key, value in pairs:
            if isinstance(value, str) and value and is_secret_key(str(key).rsplit(".", 1)[-1]):
                out.add(value)
        return out

    def window_start(self) -> float | None:
        return self.traffic[0].t if self.traffic else None

    def reported(self, key: str) -> str | None:
        value = self.state.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None


def _secret_settings(snapshot: Snapshot) -> tuple[dict[str, Any], set[str]]:
    """The settings with credentials masked, and the values masked."""
    keys = secret_config_keys(snapshot.config, snapshot.schema)
    values = {
        snapshot.config[k] for k in keys
        if isinstance(snapshot.config.get(k), str) and snapshot.config[k]
    }
    return mask_secret_fields(snapshot.config, keys), values


def _context(raw: dict[str, Any] | None, commands: dict[str, Any]) -> tuple[dict[str, Any] | None, set[str]]:
    """The page's context with a command's secret parameters masked, and the
    values masked."""
    if not raw:
        return None, set()
    out = {k: v for k, v in raw.items() if v not in (None, "", {})}
    params = raw.get("params")
    secrets: set[str] = set()
    if isinstance(params, dict):
        command = commands.get(str(raw.get("command") or "")) or {}
        defs = command.get("params") or {} if isinstance(command, dict) else {}
        masked: dict[str, Any] = {}
        for key, value in params.items():
            spec = defs.get(key) if isinstance(defs, dict) else None
            secret = (
                (isinstance(spec, dict) and (spec.get("type") == "password" or spec.get("secret")))
                or is_secret_key(key)
            )
            if secret and value not in (None, ""):
                if isinstance(value, str):
                    secrets.add(value)
                masked[key] = REDACTED
            else:
                masked[key] = value
        out["params"] = masked
    return out, secrets


def _names(snapshot: Snapshot) -> set[str]:
    """The driver's published names, never cut into by a secret."""
    out = {snapshot.driver_id}
    identity = snapshot.identity or {}
    for key in ("name", "manufacturer"):
        if isinstance(identity.get(key), str):
            out.add(identity[key])
    for f in identity.get("files") or []:
        name = f.get("name")
        if isinstance(name, str) and name:
            out |= {name, f"driver/{name}"}
    out.discard("")
    return out


# ---------------------------------------------------------------------------
# The preview: what a report would include
# ---------------------------------------------------------------------------


def preview(snapshot: Snapshot) -> dict[str, Any]:
    """What the dialog lists under "What's included", from the same sources
    the report reads."""
    identity = snapshot.identity or {}
    generator = generator_info()
    return {
        "device": {"id": snapshot.device_id, "name": snapshot.name},
        "driver": {
            "id": snapshot.driver_id,
            "name": identity.get("name") or snapshot.driver_id,
            "version": identity.get("version") or "",
            "source": identity.get("source"),
            "modified": identity.get("modified"),
            "listed": (identity.get("catalog") or {}).get("listed"),
            "checked": (identity.get("catalog") or {}).get("checked"),
            "files": [f.get("name") for f in identity.get("files") or []],
            "loaded": snapshot.driver_loaded,
        } if snapshot.driver_id else None,
        "address": snapshot.address,
        "values": len(snapshot.state),
        "changes": len(snapshot.history),
        "messages": len(snapshot.traffic),
        "window_start": snapshot.window_start(),
        "log_lines": len(snapshot.log),
        "serial_found": bool(snapshot.serial_numbers()),
        "simulated": snapshot.simulated,
        "openavc_version": generator["openavc_version"],
        "os": generator["os"],
        "deployment_type": generator["deployment_type"],
    }


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _limits(snapshot: Snapshot) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not snapshot.driver_id or not snapshot.driver_loaded:
        out.append({
            "id": "no_driver",
            "text": "No driver is loaded for this device, so the report holds no driver file.",
        })
    elif not (snapshot.identity or {}).get("catalog", {}).get("checked"):
        out.append({
            "id": "catalog_unreachable",
            "text": "The driver library could not be reached, so whether the driver "
                    "file matches the library's copy is unknown.",
        })
    if not snapshot.traffic:
        out.append({
            "id": "no_traffic",
            "text": "No messages to or from the device were recorded since it was last started.",
        })
    elif len(snapshot.traffic) >= RING_ENTRIES:
        out.append({
            "id": "traffic_window",
            "text": f"Only the most recent {RING_ENTRIES} messages are kept.",
        })
    return out


def build_report(
    snapshot: Snapshot,
    *,
    message: str = "",
    name: str = "",
    email: str = "",
    leave_out_serial: bool = False,
    context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Redactor]:
    """The whole record, redacted, and the redactor that redacted it."""
    settings, setting_secrets = _secret_settings(snapshot)
    page_context, param_secrets = _context(context, snapshot.commands)
    secrets = (
        set(get_secret_registry().secrets_for(snapshot.device_id))
        | setting_secrets | param_secrets | snapshot.state_secrets()
    )
    redactions = [Redaction(v) for v in sorted(secrets) if isinstance(v, str) and v]
    if leave_out_serial:
        redactions += [Redaction(s, SERIAL_REMOVED) for s in sorted(snapshot.serial_numbers())]
    redactor = Redactor(
        redactions, driver_public_strings(snapshot.driver_id, snapshot.identity), _names(snapshot),
    )
    traffic_redactor = audit_traffic_redactor(secrets)

    record = {
        "created_at": time.time(),
        "generator": generator_info(),
        "sender": {
            "message": message.strip(),
            "name": name.strip(),
            "email": email.strip(),
            "leave_out_serial": bool(leave_out_serial),
        },
        "context": page_context,
        "device": {
            "id": snapshot.device_id,
            "name": snapshot.name,
            "enabled": snapshot.enabled,
            "connected": snapshot.state.get("connected"),
            "manufacturer": snapshot.reported("manufacturer"),
            "model": snapshot.reported("model"),
            "driver_identity": snapshot.identity,
            "settings": settings,
            "address": snapshot.address,
            "transport": snapshot.address.get("transport"),
            "simulated": snapshot.simulated,
            "simulator_gap": snapshot.simulator_gap,
            "orphaned": bool(snapshot.info.get("orphaned")),
            "orphan_reason": snapshot.info.get("orphan_reason"),
            "installed": snapshot.installed,
        },
        "state": {"device": snapshot.state, "system": snapshot.system},
        "history": snapshot.history,
        "traffic": {
            "window_start": snapshot.window_start(),
            "count": len(snapshot.traffic),
            "entries": [serialize_entry(e, traffic_redactor) for e in snapshot.traffic],
        },
        "log": snapshot.log,
        "limits": _limits(snapshot),
    }
    record = redactor.tree(record)
    # Put in after redaction, so no secret can cut into the id.
    report = {
        "report_version": REPORT_VERSION,
        "report_kind": REPORT_KIND,
        "report_id": str(uuid.uuid4()),
        **record,
    }
    return report, redactor


def report_filename(report: dict[str, Any]) -> str:
    """``openavc-problem-report-<driver id>-<YYYYMMDD-HHMM>.zip``."""
    identity = (report.get("device") or {}).get("driver_identity") or {}
    driver = _slug(identity.get("id") or (report.get("device") or {}).get("id"), "device")
    stamp = datetime.fromtimestamp(report["created_at"]).strftime("%Y%m%d-%H%M")
    return f"openavc-problem-report-{driver}-{stamp}.zip"


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------


def _since(t: float | None) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M") if t else ""


def render_traffic(report: dict[str, Any]) -> str:
    traffic = report.get("traffic") or {}
    device = report.get("device") or {}
    entries = traffic.get("entries") or []
    start = traffic.get("window_start")
    lines = [
        f"Messages to and from {device.get('name')} ({device.get('id')}), oldest first.",
        f"Times are this computer's local time, {utc_offset(start)}.",
        f"Since {_clock(start)}: the device's last start, or as far back as the "
        f"last {RING_ENTRIES} messages go." if start else "No messages were recorded.",
        "",
    ]
    for entry in entries:
        direction = "TX" if entry.get("direction") == "tx" else "RX"
        lines.append(
            f"{_clock(entry.get('t'))}  {direction}  {entry.get('channel', '')}  "
            f"{_traffic_text(entry, limit=2000)}"
        )
    return "\n".join(lines) + "\n"


def render_log(report: dict[str, Any]) -> str:
    rows = report.get("log") or []
    first = rows[0].get("timestamp") if rows else None
    lines = [f"Times are this computer's local time, {utc_offset(first)}.", ""]
    if not rows:
        lines.append("OpenAVC wrote no log lines about this device.")
    for row in rows:
        lines.append(
            f"{_clock(row.get('timestamp'))} {row.get('level', '')} "
            f"{row.get('source', '')}: {row.get('message', '')}"
        )
    return "\n".join(lines) + "\n"


_SOURCE_WORDS = {"built_in": "built into OpenAVC", "catalog": "from the driver library",
                 "imported": "imported on this system"}


def render_summary(report: dict[str, Any]) -> str:
    """``summary.html``: the report for a person, self-contained, no scripts."""
    device = report.get("device") or {}
    sender = report.get("sender") or {}
    context = report.get("context") or {}
    identity = device.get("driver_identity") or {}
    generator = report.get("generator") or {}
    traffic = report.get("traffic") or {}
    title = device.get("name") or device.get("id") or "Device"
    created = report.get("created_at")
    when = (
        datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") + f" {utc_offset(created)}"
        if created else ""
    )
    unchecked = identity.get("source") == "imported" and not (identity.get("catalog") or {}).get("checked")
    source_words = (
        # Without the library to compare against, a library driver reads as imported.
        "not checked: the driver library could not be reached" if unchecked
        else _SOURCE_WORDS.get(identity.get("source"), identity.get("source"))
    )
    address = device.get("address") or {}
    where = ":".join(str(p) for p in (address.get("host"), address.get("port")) if p not in (None, ""))
    parts = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>Problem report: {_e(title)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>Problem report: {_e(title)}</h1>",
        f"<p class=\"meta\">{_e(device.get('id'))} &middot; {_e(when)}</p>",
        "<h2>What happened</h2>",
        f"<pre>{_e(sender.get('message'))}</pre>" if sender.get("message")
        else "<p class=\"meta\">The person left no message.</p>",
    ]
    if context:
        rows = [_row("Reported from", _e(context.get("source")))]
        if context.get("error"):
            rows.append(_row("The error on screen", _e(context["error"])))
        if context.get("command"):
            rows.append(_row("Command", f"<code>{_e(context['command'])}</code>"))
            if context.get("params"):
                rows.append(_row("Parameters", f"<code>{_e(context['params'])}</code>"))
            if context.get("status"):
                rows.append(_row("Status", _e(context["status"])))
        parts += ["<table>", *rows, "</table>"]
    contact = ", ".join(p for p in (sender.get("name"), sender.get("email")) if p)
    parts += [
        "<h2>Device</h2><table>",
        _row("Driver", f"{_e(identity.get('name') or identity.get('id'))} "
             f"{_e(identity.get('version'))} ({_e(source_words)}"
             f"{', edited' if identity.get('modified') else ''})" if identity else "none loaded"),
        _row("Address", _e(f"{address.get('transport', '')} {where}".strip())),
        _row("Connected", _e(device.get("connected"))),
        _row("Simulated", "yes" if device.get("simulated") else "no"),
        _row("OpenAVC", _e(f"{generator.get('openavc_version')} on {generator.get('os')}, "
                           f"{generator.get('deployment_type')}")),
        _row("Sent by", _e(contact) if contact else "no name or email left"),
        "</table>",
        "<h2>Settings</h2><table>",
        *(_row(k, f"<code>{_e(v)}</code>") for k, v in (device.get("settings") or {}).items()),
        "</table>",
        "<h2>Current values</h2><table>",
        *(_row(k, f"<code>{_e(v)}</code>") for k, v in ((report.get("state") or {}).get("device") or {}).items()),
        "</table>",
        "<h2>Messages</h2>",
        f"<p>{_e(traffic.get('count', 0))} messages"
        + (f" since {_e(_since(traffic.get('window_start')))}" if traffic.get("window_start") else "")
        + ", in <code>traffic.txt</code>. OpenAVC's log lines about the device are in "
        "<code>log.txt</code>.</p>",
    ]
    limits = report.get("limits") or []
    if limits:
        parts += ["<h2>What the report could not include</h2><ul>",
                  *(f"<li>{_e(item.get('text'))}</li>" for item in limits), "</ul>"]
    parts += [
        f"<footer>report_id {_e(report.get('report_id'))}. The complete record is "
        "report.json; README.txt explains its fields.</footer></main></body></html>",
    ]
    return "\n".join(parts)


FIELD_GUIDE = """\
How to read this problem report

summary.html is the report for a person. report.json is the complete record,
and this file explains the fields in it that are not plain from their names.

What it covers
  A problem report is a snapshot of what OpenAVC had already recorded about
  one device when the person pressed Report a problem: nothing was sent to
  the device to make it. The messages, values and changes cover the time
  since the device was last started (a saved settings change or a driver
  reload starts it again); traffic.window_start is the oldest message's time.

Times
  A time in report.json is seconds since 1970-01-01 UTC. The clock times in
  traffic.txt and log.txt are the local time of the computer that built the
  report; their first lines give its offset from UTC.

Bytes
  traffic.entries keeps each message as {"hex", "text"}: hex is the bytes
  unspaced (aa0b01), text the same bytes decoded as Latin-1, so every byte
  survives. chunk: true marks raw bytes as received, before framing.

Masked values
  "***" is a credential or a secret parameter. "[redacted]" is a value typed
  as a secret, wherever it appeared. "[serial number removed]" is a serial
  number the person asked to leave out.

context
  Where the person pressed Report a problem: driver_line (the link under the
  device's name), offline_banner (the offline banner) or command_failure (a
  command that failed), with the error the page showed. status is the HTTP
  status the command answered with.

device.driver_identity
  files lists each driver file with its SHA-256 and the driver library's
  (catalog_sha256); matches_catalog is null when the library has no copy or
  could not be reached. modified is true when a file differs from the
  library's.

history
  This device's value changes, oldest first: key, old_value, new_value, source
  (what wrote it) and timestamp. A device that was started again has every
  value removed first, each a row with new_value null.
"""


def build_zip(report: dict[str, Any], redactor: Redactor, snapshot: Snapshot) -> bytes:
    files = {
        "summary.html": render_summary(report),
        "report.json": json.dumps(report, indent=2, ensure_ascii=False, default=str),
        "traffic.txt": render_traffic(report),
        "log.txt": render_log(report),
        "README.txt": FIELD_GUIDE,
    }
    return write_zip(files, place_files(snapshot.identity, snapshot.contents, redactor))


def save_report(
    snapshot: Snapshot, store: ReportStore, **sender: Any,
) -> dict[str, Any]:
    """Build one report and keep it; ``{name, report_id, size_bytes}``."""
    report, redactor = build_report(snapshot, **sender)
    data = build_zip(report, redactor, snapshot)
    name = store.save(report_filename(report), data)
    return {"name": name, "report_id": report["report_id"], "size_bytes": len(data)}
