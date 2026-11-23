"""Which drivers a Discovery scan identifies devices with: one copy of each
driver, installed or from the community catalog, and the tables built from
those copies (the signal index, each driver's category, profile and name).

The rules worth knowing before changing anything here:

* One copy of each driver is read (``driver_copies``): an installed copy,
  unless the catalog's version is newer. A tie keeps the installed copy,
  which may carry local corrections; a stale install never masks a newer
  catalog's fingerprints.
* Which version is newer is the version rule's (``utils/version_rule.py``:
  a pre-release is older than its release); an unparseable or missing
  version sorts lowest, so a real catalog version supersedes a driver that
  declares none.
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
from openavc.discovery.result import DeviceState, IdentificationMatch, IdentifyingDriver
from openavc.discovery.tier_matcher import DriverProfile, SignalIndex
from openavc.utils.version_rule import read_version

# The discovery engine's own log, which the IDE's log view files these lines under.
log = logging.getLogger("discovery")


def _catalog_is_newer(catalog_version: object, installed_version: object) -> bool:
    """Whether the catalog's version of a driver is newer than the installed
    one's, by the version rule. A version that cannot be read sorts lowest, so
    a real catalog version supersedes an install that declares none."""
    catalog = read_version(catalog_version)
    if catalog is None:
        return False
    installed = read_version(installed_version)
    return installed is None or catalog > installed


def _catalog_copy_is_read(entry: Any, installed_by_id: dict[str, dict[str, Any]]) -> bool:
    """Whether discovery reads a catalog entry's copy of its driver: nothing
    installed has its id, or the catalog's version is newer than the
    installed one's. An element that is not an object, or has no id, is not
    read."""
    if not isinstance(entry, dict) or not entry.get("id"):
        return False
    installed = installed_by_id.get(str(entry["id"]))
    return installed is None or _catalog_is_newer(
        entry.get("version"), installed.get("version"),
    )


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


class DriverNames:
    """What each driver a scan reads is called, so an identified device is
    named by the driver that identified it (``result.identity_of``).

    Built from the same copies the signal index is (``driver_copies``): a
    driver only the community catalog lists names a device as an installed
    one does.
    """

    def __init__(self, names: dict[str, tuple[str, str]]) -> None:
        self._names = names

    @classmethod
    def of(cls, drivers: list[dict[str, Any]]) -> DriverNames:
        """driver id -> (its name, its maker) for every driver that names itself."""
        return cls({
            str(d["id"]): (d["name"].strip(), str(d.get("manufacturer") or "").strip())
            for d in drivers
            if d.get("id") and isinstance(d.get("name"), str) and d["name"].strip()
        })

    def identifying(self, match: IdentificationMatch | None) -> IdentifyingDriver | None:
        """The driver ``match`` identified a device as, or None when it names
        none (possible, unknown) or that driver has no name."""
        if match is None or match.state != DeviceState.IDENTIFIED or not match.driver_id:
            return None
        named = self._names.get(match.driver_id)
        if named is None:
            return None
        return IdentifyingDriver(
            name=named[0], manufacturer=named[1], cross_vendor=match.cross_vendor,
        )


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
