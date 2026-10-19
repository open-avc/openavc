"""Finite result projections for explicitly qualified legacy setup handlers.

Only the canonical action resolver may supply a descriptor for loaded code.
This leaf converts the declared return shape; it never selects a driver, repeats
an action, saves configuration or infers hardware effects from message prose.
Generated credentials are extracted separately before diagnostics are constructed.
"""
from __future__ import annotations

from openavc.core.outbound_messages import RoomText
from openavc.core.setup_results import Outcome, SetupField, SetupFinding, SetupResult
from openavc.drivers.setup_compatibility import SetupCompatibility


def generated_secret(descriptor: SetupCompatibility, values: dict) -> dict[str, str] | None:
    """Also used synchronously at config-update ingress before its first await."""
    key = descriptor.generated_secret
    if key is None or key not in values:
        return None
    value = values[key]
    if type(value) is not str or not value or len(value.encode("utf-8")) > 16384:
        raise ValueError("Declared generated credential is invalid")
    return {key: value}


def result_from_legacy(descriptor: SetupCompatibility, raw: dict) -> SetupResult:
    """Interpret only the selected profile, preserving uncertain old evidence."""
    values = descriptor.read_roles(raw)
    findings: list[SetupFinding] = []
    profile = descriptor.profile

    if profile in ("boolean-check/v1", "credential-check/v1"):
        passed = values["success"]
        outcome = Outcome.SUCCEEDED if passed else Outcome.FAILED
        if profile == "credential-check/v1" and values.get("enabled") is False:
            outcome = Outcome.UNVERIFIED
            findings.append(SetupFinding("legacy.authentication", Outcome.UNVERIFIED,
                                         "Authentication was not enabled for this check."))
        summary = RoomText("Check completed." if passed else "The check did not pass.")
        findings.append(SetupFinding("legacy.check", outcome, values["message"]))
    elif profile == "probe-check/v1":
        answered, silent = values["answered"], values["silent"]
        rejected, problems = values.get("rejected", ()), values["problems"]
        count = len(answered) + len(silent) + len(rejected)
        if not count:
            outcome = Outcome.UNVERIFIED
        elif not values["success"] or silent or rejected or problems:
            outcome = Outcome.DEGRADED if answered else Outcome.FAILED
        else:
            outcome = Outcome.SUCCEEDED
        summary = RoomText("Probe checks completed." if count else "No configured probes were verified.")
        findings.append(SetupFinding("legacy.probes", outcome, values["message"]))
        for code, rows, state in (("answered", answered, Outcome.SUCCEEDED),
                                  ("silent", silent, Outcome.FAILED),
                                  ("rejected", rejected, Outcome.FAILED),
                                  ("configuration", problems, Outcome.FAILED)):
            findings.extend(SetupFinding("legacy." + code, state, row) for row in rows)
    elif profile == "block-check/v1":
        count, answered, failed = values["count"], values["answered"], values["failed"]
        if count != len(answered) + len(failed):
            raise ValueError("Setup block count disagrees with declared findings")
        outcome = (Outcome.UNVERIFIED if not count else
                   Outcome.DEGRADED if failed and answered else Outcome.FAILED if failed else Outcome.SUCCEEDED)
        summary = RoomText("Block checks completed." if count else "No configured blocks were verified.")
        findings.append(SetupFinding("legacy.blocks", outcome, fields=(SetupField("Blocks checked", count),)))
        findings.extend(SetupFinding("legacy.answered", Outcome.SUCCEEDED, row) for row in answered)
        findings.extend(SetupFinding("legacy.failed", Outcome.FAILED, row) for row in failed)
    elif profile == "inventory-check/v1":
        if values["count"] != len(values["items"]):
            raise ValueError("Setup inventory count disagrees with declared items")
        outcome = Outcome.UNVERIFIED if values["reachable"] else Outcome.FAILED
        summary = RoomText("Connection information reported; full verification is unavailable.")
        findings.append(SetupFinding("legacy.inventory", outcome, fields=(
            SetupField("Reachable", values["reachable"]), SetupField("Items reported", values["count"]))))
        findings.extend(SetupFinding("legacy.item", Outcome.UNVERIFIED, row) for row in values["items"])
    elif profile == "session-check/v1":
        # This profile's false value means no supplied credential was checked.
        # A rejected credential in these qualified handlers raises instead.
        outcome = Outcome.SUCCEEDED if values["authenticated"] else Outcome.UNVERIFIED
        summary = RoomText("Session check completed." if values["authenticated"] else
                           "Connection reported; authentication was not checked.")
        findings.append(SetupFinding("legacy.authentication", outcome, fields=(
            SetupField("Authenticated", values["authenticated"]),)))
    elif profile == "pairing/v1":
        if not generated_secret(descriptor, raw):
            raise ValueError("Pairing returned no declared generated credential")
        outcome = Outcome.SUCCEEDED if values["paired"] else Outcome.UNVERIFIED
        summary = RoomText("Pairing completed." if values["paired"] else "Pairing outcome is not confirmed.")
    elif profile == "provisioning-claim/v1":
        # Old handlers can return false after changing hardware, and a true
        # claim does not prove hardware readback. Context stages stay separate.
        outcome = Outcome.UNVERIFIED
        summary = RoomText("Setup returned; verify the device before repeating it.")
        findings.append(SetupFinding("legacy.provisioning", outcome, values.get("message", ""),
                                     (SetupField("Reported accepted", values["accepted"]),)))
    else:
        raise ValueError("Unsupported setup result profile")

    for field in descriptor.diagnostics:
        present, value = field.read(raw)
        if not present:
            continue
        if type(value) is tuple:
            findings.extend(SetupFinding("legacy.detail", outcome,
                                         fields=(SetupField(field.label, item),)) for item in value)
            if not value:
                findings.append(SetupFinding("legacy.detail", outcome, fields=(SetupField(field.label, "None reported"),)))
        else:
            findings.append(SetupFinding("legacy.detail", outcome, fields=(SetupField(field.label, value),)))
    return SetupResult(outcome, summary, tuple(findings))
