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

        Returns [] on network failure (offline-safe). Retries once on failure.
        """
        now = time.time()
        if self._drivers and (now - self._fetched_at) < CACHE_TTL:
            return self._drivers

        try:
            import httpx
        except ImportError:
            log.warning("httpx not installed — cannot fetch community index")
            return self._drivers

        last_error = None
        for attempt in range(2):  # Try twice
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"{COMMUNITY_REPO_URL}/index.json")
                    resp.raise_for_status()
                    data = resp.json()
                    drivers = data.get("drivers", []) if isinstance(data, dict) else data
                    self._drivers = drivers
                    self._fetched_at = now
                    log.info("Community index fetched: %d drivers", len(drivers))
                    return self._drivers
            except Exception as e:
                last_error = e
                if attempt == 0:
                    import asyncio
                    await asyncio.sleep(2.0)  # Brief backoff before retry

        log.warning("Failed to fetch community driver index after 2 attempts: %s", last_error)
        if self._drivers:
            log.info("Using stale community index cache (%d drivers)", len(self._drivers))
        return self._drivers
