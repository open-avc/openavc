"""Python ``_discovery.py`` companion API + loader.

Drivers whose discovery wire format can't be expressed declaratively
(multi-step handshakes, encrypted payloads, big-endian bitfield
framing) ship a sibling Python file alongside their .avcdriver:

    audio/example_dsp.avcdriver
    audio/example_dsp_discovery.py

The companion exposes a single async function:

    async def probe(ctx: ProbeContext) -> None:
        # send packets, listen, call ctx.emit_*

The discovery engine loads every loaded driver's companion at startup
(or after a catalog refresh) and invokes its ``probe()`` once per
scan, with a hard timeout enforced via ``asyncio.wait_for``.

Safety
------
- The companion **must** bind every socket to ``ctx.source_ip``. The
  loader doesn't sandbox Python (impractical), but the API takes
  ``source_ip`` as part of the context so the contract is explicit.
- A hard wall-clock timeout (default 10s, capped at 30s) bounds the
  companion's runtime via ``asyncio.wait_for``.
- Companion code is community-trust same as Python drivers — we wrap
  every invocation in a try/except and log on failure.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from server.discovery.result import Evidence
from server.discovery.tier_matcher import (
    evidence_active_probe,
    evidence_broadcast,
    evidence_oui,
)

log = logging.getLogger("discovery.companion")

DEFAULT_PROBE_TIMEOUT_SECONDS: float = 10.0
MAX_PROBE_TIMEOUT_SECONDS: float = 30.0


@dataclass
class ProbeContext:
    """Argument passed to a companion's ``probe(ctx)`` function.

    Companions emit Evidence by calling ``emit_broadcast``,
    ``emit_active``, or ``emit_oui``. Each call routes the resulting
    Evidence to the matching device record in the engine's results
    dict, so the matcher picks it up the same way as built-in probes.

    Port-scan reuse
    ---------------
    By the time companions run, the engine has already discovered
    which hosts answer on which TCP ports. ``hosts_by_open_port`` is
    that map — keyed by port number, valued by a tuple of IPs the
    engine observed open on that port. Companions whose protocol has
    no native discovery layer should consult this map instead of
    iterating ``target_subnets`` and re-doing the port scan. Looking
    up an unseen port returns the empty tuple via
    ``hosts_by_open_port.get(port, ())``.

    Canonical synthetic probe IDs
    -----------------------------
    A companion declared via ``discovery.python`` in the driver's
    .avcdriver auto-registers two ``SignalRule`` records — one
    broadcast, one active — under the IDs:

      ``custom_<driver_id>_companion_udp`` → broadcast
      ``custom_<driver_id>_companion_tcp`` → active

    Use ``emit_broadcast`` / ``emit_active`` with no ``probe_id``
    argument to emit under those canonical IDs (matching the
    auto-registered rules so the matcher identifies the device as
    this companion's driver). Pass an explicit ``probe_id`` only
    when emitting evidence that overlaps with a different driver's
    registered probe — rare; you'll usually let the default fire.
    """

    driver_id: str
    source_ip: str
    target_subnets: tuple[str, ...]
    timeout_seconds: float
    log: logging.Logger
    # Engine-supplied callback. Treat as private to the companion API.
    _emit_for_host: Callable[[str, Evidence], Awaitable[None]] = field(repr=False)
    # Engine-built map of open TCP port -> tuple of IPs observed open
    # on that port. Populated from ``self.results[ip].open_ports`` at
    # context-construction time. Empty when the engine ran before the
    # port-scan phase (shouldn't happen with the standard phase order)
    # or no host answered on any scanned port.
    hosts_by_open_port: dict[int, tuple[str, ...]] = field(default_factory=dict)

    @property
    def companion_broadcast_probe_id(self) -> str:
        """Canonical synthetic ID for this companion's broadcast probe."""
        return f"custom_{self.driver_id}_companion_udp"

    @property
    def companion_active_probe_id(self) -> str:
        """Canonical synthetic ID for this companion's active probe."""
        return f"custom_{self.driver_id}_companion_tcp"

    async def emit_broadcast(
        self,
        host: str,
        *,
        probe_id: str | None = None,
        response: dict[str, Any] | None = None,
        txt: dict[str, str] | None = None,
        port: int | None = None,
        matched_pattern: str | None = None,
    ) -> None:
        """Emit a broadcast-probe fingerprint match from ``host``.

        Defaults ``probe_id`` to ``custom_<driver_id>_companion_udp``
        — the canonical synthetic ID auto-registered when the driver
        declares ``discovery.python``. Reserved keys (``manufacturer``,
        ``make``) inside ``txt`` are lifted to the manufacturer-alias
        enrichment path automatically by the engine's
        ``extract_vendor_strings`` finalize step.

        ``port`` is the UDP port the companion broadcast to and
        ``matched_pattern`` is a short ``kind:value`` description of the
        matcher that fired (e.g. ``"hex:417274..."`` /
        ``"regex:NovaStar"``). Both feed the scan-results "Why?" reveal
        — pass them so the UI can render the full §10 phrasing.
        """
        ev = evidence_broadcast(
            probe_id or self.companion_broadcast_probe_id,
            response=response or {"ip": host},
            txt=txt,
            port=port,
            matched_pattern=matched_pattern,
        )
        await self._emit_for_host(host, ev)

    async def emit_active(
        self,
        host: str,
        response: dict[str, Any],
        *,
        probe_id: str | None = None,
        port: int | None = None,
    ) -> None:
        """Emit an active-probe fingerprint match.

        Defaults ``probe_id`` to ``custom_<driver_id>_companion_tcp``.
        ``port`` is the TCP port the companion connected to; pass it
        so the scan-results "Why?" reveal can render
        "TCP probe on port <port> returned <excerpt>".
        """
        ev = evidence_active_probe(
            probe_id or self.companion_active_probe_id,
            response=response,
            port=port,
        )
        await self._emit_for_host(host, ev)

    async def emit_oui(
        self,
        mac: str,
        host: str,
        *,
        vendor: str | None = None,
    ) -> None:
        """Emit an OUI enrichment record bound to ``host``."""
        ev = evidence_oui(mac, vendor)
        await self._emit_for_host(host, ev)


CompanionProbe = Callable[[ProbeContext], Awaitable[None]]


def load_discovery_companions(
    directories: list[Path | str],
) -> dict[str, CompanionProbe]:
    """Scan directories for ``*_discovery.py`` and return registered probes.

    Each loaded module must expose:

        async def probe(ctx: ProbeContext) -> None: ...

    Modules without an async ``probe`` are skipped with a warning.
    Returns ``{driver_id: probe_fn}`` where driver_id is derived from
    the filename (``foo_discovery.py`` -> ``foo``).

    Duplicate driver IDs (same companion in multiple scanned
    directories) are resolved last-write-wins with a warning so
    overrides in a user's ``driver_repo/`` win over built-ins.
    """
    probes: dict[str, CompanionProbe] = {}

    for d in directories:
        path = Path(d)
        if not path.exists():
            continue
        for filepath in sorted(path.rglob("*_discovery.py")):
            driver_id = filepath.stem.removesuffix("_discovery")
            if not driver_id:
                continue

            module_name = (
                f"openavc_discovery_companion_{driver_id}_"
                f"{abs(hash(str(filepath))) & 0xffffff:06x}"
            )
            try:
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                if spec is None or spec.loader is None:
                    log.warning(
                        "Could not create module spec for discovery companion %s",
                        filepath,
                    )
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception:
                log.exception(
                    "Failed to load discovery companion %s", filepath,
                )
                continue

            probe_fn = getattr(module, "probe", None)
            if probe_fn is None or not asyncio.iscoroutinefunction(probe_fn):
                log.warning(
                    "Discovery companion %s has no async 'probe' function; skipped",
                    filepath,
                )
                continue

            if driver_id in probes:
                log.warning(
                    "Duplicate _discovery.py for driver %s; %s overrides earlier load",
                    driver_id, filepath,
                )
            probes[driver_id] = probe_fn
            log.info(
                "Loaded discovery companion for %s from %s",
                driver_id, filepath,
            )

    return probes


async def run_companion(
    driver_id: str,
    probe_fn: CompanionProbe,
    ctx: ProbeContext,
) -> None:
    """Invoke a companion probe with hard timeout + exception isolation.

    The runner caps ``ctx.timeout_seconds`` at
    ``MAX_PROBE_TIMEOUT_SECONDS`` regardless of what the companion or
    engine requests, so a buggy or hostile companion can't stall a
    scan beyond that ceiling.
    """
    cap = min(max(ctx.timeout_seconds, 0.5), MAX_PROBE_TIMEOUT_SECONDS)
    try:
        await asyncio.wait_for(probe_fn(ctx), timeout=cap)
    except asyncio.TimeoutError:
        log.warning(
            "Discovery companion %s exceeded %.1fs timeout",
            driver_id, cap,
        )
    except Exception:
        log.exception("Discovery companion %s failed", driver_id)
