"""MAC OUI lookup for device manufacturer identification.

Core ships an empty table; entries come from each loaded driver's
``discovery.oui:`` hint at startup (and the community catalog refresh).
The category string attached to each OUI prefix comes from the same
driver's ``category`` field in its registry entry — drivers
self-describe as ``audio``, ``display``, ``projector``, etc., and the
discovery scanner reuses that label as a UI hint when the OUI matches
but no fingerprint identifies the device.
"""

from __future__ import annotations

import re

from server.discovery.oui_data import AV_OUI_TABLE
from server.utils.logger import get_logger

log = get_logger(__name__)

_NON_HEX = re.compile(r"[^0-9a-f]")


def normalize_oui_prefix(value: str) -> str | None:
    """Canonicalize an OUI hint or MAC to lowercase ``xx:xx:xx`` (3 octets).

    Accepts any common style — colon, dash, dot, or no separators — and
    either a bare 3-octet OUI (``001122``, ``00-11-22``, ``0011.22``) or a
    full MAC (``00:11:22:33:44:55``, ``001122334455``, ``0011.2233.4455``);
    only the first three octets are kept. Returns ``None`` when fewer than
    three octets of hex are present, so callers can warn instead of silently
    registering a key that can never match an observed MAC.

    Shared by the OUI table (``add_prefix``) and the tier matcher's OUI rules
    so registration and lookup always agree on the key regardless of the
    separator style the hint or the observed MAC happens to use.
    """
    hex_digits = _NON_HEX.sub("", value.strip().lower())
    if len(hex_digits) < 6:
        return None
    return ":".join(hex_digits[i : i + 2] for i in range(0, 6, 2))


class OUIDatabase:
    """Lookup MAC address manufacturer from OUI prefix."""

    def __init__(self) -> None:
        # Start with whatever ships in oui_data (empty by default), then
        # extend at runtime via add_prefix() as drivers register hints.
        self._table = dict(AV_OUI_TABLE)

    def lookup(self, mac: str) -> tuple[str, str] | None:
        """Lookup manufacturer and category from a MAC address.

        Args:
            mac: MAC address in any common format
                 (00:11:22:33:44:55, 00-11-22-33-44-55, 001122334455)

        Returns:
            (manufacturer_name, category_hint) or None if no driver hint
            registered the OUI prefix.
        """
        normalized = self._normalize_mac(mac)
        if not normalized:
            return None
        prefix = normalized[:8]  # "00:11:22"
        return self._table.get(prefix)

    def add_prefix(self, prefix: str, manufacturer: str, category: str) -> None:
        """Add a MAC OUI prefix to the lookup table.

        The prefix is canonicalized (any separator style, bare 3-octet OUI or
        full MAC) so a hint written as ``001122`` or ``0011.22`` registers the
        same key an observed MAC resolves to. Only adds if the prefix is not
        already present — earlier registrations win, so an installed driver's
        hint isn't overwritten by a colliding catalog entry. A prefix with no
        usable OUI is logged and skipped rather than dropped silently.
        """
        normalized = normalize_oui_prefix(prefix)
        if normalized is None:
            log.warning(
                "Ignoring unparseable OUI prefix %r (%s)", prefix, manufacturer
            )
            return
        if normalized not in self._table:
            self._table[normalized] = (manufacturer, category)

    @staticmethod
    def _normalize_mac(mac: str) -> str | None:
        """Normalize MAC to lowercase colon-separated format."""
        mac = mac.strip().lower()
        # Remove common separators
        clean = mac.replace("-", "").replace(":", "").replace(".", "")
        if len(clean) != 12:
            return None
        # Re-insert colons
        return ":".join(clean[i : i + 2] for i in range(0, 12, 2))
