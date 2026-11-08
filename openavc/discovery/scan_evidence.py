"""The evidence records a scan builds from what it heard about a device.

A Discovery scan and a device audit's network check (``audit/footprint.py``)
both build their records here, so the two describe the same device the same
way: a host name, a MAC address, the open ports and manufacturer strings a
scan adds in its last phase, and a port's greeting keyed as the matcher reads
it. Nothing here touches the network; each function turns an observation into
``Evidence`` the matcher (``tier_matcher.py``) judges.
"""

from __future__ import annotations

from typing import Any, Iterable

from openavc.discovery.oui_database import OUIDatabase
from openavc.discovery.tier_matcher import (
    KIND_HOSTNAME,
    SignalIndex,
    evidence_hostname,
    evidence_open_port,
    evidence_oui,
    extract_vendor_strings,
    vendor_strings_in_text,
)


def hostname_evidence(hostname: str, index: SignalIndex) -> list:
    """The evidence a scan records for a host name.

    One record per driver pattern the name matches, so the "Why?" reveal can
    show the pattern that fired; a single bare record when none does, as the
    audit trail.
    """
    patterns = index.matched_hostname_patterns(hostname)
    if patterns:
        return [evidence_hostname(hostname, matched_pattern=pat) for pat in patterns]
    return [evidence_hostname(hostname)]


def mdns_hostname_evidence(result: Any, index: SignalIndex, evidence: list) -> list:
    """The host-name evidence for the name a device announces over mDNS.

    The bare name (``controller``, never ``controller.local``), as the
    device card shows it; nothing when the device announced no name or a
    record for the same name is already in ``evidence`` (reverse DNS or
    NetBIOS gave it first). Many AV devices publish no reverse-DNS or
    NetBIOS name, so without this a ``hostname:`` hint written for the
    name the device actually announces never fires.
    """
    name = result.to_device_info().get("hostname")
    if not name:
        return []
    seen = {
        str(ev.data.get("value") or "").lower()
        for ev in evidence if ev.data.get("kind") == KIND_HOSTNAME
    }
    if name.lower() in seen:
        return []
    return hostname_evidence(name, index)


def mac_info_and_evidence(
    mac: str, oui_db: OUIDatabase,
) -> tuple[dict[str, Any], Any]:
    """What a MAC address tells a scan: device info and the OUI evidence.

    The info carries ``mac`` and, when the OUI table knows the prefix, the
    ``manufacturer`` and ``category`` it names (the network card's maker,
    which is often not the device's).
    """
    info: dict[str, Any] = {"mac": mac}
    vendor = None
    block = None
    found = oui_db.lookup_block(mac)
    if found:
        block, vendor, category = found
        info["manufacturer"] = vendor
        info["category"] = category
    return info, evidence_oui(mac, vendor=vendor, block=block)


def derived_evidence(
    evidence_log: list,
    open_ports: list[int],
    index: SignalIndex,
    texts: dict[str, str] | None = None,
) -> list:
    """The records a scan adds to a device's evidence in its last phase.

    An open-port record for each open port some driver's ``port_open:`` hint
    names (bare openness on a generic port is too weak to record for every
    port), the manufacturer strings lifted from probe replies, and the
    manufacturer aliases the device names in its own words: ``texts`` (what a
    port sent unprompted, a web server's ``Server`` header, keyed as
    ``vendor_strings_in_text`` reads them) and an SSDP ``SERVER``. A string
    already recorded is not recorded again.
    """
    records = [
        evidence_open_port(port) for port in open_ports
        if index.find_soft_open_port(port)
    ]
    records.extend(extract_vendor_strings(evidence_log + records))
    named = vendor_strings_in_text(_with_ssdp_server(texts, evidence_log), index)
    records.extend(_not_yet_recorded(named, evidence_log + records))
    return records


def _with_ssdp_server(texts: dict[str, str] | None, evidence_log: list) -> dict[str, str]:
    """``texts`` plus the first SSDP ``SERVER`` the evidence carries, as
    ``ssdp_server``, unless ``texts`` already has one. A copy: the caller's
    dict is left as it was."""
    all_texts = dict(texts or {})
    servers = [ev.data.get("server") for ev in evidence_log if ev.data.get("kind") == "ssdp"]
    first = next((s for s in servers if isinstance(s, str) and s), None)
    if first:
        all_texts.setdefault("ssdp_server", first)
    return all_texts


def _not_yet_recorded(candidates: Iterable, recorded: list) -> list:
    """The manufacturer-string records among ``candidates`` whose string is in
    no ``vendor_string`` record of ``recorded``, each string once, in order."""
    have = {
        ev.data.get("value") for ev in recorded
        if ev.data.get("kind") == "vendor_string"
    }
    fresh = []
    for ev in candidates:
        if ev.data["value"] not in have:
            have.add(ev.data["value"])
            fresh.append(ev)
    return fresh


def banner_texts(banners: dict[int, str]) -> dict[str, str]:
    """A device's port greetings keyed as ``vendor_strings_in_text`` reads them."""
    return {f"greeting:{port}": text for port, text in sorted(banners.items()) if text}
