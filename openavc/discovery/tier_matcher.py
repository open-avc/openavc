"""Deterministic identification dispatcher for the discovery redesign.

Replaces the heuristic ``DriverMatcher`` (additive scoring on weak signals)
with a hash-lookup dispatcher that returns one of three states based on
the strongest signal observed.

Design contract
---------------
- A device is ``identified`` if and only if a strong signal
  (``passive_listener`` / ``broadcast_probe`` / ``active_probe``)
  matches a deterministic SignalRule. There is no scoring; the rule
  either matches or it does not. When rules for more than one driver
  match, every one of them is offered (the rest as ``alternatives``),
  vendor-specific before cross-vendor, in an order that does not depend
  on the order the signals arrived in.
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

from openavc.discovery.oui_database import mac_prefix_keys, normalize_oui_prefix
from openavc.discovery.result import (
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
            - ``KIND_OUI``: OUI prefix, lowercase, e.g. ``"00:0c:4d"``, or
              a 28/36-bit IEEE block, e.g. ``"18:66:96:1"``
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
    """Normalize an OUI prefix or MAC to canonical ``xx:xx:xx`` (a 28 or
    36-bit block keeps its length: ``oui_database.normalize_oui_prefix``).

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
        """Look up a strong-signal rule. Returns None if no match.

        The most specific of ``find_strong_all``'s rules.
        """
        matching = self.find_strong_all(kind, source_id, txt)
        return matching[0] if matching else None

    def find_strong_all(
        self,
        kind: str,
        source_id: str,
        txt: dict[str, str] | None = None,
    ) -> list[SignalRule]:
        """Every strong-signal rule the observation satisfies, most specific first.

        Two rules can both be satisfied when they share a source and filter
        on different fields (one on ``model``, one on ``manufacturer``), or,
        for AMX DDP, when two model globs both match the beacon.
        """
        if kind not in _STRONG_KINDS:
            return []
        observed = {k.lower(): str(v) for k, v in (txt or {}).items()}
        if kind == KIND_AMX_DDP:
            # AMX-DDP source_ids are "<make>/<model_glob>" patterns, so the
            # observed concrete "<make>/<model>" must be glob-matched, not
            # looked up by exact key (which never matches a wildcard model).
            return self._find_amx_ddp_globs(source_id, observed)
        normalized_source = self._normalize_source_for_kind(kind, source_id)
        bucket = self._rules.get((kind, normalized_source), [])
        # Filter rules: keep the ones whose txt_match (if any) is satisfied,
        # the most-specific filter (longest dict) first. Equally specific
        # rules go by driver id, not by the order the catalog registered them.
        matching = [r for r in bucket if _txt_match_satisfied(r.txt_match, observed)]
        matching.sort(key=lambda r: (-len(r.txt_match), r.driver_id))
        return matching

    def _find_amx_ddp_globs(
        self, source_id: str, observed_txt: dict[str, str],
    ) -> list[SignalRule]:
        """Glob-match an observed AMX-DDP ``<make>/<model>`` against the
        registered ``<make>/<model_pattern>`` rules, case-insensitively.

        A real beacon carries a concrete model (``Polycom/SoundStructureC16``)
        while rules register a glob (``Polycom/SoundStructureC*``), so this
        can't be an exact dict lookup. When several patterns match, the most
        specific one (most literal characters, then most TXT constraints)
        comes first; equally specific ones go by driver id.
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

        def _rank(rule: SignalRule) -> tuple[int, int, str]:
            pat = rule.source_id.strip().lower()
            wildcards = pat.count("*") + pat.count("?") + pat.count("[")
            return (len(pat) - wildcards, len(rule.txt_match), pat)

        # The driver-id pass first: a reversed sort keeps equal keys in order.
        matching.sort(key=lambda r: r.driver_id)
        matching.sort(key=_rank, reverse=True)
        return matching

    def find_soft_oui(self, mac: str) -> list[str]:
        """Return driver_ids whose OUI prefix matches the MAC. May be empty."""
        return self.find_soft_oui_block(mac)[1]

    def find_soft_oui_block(self, mac: str) -> tuple[str, list[str]]:
        """The declared prefix a MAC falls in and the drivers declaring it.

        The longest declared prefix wins: a maker's own MA-M or MA-S block
        (``18:66:96:1``) over a driver claiming the whole /24 around it,
        which the IEEE Registration Authority has split among other
        companies. With none declared, the MAC's three-octet prefix and no
        drivers. ``mac`` may be a full MAC or a prefix; a prefix reaches
        only the blocks its digits cover.
        """
        keys = mac_prefix_keys(mac) if mac else []
        for key in keys:
            rules = self._rules.get((KIND_OUI, key), [])
            if rules:
                return key, [r.driver_id for r in rules]
        return (keys[-1] if keys else ""), []

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

    def vendor_aliases(self) -> list[str]:
        """Every manufacturer alias some driver declares, normalized."""
        return sorted(sid for kind, sid in self._rules if kind == KIND_VENDOR_STRING)

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


# The strong tiers in the order they are trusted. A match from an earlier
# tier ranks ahead of one from a later tier of the same kind (vendor-specific
# or cross-vendor); every tier is read.
_STRONG_TIERS = (
    SignalTier.PASSIVE_LISTENER,
    SignalTier.BROADCAST_PROBE,
    SignalTier.ACTIVE_PROBE,
)


@dataclass
class TierMatcher:
    """Deterministic identification dispatcher.

    Given the full ``evidence_log`` of a device, returns one
    ``IdentificationMatch``. Every driver a strong signal identifies is
    offered: one as ``driver_id``, the rest as ``alternatives``.
    Vendor-specific matches rank ahead of cross-vendor ones whatever tier
    they came from, and within each, tiers rank in order passive_listener
    -> broadcast_probe -> active_probe. Soft signals (enrichment) only
    contribute to ``possible`` state when no strong tier matched, and to
    cross-vendor demotion when only cross-vendor rules did.
    """

    index: SignalIndex
    # Optional override: candidates ranked by (driver_id, oui_prefix_observed)
    # for tie-breaking in possible state. None = first-encountered order.
    candidate_ranker: object | None = field(default=None, repr=False)

    def match(self, evidence_log: list[Evidence]) -> IdentificationMatch:
        """Run the deterministic dispatch."""
        hits = self._strong_hits(evidence_log)
        if hits:
            return self._finalize_strong_match(hits, evidence_log)

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

    def _strong_hits(
        self, evidence_log: list[Evidence],
    ) -> list[tuple[SignalRule, Evidence]]:
        """Every driver a strong signal identifies, best first, once each.

        Vendor-specific rules come before cross-vendor ones, whatever tier
        each came from: a cross-vendor rule names a protocol, not the
        driver, however trusted the signal that carried it. Within each,
        the earlier tier first. One record's rules keep the index's order
        (most specific first). Between records of one tier nothing says which
        driver fits better, so the tie goes by driver id: the log is in the
        order probes answered and announcements arrived, and the answer must
        not depend on that. A driver a vendor-specific rule identifies counts
        as vendor-specific even if a cross-vendor rule names it too.
        """
        ranked: list[tuple[bool, int, int, str, str, SignalRule, Evidence]] = []
        for ev in evidence_log:
            if ev.tier not in _STRONG_TIERS:
                continue
            tier = _STRONG_TIERS.index(ev.tier)
            for position, rule in enumerate(strong_rules(ev, self.index)):
                ranked.append(
                    (rule.generic, tier, position, rule.driver_id, ev.source, rule, ev),
                )
        ranked.sort(key=lambda hit: hit[:5])

        hits: list[tuple[SignalRule, Evidence]] = []
        seen: set[str] = set()
        for *_, rule, ev in ranked:
            if rule.driver_id not in seen:
                seen.add(rule.driver_id)
                hits.append((rule, ev))
        return hits

    def _finalize_strong_match(
        self,
        hits: list[tuple[SignalRule, Evidence]],
        evidence_log: list[Evidence],
    ) -> IdentificationMatch:
        """Build the IdentificationMatch from the strong matches.

        ``hits`` is ``_strong_hits``: every driver a strong signal
        identifies, best first. When a vendor-specific rule matched, its
        driver is the
        identification and every other hit is an alternative; enrichment
        signals are not consulted, since a fingerprint already said which
        driver. When only cross-vendor rules (``cross_vendor: true``)
        matched, they identify a protocol class: if an enrichment signal
        produced a vendor-specific candidate, that vendor driver becomes the
        primary "best fit" and the cross-vendor drivers trail as
        alternatives.
        """
        strong_evidence = _distinct_records([ev for _, ev in hits])
        best = hits[0][0]
        best_source = f"{best.kind}:{best.source_id}"
        matched = [rule.driver_id for rule, _ in hits]

        if not best.generic:
            return IdentificationMatch.identified(
                driver_id=best.driver_id,
                source=best_source,
                alternatives=matched[1:],
                evidence=strong_evidence,
            )

        peers, soft_source = self._pick_demotion_target(
            evidence_log, anchors=set(matched),
        )
        if not peers:
            # No vendor-specific peer corroborated by a soft signal — or
            # the narrowest signal points only at the cross-vendor drivers
            # themselves, which is positive evidence one of them is right
            # rather than ambiguous "generic protocol observed". Fall back
            # to the same shape as a vendor-specific identify.
            return IdentificationMatch.identified(
                driver_id=best.driver_id,
                source=best_source,
                alternatives=matched[1:],
                evidence=strong_evidence,
            )

        soft_evidence = [
            e for e in evidence_log if e.tier == SignalTier.ENRICHMENT
        ]
        return IdentificationMatch.identified(
            driver_id=peers[0],
            source=soft_source or best_source,
            alternatives=peers[1:] + matched,
            evidence=[*strong_evidence, *soft_evidence],
        )

    def _collect_soft_signal_results(
        self, evidence_log: list[Evidence],
    ) -> list[tuple[str, list[str]]]:
        """Per-signal driver_id hits from every enrichment evidence record.

        Returns ``[(source_label, [driver_ids]), ...]`` in evidence-log
        order, for the records that hit at least one driver. Each tuple is
        one signal's hit list — the smallest ``len(hits)`` is the narrowest
        signal, used by both ``_gather_soft_candidates`` (for the
        possible-state path) and ``_pick_demotion_target`` (for cross-vendor
        demotion).
        """
        results: list[tuple[str, list[str]]] = []
        for ev in evidence_log:
            if ev.tier != SignalTier.ENRICHMENT:
                continue
            hit = soft_signal_hits(ev, self.index)
            if hit is not None and hit[1]:
                results.append(hit)
        return results

    def _pick_demotion_target(
        self, evidence_log: list[Evidence], *, anchors: set[str],
    ) -> tuple[list[str], str]:
        """Choose vendor-specific peers to promote ahead of the cross-vendor anchors.

        ``anchors`` are the drivers whose cross-vendor rules matched. Walks
        soft signals in narrowness order (smallest hit count first; source
        label as the deterministic tiebreak). The narrowest signal decides:
        if it names a driver that is not an anchor, it is the demotion
        source — that signal's peers come first, then any peer surfaced by
        broader signals as alternatives.

        Returns ``([], "")`` to signal "no demotion, an anchor wins" in two
        cases:

        - The narrowest signal names only anchors. That signal
          specifically corroborates them (a hostname pattern declared only
          by an anchor matched, etc.), so the broader signals' peer overlap
          is not enough to override it. Without this guard, a device whose
          hostname pattern narrows uniquely to the cross-vendor anchor but
          whose OUI / manufacturer alias also overlaps a peer driver (a
          vendor-specific sibling under the same OUI block) would be
          misidentified as the peer.
        - No soft signal names any driver at all.
        """
        results = self._collect_soft_signal_results(evidence_log)
        if not results:
            return [], ""

        # Tightest signal first; deterministic tiebreak by source label.
        results.sort(key=lambda r: (len(r[1]), r[0]))
        source, hits = results[0]
        peers = [d for d in hits if d not in anchors]
        if not peers:
            return [], ""

        # Build the promotion order: this signal's peers first
        # (narrowness), then any peer surfaced by broader signals
        # (deduped, original-order).
        ordered: list[str] = list(dict.fromkeys(peers))
        seen = set(ordered)
        for _src, hits2 in results[1:]:
            for d in hits2:
                if d not in anchors and d not in seen:
                    seen.add(d)
                    ordered.append(d)
        return ordered, source

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


def _distinct_records(records: list[Evidence]) -> list[Evidence]:
    """``records`` with repeats of the same record dropped, order kept."""
    seen: set[int] = set()
    out: list[Evidence] = []
    for ev in records:
        if id(ev) not in seen:
            seen.add(id(ev))
            out.append(ev)
    return out


def strong_rules(ev: Evidence, index: SignalIndex) -> list[SignalRule]:
    """Every strong-signal rule one evidence record satisfies, most specific first.

    The matcher and ``discovery/explain.py`` both read the whole list.
    Empty for a record that is not a strong signal or that no rule claims.
    """
    kind = ev.data.get("kind")
    source_id = ev.data.get("source_id")
    if not isinstance(kind, str) or not isinstance(source_id, str):
        return []
    txt = ev.data.get("txt") if isinstance(ev.data.get("txt"), dict) else None
    return index.find_strong_all(kind, source_id, txt)


def soft_signal_hits(
    ev: Evidence, index: SignalIndex,
) -> tuple[str, list[str]] | None:
    """The drivers one enrichment evidence record points at, with its label.

    Returns ``(source_label, [driver_ids])``, the list empty when no driver
    declares the signal, or None for a record that is not a soft signal the
    matcher reads. The label (``oui:00:11:22``, ``open_port:4352``) is the
    one the matcher reports as a ``possible`` state's ``source``.
    """
    kind = ev.data.get("kind")
    value = ev.data.get("value")
    if kind == KIND_OUI and isinstance(value, str):
        # The whole MAC when the record has it, so a 28 or 36-bit block is
        # reachable; the label names the block that answered.
        mac = ev.data.get("mac")
        prefix, drivers = index.find_soft_oui_block(mac if isinstance(mac, str) and mac else value)
        return f"{KIND_OUI}:{prefix}", drivers
    if kind == KIND_SNMP_PEN and isinstance(value, int):
        return f"{KIND_SNMP_PEN}:{value}", index.find_soft_pen(value)
    if kind == KIND_HOSTNAME and isinstance(value, str):
        return f"{KIND_HOSTNAME}:{value}", index.find_soft_hostname(value)
    if kind == KIND_OPEN_PORT and isinstance(value, int):
        return f"{KIND_OPEN_PORT}:{value}", index.find_soft_open_port(value)
    if kind == KIND_VENDOR_STRING and isinstance(value, str):
        return f"{KIND_VENDOR_STRING}:{value}", index.find_soft_vendor_string(value)
    return None


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
    driver_supplied: list[str] | None = None,
    supplied_by: str | None = None,
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
    ``"hex de ad be ef"``). Both feed the scan-results "Why?" reveal.
    ``driver_supplied`` names the ``txt`` fields the driver's own rule set
    to a literal (``extract_manufacturer``), not read from the reply, and
    ``supplied_by`` the driver whose rule it was.
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
    if driver_supplied:
        data["driver_supplied"] = list(driver_supplied)
        if supplied_by:
            data["supplied_by"] = supplied_by
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
    driver_supplied: list[str] | None = None,
    supplied_by: str | None = None,
) -> Evidence:
    """Build an Evidence record for an active-probe response.

    ``port`` is the TCP port the probe targeted (from
    ``tcp_probe.port``) and ``matched_pattern`` is a human-readable
    description of the regex / hex / substring matcher that the
    response satisfied (e.g. ``"regex:Lightware"``, ``"hex aa ff"``).
    Both feed the scan-results "Why?" reveal: the UI prefers
    "TCP probe on port <port> returned <excerpt>" when the response
    decodes to readable text and falls back to "TCP probe on port
    <port> matched <pattern>" for binary protocols whose response
    excerpt would be gibberish. ``driver_supplied`` names the ``response``
    fields the driver's own rule set to a literal (``extract_manufacturer``),
    not read from the reply, and ``supplied_by`` the driver whose rule it was.
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
    if driver_supplied:
        data["driver_supplied"] = list(driver_supplied)
        if supplied_by:
            data["supplied_by"] = supplied_by
    return Evidence(
        tier=SignalTier.ACTIVE_PROBE,
        source=f"probe:{probe_id}",
        data=data,
    )


def evidence_oui(mac: str, vendor: str | None = None, block: str | None = None) -> Evidence:
    """Build an Evidence record for an OUI lookup.

    ``block`` is the registered prefix the vendor name came from when it is
    longer than three octets (``OUIDatabase.lookup_block``); the record's
    value is that block, else the MAC's three-octet prefix.
    """
    prefix = _normalize_mac_prefix(block) if block else _normalize_mac_prefix(mac)
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

    Each record keeps the kind of evidence it came from (``from_kind``:
    ``probe``, ``ssdp``, ...), so a line can say where without naming a
    probe's internal id. A value the driver's own rule supplied
    (``extract_manufacturer``, listed in the probe's ``driver_supplied``) is
    marked ``from_driver``, with the probe's ``supplied_by``: it still counts
    for matching, for every driver that declares the alias, but the device did
    not say it, and only the supplying driver's probe matched.

    Looks at:
    - ``data["response"]["manufacturer"]`` and ``["make"]`` (broadcast / active probes)
    - ``data["txt"]["manufacturer"]`` and ``["make"]`` (mDNS, broadcast probes)
    - ``data["manufacturer"]`` and ``data["make"]`` (top-level: SSDP/UPnP
      rootDesc manufacturer, AMX DDP make)

    One record per string and kind of evidence, kept from the first record
    that named it: a line says only the kind, so an SSDP device naming its
    manufacturer in every NOTIFY, or two probes both naming it, would
    otherwise read as the same line over and over.
    """
    seen: set[tuple[str, str]] = set()
    extracted: list[Evidence] = []

    def _record(
        value: object, source_probe_id: str, kind: object,
        from_driver: bool = False, supplied_by: object = None,
    ) -> None:
        if not isinstance(value, str):
            return
        normalized = value.strip().lower()
        if not normalized:
            return
        by = supplied_by if from_driver and isinstance(supplied_by, str) else None
        # Two matching probes that both supply the name keep the first: one
        # line per string, and it names a driver whose probe did match.
        key = (normalized, kind if isinstance(kind, str) else source_probe_id, from_driver)
        if key in seen:
            return
        seen.add(key)
        ev = evidence_vendor_string(value, source_probe_id)
        if isinstance(kind, str):
            ev.data["from_kind"] = kind
        if from_driver:
            ev.data["from_driver"] = True
            if by:
                ev.data["supplied_by"] = by
        extracted.append(ev)

    for ev in evidence_log:
        if ev.tier == SignalTier.ENRICHMENT:
            continue  # Don't recurse on already-emitted enrichment records.

        kind = ev.data.get("kind")
        source_id = ev.data.get("source_id")
        probe_label = source_id if isinstance(source_id, str) else (kind or "unknown")
        supplied = ev.data.get("driver_supplied")
        supplied = set(supplied) if isinstance(supplied, list) else set()

        for fields in (ev.data.get("response"), ev.data.get("txt")):
            if isinstance(fields, dict):
                for name in ("manufacturer", "make"):
                    _record(fields.get(name), probe_label, kind, name in supplied,
                            ev.data.get("supplied_by"))

        # Top-level manufacturer/make. SSDP/UPnP puts the rootDesc.xml
        # <manufacturer> here — and a UPnP switch/AP often advertises only
        # the generic InternetGatewayDevice device type, so the vendor
        # string is its one usable identity signal. AMX DDP carries its
        # make here too.
        _record(ev.data.get("manufacturer"), probe_label, kind)
        _record(ev.data.get("make"), probe_label, kind)

    return extracted


# Where a manufacturer alias counts in free text (a port's greeting, a web
# server's name). The text is the device's own words, not a field that holds a
# manufacturer, so a short alias ("at", "hp", "bss") would turn up in text that
# means something else. An alias counts as the whole text or a whole line;
# with three or more letters and digits, also leading a line ("BSS Soundweb");
# with four or more, anywhere as a whole word or phrase ("... Crestron
# Webserver").
_ALIAS_AT_LINE_START = 3
_ALIAS_ANYWHERE = 4


def _alias_in_text(alias: str, text: str) -> bool:
    size = sum(c.isalnum() for c in alias)
    lines = [line.strip() for line in text.lower().splitlines() if line.strip()]
    if alias in lines:
        return True
    bounded = re.escape(alias) + r"(?![0-9a-z])"
    if size >= _ALIAS_AT_LINE_START and any(re.match(bounded, line) for line in lines):
        return True
    return size >= _ALIAS_ANYWHERE and re.search(
        r"(?<![0-9a-z])" + bounded, text.lower(),
    ) is not None


def vendor_strings_in_text(texts: dict[str, str], index: SignalIndex) -> list[Evidence]:
    """The manufacturer aliases a device names in its own free text.

    ``texts`` maps where each text came from (``greeting:23`` for what a port
    sent unprompted, ``http_server:80`` for a web server's ``Server`` header,
    ``ssdp_server`` for an SSDP ``SERVER``) to the text. Each alias the
    catalog declares that the text names (the rule at ``_alias_in_text``)
    becomes one ``vendor_string`` record, its ``raw`` the line that named it,
    unless a longer alias found in the same text contains it. Nothing is
    recorded for text that names no alias: unlike a manufacturer field, free
    text cannot say which of its words is a manufacturer.
    """
    aliases = index.vendor_aliases()
    out: list[Evidence] = []
    for where, text in texts.items():
        if not isinstance(text, str) or not text.strip():
            continue
        found = [a for a in aliases if _alias_in_text(a, text)]
        found = [a for a in found if not any(a != b and a in b for b in found)]
        for alias in found:
            line = next(
                (ln.strip() for ln in text.splitlines() if alias in ln.lower()),
                text.strip(),
            )
            ev = evidence_vendor_string(alias, where)
            ev.data["raw"] = line[:120]
            out.append(ev)
    return out


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
