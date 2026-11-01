"""Operating-system programs a transport runs, and getting them onto the host.

The SSH transport (``transport/ssh.py``) runs the host's own OpenSSH client,
because no MIT-compatible Python SSH library exists. Every install type has to
provide that program:

* Windows and macOS include it.
* The Pi image includes it.
* The Linux installer installs it (``installer/install.sh``).
* The Docker image installs it (``Dockerfile``).
* Appliance hardware installs it itself, from the running server
  (:func:`ensure_programs_loop`), because nobody can open a shell on a panel
  to add it, and a panel shipped before the image carried it still needs it.

``TRANSPORT_PROGRAMS`` is the table every one of those is checked against
(``tests/test_install_types_ship_transport_programs.py``): a new transport that
runs an outside program adds a row here, and each install type has to ship it.

``missing_program_sentence`` is what a device card says when the program is
absent, worded for the install type in front of the reader: a panel owner is
never told to install something, and a Linux admin is given the command.
"""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from openavc.utils.logger import get_logger
from openavc.utils.spawn import CREATE_NO_WINDOW

log = get_logger(__name__)


@dataclass(frozen=True)
class OsProgram:
    """An outside program a transport runs, and the package that provides it."""

    command: str  # what is looked up on PATH
    apt: str      # Debian, Ubuntu, Raspberry Pi OS, the appliance
    dnf: str      # Fedora, RHEL, Rocky, Alma
    pacman: str   # Arch


SSH_CLIENT = OsProgram(command="ssh", apt="openssh-client", dnf="openssh-clients", pacman="openssh")

#: transport name -> the program it runs. Every install type ships each one.
TRANSPORT_PROGRAMS: dict[str, OsProgram] = {"ssh": SSH_CLIENT}


@functools.lru_cache(maxsize=1)
def _deployment() -> str:
    try:
        from openavc.updater.platform import detect_deployment_type

        return detect_deployment_type().value
    except Exception:  # never let a wording lookup break a fault classification
        return "unknown"


def missing_program_sentence(program: OsProgram = SSH_CLIENT) -> str:
    """The device card's sentence when ``program`` is not on this host."""
    if program is not SSH_CLIENT:
        return (
            f"The '{program.command}' program this device needs is not installed. "
            "Install it, then press Reconnect."
        )
    deployment = _deployment()
    if deployment == "android_appliance":
        return (
            "This panel does not have the SSH client yet. It installs it by itself "
            "once it can reach the internet, then connects this device."
        )
    if deployment == "docker":
        return (
            "This OpenAVC image has no SSH client. Update to the newest image "
            "(docker compose pull, then docker compose up -d)."
        )
    if sys.platform == "win32":
        return (
            "Windows' OpenSSH Client is not installed. Add it in Settings > System > "
            "Optional features (Windows 10: Settings > Apps > Optional features), "
            "then press Reconnect."
        )
    if sys.platform.startswith("linux"):
        return (
            "This computer has no SSH client. Install it (Debian or Ubuntu: "
            "sudo apt install openssh-client; Fedora or RHEL: sudo dnf install "
            "openssh-clients), then press Reconnect."
        )
    return "No SSH client was found on this computer. Install OpenSSH, then press Reconnect."


# --- Appliance hardware: install what is missing --------------------------

#: First attempt waits for device bring-up to finish; later attempts cover a
#: panel that had no internet when it started.
FIRST_ATTEMPT_DELAY = 60.0
RETRY_INTERVAL = 6 * 3600.0
_STEP_TIMEOUT = 300.0
_APT_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "DEBIAN_FRONTEND": "noninteractive",
    "HOME": "/root",
    "LANG": "C",
}
# apt downloads as its own unprivileged user by default. Where that user may
# not open network sockets the download fails while the same command as root
# succeeds, so a failed attempt is repeated once with downloads run as root.
_APT_AS_ROOT = ("-o", "APT::Sandbox::User=root")

_install_lock = asyncio.Lock()


def can_install_programs() -> bool:
    """True where this server installs missing programs itself: appliance
    hardware, running with the rights to install packages."""
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        return False
    if shutil.which("apt-get", path=_APT_ENV["PATH"]) is None:
        return False
    return _deployment() == "android_appliance"


def missing_programs() -> list[OsProgram]:
    return [p for p in TRANSPORT_PROGRAMS.values() if shutil.which(p.command) is None]


async def _run(argv: list[str]) -> tuple[bool, str]:
    """Run one step, bounded. Never kills a running package install on
    shutdown: an interrupted dpkg is repaired by the next attempt's
    ``dpkg --configure -a``, a killed one mid-unpack is worse."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_APT_ENV,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return False, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), _STEP_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"{argv[0]} took longer than {int(_STEP_TIMEOUT)}s"
    text = (out or b"").decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, text[-600:]


async def _apt_install(packages: list[str], extra: tuple[str, ...]) -> tuple[bool, str]:
    ok, out = await _run(["apt-get", *extra, "update", "-qq"])
    if not ok:
        return False, out
    return await _run(["apt-get", *extra, "install", "-y", "-qq", "--no-install-recommends", *packages])


async def install_missing_programs() -> bool:
    """Install every missing transport program. True when none is missing
    afterwards. Safe to call concurrently; one install runs at a time."""
    async with _install_lock:
        missing = missing_programs()
        if not missing:
            return True
        packages = [p.apt for p in missing]
        log.info("Installing the operating-system packages devices need: %s", ", ".join(packages))
        await _run(["dpkg", "--configure", "-a"])
        ok, out = await _apt_install(packages, ())
        if not ok:
            log.info("Package install failed, retrying with downloads run as root: %s", out)
            ok, out = await _apt_install(packages, _APT_AS_ROOT)
        # The package lists are only needed for this install; drop them so the
        # system's disk use matches a freshly provisioned one.
        await _run(["apt-get", "clean"])
        shutil.rmtree("/var/lib/apt/lists", ignore_errors=True)
        os.makedirs("/var/lib/apt/lists/partial", exist_ok=True)
        still_missing = missing_programs()
        if still_missing:
            log.warning(
                "Could not install %s (needs internet access to the Debian package servers; "
                "tried again in %d hours and at every start): %s",
                ", ".join(p.apt for p in still_missing), int(RETRY_INTERVAL // 3600), out,
            )
            return False
        log.info("Installed %s", ", ".join(packages))
        return True


async def ensure_programs_loop(on_installed: Callable[[], Awaitable[None]]) -> None:
    """Background task: on appliance hardware, install missing transport
    programs, then call ``on_installed`` so devices waiting on them connect.
    Returns at once anywhere else, and as soon as nothing is missing."""
    if not can_install_programs() or not missing_programs():
        return
    await asyncio.sleep(FIRST_ATTEMPT_DELAY)
    while True:
        try:
            if await install_missing_programs():
                await on_installed()
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Installing missing operating-system packages failed")
        await asyncio.sleep(RETRY_INTERVAL)
