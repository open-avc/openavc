"""Which drivers a Discovery scan identifies devices with: one copy of each
driver, installed or from the community catalog, and the tables built from
those copies (the signal index, each driver's category and profile).

The rules worth knowing before changing anything here:

* One copy of each driver is read (``driver_copies``): an installed copy,
  unless the catalog's version is newer. A tie keeps the installed copy,
  which may carry local corrections; a stale install never masks a newer
  catalog's fingerprints.
* An unparseable or missing version sorts lowest (``driver_version_tuple``),
  so a real catalog version supersedes a driver that declares none.
* A catalog index the signal-index builder rejects falls back to the
  installed drivers' hints alone, then to an empty index; the scan never
  stops for it (``signal_index_from``).

The engine (``DiscoveryEngine._rebuild_signal_index``) calls these as the
registry or the catalog changes and keeps what they return.
"""

from __future__ import annotations

import logging
from typing import Any

from openavc.discovery.hints import DiscoveryHint, build_signal_index
from openavc.discovery.tier_matcher import DriverProfile, SignalIndex

# The discovery engine's own log, which the IDE's log view files these lines under.
log = logging.getLogger("discovery")


def driver_version_tuple(version: object) -> tuple[int, int, int]:
    """Comparable (major, minor, patch) from a driver version string like
    ``'1.2.0'``. Unparseable or missing versions sort lowest, so a real catalog
    version supersedes a driver that declares no usable version. Stdlib-only
    (no regex) and never raises.
    """
    nums: list[int] = []
    for part in str(version or "").split(".")[:3]:
        digits = ""
        for ch in part.strip():
            if ch.isdigit():
                digits += ch
            else:
                break
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _catalog_copy_is_read(entry: Any, installed_by_id: dict[str, dict[str, Any]]) -> bool:
    """Whether discovery reads a catalog entry's copy of its driver: nothing
    installed has its id, or the catalog's version is newer than the
    installed one's. An element that is not an object, or has no id, is not
    read."""
    if not isinstance(entry, dict) or not entry.get("id"):
        return False
    installed = installed_by_id.get(str(entry["id"]))
    return installed is None or driver_version_tuple(
        entry.get("version")
    ) > driver_version_tuple(installed.get("version"))


def driver_copies(
    installed: list[dict[str, Any]], catalog: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The one copy of each driver discovery reads, as (the installed copies
    kept, the catalog copies read).

    For a driver both installed and in the catalog, the NEWER copy is
    authoritative for discovery hints. The installed copy usually wins (it
    may carry local corrections, and it wins a tie), but a STALE install must
    not mask a newer catalog's improved fingerprints -- otherwise a device
    that a clean machine would identify outright shows here only as a weak
    "possible" just because an older copy happens to be installed. The
    superseded installed copy is dropped so its hints can't shadow the
    catalog's stronger signal.

    The cache filters non-object entries at the fetch boundary; one here
    anyway (or one with no id) is left out, so a malformed element from any
    caller degrades that one rule rather than aborting the whole scan.
    """
    installed_by_id = {str(d.get("id") or ""): d for d in installed if d.get("id")}
    catalog_read = [d for d in catalog if _catalog_copy_is_read(d, installed_by_id)]
    superseded = {str(d["id"]) for d in catalog_read} & installed_by_id.keys()
    installed_kept = [d for d in installed if str(d.get("id") or "") not in superseded]
    return installed_kept, catalog_read


def driver_categories(drivers: list[dict[str, Any]]) -> dict[str, str]:
    """driver id -> the category that driver declares, for every driver that
    names one. An identified device with no category takes its driver's (see
    ``DiscoveryEngine._fill_category_from_match``)."""
    return {
        str(d["id"]): str(d["category"])
        for d in drivers
        if d.get("id") and isinstance(d.get("category"), str) and d["category"]
    }


def driver_profiles(drivers: list[dict[str, Any]]) -> dict[str, DriverProfile]:
    """driver id -> what it says about its equipment, which the matcher asks
    only to break a tie."""
    return {str(d["id"]): DriverProfile.of(d) for d in drivers if d.get("id")}


def signal_index_from(
    all_hints: list[DiscoveryHint], installed_hints: list[DiscoveryHint],
) -> SignalIndex:
    """The signal index for every hint; else, if that is rejected, for the
    installed drivers' hints alone; else an empty one.

    ``build_signal_index`` isolates colliding rules itself (drops the rule,
    keeps the rest), so a rejection is something unexpected. Installed-only is
    the last line of defense.
    """
    try:
        return build_signal_index(all_hints)
    except ValueError as exc:
        log.error("Discovery signal index rejected with catalog: %s; falling back to installed-only", exc)
    try:
        return build_signal_index(installed_hints)
    except ValueError as exc:
        log.error("Installed-only signal index also rejected: %s", exc)
        return SignalIndex()
