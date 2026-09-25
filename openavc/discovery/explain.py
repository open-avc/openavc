"""Which drivers a device's signals point at, and how one driver's
``discovery:`` block fares against what a device showed.

``TierMatcher.match`` gives one answer per device: the first strong match,
or the soft candidates. Two questions it does not answer are answered here.

- ``explain_matches(evidence, index)`` lists, for every signal a device
  produced, every driver that signal points at, strong and soft. It picks no
  winner; the matcher does that. A device two drivers both fingerprint shows
  both here, whichever the matcher offered.
- ``evaluate_driver_signals(hint, observed)`` turns it around. Given one
  driver's declared signals and what one device showed, it says of each
  declaration whether the device matched it, did not (and what the device
  said instead), or was never seen doing that kind of thing at all. For a
  probe: what was sent on which port, what was expected, what came back.

Both ask the lookups the matcher asks (``SignalIndex``, ``strong_rules``,
``soft_signal_hits``, ``hints.signal_rules``) and take a probe's verdict
from the probe runner (``ProbeObservation.matched`` / ``miss``), so neither
can disagree with a scan about what matches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from openavc.discovery.hints import (
    CustomProbeSpec,
    DiscoveryHint,
    describe_response_match,
    signal_rules,
)
from openavc.discovery.port_scanner import PORT_OPEN
from openavc.discovery.probe_runner import (
    MISS_CERT_SUBJECT,
    MISS_CONNECT,
    MISS_FOLLOW_UP,
    MISS_NO_REPLY,
    MISS_NOT_SENT,
    MISS_REPLY,
    ProbeObservation,
)
from openavc.discovery.result import Evidence, SignalTier
from openavc.discovery.tier_matcher import (
    KIND_ACTIVE_PROBE,
    KIND_AMX_DDP,
    KIND_BROADCAST,
    KIND_HOSTNAME,
    KIND_MDNS,
    KIND_OPEN_PORT,
    KIND_OUI,
    KIND_SNMP_PEN,
    KIND_SSDP,
    KIND_VENDOR_STRING,
    SignalIndex,
    SignalRule,
    evidence_open_port,
    extract_vendor_strings,
    soft_signal_hits,
    strong_rules,
)

# The matcher's tier order: a strong signal from an earlier tier wins.
_STRONG_TIER_ORDER = (
    SignalTier.PASSIVE_LISTENER,
    SignalTier.BROADCAST_PROBE,
    SignalTier.ACTIVE_PROBE,
)

# How much of a reply a sentence quotes. The observation keeps every byte.
_QUOTE_LIMIT = 120


# ---------------------------------------------------------------------------
# Every driver each signal points at
# ---------------------------------------------------------------------------


@dataclass
class SignalHits:
    """One signal a device produced and every driver it points at.

    ``source`` is the evidence record's source, or for a soft signal the
    label the matcher reports (``oui:00:11:22``). ``drivers`` is empty when
    no driver claims the signal; for a strong signal the most specific rule's
    driver comes first.
    """

    source: str
    tier: SignalTier
    strong: bool
    drivers: list[str]
    evidence: Evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "tier": self.tier.value,
            "strong": self.strong,
            "drivers": list(self.drivers),
            "evidence": self.evidence.to_dict(),
        }


@dataclass
class MatchExplanation:
    """Every signal a device produced, in evidence order, with its drivers."""

    signals: list[SignalHits] = field(default_factory=list)

    def drivers(self) -> dict[str, list[str]]:
        """Each driver any signal points at, with the sources that do.

        Drivers a strong signal points at come first, in the matcher's tier
        order, then the drivers only soft signals point at.
        """
        out: dict[str, list[str]] = {}
        ordered = [
            s for tier in _STRONG_TIER_ORDER for s in self.signals if s.tier == tier
        ] + [s for s in self.signals if not s.strong]
        for sig in ordered:
            for driver_id in sig.drivers:
                sources = out.setdefault(driver_id, [])
                if sig.source not in sources:
                    sources.append(sig.source)
        return out

    def strong_drivers(self) -> list[str]:
        """Every driver a strong signal identifies, in the matcher's tier order."""
        out: list[str] = []
        for tier in _STRONG_TIER_ORDER:
            for sig in self.signals:
                if sig.tier == tier:
                    out.extend(d for d in sig.drivers if d not in out)
        return out

    def unclaimed(self) -> list[SignalHits]:
        """The signals no driver claims."""
        return [s for s in self.signals if not s.drivers]

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": [s.to_dict() for s in self.signals],
            "drivers": self.drivers(),
            "strong_drivers": self.strong_drivers(),
        }


def explain_matches(
    evidence: Iterable[Evidence], index: SignalIndex,
) -> MatchExplanation:
    """List every driver each of a device's signals points at.

    ``evidence`` is the device's evidence log; ``index`` the signal index the
    scan matched against. Soft signals that repeat a label (a host name the
    scan recorded once per matching pattern) are listed once.
    """
    explanation = MatchExplanation()
    soft_seen: set[str] = set()
    for ev in evidence:
        if ev.tier == SignalTier.ENRICHMENT:
            hit = soft_signal_hits(ev, index)
            label, drivers = hit if hit is not None else (ev.source, [])
            if label in soft_seen:
                continue
            soft_seen.add(label)
            explanation.signals.append(SignalHits(
                source=label, tier=ev.tier, strong=False,
                drivers=list(dict.fromkeys(drivers)), evidence=ev,
            ))
            continue
        drivers = list(dict.fromkeys(r.driver_id for r in strong_rules(ev, index)))
        explanation.signals.append(SignalHits(
            source=ev.source, tier=ev.tier, strong=True,
            drivers=drivers, evidence=ev,
        ))
    return explanation


# ---------------------------------------------------------------------------
# One driver's declared signals against one device
# ---------------------------------------------------------------------------


MATCHED = "matched"
NOT_MATCHED = "not_matched"
NOT_OBSERVED = "not_observed"


@dataclass
class DeviceObservations:
    """What one device showed, as the discovery code recorded it.

    - ``evidence``: every Evidence record built for the device, by the
      scanners' own evidence functions.
    - ``probes``: every driver-probe exchange with the device, matched or not
      (``observe_tcp_active_probe`` / ``observe_udp_probe``).
    - ``port_states``: how each scanned TCP port answered
      (``scan_host_port_states``).
    """

    evidence: list[Evidence] = field(default_factory=list)
    probes: list[ProbeObservation] = field(default_factory=list)
    port_states: dict[int, str] = field(default_factory=dict)


@dataclass
class SignalCheck:
    """One declared signal, judged against what the device showed.

    ``status`` is ``matched``, ``not_matched`` (the device showed this kind
    of signal, or answered the probe, and it did not fit) or ``not_observed``
    (nothing to judge by: nothing heard, nothing sent). ``observed`` lists
    what the device showed for this kind of signal; ``detail`` says it in one
    sentence. ``probes`` holds a probe's exchanges, every byte kept.
    """

    kind: str
    declared: str
    strong: bool
    cross_vendor: bool
    status: str
    observed: list[str]
    detail: str
    probes: list[ProbeObservation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "declared": self.declared,
            "strong": self.strong,
            "cross_vendor": self.cross_vendor,
            "status": self.status,
            "observed": list(self.observed),
            "detail": self.detail,
        }
        if self.probes:
            out["probes"] = [p.to_dict() for p in self.probes]
        return out


def evaluate_driver_signals(
    hint: DiscoveryHint, observed: DeviceObservations,
) -> list[SignalCheck]:
    """Judge each signal ``hint`` declares against what one device showed.

    One ``SignalCheck`` per declaration, in ``hints.signal_rules`` order; a
    Python companion's two probe IDs are one check.
    """
    evidence = _with_derived_evidence(observed)
    checks: list[SignalCheck] = []
    companion_done = False
    for rule in signal_rules(hint):
        single = SignalIndex()
        single.add_rule(rule)
        if rule.kind in (KIND_MDNS, KIND_SSDP, KIND_AMX_DDP):
            checks.append(_check_passive(rule, single, evidence))
        elif hint.python_probe is not None and rule.source_id in (
            hint.python_probe.broadcast_probe_id, hint.python_probe.active_probe_id,
        ):
            if not companion_done:
                companion_done = True
                checks.append(_check_companion(hint, evidence))
        elif rule.kind in (KIND_ACTIVE_PROBE, KIND_BROADCAST):
            spec = hint.tcp_probe if rule.kind == KIND_ACTIVE_PROBE else hint.udp_probe
            if spec is not None:
                checks.append(_check_probe(rule, spec, single, evidence, observed))
        elif rule.kind == KIND_OPEN_PORT:
            checks.append(_check_open_port(rule, single, evidence, observed))
        else:
            checks.append(_check_soft(rule, single, evidence))
    return checks


def _with_derived_evidence(observed: DeviceObservations) -> list[Evidence]:
    """The device's evidence plus the records a scan derives in its last phase.

    An open-port record for every open port (a scan keeps only the ports some
    driver declares; one driver's check needs them all) and the manufacturer
    strings lifted from probe replies, each once.
    """
    evidence = list(observed.evidence)
    have_ports = {
        ev.data.get("value") for ev in evidence
        if ev.data.get("kind") == KIND_OPEN_PORT
    }
    for port, state in sorted(observed.port_states.items()):
        if state == PORT_OPEN and port not in have_ports:
            evidence.append(evidence_open_port(port))
    have_vendors = {
        ev.data.get("value") for ev in evidence
        if ev.data.get("kind") == KIND_VENDOR_STRING
    }
    for ev in extract_vendor_strings(evidence):
        if ev.data.get("value") not in have_vendors:
            have_vendors.add(ev.data.get("value"))
            evidence.append(ev)
    return evidence


def _check(
    rule: SignalRule, *, declared: str, status: str, observed: list[str],
    detail: str, probes: list[ProbeObservation] | None = None,
) -> SignalCheck:
    return SignalCheck(
        kind=rule.kind,
        declared=declared,
        strong=rule.tier != SignalTier.ENRICHMENT,
        cross_vendor=rule.generic,
        status=status,
        observed=observed,
        detail=detail,
        probes=list(probes or []),
    )


# -- passive fingerprints (mDNS, SSDP, AMX DDP) ------------------------------

_PASSIVE_NOUN = {
    KIND_MDNS: "mDNS service",
    KIND_SSDP: "SSDP device type",
    KIND_AMX_DDP: "AMX DDP beacon",
}


def _declared_passive(rule: SignalRule) -> str:
    text = rule.source_id
    if rule.txt_match:
        text += " with " + ", ".join(f"{k}={v}" for k, v in rule.txt_match)
    return text


def _observed_passive(rule: SignalRule, ev: Evidence) -> str:
    """How one passive record reads, with the fields this rule filters on."""
    source_id = str(ev.data.get("source_id", ""))
    if not rule.txt_match:
        return source_id
    txt = ev.data.get("txt") if isinstance(ev.data.get("txt"), dict) else {}
    observed = {str(k).lower(): str(v) for k, v in txt.items()}
    parts = [
        f"{key}={observed[key]}" if key in observed else f"no {key}"
        for key, _ in rule.txt_match
    ]
    return f"{source_id} ({', '.join(parts)})"


def _check_passive(
    rule: SignalRule, single: SignalIndex, evidence: list[Evidence],
) -> SignalCheck:
    noun = _PASSIVE_NOUN[rule.kind]
    declared = _declared_passive(rule)
    same_kind = [ev for ev in evidence if ev.data.get("kind") == rule.kind]
    if not same_kind:
        return _check(
            rule, declared=declared, status=NOT_OBSERVED, observed=[],
            detail=f"No {noun} was heard from the device.",
        )
    hits = [ev for ev in same_kind if strong_rules(ev, single)]
    shown = list(dict.fromkeys(_observed_passive(rule, ev) for ev in (hits or same_kind)))
    if hits:
        return _check(
            rule, declared=declared, status=MATCHED, observed=shown,
            detail=f"The device announced {shown[0]}.",
        )
    return _check(
        rule, declared=declared, status=NOT_MATCHED, observed=shown,
        detail=f"The device announced {_join(shown)}, not {declared}.",
    )


# -- driver probes ------------------------------------------------------------

# A later reason means the exchange got further, so it says more.
_MISS_ORDER = (
    MISS_NOT_SENT, MISS_CONNECT, MISS_NO_REPLY, MISS_REPLY,
    MISS_CERT_SUBJECT, MISS_FOLLOW_UP,
)


def _quote(data: bytes) -> str:
    """Bytes as a reader sees them: quoted text when printable, else hex."""
    if not data:
        return "nothing"
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        text = None
    if text is not None and all(c.isprintable() or c in "\r\n\t" for c in text):
        shown = text.encode("unicode_escape").decode("ascii")
        if len(shown) > _QUOTE_LIMIT:
            shown = shown[:_QUOTE_LIMIT] + "..."
        return f'"{shown}"'
    shown = data.hex(" ")
    if len(shown) > _QUOTE_LIMIT:
        shown = shown[:_QUOTE_LIMIT] + "..."
    return f"hex {shown}"


def _expectation(spec: CustomProbeSpec) -> str:
    return describe_response_match(spec.response_match) or "any reply"


def _declared_probe(spec: CustomProbeSpec) -> str:
    proto = "TCP" if spec.kind == "tcp" else "UDP"
    parts = [f"{proto} port {spec.port}" + (" over TLS" if spec.tls else "")]
    if spec.send:
        parts.append(f"send {_quote(spec.send)}")
    if spec.cert_subject is not None:
        parts.append(f"certificate subject regex:{spec.cert_subject_source}")
    if describe_response_match(spec.response_match) or not spec.cert_subject:
        parts.append(f"expect {_expectation(spec)}")
    if spec.follow_up is not None:
        step = spec.follow_up
        expect = (
            "silence" if step.expect_silence
            else describe_response_match(step.response_match)
        )
        parts.append(f"then send {_quote(step.send)}, expect {expect}")
    return ", ".join(parts)


def _describe_exchange(spec: CustomProbeSpec, obs: ProbeObservation) -> str:
    """One sentence for one exchange, in the words of what happened."""
    proto = "TCP" if spec.kind == "tcp" else "UDP"
    where = f"{proto} port {spec.port}"
    sent = f"Sent {_quote(obs.sent)} to {where}" if obs.sent else f"Connected to {where}"
    if obs.matched:
        if obs.cert_subject and spec.cert_subject is not None and not obs.reply:
            return f"{sent}; the certificate subject was {obs.cert_subject!r}."
        return f"{sent}; the device replied {_quote(obs.reply)}."
    if obs.miss == MISS_NOT_SENT:
        return f"The probe could not be sent: {obs.error}."
    if obs.miss == MISS_CONNECT:
        return f"Could not connect to {where}: {obs.error}."
    if obs.miss == MISS_NO_REPLY:
        if spec.kind == "udp" or obs.sent:
            return f"{sent}; the device did not reply."
        return f"{sent}; the device sent nothing."
    if obs.miss == MISS_CERT_SUBJECT:
        subject = repr(obs.cert_subject) if obs.cert_subject else "not readable"
        return (
            f"Expected the certificate subject to match "
            f"regex:{spec.cert_subject_source}; it was {subject}."
        )
    if obs.miss == MISS_FOLLOW_UP and spec.follow_up is not None:
        step = spec.follow_up
        then = f"then sent {_quote(obs.follow_up_sent)}"
        if step.expect_silence:
            return (
                f"{sent} and the first reply matched; {then}, expected silence, "
                f"and the device replied {_quote(obs.follow_up_reply)}."
            )
        return (
            f"{sent} and the first reply matched; {then}, expected "
            f"{describe_response_match(step.response_match)}, and the device "
            f"replied {_quote(obs.follow_up_reply)}."
        )
    return (
        f"{sent}, expected {_expectation(spec)}; "
        f"the device replied {_quote(obs.reply)}."
    )


def _furthest(exchanges: list[ProbeObservation]) -> ProbeObservation:
    def rank(obs: ProbeObservation) -> int:
        return _MISS_ORDER.index(obs.miss) if obs.miss in _MISS_ORDER else -1
    return max(exchanges, key=rank)


def _check_probe(
    rule: SignalRule,
    spec: CustomProbeSpec,
    single: SignalIndex,
    evidence: list[Evidence],
    observed: DeviceObservations,
) -> SignalCheck:
    declared = _declared_probe(spec)
    exchanges = [o for o in observed.probes if o.probe_id == spec.probe_id]
    matched = [o for o in exchanges if o.matched]
    if matched:
        return _check(
            rule, declared=declared, status=MATCHED,
            observed=[_quote(o.reply) for o in matched],
            detail=_describe_exchange(spec, matched[0]), probes=exchanges,
        )
    # A scan's own match, when no exchange was kept.
    scan_hits = [ev for ev in evidence if strong_rules(ev, single)]
    if scan_hits:
        response = scan_hits[0].data.get("response")
        text = response.get("text") if isinstance(response, dict) else None
        shown = [repr(text)] if text else []
        return _check(
            rule, declared=declared, status=MATCHED, observed=shown,
            detail=f"The scan matched this probe on {rule.source_id}.",
        )
    if exchanges:
        replies = [_quote(o.reply) for o in exchanges if o.reply]
        return _check(
            rule, declared=declared, status=NOT_MATCHED, observed=replies,
            detail=_describe_exchange(spec, _furthest(exchanges)), probes=exchanges,
        )
    proto = "TCP" if spec.kind == "tcp" else "UDP"
    state = observed.port_states.get(spec.port) if spec.kind == "tcp" else None
    if state and state != PORT_OPEN:
        return _check(
            rule, declared=declared, status=NOT_MATCHED, observed=[f"port {spec.port} {state}"],
            detail=f"{proto} port {spec.port} was {state}, so the probe was not sent.",
        )
    return _check(
        rule, declared=declared, status=NOT_OBSERVED, observed=[],
        detail=f"The {proto} probe on port {spec.port} was not sent.",
    )


def _check_companion(hint: DiscoveryHint, evidence: list[Evidence]) -> SignalCheck:
    py = hint.python_probe
    assert py is not None
    ids = {py.broadcast_probe_id, py.active_probe_id}
    hits = [
        ev for ev in evidence
        if ev.data.get("kind") in (KIND_BROADCAST, KIND_ACTIVE_PROBE)
        and ev.data.get("source_id") in ids
    ]
    declared = f"Python companion {py.file_path}"
    if hits:
        return SignalCheck(
            kind="companion", declared=declared, strong=True, cross_vendor=py.cross_vendor,
            status=MATCHED, observed=list(dict.fromkeys(ev.source for ev in hits)),
            detail="The driver's Python companion identified the device.",
        )
    return SignalCheck(
        kind="companion", declared=declared, strong=True, cross_vendor=py.cross_vendor,
        status=NOT_OBSERVED, observed=[],
        detail="The driver's Python companion reported nothing about the device.",
    )


# -- hints (soft signals) -----------------------------------------------------

_SOFT_DECLARED = {
    KIND_OUI: "MAC address prefix {}",
    KIND_SNMP_PEN: "SNMP enterprise number {}",
    KIND_HOSTNAME: "host name matching regex:{}",
    KIND_VENDOR_STRING: 'manufacturer name "{}"',
}

_SOFT_UNSEEN = {
    KIND_OUI: "No MAC address was seen for the device.",
    KIND_SNMP_PEN: "The device gave no SNMP enterprise number.",
    KIND_HOSTNAME: "No host name was seen for the device.",
    KIND_VENDOR_STRING: "No probe reply or announcement named a manufacturer.",
}

_SOFT_NOUN = {
    KIND_OUI: "MAC address prefix",
    KIND_SNMP_PEN: "SNMP enterprise number",
    KIND_HOSTNAME: "host name",
    KIND_VENDOR_STRING: "manufacturer name",
}


def _observed_soft(ev: Evidence) -> str:
    kind = ev.data.get("kind")
    if kind == KIND_VENDOR_STRING:
        return str(ev.data.get("raw") or ev.data.get("value"))
    if kind == KIND_OUI:
        vendor = ev.data.get("vendor")
        value = str(ev.data.get("value"))
        return f"{value} ({vendor})" if vendor else value
    return str(ev.data.get("value"))


def _check_soft(
    rule: SignalRule, single: SignalIndex, evidence: list[Evidence],
) -> SignalCheck:
    declared = _SOFT_DECLARED[rule.kind].format(rule.source_id)
    same_kind = [ev for ev in evidence if ev.data.get("kind") == rule.kind]
    if not same_kind:
        return _check(
            rule, declared=declared, status=NOT_OBSERVED, observed=[],
            detail=_SOFT_UNSEEN[rule.kind],
        )
    hits = []
    for ev in same_kind:
        hit = soft_signal_hits(ev, single)
        if hit is not None and hit[1]:
            hits.append(ev)
    shown = list(dict.fromkeys(_observed_soft(ev) for ev in (hits or same_kind)))
    noun = _SOFT_NOUN[rule.kind]
    if hits:
        return _check(
            rule, declared=declared, status=MATCHED, observed=shown,
            detail=f"The device's {noun} is {shown[0]}.",
        )
    return _check(
        rule, declared=declared, status=NOT_MATCHED, observed=shown,
        detail=f"The device's {noun} is {_join(shown)}.",
    )


def _check_open_port(
    rule: SignalRule,
    single: SignalIndex,
    evidence: list[Evidence],
    observed: DeviceObservations,
) -> SignalCheck:
    port = int(rule.source_id)
    declared = f"TCP port {port} open"
    for ev in evidence:
        if ev.data.get("kind") != KIND_OPEN_PORT:
            continue
        hit = soft_signal_hits(ev, single)
        if hit is not None and hit[1]:
            return _check(
                rule, declared=declared, status=MATCHED, observed=[f"port {port} open"],
                detail=f"TCP port {port} is open.",
            )
    state = observed.port_states.get(port)
    if state:
        return _check(
            rule, declared=declared, status=NOT_MATCHED, observed=[f"port {port} {state}"],
            detail=f"TCP port {port} was {state}.",
        )
    return _check(
        rule, declared=declared, status=NOT_OBSERVED, observed=[],
        detail=f"TCP port {port} was not scanned.",
    )


def _join(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]
