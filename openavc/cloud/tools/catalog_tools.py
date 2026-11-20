"""The AI's view of the community driver catalog: search, one driver's entry,
the drivers for a named model, and installing one.

The catalog is the openavc-drivers repo's published branch
(``COMMUNITY_REPO_URL``): ``index.json`` lists every driver, ``devices.json``
maps each manufacturer and model to the drivers that control it. These tools
read this server's one copy of each (``api/driver_catalog.py``
``index_cache`` and ``devices_cache``), the index the same copy Browse
Drivers lists and installs check against, so the AI and the Programmer answer
the same catalog. Each copy is kept for ten minutes, a failed fetch is tried
once more, and the copy it has is answered when that fails too; with no copy
to answer, the tool says the catalog could not be fetched, which is not the
same answer as a catalog with nothing in it.

A search answers lean entries (``_lean_driver_entry``). What a free-text
query finds is the search rule's (``utils/search_words``, the rule Browse
Community, the Installed list and the Add Device picker ask through the
Programmer's copy): every word typed is somewhere in the entry's id, name,
manufacturer, description, tags or compatible models, each word wherever it
is. What it found is ranked by how the query scores each one
(``_score_driver_match``: an exact id or manufacturer first, then the id,
manufacturer, name, compatible models, tags and description, each with its
own weight; several words add each word's score to the whole query's). Each entry with a file is
answered with its ``download_url`` (``api/driver_catalog.catalog_file_url``,
the address every catalog install is built from), built into the answer and never written
into the shared copy. Installing goes through the REST install route, so an
AI install does everything an IDE install does.

The catalog is published data, read as it comes: a row that is not an object
is skipped (by the caches), and a field of the wrong type reads as absent
(_text, _listed), so one bad row never hides the rest. A file that is not
JSON, or whose list is not a list, answers as a catalog that could not be
fetched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openavc.api.driver_catalog import catalog_file_url, devices_cache, index_cache
from openavc.utils.search_words import finds_every_word, search_words

#: The fields a search filters on, each compared whole, in any case.
_SEARCH_FILTERS = ("category", "manufacturer", "transport")

#: What a search answers of each entry: enough to pick a driver.
#: ``get_community_driver_detail`` answers the rest (help, compatible models).
_LEAN_FIELDS = (
    "id", "name", "manufacturer", "category", "version", "author",
    "transport", "description", "file", "format", "ports", "protocols",
    "tags", "simulated", "verified", "deprecated", "replacement_id",
    "min_platform_version", "download_url",
)


async def _community_drivers() -> list[dict] | None:
    """The catalog's index from this server's copy; None when there is no
    copy to answer and the fetch failed, which an empty catalog is not."""
    cache = index_cache()
    drivers = await cache.get_drivers()
    if not drivers and cache.identity()["error"]:
        return None
    return drivers


def _with_download_url(drv: dict) -> dict:
    """A copy of a catalog entry, with its ``download_url`` when it names a
    file. The entry itself is the shared copy Browse Drivers lists, so it is
    never written."""
    answer = dict(drv)
    url = catalog_file_url(drv)
    if url is not None:
        answer["download_url"] = url
    return answer


def _text(entry: dict, key: str) -> str:
    """An entry's text field for matching: lowercased, empty when absent or not text."""
    value = entry.get(key)
    return value.lower() if isinstance(value, str) else ""


def _listed(entry: dict, key: str) -> list:
    """An entry's list field, empty when absent or not a list."""
    value = entry.get(key)
    return value if isinstance(value, list) else []


def _id_of(entry: dict) -> str:
    """An entry's id as text, for ordering; empty when it is not text."""
    value = entry.get("id")
    return value if isinstance(value, str) else ""


def _search_text(input: dict, key: str) -> str:
    return (input.get(key) or "").strip().lower()


def _search_limit(value: Any) -> int:
    """At least 1, at most 100; 25 for a value that is not a number."""
    try:
        return max(1, min(int(value), 100))
    except (TypeError, ValueError):
        return 25


def _passes_filters(drv: dict, filters: dict[str, str]) -> bool:
    return all(not wanted or _text(drv, field) == wanted for field, wanted in filters.items())


def _compatible_model_names(drv: dict) -> list[str]:
    return [
        model.lower()
        for entry in _listed(drv, "compatible_models")
        if isinstance(entry, dict)
        for model in _listed(entry, "models")
        if isinstance(model, str)
    ]


@dataclass(frozen=True)
class _Searched:
    """What a search reads of one catalog entry, lowercased: the fields the
    search rule looks for each word in, and the weights that rank a match."""

    id: str
    name: str
    manufacturer: str
    description: str
    tags: tuple[str, ...]
    models: tuple[str, ...]

    @classmethod
    def of(cls, drv: dict) -> _Searched:
        return cls(
            id=_text(drv, "id"),
            name=_text(drv, "name"),
            manufacturer=_text(drv, "manufacturer"),
            description=_text(drv, "description"),
            tags=tuple(t.lower() for t in _listed(drv, "tags") if isinstance(t, str)),
            models=tuple(_compatible_model_names(drv)),
        )

    def fields(self) -> tuple[str, ...]:
        """Every field the search rule looks in, each one the weights rank."""
        return (self.id, self.name, self.manufacturer, self.description, *self.tags, *self.models)

    def score(self, text: str) -> int:
        """How well one piece of text matched, each field with its own weight."""
        hits = (
            (100, text in (self.id, self.manufacturer)),
            (30, text in self.id),
            (30, text in self.manufacturer),
            (20, text in self.name),
            (15, text in self.tags),
            (5, any(text in t for t in self.tags)),
            (25, any(text in m for m in self.models)),
            (3, text in self.description),
        )
        return sum(weight for weight, hit in hits if hit)


def _score_driver_match(drv: dict, query: str) -> int:
    """Rank a driver against a free-text query. Higher score = better match;
    0 is no match, and an empty query matches everything at 1.

    Whether it matches is the search rule's (``utils/search_words``): every
    word typed is somewhere in the entry, each word wherever it is. One word
    scores by its weights; several score each word's own, plus the whole
    query's, so the words written together outrank the same words apart, the
    way Browse Community ranks its cards."""
    words = search_words(query)
    if not words:
        return 1
    searched = _Searched.of(drv)
    if not finds_every_word(words, searched.fields()):
        return 0
    phrase = searched.score(" ".join(words))
    if len(words) == 1:
        return phrase
    return phrase + sum(searched.score(word) for word in words)


def _lean_driver_entry(drv: dict) -> dict:
    """Only the fields needed to pick a driver (``_LEAN_FIELDS``)."""
    return {k: drv[k] for k in _LEAN_FIELDS if k in drv}


def _ranked(drivers: list[dict], query: str, filters: dict[str, str]) -> list[tuple[int, dict]]:
    """The entries that pass ``filters`` and match ``query``, best first, then by id."""
    results: list[tuple[int, dict]] = []
    for drv in drivers:
        if not _passes_filters(drv, filters):
            continue
        score = _score_driver_match(drv, query)
        if score:
            results.append((score, drv))
    results.sort(key=lambda pair: (-pair[0], _id_of(pair[1])))
    return results


def _model_match(drv: dict) -> dict:
    """One driver the device catalog lists for a model, as the AI answers it."""
    return {
        "driver_id": drv.get("id"),
        "confidence": drv.get("confidence", "untested"),
        "notes": drv.get("notes"),
    }


class CatalogToolsMixin:
    """The community catalog tools: search, detail, find by model, install."""

    async def _search_community_drivers(self, input: dict) -> Any:
        """Search the community driver catalog with filters and ranking.

        Inputs (all optional):
          query        - free text; every word must be somewhere in the id,
                         name, manufacturer, description, tags or compatible
                         model names, each word wherever it is
          category     - exact category filter (projector, display, switcher,
                         audio, camera, lighting, video, streaming, power,
                         utility)
          manufacturer - case-insensitive manufacturer match
          transport    - tcp | http | osc | serial | udp
          limit        - cap on result count (default 25, max 100)

        Returns lean entries (omits help and compatible_models — call
        get_community_driver_detail for those). Results are ranked: exact id
        and manufacturer matches first, then name matches, then description /
        tag hits; for several words, the words written together first.
        """
        drivers = await _community_drivers()
        if drivers is None:
            return {"drivers": [], "total": 0, "error": "Failed to fetch community index"}

        query = _search_text(input, "query")
        filters = {field: _search_text(input, field) for field in _SEARCH_FILTERS}
        limit = _search_limit(input.get("limit", 25))

        results = _ranked(drivers, query, filters)
        trimmed = [_lean_driver_entry(_with_download_url(d)) for _, d in results[:limit]]
        return {
            "drivers": trimmed,
            "total": len(results),
            "returned": len(trimmed),
            "truncated": len(results) > len(trimmed),
            "error": None,
        }

    async def _get_community_driver_detail(self, input: dict) -> Any:
        """Return the full community catalog entry for a single driver.

        Includes help.overview, help.setup, compatible_models, and other heavy
        fields stripped from search results. Use this after search to read a
        driver's setup notes or check whether a specific model is supported.
        """
        driver_id = (input.get("driver_id") or "").strip()
        if not driver_id:
            return {"error": "driver_id is required"}

        drivers = await _community_drivers()
        if drivers is None:
            return {"error": "Failed to fetch community index"}

        for drv in drivers:
            if drv.get("id") == driver_id:
                return _with_download_url(drv)
        return {"error": f"Driver '{driver_id}' not found in community catalog"}

    async def _find_driver_for_device(self, input: dict) -> Any:
        """Look up community drivers that control a specific device by exact
        manufacturer + model match using the curated devices.json catalog.

        Use this when the user names specific hardware (e.g. "Sharp NEC
        NP-PA853UL"). Returns matching driver entries with confidence
        ('verified', 'tested', 'untested'). Empty list when no exact match —
        fall back to search_community_drivers in that case.
        """
        manufacturer = (input.get("manufacturer") or "").strip()
        model = (input.get("model") or "").strip()
        if not manufacturer or not model:
            return {"matches": [], "error": "manufacturer and model are required"}

        cache = devices_cache()
        rows = await cache.find_drivers(manufacturer, model)
        if not cache.cached() and cache.last_error:
            # Why is in the cache's log line, never here: the library's text
            # is not for the cloud chat history.
            return {"matches": [], "error": "Failed to fetch the community device catalog"}
        return {
            "manufacturer": manufacturer,
            "model": model,
            "matches": [_model_match(drv) for drv in rows],
            "error": None,
        }

    async def _install_community_driver(self, input: dict) -> Any:
        """Install a community driver through the canonical REST install path.

        Delegating (rather than reimplementing) is the whole point: the shared
        ``/api/drivers/install`` handler also fetches a YAML driver's declared
        Python companion and a Python driver's ``_sim.py`` / ``_discovery.py``
        siblings, refreshes device matches, and promotes any devices that were
        orphaned waiting on this driver. The REST layer raises
        ``HTTPException`` on every failure (bad host, min-version gate,
        id-mismatch guard, download/load errors); the tool layer returns
        ``{"error": ...}`` dicts, so translate at the boundary.
        """
        from fastapi import HTTPException

        from openavc.api.models import CommunityDriverInstallRequest
        from openavc.api.routes.drivers import install_community_driver

        if not input.get("file_url"):
            return {"error": "No file_url provided"}

        try:
            body = CommunityDriverInstallRequest(
                driver_id=input.get("driver_id", ""),
                file_url=input.get("file_url", ""),
                min_platform_version=input.get("min_platform_version"),
            )
        except Exception as e:
            return {"error": f"Invalid install request: {e}"}

        try:
            return await install_community_driver(body)
        except HTTPException as e:
            return {"error": str(e.detail)}
