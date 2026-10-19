"""Bounded read-only capture of explicitly supplied setup source locations.

This is the membership/snapshot part of admission, not installation discovery.
Deployment owners must derive their complete required roles from real installed
configuration, account for restore destinations and candidate contents, and hold
the relevant writers while rechecking before activation. No report from this
module alone clears an installation, including one with zero Python sources.
Installed Python is never imported. Linked layouts retain their spelling and
observed targets; selected bytes can become regular files in private staging.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import stat
import unicodedata
import zipfile

from openavc.drivers.setup_compatibility import declaration_digest
from openavc.drivers.setup_preflight import _file_identity, _read_regular
from openavc.drivers.source_names import is_driver_file

_MAX_ENTRIES = 10_000
_MAX_SOURCES = 4096
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 250 * 1024 * 1024


@dataclass(frozen=True)
class SourceCapture:
    """Observed membership and immutable bytes, with no activation authority."""

    report: dict
    source_bytes: dict[str, bytes]


def _identity(value):
    return list(_file_identity(value))


def _location(path: Path) -> dict:
    """Observe original aliases and all reachable link targets without hiding errors."""
    path = path.absolute()
    pending, seen, chain = [path], set(), []
    while pending:
        item = pending.pop()
        for part in reversed((item, *item.parents)):
            spelling = str(part)
            if spelling in seen:
                continue
            if len(seen) >= 256:
                raise ValueError("The source path has too many link components.")
            seen.add(spelling)
            value = part.lstat()
            # Ancestor directory membership is not this root's membership.
            # Still bind its object/mode so a replaced parent cannot disappear.
            identity = _identity(value)
            row = {"path": spelling, "identity": identity if part == item else identity[:3]}
            junction = getattr(os.path, "isjunction", lambda _: False)(part)
            if stat.S_ISLNK(value.st_mode) or junction:
                target = os.readlink(part)
                row["link_target"] = target
                pending.append(Path(target) if os.path.isabs(target) else part.parent / target)
            chain.append(row)
    resolved = path.resolve(strict=True)
    return {"path": str(path), "resolved": str(resolved),
            "identity": _identity(resolved.stat()),
            "chain": sorted(chain, key=lambda row: row["path"])}


class _Capture:
    def __init__(self):
        self.observed = {}
        self.directories = {}
        self.members = []
        self.sources = {}
        self.total_bytes = 0
        self.source_count = 0
        self.retained_names = 0

    def names(self, entries):
        # Membership lists occur in root, library and optional-parent records,
        # even when no source/member row is selected. Bound their aggregate
        # retained size too, counting repeated roles without dropping either.
        self.retained_names += len(entries)
        if self.retained_names > _MAX_ENTRIES:
            raise ValueError("The source inventory exceeds its retained membership limit.")
        return [entry.name for entry in entries]

    def observe(self, path):
        value = _location(path)
        previous = self.observed.setdefault(str(path.absolute()), value)
        if previous != value:
            raise ValueError("A source location changed during inventory.")
        return value

    def member(self, row):
        if len(self.members) >= _MAX_ENTRIES:
            raise ValueError("The source inventory exceeds its entry limit.")
        self.members.append(row)

    def source(self, row, data):
        self.source_count += 1
        self.total_bytes += len(data)
        if self.source_count > _MAX_SOURCES or self.total_bytes > _MAX_TOTAL_SOURCE_BYTES:
            raise ValueError("The source inventory exceeds its source limit.")
        digest = hashlib.sha256(data).hexdigest()
        self.sources[digest] = data
        row.update(disposition="primary_python", source_sha256=digest, bytes=len(data))

    def directory(self, path):
        root = self.observe(path)
        if not stat.S_ISDIR(root["identity"][2]):
            raise ValueError("An expected source directory is not a directory.")
        # Scandir propagates permission errors and bounds allocation before
        # materializing a huge directory. No glob/walk error suppression.
        names = []
        with os.scandir(path) as entries:
            for entry in entries:
                if len(names) >= _MAX_ENTRIES:
                    raise ValueError("A source directory exceeds its entry limit.")
                names.append(path / entry.name)
        names.sort(key=lambda item: item.name)
        current = [entry.name for entry in names]
        previous = self.directories.setdefault(str(path.absolute()), current)
        if previous != current:
            raise ValueError("Directory membership changed during inventory.")
        return root, names

    def primary(self, path, role):
        root, entries = self.directory(path)
        for entry in entries:
            observed = self.observe(entry)
            mode = observed["identity"][2]
            row = {"role": role, "path": str(entry.absolute()), "location": observed,
                   "disposition": "outside_primary_selection"}
            if entry.match("*.py") and is_driver_file(entry):
                if not stat.S_ISREG(mode):
                    raise ValueError("A selected Python source is not a regular file.")
                self.source(row, _read_regular(Path(observed["resolved"]),
                                              expected_identity=observed["identity"]))
            self.member(row)
        return {"role": role, "kind": "primary_directory", "location": root,
                "entries": self.names(entries)}

    def archive(self, path, role):
        observed = self.observe(path)
        data = _read_regular(Path(observed["resolved"]), max_bytes=_MAX_ARCHIVE_BYTES,
                             expected_identity=observed["identity"])
        digest = hashlib.sha256(data).hexdigest()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ENTRIES or sum(i.file_size for i in infos) > _MAX_ARCHIVE_BYTES:
                raise ValueError("The project bundle exceeds its member or expanded-byte limit.")
            seen, destinations = set(), set()
            for index, info in enumerate(infos):
                name = info.filename
                if (name != info.orig_filename or name in seen or "\\" in name
                        or "\x00" in name or ":" in name or name.startswith("/")
                        or any(part in (".", "..") for part in name.rstrip("/").split("/"))):
                    raise ValueError("The project bundle has ambiguous member names.")
                seen.add(name)
                if name.startswith("drivers/") and info.is_dir() and Path(name).name.endswith(".py"):
                    raise ValueError("The project bundle has a directory with a Python source name.")
                row = {"role": role, "archive": str(path.absolute()), "archive_sha256": digest,
                       "index": index, "name": name, "bytes": info.file_size,
                       "compressed_bytes": info.compress_size, "compression": info.compress_type,
                       "crc": info.CRC, "disposition": "outside_primary_selection"}
                if name.startswith("drivers/") and not info.is_dir():
                    filename = Path(name).name
                    row["destination_name"] = filename
                    if not filename.startswith("_"):
                        # A portable inventory refuses all case/normalization
                        # aliases rather than inventing a filesystem winner.
                        key = unicodedata.normalize("NFC", filename).casefold()
                        if key in destinations:
                            raise ValueError("The project bundle has ambiguous flattened driver names.")
                        destinations.add(key)
                    if filename.endswith(".py") and is_driver_file(Path(filename)):
                        if info.file_size > _MAX_SOURCE_BYTES:
                            raise ValueError("A bundled Python source exceeds its byte limit.")
                        with archive.open(info) as stream:
                            source = stream.read(_MAX_SOURCE_BYTES + 1)
                        if len(source) > _MAX_SOURCE_BYTES or len(source) != info.file_size:
                            raise ValueError("A bundled Python source has an invalid expanded size.")
                        self.source(row, source)
                self.member(row)
        return {"role": role, "kind": "project_bundle", "location": observed,
                "archive_sha256": digest, "bytes": len(data), "members": len(infos)}

    def library(self, path, role):
        root, entries = self.directory(path)
        bundles = []
        for entry in entries:
            observed = self.observe(entry)
            row = {"role": role, "path": str(entry.absolute()), "location": observed,
                   "disposition": "not_a_library_directory"}
            if stat.S_ISDIR(observed["identity"][2]):
                # Record complete child membership even when no bundle exists.
                _, children = self.directory(entry)
                row.update(disposition="library_directory", entries=self.names(children))
                if "bundle.zip" in row["entries"]:
                    bundles.append(self.archive(entry / "bundle.zip", role))
            self.member(row)
        return {"role": role, "kind": "saved_library", "location": root,
                "entries": self.names(entries), "bundles": bundles}

    def optional_directory(self, path, role, read):
        # lstat distinguishes an absent child from a present dangling link.
        # Observe the required parent even when the child is present, retaining
        # the same complete membership proof across both dispositions.
        parent, entries = self.directory(path.parent)
        parent_entries = self.names(entries)
        try:
            path.lstat()
        except FileNotFoundError:
            if path.name in parent_entries:
                raise ValueError("The optional source directory changed during inventory.") from None
            return {"role": role, "kind": "absent_directory", "path": str(path.absolute()),
                    "parent": parent, "parent_entries": parent_entries}
        return {**read(path, role), "optional": True, "parent": parent,
                "parent_entries": parent_entries}

    def templates(self, path, role):
        root, entries = self.directory(path)
        bundles = []
        for entry in entries:
            observed = self.observe(entry)
            row = {"role": role, "path": str(entry.absolute()), "location": observed,
                   "disposition": "outside_template_selection"}
            # Match the runtime's flat glob selection, including native case
            # semantics. A selected non-file refuses instead of disappearing.
            if entry.match("*.zip"):
                bundles.append(self.archive(entry, role))
                row["disposition"] = "template_bundle"
            self.member(row)
        return {"role": role, "kind": "template_directory", "location": root,
                "entries": self.names(entries), "bundles": bundles}

    def recheck(self):
        for spelling in list(self.directories):
            self.directory(Path(spelling))
        for spelling, previous in self.observed.items():
            if _location(Path(spelling)) != previous:
                raise ValueError("Source membership or location changed during inventory.")


def capture_source_inputs(*, primary_roots: dict[str, Path],
                          library_roots: dict[str, Path], bundles: dict[str, Path],
                          optional_primary_roots: dict[str, Path] | None = None,
                          optional_library_roots: dict[str, Path] | None = None,
                          template_roots: dict[str, Path] | None = None) -> SourceCapture:
    """Capture named existing locations; completeness of installation roles is separate.

Role names associate this capture with an owning adapter's manifest. They do not
define discovery rules or prove the adapter supplied all actual roots. Missing
roots fail. Optional directories require an observed existing parent, and their
absence is retained and rechecked. Template roots select every flat ZIP.
Re-run under the owner's write exclusion before stop, comparing the census hash.
"""
    groups = (primary_roots, library_roots, bundles, optional_primary_roots or {},
              optional_library_roots or {}, template_roots or {})
    roles = [role for group in groups for role in group]
    if (not roles or len(roles) > 64 or len(set(roles)) != len(roles)
            or any(type(role) is not str or not role or len(role) > 100 for role in roles)):
        raise ValueError("Supply a bounded, distinct, nonempty set of named source locations.")
    capture = _Capture()
    roots = []
    for values, read in zip(groups[:3], (capture.primary, capture.library, capture.archive)):
        for role, path in sorted(values.items()):
            roots.append(read(Path(path).absolute(), role))
    for values, read in zip(groups[3:5], (capture.primary, capture.library)):
        for role, path in sorted(values.items()):
            roots.append(capture.optional_directory(Path(path).absolute(), role, read))
    for role, path in sorted(groups[5].items()):
        roots.append(capture.templates(Path(path).absolute(), role))
    capture.recheck()
    body = {"schema": "setup-source-capture/v1", "scope": "supplied_locations",
            "installation_complete": False, "activation_authorized": False,
            "roots": roots, "members": capture.members,
            "source_count": capture.source_count, "source_bytes": capture.total_bytes}
    return SourceCapture({**body, "census_sha256": declaration_digest(body)}, capture.sources)
