"""THE names of the files a release publishes, as an installed system reads them.

The release workflow (``.github/workflows/release.yml``, with
``installer/setup.iss`` and ``installer/build-macos.sh``) writes each file and
its name; this module is how the updater finds the one it installs and that
file's line in the release's checksum list:

- ``artifact_name``: the file an install type downloads, for its machine;
- ``CHECKSUMS_FILE`` and ``listed_checksum``: the release's ``SHA256SUMS.txt``,
  written by ``sha256sum``, and the hash it gives one file;
- ``signature_name``: the detached signature published beside every file.

``tests/fixtures/release_assets.json`` lists what one release publishes, and
``tests/test_release_assets.py`` holds the workflow's names, this module, the
Linux install script and the Windows rollback's installer cache to it. The
cloud copies that list and holds its own reader of the release to it, so a
renamed file fails a test on both sides rather than an update in the field.
"""

from __future__ import annotations

from openavc.updater.platform import DeploymentType, artifact_kind

#: The checksum list every release publishes beside its files.
CHECKSUMS_FILE = "SHA256SUMS.txt"

_MACOS_ARCH = {"aarch64": "arm64", "arm64": "arm64"}
_LINUX_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def artifact_name(version: str, deployment_type: DeploymentType, machine: str) -> str:
    """The release file this install type updates from, on this machine
    (``platform.machine()``). ``version`` is the release's tag without its
    ``v``, which is how the workflow spells it in every name."""
    machine = machine.lower()
    if artifact_kind(deployment_type) == "installer":
        return f"OpenAVC-Setup-{version}.exe"
    # A Mac updates from an archive of the app; the .pkg is a first install only.
    if deployment_type == DeploymentType.MACOS_APP:
        return f"openavc-{version}-macos-{_MACOS_ARCH.get(machine, 'x86_64')}.tar.gz"
    return f"openavc-{version}-linux-{_LINUX_ARCH.get(machine, machine)}.tar.gz"


def signature_name(file_name: str) -> str:
    """The detached signature the release publishes beside ``file_name``."""
    return f"{file_name}.sig"


def listed_checksum(sums: str, file_name: str) -> str:
    """The SHA-256 the release's checksum list gives ``file_name``, in lower
    case; "" when it lists none. Each line is what ``sha256sum`` writes: the
    hash, then the name, which a ``*`` starts in binary mode."""
    for line in sums.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].strip("*") == file_name:
            return parts[0].lower()
    return ""
