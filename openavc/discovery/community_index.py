"""Community driver index + device catalog caches — fetched from GitHub raw.

Each cache keeps its copy for ten minutes, tries a failed fetch once more,
and answers the copy it has when that fails too. The server's one copy of
each is made in ``api/driver_catalog.py``: Browse Drivers, installs, the AI's
catalog tools and a device page's missing drivers read it, and ``main.py``
hands it to the Discovery engine, so a scan, a device audit and a problem
report read it too.

The index cache also keeps what it last fetched (``identity``): when, from
where, the SHA-256 of the bytes, and how many drivers they held, or why the
fetch failed. A device audit records that, so a report says exactly which
catalog its device was checked against.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any
from openavc.utils.outbound import internet_client, internet_failure, is_internet_failure_sentence

log = logging.getLogger("discovery.community")

COMMUNITY_REPO_URL = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"
CACHE_TTL = 600  # 10 minutes

#: How long a failed request waits before it is tried the second time.
_RETRY_DELAY_SECONDS = 2.0


async def _fetch_raw_with_retry(path: str) -> tuple[bytes | None, str]:
    """Fetch a file from the community repo with one retry.

    Returns (bytes, "") or (None, why it failed). When the network's security
    refused the connection (an untrusted certificate, a proxy), why is the
    sentence for the reader (``utils/outbound.py`` ``internet_failure``).
    """
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with internet_client(timeout=10.0) as client:
                resp = await client.get(f"{COMMUNITY_REPO_URL}/{path}")
                resp.raise_for_status()
                return resp.content, ""
        except Exception as e:
            last_error = e
            if attempt == 0:
                import asyncio
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
    log.warning("Failed to fetch %s after 2 attempts: %s", path, last_error)
    if last_error is not None:
        sentence = internet_failure(last_error, "raw.githubusercontent.com")
        if sentence:
            return None, sentence
    return None, str(last_error) or type(last_error).__name__


class CommunityIndexCache:
    """In-memory cache for the community driver index.json from GitHub."""

    def __init__(self) -> None:
        self._drivers: list[dict[str, Any]] = []
        self._fetched_at: float = 0.0
        self._sha256: str = ""
        self._last_error: str = ""
        self._last_attempt: float = 0.0
        # The reader's sentence when the network's security refused the last
        # fetch (a certificate this computer does not trust, a proxy).
        self.failure_sentence: str = ""

    def identity(self) -> dict[str, Any]:
        """Which catalog the cached drivers came from.

        ``fetched_at`` and ``sha256`` describe the copy in use (0 and "" when
        there is none); ``error`` is why the most recent fetch failed, empty
        when it succeeded. A failed fetch keeps the earlier copy, so both can
        be set at once.
        """
        return {
            "source": f"{COMMUNITY_REPO_URL}/index.json",
            "fetched_at": self._fetched_at,
            "sha256": self._sha256,
            "driver_count": len(self._drivers),
            "last_attempt": self._last_attempt,
            "error": self._last_error,
        }

    def cached(self) -> list[dict[str, Any]]:
        """The drivers last fetched, without fetching: [] before a first
        fetch."""
        return self._drivers

    async def get_drivers(self, force: bool = False) -> list[dict[str, Any]]:
        """Fetch community drivers, using cached data if fresh enough.

        ``force`` skips the cache and fetches now (the cached copy is still
        returned if that fetch fails). Returns [] on network failure
        (offline-safe). Retries once on failure.
        """
        now = time.time()
        if not force and self._drivers and (now - self._fetched_at) < CACHE_TTL:
            return self._drivers

        self._last_attempt = now
        raw, error = await _fetch_raw_with_retry("index.json")
        data: Any = None
        if raw is not None:
            try:
                data = json.loads(raw)
            except ValueError as e:
                error = f"index.json is not valid JSON: {e}"
        if data is None:
            self._last_error = error or "no data"
            self.failure_sentence = error if is_internet_failure_sentence(error) else ""
            if self._drivers:
                log.info("Using stale community index cache (%d drivers)", len(self._drivers))
            return self._drivers

        entries = data.get("drivers", []) if isinstance(data, dict) else data
        if not isinstance(entries, list):
            log.warning("index.json had unexpected shape; ignoring")
            self._last_error = "index.json had an unexpected shape"
            return self._drivers
        # Drop non-object entries so a single malformed element in the remote
        # catalog can't crash a downstream consumer's `.get(...)` (which would
        # abort the whole scan) — skip the bad entry, keep the rest.
        drivers = [d for d in entries if isinstance(d, dict)]
        skipped = len(entries) - len(drivers)
        if skipped:
            log.warning("index.json: skipped %d malformed (non-object) driver entr(ies)", skipped)
        self._drivers = drivers
        self._fetched_at = now
        self._sha256 = hashlib.sha256(raw or b"").hexdigest()
        self._last_error = ""
        self.failure_sentence = ""
        log.info("Community index fetched: %d drivers", len(drivers))
        return self._drivers


class CommunityDevicesCache:
    """In-memory cache for the community devices.json from GitHub.

    Provides O(1) lookup of `(manufacturer, model)` to the drivers that
    control that device. The catalog is reverse-indexed at build time from
    every driver's `compatible_models`, so a hit here is the authoritative
    "which driver controls this device" answer. One model can sit on more
    than one row (its name cased two ways, each row naming its own driver),
    so a lookup answers the drivers of every row for it, in row order.
    """

    def __init__(self) -> None:
        self._devices: list[dict[str, Any]] = []
        # (manufacturer_lower, model_lower) -> list of driver entries
        self._lookup: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._fetched_at: float = 0.0
        self._last_error: str = ""

    @property
    def last_error(self) -> str:
        """Why the most recent fetch failed, empty when it succeeded. A failed
        fetch keeps the earlier copy, so a lookup can still answer."""
        return self._last_error

    def cached(self) -> list[dict[str, Any]]:
        """The devices last fetched, without fetching: [] before a first
        fetch."""
        return self._devices

    async def get_devices(self) -> list[dict[str, Any]]:
        """Fetch the devices catalog, using cached data if fresh enough."""
        await self._refresh_if_stale()
        return self._devices

    async def find_drivers(
        self, manufacturer: str, model: str
    ) -> list[dict[str, Any]]:
        """Return driver entries (id, confidence, optional notes) controlling this device.

        Empty list when no exact match. Lookup is case-insensitive.
        """
        if not manufacturer or not model:
            return []
        await self._refresh_if_stale()
        return self._lookup.get((manufacturer.lower(), model.lower()), [])

    async def get_lookup(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Return the full case-insensitive lookup table.

        Useful for matchers that need a snapshot to score many devices.
        """
        await self._refresh_if_stale()
        return self._lookup

    async def _refresh_if_stale(self) -> None:
        now = time.time()
        if self._devices and (now - self._fetched_at) < CACHE_TTL:
            return

        devices = await self._fetch_devices()
        if devices is None:
            if self._devices:
                log.info("Using stale community devices cache (%d devices)", len(self._devices))
            return

        self._devices = devices
        self._lookup = _devices_lookup(devices)
        self._fetched_at = now
        self._last_error = ""
        log.info("Community devices fetched: %d devices", len(devices))

    async def _fetch_devices(self) -> list[dict[str, Any]] | None:
        """devices.json's rows, each an object; None (with why kept in
        ``last_error``) when there is no list of devices to read."""
        raw, error = await _fetch_raw_with_retry("devices.json")
        if raw is None:
            self._last_error = error or "no data"
            return None
        try:
            data = json.loads(raw)
        except ValueError as e:
            log.warning("devices.json is not valid JSON: %s", e)
            self._last_error = f"devices.json is not valid JSON: {e}"
            return None

        rows = data.get("devices", []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            log.warning("devices.json had unexpected shape; ignoring")
            self._last_error = "devices.json had an unexpected shape"
            return None

        # Drop non-object entries so one malformed element can't crash the
        # per-element `.get(...)` below (and, via the engine catch-all, abort
        # the whole scan) — skip the bad entry, keep the rest.
        devices = [d for d in rows if isinstance(d, dict)]
        skipped = len(rows) - len(devices)
        if skipped:
            log.warning("devices.json: skipped %d malformed (non-object) entr(ies)", skipped)
        return devices


def _device_key(row: dict[str, Any]) -> tuple[str, str] | None:
    """A row's (manufacturer, model), lowercased; None when either is not
    text or is empty."""
    names = (row.get("manufacturer"), row.get("model"))
    if all(isinstance(name, str) and name for name in names):
        return names[0].lower(), names[1].lower()
    return None


def _devices_lookup(devices: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """(manufacturer, model), lowercased, to the driver entries of every row
    naming them. A row whose manufacturer or model is not text, or whose
    drivers are not a list, is skipped, as is a driver entry that is not an
    object."""
    lookup: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in devices:
        key, drivers = _device_key(row), row.get("drivers")
        if key is None or not isinstance(drivers, list):
            continue
        lookup.setdefault(key, []).extend(drv for drv in drivers if isinstance(drv, dict))
    return lookup
