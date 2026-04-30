"""Community driver index + device catalog caches — fetched from GitHub raw."""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("discovery.community")

COMMUNITY_REPO_URL = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"
CACHE_TTL = 600  # 10 minutes


async def _fetch_json_with_retry(path: str) -> dict[str, Any] | list[Any] | None:
    """Fetch a JSON file from the community repo with one retry. None on failure."""
    try:
        import httpx
    except ImportError:
        log.warning("httpx not installed — cannot fetch %s", path)
        return None

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{COMMUNITY_REPO_URL}/{path}")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_error = e
            if attempt == 0:
                import asyncio
                await asyncio.sleep(2.0)
    log.warning("Failed to fetch %s after 2 attempts: %s", path, last_error)
    return None


class CommunityIndexCache:
    """In-memory cache for the community driver index.json from GitHub."""

    def __init__(self) -> None:
        self._drivers: list[dict[str, Any]] = []
        self._fetched_at: float = 0.0

    async def get_drivers(self) -> list[dict[str, Any]]:
        """Fetch community drivers, using cached data if fresh enough.

        Returns [] on network failure (offline-safe). Retries once on failure.
        """
        now = time.time()
        if self._drivers and (now - self._fetched_at) < CACHE_TTL:
            return self._drivers

        data = await _fetch_json_with_retry("index.json")
        if data is None:
            if self._drivers:
                log.info("Using stale community index cache (%d drivers)", len(self._drivers))
            return self._drivers

        drivers = data.get("drivers", []) if isinstance(data, dict) else data
        self._drivers = drivers
        self._fetched_at = now
        log.info("Community index fetched: %d drivers", len(drivers))
        return self._drivers


class CommunityDevicesCache:
    """In-memory cache for the community devices.json from GitHub.

    Provides O(1) lookup of `(manufacturer, model)` to the drivers that
    control that device. The catalog is reverse-indexed at build time from
    every driver's `compatible_models`, so a hit here is the authoritative
    "which driver controls this device" answer.
    """

    def __init__(self) -> None:
        self._devices: list[dict[str, Any]] = []
        # (manufacturer_lower, model_lower) -> list of driver entries
        self._lookup: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._fetched_at: float = 0.0

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

        data = await _fetch_json_with_retry("devices.json")
        if data is None:
            if self._devices:
                log.info("Using stale community devices cache (%d devices)", len(self._devices))
            return

        devices = data.get("devices", []) if isinstance(data, dict) else data
        if not isinstance(devices, list):
            log.warning("devices.json had unexpected shape; ignoring")
            return

        self._devices = devices
        self._lookup = {}
        for d in devices:
            mfr = d.get("manufacturer", "")
            model = d.get("model", "")
            drivers = d.get("drivers", [])
            if mfr and model and isinstance(drivers, list):
                self._lookup[(mfr.lower(), model.lower())] = drivers
        self._fetched_at = now
        log.info("Community devices fetched: %d devices", len(devices))
