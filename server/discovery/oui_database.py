"""MAC OUI lookup for AV device manufacturer identification."""

from __future__ import annotations

from server.discovery.oui_data import AV_OUI_TABLE, NON_AV_CATEGORIES


class OUIDatabase:
    """Lookup MAC address manufacturer from OUI prefix."""

    def __init__(self) -> None:
        # Start with built-in table, can be extended by driver hints
        self._table = dict(AV_OUI_TABLE)

    def lookup(self, mac: str) -> tuple[str, str] | None:
        """Lookup manufacturer and category from a MAC address.

        Args:
            mac: MAC address in any common format
                 (00:11:22:33:44:55, 00-11-22-33-44-55, 001122334455)

        Returns:
            (manufacturer_name, category_hint) or None if not in AV database.
        """
        normalized = self._normalize_mac(mac)
        if not normalized:
            return None
        prefix = normalized[:8]  # "00:11:22"
        return self._table.get(prefix)

    def is_av_manufacturer(self, mac: str) -> bool:
        """Check if a MAC belongs to a known AV equipment manufacturer."""
        result = self.lookup(mac)
        if result is None:
            return False
        _, category = result
        return category not in NON_AV_CATEGORIES

    def add_prefix(self, prefix: str, manufacturer: str, category: str) -> None:
        """Add a MAC OUI prefix to the lookup table.

        Only adds if the prefix is not already present — built-in entries
        are not overwritten by driver hints.
        """
        normalized = prefix.strip().lower().replace("-", ":")
        if len(normalized) == 8 and normalized not in self._table:
            self._table[normalized] = (manufacturer, category)

    def is_network_device(self, mac: str) -> bool:
        """Check if a MAC belongs to a known network infrastructure manufacturer."""
        result = self.lookup(mac)
        if result is None:
            return False
        _, category = result
        return category == "network"

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
