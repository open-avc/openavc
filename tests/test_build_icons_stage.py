"""The icon build runs where the Docker image builds the frontend.

The image's frontend stage (``Dockerfile``, stage 1) holds only the web
folders: ``programmer/``, ``simulator/`` and ``panel/`` side by side, with no
Python package around them. ``npm run build`` runs ``scripts/build-icons.mjs``
there, so the script has to finish in that layout; a write into a folder the
stage does not have fails the build, and the image is built only when a release
is cut. This lays the folders out the same way and runs the real script.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tests import gates

WEB = Path(__file__).resolve().parents[1] / "openavc" / "web"
PROGRAMMER = WEB / "programmer"
NODE_MODULES = PROGRAMMER / "node_modules"


def test_build_icons_finishes_with_only_the_web_folders(tmp_path) -> None:
    if shutil.which("node") is None or not (NODE_MODULES / "lucide-static").is_dir():
        gates.skip_or_fail(gates.NODE, "node or lucide-static not installed (run `npm ci` in openavc/web/programmer)")
    stage = tmp_path / "build"
    programmer = stage / "programmer"
    shutil.copytree(PROGRAMMER / "scripts", programmer / "scripts")
    (programmer / "src" / "components" / "ui-builder").mkdir(parents=True)
    (programmer / "node_modules").symlink_to(NODE_MODULES, target_is_directory=True)
    (stage / "panel" / "modules").mkdir(parents=True)

    proc = subprocess.run(
        ["node", "scripts/build-icons.mjs"],
        cwd=programmer, capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    assert (stage / "panel" / "icons.svg").is_file()
    assert (stage / "panel" / "modules" / "icon_aliases.gen.js").is_file()
    assert (programmer / "src" / "components" / "ui-builder" / "spriteIconNames.ts").is_file()
