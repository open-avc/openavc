"""
The Windows installer's signature, checked before an update runs it.

A Windows update runs the downloaded installer as SYSTEM through Task Scheduler
(``rollback._launch_installer_via_scheduler``), and Windows checks nothing on
that path: SmartScreen and the publisher prompt protect a person opening a
download, not a service handing a file to the scheduler. The SHA-256 the update
is checked against comes from the same place as the file (the cloud's message,
or the release's SHA256SUMS.txt), so it proves the download is complete, not
who made it. This module is the check that does: ``_apply_windows`` calls
``check_installer`` before anything is scheduled, and a refusal runs nothing.

Two questions, and both must pass:

1. Does Windows trust the signature? ``Get-AuthenticodeSignature`` (which is
   WinVerifyTrust) answers: the file is byte-for-byte what was signed, the
   certificate chains to a root Windows trusts, and a timestamp vouches for
   when it was signed. The timestamp matters: the signing certificates live
   three days, so every installer older than that is valid only through it.
   This is why the chain is never rebuilt here: an X509Chain built on the
   signer certificate today reports it expired.
2. Is it OpenAVC's signature? Anyone can buy a valid one, so the signer must be
   OpenAVC LLC's identity as issued by Microsoft's code signing service (Azure
   Artifact Signing, the account release.yml signs with). What is pinned is
   chosen for what stays the same across releases:
   - the subject's CN, O, S (state) and C. L (the city) is left out: a move
     changes it, and an NC entity name is unique within the state already.
   - the issuer's O, and a CN that names one of Microsoft's "ID Verified CS"
     issuing CAs. The CA itself is not pinned: releases alternate between
     AOC CA 03 and AOC CA 04.
   Not pinned: the leaf thumbprint (a new certificate every few days), and the
   durable identity EKU (1.3.6.1.4.1.311.97.<n>.<n>.<n>.<n>). That EKU is
   Microsoft's suggested pin, but it belongs to the identity validation, and a
   validation that lapses is replaced by one with a new EKU, which would make
   every Windows install refuse every update at once.

Changing the signer (a new legal name, another state): ship a release that
accepts both identities, signed with the current one, before signing with the
new one. An install only ever trusts the identity its own code names.

The check runs Windows PowerShell 5.1 by absolute path (always present; a
PATH lookup as SYSTEM is a hijack risk), passes the file through an environment
variable so no path is ever parsed as script, and emits one JSON object that
the Python here judges. The script uses only a cmdlet and property reads, so it
also runs under Constrained Language Mode on locked-down machines.

``python -m openavc.updater.installer_signature <file>`` runs the same check
from a shell. release.yml runs it on every installer it signs, and CI's Windows
leg runs it on the latest release's installer and a tampered copy of it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from openavc.utils.spawn import CREATE_NO_WINDOW

log = logging.getLogger(__name__)

# Who signs OpenAVC's Windows installer (the leaf certificate's subject), and
# who issues that certificate. Field names as Windows writes them: "S" is the
# state, which OpenSSL calls "ST".
SIGNER = {
    "CN": "OpenAVC LLC",
    "O": "OpenAVC LLC",
    "S": "North Carolina",
    "C": "US",
}
ISSUER_ORGANIZATION = "Microsoft Corporation"
ISSUER_NAME_PREFIX = "Microsoft ID Verified CS "

RELEASES_URL = "https://github.com/open-avc/openavc/releases"

# Long enough for a chain check that waits on revocation lists an isolated
# network never answers; the hash of the installer itself takes a second.
TIMEOUT_SECONDS = 120

# One line, single quotes only: it goes on the command line as one argument,
# and -EncodedCommand is what endpoint protection flags as malware.
_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "$s = Get-AuthenticodeSignature -LiteralPath $env:OPENAVC_INSTALLER_PATH; "
    "$c = $s.SignerCertificate; "
    "@{status = [string]$s.Status; message = [string]$s.StatusMessage; "
    "subject = [string]$c.Subject; issuer = [string]$c.Issuer; "
    "thumbprint = [string]$c.Thumbprint} | ConvertTo-Json -Compress"
)

_REPORT_FIELDS = ("status", "message", "subject", "issuer", "thumbprint")


class InstallerSignatureError(RuntimeError):
    """The installer was refused. The message is the sentence the user sees."""


class SignatureUnreadable(RuntimeError):
    """Windows could not be asked about the signature at all."""


def _powershell() -> Path:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def read_signature(path: Path) -> dict[str, str]:
    """Ask Windows about ``path``'s Authenticode signature.

    Returns the report as strings: ``status`` (Valid, NotSigned, HashMismatch,
    NotTrusted, ...), ``message`` (Windows' sentence for it), and the signer
    certificate's ``subject``, ``issuer`` and ``thumbprint`` (empty when there
    is no signer). Raises ``SignatureUnreadable`` when there is no answer.
    """
    # PSModulePath is left out: started from PowerShell 7 (a terminal, a CI
    # step), 5.1 would inherit 7's module folders, find 7's copy of the module
    # Get-AuthenticodeSignature lives in, and fail to load it. Without it, 5.1
    # builds its own.
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    env["OPENAVC_INSTALLER_PATH"] = str(path)
    try:
        result = subprocess.run(
            [str(_powershell()), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", _SCRIPT],
            capture_output=True, timeout=TIMEOUT_SECONDS, env=env,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        raise SignatureUnreadable("Windows PowerShell was not found") from None
    except subprocess.TimeoutExpired:
        raise SignatureUnreadable(
            f"the check did not finish within {TIMEOUT_SECONDS} seconds"
        ) from None
    except OSError as e:
        raise SignatureUnreadable(f"Windows PowerShell could not be started: {e}") from None

    out = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        raise SignatureUnreadable(
            _error_text(result.stderr)
            or f"Windows PowerShell exited with code {result.returncode}"
        )
    try:
        data = json.loads(out.splitlines()[-1]) if out else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise SignatureUnreadable("Windows PowerShell returned no signature report")
    return {field: str(data.get(field) or "") for field in _REPORT_FIELDS}


def _error_text(stderr: bytes) -> str:
    """PowerShell's error message, which it wraps across lines, as one line.

    The message comes first; the "At line:" position report after it is cut.
    """
    words: list[str] = []
    for line in stderr.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(("At line:", "+ ")):
            break
        if line:
            words.append(line)
    return " ".join(words)[:300]


def _dn_fields(dn: str) -> dict[str, list[str]]:
    """A distinguished name as Windows writes it, field by field.

    ``CN=OpenAVC LLC, O=OpenAVC LLC, S=North Carolina, C=US``. A value holding
    a comma is quoted (``O="Acme, Inc."``), so the split honors quotes; a field
    may repeat, so each maps to every value it has.
    """
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in dn:
        if ch == '"':
            in_quotes = not in_quotes
        if ch == "," and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))

    fields: dict[str, list[str]] = {}
    for part in parts:
        key, sep, value = part.partition("=")
        if not sep:
            continue
        key = key.strip().upper()
        if key == "ST":
            key = "S"
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('""', '"')
        fields.setdefault(key, []).append(value)
    return fields


def signature_problem(report: dict[str, str]) -> str | None:
    """What is wrong with a signature report, as one sentence; None if nothing."""
    status = report.get("status", "")
    if status == "NotSigned":
        return "The downloaded installer is not signed."
    if status == "HashMismatch":
        return "The downloaded installer was changed after it was signed."
    if status != "Valid":
        detail = report.get("message") or status or "no reason given"
        return f"Windows does not trust the downloaded installer's signature ({detail})."

    subject = _dn_fields(report.get("subject", ""))
    if any(subject.get(key) != [value] for key, value in SIGNER.items()):
        signer = report.get("subject") or "an unnamed publisher"
        return f"The downloaded installer is signed by {signer}, not by OpenAVC LLC."

    issuer = _dn_fields(report.get("issuer", ""))
    issuer_name = issuer.get("CN", [""])
    if (
        issuer.get("O") != [ISSUER_ORGANIZATION]
        or len(issuer_name) != 1
        or not issuer_name[0].startswith(ISSUER_NAME_PREFIX)
    ):
        return (
            "The downloaded installer's OpenAVC LLC certificate was issued by "
            f"{report.get('issuer') or 'an unnamed authority'}, not by Microsoft."
        )
    return None


def check_installer(path: Path, version: str = "") -> dict[str, str]:
    """Refuse ``path`` unless it carries OpenAVC LLC's valid signature.

    Returns the signature report when the installer may run. Raises
    ``InstallerSignatureError`` with the sentence for ``system.update_error``
    otherwise; the caller runs nothing and deletes the file.
    """
    installer = f"OpenAVC-Setup-{version}.exe" if version else "the OpenAVC installer"
    download = f"download {installer} from {RELEASES_URL} and run it on this computer."
    try:
        report = read_signature(path)
    except SignatureUnreadable as e:
        log.error("Installer signature check could not run for %s: %s", path.name, e)
        raise InstallerSignatureError(
            f"The downloaded installer's signature could not be checked ({e}). "
            f"Nothing was installed. Try the update again, or {download}"
        ) from None

    problem = signature_problem(report)
    if problem:
        log.error(
            "Refusing installer %s: %s (status %s, signer %r, issuer %r, certificate %s)",
            path.name, problem, report["status"], report["subject"],
            report["issuer"], report["thumbprint"] or "none",
        )
        raise InstallerSignatureError(
            f"{problem} Nothing was installed. To update, {download}"
        )

    log.info(
        "Installer signature verified for %s: %s (certificate %s)",
        path.name, report["subject"], report["thumbprint"],
    )
    return report


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) != 2:
        print("usage: python -m openavc.updater.installer_signature <installer.exe>")
        sys.exit(2)
    try:
        found = check_installer(Path(sys.argv[1]))
    except InstallerSignatureError as e:
        print(f"REFUSED: {e}")
        sys.exit(1)
    print(f"ACCEPTED: signed by {found['subject']}")
