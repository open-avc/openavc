"""What a Discovery scan holds: its status as it runs, and the outcome every
reader is given.

``ScanStatus`` is the scan's progress and end state, as the engine moves it:
the phase, the bar, the warnings, how it ended. ``ScanOutcome`` is what the
scan holds at one moment, read from that status and the engine's results:
the devices it keeps, in the order a reader lists them, and their count.

The rules worth knowing before changing anything here:

* Every exit reads the outcome (``DiscoveryEngine.outcome``): the WebSocket's
  ``discovery.complete``, REST's status, results and export, the assistant's
  ``get_discovery_results`` and the re-match after a driver change. None of
  them counts or filters the engine's results on its own, so a count on one
  is the count on all of them.
* A host the ARP table only named is not in it while its probation is open
  (``arp_phase``): the live feed does not send one either, and no other
  reader may list or count a device a client has never been sent.
* The count is the length of the kept devices, computed when it is read and
  never stored, so nothing can leave it behind.
"""

from __future__ import annotations

from collections.abc import Container, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.discovery.result import DiscoveredDevice

# The order every reader lists a scan's devices in: identified, possible,
# unknown, then by address within each.
_STATE_RANK = {"identified": 0, "possible": 1, "unknown": 2}


class ScanStatus:
    """A scan's progress and how it ended, as the engine moves them. It holds
    no device count: that is the outcome's."""

    def __init__(self) -> None:
        self.scan_id: str = ""
        # idle, running, complete, cancelled, partial ("partial" = the scan hit
        # its time limit or errored, so the phases below ran on a subset of the
        # network; the accompanying warning says so).
        self.status: str = "idle"
        self.phase: str = ""
        self.phase_number: int = 0
        self.total_phases: int = 8
        self.message: str = ""
        self.progress: float = 0.0
        self.started_at: float = 0.0
        self.duration: float = 0.0
        self.subnets: list[str] = []
        self.total_hosts_scanned: int = 0
        self.active_adapter: dict[str, str] | None = None
        # Environment problems that kept scan phases from working (missing
        # ping capability, multicast joins failing, ...) plus anything the
        # budget narrowed. Zero devices plus a reason beats zero devices
        # plus silence, and narrowed coverage must never read as full.
        self.warnings: list[str] = []
        # Ceiling on the whole scan: the request's explicit timeout, or the
        # scan_depth policy's when it supplied none.
        self.budget_seconds: float = 0.0
        # Deadline actually granted to each budgeted phase, derived from
        # that phase's measured workload (see discovery.scan_budget).
        self.phase_budgets: dict[str, float] = {}

    def to_dict(self) -> dict[str, Any]:
        """The status as REST sends it, less the count, which is the
        outcome's (``DiscoveryEngine.get_status`` adds it)."""
        return {
            "scan_id": self.scan_id,
            "status": self.status,
            "phase": self.phase,
            "phase_number": self.phase_number,
            "total_phases": self.total_phases,
            "message": self.message,
            "progress": round(self.progress, 2),
            "started_at": self.started_at,
            "duration": round(self.duration, 2),
            "subnets": self.subnets,
            "total_hosts_scanned": self.total_hosts_scanned,
            "active_adapter": self.active_adapter,
            "warnings": list(self.warnings),
            "budget_seconds": round(self.budget_seconds, 2),
            "phase_budgets": dict(self.phase_budgets),
        }


def _display_order(device: DiscoveredDevice) -> tuple:
    state = device.identification.state.value if device.identification else "unknown"
    try:
        address = tuple(int(part) for part in device.ip.split("."))
    except ValueError:
        address = (999, 999, 999, 999)
    return (_STATE_RANK.get(state, 9), address)


@dataclass(frozen=True)
class ScanOutcome:
    """What a scan holds at one moment: how it stands or ended, its warnings,
    and the devices it keeps, in the order a reader lists them."""

    scan_id: str
    status: str
    devices: tuple[DiscoveredDevice, ...]
    warnings: tuple[str, ...]
    duration: float

    @classmethod
    def read(
        cls,
        status: ScanStatus,
        results: Mapping[str, DiscoveredDevice],
        on_probation: Container[str],
    ) -> ScanOutcome:
        """The outcome of ``status`` over ``results``, less the hosts still
        ``on_probation``."""
        kept = sorted(
            (device for ip, device in results.items() if ip not in on_probation),
            key=_display_order,
        )
        return cls(
            scan_id=status.scan_id,
            status=status.status,
            devices=tuple(kept),
            warnings=tuple(status.warnings),
            duration=status.duration,
        )

    @property
    def count(self) -> int:
        """How many devices the scan keeps."""
        return len(self.devices)

    def device_records(self) -> list[dict[str, Any]]:
        """Each kept device's record, as REST, the assistant and the feed send it."""
        return [device.to_dict() for device in self.devices]

    def complete_event(self) -> dict[str, Any]:
        """The ``discovery.complete`` message: how the scan ended, its count,
        duration and warnings."""
        return {
            "type": "discovery.complete",
            "scan_id": self.scan_id,
            "status": self.status,
            "total_devices": self.count,
            "duration_seconds": self.duration,
            "warnings": list(self.warnings),
        }
