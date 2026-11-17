"""Driver discovery declarations, as the discovery engine uses them.

Each driver's ``discovery:`` block (YAML drivers) or
``DRIVER_INFO['discovery']`` (Python drivers) is read by
``openavc.drivers.discovery_block`` (THE reader, shared with every door that
judges a driver file and vendored into the community catalog) into a
``DiscoveryHint`` that the engine and matcher consume. This module is the
runtime's side of it: the platform release a ``requires:`` gate is judged
against, the log lines, and the signal index built from the hints.

Two kinds of declarations:

- **Fingerprints** identify the driver alone. mDNS service, SSDP
  device type, AMX DDP beacon, TCP/UDP probe response, or a Python
  escape-hatch (``python: ./foo_discovery.py``). One fingerprint match
  is enough to identify the device.
- **Hints** narrow candidates. OUI, hostname pattern, observed open
  port, manufacturer alias, SNMP enterprise number. Multiple hints
  combined produce a ``possible`` candidate list.

Each fingerprint may carry a ``cross_vendor: true`` flag. When such a
fingerprint matches, the matcher consults peer drivers' hints; if a
vendor-specific peer matches, it wins and the cross-vendor driver
demotes to alternative.

"""

from __future__ import annotations

import logging
from typing import Any

from openavc.discovery.tier_matcher import (
    SignalIndex,
    SignalRule,
)
from openavc.drivers.discovery_block import (  # noqa: F401  (the names discovery code imports from here)
    DISALLOWED_OPEN_PORTS,
    MAX_PROBE_TIMEOUT_MS,
    read_discovery_block,
)
from openavc.drivers.discovery_hint import (  # noqa: F401
    AmxDdpFingerprint,
    CustomProbeSpec,
    DiscoveryHint,
    DiscoveryHintError,
    ExtractRule,
    MdnsFingerprint,
    ProbeFollowUp,
    PythonProbe,
    ResponseMatch,
    SsdpFingerprint,
)

log = logging.getLogger("discovery.hints")


def describe_response_match(match: ResponseMatch) -> str:
    """Return a short description of the matcher: ``regex:<pattern>``,
    ``contains:<text>``, or ``hex aa ff`` for the bytes a reply starts with.

    Used by the probe runner to bake the matched pattern into evidence
    records so the scan-results "Why?" reveal can render lines like
    "UDP probe on port 6454 matched regex:<vendor-pattern>". Format is
    stable enough for the React UI to split on the first colon and
    render "kind = value" with appropriate styling.

    Empty string when the matcher has no declared sub-matcher (a
    connect-only TCP probe — banner-grab style).
    """
    parts: list[str] = []
    if match.starts_with is not None:
        # Bytes as every sentence writes them: "hex aa ff".
        parts.append(f"hex {match.starts_with.hex(' ')}")
    if match.regex is not None and match.regex_source:
        parts.append(f"regex:{match.regex_source}")
    if match.contains is not None:
        parts.append(f"contains:{match.contains}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------


def _platform_version() -> str:
    """The running platform version (a function so tests can patch it)."""
    from openavc.version import __version__
    return __version__


def parse_driver_discovery(driver_info: dict[str, Any]) -> DiscoveryHint | None:
    """Parse one driver-registry entry into a ``DiscoveryHint``.

    Returns ``None`` for template drivers (``generic_*``) and for blocks
    whose ``requires:`` names a newer platform than this one (or no version
    at all); otherwise a populated ``DiscoveryHint``. Raises
    ``DiscoveryHintError`` with the first structural problem
    (``read_discovery_block`` finds them all; the walk reports every one).

    ``requires: "<version>"`` is the discovery schema's compatibility
    gate. The community catalog build stamps it onto any driver whose
    block uses features an older parser would silently mis-read (rather
    than reject) — e.g. SSDP description filters, which pre-0.23.0
    parsers ignore, collapsing distinct filtered claims into colliding
    unfiltered ones. A parser that predates the gate itself rejects the
    unknown ``requires`` key, so either way the driver's hints skip
    cleanly on old platforms and the rest of the catalog stays live.

    A driver that *declares* a ``discovery:`` block with no fingerprints
    and no hints still parses — it simply never participates in matching,
    which is logged as a warning, as is an OUI entry that names no prefix.
    """
    driver_id = str(driver_info.get("id") or "").strip()
    if not driver_id:
        raise DiscoveryHintError("Driver missing required 'id' field")
    read = read_discovery_block(driver_info, platform_version=_platform_version())
    if read.errors:
        raise DiscoveryHintError(f"{driver_id}: {read.errors[0]}")
    if read.skipped:
        log.info(
            "%s: discovery block requires platform %s (running %s); "
            "skipping its hints",
            driver_id, str(driver_info["discovery"]["requires"]).strip(), _platform_version(),
        )
        return None
    for warning in read.warnings:
        log.warning("%s: %s", driver_id, warning)
    return read.hint


def load_discovery_hints(registry: list[dict[str, Any]]) -> list[DiscoveryHint]:
    """Parse the ``discovery:`` block from every registered driver.

    Drivers with malformed blocks are logged and skipped — the loader
    does not raise. Strict validation happens earlier, at the doors that
    judge a driver file (``drivers/discovery_block.py``, asked by the
    definition walk and by the community catalog's build).
    """
    hints: list[DiscoveryHint] = []
    for driver_info in registry:
        try:
            hint = parse_driver_discovery(driver_info)
        except DiscoveryHintError as exc:
            log.warning("Skipping driver discovery hints: %s", exc)
            continue
        if hint is None:
            continue
        hints.append(hint)

    log.info("Loaded discovery hints for %d drivers", len(hints))
    return hints


# ---------------------------------------------------------------------------
# Signal-index builder
# ---------------------------------------------------------------------------


def signal_rules(hint: DiscoveryHint) -> list[SignalRule]:
    """Every ``SignalRule`` one driver's ``discovery:`` block declares.

    Fingerprints first (mDNS, SSDP, AMX DDP, the TCP probe, the UDP probe,
    the Python companion's two IDs), then the hints (SNMP PEN, OUI, host
    name, open port, manufacturer alias), in declaration order within each.
    """
    rules: list[SignalRule] = []
    for fp in hint.mdns:
        rules.append(SignalRule.for_mdns(
            hint.driver_id,
            fp.service,
            txt_match={k: v for k, v in fp.txt} or None,
            generic=fp.cross_vendor,
        ))
    for fp in hint.ssdp:
        rules.append(SignalRule.for_ssdp(
            hint.driver_id, fp.device_type,
            txt_match={k: v for k, v in fp.fields} or None,
            generic=fp.cross_vendor,
        ))
    for fp in hint.amx_ddp:
        rules.append(SignalRule.for_amx_ddp(
            hint.driver_id, fp.make, fp.model_pattern,
            generic=fp.cross_vendor,
        ))

    if hint.tcp_probe is not None:
        spec = hint.tcp_probe
        rules.append(SignalRule.for_active_probe(
            hint.driver_id, spec.probe_id, generic=spec.cross_vendor,
        ))
    if hint.udp_probe is not None:
        spec = hint.udp_probe
        rules.append(SignalRule.for_broadcast(
            hint.driver_id, spec.probe_id, generic=spec.cross_vendor,
        ))
    if hint.python_probe is not None:
        py = hint.python_probe
        rules.append(SignalRule.for_broadcast(
            hint.driver_id, py.broadcast_probe_id, generic=py.cross_vendor,
        ))
        rules.append(SignalRule.for_active_probe(
            hint.driver_id, py.active_probe_id, generic=py.cross_vendor,
        ))

    if hint.snmp_pen is not None:
        rules.append(SignalRule.for_snmp_pen(hint.driver_id, hint.snmp_pen))
    for prefix in hint.oui:
        rules.append(SignalRule.for_oui(hint.driver_id, prefix))
    for pattern in hint.hostname:
        rules.append(SignalRule.for_hostname(hint.driver_id, pattern))
    for port in hint.port_open:
        rules.append(SignalRule.for_open_port(hint.driver_id, port))
    for alias in hint.manufacturer_alias:
        rules.append(SignalRule.for_vendor_string(hint.driver_id, alias))
    return rules


def build_signal_index(hints: list[DiscoveryHint]) -> SignalIndex:
    """Register every fingerprint and hint into a ``SignalIndex``.

    A rule that collides with an already-registered one (see
    ``SignalIndex.add_rule``) is dropped and logged; the rest of the
    index survives. Earlier hints win, so with the engine's
    installed-then-catalog ordering an installed driver's claim beats
    a catalog entry's. Collisions are authoring errors that CI in the
    drivers repo rejects — but a runtime index build must degrade by
    one rule, not abort, or a single inconsistent catalog entry would
    knock every catalog driver out of scans at once.
    """
    index = SignalIndex()
    dropped = 0

    for hint in hints:
        for rule in signal_rules(hint):
            try:
                index.add_rule(rule)
            except ValueError as exc:
                dropped += 1
                log.error("Dropping colliding discovery rule: %s", exc)

    if dropped:
        log.error(
            "Built signal index covering %d driver(s); dropped %d colliding rule(s)",
            index.driver_count(), dropped,
        )
    else:
        log.info(
            "Built signal index covering %d driver(s)",
            index.driver_count(),
        )
    return index
