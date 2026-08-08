"""Regression test for zip-slip in bundled-plugin extraction (audit C3).

`_install_bundled_plugins` extracted each member of a bundle's ``plugins/``
tree by joining the plugin id (the path component after ``plugins/``) and the
per-file relative path straight onto ``plugin_repo`` — no containment check.
A crafted bundle member like ``plugins/foo/../../evil.py`` (or a plugin id of
``..``) therefore wrote files outside ``plugin_repo``. Plugin ``.py`` files are
imported on load, so an escape is arbitrary code execution.

This test feeds a crafted zip to ``_install_bundled_plugins`` and asserts the
traversal members are skipped (nothing written outside the repo) while a
legitimate plugin still installs.

The sibling extraction paths (seed/import scripts + assets, bundled drivers)
already reduce each member to ``Path(name).name`` (a basename), so they can't
escape; this test covers the one site that used a full relative path.
"""

from __future__ import annotations

import io
import zipfile

from openavc.core import project_library


def _make_zip(members: dict[str, bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


def test_install_bundled_plugins_blocks_zip_slip(tmp_path, monkeypatch):
    plugin_repo = tmp_path / "plugin_repo"
    plugin_repo.mkdir()
    monkeypatch.setattr(project_library, "_PLUGIN_REPO_DIR", plugin_repo)

    zf = _make_zip(
        {
            # Malicious: per-file relative path escapes the plugin dir.
            "plugins/goodname/../../escape_rel.py": b"RCE = 1",
            # Malicious: the plugin id itself is a traversal component.
            "plugins/../escape_id.py": b"RCE = 1",
            # Legitimate plugin (control) — including a nested file.
            "plugins/realplugin/__init__.py": b"OK = 1",
            "plugins/realplugin/sub/helper.py": b"HELPER = 1",
        }
    )

    installed = project_library._install_bundled_plugins(zf)

    # Legit plugin installed inside the repo, nested file preserved.
    assert (plugin_repo / "realplugin" / "__init__.py").read_bytes() == b"OK = 1"
    assert (plugin_repo / "realplugin" / "sub" / "helper.py").read_bytes() == b"HELPER = 1"
    assert "realplugin" in installed

    # Nothing escaped the plugin repo (the documented escape targets).
    assert not (tmp_path / "escape_rel.py").exists()
    assert not (tmp_path / "escape_id.py").exists()

    # Belt-and-suspenders: no "escape*" file landed anywhere under tmp_path
    # outside the repo, and none above it.
    strays = [p for p in tmp_path.rglob("escape*.py")]
    assert strays == [], f"files escaped containment: {strays}"
