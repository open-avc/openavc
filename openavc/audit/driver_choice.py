"""Which driver the person chose, and exactly which file will run.

A report has to say what was tested, precisely enough that a later reader can
tell the published driver from a locally edited copy: the driver's id, version
and format; where it came from (the community catalog, a file the person
imported, or one built into OpenAVC); and, for every file beside it on disk,
its SHA-256 against the one the catalog publishes for that file. A difference
is flagged, never hidden: a modified copy is a fair thing to audit, as long as
the report says it was one.

The person's own answers travel with it: the manufacturer and model they say
the device is (and whether the driver lists that model), the firmware they
read off the device, and whether their choice agreed with the verdict the
network check reached before they were asked.
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openavc.drivers.registry import get_driver_class
from openavc.utils.logger import get_logger

log = get_logger(__name__)

SOURCE_CATALOG = "catalog"
SOURCE_IMPORTED = "imported"
SOURCE_BUILT_IN = "built_in"


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def driver_main_file(driver_id: str) -> Path | None:
    """The file that defines ``driver_id``, as the loader would serve it."""
    from openavc.drivers.driver_loader import find_driver_file_by_id
    from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR

    cls = get_driver_class(driver_id)
    if cls is None:
        return None
    # A YAML driver is a class built from a definition; the file is found by
    # its declared id, the last directory winning as it does at load time.
    found = find_driver_file_by_id([DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR], driver_id)
    if found is not None:
        return found
    try:
        source = inspect.getsourcefile(cls)
    except (TypeError, OSError):
        source = None
    return Path(source) if source else None


def driver_files(main: Path) -> list[Path]:
    """The driver file and the companions that travel with it."""
    from openavc.drivers.driver_loader import driver_companions

    return [main, *driver_companions(main)]


def driver_identity(driver_id: str, catalog: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Everything a report says about the driver that ran.

    ``catalog`` is the community index as fetched (``None`` or empty when it
    could not be). File paths stay on the server; the record names files by
    their base name.
    """
    from openavc.drivers.driver_loader import is_builtin_definition_path

    cls = get_driver_class(driver_id)
    info = dict(getattr(cls, "DRIVER_INFO", {}) or {})
    entry = next((d for d in (catalog or []) if d.get("id") == driver_id), None)
    main = driver_main_file(driver_id)
    fmt = "python" if main is not None and main.suffix == ".py" else "avcdriver"

    published = entry.get("files") if isinstance(entry, dict) else None
    published_by_name: dict[str, str] = {}
    if isinstance(published, dict):
        for path, digest in published.items():
            published_by_name[Path(str(path)).name] = str(digest).lower()

    files: list[dict[str, Any]] = []
    for path in driver_files(main) if main is not None else []:
        digest = _sha256(path)
        catalog_digest = published_by_name.get(path.name)
        files.append({
            "name": path.name,
            "sha256": digest,
            "catalog_sha256": catalog_digest,
            "matches_catalog": None if catalog_digest is None else digest == catalog_digest,
        })
    local_names = {f["name"] for f in files}
    missing = sorted(name for name in published_by_name if name not in local_names)

    if main is not None and is_builtin_definition_path(main):
        source = SOURCE_BUILT_IN
    elif entry is not None:
        source = SOURCE_CATALOG
    elif main is not None and main.suffix == ".py" and _within_app(main):
        source = SOURCE_BUILT_IN
    else:
        source = SOURCE_IMPORTED

    modified = any(f["matches_catalog"] is False for f in files)
    catalog_version = str(entry.get("version") or "") if entry else ""
    return {
        "id": driver_id,
        "name": info.get("name") or driver_id,
        "manufacturer": info.get("manufacturer", ""),
        "version": str(info.get("version") or ""),
        "format": fmt,
        "transport": info.get("transport", ""),
        "source": source,
        "files": files,
        "catalog_files_missing": missing,
        "modified": modified,
        "catalog": {
            "listed": entry is not None,
            "version": catalog_version,
            "verified": bool(entry.get("verified")) if entry else None,
            "checked": catalog is not None and len(catalog) > 0,
        },
        "_paths": [str(p) for p in (driver_files(main) if main is not None else [])],
    }


def _within_app(path: Path) -> bool:
    """A Python driver shipped inside OpenAVC itself (not in driver_repo)."""
    from openavc.system_config import DRIVER_REPO_DIR

    try:
        path.resolve().relative_to(Path(DRIVER_REPO_DIR).resolve())
        return False
    except ValueError:
        return True


def compatible_models(driver_id: str, catalog: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The driver's model list: the catalog's, else the driver's own (a YAML
    driver's runtime class does not carry it, so its file is read)."""
    entry = next((d for d in (catalog or []) if d.get("id") == driver_id), None)
    if entry and isinstance(entry.get("compatible_models"), list):
        return list(entry["compatible_models"])
    cls = get_driver_class(driver_id)
    info = getattr(cls, "DRIVER_INFO", {}) or {}
    models = info.get("compatible_models")
    if isinstance(models, list):
        return list(models)
    main = driver_main_file(driver_id)
    if main is not None and main.suffix != ".py":
        import yaml

        try:
            definition = yaml.safe_load(main.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            return []
        models = definition.get("compatible_models") if isinstance(definition, dict) else None
        if isinstance(models, list):
            return list(models)
    return []


def model_listing(
    driver_id: str,
    manufacturer: str,
    model: str,
    catalog: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Whether the driver lists this model for this manufacturer, and at what
    confidence. ``listed`` is None when no model was entered."""
    model_key = model.strip().lower()
    maker_key = manufacturer.strip().lower()
    if not model_key:
        return {"listed": None, "confidence": None}
    for group in compatible_models(driver_id, catalog):
        if maker_key and str(group.get("manufacturer", "")).strip().lower() != maker_key:
            continue
        for listed in group.get("models") or []:
            if str(listed).strip().lower() == model_key:
                return {"listed": True, "confidence": group.get("confidence")}
    return {"listed": False, "confidence": None}


def verdict_agreement(verdict: dict[str, Any] | None, driver_id: str) -> str:
    """How the person's choice relates to what the network check concluded:
    ``agrees`` (it identified this driver), ``candidate`` (one it thought
    might fit), ``differs`` (it identified another), ``no_verdict``."""
    if not verdict:
        return "no_verdict"
    ident = verdict.get("identification") or {}
    identified = ident.get("driver_id")
    if identified == driver_id:
        return "agrees"
    if driver_id in (ident.get("candidates") or []):
        return "candidate"
    if identified:
        return "differs"
    return "no_verdict"


@dataclass
class DriverChoice:
    """What the person chose on the "Which driver?" step."""

    driver_id: str
    manufacturer: str = ""
    model: str = ""
    firmware: str = ""
    identity: dict[str, Any] = field(default_factory=dict)
    model_listing: dict[str, Any] = field(default_factory=dict)
    verdict_agreement: str = "no_verdict"

    def to_dict(self) -> dict[str, Any]:
        identity = {k: v for k, v in self.identity.items() if not k.startswith("_")}
        return {
            "driver_id": self.driver_id,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "firmware": self.firmware,
            "identity": identity,
            "model_listing": dict(self.model_listing),
            "verdict_agreement": self.verdict_agreement,
        }

    @property
    def file_paths(self) -> list[str]:
        """Where the driver's files are on this server (for the report zip)."""
        return list(self.identity.get("_paths") or [])
