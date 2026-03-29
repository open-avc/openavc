"""Discovery hint parser — extracts hints from registered drivers."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("discovery.hints")


@dataclass
class DriverHint:
    """Parsed discovery hints from a single driver."""

    driver_id: str
    driver_name: str
    manufacturer: str
    category: str
    transport: str
    # From explicit discovery hints (optional)
    ports: list[int] = field(default_factory=list)
    mac_prefixes: list[str] = field(default_factory=list)
    mdns_services: list[str] = field(default_factory=list)
    upnp_types: list[str] = field(default_factory=list)
    snmp_pattern: str | None = None
    hostname_patterns: list[re.Pattern] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    # Inferred from driver config
    default_port: int | None = None


def load_driver_hints(registry: list[dict[str, Any]]) -> list[DriverHint]:
    """Extract discovery hints from all registered drivers.

    Works for both YAML-based (ConfigurableDriver) and Python drivers.
    Drivers without explicit discovery hints still get a DriverHint with
    inferred data (manufacturer, category, default port from config_schema).

    Args:
        registry: Output of get_driver_registry() — list of driver info dicts.
    """
    hints: list[DriverHint] = []

    for driver_info in registry:
        driver_id = driver_info.get("id", "")
        if not driver_id:
            continue

        # Skip generic drivers — they're templates, not real devices
        if driver_id.startswith("generic_"):
            continue

        hint = DriverHint(
            driver_id=driver_id,
            driver_name=driver_info.get("name", driver_id),
            manufacturer=driver_info.get("manufacturer", ""),
            category=driver_info.get("category", ""),
            transport=driver_info.get("transport", "tcp"),
        )

        # Load protocol declarations from driver metadata
        protocols = driver_info.get("protocols", [])
        if isinstance(protocols, str):
            protocols = [protocols]
        hint.protocols = [p.lower() for p in protocols if isinstance(p, str)]

        # Infer default port from config_schema or default_config
        default_config = driver_info.get("default_config", {})
        config_schema = driver_info.get("config_schema", {})

        port = default_config.get("port")
        if port is None and "port" in config_schema:
            port = config_schema["port"].get("default")
        if isinstance(port, (int, float)):
            hint.default_port = int(port)
            hint.ports = [int(port)]

        # Load explicit discovery hints if present
        # (These are added to .avcdriver files in the `discovery:` section)
        discovery = driver_info.get("discovery", {})
        if discovery:
            if "ports" in discovery:
                hint.ports = [int(p) for p in discovery["ports"]]
            if "mac_prefixes" in discovery:
                hint.mac_prefixes = [
                    p.lower().replace("-", ":") for p in discovery["mac_prefixes"]
                ]
            if "mdns_services" in discovery:
                hint.mdns_services = discovery["mdns_services"]
            if "upnp_types" in discovery:
                hint.upnp_types = discovery["upnp_types"]
            if "snmp_pattern" in discovery:
                hint.snmp_pattern = discovery["snmp_pattern"]
            if "hostname_patterns" in discovery:
                hint.hostname_patterns = [
                    re.compile(p, re.IGNORECASE)
                    for p in discovery["hostname_patterns"]
                ]
            if "default_port" in discovery:
                hint.default_port = int(discovery["default_port"])

        hints.append(hint)
        log.debug(
            "Loaded hints for %s: mfg=%s cat=%s port=%s",
            driver_id, hint.manufacturer, hint.category, hint.default_port,
        )

    log.info("Loaded discovery hints for %d drivers", len(hints))
    return hints
