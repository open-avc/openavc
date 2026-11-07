"""What a report must not contain, and taking it out in every form it is written in.

A ``Redaction`` is one value and what goes in its place. ``redactions_for``
is a device audit's list: every read community other than ``public``, what
the person typed for a driver and every credential the driver registered
while it ran, and, when the person asked, every serial number the audit
heard (``serial_numbers``: the network check's, and any value a driver
reported under a name with "serial" in it). A ``Redactor`` replaces each one
string by string, in its plain, JSON, HTML and hex forms (unspaced and in
pairs). Report a problem builds its own list and redacts with the same
``Redactor``.

What is already public is left as it is: a string that is exactly one of the
names the catalog and the drivers that ran publish (a driver's id, name and
manufacturer, and every string a published driver declares: its labels,
defaults, command and value names) is never changed, and the catalog's names
are left as they are inside a longer string too ("Driver chosen: ..."). Otherwise a password
that is the driver's published default, or a word a driver uses, would be
cut out of the driver's own text and say what it was. The wizard tells the
person before the download when a credential they typed is the driver's
published default (``DriverRun.published_secrets``); the report says nothing
of it.

A secret shorter than ``observe.MIN_SECRET_LENGTH`` is not searched for,
since it would match ordinary text everywhere; the report never writes a
typed secret into a field of its own, so such a value could only appear if
the device repeated it. A numeric secret (a PIN) is replaced in its plain
forms wherever it is not part of a longer number (``CODE1234``, yes;
``12345`` or ``1.1234``, no), and one made only of hex digits wherever it is
not part of a longer run of them. In a hex or hash field (``hex``,
``sha256``) only a secret's hex forms are looked for, since that is where
its own bytes show; there they are replaced wherever they appear. A secret
is also looked for as a URL writes it (percent-encoded) and as a traffic
entry's text shows its UTF-8 bytes.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openavc.audit.session import footprint_of

if TYPE_CHECKING:
    from openavc.audit.session import AuditSession

REDACTED = "[redacted]"
SERIAL_REMOVED = "[serial number removed]"


@dataclass(frozen=True)
class Redaction:
    """One value to take out of a report, and what to put in its place."""

    value: str
    replacement: str = REDACTED


_HEX_DIGITS = re.compile(r"[0-9A-Fa-f]+")
_DIGITS = re.compile(r"[0-9]+")
# Where a record holds bytes as hex or a hash: only a secret's hex forms are
# looked for there (its plain digits would match inside any hex run).
_HEX_FIELDS = frozenset({"hex", "sha256", "catalog_sha256"})

# How a plain form is bounded: not at all; by digits (a numeric PIN is
# masked in "CODE1234" but never cut out of "12345" or "1.1234"); by hex
# digits (a secret that is all hex letters and digits).
_FREE, _NOT_IN_A_NUMBER, _NOT_IN_HEX = "free", "number", "hex"
_BOUNDS = {
    _NOT_IN_A_NUMBER: (r"(?<![0-9.])", r"(?![0-9])"),
    _NOT_IN_HEX: (r"(?<![0-9A-Fa-f.])", r"(?![0-9A-Fa-f])"),
}


def _forms(redaction: Redaction) -> list[tuple[str, str, str, bool]]:
    """Each way ``value`` can be written in a report: ``(form, replacement,
    bound, is_hex)``, longest first (see the module docstring)."""
    from openavc.utils.log_redaction import _percent_forms

    value, repl = redaction.value, redaction.replacement
    bound = (
        _NOT_IN_A_NUMBER if _DIGITS.fullmatch(value)
        else _NOT_IN_HEX if _HEX_DIGITS.fullmatch(value) else _FREE
    )
    raw = value.encode("utf-8")
    quads = [
        (value, repl, bound, False),
        (json.dumps(value)[1:-1], json.dumps(repl)[1:-1], bound, False),
        (html.escape(value), html.escape(repl), bound, False),
        # Its UTF-8 bytes read as Latin-1: how a traffic entry's text shows them.
        (raw.decode("latin-1"), repl, _FREE, False),
        *((form, repl, _FREE, False) for form in _percent_forms(value)),
        (raw.hex(), repl.encode("utf-8").hex(), _FREE, True),
        (raw.hex().upper(), repl.encode("utf-8").hex().upper(), _FREE, True),
        # The spaced form a sentence writes bytes in (``hex_pairs``).
        (raw.hex(" "), repl.encode("utf-8").hex(" "), _FREE, True),
        (raw.hex(" ").upper(), repl.encode("utf-8").hex(" ").upper(), _FREE, True),
    ]
    seen: dict[str, tuple[str, str, bool]] = {}
    for form, replacement, bounded, is_hex in quads:
        if form and form not in seen:
            seen[form] = (replacement, bounded, is_hex)
    return sorted(
        ((form, *rest) for form, rest in seen.items()),
        key=lambda quad: -len(quad[0]),
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
        self._pairs: list[tuple[str, str, re.Pattern[str] | None]] = []
        self._hex_pairs: list[tuple[str, str, re.Pattern[str] | None]] = []
        for redaction in redactions:
            if len(redaction.value) >= MIN_SECRET_LENGTH:
                self._add_forms(redaction)
        self._pairs.sort(key=lambda triple: -len(triple[0]))
        self._hex_pairs.sort(key=lambda triple: -len(triple[0]))
        self._shield = self._shield_for(names)

    def _add_forms(self, redaction: Redaction) -> None:
        for form, replacement, bound, is_hex in _forms(redaction):
            left, right = _BOUNDS.get(bound, ("", ""))
            pattern = re.compile(left + re.escape(form) + right) if left else None
            self._pairs.append((form, replacement, pattern))
            if is_hex:
                self._hex_pairs.append((form, replacement, pattern))

    def _shield_for(self, names: Any) -> re.Pattern[str] | None:
        """Only a name a secret would cut into needs shielding."""
        shielded = sorted(
            {n for n in names if n and any(form in n for form, _, _ in self._pairs)},
            key=len, reverse=True,
        )
        if not shielded:
            return None
        return re.compile("(" + "|".join(re.escape(n) for n in shielded) + ")")

    def text(self, value: str) -> str:
        if self._shield is not None and self._shield.search(value):
            parts = self._shield.split(value)
            # Odd parts are the names the split kept.
            return "".join(
                part if i % 2 else self._replace(part) for i, part in enumerate(parts)
            )
        return self._replace(value)

    def _replace(self, value: str, pairs: list | None = None) -> str:
        for form, replacement, pattern in self._pairs if pairs is None else pairs:
            if form in value:
                value = (
                    pattern.sub(lambda _m, r=replacement: r, value) if pattern is not None
                    else value.replace(form, replacement)
                )
        return value

    def tree(self, value: Any) -> Any:
        """``value`` with every string in it redacted (dict keys included),
        except a string that is exactly a published one; under a hex or hash
        field only a secret's hex forms are looked for."""
        if isinstance(value, str):
            return value if value in self._public else self.text(value)
        if isinstance(value, dict):
            return {
                self.tree(k) if isinstance(k, str) else k:
                self._replace(v, self._hex_pairs) if k in _HEX_FIELDS and isinstance(v, str)
                else self.tree(v)
                for k, v in value.items()
            }
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
    if tester.get("leave_out_serial"):
        out.extend(Redaction(s, SERIAL_REMOVED) for s in sorted(serial_numbers(session)))
    out.extend(Redaction(v) for v in sorted(_run_secrets(session)) if isinstance(v, str) and v)
    return out


def _run_secrets(session: "AuditSession") -> set[Any]:
    """A driver run's credentials: what the person typed, and what the
    driver registered as secret while it ran."""
    values: set[Any] = set()
    for run in getattr(session, "runs", None) or []:
        values |= set(getattr(run, "secrets", None) or ())
        for attempt in getattr(run, "listens", None) or []:
            values |= attempt.sandbox.observer.secrets
    return values


_SERIAL = re.compile(r"serial", re.IGNORECASE)
# Where a record holding a value names it (a status value, a change), and
# the fields that hold the value itself.
_NAMING_FIELDS = ("name", "key", "state", "state_key")
_VALUE_FIELDS = ("value", "now", "before", "old", "new", "first", "last", "original")
# ENTITY-MIB entPhysicalSerialNum, as an SNMP walk's rows name it.
_SERIAL_OID = "1.3.6.1.2.1.47.1.1.1.1.11."


def serials_in(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        _serials_in_record(value, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            serials_in(item, out)


def _serials_in_record(record: dict[Any, Any], out: set[str]) -> None:
    named = _names_a_serial(record)
    # A TLS certificate's serial is the certificate's, not the device's.
    certificate = "issuer" in record
    serial = _oid_serial(record)
    if serial is not None:
        out.add(serial)
    for key, item in record.items():
        if _holds_a_serial(key, item, named, certificate):
            out.add(str(item))
        serials_in(item, out)


def _names_a_serial(record: dict[Any, Any]) -> bool:
    """The record names a value with "serial" in its name (a status value, a change)."""
    return any(
        isinstance(record.get(k), str) and _SERIAL.search(record[k]) for k in _NAMING_FIELDS
    )


def _oid_serial(record: dict[Any, Any]) -> str | None:
    """The value of an SNMP walk's entPhysicalSerialNum row, else None."""
    oid, value = record.get("oid"), record.get("value")
    if not isinstance(value, str):
        return None
    return value if isinstance(oid, str) and oid.startswith(_SERIAL_OID) else None


def _holds_a_serial(key: Any, item: Any, named: bool, certificate: bool) -> bool:
    """A plain value under a key that says serial (not in a certificate), or
    under a value field of a record that names a serial."""
    if not isinstance(item, (str, int)) or isinstance(item, bool):
        return False
    if named and key in _VALUE_FIELDS:
        return True
    return _says_serial(key) and not certificate


def _says_serial(key: Any) -> bool:
    return isinstance(key, str) and bool(_SERIAL.search(key))


def serial_numbers(session: "AuditSession") -> set[str]:
    """Every serial number the audit heard, from wherever it came: the
    network check (SSDP, SNMP, mDNS, AMX DDP) and every value a driver
    reported under a name with "serial" in it."""
    out: set[str] = set()
    footprint = footprint_of(session)
    if footprint is not None:
        device = footprint.device
        if device is not None and device.serial_number:
            out.add(str(device.serial_number))
        serials_in(footprint.to_dict(), out)
    for run in getattr(session, "runs", None) or []:
        _run_serials(run, out)
    from openavc.audit.observe import MIN_SECRET_LENGTH

    return {s for s in out if len(s.strip()) >= MIN_SECRET_LENGTH}


def _run_serials(run: Any, out: set[str]) -> None:
    """Every value one driver run reported, its traffic aside."""
    for attempt in getattr(run, "listens", None) or []:
        live = attempt.to_dict()
        serials_in({k: v for k, v in live.items() if k != "traffic"}, out)
        serials_in(list(getattr(attempt, "changes", None) or []), out)
    for part in (getattr(run, "commands", None), getattr(run, "settings", None)):
        if part is not None:
            serials_in(part.to_dict(), out)


def _strings_in(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, dict):
        out.update(key for key in value if isinstance(key, str))
        _strings_in(list(value.values()), out)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _strings_in(item, out)


def _catalog_names(value: Any, out: set[str]) -> None:
    """The driver ids, names and manufacturers anything in ``value`` names."""
    if isinstance(value, dict):
        out.update(name for key, item in value.items() for name in _names_under(key, item))
        _catalog_names(list(value.values()), out)
    elif isinstance(value, list):
        for item in value:
            _catalog_names(item, out)


_CATALOG_NAME_KEYS = ("driver_id", "driver_name", "manufacturer")


def _names_under(key: Any, item: Any) -> list[str]:
    if key in _CATALOG_NAME_KEYS and isinstance(item, str):
        return [item]
    if key == "drivers" and isinstance(item, list):
        return [d for d in item if isinstance(d, str)]
    return []


def catalog_names(session: "AuditSession") -> set[str]:
    """The names the catalog publishes for the drivers this audit met: each
    tested driver's id, name, manufacturer and file names (and where the zip
    puts them), and every driver the verdict names."""
    out: set[str] = set()
    for run in getattr(session, "runs", None) or []:
        out |= _run_catalog_names(run)
    footprint = footprint_of(session)
    if footprint is not None:
        verdict = (footprint.to_dict() or {}).get("verdict") or {}
        _catalog_names({k: v for k, v in verdict.items() if k != "catalog"}, out)
    out.discard("")
    return out


def _run_catalog_names(run: Any) -> set[str]:
    """One tested driver's id, name, manufacturer and file names, and the
    paths the zip puts those files at."""
    identity = run.choice.identity or {}
    out = {
        identity[key] for key in ("id", "name", "manufacturer")
        if isinstance(identity.get(key), str)
    }
    for f in identity.get("files") or []:
        name = f.get("name")
        if isinstance(name, str) and name:
            out |= {name, f"driver/{name}", f"driver/run-{run.index + 1}/{name}"}
    return out


def driver_public_strings(driver_id: str, identity: dict[str, Any] | None) -> set[str]:
    """Every string one driver declares, when the copy that ran is the
    catalog's (every file matches it); nothing for any other copy."""
    from openavc.drivers.registry import get_driver_class

    out: set[str] = set()
    files = (identity or {}).get("files") or []
    if files and all(f.get("matches_catalog") is True for f in files):
        cls = get_driver_class(driver_id)
        _strings_in(getattr(cls, "DRIVER_INFO", None) or {}, out)
    out.discard("")
    return out


def public_strings(session: "AuditSession") -> set[str]:
    """Every string a published driver that ran declares (see the module
    docstring): never redacted where a whole string is one of them."""
    out: set[str] = set()
    for run in getattr(session, "runs", None) or []:
        out |= driver_public_strings(run.choice.driver_id, run.choice.identity)
    return out


def session_redactor(session: "AuditSession") -> "Redactor":
    """The redactor for ``session``'s report."""
    return Redactor(redactions_for(session), public_strings(session), catalog_names(session))
