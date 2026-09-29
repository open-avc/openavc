"""MAC OUI lookup for device manufacturer identification.

Core ships an empty table; entries come from each loaded driver's
``discovery.oui:`` hint at startup (and the community catalog refresh).
The category string attached to each OUI prefix comes from the same
driver's ``category`` field in its registry entry — drivers
self-describe as ``audio``, ``display``, ``projector``, etc., and the
discovery scanner reuses that label as a UI hint when the OUI matches
but no fingerprint identifies the device.

A prefix is the classic three-octet OUI (an IEEE MA-L block) or, for a
maker whose only IEEE assignment is a medium or small block, that block's
28 or 36 bits (MA-M ``18:66:96:1``, MA-S ``70:b3:d5:12:3``): the IEEE
Registration Authority splits such a /24 among many companies, so the
three octets alone would claim all of them. A MAC falls in the longest
registered prefix that matches it, here and in the matcher
(``tier_matcher.SignalIndex.find_soft_oui_block``).
"""

from __future__ import annotations

import re

from openavc.discovery.oui_data import AV_OUI_TABLE
from openavc.utils.logger import get_logger

log = get_logger(__name__)

_NON_HEX = re.compile(r"[^0-9a-f]")

# The IEEE block sizes a prefix can name, in hex digits, longest first:
# MA-S (36 bits), MA-M (28 bits), MA-L (24 bits, the classic OUI).
OUI_BLOCK_DIGITS = (9, 7, 6)


def _hex_digits(value: str) -> str:
    return _NON_HEX.sub("", value.strip().lower())


def _colon_form(digits: str) -> str:
    """``1866961`` -> ``18:66:96:1``: pairs, then any odd digit on its own."""
    return ":".join(digits[i : i + 2] for i in range(0, len(digits), 2))


def normalize_oui_prefix(value: str) -> str | None:
    """Canonicalize an OUI hint or MAC to its lowercase colon-separated prefix.

    Accepts any common style — colon, dash, dot, or no separators. Exactly
    7 or 9 hex digits name an IEEE MA-M or MA-S block and keep their length
    (``18-66-96-1`` -> ``18:66:96:1``). Anything else with at least 6 — a
    bare 3-octet OUI (``001122``, ``00-11-22``, ``0011.22``) or a full MAC
    (``00:11:22:33:44:55``, ``0011.2233.4455``) — keeps the first three
    octets. Returns ``None`` when fewer than three octets of hex are
    present, so callers can warn instead of silently registering a key that
    can never match an observed MAC.

    Shared by the OUI table (``add_prefix``) and the tier matcher's OUI rules
    so registration and lookup always agree on the key regardless of the
    separator style the hint or the observed MAC happens to use.
    """
    hex_digits = _hex_digits(value)
    if len(hex_digits) < 6:
        return None
    if len(hex_digits) in (7, 9):
        return _colon_form(hex_digits)
    return _colon_form(hex_digits[:6])


def mac_prefix_keys(value: str) -> list[str]:
    """The prefixes a MAC (or a longer prefix) falls in, longest first: its
    36-, 28- and 24-bit prefixes as far as its digits reach. A lookup takes
    the first one registered, so a maker's own MA-M or MA-S block wins over
    a claim on the whole /24 it sits in."""
    hex_digits = _hex_digits(value)
    return [_colon_form(hex_digits[:n]) for n in OUI_BLOCK_DIGITS if len(hex_digits) >= n]


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
        found = self.lookup_block(mac)
        return (found[1], found[2]) if found else None

    def lookup_block(self, mac: str) -> tuple[str, str, str] | None:
        """The registered prefix a MAC falls in, with its manufacturer and
        category: ``(prefix, manufacturer, category)``, the longest prefix
        registered (``18:66:96:1`` before ``18:66:96``), or None."""
        normalized = self._normalize_mac(mac)
        if not normalized:
            return None
        for prefix in mac_prefix_keys(normalized):
            if prefix in self._table:
                manufacturer, category = self._table[prefix]
                return prefix, manufacturer, category
        return None

    def add_prefix(self, prefix: str, manufacturer: str, category: str) -> None:
        """Add a MAC OUI prefix to the lookup table.

        The prefix is canonicalized (any separator style, a bare 3-octet OUI,
        a 28- or 36-bit IEEE block, or a full MAC) so a hint written as
        ``001122`` or ``0011.22`` registers the same key an observed MAC
        resolves to. Only adds if the prefix is not
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
