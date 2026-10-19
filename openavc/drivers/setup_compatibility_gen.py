"""Build offline setup compatibility data from maintained historical Git blobs.

This is build tooling for trusted, reviewed driver sources, not a sandbox or a
driver discovery tool. It imports each historical module in a separate process
through the actual platform loader, without constructing a device. The canonical
descriptor parser validates every result. A mutable checkout, partial AST result
or nearby bytecode file cannot supply the selected source/declaration identity.
Per-family behavior and dependency qualification remain required separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

from openavc.drivers.setup_compatibility import (
    SetupCompatibility, declaration_digest, declaration_json, parse_descriptors,
)
from openavc.utils.spawn import CREATE_NO_WINDOW

MANIFEST_PATH = "setup-compatibility/manifest.json"
MAX_SOURCE_BYTES = 4 * 1024 * 1024


def _object(value, required, optional=()):
    if type(value) is not dict or set(value) - set(required) - set(optional) or not set(required) <= value.keys():
        raise ValueError("Unsupported setup generation input")
    return value


def _revision(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Use an exact forty-character Git commit")
    return value


def _path(value):
    if (type(value) is not str or not value or "\\" in value or ":" in value
            or PurePosixPath(value).is_absolute() or any(p in ("", ".", "..") for p in value.split("/"))):
        raise ValueError("Use a repository-relative file path")
    return value


def _git(repo: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True,
                            check=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    return result.stdout


def _blob(repo: Path, revision: str, path: str) -> bytes:
    _revision(revision)
    _path(path)
    actual = _git(repo, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    if actual != revision:
        raise ValueError("The source reference must identify a commit directly")
    row = _git(repo, "ls-tree", "-z", revision, "--", ":(literal)" + path)
    if not row or row.split(b" ", 1)[0] not in (b"100644", b"100755"):
        raise ValueError("The selected source must be a tracked regular file")
    data = _git(repo, "cat-file", "blob", revision + ":" + path)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("The selected source exceeds the generation limit")
    return data


def _capture(path: Path, output: Path) -> None:
    """Child process: use the loader's actual captured bytes and declaration."""
    from openavc.drivers.driver_loader import load_python_driver_file, loaded_python_source

    cls = load_python_driver_file(path)
    receipt = loaded_python_source(cls) if cls is not None else None
    if receipt is None:
        raise ValueError("The historical handler has no qualified loaded-source identity")
    output.write_text(declaration_json({"sha256": receipt.sha256,
                                       "declaration": json.loads(receipt.declaration)}), encoding="utf-8")


def _inspect(source: bytes, path: str) -> dict:
    # A new interpreter prevents a previous driver/module cache from supplying
    # this declaration. The repository and its uncommitted files are not on the
    # module search path. Imports use this exact platform checkout/installation.
    with tempfile.TemporaryDirectory(prefix="openavc-setup-source-") as directory:
        root = Path(directory)
        primary = root / PurePosixPath(path).name
        primary.write_bytes(source)
        output = root / "loaded.json"
        # Loader imports initialize logging/system configuration. They must not
        # read the caller's instance settings or create files in its data tree.
        env = {key: value for key, value in os.environ.items() if not key.startswith("OPENAVC_")}
        env.update(OPENAVC_DATA_DIR=str(root / "data"), OPENAVC_LOG_DIR=str(root / "logs"),
                   OPENAVC_PROJECT=str(root / "project" / "project.avc"), PYTHONNOUSERSITE="1")
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        subprocess.run([sys.executable, "-m", "openavc.drivers.setup_compatibility_gen", "--capture", str(primary), str(output)],
                       cwd=root, env=env, check=True, capture_output=True, timeout=60,
                       creationflags=CREATE_NO_WINDOW)
        return json.loads(output.read_text(encoding="utf-8"))


def build_resource(repo: Path, revision: str, manifest_path: str = MANIFEST_PATH) -> dict:
    """Generate from one committed input manifest, never working-tree bytes."""
    manifest = _object(json.loads(_blob(repo, revision, manifest_path)), ("schema", "sources"))
    if manifest["schema"] != "setup-compatibility-input/v1" or type(manifest["sources"]) is not list:
        raise ValueError("Unsupported setup generation manifest")
    if not 1 <= len(manifest["sources"]) <= 1024:
        raise ValueError("A bounded nonempty source list is required")
    descriptors = []
    for entry in manifest["sources"]:
        _object(entry, ("revision", "path", "source_sha256", "driver_id", "driver_version", "actions"))
        if not _path(entry["path"]).endswith(".py"):
            raise ValueError("Legacy setup qualification requires Python source")
        source = _blob(repo, entry["revision"], entry["path"])
        digest = hashlib.sha256(source).hexdigest()
        if digest != entry["source_sha256"]:
            raise ValueError("Historical source bytes do not match their declared SHA-256")
        captured = _inspect(source, entry["path"])
        info = captured["declaration"]
        if (captured["sha256"] != digest or info.get("id") != entry["driver_id"]
                or info.get("version") != entry["driver_version"]):
            raise ValueError("Loaded source identity does not match the maintained input")
        if type(entry["actions"]) is not list or not 1 <= len(entry["actions"]) <= 64:
            raise ValueError("A bounded nonempty action list is required")
        for mapping in entry["actions"]:
            _object(mapping, ("action_id", "profile", "roles", "diagnostics"), ("generated_secret",))
            matches = [a for a in info.get("actions", []) if type(a) is dict and a.get("id") == mapping["action_id"]]
            if len(matches) != 1 or matches[0].get("kind") != "setup" or "result_contract" in matches[0]:
                raise ValueError("Select one legacy setup action from the loaded declaration")
            descriptor = {"schema": "setup-compatibility/v1", "driver_id": entry["driver_id"],
                          "driver_version": entry["driver_version"], "source_sha256": digest,
                          "declaration_sha256": declaration_digest(info),
                          "action_sha256": declaration_digest(matches[0]), **mapping}
            SetupCompatibility.parse(descriptor)
            descriptors.append(descriptor)
    parse_descriptors(descriptors)  # conflicting or duplicate exact tuples refuse
    descriptors.sort(key=lambda d: (d["driver_id"], d["driver_version"], d["source_sha256"], d["action_id"]))
    return {"schema": "setup-compatibility-resource/v1", "source_revision": revision, "descriptors": descriptors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--drivers", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--capture", nargs=2, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.capture:
        _capture(*args.capture)
        return 0
    if args.drivers is None or args.revision is None or args.output is None:
        parser.error("--drivers, --revision and --output are required")
    rendered = (json.dumps(build_resource(args.drivers.resolve(), args.revision, args.manifest),
                           indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != rendered:
            raise SystemExit("Setup compatibility data differs; regenerate it from the pinned inputs.")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
