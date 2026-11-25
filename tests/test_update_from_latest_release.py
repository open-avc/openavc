"""The latest release's update scripts, run against what this tree builds.

An update is applied by the script the INSTALLED release carries, not by the
one inside the update. On Linux, the Pi and appliance hardware, the service
runs the installed ``update-helper.sh`` before the server starts, and that
helper hands over to the new release's copy only after it has swapped the code.
On a Mac, the LaunchDaemon runs the wrapper inside the installed bundle, and
that wrapper does the whole swap. So a change in this tree that the released
scripts cannot apply breaks the next update of every installed system, while
every other test, which runs this tree's scripts on this tree's fixtures, stays
green.

These run the latest published release's scripts against the update artifacts
this tree builds, by this tree's own build commands: the Linux archive by
release.yml's ``tar`` command, the macOS update tarball by
``installer/build-macos.sh``, with stand-ins for the frozen programs it would
bundle and for the installer tools it calls. The installed copies are built the
same way from the release's own files. ``systemctl``, ``systemd-run`` and
``launchctl`` are stand-ins that record what they were asked, so nothing on the
machine running the tests is restarted.

Which release: ``OPENAVC_LATEST_RELEASE`` names its tag. The CI test jobs ask
GitHub for the latest release, fetch its tag (their checkout has none) and
promise the check with ``OPENAVC_REQUIRE_LATEST_RELEASE`` (tests/gates.py).
Anywhere else, the newest release tag in the clone stands in.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tarfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from openavc.utils.version_rule import read_version
from tests import gates

REPO_ROOT = Path(__file__).resolve().parents[1]

# Where the installed system's own files point: the unit's paths, and the
# program the LaunchDaemon runs. The sandboxed installs below stand in for them.
LINUX_INSTALL_DIR = "/opt/openavc"
MAC_INSTALLED_APP = "/Applications/OpenAVC.app"
MENU_BAR_JOB = "com.openavc.menubar"
CONSOLE_UID = "501"

# The directories update-helper.sh carries across a swap because the release
# archive never ships them, and the two scripts the installer copies to the top
# of the install (install.sh does the same).
LINUX_KEPT_DIRS = ("venv", "scripts")
LINUX_TOP_LEVEL_COPIES = ("update-helper.sh", "firewall-sync.sh")

# A hang detector, not a performance budget (test_update_system.py says why).
SCRIPT_TIMEOUT = 90

BASH = shutil.which("bash")
OPENSSL = shutil.which("openssl")


def _git_reason() -> str | None:
    if not shutil.which("git"):
        return "git not installed"
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--git-dir"],
        capture_output=True, text=True, check=False,
    )
    return None if proc.returncode == 0 else "not a git checkout"


pytestmark = [
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="these scripts update Linux and macOS installs; Windows updates through its installer",
    ),
    gates.skipif_missing(gates.BASH, None if BASH else "bash not installed"),
    gates.skipif_missing(gates.OPENSSL, None if OPENSSL else "openssl not installed"),
    gates.skipif_missing(gates.GIT, _git_reason()),
]


# --- Which release ----------------------------------------------------------


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, check=check,
    )


def _has_tag(tag: str) -> bool:
    return _git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}", check=False).returncode == 0


def _newest_release_tag_in_clone() -> str | None:
    newest: tuple[str, object] | None = None
    for name in _git("tag", "--list", "v*").stdout.decode().split():
        version = read_version(name)
        if version is None or version.is_prerelease:
            continue
        if newest is None or newest[1] < version:
            newest = (name, version)
    return newest[0] if newest else None


def _latest_release_tag() -> tuple[str | None, str | None]:
    """(tag, None), or (None, why there is none to run)."""
    named = os.environ.get("OPENAVC_LATEST_RELEASE", "").strip()
    if named:
        if not _has_tag(named):
            return None, f"OPENAVC_LATEST_RELEASE names {named}, and this clone has no such tag"
        return named, None
    # In CI the checkout of a release carries the tag being released, which is
    # the newest in the clone, so the stand-in below would test it against
    # itself. A run that promised the check has to name the published one.
    if gates.is_required(gates.LATEST_RELEASE):
        return None, "OPENAVC_LATEST_RELEASE does not name the latest release's tag"
    tag = _newest_release_tag_in_clone()
    if tag is None:
        return None, "this clone has no release tags (git fetch --tags)"
    return tag, None


def _cannot_run_here() -> str | None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # The release's helper writes the system's own unit and helper paths,
        # which no stand-in can redirect; as root it would really write them.
        return "running as root, where the release's update helper would write this machine's service files"
    if Path("/etc/openavc-firmware-host.json").exists():
        return "this machine is a firmware host, where both scripts leave updates to the firmware"
    return None


# --- Building the artifacts -------------------------------------------------


def _tar_command(script: str) -> str:
    """The ``tar`` command in a workflow step's script, continuations kept."""
    lines = script.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("tar ")), None)
    assert start is not None, "no tar command in the archive step -- this parser is stale"
    end = start
    while lines[end].rstrip().endswith("\\"):
        end += 1
    return "\n".join(lines[start:end + 1])


def _linux_archive_command(release_yml: str) -> str:
    """release.yml's command for the Linux archive an in-app update installs.

    The two architectures' archives are one list, so building one builds what
    both carry.
    """
    jobs = yaml.safe_load(release_yml)["jobs"]
    commands = {}
    for job in ("linux-amd64", "linux-arm64"):
        step = next((s for s in jobs[job]["steps"] if s.get("name") == "Create archive"), None)
        assert step is not None, f"release.yml's {job} job has no 'Create archive' step -- this parser is stale"
        commands[job] = _tar_command(step["run"])
    assert commands["linux-amd64"] == commands["linux-arm64"], (
        "the amd64 and arm64 Linux archives no longer pack the same files"
    )
    return commands["linux-amd64"]


def _run_checked(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT, check=False, **kwargs)
    assert proc.returncode == 0, f"{args} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def _packed_paths(command: str) -> list[str]:
    """The paths a ``tar czf "$ARCHIVE" <options> <paths>`` command packs."""
    words = shlex.split(command.replace("\\\n", " "))
    assert words[:3] == ["tar", "czf", "$ARCHIVE"], f"unexpected archive command: {words[:3]} -- this parser is stale"
    return [w for w in words[3:] if not w.startswith("-")]


def _copy_present(source: Path, target: Path) -> None:
    """Copy a file or folder, less what vanished while it was copied.

    Something another test removed between the listing and the copy is not
    part of the tree; anything else that cannot be copied fails the test.
    """
    if not source.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        return
    ignore = shutil.ignore_patterns("__pycache__", "node_modules")
    try:
        shutil.copytree(source, target, symlinks=True, ignore=ignore)
    except shutil.Error as err:
        still_there = [e for e in err.args[0] if os.path.lexists(e[0])]
        if still_there:
            raise shutil.Error(still_there) from err


def _build_linux_archive(tree: Path, out: Path) -> Path:
    """Run ``tree``'s release.yml archive command on a copy of what it packs.

    A copy, because GNU tar fails a folder that gains an entry while it reads
    it, and in a parallel run other workers are writing bytecode caches under
    openavc/. Those caches are excluded from the archive anyway, so the copy
    leaves them out.
    """
    command = _linux_archive_command((tree / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    staging = out.with_suffix(".src")
    for path in _packed_paths(command):
        _copy_present(tree / path, staging / path)
    # COPYFILE_DISABLE: macOS's tar would otherwise add a ._ file for each file
    # carrying extended attributes, which the Linux build never has.
    env = {**os.environ, "ARCHIVE": str(out), "COPYFILE_DISABLE": "1"}
    _run_checked([BASH, "-c", command], cwd=staging, env=env)
    return out


def _write_program(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _build_macos_update_tarball(root: Path, server_says: str, stubs: Path) -> Path:
    """Run ``root``'s own build-macos.sh, with stand-in frozen programs.

    The script freezes the server and the menu bar with PyInstaller unless it
    is handed ready-made ones, and builds a .pkg with Apple's tools; neither is
    what an update installs. Everything it does to lay out the bundle and pack
    the update tarball runs as it does in a release build.
    """
    frozen = root / "frozen-stand-ins"
    _write_program(frozen / "openavc" / "openavc-server", f"echo '{server_says}'\n")
    _write_program(frozen / "openavc-menubar" / "openavc-menubar", "exit 0\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("APPLE_")}
    env.update(
        PATH=f"{stubs}{os.pathsep}{env.get('PATH', '')}",
        PYTHON=sys.executable,
        OPENAVC_PREBUILT_DIST=str(frozen / "openavc"),
        OPENAVC_PREBUILT_MENUBAR=str(frozen / "openavc-menubar"),
    )
    _run_checked([BASH, str(root / "installer" / "build-macos.sh")], cwd=root, env=env)
    tarballs = sorted((root / "dist").glob("openavc-*-macos-*.tar.gz"))
    assert len(tarballs) == 1, f"build-macos.sh left {tarballs} in {root / 'dist'}"
    return tarballs[0]


def _make_stubs(stubs: Path) -> Path:
    """Stand-ins for the service managers and Apple's installer tools.

    Each records its arguments in ``calls.log``. ``launchctl print`` reports the
    menu bar running, so the wrapper's restart of it is exercised.
    """
    log = stubs / "calls.log"
    record = f'echo "$(basename "$0") $*" >> "{log}"\n'
    for name in ("systemctl", "systemd-run"):
        _write_program(stubs / name, record + "exit 0\n")
    _write_program(stubs / "launchctl", record + "[ \"$1\" = print ] && printf '\\tstate = running\\n'\nexit 0\n")
    # pkgbuild --analyze writes the component list build-macos.sh then edits.
    last_arg = 'for last; do :; done\n'
    _write_program(
        stubs / "pkgbuild",
        record + last_arg
        + 'case " $* " in *" --analyze "*) printf \'<plist version="1.0"><array/></plist>\\n\' > "$last" ;;'
        + ' *) : > "$last" ;; esac\n',
    )
    _write_program(stubs / "productbuild", record + last_arg + ': > "$last"\n')
    log.touch()
    return log


def _signing_key(where: Path) -> tuple[Path, Path]:
    """A throwaway EC P-256 key pair: (private key, public key)."""
    where.mkdir(parents=True, exist_ok=True)
    private, public = where / "signing.key", where / "signing.pem"
    _run_checked([OPENSSL, "ecparam", "-genkey", "-name", "prime256v1", "-noout", "-out", str(private)])
    _run_checked([OPENSSL, "ec", "-in", str(private), "-pubout", "-out", str(public)])
    return private, public


def _sign(artifact: Path, private: Path) -> None:
    """``artifact``.sig, as the release workflow signs every artifact."""
    _run_checked([OPENSSL, "dgst", "-sha256", "-sign", str(private), "-out", f"{artifact}.sig", str(artifact)])


def _version_of(tree: Path) -> str:
    return tomllib.loads((tree / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


@dataclass
class Release:
    tag: str
    tree: Path
    linux_archive: Path
    macos_tarball: Path

    @property
    def version(self) -> str:
        return _version_of(self.tree)

    def file(self, path: str) -> bytes:
        return (self.tree / path).read_bytes()


@dataclass
class ThisTree:
    linux_archive: Path
    macos_tarball: Path
    version: str


@pytest.fixture(scope="module")
def stubs(tmp_path_factory) -> Path:
    where = tmp_path_factory.mktemp("stand-ins")
    _make_stubs(where)
    return where


@pytest.fixture(scope="module")
def key(tmp_path_factory) -> tuple[Path, Path]:
    return _signing_key(tmp_path_factory.mktemp("key"))


@pytest.fixture(scope="module")
def released(tmp_path_factory, stubs) -> Release:
    tag, reason = _latest_release_tag()
    gates.skip_or_fail(gates.LATEST_RELEASE, reason or _cannot_run_here())
    tree = tmp_path_factory.mktemp("release") / "tree"
    tree.mkdir()
    archive = _git("archive", "--format=tar", tag).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True, timeout=SCRIPT_TIMEOUT)
    out = tree.parent
    return Release(
        tag=tag,
        tree=tree,
        linux_archive=_build_linux_archive(tree, out / "release-linux.tar.gz"),
        macos_tarball=_build_macos_update_tarball(tree, f"openavc-server {tag}", stubs),
    )


@pytest.fixture(scope="module")
def this_tree(tmp_path_factory, stubs, key, released) -> ThisTree:
    out = tmp_path_factory.mktemp("this-tree")
    linux = _build_linux_archive(REPO_ROOT, out / "linux.tar.gz")
    # build-macos.sh writes its build and dist folders beside the installer/
    # folder it runs from, so it runs from a copy, never in the checkout.
    root = out / "root"
    shutil.copytree(REPO_ROOT / "installer", root / "installer", symlinks=True)
    shutil.copy2(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    macos = _build_macos_update_tarball(root, "openavc-server this tree", stubs)
    for artifact in (linux, macos):
        _sign(artifact, key[0])
    return ThisTree(linux_archive=linux, macos_tarball=macos, version=_version_of(REPO_ROOT))


# --- Running the installed scripts -----------------------------------------


def _script_env(stubs: Path, **extra: str) -> dict[str, str]:
    """The environment a script runs in: nothing of this process's OPENAVC_*
    (a firmware-host or re-exec switch would change what it does), the
    stand-ins first on PATH."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAVC_")}
    env["PATH"] = f"{stubs}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHON"] = sys.executable
    env.update(extra)
    return env


def _run_script(script: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [BASH, str(script), *args],
        capture_output=True, text=True, timeout=SCRIPT_TIMEOUT, env=env, check=False,
    )
    for broken in ("command not found", "syntax error", "unbound variable"):
        assert broken not in proc.stderr, f"{script} reported '{broken}':\n{proc.stderr}"
    return proc


def _files(tree: Path) -> set[str]:
    return {p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file() or p.is_symlink()}


def _archive_files(archive: Path) -> set[str]:
    with tarfile.open(archive) as tar:
        return {m.name.removeprefix("./") for m in tar.getmembers() if m.isfile() or m.issym()}


def _log(stubs: Path) -> str:
    return (stubs / "calls.log").read_text()


def _install_linux(release: Release, app: Path, public_key: Path, pip_log: Path) -> None:
    """An install of the release as the installer leaves it.

    The release's archive, unpacked; the two helpers the unit runs copied to
    the top; a virtual environment (a stand-in whose pip records what it is
    asked, so the dependency sync installs nothing); the image's launchers; and
    a trusted key for this test's signature beside the release's own.
    """
    app.mkdir(parents=True)
    subprocess.run(["tar", "-xzf", str(release.linux_archive), "-C", str(app)], check=True, timeout=SCRIPT_TIMEOUT)
    for name in LINUX_TOP_LEVEL_COPIES:
        shutil.copy2(app / "installer" / name, app / name)
        (app / name).chmod(0o755)
    for name in ("python3", "python"):
        _write_program(app / "venv" / "bin" / name, "exit 0\n")
    _write_program(app / "venv" / "bin" / "pip", f'echo "pip $*" >> "{pip_log}"\nexit 0\n')
    _write_program(app / "scripts" / "panel-kiosk.sh", "exit 0\n")
    shutil.copy2(public_key, app / "installer" / "trusted-keys" / "test-signing.pem")


def _unit_programs(unit: str) -> list[str]:
    """The program each ExecStartPre and ExecStart line of a unit runs."""
    programs = []
    for line in unit.splitlines():
        key, _, value = line.partition("=")
        if key in ("ExecStartPre", "ExecStart") and value:
            programs.append(value.lstrip("-+@:!").split()[0])
    return programs


def _stage_update(data: Path, artifact: Path, from_version: str, to_version: str) -> None:
    """The instruction the server leaves for the script, as the updater writes it."""
    (data / "apply-update.json").write_text(json.dumps(
        {"artifact": str(artifact), "from_version": from_version, "to_version": to_version}
    ))


def test_the_released_update_helper_installs_this_tree_and_can_roll_back(
    tmp_path, released, this_tree, key, stubs,
):
    app, data, pip_log = tmp_path / "opt" / "openavc", tmp_path / "data", tmp_path / "pip.log"
    data.mkdir()
    boot = tmp_path / "boot"
    boot.mkdir()
    _install_linux(released, app, key[1], pip_log)
    installed = _files(app)
    _stage_update(data, this_tree.linux_archive, released.version, this_tree.version)
    env = _script_env(stubs, OPENAVC_BOOT_DIRS=str(boot))

    # What the service runs before the server: the installed copy of the helper.
    applied = _run_script(app / "update-helper.sh", str(data), str(app), env=env)
    output = f"{applied.stdout}\n{applied.stderr}"
    assert applied.returncode == 0, output
    assert f"update to v{this_tree.version} applied" in applied.stdout, output
    assert "signature verified" in applied.stdout, output
    assert not (data / "apply-update.json").exists(), output
    assert not (data / "apply-update.retry").exists(), output

    # Everything the unit runs is in place.
    for program in _unit_programs((REPO_ROOT / "installer" / "openavc.service").read_text(encoding="utf-8")):
        if program.startswith(LINUX_INSTALL_DIR + "/"):
            path = app / program.removeprefix(LINUX_INSTALL_DIR + "/")
            assert os.access(path, os.X_OK), f"the unit runs {program}, and the update left no program there"

    # The installed tree is this tree's archive, with what an update carries
    # across kept, and the release's install whole beside it to roll back to.
    kept = {f for f in _files(app) if f.split("/", 1)[0] in LINUX_KEPT_DIRS or f in LINUX_TOP_LEVEL_COPIES}
    assert _files(app) - kept == _archive_files(this_tree.linux_archive), output
    assert kept == {f for f in installed if f.split("/", 1)[0] in LINUX_KEPT_DIRS or f in LINUX_TOP_LEVEL_COPIES}
    assert _files(app.with_name("openavc.previous")) == installed, output
    assert f"pip install -r {app}/requirements.txt" in pip_log.read_text(), output

    # The release's helper handed over to this tree's, which finished the start.
    assert "re-exec'ing updated helper" in applied.stdout, output
    for name in LINUX_TOP_LEVEL_COPIES:
        assert (app / name).read_bytes() == (REPO_ROOT / "installer" / name).read_bytes(), name

    # A rollback asked for now is applied by the helper this tree installed.
    (data / "apply-rollback").write_text("")
    rolled_back = _run_script(app / "update-helper.sh", str(data), str(app), env=env)
    output = f"{rolled_back.stdout}\n{rolled_back.stderr}"
    assert rolled_back.returncode == 0, output
    assert "rollback complete" in rolled_back.stdout, output
    assert _files(app) == installed, output
    assert (app / "update-helper.sh").read_bytes() == released.file("installer/update-helper.sh")


def test_the_helper_this_tree_installs_applies_the_next_update(
    tmp_path, released, this_tree, key, stubs,
):
    """After the hand-over the helper that runs is this tree's, from where an
    install keeps it, reading what it applies an update with from the
    installed tree, which only the archive put there."""
    app, data, pip_log = tmp_path / "opt" / "openavc", tmp_path / "data", tmp_path / "pip.log"
    data.mkdir()
    _install_linux(released, app, key[1], pip_log)
    env = _script_env(stubs, OPENAVC_BOOT_DIRS=str(tmp_path))
    _stage_update(data, this_tree.linux_archive, released.version, this_tree.version)
    handed_over = _run_script(app / "update-helper.sh", str(data), str(app), env=env)
    assert f"update to v{this_tree.version} applied" in handed_over.stdout, handed_over.stdout
    first = _files(app)

    # The archive carries the release's trusted keys, not this test's.
    shutil.copy2(key[1], app / "installer" / "trusted-keys" / "test-signing.pem")
    _stage_update(data, this_tree.linux_archive, this_tree.version, this_tree.version)
    again = _run_script(app / "update-helper.sh", str(data), str(app), env=env)
    output = f"{again.stdout}\n{again.stderr}"
    assert again.returncode == 0, output
    assert f"update to v{this_tree.version} applied" in again.stdout, output
    assert _files(app.with_name("openavc.previous")) == first | {"installer/trusted-keys/test-signing.pem"}, output


def _launchd_program(plist: bytes) -> str:
    """The program a LaunchDaemon plist runs."""
    return plistlib.loads(plist)["ProgramArguments"][0]


def _in_bundle(app: Path, program: str) -> Path:
    assert program.startswith(MAC_INSTALLED_APP + "/"), f"the LaunchDaemon runs {program}, outside the app"
    return app / program.removeprefix(MAC_INSTALLED_APP + "/")


def _server_line(proc: subprocess.CompletedProcess) -> str:
    """What the server the wrapper started printed: its last line of output."""
    lines = proc.stdout.strip().splitlines()
    return lines[-1] if lines else ""


def test_the_released_macos_wrapper_installs_this_tree_and_can_roll_back(
    tmp_path, released, this_tree, key, stubs,
):
    applications, data = tmp_path / "Applications", tmp_path / "data"
    applications.mkdir()
    data.mkdir()
    subprocess.run(["tar", "-xzf", str(released.macos_tarball), "-C", str(applications)], check=True)
    app = applications / "OpenAVC.app"
    shutil.copy2(key[1], app / "Contents" / "Resources" / "trusted-keys" / "test-signing.pem")
    _stage_update(data, this_tree.macos_tarball, released.version, this_tree.version)
    env = _script_env(
        stubs,
        OPENAVC_APP=str(app),
        OPENAVC_DATA_DIR=str(data),
        OPENAVC_LAUNCHCTL=str(stubs / "launchctl"),
        OPENAVC_CONSOLE_UID=CONSOLE_UID,
    )
    # What the installed LaunchDaemon runs: the release's wrapper, in the bundle.
    installed_program = _launchd_program(released.file("installer/com.openavc.server.plist"))
    wrapper = _in_bundle(app, installed_program)
    assert wrapper.read_bytes() == released.file("installer/openavc-macos-run.sh")

    applied = _run_script(wrapper, env=env)
    output = f"{applied.stdout}\n{applied.stderr}"
    assert applied.returncode == 0, output
    assert f"update to v{this_tree.version} applied" in applied.stdout, output
    assert "signature verified" in applied.stdout, output
    assert not (data / "apply-update.json").exists(), output
    # The release's wrapper started the server this tree built.
    assert _server_line(applied) == "openavc-server this tree", output
    assert f"launchctl kickstart -k gui/{CONSOLE_UID}/{MENU_BAR_JOB}" in _log(stubs), output
    assert (app.with_name("OpenAVC.app.previous") / "Contents" / "Resources" / "server" / "openavc-server").exists()

    # The program the installed LaunchDaemon names is this tree's wrapper, and
    # so is the one this tree's own plist names, for a Mac installed from now on.
    this_trees_program = _launchd_program((REPO_ROOT / "installer" / "com.openavc.server.plist").read_bytes())
    for program in {installed_program, this_trees_program}:
        path = _in_bundle(app, program)
        assert os.access(path, os.X_OK), f"the LaunchDaemon runs {program}, and the update left no program there"
        assert path.read_bytes() == (REPO_ROOT / "installer" / "openavc-macos-run.sh").read_bytes(), program

    # The next start, through this tree's wrapper, with nothing to apply.
    restarted = _run_script(wrapper, env=env)
    assert restarted.returncode == 0, f"{restarted.stdout}\n{restarted.stderr}"
    assert _server_line(restarted) == "openavc-server this tree", restarted.stdout

    # A rollback asked for now is applied by the wrapper this tree installed.
    (data / "apply-rollback").write_text("")
    rolled_back = _run_script(wrapper, env=env)
    output = f"{rolled_back.stdout}\n{rolled_back.stderr}"
    assert rolled_back.returncode == 0, output
    assert "rollback complete" in rolled_back.stdout, output
    assert _server_line(rolled_back) == f"openavc-server {released.tag}", output
    assert wrapper.read_bytes() == released.file("installer/openavc-macos-run.sh")


def test_the_wrapper_this_tree_installs_applies_the_next_update(
    tmp_path, released, this_tree, key, stubs,
):
    """After the swap the wrapper that runs is this tree's, from inside the
    bundle this tree built, reading what it applies an update with from beside
    itself, which only build-macos.sh put there."""
    applications, data = tmp_path / "Applications", tmp_path / "data"
    applications.mkdir()
    data.mkdir()
    subprocess.run(["tar", "-xzf", str(released.macos_tarball), "-C", str(applications)], check=True)
    app = applications / "OpenAVC.app"
    trusted = app / "Contents" / "Resources" / "trusted-keys" / "test-signing.pem"
    shutil.copy2(key[1], trusted)
    env = _script_env(
        stubs,
        OPENAVC_APP=str(app),
        OPENAVC_DATA_DIR=str(data),
        OPENAVC_LAUNCHCTL=str(stubs / "launchctl"),
        OPENAVC_CONSOLE_UID=CONSOLE_UID,
    )
    wrapper = _in_bundle(app, _launchd_program(released.file("installer/com.openavc.server.plist")))
    _stage_update(data, this_tree.macos_tarball, released.version, this_tree.version)
    handed_over = _run_script(wrapper, env=env)
    assert f"update to v{this_tree.version} applied" in handed_over.stdout, handed_over.stdout
    first = _files(app)

    # The tarball carries the release's trusted keys, not this test's.
    shutil.copy2(key[1], trusted)
    _stage_update(data, this_tree.macos_tarball, this_tree.version, this_tree.version)
    again = _run_script(wrapper, env=env)
    output = f"{again.stdout}\n{again.stderr}"
    assert again.returncode == 0, output
    assert f"update to v{this_tree.version} applied" in again.stdout, output
    assert _server_line(again) == "openavc-server this tree", output
    trusted_name = trusted.relative_to(app).as_posix()
    assert _files(app.with_name("OpenAVC.app.previous")) == first | {trusted_name}, output
