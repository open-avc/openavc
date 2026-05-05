"""Deterministic identification dispatcher for the discovery redesign.

Replaces the heuristic ``DriverMatcher`` (additive scoring on weak signals)
with a hash-lookup dispatcher that returns one of three states based on
the strongest signal observed.

Design contract
---------------
- A device is ``identified`` if and only if a Tier 1, 2, or 3 signal
  matches a deterministic SignalRule. There is no scoring; the rule
  either matches or it does not.
- A device is ``possible`` only via Tier 4 soft signals (OUI / SNMP PEN /
  hostname pattern), and only when the soft signal narrows the candidate
  set down to something useful.
- A device is ``unknown`` when no signal matches.

Scaling
-------
Driver hints are indexed at load time by signal kind + id, giving O(1)
match lookup per Evidence record. With 500 drivers each declaring a
unique Tier 1/2/3 fingerprint, every matching device hits exactly one
rule. The system gets BETTER as more drivers are added because the
fingerprint registry covers more devices, not because scoring becomes
more accurate.

Validation invariants (enforced by SignalIndex.add_rule)
-------------------------------------------------------
- A SignalRule is uniquely identified by (kind, source_id) plus its
  optional ``txt_match`` filter. Two rules collision-checked at index
  build time so two drivers cannot both claim "I am _netaudio-cmc._udp"
  without further qualification.

This module is the central coordinator. Tier-specific *evidence
producers* (mDNS scanner, AMX DDP listener, broadcast probes, active
probes) live in their own modules and emit ``Evidence`` records into
the device's ``evidence_log``. ``TierMatcher.match()`` is the consumer.

See discovery-redesign-plan.md for the full architecture.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from server.discovery.result import (
    Evidence,
    IdentificationMatch,
    SignalTier,
)

log = logging.getLogger("discovery.tier_matcher")


# ---------------------------------------------------------------------------
# Rule + index
# ---------------------------------------------------------------------------


# Stable strings used as the ``kind`` field on rules and evidence sources.
# Rule kinds are scoped per-tier so a Tier 1 mdns rule cannot accidentally
# collide with a Tier 3 active probe rule.
KIND_MDNS = "mdns"
KIND_SSDP = "ssdp"
KIND_AMX_DDP = "amx_ddp"
KIND_BROADCAST = "broadcast"     # PJLink Class 2 SRCH, Crestron CIP, ONVIF, HiQnet, Symetrix
KIND_ACTIVE_PROBE = "probe"      # PJLink Class 1, Extron SIS, Samsung MDC, etc.

KIND_OUI = "oui"                 # Tier 4 soft
KIND_SNMP_PEN = "snmp_pen"       # Tier 4 soft
KIND_HOSTNAME = "hostname"       # Tier 4 soft


_STRONG_KINDS = {
    KIND_MDNS, KIND_SSDP, KIND_AMX_DDP, KIND_BROADCAST, KIND_ACTIVE_PROBE,
}
_SOFT_KINDS = {KIND_OUI, KIND_SNMP_PEN, KIND_HOSTNAME}


@dataclass(frozen=True)
class SignalRule:
    """A deterministic rule that maps a signal to a driver_id.

    Fields:
        driver_id: The driver this rule identifies.
        tier: Which tier produces this signal (used for ordering).
        kind: One of the ``KIND_*`` constants. Disambiguates source_id
            namespaces so an mDNS service and an active probe with the
            same string can't collide.
        source_id: Stable identifier within the kind:
            - ``KIND_MDNS``: service type, e.g. ``"_netaudio-cmc._udp.local."``
            - ``KIND_SSDP``: UPnP device type URN
            - ``KIND_AMX_DDP``: ``"<Make>/<ModelGlob>"``, e.g. ``"Polycom/SoundStructure*"``
            - ``KIND_BROADCAST``: probe id, e.g. ``"pjlink_class2"``, ``"crestron_cip"``,
              ``"onvif"``, ``"hiqnet"``, ``"symetrix"``
            - ``KIND_ACTIVE_PROBE``: probe id, e.g. ``"pjlink_class1"``, ``"extron_sis"``,
              ``"samsung_mdc"``, ``"visca"``, ``"qrc"``
            - ``KIND_OUI``: 6-char OUI prefix, lowercase, e.g. ``"00:0c:4d"``
            - ``KIND_SNMP_PEN``: integer Private Enterprise Number as string
            - ``KIND_HOSTNAME``: regex source string (compiled lazily by the index)
        txt_match: Optional TXT-record filter. The signal matches only
            when every key in this dict is present in the observed TXT
            and matches the value (case-insensitive). Used to disambiguate
            generic service types (``_http._tcp``, ``_airplay._tcp``) by
            requiring a manufacturer or model TXT field.
        evidence_data: Optional static data merged into the evidence
            record when this rule matches. Used to pre-fill manufacturer
            / model when the signal alone implies them.
    """

    driver_id: str
    tier: SignalTier
    kind: str
    source_id: str
    txt_match: tuple[tuple[str, str], ...] = ()
    evidence_data: tuple[tuple[str, str], ...] = ()

    @classmethod
    def for_mdns(
        cls,
        driver_id: str,
        service_type: str,
        txt_match: dict[str, str] | None = None,
    ) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.PASSIVE_LISTENER,
            kind=KIND_MDNS,
            source_id=_normalize_service_type(service_type),
            txt_match=_freeze_dict(txt_match),
        )

    @classmethod
    def for_ssdp(
        cls,
        driver_id: str,
        device_type: str,
    ) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.PASSIVE_LISTENER,
            kind=KIND_SSDP,
            source_id=device_type,
        )

    @classmethod
    def for_amx_ddp(
        cls,
        driver_id: str,
        make: str,
        model_pattern: str,
    ) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.PASSIVE_LISTENER,
            kind=KIND_AMX_DDP,
            source_id=f"{make}/{model_pattern}",
        )

    @classmethod
    def for_broadcast(cls, driver_id: str, probe_id: str) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.BROADCAST_PROBE,
            kind=KIND_BROADCAST,
            source_id=probe_id,
        )

    @classmethod
    def for_active_probe(cls, driver_id: str, probe_id: str) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.ACTIVE_PROBE,
            kind=KIND_ACTIVE_PROBE,
            source_id=probe_id,
        )

    @classmethod
    def for_oui(cls, driver_id: str, prefix: str) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.ENRICHMENT,
            kind=KIND_OUI,
            source_id=_normalize_mac_prefix(prefix),
        )

    @classmethod
    def for_snmp_pen(cls, driver_id: str, pen: int) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.ENRICHMENT,
            kind=KIND_SNMP_PEN,
            source_id=str(pen),
        )

    @classmethod
    def for_hostname(cls, driver_id: str, pattern: str) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.ENRICHMENT,
            kind=KIND_HOSTNAME,
            source_id=pattern,
        )


def _normalize_service_type(service: str) -> str:
    """Return service type with a trailing dot, lowercase up to the dot."""
    s = service.strip()
    if not s.endswith("."):
        s = s + "."
    return s.lower()


def _normalize_mac_prefix(prefix: str) -> str:
    """Normalize OUI prefix to colon-separated lowercase, first 8 chars."""
    return prefix.replace("-", ":").lower()[:8]


def _freeze_dict(d: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not d:
        return ()
    return tuple(sorted((k.lower(), str(v)) for k, v in d.items()))


class SignalIndex:
    """Indexes SignalRules for O(1) match lookup per Evidence record."""

    def __init__(self) -> None:
        # (kind, source_id) -> list of rules. Multiple rules per key are
        # allowed only when each carries a distinct ``txt_match`` filter
        # — see add_rule() for the validation.
        self._rules: dict[tuple[str, str], list[SignalRule]] = {}
        # Compiled hostname regex cache.
        self._hostname_rules: list[tuple[re.Pattern, SignalRule]] = []

    def add_rule(self, rule: SignalRule) -> None:
        """Register a rule. Raises ValueError on disallowed collisions.

        Same (kind, source_id) is allowed only if both sides carry distinct
        ``txt_match`` filters — generic service types like ``_http._tcp``
        can be claimed by multiple drivers as long as they each constrain
        on different TXT fields. Identical (kind, source_id, txt_match)
        is a duplicate and raises.
        """
        if rule.kind not in _STRONG_KINDS and rule.kind not in _SOFT_KINDS:
            raise ValueError(f"Unknown rule kind: {rule.kind!r}")

        if rule.kind == KIND_HOSTNAME:
            try:
                pattern = re.compile(rule.source_id, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(
                    f"Invalid hostname pattern for {rule.driver_id}: {rule.source_id!r}"
                ) from exc
            self._hostname_rules.append((pattern, rule))
            return

        key = (rule.kind, rule.source_id)
        bucket = self._rules.setdefault(key, [])

        # Idempotent re-add of the same (driver_id, kind, source_id, txt_match)
        # is always allowed.
        for existing in bucket:
            if (
                existing.driver_id == rule.driver_id
                and existing.txt_match == rule.txt_match
            ):
                return

        # Soft signals (Tier 4) deliberately allow multiple drivers per
        # source_id — that is what produces the "possible" state with a
        # candidate list. No collision check.
        if rule.kind in _SOFT_KINDS:
            bucket.append(rule)
            return

        # Strong signals (Tier 1/2/3) must be unambiguous. Two drivers
        # cannot both claim the same (kind, source_id) with the same
        # txt_match filter, and a generic (no-filter) rule cannot coexist
        # with a filtered rule for the same source_id.
        for existing in bucket:
            if existing.txt_match == rule.txt_match:
                raise ValueError(
                    f"Signal collision: {rule.kind}:{rule.source_id} "
                    f"(txt_match={dict(rule.txt_match)}) claimed by both "
                    f"{existing.driver_id!r} and {rule.driver_id!r}. "
                    "Add a TXT filter or pick a more specific signal."
                )
            # On strong signals, generic (no-filter) rules cannot coexist
            # with filtered rules — the generic one would shadow the filtered.
            if not existing.txt_match and rule.txt_match:
                raise ValueError(
                    f"Signal collision: {rule.kind}:{rule.source_id} — "
                    f"{existing.driver_id!r} claims it without a TXT filter, "
                    f"so {rule.driver_id!r}'s filtered claim cannot win."
                )
            if existing.txt_match and not rule.txt_match:
                raise ValueError(
                    f"Signal collision: {rule.kind}:{rule.source_id} — "
                    f"{rule.driver_id!r} claims it without a TXT filter, "
                    f"which would shadow {existing.driver_id!r}'s filtered claim."
                )

        bucket.append(rule)

    def add_rules(self, rules: Iterable[SignalRule]) -> None:
        for rule in rules:
            self.add_rule(rule)

    def find_strong(
        self,
        kind: str,
        source_id: str,
        txt: dict[str, str] | None = None,
    ) -> SignalRule | None:
        """Look up a strong-signal rule. Returns None if no match."""
        if kind not in _STRONG_KINDS:
            return None
        normalized_source = self._normalize_source_for_kind(kind, source_id)
        bucket = self._rules.get((kind, normalized_source), [])
        if not bucket:
            return None
        # Filter rules: pick the one whose txt_match (if any) is satisfied.
        # When multiple match, prefer the most-specific filter (longest dict).
        observed = {k.lower(): str(v) for k, v in (txt or {}).items()}
        matching = [r for r in bucket if _txt_match_satisfied(r.txt_match, observed)]
        if not matching:
            return None
        matching.sort(key=lambda r: -len(r.txt_match))
        return matching[0]

    def find_soft_oui(self, mac: str) -> list[str]:
        """Return driver_ids whose OUI prefix matches the MAC. May be empty."""
        if not mac:
            return []
        prefix = _normalize_mac_prefix(mac)
        rules = self._rules.get((KIND_OUI, prefix), [])
        return [r.driver_id for r in rules]

    def find_soft_pen(self, pen: int | None) -> list[str]:
        """Return driver_ids whose SNMP PEN matches. May be empty."""
        if pen is None:
            return []
        rules = self._rules.get((KIND_SNMP_PEN, str(pen)), [])
        return [r.driver_id for r in rules]

    def find_soft_hostname(self, hostname: str | None) -> list[str]:
        """Return driver_ids whose hostname pattern matches. May be empty."""
        if not hostname:
            return []
        return [
            rule.driver_id
            for pat, rule in self._hostname_rules
            if pat.search(hostname)
        ]

    def _normalize_source_for_kind(self, kind: str, source_id: str) -> str:
        if kind == KIND_MDNS:
            return _normalize_service_type(source_id)
        if kind == KIND_OUI:
            return _normalize_mac_prefix(source_id)
        return source_id

    def driver_count(self) -> int:
        """Return the number of distinct driver_ids registered."""
        seen: set[str] = set()
        for bucket in self._rules.values():
            for rule in bucket:
                seen.add(rule.driver_id)
        for _, rule in self._hostname_rules:
            seen.add(rule.driver_id)
        return len(seen)


def _txt_match_satisfied(
    required: tuple[tuple[str, str], ...],
    observed: dict[str, str],
) -> bool:
    """Return True iff every required (key, value) appears in observed."""
    if not required:
        return True
    for key, expected in required:
        actual = observed.get(key)
        if actual is None:
            return False
        if actual.lower() != expected.lower():
            return False
    return True


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


@dataclass
class TierMatcher:
    """Deterministic identification dispatcher.

    Given the full ``evidence_log`` of a device, returns one
    ``IdentificationMatch``. First strong-tier match wins, in order
    Tier 1 -> Tier 2 -> Tier 3. Soft signals (Tier 4) only contribute
    to ``possible`` state when no strong tier matched.
    """

    index: SignalIndex
    # Optional override: candidates ranked by (driver_id, oui_prefix_observed)
    # for tie-breaking in possible state. None = first-encountered order.
    candidate_ranker: object | None = field(default=None, repr=False)

    def match(self, evidence_log: list[Evidence]) -> IdentificationMatch:
        """Run the deterministic dispatch."""
        # Tier 1: passive listeners
        for ev in evidence_log:
            if ev.tier != SignalTier.PASSIVE_LISTENER:
                continue
            rule = self._lookup_strong(ev)
            if rule:
                return IdentificationMatch.identified(
                    driver_id=rule.driver_id,
                    source=f"{rule.kind}:{rule.source_id}",
                    evidence=[ev],
                )

        # Tier 2: broadcast probes
        for ev in evidence_log:
            if ev.tier != SignalTier.BROADCAST_PROBE:
                continue
            rule = self._lookup_strong(ev)
            if rule:
                return IdentificationMatch.identified(
                    driver_id=rule.driver_id,
                    source=f"{rule.kind}:{rule.source_id}",
                    evidence=[ev],
                )

        # Tier 3: active probes
        for ev in evidence_log:
            if ev.tier != SignalTier.ACTIVE_PROBE:
                continue
            rule = self._lookup_strong(ev)
            if rule:
                return IdentificationMatch.identified(
                    driver_id=rule.driver_id,
                    source=f"{rule.kind}:{rule.source_id}",
                    evidence=[ev],
                )

        # Tier 4: soft signals -> possible state if any narrows the candidate set
        candidates, source = self._gather_soft_candidates(evidence_log)
        if candidates:
            relevant = [
                ev for ev in evidence_log
                if ev.tier == SignalTier.ENRICHMENT
            ]
            return IdentificationMatch.possible(
                candidates=candidates,
                source=source,
                evidence=relevant,
            )

        return IdentificationMatch.unknown(
            reason="no_signal_matched",
            evidence=list(evidence_log),
        )

    def _lookup_strong(self, ev: Evidence) -> SignalRule | None:
        """Find a strong-tier rule for an evidence record."""
        kind = ev.data.get("kind")
        source_id = ev.data.get("source_id")
        if not isinstance(kind, str) or not isinstance(source_id, str):
            return None
        txt = ev.data.get("txt") if isinstance(ev.data.get("txt"), dict) else None
        return self.index.find_strong(kind, source_id, txt)

    def _gather_soft_candidates(
        self, evidence_log: list[Evidence],
    ) -> tuple[list[str], str]:
        """Collect candidate driver_ids from soft signals.

        Returns (candidates, source_label). Source label points at the
        signal that produced the narrowest candidate set; ties broken by
        first observation.
        """
        results: list[tuple[str, list[str]]] = []
        for ev in evidence_log:
            if ev.tier != SignalTier.ENRICHMENT:
                continue
            kind = ev.data.get("kind")
            value = ev.data.get("value")
            if kind == KIND_OUI and isinstance(value, str):
                hits = self.index.find_soft_oui(value)
                if hits:
                    results.append((f"{KIND_OUI}:{_normalize_mac_prefix(value)}", hits))
            elif kind == KIND_SNMP_PEN and isinstance(value, int):
                hits = self.index.find_soft_pen(value)
                if hits:
                    results.append((f"{KIND_SNMP_PEN}:{value}", hits))
            elif kind == KIND_HOSTNAME and isinstance(value, str):
                hits = self.index.find_soft_hostname(value)
                if hits:
                    results.append((f"{KIND_HOSTNAME}:{value}", hits))

        if not results:
            return [], ""

        # Pick the soft signal with the smallest candidate set.
        results.sort(key=lambda r: (len(r[1]), r[0]))
        source, candidates = results[0]
        # Stable de-dup, preserving first-seen order across all soft hits
        seen: set[str] = set()
        ordered: list[str] = []
        for _, ids in results:
            for did in ids:
                if did not in seen:
                    seen.add(did)
                    ordered.append(did)
        # Return the narrowest result first, with broader hits as tail.
        narrow_set = set(candidates)
        first = [d for d in ordered if d in narrow_set]
        rest = [d for d in ordered if d not in narrow_set]
        return first + rest, source


# ---------------------------------------------------------------------------
# Evidence helpers (consumers emit evidence in this shape)
# ---------------------------------------------------------------------------


def evidence_mdns(
    service_type: str,
    txt: dict[str, str] | None = None,
    instance_name: str | None = None,
) -> Evidence:
    """Build an Evidence record for an mDNS observation."""
    data: dict = {
        "kind": KIND_MDNS,
        "source_id": _normalize_service_type(service_type),
    }
    if txt:
        data["txt"] = dict(txt)
    if instance_name:
        data["instance"] = instance_name
    return Evidence(
        tier=SignalTier.PASSIVE_LISTENER,
        source=f"mdns:{_normalize_service_type(service_type)}",
        data=data,
    )


def evidence_amx_ddp(make: str, model: str, raw: str | None = None) -> Evidence:
    """Build an Evidence record for an AMX DDP beacon."""
    return Evidence(
        tier=SignalTier.PASSIVE_LISTENER,
        source=f"amx_ddp:{make}/{model}",
        data={
            "kind": KIND_AMX_DDP,
            "source_id": f"{make}/{model}",
            "make": make,
            "model": model,
            "raw": raw,
        },
    )


def evidence_broadcast(probe_id: str, response: dict | None = None) -> Evidence:
    """Build an Evidence record for a Tier 2 broadcast probe response."""
    return Evidence(
        tier=SignalTier.BROADCAST_PROBE,
        source=f"broadcast:{probe_id}",
        data={
            "kind": KIND_BROADCAST,
            "source_id": probe_id,
            "response": response or {},
        },
    )


def evidence_active_probe(probe_id: str, response: dict | None = None) -> Evidence:
    """Build an Evidence record for a Tier 3 active probe response."""
    return Evidence(
        tier=SignalTier.ACTIVE_PROBE,
        source=f"probe:{probe_id}",
        data={
            "kind": KIND_ACTIVE_PROBE,
            "source_id": probe_id,
            "response": response or {},
        },
    )


def evidence_oui(mac: str, vendor: str | None = None) -> Evidence:
    """Build an Evidence record for an OUI lookup."""
    prefix = _normalize_mac_prefix(mac)
    return Evidence(
        tier=SignalTier.ENRICHMENT,
        source=f"oui:{prefix}",
        data={
            "kind": KIND_OUI,
            "value": prefix,
            "mac": mac,
            "vendor": vendor,
        },
    )


def evidence_snmp_pen(pen: int, sysdescr: str | None = None) -> Evidence:
    """Build an Evidence record for an SNMP sysObjectID PEN match."""
    return Evidence(
        tier=SignalTier.ENRICHMENT,
        source=f"snmp_pen:{pen}",
        data={
            "kind": KIND_SNMP_PEN,
            "value": pen,
            "sysdescr": sysdescr,
        },
    )


def evidence_hostname(hostname: str) -> Evidence:
    """Build an Evidence record for an observed hostname."""
    return Evidence(
        tier=SignalTier.ENRICHMENT,
        source=f"hostname:{hostname}",
        data={
            "kind": KIND_HOSTNAME,
            "value": hostname,
        },
    )
