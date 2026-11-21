"""Check a release tag and say what kind of release it is.

The release workflow's ``version`` job runs it with the tag (``v`` and
``refs/tags/`` already stripped) and appends what it prints to
``$GITHUB_OUTPUT``:

    version=<tag>
    prerelease=true|false

It refuses, with a GitHub ``::error::`` line and exit 1, a tag that:

* is not a version OpenAVC reads (``openavc/utils/version_rule.py``): every
  installed system and the cloud skip such a release, so it would reach no
  one;
* differs from ``pyproject.toml``'s version: the installer and the release's
  file names carry the tag, while the running server reports pyproject's
  version (``version.py`` -> ``/api/health``, the tray, ``system.version``,
  the cloud), so they are spelled the same.

A pre-release is what the version rule says it is: a release candidate,
beta, alpha or development build in either spelling (``0.41.0-rc1`` or
``0.41.0rc1``). It is published as a GitHub pre-release and is not tagged
``latest`` on Docker.

Usage:
    python installer/release-version.py <tag>
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
VERSION_RULE = ROOT / "openavc" / "utils" / "version_rule.py"


def _version_rule():
    """The version rule module, loaded from its file: it is plain stdlib, and
    the job that runs this installs nothing."""
    spec = importlib.util.spec_from_file_location("openavc_version_rule", VERSION_RULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass looks its module up while it is built
    spec.loader.exec_module(module)
    return module


def _pyproject_version() -> str:
    with open(PYPROJECT, "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def release_outputs(tag: str, pyproject_version: str) -> tuple[dict[str, str] | None, str]:
    """``({"version": ..., "prerelease": ...}, "")`` for a tag that may be
    released, else ``(None, the error)``."""
    version = _version_rule().read_version(tag)
    if version is None:
        return None, (
            f"Release tag v{tag} is not a version OpenAVC reads (such as v1.2.3 or "
            f"v1.2.3-rc1), so no installed system would be offered it. Fix the tag and re-release."
        )
    if tag != pyproject_version:
        return None, (
            f"Release tag v{tag} does not match pyproject.toml version {pyproject_version}. "
            f"Bump pyproject.toml to {tag} (or fix the tag) and re-release."
        )
    return {"version": tag, "prerelease": "true" if version.is_prerelease else "false"}, ""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release-version.py <tag>", file=sys.stderr)
        return 2
    outputs, error = release_outputs(argv[1], _pyproject_version())
    if outputs is None:
        print(f"::error::{error}")
        return 1
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
