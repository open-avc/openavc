"""The device audit report: one zip a person can email.

::

    openavc-device-audit-<manufacturer>-<model>-<YYYYMMDD-HHMM>.zip
      summary.html     readable summary, self-contained, no scripts
      report.json      the complete record
      timeline.txt     every event in order, human-readable

``report.json`` (``report_version`` 1) holds:

- ``report_version``; ``generator``: the OpenAVC version, the OS, the Python
  version and how OpenAVC was installed.
- ``session``: id, start and end, status, the steps run, and the tester's
  name, company, email and notes (all optional).
- ``target``: the address as typed, the address it resolved to, the reverse
  DNS name, whether it is on one of this computer's subnets, the local
  address and adapter the check used, and the serial port (none yet).
- ``device``: manufacturer, model and firmware as entered (none yet), and the
  identity the device reported (``reported``).
- ``catalog``: where the driver catalog came from, when it was fetched, the
  SHA-256 of its ``index.json``, its driver count, and whether it was
  fetched fresh, taken from an earlier fetch, or not available at all.
- ``footprint``: every raw observation of the network check. Bytes appear as
  ``{"hex", "text"}`` with the text decoded latin-1, so every byte survives.
- ``evidence``: the discovery Evidence records built from those observations.
- ``verdict``: the matcher's identification, every driver each signal points
  at, and each named driver's declared signals judged against the device.
- ``drivers``: one entry per driver tested (none yet).
- ``timeline``: every session event in order, typed and timestamped.
- ``limits``: what the audit could not see, and why.
- ``complete``: false when the report was taken before the network check
  finished.

**Redaction.** Every secret the person typed (a read community other than
``public``) is replaced in every file, in its plain, JSON, HTML and hex forms,
before anything is written; a serial number the person asked to leave out is
replaced the same way. The report never reads the server's own configuration.
A secret shorter than three characters is not searched for, since it would
match ordinary text everywhere; the report never writes a typed secret into a
field of its own, so such a value could only appear if the device repeated it.

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
_MIN_SECRET_LENGTH = 3
_NAME_RE = re.compile(r"^openavc-device-audit-[a-z0-9-]+\.zip$")


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Redaction:
    """One value to take out of a report, and what to put in its place."""

    value: str
    replacement: str = REDACTED


def _forms(redaction: Redaction) -> list[tuple[str, str]]:
    """Each way ``value`` can be written in a report, with its replacement."""
    value, repl = redaction.value, redaction.replacement
    pairs = [
        (value, repl),
        (json.dumps(value)[1:-1], json.dumps(repl)[1:-1]),
        (html.escape(value), html.escape(repl)),
        (value.encode("utf-8").hex(), repl.encode("utf-8").hex()),
        (value.encode("utf-8").hex().upper(), repl.encode("utf-8").hex().upper()),
    ]
    seen: dict[str, str] = {}
    for form, replacement in pairs:
        if form and form not in seen:
            seen[form] = replacement
    return sorted(seen.items(), key=lambda kv: -len(kv[0]))


class Redactor:
    """Replaces each secret in a text, whatever form it was written in."""

    def __init__(self, redactions: list[Redaction]) -> None:
        self._pairs: list[tuple[str, str]] = []
        for redaction in redactions:
            if len(redaction.value) < _MIN_SECRET_LENGTH:
                continue
            self._pairs.extend(_forms(redaction))
        self._pairs.sort(key=lambda kv: -len(kv[0]))

    def text(self, value: str) -> str:
        for form, replacement in self._pairs:
            if form in value:
                value = value.replace(form, replacement)
        return value

    def tree(self, value: Any) -> Any:
        """``value`` with every string in it redacted (dict keys included)."""
        if isinstance(value, str):
            return self.text(value)
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
    return out


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def _footprint_of(session: "AuditSession"):
    if session.footprint is not None:
        return session.footprint
    check = getattr(session, "check", None)
    return check.footprint if check is not None else None


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
    limits.append({
        "id": "no_driver_tested",
        "text": "No driver was tested; this report covers the network check only.",
    })

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
            "entered": {"manufacturer": None, "model": None, "firmware": None},
            "reported": {
                key: device.get(key)
                for key in (
                    "manufacturer", "model", "firmware", "serial_number",
                    "device_name", "hostname", "mac",
                )
            },
        },
        "catalog": (fp.get("verdict") or {}).get("catalog", {}),
        "footprint": raw_footprint,
        "evidence": fp.get("evidence", []),
        "verdict": {k: v for k, v in (fp.get("verdict") or {}).items() if k != "catalog"},
        "drivers": [],
        "timeline": [entry.to_dict() for entry in session.timeline],
        "limits": limits,
    }
    return Redactor(redactions_for(session)).tree(report)


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


def _quote(view: dict[str, str] | None, limit: int = 160) -> str:
    """A byte view as a reader sees it: text when printable, else hex."""
    if not view:
        return "nothing"
    text = view.get("text", "")
    if not text:
        return "nothing"
    printable = all(c.isprintable() or c in "\r\n\t" for c in text)
    shown = text.encode("unicode_escape").decode("ascii") if printable else "hex " + view.get("hex", "")
    if len(shown) > limit:
        shown = shown[:limit] + "..."
    return f'"{shown}"' if printable else shown


def render_timeline(report: dict[str, Any]) -> str:
    """``timeline.txt``: the session's events and the check's exchanges, in order."""
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
    rows.sort(key=lambda row: row[0])
    target = report.get("target", {})
    started = report.get("session", {}).get("started_at")
    header = [
        f"OpenAVC device audit of {target.get('address')} ({target.get('ip') or 'unresolved'})",
        f"Started {datetime.fromtimestamp(started).isoformat(timespec='seconds') if started else '?'}"
        f", OpenAVC {report.get('generator', {}).get('openavc_version')}",
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

    title_bits = [reported.get("manufacturer"), reported.get("model")]
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
    if signals:
        items = []
        for sig in signals:
            drivers = [str(names.get(d, {}).get("name") or d) for d in sig.get("drivers", [])]
            strength = "strong" if sig.get("strong") else "hint"
            items.append(
                f"<li><code>{_e(sig.get('source'))}</code> ({strength}): "
                f"{_e(_join(drivers)) if drivers else 'no driver claims it'}</li>"
            )
        parts.append("<h3>Why</h3><ul>" + "".join(items) + "</ul>")
    parts.append(
        f"<p class=\"meta\">Checked against {_e(catalog.get('driver_count'))} catalog drivers"
        f" ({_e({'fresh': 'fetched for this audit', 'cached': 'from an earlier fetch', 'none': 'catalog unavailable'}.get(catalog.get('used'), ''))})"
        f"{', index.json SHA-256 ' + _e(catalog.get('sha256')) if catalog.get('sha256') else ''}.</p>"
    )

    # Identity.
    parts.append("<h2>What the device reported</h2><table>")
    for key, label in (
        ("manufacturer", "Manufacturer"), ("model", "Model"), ("firmware", "Firmware"),
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
        f"{mac.get('address')} (from {mac.get('source')})" if mac.get("address") else "not read"
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
        parts.append("<h3>What each open port said, unprompted</h3>")
        if spoke:
            for port, g in spoke.items():
                parts.append(f"<p>Port {_e(port)}:</p><pre>{_e(_quote(g, 600))}</pre>")
        else:
            parts.append("<p>No open port sent anything before being spoken to.</p>")

    web = fp.get("web", {})
    if web:
        parts.append("<h3>Web pages</h3><table>")
        for port, page in web.items():
            bits = [page.get("status_line") or page.get("error") or "no answer"]
            if page.get("title"):
                bits.append(f"title \"{page['title']}\"")
            if page.get("server"):
                bits.append(f"server {page['server']}")
            if page.get("www_authenticate"):
                bits.append(f"asks for sign-in: {page['www_authenticate']}")
            if page.get("location"):
                bits.append(f"redirects to {page['location']}")
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
    mdns = fp.get("mdns")
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
            ("server", "Server header"), ("location", "Description at"),
        ):
            if ssdp.get(key):
                parts.append(_row(label, _e(ssdp[key])))
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
        "<footer>The complete record is report.json, and every event in order is "
        "timeline.txt, both in the same file as this page.</footer></main></body></html>"
    )
    return "".join(parts)


def build_zip(report: dict[str, Any], redactor: Redactor | None = None) -> bytes:
    """The report zip. ``redactor`` runs once more over every file's text."""
    files = {
        "summary.html": render_summary(report),
        "report.json": json.dumps(report, indent=2, ensure_ascii=False, default=str),
        "timeline.txt": render_timeline(report),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            if redactor is not None:
                text = redactor.text(text)
            zf.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


def report_zip(session: "AuditSession") -> tuple[str, bytes]:
    """(file name, zip bytes) for ``session`` as it stands now."""
    report = build_report(session)
    name = report_filename(report)
    return name, build_zip(report, Redactor(redactions_for(session)))


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
