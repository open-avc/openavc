"""Client for an external infrared code database (IRDB).

Searches a large crowd-sourced database of remote-control codes by brand and
device, then renders a chosen function to vendor-neutral Pronto hex via
:mod:`server.transport.ir_render`. The database stores codes compactly as
``(protocol, device, subdevice, function)`` rather than raw timing, so a code is
only emittable once the renderer turns it into Pronto — this module is the bridge
between the two.

The database is fetched at runtime from the recommended CDN and cached in memory;
nothing is bundled or written to disk, so the local copy always tracks upstream
(and stale cache / offline is handled gracefully). Fetch and parse are kept
separate: the parse helpers are pure and unit-tested, the cache does the I/O.

Per the database's license, any product that accesses it must display this
notice and notify the project before use; both are honored by the search UI and
by opening a notify issue on the project's tracker.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

from server.transport.ir_render import UnsupportedProtocolError, is_supported, render_pronto

log = logging.getLogger("ir.database")

# Fetched over the CDN the database README recommends (kinder to GitHub than raw
# and cache-friendly). ``codes/index`` lists every code set as a relative path.
IRDB_CDN = "https://cdn.jsdelivr.net/gh/probonopd/irdb@master"
INDEX_PATH = "codes/index"
CACHE_TTL = 3600  # 1 hour

IRDB_HOMEPAGE = "https://github.com/probonopd/irdb"
IRDB_ISSUES = "https://github.com/probonopd/irdb/issues"
# Required attribution — shown wherever the database is surfaced.
IRDB_NOTICE = (
    "Contains/accesses irdb by Simon Peter and contributors, used under "
    "permission. For licensing details and for information on how to contribute "
    "to the database, see https://github.com/probonopd/irdb"
)


# ── pure parse helpers (no I/O) ───────────────────────────────────────────────


def parse_index(text: str) -> list[dict[str, Any]]:
    """Parse the ``codes/index`` manifest into code-set entries.

    Each line is ``<brand>/<device type>/<device>,<subdevice>.csv``. Malformed
    lines are skipped rather than aborting the whole parse.
    """
    entries: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or not line.endswith(".csv"):
            continue
        segs = line.split("/")
        if len(segs) < 3:
            continue
        brand = segs[0]
        device_type = "/".join(segs[1:-1])
        stem = segs[-1][:-4]  # drop ".csv"
        try:
            dev_str, sub_str = stem.rsplit(",", 1)
            device, subdevice = int(dev_str), int(sub_str)
        except ValueError:
            continue
        entries.append(
            {
                "brand": brand,
                "type": device_type,
                "device": device,
                "subdevice": subdevice,
                "path": line,
            }
        )
    return entries


def parse_csv(text: str) -> list[dict[str, Any]]:
    """Parse a code-set CSV into function rows.

    Columns: ``functionname,protocol,device,subdevice,function``. The header row
    and any malformed rows are skipped.
    """
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        name, protocol = parts[0].strip(), parts[1].strip()
        if name.lower() == "functionname" or not protocol:
            continue
        try:
            device = int(parts[2])
            subdevice = int(parts[3])
            function = int(parts[4])
        except ValueError:
            continue
        rows.append(
            {
                "name": name,
                "protocol": protocol,
                "device": device,
                "subdevice": subdevice,
                "function": function,
            }
        )
    return rows


def render_function(row: dict[str, Any]) -> dict[str, Any]:
    """Annotate a function row with its rendered Pronto (or why it can't render)."""
    protocol = row["protocol"]
    out = {**row, "supported": is_supported(protocol), "pronto": None, "error": None}
    if not out["supported"]:
        out["error"] = f"Protocol '{protocol}' is not supported yet"
        return out
    try:
        out["pronto"] = render_pronto(
            protocol, row["device"], row["subdevice"], row["function"]
        )
    except (UnsupportedProtocolError, ValueError) as e:
        out["supported"] = False
        out["error"] = str(e)
    return out


# ── async cache / client ──────────────────────────────────────────────────────


class IrDatabase:
    """In-memory-cached client for the IR code database."""

    def __init__(self) -> None:
        self._index: list[dict[str, Any]] = []
        self._paths: set[str] = set()
        self._fetched_at: float = 0.0

    async def _fetch_text(self, path: str) -> str | None:
        """Fetch a text file from the database CDN with one retry. None on failure."""
        try:
            import httpx
        except ImportError:
            log.warning("httpx not installed — cannot fetch IR database")
            return None
        url = f"{IRDB_CDN}/{quote(path, safe='/,@')}"
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    return resp.text
            except Exception as e:  # noqa: BLE001 — offline-safe, reported to caller
                last_error = e
                if attempt == 0:
                    await asyncio.sleep(1.5)
        log.warning("Failed to fetch %s after 2 attempts: %s", path, last_error)
        return None

    async def _load_index(self) -> list[dict[str, Any]]:
        now = time.time()
        if self._index and (now - self._fetched_at) < CACHE_TTL:
            return self._index
        text = await self._fetch_text(INDEX_PATH)
        if text is None:
            if self._index:
                log.info("Using stale IR database index (%d entries)", len(self._index))
            return self._index
        self._index = parse_index(text)
        self._paths = {e["path"] for e in self._index}
        self._fetched_at = now
        log.info("IR database index fetched: %d code sets", len(self._index))
        return self._index

    async def available(self) -> bool:
        """Whether the index could be loaded (network reachable at least once)."""
        return bool(await self._load_index())

    async def brands(self) -> list[str]:
        """All brands in the database, sorted case-insensitively."""
        index = await self._load_index()
        return sorted({e["brand"] for e in index}, key=str.lower)

    async def devices(self, brand: str) -> list[dict[str, Any]]:
        """Code sets for a brand (each is one CSV: a device type + code numbers)."""
        index = await self._load_index()
        want = brand.strip().lower()
        out = [e for e in index if e["brand"].lower() == want]
        out.sort(key=lambda e: (e["type"].lower(), e["device"], e["subdevice"]))
        return out

    async def functions(self, path: str) -> list[dict[str, Any]]:
        """Functions of one code set, each rendered to Pronto where supported.

        ``path`` must be a code-set path present in the index (guards the fetch
        against arbitrary URLs).
        """
        await self._load_index()
        if path not in self._paths:
            raise ValueError(f"Unknown code set: {path}")
        # Index paths are relative to codes/; the files live under it.
        text = await self._fetch_text(f"codes/{path}")
        if text is None:
            raise ConnectionError("Could not reach the IR code database")
        return [render_function(row) for row in parse_csv(text)]
