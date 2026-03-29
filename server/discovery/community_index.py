"""Community driver index cache — fetches and caches index.json from GitHub."""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("discovery.community")

COMMUNITY_REPO_URL = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"
CACHE_TTL = 600  # 10 minutes


class CommunityIndexCache:
    """In-memory cache for the community driver index.json from GitHub."""

    def __init__(self) -> None:
        self._drivers: list[dict[str, Any]] = []
        self._fetched_at: float = 0.0

    async def get_drivers(self) -> list[dict[str, Any]]:
        """Fetch community drivers, using cached data if fresh enough.

        Returns [] on network failure (offline-safe).
        """
        now = time.time()
        if self._drivers and (now - self._fetched_at) < CACHE_TTL:
            return self._drivers

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{COMMUNITY_REPO_URL}/index.json")
                resp.raise_for_status()
                data = resp.json()
                drivers = data.get("drivers", []) if isinstance(data, dict) else data
                self._drivers = drivers
                self._fetched_at = now
                log.info("Community index fetched: %d drivers", len(drivers))
                return self._drivers
        except Exception as e:  # Catch-all: ImportError (httpx missing), network, HTTP, JSON errors
            log.warning("Failed to fetch community driver index: %s", e)
            # Return stale cache if available, else empty
            return self._drivers
