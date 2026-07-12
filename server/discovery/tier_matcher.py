"""Deterministic identification dispatcher for the discovery redesign.

Replaces the heuristic ``DriverMatcher`` (additive scoring on weak signals)
with a hash-lookup dispatcher that returns one of three states based on
the strongest signal observed.

Design contract
---------------
- A device is ``identified`` if and only if a strong signal
  (``passive_listener`` / ``broadcast_probe`` / ``active_probe``)
  matches a deterministic SignalRule. There is no scoring; the rule
  either matches or it does not.
- A device is ``possible`` only via ``enrichment`` soft signals
  (OUI / SNMP PEN / hostname pattern), and only when the soft signal
  narrows the candidate set down to something useful.
- A device is ``unknown`` when no signal matches.

Scaling
-------
Driver hints are indexed at load time by signal kind + id, giving O(1)
match lookup per Evidence record. With 500 drivers each declaring a
unique strong-signal fingerprint, every matching device hits exactly
one rule. The system gets BETTER as more drivers are added because the
fingerprint registry covers more devices, not because scoring becomes
more accurate.

Validation invariants (enforced by SignalIndex.add_rule)
-------------------------------------------------------
- A SignalRule is uniquely identified by (kind, source_id) plus its
  optional ``txt_match`` filter. Two rules collision-checked at index
  build time so two drivers cannot both claim "I am _netaudio-cmc._udp"
  without further qualification.

This module is the central coordinator. Per-signal *evidence
producers* (mDNS scanner, AMX DDP listener, broadcast probes, active
probes) live in their own modules and emit ``Evidence`` records into
the device's ``evidence_log``. ``TierMatcher.match()`` is the consumer.

See ``OpenAVC-Discovery-Spec.md`` for the full architecture.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from server.discovery.oui_database import normalize_oui_prefix
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
# Rule kinds are scoped per signal so an mdns rule cannot accidentally
# collide with an active-probe rule.
KIND_MDNS = "mdns"
KIND_SSDP = "ssdp"
KIND_AMX_DDP = "amx_ddp"
KIND_BROADCAST = "broadcast"     # driver-declared udp_probe / companion broadcast IDs
KIND_ACTIVE_PROBE = "probe"      # driver-declared tcp_probe / companion active IDs

KIND_OUI = "oui"                       # enrichment
KIND_SNMP_PEN = "snmp_pen"             # enrichment
KIND_HOSTNAME = "hostname"             # enrichment
KIND_OPEN_PORT = "open_port"           # enrichment — AV-specific port observed open
KIND_VENDOR_STRING = "vendor_string"   # enrichment — manufacturer string from a probe response


_STRONG_KINDS = {
    KIND_MDNS, KIND_SSDP, KIND_AMX_DDP, KIND_BROADCAST, KIND_ACTIVE_PROBE,
}
_SOFT_KINDS = {
    KIND_OUI, KIND_SNMP_PEN, KIND_HOSTNAME, KIND_OPEN_PORT, KIND_VENDOR_STRING,
}


# Cross-vendor / generic flagging is per-driver. A driver declares
# ``cross_vendor: true`` on its fingerprint when the wire response
# identifies a protocol class but not a specific vendor — for example
# a multi-vendor projector control protocol or an unfiltered camera
# discovery beacon. The schema parser passes that flag through to
# ``SignalRule.generic`` at index-build time, and the matcher consults
# enrichment evidence (OUI / hostname / manufacturer alias) to pick a
# vendor-specific peer when a generic rule wins.


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
            - ``KIND_MDNS``: service type, e.g. ``"_example._tcp.local."``
            - ``KIND_SSDP``: UPnP device type URN
            - ``KIND_AMX_DDP``: ``"<Make>/<ModelGlob>"``
            - ``KIND_BROADCAST``: ``custom_<driver_id>_udp`` for a
              declarative ``udp_probe:`` or
              ``custom_<driver_id>_companion_udp`` for a Python
              companion's broadcast ID.
            - ``KIND_ACTIVE_PROBE``: ``custom_<driver_id>_tcp`` for a
              declarative ``tcp_probe:`` or
              ``custom_<driver_id>_companion_tcp`` for a Python
              companion's active ID.
            - ``KIND_OUI``: 6-char OUI prefix, lowercase, e.g. ``"00:0c:4d"``
            - ``KIND_SNMP_PEN``: integer Private Enterprise Number as string
            - ``KIND_HOSTNAME``: regex source string (compiled lazily by the index)
            - ``KIND_OPEN_PORT``: port number as string, e.g. ``"4352"``
            - ``KIND_VENDOR_STRING``: lowercased manufacturer alias
        txt_match: Optional observed-field filter. The signal matches only
            when every key in this dict is present in the observation's
            field map and matches the value (case-insensitive). For mDNS
            the fields are the TXT record; for SSDP they are the UPnP
            device-description fields (model / manufacturer /
            friendly_name). Used to disambiguate shared source IDs
            (``_http._tcp``, a family-wide UPnP device-type URN) by
            requiring a manufacturer or model field.
        evidence_data: Optional static data merged into the evidence
            record when this rule matches. Used to pre-fill manufacturer
            / model when the signal alone implies them.
        generic: True when this rule's fingerprint identifies a protocol
            class but not a specific vendor (driver declared
            ``cross_vendor: true``). When a generic rule wins, the
            matcher consults enrichment evidence for a vendor-specific
            peer driver and demotes the generic to alternative.
    """

    driver_id: str
    tier: SignalTier
    kind: str
    source_id: str
    txt_match: tuple[tuple[str, str], ...] = ()
    evidence_data: tuple[tuple[str, str], ...] = ()
    generic: bool = False

    @classmethod
    def for_mdns(
        cls,
        driver_id: str,
        service_type: str,
        txt_match: dict[str, str] | None = None,
        *,
        generic: bool = False,
    ) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.PASSIVE_LISTENER,
            kind=KIND_MDNS,
            source_id=_normalize_service_type(service_type),
            txt_match=_freeze_dict(txt_match),
            generic=generic,
        )

    @classmethod
    def for_ssdp(
        cls,
        driver_id: str,
        device_type: str,
        txt_match: dict[str, str] | None = None,
        *,
        generic: bool = False,
    ) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.PASSIVE_LISTENER,
            kind=KIND_SSDP,
            source_id=device_type,
            txt_match=_freeze_dict(txt_match),
            generic=generic,
        )

    @classmethod
    def for_amx_ddp(
        cls,
        driver_id: str,
        make: str,
        model_pattern: str,
        *,
        generic: bool = False,
    ) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.PASSIVE_LISTENER,
            kind=KIND_AMX_DDP,
            source_id=f"{make}/{model_pattern}",
            generic=generic,
        )

    @classmethod
    def for_broadcast(
        cls,
        driver_id: str,
        probe_id: str,
        txt_match: dict[str, str] | None = None,
        *,
        generic: bool = False,
    ) -> "SignalRule":
        """Build a broadcast-probe rule.

        ``txt_match`` lets multiple drivers safely claim a shared probe
        ID by attaching a manufacturer/model filter — the responder's
        parsed identification fields are matched against the filter at
        lookup time.

        ``generic`` mirrors the driver's ``cross_vendor:`` flag. When
        true, a winning match consults enrichment evidence for a
        vendor-specific peer to demote to alternative.
        """
        return cls(
            driver_id=driver_id,
            tier=SignalTier.BROADCAST_PROBE,
            kind=KIND_BROADCAST,
            source_id=probe_id,
            txt_match=_freeze_dict(txt_match),
            generic=generic,
        )

    @classmethod
    def for_active_probe(
        cls,
        driver_id: str,
        probe_id: str,
        *,
        generic: bool = False,
    ) -> "SignalRule":
        """Build an active-probe rule. ``generic`` mirrors ``cross_vendor:``."""
        return cls(
            driver_id=driver_id,
            tier=SignalTier.ACTIVE_PROBE,
            kind=KIND_ACTIVE_PROBE,
            source_id=probe_id,
            generic=generic,
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

    @classmethod
    def for_open_port(cls, driver_id: str, port: int) -> "SignalRule":
        return cls(
            driver_id=driver_id,
            tier=SignalTier.ENRICHMENT,
            kind=KIND_OPEN_PORT,
            source_id=str(port),
        )

    @classmethod
    def for_vendor_string(cls, driver_id: str, alias: str) -> "SignalRule":
        """Build an enrichment manufacturer-alias rule.

        ``alias`` is normalized to ``alias.strip().lower()`` so the
        index lookup is a plain dict hit. Multiple drivers may declare
        the same alias — vendor strings are soft signals like OUI and
        produce a multi-candidate ``possible`` result when no other
        narrowing signal is present.
        """
        return cls(
            driver_id=driver_id,
            tier=SignalTier.ENRICHMENT,
            kind=KIND_VENDOR_STRING,
            source_id=alias.strip().lower(),
        )


def _normalize_service_type(service: str) -> str:
    """Return service type with a trailing dot, lowercase up to the dot."""
    s = service.strip()
    if not s.endswith("."):
        s = s + "."
    return s.lower()


def _normalize_mac_prefix(prefix: str) -> str:
    """Normalize an OUI prefix or MAC to canonical ``xx:xx:xx``.

    Delegates to the shared canonicalizer so rule registration (``for_oui``)
    and lookup (``find_soft_oui`` / ``evidence_oui``) always agree on the key
    regardless of the caller's separator style — dotted MACs included. Returns
    ``''`` for a value with no usable OUI so a malformed entry never matches
    (parse-time validation already warns on those).
    """
    return normalize_oui_prefix(prefix) or ""


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

        # Soft signals (enrichment) deliberately allow multiple drivers
        # per source_id — that is what produces the "possible" state with
        # a candidate list. No collision check.
        if rule.kind in _SOFT_KINDS:
            bucket.append(rule)
            return

        # Strong signals (passive_listener / broadcast_probe / active_probe)
        # must be unambiguous. Two drivers cannot both claim the same
        # (kind, source_id) with the same txt_match filter, and a generic
        # (no-filter) rule cannot coexist with a filtered rule for the
        # same source_id.
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
        observed = {k.lower(): str(v) for k, v in (txt or {}).items()}
        if kind == KIND_AMX_DDP:
            # AMX-DDP source_ids are "<make>/<model_glob>" patterns, so the
            # observed concrete "<make>/<model>" must be glob-matched, not
            # looked up by exact key (which never matches a wildcard model).
            return self._find_amx_ddp_glob(source_id, observed)
        normalized_source = self._normalize_source_for_kind(kind, source_id)
        bucket = self._rules.get((kind, normalized_source), [])
        if not bucket:
            return None
        # Filter rules: pick the one whose txt_match (if any) is satisfied.
        # When multiple match, prefer the most-specific filter (longest dict).
        matching = [r for r in bucket if _txt_match_satisfied(r.txt_match, observed)]
        if not matching:
            return None
        matching.sort(key=lambda r: -len(r.txt_match))
        return matching[0]

    def _find_amx_ddp_glob(
        self, source_id: str, observed_txt: dict[str, str],
    ) -> SignalRule | None:
        """Glob-match an observed AMX-DDP ``<make>/<model>`` against the
        registered ``<make>/<model_pattern>`` rules, case-insensitively.

        A real beacon carries a concrete model (``Polycom/SoundStructureC16``)
        while rules register a glob (``Polycom/SoundStructureC*``), so this
        can't be an exact dict lookup. When several patterns match, the most
        specific one (most literal characters, then most TXT constraints) wins.
        """
        observed = source_id.strip().lower()
        matching = [
            rule
            for (rkind, _rsid), bucket in self._rules.items()
            if rkind == KIND_AMX_DDP
            for rule in bucket
            if fnmatch.fnmatchcase(observed, rule.source_id.strip().lower())
            and _txt_match_satisfied(rule.txt_match, observed_txt)
        ]
        if not matching:
            return None

        def _rank(rule: SignalRule) -> tuple[int, int, str]:
            pat = rule.source_id.strip().lower()
            wildcards = pat.count("*") + pat.count("?") + pat.count("[")
            return (len(pat) - wildcards, len(rule.txt_match), pat)

        matching.sort(key=_rank, reverse=True)
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

    def find_soft_open_port(self, port: int | None) -> list[str]:
        """Return driver_ids whose open_ports declaration includes this port.

        May be empty. Soft signal — multiple drivers can reference the
        same port, producing a `possible` candidate list.
        """
        if port is None:
            return []
        rules = self._rules.get((KIND_OPEN_PORT, str(port)), [])
        return [r.driver_id for r in rules]

    def find_soft_vendor_string(self, value: str | None) -> list[str]:
        """Return driver_ids whose ``vendor_aliases`` include this string.

        Match is case-insensitive exact (after ``.strip().lower()``).
        Empty / None input returns ``[]``.
        """
        if not value:
            return []
        normalized = value.strip().lower()
        if not normalized:
            return []
        rules = self._rules.get((KIND_VENDOR_STRING, normalized), [])
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

    def matched_hostname_patterns(self, hostname: str | None) -> list[str]:
        """Return the regex source strings whose pattern matches ``hostname``.

        Used by the scan engine at hostname-resolution time so each
        evidence record can carry the specific pattern that fired (for
        the scan-results "Why?" reveal). De-duplicated, order preserved
        from the underlying rule registration order.
        """
        if not hostname:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for pat, rule in self._hostname_rules:
            if pat.search(hostname) and rule.source_id not in seen:
                seen.add(rule.source_id)
                out.append(rule.source_id)
        return out

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
    passive_listener -> broadcast_probe -> active_probe. Soft signals
    (enrichment) only contribute to ``possible`` state when no strong
    tier matched.
    """

    index: SignalIndex
    # Optional override: candidates ranked by (driver_id, oui_prefix_observed)
    # for tie-breaking in possible state. None = first-encountered order.
    candidate_ranker: object | None = field(default=None, repr=False)

    def match(self, evidence_log: list[Evidence]) -> IdentificationMatch:
        """Run the deterministic dispatch."""
        # passive_listener tier
        for ev in evidence_log:
            if ev.tier != SignalTier.PASSIVE_LISTENER:
                continue
            rule = self._lookup_strong(ev)
            if rule:
                return self._finalize_strong_match(rule, ev, evidence_log)

        # broadcast_probe tier
        for ev in evidence_log:
            if ev.tier != SignalTier.BROADCAST_PROBE:
                continue
            rule = self._lookup_strong(ev)
            if rule:
                return self._finalize_strong_match(rule, ev, evidence_log)

        # active_probe tier
        for ev in evidence_log:
            if ev.tier != SignalTier.ACTIVE_PROBE:
                continue
            rule = self._lookup_strong(ev)
            if rule:
                return self._finalize_strong_match(rule, ev, evidence_log)

        # enrichment soft signals -> possible state if any narrows the candidate set
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

    def _finalize_strong_match(
        self,
        rule: SignalRule,
        ev: Evidence,
        evidence_log: list[Evidence],
    ) -> IdentificationMatch:
        """Build the IdentificationMatch for a winning strong-tier rule.

        Vendor-specific rules stand alone — enrichment signals are
        ignored. Generic rules (``cross_vendor: true``) are
        protocol-class winners; if an enrichment signal also produced a
        vendor-specific candidate, that vendor driver becomes the
        primary "best fit" and the generic driver demotes to the
        trailing alternative.
        """
        if not rule.generic:
            return IdentificationMatch.identified(
                driver_id=rule.driver_id,
                source=f"{rule.kind}:{rule.source_id}",
                evidence=[ev],
            )

        peers, soft_source = self._pick_demotion_target(
            evidence_log, anchor=rule.driver_id,
        )
        if not peers:
            # No vendor-specific peer corroborated by a soft signal — or
            # the narrowest signal uniquely points at the anchor itself,
            # which is positive evidence the anchor is the right driver
            # rather than ambiguous "generic protocol observed". Fall
            # back to the same shape as a vendor-specific identify.
            return IdentificationMatch.identified(
                driver_id=rule.driver_id,
                source=f"{rule.kind}:{rule.source_id}",
                evidence=[ev],
            )

        primary = peers[0]
        alternatives = peers[1:] + [rule.driver_id]
        soft_evidence = [
            e for e in evidence_log if e.tier == SignalTier.ENRICHMENT
        ]
        return IdentificationMatch.identified(
            driver_id=primary,
            source=soft_source or f"{rule.kind}:{rule.source_id}",
            alternatives=alternatives,
            evidence=[ev, *soft_evidence],
        )

    def _lookup_strong(self, ev: Evidence) -> SignalRule | None:
        """Find a strong-tier rule for an evidence record."""
        kind = ev.data.get("kind")
        source_id = ev.data.get("source_id")
        if not isinstance(kind, str) or not isinstance(source_id, str):
            return None
        txt = ev.data.get("txt") if isinstance(ev.data.get("txt"), dict) else None
        return self.index.find_strong(kind, source_id, txt)

    def _collect_soft_signal_results(
        self, evidence_log: list[Evidence],
    ) -> list[tuple[str, list[str]]]:
        """Per-signal driver_id hits from every enrichment evidence record.

        Returns ``[(source_label, [driver_ids]), ...]`` in evidence-log
        order. Each tuple is one signal's hit list — the smallest
        ``len(hits)`` is the narrowest signal, used by both
        ``_gather_soft_candidates`` (for the possible-state path) and
        ``_pick_demotion_target`` (for cross-vendor demotion).
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
            elif kind == KIND_OPEN_PORT and isinstance(value, int):
                hits = self.index.find_soft_open_port(value)
                if hits:
                    results.append((f"{KIND_OPEN_PORT}:{value}", hits))
            elif kind == KIND_VENDOR_STRING and isinstance(value, str):
                hits = self.index.find_soft_vendor_string(value)
                if hits:
                    results.append((f"{KIND_VENDOR_STRING}:{value}", hits))
        return results

    def _pick_demotion_target(
        self, evidence_log: list[Evidence], *, anchor: str,
    ) -> tuple[list[str], str]:
        """Choose vendor-specific peers to promote ahead of a cross-vendor anchor.

        Walks soft signals in narrowness order (smallest hit count first;
        source label as the deterministic tiebreak). The first signal
        that produces a non-anchor peer is the demotion source — that
        signal's peers come first, then any peer surfaced by broader
        signals as alternatives.

        Returns ``([], "")`` to signal "no demotion, anchor wins" in two
        cases:

        - The narrowest signal's hit set is exactly ``[anchor]``. That
          signal specifically corroborates the anchor (a hostname pattern
          declared only by the anchor matched, etc.), so the anchor is
          the right driver and the broader signals' peer overlap is not
          enough to override it. Without this guard, a device whose
          hostname pattern narrows uniquely to the cross-vendor anchor
          but whose OUI / manufacturer alias also overlaps a peer driver
          (a vendor-specific sibling under the same OUI block) would be
          misidentified as the peer.
        - No signal yields a non-anchor peer at all.
        """
        results = self._collect_soft_signal_results(evidence_log)
        if not results:
            return [], ""

        # Tightest signal first; deterministic tiebreak by source label.
        results.sort(key=lambda r: (len(r[1]), r[0]))

        for source, hits in results:
            peers = [d for d in hits if d != anchor]
            if not peers:
                # This signal narrows to the anchor (or to nothing useful).
                # If it's the narrowest signal — i.e. the very first one
                # iterated — its specificity outweighs any broader
                # peer-overlap signal.
                if hits == [anchor]:
                    return [], ""
                continue

            # Build the promotion order: this signal's peers first
            # (narrowness), then any peer surfaced by broader signals
            # (deduped, original-order).
            ordered: list[str] = list(dict.fromkeys(peers))
            seen = set(ordered)
            for src2, hits2 in results:
                if src2 == source:
                    continue
                for d in hits2:
                    if d != anchor and d not in seen:
                        seen.add(d)
                        ordered.append(d)
            return ordered, source

        return [], ""

    def _gather_soft_candidates(
        self, evidence_log: list[Evidence],
    ) -> tuple[list[str], str]:
        """Collect candidate driver_ids from soft signals.

        Returns (candidates, source_label). Source label points at the
        signal that produced the narrowest candidate set; ties broken by
        first observation.
        """
        results = self._collect_soft_signal_results(evidence_log)

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


def evidence_broadcast(
    probe_id: str,
    response: dict | None = None,
    txt: dict[str, str] | None = None,
    *,
    port: int | None = None,
    matched_pattern: str | None = None,
) -> Evidence:
    """Build an Evidence record for a broadcast probe response.

    ``txt`` carries identification fields parsed from the responder
    (manufacturer, model, hardware id) so the matcher can distinguish
    drivers that share a generic fingerprint — e.g. several drivers
    claim a common discovery beacon, each adding a different
    manufacturer filter.

    ``port`` is the UDP port the probe targeted (from
    ``udp_probe.port``) and ``matched_pattern`` is a human-readable
    description of the regex / hex / substring matcher that the
    response satisfied (e.g. ``"regex:<vendor-pattern>"``,
    ``"hex:deadbeef"``). Both feed the scan-results "Why?" reveal.
    """
    data: dict[str, Any] = {
        "kind": KIND_BROADCAST,
        "source_id": probe_id,
        "response": response or {},
    }
    if txt:
        data["txt"] = dict(txt)
    if port is not None:
        data["port"] = port
    if matched_pattern is not None:
        data["matched_pattern"] = matched_pattern
    return Evidence(
        tier=SignalTier.BROADCAST_PROBE,
        source=f"broadcast:{probe_id}",
        data=data,
    )


def evidence_active_probe(
    probe_id: str,
    response: dict | None = None,
    *,
    port: int | None = None,
    matched_pattern: str | None = None,
) -> Evidence:
    """Build an Evidence record for an active-probe response.

    ``port`` is the TCP port the probe targeted (from
    ``tcp_probe.port``) and ``matched_pattern`` is a human-readable
    description of the regex / hex / substring matcher that the
    response satisfied (e.g. ``"regex:Lightware"``, ``"hex:aaff..."``).
    Both feed the scan-results "Why?" reveal: the UI prefers
    "TCP probe on port <port> returned <excerpt>" when the response
    decodes to readable text and falls back to "TCP probe on port
    <port> matched <pattern>" for binary protocols whose response
    excerpt would be gibberish.
    """
    data: dict[str, Any] = {
        "kind": KIND_ACTIVE_PROBE,
        "source_id": probe_id,
        "response": response or {},
    }
    if port is not None:
        data["port"] = port
    if matched_pattern is not None:
        data["matched_pattern"] = matched_pattern
    return Evidence(
        tier=SignalTier.ACTIVE_PROBE,
        source=f"probe:{probe_id}",
        data=data,
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


def evidence_hostname(
    hostname: str,
    *,
    matched_pattern: str | None = None,
) -> Evidence:
    """Build an Evidence record for an observed hostname.

    ``matched_pattern`` is the regex source string from the driver
    rule whose pattern matched ``hostname``. The engine emits one
    record per matching pattern at scan time; if no driver pattern
    matches the hostname, a single record with ``matched_pattern=None``
    is emitted as a generic audit-trail entry.
    """
    data: dict[str, Any] = {
        "kind": KIND_HOSTNAME,
        "value": hostname,
    }
    if matched_pattern is not None:
        data["matched_pattern"] = matched_pattern
    return Evidence(
        tier=SignalTier.ENRICHMENT,
        source=f"hostname:{hostname}",
        data=data,
    )


def extract_vendor_strings(evidence_log: list[Evidence]) -> list[Evidence]:
    """Mine strong-tier evidence for manufacturer strings and emit enrichment hints.

    The engine calls this after all probe phases land their strong evidence,
    once per device, to surface ``manufacturer`` / ``make`` strings the
    device returned in its probe responses as ``vendor_string`` enrichment
    evidence the matcher can consult against ``vendor_aliases``.

    Looks at:
    - ``data["response"]["manufacturer"]`` and ``["make"]`` (broadcast / active probes)
    - ``data["txt"]["manufacturer"]`` and ``["make"]`` (mDNS, broadcast probes)
    - ``data["manufacturer"]`` and ``data["make"]`` (top-level: SSDP/UPnP
      rootDesc manufacturer, AMX DDP make)

    De-duplicates by ``(value, source_probe_id)`` so the same string from
    one probe doesn't get emitted twice.
    """
    seen: set[tuple[str, str]] = set()
    extracted: list[Evidence] = []

    def _record(value: object, source_probe_id: str) -> None:
        if not isinstance(value, str):
            return
        normalized = value.strip().lower()
        if not normalized:
            return
        key = (normalized, source_probe_id)
        if key in seen:
            return
        seen.add(key)
        extracted.append(evidence_vendor_string(value, source_probe_id))

    for ev in evidence_log:
        if ev.tier == SignalTier.ENRICHMENT:
            continue  # Don't recurse on already-emitted enrichment records.

        kind = ev.data.get("kind")
        source_id = ev.data.get("source_id")
        probe_label = source_id if isinstance(source_id, str) else (kind or "unknown")

        response = ev.data.get("response")
        if isinstance(response, dict):
            _record(response.get("manufacturer"), probe_label)
            _record(response.get("make"), probe_label)

        txt = ev.data.get("txt")
        if isinstance(txt, dict):
            _record(txt.get("manufacturer"), probe_label)
            _record(txt.get("make"), probe_label)

        # Top-level manufacturer/make. SSDP/UPnP puts the rootDesc.xml
        # <manufacturer> here — and a UPnP switch/AP often advertises only
        # the generic InternetGatewayDevice device type, so the vendor
        # string is its one usable identity signal. AMX DDP carries its
        # make here too.
        _record(ev.data.get("manufacturer"), probe_label)
        _record(ev.data.get("make"), probe_label)

    return extracted


def evidence_vendor_string(value: str, source_probe_id: str) -> Evidence:
    """Build an Evidence record for a manufacturer string lifted from a
    fingerprint probe response.

    ``value`` is normalized to ``.strip().lower()``; the original raw
    string is preserved in ``data["raw"]`` for the "Why?" UI reveal.
    ``source_probe_id`` records which fingerprint probe produced the
    string (the canonical synthetic ID from ``hints.py``) — also
    surfaced in the audit trail.
    """
    normalized = value.strip().lower()
    return Evidence(
        tier=SignalTier.ENRICHMENT,
        source=f"vendor_string:{normalized}",
        data={
            "kind": KIND_VENDOR_STRING,
            "value": normalized,
            "raw": value,
            "source_probe_id": source_probe_id,
        },
    )


def evidence_open_port(port: int) -> Evidence:
    """Build an Evidence record for an observed open port.

    The engine emits one of these per device for every port that
    appears in both the device's port-scan results and at least one
    driver's ``open_ports:`` declaration. Bare port openness is too
    weak a signal to emit unconditionally.
    """
    return Evidence(
        tier=SignalTier.ENRICHMENT,
        source=f"open_port:{port}",
        data={
            "kind": KIND_OPEN_PORT,
            "value": port,
        },
    )
