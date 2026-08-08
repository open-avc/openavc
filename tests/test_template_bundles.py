"""The starter-template .zip bundles match the sources they are built from.

Each template in ``server/templates/`` exists as a loose ``<name>.avc`` plus an
optional ``<name>.scripts/`` directory, and as a ``<name>.zip`` bundle. The
bundle is the copy a fresh install seeds, and it is the only one that carries
the template's drivers.

Two copies of the same thing drift, and these did. The committed bundles fell
behind the loose files beside them and ended up carrying Python whose UTF-8 had
been double-encoded -- a projector's "authentication failed -- check password"
log line reached real installs with garbage where the dash belonged. It never
crashed, because the result is still valid UTF-8 Python, which is why it went
unnoticed for so long.

``scripts/build_templates.py`` is the fix; these tests are what keep it fixed.
They fail if anyone edits a bundle by hand, or edits a loose source and forgets
to rebuild.

The first three tests read nothing outside this repo. The last one compares the
bundled drivers against the community library and skips when it is not checked
out beside us.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from tests import gates

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "server" / "templates"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

# The byte signature of text that was UTF-8 encoded twice. An em dash (U+2014,
# ``e2 80 94``) read back as cp1252 becomes "a-hat, euro, quote"; encoding that
# to UTF-8 again starts with these five bytes. Every mojibake case in the
# corrupted bundles began this way, and it cannot occur in correctly encoded
# text -- U+00E2 is only ever followed by a combining mark, never by U+20AC.
DOUBLE_ENCODED_UTF8 = b"\xc3\xa2\xe2\x82\xac"

BUNDLES = sorted(TEMPLATE_DIR.glob("*.zip"))
BUNDLE_IDS = [p.name for p in BUNDLES]


def _bundle_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {info.filename: zf.read(info) for info in zf.infolist()}


def test_every_template_has_a_bundle() -> None:
    """A loose .avc with no .zip beside it would never reach a fresh install."""
    loose = {p.stem for p in TEMPLATE_DIR.glob("*.avc")}
    bundled = {p.stem for p in BUNDLES}
    assert loose == bundled, (
        f"templates without a bundle: {sorted(loose - bundled)}; "
        f"bundles without a template: {sorted(bundled - loose)}"
    )
    assert BUNDLES, "no starter templates found at all"


@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_bundle_carries_no_double_encoded_text(bundle: Path) -> None:
    """No entry in a committed bundle contains twice-encoded UTF-8."""
    damaged = [
        name
        for name, payload in _bundle_members(bundle).items()
        if DOUBLE_ENCODED_UTF8 in payload
    ]
    assert not damaged, (
        f"{bundle.name} carries double-encoded UTF-8 in: {damaged}. "
        "Rebuild with: python scripts/build_templates.py"
    )


@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_bundle_is_valid_utf8(bundle: Path) -> None:
    """Every text entry decodes as UTF-8, and the project file parses."""
    members = _bundle_members(bundle)
    for name, payload in members.items():
        if name.endswith((".py", ".avc", ".json")):
            payload.decode("utf-8")  # raises UnicodeDecodeError on a bad entry
    json.loads(members["project.avc"].decode("utf-8"))


@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_bundle_matches_its_loose_sources(bundle: Path) -> None:
    """The bundled project and scripts are byte-identical to the loose copies.

    This is the drift half. The bundled driver is checked separately, because
    only that one needs the community library on disk.
    """
    import build_templates

    stem = bundle.stem
    members = _bundle_members(bundle)

    loose_avc = build_templates.read_source(TEMPLATE_DIR / f"{stem}.avc")
    assert members.get("project.avc") == loose_avc, (
        f"{bundle.name}: bundled project.avc differs from {stem}.avc. "
        "Rebuild with: python scripts/build_templates.py"
    )

    scripts_dir = TEMPLATE_DIR / f"{stem}.scripts"
    expected = (
        {f"scripts/{p.name}": build_templates.read_source(p) for p in scripts_dir.glob("*.py")}
        if scripts_dir.is_dir()
        else {}
    )
    bundled = {n: b for n, b in members.items() if n.startswith("scripts/")}
    assert bundled == expected, (
        f"{bundle.name}: bundled scripts differ from {stem}.scripts/. "
        "Rebuild with: python scripts/build_templates.py"
    )


def _corpus_reason() -> str | None:
    """Why the community-library comparison cannot run here, or None."""
    import build_templates

    if not (build_templates.DRIVERS_ROOT / "index.json").is_file():
        return f"no community driver catalog at {build_templates.DRIVERS_ROOT}"
    return None


@gates.skipif_missing(gates.DRIVER_CORPUS, _corpus_reason())
def test_bundles_are_what_the_build_script_produces() -> None:
    """Rebuilding from source reproduces every committed bundle byte for byte.

    The strongest form of the check: it covers the bundled drivers being both
    current with the community catalog and correctly encoded, and it proves the
    committed artifacts really are what the script emits, rather than something
    hand-assembled that happens to look right.
    """
    import build_templates

    catalog = build_templates.catalog_entries()
    builtins = build_templates.builtin_driver_ids()

    stale = []
    for bundle in BUNDLES:
        expected = build_templates.build_bundle(bundle.stem, catalog, builtins)
        if bundle.read_bytes() != expected:
            stale.append(bundle.name)

    assert not stale, (
        f"bundle(s) out of date with their sources: {', '.join(stale)}. "
        "Rebuild with: python scripts/build_templates.py"
    )


@gates.skipif_missing(gates.DRIVER_CORPUS, _corpus_reason())
@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_bundled_drivers_are_the_full_installed_set(bundle: Path) -> None:
    """A template installs the same files a person installing that driver would.

    Templates embed their drivers on purpose -- AV control networks are often
    isolated, so a starter project has to open without reaching the internet.
    The cost of embedding is that the copy can fall behind, so it is pinned to
    the catalog's own file list here, simulator and discovery probe included.
    """
    import build_templates

    catalog = build_templates.catalog_entries()
    builtins = build_templates.builtin_driver_ids()
    project = json.loads((TEMPLATE_DIR / f"{bundle.stem}.avc").read_text(encoding="utf-8"))

    expected = {}
    for driver_id in build_templates.template_drivers(project, builtins):
        for path in build_templates.driver_files(driver_id, catalog):
            expected[f"drivers/{path.name}"] = build_templates.read_source(path)

    bundled = {n: b for n, b in _bundle_members(bundle).items() if n.startswith("drivers/")}
    assert bundled.keys() == expected.keys(), (
        f"{bundle.name}: bundled driver files {sorted(bundled)} do not match the "
        f"catalog's installed set {sorted(expected)}"
    )
    for name in sorted(expected):
        assert bundled[name] == expected[name], (
            f"{bundle.name}: bundled {name} differs from the community library copy. "
            "Rebuild with: python scripts/build_templates.py"
        )


def test_no_template_needs_a_driver_it_does_not_carry() -> None:
    """Every device's driver is either built into the server or in the bundle.

    A template whose device referenced a driver from neither would open with an
    orphaned device and, on an isolated network, no way to repair it.
    """
    builtin_ids = set()
    definitions = REPO_ROOT / "server" / "drivers" / "definitions"
    for path in definitions.glob("*.avcdriver"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("id:"):
                builtin_ids.add(line.split(":", 1)[1].strip())
                break

    for bundle in BUNDLES:
        members = _bundle_members(bundle)
        project = json.loads(members["project.avc"].decode("utf-8"))
        carried = {
            Path(n).stem
            for n in members
            if n.startswith("drivers/") and not n.endswith(("_discovery.py", "_sim.py"))
        }
        for device in project.get("devices", []):
            driver = device.get("driver", "")
            assert driver in builtin_ids or driver in carried, (
                f"{bundle.name}: device '{device.get('id')}' needs driver "
                f"'{driver}', which is neither built in nor bundled"
            )


@gates.skipif_missing(gates.DRIVER_CORPUS, _corpus_reason())
def test_build_script_is_deterministic() -> None:
    """Two builds of the same sources produce identical bytes.

    Without this the zip's timestamps would change on every run, every rebuild
    would show up as a diff, and the drift checks above could not mean anything.
    """
    import build_templates

    catalog = build_templates.catalog_entries()
    builtins = build_templates.builtin_driver_ids()
    stem = BUNDLES[0].stem
    assert build_templates.build_bundle(stem, catalog, builtins) == (
        build_templates.build_bundle(stem, catalog, builtins)
    )


def _load_template_scripts(scripts: dict[str, bytes], workdir: Path) -> tuple[int, dict]:
    """Run a template's scripts through a real ScriptEngine.

    Returns (handlers registered, load errors keyed by script id).
    """
    import sys as _sys

    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.script_engine import ScriptEngine
    from openavc.core.state_store import StateStore

    scripts_dir = workdir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in scripts.items():
        (scripts_dir / name).write_bytes(payload)

    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    engine = ScriptEngine(state, events, DeviceManager(state, events), workdir)
    engine.install()
    try:
        handlers = engine.load_scripts(
            [{"id": Path(n).stem, "file": n, "enabled": True} for n in sorted(scripts)]
        )
        return handlers, engine.get_load_errors()
    finally:
        engine.unload_all()
        _sys.modules.pop("openavc", None)


@pytest.mark.parametrize("bundle", BUNDLES, ids=BUNDLE_IDS)
def test_template_scripts_load_from_bundle_and_loose_copy(bundle: Path, tmp_path: Path) -> None:
    """A template's scripts actually run on this platform, from either source.

    Byte comparison proves the two copies agree; it cannot prove either one
    still works. A script written against an older handler signature stays
    byte-identical in both places and fails at load on the day the signature
    changes -- so the scripts are loaded here for real, from the bundle and
    from the loose directory, and both have to come up clean.
    """
    bundled = {
        Path(n).name: p
        for n, p in _bundle_members(bundle).items()
        if n.startswith("scripts/")
    }
    scripts_dir = TEMPLATE_DIR / f"{bundle.stem}.scripts"
    loose = (
        {p.name: p.read_bytes() for p in scripts_dir.glob("*.py")}
        if scripts_dir.is_dir()
        else {}
    )
    if not bundled and not loose:
        pytest.skip(f"{bundle.stem} ships no scripts")

    bundle_handlers, bundle_errors = _load_template_scripts(bundled, tmp_path / "bundle")
    assert not bundle_errors, f"{bundle.name}: bundled scripts failed to load: {bundle_errors}"

    loose_handlers, loose_errors = _load_template_scripts(loose, tmp_path / "loose")
    assert not loose_errors, f"{bundle.stem}.scripts/: failed to load: {loose_errors}"

    assert bundle_handlers == loose_handlers, (
        f"{bundle.name}: bundled scripts registered {bundle_handlers} handler(s) but the "
        f"loose copies registered {loose_handlers} -- the two have drifted apart"
    )
    assert bundle_handlers > 0, f"{bundle.name}: scripts loaded but registered no handlers"


def test_source_reads_are_platform_independent(tmp_path: Path) -> None:
    """A CRLF working copy contributes the same bytes as an LF one.

    This repo has no .gitattributes, so git hands a Windows checkout CRLF for
    the .avc and .py sources while the bundle stores LF. Comparing raw on-disk
    bytes against a bundle entry therefore passed everywhere except Windows,
    which is exactly how this first went red -- the drift check has to use the
    same reader the build script does, and that reader has to collapse line
    endings.
    """
    import build_templates

    text = 'x = "a — b"\ny = 2\n'
    lf, crlf = tmp_path / "lf.py", tmp_path / "crlf.py"
    lf.write_bytes(text.encode("utf-8"))
    crlf.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    assert build_templates.read_source(crlf) == build_templates.read_source(lf)
    assert b"\r" not in build_templates.read_source(crlf)
    # And the UTF-8 half of the same guarantee: no re-encoding on the way through.
    assert "—".encode("utf-8") in build_templates.read_source(crlf)


def test_check_mode_reports_a_hand_edited_bundle(tmp_path: Path) -> None:
    """The guard actually fires: corrupt a copy, and the comparison rejects it."""
    source = BUNDLES[0]
    tampered = tmp_path / source.name
    members = _bundle_members(source)

    with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members.items():
            if name.endswith(".py"):
                payload = payload.replace(b"#", DOUBLE_ENCODED_UTF8 + b"#", 1)
            zf.writestr(name, payload)

    damaged = [
        n for n, p in _bundle_members(tampered).items() if DOUBLE_ENCODED_UTF8 in p
    ]
    assert damaged, "the mojibake marker check would not have caught the real bug"
