"""Run the Windows installer's own [Code] without installing anything.

A test setup script is built from the whole [Code] section of
installer/setup.iss, plus an InitializeSetup that runs the lines a test hands
it and then stops, so setup ends before its wizard or any file copy. Building
from the whole section rather than one procedure copied out also compiles
everything else in it on every run; CI otherwise compiles setup.iss only when
it builds a release.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

SETUP_ISS = Path(__file__).resolve().parents[1] / "installer" / "setup.iss"


def iscc() -> str | None:
    """Inno Setup's command-line compiler, where any of its installers put it."""
    found = shutil.which("iscc")
    if found:
        return found
    candidates = [Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")]
    local = os.environ.get("LOCALAPPDATA")
    if local:  # winget installs it per user
        candidates.append(Path(local) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


def code_harness(
    setup_iss: str, title: str, body: list[str], *,
    variables: Sequence[str] = (), run_as_invoker: bool = False,
) -> str:
    """A setup script holding the installer's [Code] whose InitializeSetup
    declares ``variables``, runs ``body`` and then stops.

    ``run_as_invoker`` builds it with ``PrivilegesRequired=lowest``: setup then
    runs with the token of the process that starts it and never asks for
    elevation, so a test that needs none can run it from an ordinary shell.
    """
    code = setup_iss[setup_iss.index("[Code]"):]
    assert "function InitializeSetup" not in code, (
        "setup.iss now has its own InitializeSetup; give this harness another way in"
    )
    setup = setup_iss[setup_iss.index("[Setup]"):setup_iss.index("[Code]")]
    privileges = (
        "PrivilegesRequired=lowest" if run_as_invoker else next(
            line for line in setup.splitlines() if line.startswith("PrivilegesRequired=")
        )
    )
    arch = [line for line in setup.splitlines() if line.startswith("Architectures")]
    return "\n".join([
        '#define MyAppVersion "0.0.0"',
        "[Setup]",
        f"AppName={title}",
        "AppVersion=0.0.0",
        rf"DefaultDirName={{autopf}}\{title}",
        "Uninstallable=no",
        privileges,
        *arch,
        "",
        code.rstrip(),
        "",
        "function InitializeSetup(): Boolean;",
        *(["var", *(f"  {line}" for line in variables)] if variables else []),
        "begin",
        *(f"  {line}" for line in body),
        "  Result := False;",
        "end;",
        "",
    ])


def build(script: str, out_dir: Path, name: str) -> Path:
    """Compile ``script`` into ``out_dir/name.exe``; fail with ISCC's output."""
    compiler = iscc()
    assert compiler, "Inno Setup (iscc) is not installed"
    source = out_dir / f"{name}.iss"
    source.write_text(script, encoding="utf-8")
    built = subprocess.run(
        [compiler, "/Q", f"/O{out_dir}", f"/F{name}", str(source)],
        capture_output=True, text=True, errors="replace", timeout=300,
    )
    assert built.returncode == 0, (
        f"the installer's [Code] does not compile:\n{built.stdout}\n{built.stderr}"
    )
    return out_dir / f"{name}.exe"


def run(exe: Path) -> None:
    """Start the harness the way the updater starts the installer, and wait."""
    subprocess.run(
        [str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        capture_output=True, timeout=300,
    )
