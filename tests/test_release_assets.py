"""The files a release publishes, held to the workflow that writes them and to every reader.

``tests/fixtures/release_assets.json`` lists one release's files. Two halves
are held to it:

- what the release workflow writes: the names ``installer/setup.iss``,
  ``installer/build-macos.sh`` and the Linux jobs of
  ``.github/workflows/release.yml`` give the files, the upload and download
  steps that carry them to the release job, and that job's own checksum and
  signing steps, run here on stand-in files;
- what reads them: the updater (``openavc/updater/release_files.py``), the
  Linux install script, the Windows rollback's installer cache and the
  update-by-hand instructions.

A renamed file, or a checksum list written another way, fails here before a
release ships it. Change the workflow, the list and every reader together; the
cloud copies the list and holds its own reader of a release to it.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from openavc.updater import release_files
from openavc.updater.checker import ReleaseInfo
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import (
    DeploymentType,
    artifact_kind,
    offline_update_instructions,
    update_instructions,
)
from openavc.updater.rollback import _installer_version
from openavc.utils.version_rule import read_version
from tests import gates

REPO = Path(__file__).resolve().parent.parent
CORPUS = json.loads((REPO / "tests" / "fixtures" / "release_assets.json").read_text(encoding="utf-8"))
VERSION = CORPUS["version"]
BUILT = {row["name"]: row for row in CORPUS["built"]}
PUBLISHED = set(CORPUS["published"])
FINAL_ADDS = set(CORPUS["final_release_adds"])
SUMS_TEXT = "\n".join(CORPUS["sha256sums"]) + "\n"

WORKFLOW = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
JOBS = WORKFLOW["jobs"]
PI_WORKFLOW = (REPO / ".github" / "workflows" / "release-pi.yml").read_text(encoding="utf-8")
SETUP_ISS = (REPO / "installer" / "setup.iss").read_text(encoding="utf-8")
BUILD_MACOS = (REPO / "installer" / "build-macos.sh").read_text(encoding="utf-8")
INSTALL_SCRIPT = REPO / "installer" / "install.sh"

#: Each install that updates from a file, as the corpus names it.
INSTALLS = [
    pytest.param(row["name"], row["kind"], install, id=f"{install['deployment_type']}-{install['machine']}")
    for row in CORPUS["built"] for install in row["installed_by"]
]

_BASH = shutil.which("bash") if sys.platform != "win32" else None
_SHA256SUM = shutil.which("sha256sum")
_OPENSSL = shutil.which("openssl")


def _one(pattern: str, text: str, where: str) -> str:
    """The one match of ``pattern`` in ``text``; a failure naming ``where``
    when it finds none or several, so a name written another way is never
    read as nothing to check."""
    found = re.findall(pattern, text, flags=re.MULTILINE)
    assert len(found) == 1, (
        f"{where} no longer writes its file name the way this test reads it "
        f"({pattern!r} found {found!r}); read it again and update the test"
    )
    return found[0]


def _render(template: str, arch: str = "") -> str:
    """A name template from the build, with the version (and the arch) put in."""
    name = template
    for spelling in ("${{ env.VERSION }}", "${VERSION}", "$VERSION", "{#MyAppVersion}", "${SEMVER}"):
        name = name.replace(spelling, VERSION)
    for spelling in ("${ARCH}", "$ARCH"):
        name = name.replace(spelling, arch)
    assert "$" not in name and "{" not in name, f"{template!r} names something this test does not fill in"
    return name


def _steps(job: str) -> list[dict]:
    return JOBS[job]["steps"]


def _step(job: str, name: str) -> dict:
    found = [step for step in _steps(job) if step.get("name") == name]
    assert len(found) == 1, f"the {job} job has no one step named {name!r}"
    return found[0]


def _uses(job: str, action: str) -> list[dict]:
    return [step for step in _steps(job) if str(step.get("uses", "")).startswith(action)]


def _run_text(job: str) -> str:
    return "\n".join(step.get("run", "") for step in _steps(job))


# --- What the workflow writes ----------------------------------------------


def _macos_arches() -> list[str]:
    arches = re.findall(r'ARCH="([A-Za-z0-9_]+)"', BUILD_MACOS)
    assert arches, "installer/build-macos.sh sets no ARCH this test can read"
    return sorted(set(arches))


def _names_each_job_writes() -> dict[str, set[str]]:
    """The files each build job writes, by job, with the corpus's version."""
    windows = _render(_one(r"^OutputBaseFilename=(\S+)\s*$", SETUP_ISS, "installer/setup.iss")) + ".exe"
    pkg = _one(r'^FINAL_PKG="\$DIST/([^"]+)"$', BUILD_MACOS, "installer/build-macos.sh")
    tarball = _one(r'^TARBALL="\$DIST/([^"]+)"$', BUILD_MACOS, "installer/build-macos.sh")
    written = {
        "windows": {windows},
        "macos": {_render(template, arch) for template in (pkg, tarball) for arch in _macos_arches()},
    }
    for job in ("linux-amd64", "linux-arm64"):
        written[job] = {_render(_one(r'ARCHIVE="([^"]+)"', _run_text(job), f"the {job} job"))}
    return written


def test_the_build_writes_the_listed_files():
    written = set().union(*_names_each_job_writes().values())
    assert written == set(BUILT), "the release workflow writes other files than the corpus lists"


def test_the_windows_signature_check_reads_the_installer_the_build_writes():
    """The release refuses an installer the updater would refuse, by its name."""
    run = _step("windows", "Check the updater accepts the installer")["run"]
    checked = _render(Path(_one(r"installer_signature (.+\.exe)", run, "the windows job")).name)
    assert {checked} == _names_each_job_writes()["windows"]


def test_the_macos_matrix_builds_each_arch_the_build_script_names():
    matrix = JOBS["macos"]["strategy"]["matrix"]["include"]
    assert sorted(entry["arch"] for entry in matrix) == _macos_arches()


def _uploaded_globs(job: str) -> list[str]:
    paths = [line.strip() for step in _uses(job, "actions/upload-artifact")
             for line in step["with"]["path"].splitlines() if line.strip()]
    assert paths, f"the {job} job uploads nothing"
    return paths


@pytest.mark.parametrize("job", ["windows", "macos", "linux-amd64", "linux-arm64"])
def test_each_build_job_uploads_every_file_it_writes(job):
    globs = [Path(path).name for path in _uploaded_globs(job)]
    written = _names_each_job_writes()[job]
    assert all(any(fnmatch.fnmatchcase(name, glob) for glob in globs) for name in written), (job, globs)
    assert all(any(fnmatch.fnmatchcase(name, glob) for name in written) for glob in globs), (job, globs)


def _uploaded_artifacts() -> set[str]:
    names = set()
    for job in ("windows", "macos", "linux-amd64", "linux-arm64"):
        for step in _uses(job, "actions/upload-artifact"):
            name = step["with"]["name"]
            if "${{ matrix.arch }}" in name:
                names |= {name.replace("${{ matrix.arch }}", arch) for arch in _macos_arches()}
            else:
                names.add(name)
    return names


def test_the_release_job_takes_every_uploaded_file():
    downloaded = {step["with"]["name"] for step in _uses("release", "actions/download-artifact")}
    assert downloaded == _uploaded_artifacts()
    assert {step["with"]["path"] for step in _uses("release", "actions/download-artifact")} == {"release/"}
    assert _step("release", "Create GitHub Release")["with"]["files"] == "release/*"


def _missing_release_tools() -> str | None:
    if not _BASH:
        return "bash not available"
    if not _SHA256SUM:
        return "sha256sum not available"
    return None


@gates.skipif_missing(gates.BASH, _missing_release_tools())
@gates.skipif_missing(gates.OPENSSL, None if _OPENSSL else "openssl not available")
def test_the_release_job_publishes_the_listed_files_and_checksums(tmp_path):
    """The release job's own checksum and signing steps, run on stand-in files
    whose bytes are their names, publish exactly the corpus's files, and write
    the checksum list exactly as the corpus spells it."""
    release = tmp_path / "release"
    release.mkdir()
    for name in BUILT:
        (release / name).write_bytes(name.encode())
    key = tmp_path / "signing.pem"
    subprocess.run([_OPENSSL, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key)],
                   check=True, capture_output=True)
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path),
           "RELEASE_SIGNING_KEY": base64.b64encode(key.read_bytes()).decode()}
    for name in ("Generate checksums", "Sign release artifacts"):
        step = _step("release", name)
        assert step["working-directory"] == "release"
        subprocess.run([_BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                       cwd=release, env=env, check=True, capture_output=True, text=True, timeout=60)

    assert {path.name for path in release.iterdir()} == PUBLISHED
    written = (release / release_files.CHECKSUMS_FILE).read_text(encoding="utf-8")
    assert set(written.splitlines()) == set(CORPUS["sha256sums"])


def test_the_listed_checksums_are_of_the_stand_in_files():
    assert {line.split("  ", 1)[1] for line in CORPUS["sha256sums"]} == set(BUILT)
    for line in CORPUS["sha256sums"]:
        digest, name = line.split("  ", 1)
        assert digest == hashlib.sha256(name.encode()).hexdigest(), name


def test_every_file_and_its_checksum_list_are_signed():
    assert CORPUS["checksum_list"] == release_files.CHECKSUMS_FILE
    signed = set(BUILT) | {release_files.CHECKSUMS_FILE}
    assert PUBLISHED == signed | {release_files.signature_name(name) for name in signed}


def test_a_final_release_also_gets_the_listed_pi_image():
    """release-pi.yml adds the Pi image and its own checksum file afterwards;
    the shared checksum list is never rewritten for it."""
    image = _render(_one(r'^\s*PI_IMAGE="([^"]+)"$', PI_WORKFLOW, ".github/workflows/release-pi.yml"))
    upload = _one(r'^\s*gh release upload "\$VERSION" (.+) --clobber$', PI_WORKFLOW, ".github/workflows/release-pi.yml")
    assert upload == '"$PI_IMAGE" "${PI_IMAGE}.sha256"'
    assert {image, f"{image}.sha256"} == FINAL_ADDS
    assert not FINAL_ADDS & PUBLISHED


# --- What reads them -------------------------------------------------------


def _manager(deployment: DeploymentType) -> UpdateManager:
    mgr = UpdateManager.__new__(UpdateManager)
    mgr._deployment_type = deployment
    mgr._state = MagicMock()
    return mgr


def _release() -> ReleaseInfo:
    return ReleaseInfo(
        version=VERSION, tag=f"v{VERSION}", prerelease=True, changelog="", published_at="",
        assets=[{"name": name, "url": f"https://example.invalid/{name}"} for name in sorted(PUBLISHED | FINAL_ADDS)],
    )


@pytest.mark.parametrize("name,kind,install", INSTALLS)
def test_each_install_type_downloads_its_listed_file(name, kind, install):
    deployment = DeploymentType(install["deployment_type"])
    assert artifact_kind(deployment) == kind
    assert release_files.artifact_name(VERSION, deployment, install["machine"]) == name
    with patch("platform.machine", return_value=install["machine"]):
        assert _manager(deployment)._get_artifact_name(_release()) == name


def test_every_install_type_that_downloads_a_file_is_listed():
    listed = {install.values[2]["deployment_type"] for install in INSTALLS}
    assert listed == {d.value for d in DeploymentType if artifact_kind(d) != "none"}


def test_no_install_downloads_a_file_listed_as_installed_by_none():
    for name, row in BUILT.items():
        assert (row["kind"] is None) == (not row["installed_by"]), name


@pytest.mark.parametrize("name,kind,install", INSTALLS)
def test_the_updater_finds_its_file_the_checksum_list_and_the_signature(name, kind, install):
    mgr = _manager(DeploymentType(install["deployment_type"]))
    release = _release()
    for wanted in (name, release_files.CHECKSUMS_FILE, release_files.signature_name(name)):
        assert mgr._find_asset_url(release, wanted) == f"https://example.invalid/{wanted}"


def test_the_updater_reads_each_files_checksum():
    for name in BUILT:
        assert release_files.listed_checksum(SUMS_TEXT, name) == hashlib.sha256(name.encode()).hexdigest()
    for name in (PUBLISHED | FINAL_ADDS) - set(BUILT):
        assert release_files.listed_checksum(SUMS_TEXT, name) == ""


def test_the_updater_reads_a_binary_mode_line_and_an_upper_case_hash():
    name = next(iter(BUILT))
    digest = hashlib.sha256(name.encode()).hexdigest()
    assert release_files.listed_checksum(f"{digest.upper()} *{name}\n", name) == digest


def test_the_windows_rollback_reads_the_installers_version():
    installers = [name for name, row in BUILT.items() if row["kind"] == "installer"]
    assert installers
    for name in installers:
        assert _installer_version(Path(name)) == read_version(VERSION)


def _named_files(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9<>._-]+\.(?:exe|pkg|tar\.gz)\b", text)


@pytest.mark.parametrize("deployment", [d for d in DeploymentType if artifact_kind(d) != "none"])
def test_the_update_by_hand_instructions_name_published_files(deployment):
    texts = [update_instructions(deployment, VERSION), offline_update_instructions(deployment)]
    for named in [name for text in texts for name in _named_files(text)]:
        pattern = re.escape(named).replace(re.escape("<arch>"), "[A-Za-z0-9_]+")
        pattern = pattern.replace(re.escape("<version>"), re.escape(VERSION))
        assert any(re.fullmatch(pattern, name) for name in PUBLISHED), (deployment, named)


# --- The Linux install script ---------------------------------------------


def _install_script_lib(tmp_path: Path) -> Path:
    """install.sh without its final ``main "$@"``, so sourcing only defines things.

    Written to a file rather than piped: bash 3.2 (macOS) drops most of a
    ``source <(...)`` FIFO and reports success."""
    body = INSTALL_SCRIPT.read_text(encoding="utf-8").rstrip("\n").rsplit("\n", 1)
    assert body[1] == 'main "$@"', "install.sh no longer ends with its main call"
    lib = tmp_path / "install_lib.sh"
    lib.write_text(body[0] + "\n", encoding="utf-8")
    return lib


def _github_release_json() -> str:
    """The release as GitHub's API answers for it (the fields the script reads)."""
    base = f"https://github.com/open-avc/openavc/releases/download/v{VERSION}"
    return json.dumps({
        "tag_name": f"v{VERSION}",
        "assets": [{"name": name, "browser_download_url": f"{base}/{name}"}
                   for name in sorted(PUBLISHED | FINAL_ADDS)],
    }, indent=2)


_LINUX_INSTALLS = [install for install in INSTALLS if install.values[2]["deployment_type"] == "linux_package"]


def _missing_linux_shell() -> str | None:
    """Why the script's own reading of the release cannot run here: it reads
    GitHub's answer with GNU sed (``\\s``), as on every Linux it installs on."""
    if not _BASH:
        return "bash not available"
    try:
        gnu = subprocess.run(["sed", "--version"], capture_output=True, text=True, timeout=10)
    except OSError:
        return "sed not available"
    return None if gnu.returncode == 0 and "GNU" in gnu.stdout else "GNU sed not available"


@gates.skipif_missing(gates.BASH, _missing_linux_shell())
@pytest.mark.parametrize("name,kind,install", _LINUX_INSTALLS)
def test_the_install_script_finds_its_file_and_the_checksum_list(tmp_path, name, kind, install):
    lib = _install_script_lib(tmp_path)
    answer = tmp_path / "release.json"
    answer.write_text(_github_release_json(), encoding="utf-8")
    script = f"""
        set -euo pipefail
        source {lib}
        uname() {{ echo {install['machine']}; }}
        curl() {{ cat {answer}; }}
        DOWNLOADER=curl
        detect_arch >/dev/null
        get_latest_release_url >/dev/null
        echo "$RELEASE_VERSION"
        echo "$RELEASE_URL"
        echo "$CHECKSUMS_URL"
        echo "$CHECKSUMS_SIG_URL"
    """
    run = subprocess.run([_BASH, "-c", script], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    version, archive, sums, sums_sig = run.stdout.splitlines()
    assert version == VERSION
    assert archive.rsplit("/", 1)[1] == name
    assert sums.rsplit("/", 1)[1] == release_files.CHECKSUMS_FILE
    assert sums_sig.rsplit("/", 1)[1] == release_files.signature_name(release_files.CHECKSUMS_FILE)


@gates.skipif_missing(gates.BASH, None if _BASH else "bash not available")
@pytest.mark.parametrize("name,kind,install", _LINUX_INSTALLS)
def test_the_install_script_reads_its_files_checksum(tmp_path, name, kind, install):
    awk = _one(r"""awk -v name="\$artifact_name" '([^']+)' "\$sums_file\"""",
               INSTALL_SCRIPT.read_text(encoding="utf-8"), "installer/install.sh")
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(SUMS_TEXT, encoding="utf-8")
    run = subprocess.run(["awk", "-v", f"name={name}", awk, str(sums)],
                         capture_output=True, text=True, timeout=30, check=True)
    assert run.stdout.strip() == hashlib.sha256(name.encode()).hexdigest()
