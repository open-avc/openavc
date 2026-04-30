"""Driver matcher — matches discovered devices to installed drivers."""

from __future__ import annotations

import logging
from typing import Any

from server.discovery.hints import DriverHint
from server.discovery.result import DiscoveredDevice, DriverMatch

log = logging.getLogger("discovery.matcher")


class DriverMatcher:
    """Match discovered devices to installed drivers."""

    def __init__(self, driver_hints: list[DriverHint]) -> None:
        self.hints = driver_hints

    def match_device(self, device: DiscoveredDevice) -> list[DriverMatch]:
        """Find matching drivers for a discovered device.

        Matching criteria (each adds to match confidence):
          1. Protocol match (probe identified the exact protocol)
          2. Manufacturer match (OUI, SNMP, probe, mDNS)
          3. Category match (OUI, SSDP)
          4. Port match (device has open port that matches driver default)
          5. Transport match (TCP/UDP/HTTP inferred from ports)
          6. MAC OUI match (explicit hint)
          7. Hostname match (explicit hint regex)

        Returns list of DriverMatch sorted by confidence (best first).
        """
        matches: list[DriverMatch] = []

        for hint in self.hints:
            score = 0.0
            reasons: list[str] = []

            # --- Protocol match (highest signal) ---
            protocol_match = self._check_protocol(device, hint)
            if protocol_match:
                score += 0.40
                reasons.append(protocol_match)

            # --- Manufacturer match ---
            mfg_match = self._check_manufacturer(device, hint)
            if mfg_match:
                score += 0.25
                reasons.append(mfg_match)

            # --- Category match ---
            cat_match = self._check_category(device, hint)
            if cat_match:
                score += 0.10
                reasons.append(cat_match)

            # --- Port match ---
            port_match = self._check_port(device, hint)
            if port_match:
                score += 0.15
                reasons.append(port_match)

            # --- MAC OUI match (explicit hint) ---
            mac_match = self._check_mac_prefix(device, hint)
            if mac_match:
                score += 0.10
                reasons.append(mac_match)

            # --- Hostname match (explicit hint) ---
            hostname_match = self._check_hostname(device, hint)
            if hostname_match:
                score += 0.05
                reasons.append(hostname_match)

            # Only include if we have some signal
            if score < 0.20:
                continue

            # Cap at 1.0
            score = min(score, 1.0)

            # Build suggested config
            suggested_config = self._build_suggested_config(device, hint)

            matches.append(DriverMatch(
                driver_id=hint.driver_id,
                driver_name=hint.driver_name,
                confidence=round(score, 2),
                match_reasons=reasons,
                suggested_config=suggested_config,
            ))

        # Sort by confidence descending
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches

    def _check_protocol(self, device: DiscoveredDevice, hint: DriverHint) -> str | None:
        """Check if device protocols match the driver."""
        if not device.protocols:
            return None

        # Check driver-declared protocols first (from DRIVER_INFO or .avcdriver)
        if hint.protocols:
            for protocol in device.protocols:
                if protocol in hint.protocols:
                    return f"Protocol {protocol} matches driver"

        # Fallback: hardcoded map for drivers that don't declare protocols
        _PROTOCOL_DRIVER_MAP = {
            "pjlink": ["pjlink", "pjlink_class1", "pjlink_class2"],
            "extron_sis": ["extron_sis"],
            "biamp_tesira": ["biamp_tesira_ttp", "biamp_tesira"],
            "qsc_qrc": ["qsc_qrc", "qsc_qsys"],
            "kramer_p3000": ["kramer_p3000"],
            "shure_dcs": ["shure_network", "shure_dcs"],
            "samsung_mdc": ["samsung_mdc"],
            "visca": ["visca", "visca_ip"],
            "crestron_cip": ["crestron_cip"],
            "crestron_http": ["crestron_http"],
            "panasonic_ptz": ["panasonic_ptz", "panasonic_aw"],
            "panasonic_http": ["panasonic_ptz", "panasonic_http"],
            "lg_http": ["lg_sicp"],
        }

        for protocol in device.protocols:
            driver_ids = _PROTOCOL_DRIVER_MAP.get(protocol, [])
            if hint.driver_id in driver_ids:
                return f"Protocol {protocol} matches driver"

            # Fallback: check if protocol name is in the driver ID
            if protocol.replace("_", "") in hint.driver_id.replace("_", ""):
                return f"Protocol {protocol} matches driver ID"

        return None

    def _check_manufacturer(self, device: DiscoveredDevice, hint: DriverHint) -> str | None:
        """Check if device manufacturer matches driver manufacturer."""
        if not device.manufacturer or not hint.manufacturer:
            return None

        dev_mfg = device.manufacturer.lower()
        drv_mfg = hint.manufacturer.lower()

        if dev_mfg == drv_mfg:
            return f"Manufacturer matches ({device.manufacturer})"

        # Fuzzy: one contains the other
        if dev_mfg in drv_mfg or drv_mfg in dev_mfg:
            return f"Manufacturer matches ({device.manufacturer} ~ {hint.manufacturer})"

        return None

    def _check_category(self, device: DiscoveredDevice, hint: DriverHint) -> str | None:
        """Check if device category matches driver category."""
        if not device.category or not hint.category:
            return None

        if device.category.lower() == hint.category.lower():
            return f"Category matches ({device.category})"

        return None

    def _check_port(self, device: DiscoveredDevice, hint: DriverHint) -> str | None:
        """Check if device has an open port matching the driver's default."""
        if not device.open_ports or not hint.ports:
            return None

        for port in hint.ports:
            if port in device.open_ports:
                return f"Port {port} open (driver default)"

        return None

    def _check_mac_prefix(self, device: DiscoveredDevice, hint: DriverHint) -> str | None:
        """Check MAC OUI prefix against explicit driver hints."""
        if not device.mac or not hint.mac_prefixes:
            return None

        # Normalize to colon-separated lowercase (handles both "00:05:a6" and "00-05-a6")
        mac_prefix = device.mac.replace("-", ":")[:8].lower()
        for prefix in hint.mac_prefixes:
            if mac_prefix == prefix.replace("-", ":").lower():
                return f"MAC prefix {mac_prefix} matches hint"

        return None

    def _check_hostname(self, device: DiscoveredDevice, hint: DriverHint) -> str | None:
        """Check hostname against driver hint patterns."""
        if not device.hostname or not hint.hostname_patterns:
            return None

        for pattern in hint.hostname_patterns:
            if pattern.search(device.hostname):
                return "Hostname matches pattern"

        return None

    def _build_suggested_config(
        self, device: DiscoveredDevice, hint: DriverHint
    ) -> dict[str, Any]:
        """Build a pre-filled device config for 'Add to Project'."""
        config: dict[str, Any] = {
            "host": device.ip,
        }

        # Use the driver's default port, or the first matching open port
        if hint.default_port:
            config["port"] = hint.default_port
        elif hint.ports:
            for p in hint.ports:
                if p in device.open_ports:
                    config["port"] = p
                    break

        return config


class CommunityDriverMatcher:
    """Match discovered devices to community drivers from index.json.

    Community matches use the same scoring signals as installed driver matching
    but apply a 0.7 penalty multiplier so they never outrank installed drivers.

    When `devices_lookup` is provided (the reverse-indexed devices.json catalog),
    a discovered device with a known (manufacturer, model) gets an authoritative
    suggestion at high confidence. Heuristic scoring still runs alongside.
    """

    PENALTY = 0.7  # Community matches are penalized vs installed

    # Confidence-to-score for exact device-catalog hits (before PENALTY).
    _EXACT_CONFIDENCE_SCORES = {
        "full": 0.95,
        "partial": 0.75,
        "untested": 0.55,
    }

    def __init__(
        self,
        community_drivers: list[dict[str, Any]],
        installed_ids: set[str],
        devices_lookup: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        # Filter out already-installed and utility drivers
        self.drivers = [
            d for d in community_drivers
            if d.get("id") not in installed_ids
            and d.get("category") != "utility"
        ]
        self._driver_by_id = {d["id"]: d for d in self.drivers if d.get("id")}
        self.devices_lookup = devices_lookup or {}

    def match_device(self, device: DiscoveredDevice) -> list[DriverMatch]:
        """Find matching community drivers for a discovered device."""
        # Step 1: exact (manufacturer, model) hits from the device catalog.
        exact_matches = self._exact_device_matches(device)
        exact_ids = {m.driver_id for m in exact_matches}

        # Step 2: heuristic scoring across remaining drivers (existing logic).
        matches: list[DriverMatch] = list(exact_matches)
        device_protocols = {p.lower() for p in device.protocols}
        device_ports = set(device.open_ports)
        dev_mfg = (device.manufacturer or "").lower()
        dev_cat = (device.category or "").lower()

        for drv in self.drivers:
            drv_id = drv.get("id", "")
            if drv_id in exact_ids:
                continue  # Already added via exact device-catalog hit
            score = 0.0
            reasons: list[str] = []

            drv_mfg = drv.get("manufacturer", "").lower()
            drv_cat = drv.get("category", "").lower()
            drv_ports = drv.get("ports", [])
            drv_protocols = [p.lower() for p in drv.get("protocols", [])]

            # --- Protocol match (+0.40) ---
            # Uses the protocols field from index.json — no hardcoded map
            protocol_matched = False
            for proto in device_protocols:
                if proto in drv_protocols:
                    protocol_matched = True
                    reasons.append(f"Protocol {proto} confirmed")
                    break

            if protocol_matched:
                score += 0.40

            # --- Manufacturer match (+0.25) ---
            if dev_mfg and drv_mfg and drv_mfg != "generic":
                if dev_mfg == drv_mfg or dev_mfg in drv_mfg or drv_mfg in dev_mfg:
                    score += 0.25
                    reasons.append(f"Manufacturer matches ({drv.get('manufacturer', '')})")

            # --- Category match (+0.10) ---
            if dev_cat and drv_cat and dev_cat == drv_cat:
                score += 0.10
                reasons.append(f"Category matches ({drv_cat})")

            # --- Port match (+0.15) ---
            for p in drv_ports:
                if p in device_ports:
                    score += 0.15
                    reasons.append(f"Port {p} open")
                    break

            # Minimum threshold before penalty — a single signal (e.g.
            # a specific AV port match at 0.15) should be enough to suggest
            if score < 0.15:
                continue

            # Apply community penalty
            score = round(min(score * self.PENALTY, 1.0), 2)

            # Build suggested config
            config: dict[str, Any] = {"host": device.ip}
            # Use matching open port, or first driver port
            for p in drv_ports:
                if p in device_ports:
                    config["port"] = p
                    break
            else:
                if drv_ports:
                    config["port"] = drv_ports[0]

            matches.append(DriverMatch(
                driver_id=drv_id,
                driver_name=drv.get("name", drv_id),
                confidence=score,
                match_reasons=reasons,
                suggested_config=config,
                source="community",
                description=drv.get("description", ""),
            ))

        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches

    def _exact_device_matches(self, device: DiscoveredDevice) -> list[DriverMatch]:
        """Resolve (device.manufacturer, device.model) against the devices catalog.

        When a discovered device's manufacturer + model are both known, the
        catalog provides an authoritative driver mapping with a confidence tag
        that's been curated per driver. The PENALTY still applies so installed
        drivers that responded to a probe outrank these.
        """
        if not self.devices_lookup or not device.manufacturer or not device.model:
            return []
        key = (device.manufacturer.lower(), device.model.lower())
        drv_refs = self.devices_lookup.get(key, [])
        if not drv_refs:
            return []

        results: list[DriverMatch] = []
        for ref in drv_refs:
            ref_id = ref.get("id")
            drv = self._driver_by_id.get(ref_id)
            if drv is None:
                continue  # Driver in catalog but filtered out (installed/utility)
            confidence = ref.get("confidence", "untested")
            base_score = self._EXACT_CONFIDENCE_SCORES.get(confidence, 0.55)
            score = round(base_score * self.PENALTY, 2)

            reasons = [f"Exact match: {device.manufacturer} {device.model} ({confidence})"]
            if ref.get("notes"):
                reasons.append(ref["notes"])

            config: dict[str, Any] = {"host": device.ip}
            drv_ports = drv.get("ports", [])
            for p in drv_ports:
                if p in device.open_ports:
                    config["port"] = p
                    break
            else:
                if drv_ports:
                    config["port"] = drv_ports[0]

            results.append(DriverMatch(
                driver_id=ref_id,
                driver_name=drv.get("name", ref_id),
                confidence=score,
                match_reasons=reasons,
                suggested_config=config,
                source="community",
                description=drv.get("description", ""),
            ))
        return results
