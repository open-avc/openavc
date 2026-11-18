"""The report contract: what a problem report and a device audit report promise their readers.

A report leaves this system as a zip whose ``report.json`` is read by others:
the service a Send reaches (``config.REPORT_URL``), which files it and shows it
to OpenAVC's staff, and OpenAVC's own reading of a device audit into driver
work. This module is the one place that says, for each kind of report:

* its ``report_version``, and every version before it with the release it
  started in;
* the fields a reader may rely on: every top-level field, and each nested
  field a reader reads, with its type, whether the builder always writes it
  (when what holds it is there), the release it arrived in and who reads it;
* the size caps a report is held to on both ends.

``audit/report.py`` and ``support/problem_report.py`` stamp the version from
here and ``support/report_upload.py`` refuses by these caps. ``python -m
openavc.cloud.contract_gen`` renders the same rows into ``wire_contract.json``
(its ``reports`` section, with an example of each kind), which the receiving
service copies and reads by.

**What bumps** ``report_version``: a declared field that arrives, goes,
changes type or stops being always written, and a new top-level field. A
field nobody reads may come and go without one: no reader can tell. The
fields' shape is pinned per version by ``shape_digest``, so a table edit
without a bump fails its test, and the builders' output is held to the table,
so a builder that drops, renames or retypes a declared field fails too.

**A field's path**: names joined by ``.``; ``[]`` after a name means each
item of that list; ``*`` stands for any key of a map (a port, a driver id).
What holds a field is implied by its path, so ``drivers[].driver.id`` says
``drivers`` is a list of objects each holding a ``driver`` object.

**A field's** ``since`` is the first release whose reports carried it; one
present in the kind's first release keeps the kind's own. Whoever adds a field
writes the release it will ship in. Version 1 named no shape: it was stamped
from 0.36.0 to 0.40.0 while fields arrived, so a version 1 report is read
field by field, each by its ``since``.

Pure data and stdlib, so it imports anywhere.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

#: The service a Send reaches, which files a report and shows it to staff.
RECEIVER = "receiver"
#: OpenAVC's own reading of a device audit into driver work.
TRIAGE = "triage"
READERS = (RECEIVER, TRIAGE)

#: The largest report zip either end takes.
MAX_REPORT_BYTES = 20 * 1024 * 1024
#: The largest ``report.json`` inside it, read only this far.
MAX_REPORT_JSON_BYTES = 5 * 1024 * 1024

LIMITS: dict[str, tuple[int, str]] = {
    "max_report_bytes": (MAX_REPORT_BYTES, "The largest report zip either end takes."),
    "max_report_json_bytes": (
        MAX_REPORT_JSON_BYTES, "The largest report.json inside it; read only this far.",
    ),
}

#: The first release whose reports are versioned with a recorded shape.
FIRST_RECORDED_RELEASE = "0.41.0"


class _Absent:
    def __repr__(self) -> str:
        return "ABSENT"


#: No example of its own: the example report takes the type's plain value.
ABSENT: Any = _Absent()


@dataclass(frozen=True)
class ReportField:
    """One field a reader may rely on.

    ``type`` is a JSON type name ("string", "integer", "number", "boolean",
    "object", "array", "null"), several joined with ``|``, or ``any``.
    ``required``: the builder always writes it when what holds it is there
    (its value may still be null where the type allows). ``readers`` names who
    reads it; a top-level field nobody reads names none.
    """

    path: str
    type: str
    readers: tuple[str, ...] = ()
    since: str | None = None
    required: bool = True
    doc: str = ""
    example: Any = ABSENT


@dataclass(frozen=True)
class ReportVersion:
    """One ``report_version`` of a kind: the release it started in, and the
    digest of its fields' shape (none for version 1, which recorded none)."""

    version: int
    since: str
    doc: str
    shape: str | None


@dataclass(frozen=True)
class ReportKind:
    kind: str
    since: str
    doc: str
    versions: tuple[ReportVersion, ...]
    fields: tuple[ReportField, ...]

    @property
    def report_version(self) -> int:
        """The version this release's builder stamps: the newest."""
        return self.versions[-1].version


_R, _T, _RT = (RECEIVER,), (TRIAGE,), (RECEIVER, TRIAGE)


def _f(path: str, type_: str, readers: tuple[str, ...] = (), **kw: Any) -> ReportField:
    return ReportField(path, type_, readers, **kw)


# --- Fields both kinds carry ---

def _envelope(kind: str) -> tuple[ReportField, ...]:
    return (
        _f("report_version", "integer", _RT, doc="Which shape of this kind the file is.",
           example=None),
        _f("report_kind", "string", _RT, since="0.38.0", doc="What made the file.",
           example=kind),
        _f("report_id", "string", _RT, since="0.38.0",
           doc="A UUID new with every file built; the receiver keeps one report per id.",
           example="2f6d0b8e-5a41-4c2e-9e1f-7b3a9c0d4e51"),
        _f("generator", "object", _RT, doc="The OpenAVC that built the file."),
        _f("generator.openavc_version", "string", _RT, example="0.41.0"),
        _f("generator.os", "string", _RT, example="Linux-6.8.0-x86_64-with-glibc2.39"),
        _f("generator.deployment_type", "string", _RT, doc="How OpenAVC was installed.",
           example="linux"),
        _f("generator.python", "string", example="3.12.3"),
        _f("limits", "array", _T, doc="What the report could not include, and why."),
        _f("limits[].id", "string", example="catalog_unreachable"),
        _f("limits[].text", "string", _T, example="The driver library could not be reached."),
    )


def _driver_identity(at: str) -> tuple[ReportField, ...]:
    """Exactly which driver ran, as both kinds write it (``audit.driver_choice``)."""
    return (
        _f(at, "object", _RT, doc="Exactly which driver: its files hashed against the "
           "driver library's."),
        _f(f"{at}.id", "string", _RT, example="acme_widget"),
        _f(f"{at}.name", "string", example="Acme Widget"),
        _f(f"{at}.manufacturer", "string", _R, example="Acme"),
        _f(f"{at}.version", "string", _RT, example="1.2.0"),
        _f(f"{at}.format", "string", _T, example="avcdriver"),
        _f(f"{at}.source", "string", _T, example="catalog"),
        _f(f"{at}.modified", "boolean|null", _RT, example=None,
           doc="True when a file differs from the library's, false when every file "
               "matches, null when nothing was compared."),
        _f(f"{at}.files", "array", _RT),
        _f(f"{at}.files[].name", "string", example="acme_widget.avcdriver"),
        _f(f"{at}.files[].matches_catalog", "boolean|null", _RT, example=None),
        _f(f"{at}.catalog", "object", _RT),
        _f(f"{at}.catalog.version", "string", _R, example="1.2.0"),
        _f(f"{at}.catalog.checked", "boolean", _RT, example=True,
           doc="Whether the driver library was reached at all."),
    )


# --- A problem report ---

_PROBLEM_FIELDS = (
    *_envelope("problem_report"),
    _f("created_at", "number", _T, doc="Seconds since 1970 UTC.", example=1794967158.0),
    _f("sender", "object", _RT, doc="What the person typed in the dialog, all optional."),
    _f("sender.message", "string", _RT, example="It stopped answering after the update."),
    _f("sender.name", "string", _RT, example="Pat Example"),
    _f("sender.email", "string", _RT, example="pat@example.com"),
    _f("sender.leave_out_serial", "boolean", example=False),
    _f("context", "object", _T, doc="Where the press came from, as the page sent it."),
    _f("context.source", "string", _T, example="driver_line"),
    _f("device", "object", _RT, doc="The one device the report is about."),
    _f("device.id", "string", _T, example="hall_widget"),
    _f("device.name", "string", _T, example="Hall Widget"),
    _f("device.enabled", "boolean", _T, example=True),
    _f("device.transport", "string|null", _T, example="tcp"),
    _f("device.address", "object|null", _T, doc="Where it really connects, from the saved record.",
       example={"host": "10.0.0.50", "port": 23}),
    _f("device.simulated", "boolean", _T, example=False),
    _f("device.manufacturer", "string|null", _R, doc="As the device reported it.",
       example="Acme"),
    _f("device.model", "string|null", _R, doc="As the device reported it.", example="Widget 3000"),
    *_driver_identity("device.driver_identity"),
    _f("state", "object", _T, doc="The device's values now, and three of the system's."),
    _f("state.device", "object", _T),
    _f("state.system", "object"),
    _f("history", "array", _T, doc="This device's rows of the state history."),
    _f("traffic", "object", _T, doc="The device's recent messages."),
    _f("traffic.window_start", "number|null", _T, example=None),
    _f("traffic.count", "integer", example=0),
    _f("traffic.entries", "array"),
    _f("log", "array", _T, doc="OpenAVC's recent log lines about the device."),
)


# --- A device audit report ---

_SINCE_FOLLOWS = "0.38.0"
_SINCE_FOLLOWED = "0.39.0"

_AUDIT_FIELDS = (
    *_envelope("device_audit"),
    _f("complete", "boolean", _T, doc="Whether the network check finished.", example=True),
    _f("session", "object", _RT),
    _f("session.started_at", "number", _T, example=1794967158.0),
    _f("session.status", "string", _T, example="active"),
    _f("session.tester", "object", _RT, doc="\"About you\" on the report step, all optional."),
    _f("session.tester.name", "string", _RT, required=False, example="Pat Example"),
    _f("session.tester.company", "string", _RT, required=False, example="Acme AV"),
    _f("session.tester.email", "string", _RT, required=False, example="pat@example.com"),
    _f("session.tester.notes", "string", _RT, required=False,
       example="Bench unit, latest firmware."),
    _f("session.follows_report", "object|null", _T, since=_SINCE_FOLLOWS,
       doc="The problem report sent just before, when the audit was started from it.",
       example=None),
    _f("target", "object", _T),
    _f("target.address", "string", _T, example="10.0.0.50"),
    _f("target.ip", "string|null", _T, example="10.0.0.50"),
    _f("target.same_subnet", "boolean|null", _T, example=True),
    _f("device", "object", _RT),
    _f("device.entered", "object", _RT, doc="As the person typed it on \"Which driver?\"."),
    _f("device.entered.manufacturer", "string|null", _RT, example="Acme"),
    _f("device.entered.model", "string|null", _RT, example="Widget 3000"),
    _f("device.entered.firmware", "string|null", _T, example=None),
    _f("device.reported", "object", _RT, doc="As the device reported itself."),
    _f("device.reported.manufacturer", "string|null", _RT, example="Acme"),
    _f("device.reported.model", "string|null", _RT, example="Widget 3000"),
    _f("catalog", "object", _T, doc="The driver catalog the audit was checked against."),
    _f("catalog.used", "string", _T, required=False, example="fresh"),
    _f("catalog.driver_count", "integer", _T, required=False, example=1),
    _f("catalog.sha256", "string|null", _T, required=False, example=None),
    _f("footprint", "object", _T, doc="Every raw observation of the network check."),
    _f("footprint.mac", "object", _T),
    _f("footprint.mac.address", "string|null", _T, example=None),
    _f("footprint.ports", "object", _T),
    _f("footprint.ports.open", "array", _T),
    _f("footprint.ports.refused", "array", _T),
    _f("footprint.ports.filtered", "array", _T),
    _f("footprint.greetings", "object", _T, doc="What each open port sent first, by port."),
    _f("footprint.greetings.*.hex", "string", _T),
    _f("footprint.web", "object", _T, doc="Each port's answer to GET /, by port."),
    _f("footprint.web.*.url", "string", _T),
    _f("footprint.web.*.status_line", "string", _T),
    _f("footprint.web.*.headers", "array", _T),
    _f("footprint.web.*.title", "string|null", _T),
    _f("footprint.web.*.server", "string|null", _T),
    _f("footprint.web.*.www_authenticate", "string|null", _T),
    _f("footprint.web.*.body", "object", _T),
    _f("footprint.web.*.body.hex", "string", _T),
    _f("footprint.web.*.followed", "object|null", _T, since=_SINCE_FOLLOWED,
       doc="The page a redirect on the same address and port led to."),
    _f("footprint.certificates", "object", _T, doc="Each TLS port's certificate, by port."),
    _f("footprint.certificates.*.subject", "any", _T),
    _f("footprint.mdns", "object|null", _T, example=None),
    _f("footprint.mdns.hostname", "string|null", _T),
    _f("footprint.mdns.address_name", "string|null", _T),
    _f("footprint.mdns.services", "array", _T),
    _f("footprint.mdns.services[].service_type", "string", _T),
    _f("footprint.mdns.services[].txt", "object", _T),
    _f("footprint.ssdp", "object|null", _T),
    _f("footprint.ssdp.device_types", "array", _T),
    _f("footprint.ssdp.manufacturer", "string|null", _T, required=False),
    _f("footprint.ssdp.model_name", "string|null", _T, required=False),
    _f("footprint.amx_ddp", "any", _T, example=None),
    _f("evidence", "array", _T, doc="The discovery Evidence built from those observations."),
    _f("evidence[].tier", "string", _T),
    _f("evidence[].source", "string", _T),
    _f("evidence[].data", "object", _T),
    _f("evidence[].at", "number", _T),
    _f("verdict", "object", _T, doc="The matcher's identification and each named driver's "
       "signals judged against the device."),
    _f("verdict.sentence", "string", _T, example="OpenAVC does not recognize this device."),
    _f("verdict.explanation", "object", _T),
    _f("verdict.explanation.signals", "array", _T),
    _f("verdict.explanation.signals[].source", "string", _T),
    _f("verdict.explanation.signals[].drivers", "array", _T),
    _f("verdict.checks", "object", _T, doc="Each named driver's declared signals, by driver id."),
    _f("verdict.checks.*[].kind", "string", _T),
    _f("verdict.checks.*[].declared", "string", _T),
    _f("verdict.checks.*[].status", "string", _T),
    _f("verdict.checks.*[].detail", "string", _T),
    _f("verdict.checks.*[].probes", "array", _T, required=False),
    _f("verdict.checks.*[].probes[].matched", "boolean", _T),
    _f("verdict.checks.*[].probes[].reply", "object|null", _T),
    _f("verdict.checks.*[].probes[].reply.hex", "string", _T),
    _f("drivers", "array", _RT, doc="One entry per driver chosen, in order."),
    *_driver_identity("drivers[].driver"),
    _f("drivers[].verdict_agreement", "string", _T),
    _f("drivers[].attempts", "array", _T, doc="Every connect and listen, in order."),
    _f("drivers[].attempts[].status", "string", _T),
    _f("drivers[].attempts[].started_at", "number|null", _T),
    _f("drivers[].attempts[].connected_at", "number|null", _T),
    _f("drivers[].attempts[].drops", "integer", _T),
    _f("drivers[].attempts[].declared", "integer", _T),
    _f("drivers[].attempts[].reported", "integer", _T),
    _f("drivers[].attempts[].status_table", "object", _T),
    _f("drivers[].attempts[].status_table.variables", "array", _T),
    _f("drivers[].attempts[].status_table.variables[].name", "string", _T),
    _f("drivers[].attempts[].status_table.variables[].value", "any", _T),
    _f("drivers[].attempts[].status_table.variables[].reported", "boolean", _T),
    _f("drivers[].attempts[].contract", "object", _T),
    _f("drivers[].attempts[].contract.counts", "object", _T),
    _f("drivers[].attempts[].traffic", "object", _T),
    _f("drivers[].attempts[].traffic.count", "integer", _T),
    _f("drivers[].attempts[].traffic.sent", "integer", _T),
    _f("drivers[].attempts[].traffic.received", "integer", _T),
    _f("drivers[].attempts[].traffic.not_captured", "boolean", _T),
    _f("drivers[].attempts[].traffic.entries", "array", _T),
    _f("drivers[].attempts[].traffic.entries[].seq", "integer", _T),
    _f("drivers[].attempts[].traffic.entries[].t", "number", _T),
    _f("drivers[].attempts[].traffic.entries[].direction", "string", _T),
    _f("drivers[].attempts[].traffic.entries[].hex", "string", _T),
    _f("drivers[].attempts[].traffic.entries[].chunk", "boolean", _T, required=False,
       doc="A raw receive chunk before framing."),
    _f("drivers[].commands", "object|null", _T, doc="Null when the driver never connected."),
    _f("drivers[].commands.trials", "array", _T),
    _f("drivers[].commands.trials[].number", "integer", _T),
    _f("drivers[].commands.trials[].command", "string", _T),
    _f("drivers[].commands.trials[].params", "object", _T),
    _f("drivers[].commands.trials[].connect_attempt", "integer|null", _T),
    _f("drivers[].commands.trials[].error", "string", _T),
    _f("drivers[].commands.trials[].summary", "string", _T),
    _f("drivers[].commands.trials[].answer", "object|null", _T),
    _f("drivers[].commands.trials[].answer.answer", "string", _T),
    _f("drivers[].commands.trials[].traffic", "object", _T),
    _f("drivers[].commands.trials[].traffic.entries", "array", _T),
    _f("drivers[].commands.trials[].traffic.entries[].seq", "integer", _T),
    _f("drivers[].commands.trials[].traffic.entries[].direction", "string", _T),
    _f("drivers[].commands.trials[].traffic.entries[].hex", "string", _T),
    _f("drivers[].outages", "array", _T),
    _f("drivers[].outages[].kind", "string", _T),
    _f("drivers[].outages[].summary", "string", _T),
    _f("drivers[].settings", "object", _T),
    _f("drivers[].settings.trials", "array", _T),
    _f("drivers[].settings.trials[].key", "string", _T),
    _f("drivers[].settings.trials[].summary", "string", _T),
    _f("timeline", "array", doc="Every event in order."),
)


KINDS: dict[str, ReportKind] = {
    "problem_report": ReportKind(
        "problem_report", "0.38.0", "One device's diagnostics, sent from its page.",
        versions=(
            ReportVersion(1, "0.38.0", "Stamped while fields arrived; no shape recorded.", None),
            ReportVersion(2, FIRST_RECORDED_RELEASE, "The first recorded shape.",
                          "19ae8fad9a1cd523"),
        ),
        fields=_PROBLEM_FIELDS,
    ),
    "device_audit": ReportKind(
        "device_audit", "0.36.0", "One device and its driver tested end to end.",
        versions=(
            ReportVersion(1, "0.36.0", "Stamped while fields arrived; no shape recorded.", None),
            ReportVersion(2, FIRST_RECORDED_RELEASE, "The first recorded shape.",
                          "85cb92f8b71ad5ed"),
        ),
        fields=_AUDIT_FIELDS,
    ),
}


def report_version(kind: str) -> int:
    """The ``report_version`` this release stamps on a report of ``kind``."""
    return KINDS[kind].report_version


def field_since(kind: ReportKind, f: ReportField) -> str:
    return f.since or kind.since


def top_level(kind: str) -> tuple[str, ...]:
    """Every top-level field of ``kind``, in the table's order."""
    return tuple(f.path for f in KINDS[kind].fields if is_top_level(f.path))


def is_top_level(path: str) -> bool:
    return "." not in path and "[" not in path


# --- Holding a report to its kind's fields ---

_JSON_TYPES: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def tokens(path: str) -> list[str]:
    """``"verdict.checks.*[].kind"`` as ``["verdict", "checks", "*", "[]", "kind"]``."""
    out: list[str] = []
    for part in path.split("."):
        name = part.rstrip("[]")
        out.append(name)
        out.extend(["[]"] * ((len(part) - len(name)) // 2))
    return out


def type_fits(type_expr: str, value: Any) -> bool:
    if type_expr == "any":
        return True
    return any(_JSON_TYPES[t](value) for t in type_expr.split("|"))


def _holders(node: Any, toks: list[str], where: str):
    """Every object the path's last name sits in, with where it is."""
    if not toks:
        yield where, node
        return
    tok, rest = toks[0], toks[1:]
    if tok == "[]":
        if isinstance(node, list):
            for i, item in enumerate(node):
                yield from _holders(item, rest, f"{where}[{i}]")
    elif tok == "*":
        if isinstance(node, dict):
            for key, value in node.items():
                yield from _holders(value, rest, f"{where}.{key}" if where else str(key))
    elif isinstance(node, dict) and tok in node:
        yield from _holders(node[tok], rest, f"{where}.{tok}" if where else tok)


def field_problems(kind: str, report: dict[str, Any]) -> list[str]:
    """Where ``report`` breaks ``kind``'s declared fields: a top-level field
    the table does not declare, a field always written that is missing, a
    field of another type. A field inside something absent or null is not
    looked for; what holds it answers for that."""
    declared = KINDS[kind].fields
    problems = [
        f"{key}: a top-level field the report contract does not declare"
        for key in report if key not in top_level(kind)
    ]
    for f in declared:
        toks = tokens(f.path)
        for where, holder in _holders(report, toks[:-1], ""):
            if not isinstance(holder, dict):
                continue
            at = f"{where}.{toks[-1]}" if where else toks[-1]
            if toks[-1] not in holder:
                if f.required:
                    problems.append(f"{at}: missing")
            elif not type_fits(f.type, holder[toks[-1]]):
                problems.append(f"{at}: {type(holder[toks[-1]]).__name__}, declared {f.type}")
    return problems


def shape_digest(kind: str) -> str:
    """The digest of ``kind``'s declared shape: each field's path, type and
    whether it is always written. A since, a doc, a reader or an example is no
    part of the shape."""
    rows = sorted((f.path, f.type, f.required) for f in KINDS[kind].fields)
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()[:16]
