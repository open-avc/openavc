"""Read-only setup eligibility for explicitly inventoried installed Python sources.

The installer supplies the complete primary-source list and the trusted target
and resource hashes. This leaf imports no drivers and grants no activation: the
installation owner must also qualify dependencies/clients, discover every active
and retained source, and recheck the snapshot before replacement. A source match
alone never proves coverage of all its setup actions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from openavc.drivers.setup_compatibility import declaration_digest, parse_descriptors

_MAX_BYTES = 4 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _digest(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ValueError("Supply an exact SHA-256 from the qualified target.")
    return value


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("The setup source coverage record is incomplete or unsupported.")


def _text(value):
    if type(value) is not str or not value or len(value) > 160:
        raise ValueError("The setup source coverage identity is invalid.")
    return value


def _file_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _read_regular(path: Path, *, max_bytes: int = _MAX_BYTES,
                  expected_identity: list | tuple | None = None) -> bytes:
    # Refuse links in every component, including the final source. Capture and
    # recheck file identity; the caller must still recheck the whole snapshot at
    # cutover. This is a read-only check, not a filesystem transaction or sandbox.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Use a protected regular-file snapshot without symbolic links.")
    def common_identity(value):
        # Windows pathname stat and fstat do not expose identical ctime/mode
        # semantics. Compare their shared object facts, then retain each full
        # snapshot for the same-method mutation checks below.
        return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode),
                value.st_size, value.st_mtime_ns)

    if type(max_bytes) is not int or not 0 < max_bytes <= 250 * 1024 * 1024:
        raise ValueError("The file inspection bound is invalid.")
    before = path.lstat()
    if expected_identity is not None and _file_identity(before) != tuple(expected_identity):
        raise ValueError("The source differs from its captured location identity.")
    if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
        raise ValueError("The supplied source is not a bounded regular file.")
    # Nonblocking prevents a regular-to-pipe swap parking an installer; fstat
    # checks the opened object before any read. O_NOFOLLOW is unavailable on
    # Windows, where descriptor/path identity still must agree. Installation
    # adapters remain responsible for protected roots and the final recheck.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or common_identity(opened) != common_identity(before):
            raise ValueError("The supplied source changed before inspection; capture it again.")
        data = stream.read(max_bytes + 1)
        if (len(data) > max_bytes or _file_identity(os.fstat(stream.fileno())) != _file_identity(opened)
                or _file_identity(path.lstat()) != _file_identity(before)):
            raise ValueError("The supplied source changed during inspection; capture it again.")
    return data


def _coverage(resource):
    _object(resource, ("schema", "source_revision", "source_coverage", "descriptors"))
    if (resource["schema"] != "setup-compatibility-resource/v1"
            or type(resource["source_revision"]) is not str
            or not re.fullmatch(r"[0-9a-f]{40}", resource["source_revision"])):
        raise ValueError("The target setup resource has an unsupported identity.")
    descriptors = parse_descriptors(resource["descriptors"])
    rows = resource["source_coverage"]
    if type(rows) is not list or not 1 <= len(rows) <= 1024:
        raise ValueError("The target must include complete setup source coverage.")
    covered, used = {}, set()
    for row in rows:
        _object(row, ("source_sha256", "driver_id", "driver_version", "declaration_sha256", "actions"))
        source = _digest(row["source_sha256"])
        _digest(row["declaration_sha256"])
        _text(row["driver_id"])
        _text(row["driver_version"])
        if source in covered:
            raise ValueError("The target repeats a setup source coverage record.")
        if type(row["actions"]) is not list or len(row["actions"]) > 64:
            raise ValueError("The target setup action coverage is invalid.")
        seen, unsupported = set(), []
        for action in row["actions"]:
            _object(action, ("action_id", "action_sha256", "result_contract"))
            identity = _text(action["action_id"])
            _digest(action["action_sha256"])
            if identity in seen:
                raise ValueError("The target repeats a setup action coverage record.")
            seen.add(identity)
            matches = [d for d in descriptors if d.source_sha256 == source
                       and d.driver_id == row["driver_id"] and d.driver_version == row["driver_version"]
                       and d.declaration_sha256 == row["declaration_sha256"]
                       and d.action_id == identity and d.action_sha256 == action["action_sha256"]]
            if action["result_contract"] is None and len(matches) == 1:
                used.add(matches[0].descriptor_sha256)
            elif action["result_contract"] != "setup/v1":
                unsupported.append(identity)
        covered[source] = {"driver_id": row["driver_id"], "driver_version": row["driver_version"],
                           "actions": len(seen), "unsupported_actions": unsupported}
    if used != {d.descriptor_sha256 for d in descriptors}:
        raise ValueError("A setup mapping does not match its complete source coverage.")
    return covered


def _target_coverage(resource, expected_resource_sha256, target_sha256):
    _digest(target_sha256)
    _digest(expected_resource_sha256)
    data = _read_regular(resource.absolute())
    if hashlib.sha256(data).hexdigest() != expected_resource_sha256:
        raise ValueError("The setup resource differs from the qualified target.")
    return _coverage(json.loads(data))


def _source_result(digest, covered):
    coverage = covered.get(digest)
    reason = ("unknown_source" if coverage is None else
              "unsupported_actions" if coverage["unsupported_actions"] else None)
    return {"source_sha256": digest, "status": "refused" if reason else "passed",
            "reason": reason, "coverage": coverage}


def assess_capture(capture, *, resource: Path, expected_resource_sha256: str,
                   target_sha256: str) -> dict:
    """Assess retained primary bytes without reopening or importing installed sources.

    Empty means no primary bytes in these observed locations, never a passing
    installation. A deployment adapter still owns its required-role census and
    actual launch association. This function supplies no activation authority.
    """
    from openavc.drivers.setup_inventory import (
        SourceCapture, _MAX_ENTRIES, _MAX_SOURCES, _MAX_TOTAL_SOURCE_BYTES,
    )

    if type(capture) is not SourceCapture:
        raise ValueError("Supply a canonical source capture.")
    # These are detached views of the capture owner's immutable observations,
    # not a caller's rehashed JSON report or mutable retained-byte dictionary.
    report, sources = capture.report, capture.source_bytes
    _object(report, ("schema", "scope", "installation_complete", "activation_authorized",
                     "roots", "members", "source_count", "source_bytes", "census_sha256"))
    census = report.pop("census_sha256")
    if (report["schema"] != "setup-source-capture/v1" or report["scope"] != "supplied_locations"
            or report["installation_complete"] is not False or report["activation_authorized"] is not False
            or census != declaration_digest(report)
            or type(report["roots"]) is not list or not 1 <= len(report["roots"]) <= 64
            or type(report["members"]) is not list or len(report["members"]) > _MAX_ENTRIES
            or any(type(row) is not dict for row in report["members"])):
        raise ValueError("The source capture is incomplete or changed.")
    selected = [row for row in report["members"] if row.get("disposition") == "primary_python"]
    if (len(selected) > _MAX_SOURCES or type(report["source_count"]) is not int
            or report["source_count"] != len(selected)
            or any(type(data) is not bytes or len(data) > _MAX_BYTES
                   or hashlib.sha256(data).hexdigest() != digest for digest, data in sources.items())
            or set(sources) != {row.get("source_sha256") for row in selected}
            or any(row.get("bytes") != len(sources[row["source_sha256"]]) for row in selected)
            or type(report["source_bytes"]) is not int
            or report["source_bytes"] != sum(len(sources[row["source_sha256"]]) for row in selected)
            or report["source_bytes"] > _MAX_TOTAL_SOURCE_BYTES):
        raise ValueError("The retained primary bytes differ from their source capture.")
    covered = _target_coverage(resource, expected_resource_sha256, target_sha256)
    rows = [_source_result(digest, covered) for digest in sorted(sources)]
    eligibility = ("empty" if not rows else
                   "passed" if all(row["status"] == "passed" for row in rows) else "refused")
    return {"schema": "setup-capture-assessment/v1", "scope": "captured_primary_sources",
            "source_eligibility": eligibility, "installation_complete": False,
            "activation_authorized": False, "target_sha256": target_sha256,
            "resource_sha256": expected_resource_sha256, "census_sha256": census,
            "capture": {**report, "census_sha256": census}, "sources": rows}


def check_sources(sources: list[Path], *, resource: Path, expected_resource_sha256: str,
                  target_sha256: str) -> dict:
    """Return a hash-bound report for the supplied primary-source snapshot only."""
    covered = _target_coverage(resource, expected_resource_sha256, target_sha256)
    if not sources or len(sources) > 4096:
        raise ValueError("Supply a bounded nonempty list of installed primary Python sources.")
    rows, seen = [], set()
    for source in sources:
        path = source.absolute()
        if path.suffix != ".py" or str(path) in seen:
            raise ValueError("Supply distinct primary Python source paths.")
        seen.add(str(path))
        digest = hashlib.sha256(_read_regular(path)).hexdigest()
        rows.append({"path": str(path), **_source_result(digest, covered)})
    rows.sort(key=lambda row: row["path"])
    return {"schema": "setup-source-preflight/v1", "status": "passed" if all(row["status"] == "passed" for row in rows) else "refused",
            "scope": "supplied_primary_sources", "activation_authorized": False,
            "target_sha256": target_sha256, "resource_sha256": expected_resource_sha256,
            "source_snapshot_sha256": declaration_digest(rows), "sources": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resource", type=Path, default=Path(__file__).with_name("setup-compatibility.json"))
    parser.add_argument("--expect-resource", required=True)
    parser.add_argument("--target-sha256", required=True)
    parser.add_argument("--source", required=True, type=Path, action="append")
    args = parser.parse_args(argv)
    try:
        result = check_sources(args.source, resource=args.resource,
                               expected_resource_sha256=args.expect_resource, target_sha256=args.target_sha256)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"status": "refused", "activation_authorized": False, "detail": str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
