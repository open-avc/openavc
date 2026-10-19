"""Eligibility checks read inventoried files without executing installed code."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from openavc.drivers.setup_preflight import assess_capture, check_sources
from openavc.utils.spawn import CREATE_NO_WINDOW


@pytest.fixture
def snapshot(tmp_path):
    source = tmp_path / "acme.py"
    marker = tmp_path / "must-not-execute"
    source.write_text(f'raise AssertionError("must not import")\nopen({str(marker)!r}, "w").write("bad")\n')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    mapping = {"schema": "setup-compatibility/v1", "driver_id": "acme", "driver_version": "1.0",
               "source_sha256": digest, "declaration_sha256": "2" * 64, "action_id": "check",
               "action_sha256": "3" * 64, "profile": "boolean-check/v1",
               "roles": {"success": "ok", "message": "message"}, "diagnostics": []}
    resource = {"schema": "setup-compatibility-resource/v1", "source_revision": "1" * 40,
                "descriptors": [mapping], "source_coverage": [{
                    "source_sha256": digest, "driver_id": "acme", "driver_version": "1.0",
                    "declaration_sha256": "2" * 64, "actions": [{
                        "action_id": "check", "action_sha256": "3" * 64, "result_contract": None}]}]}
    file = tmp_path / "resource.json"
    def run(value=None, files=None, expected=None):
        file.write_text(json.dumps(resource if value is None else value))
        sha = hashlib.sha256(file.read_bytes()).hexdigest()
        result = check_sources(files or [source], resource=file, expected_resource_sha256=expected or sha,
                               target_sha256="4" * 64)
        assert not marker.exists()
        return result
    return source, resource, file, run


def test_report_binds_snapshot_target_and_resource_without_import(snapshot):
    source, resource, file, run = snapshot
    before = source.read_bytes()
    result = run()
    assert result["status"] == "passed" and result["activation_authorized"] is False
    assert result["scope"] == "supplied_primary_sources"
    assert result["target_sha256"] == "4" * 64
    assert result["resource_sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()
    assert source.read_bytes() == before
    source.write_bytes(before + b"# edited installation\n")
    refused = run()
    assert refused["status"] == "refused"
    assert refused["sources"][0]["reason"] == "unknown_source"
    assert refused["source_snapshot_sha256"] != result["source_snapshot_sha256"]


def test_mapping_one_action_cannot_qualify_unmapped_second_action(snapshot):
    _, resource, _, run = snapshot
    resource["source_coverage"][0]["actions"].append({
        "action_id": "pair", "action_sha256": "5" * 64, "result_contract": None})
    report = run()
    assert report["status"] == "refused"
    assert report["sources"][0]["coverage"]["unsupported_actions"] == ["pair"]


@pytest.mark.parametrize("change", ["missing_coverage", "duplicate_source", "duplicate_action", "declaration",
                                   "action_hash", "orphan", "resource_hash"])
def test_malformed_or_wrong_target_coverage_refuses(snapshot, change):
    _, original, _, run = snapshot
    resource = copy.deepcopy(original)
    if change == "missing_coverage":
        resource.pop("source_coverage")
    elif change == "duplicate_source":
        resource["source_coverage"].append(copy.deepcopy(resource["source_coverage"][0]))
    elif change == "duplicate_action":
        resource["source_coverage"][0]["actions"] *= 2
    elif change == "declaration":
        resource["source_coverage"][0]["declaration_sha256"] = "6" * 64
    elif change == "action_hash":
        resource["source_coverage"][0]["actions"][0]["action_sha256"] = "6" * 64
    elif change == "orphan":
        resource["source_coverage"][0]["actions"] = []
    with pytest.raises(ValueError):
        run(resource, expected="0" * 64 if change == "resource_hash" else None)


def test_unsupported_explicit_contract_is_not_a_legacy_fallback(snapshot):
    _, resource, _, run = snapshot
    resource["descriptors"] = []
    resource["source_coverage"][0]["actions"][0]["result_contract"] = "setup/v99"
    assert run()["status"] == "refused"


def test_capture_assessment_uses_retained_bytes_and_preserves_occurrences(snapshot, tmp_path):
    from openavc.drivers.setup_inventory import capture_source_inputs
    source, _, resource, run = snapshot
    run()
    sha = hashlib.sha256(resource.read_bytes()).hexdigest()
    captured = capture_source_inputs(primary_roots={"one": tmp_path, "alias": tmp_path},
                                     library_roots={}, bundles={})
    original = source.read_bytes()
    source.write_bytes(b"changed after capture")
    result = assess_capture(captured, resource=resource, expected_resource_sha256=sha,
                            target_sha256="4" * 64)
    assert result["source_eligibility"] == "passed"
    assert len(result["sources"]) == 1
    assert result["capture"]["source_count"] == 2
    assert {row["role"] for row in result["capture"]["members"]
            if row["disposition"] == "primary_python"} == {"one", "alias"}
    assert captured.source_bytes[hashlib.sha256(original).hexdigest()] == original
    assert source.read_bytes() == b"changed after capture"
    assert result["installation_complete"] is False and result["activation_authorized"] is False


@pytest.mark.parametrize("change", ["bytes", "omitted", "report", "count"])
def test_capture_cannot_borrow_different_bytes_or_membership(snapshot, tmp_path, change):
    from openavc.drivers.setup_inventory import capture_source_inputs
    source, _, resource, run = snapshot
    run()
    captured = capture_source_inputs(primary_roots={"root": tmp_path}, library_roots={}, bundles={})
    if change == "bytes":
        captured.source_bytes[hashlib.sha256(source.read_bytes()).hexdigest()] = b"other bytes"
    elif change == "omitted":
        captured.source_bytes.clear()
    elif change == "report":
        captured.report["members"].clear()
    else:
        from openavc.drivers.setup_compatibility import declaration_digest
        captured.report["source_count"] += 1
        captured.report["census_sha256"] = declaration_digest(
            {key: value for key, value in captured.report.items() if key != "census_sha256"})
    with pytest.raises(ValueError):
        assess_capture(captured, resource=resource,
                       expected_resource_sha256=hashlib.sha256(resource.read_bytes()).hexdigest(),
                       target_sha256="4" * 64)


def test_empty_capture_is_observation_not_installation_acceptance(snapshot, tmp_path):
    from openavc.drivers.setup_inventory import capture_source_inputs
    _, _, resource, run = snapshot
    run()
    empty = tmp_path / "empty"
    empty.mkdir()
    captured = capture_source_inputs(primary_roots={"observed": empty}, library_roots={}, bundles={})
    result = assess_capture(captured, resource=resource,
                            expected_resource_sha256=hashlib.sha256(resource.read_bytes()).hexdigest(),
                            target_sha256="4" * 64)
    assert result["source_eligibility"] == "empty"
    assert "status" not in result
    assert result["installation_complete"] is False and result["activation_authorized"] is False


def test_symlink_input_and_parent_and_directory_refuse(snapshot, tmp_path):
    source, _, _, run = snapshot
    link = tmp_path / "linked.py"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symbolic"):
        run(files=[link])
    parent = tmp_path / "parent"
    parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        run(files=[parent / source.name])
    directory = tmp_path / "directory.py"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular"):
        run(files=[directory])


@pytest.mark.parametrize("entry", [("openavc.drivers.setup_preflight",), ("openavc.main", "--setup-preflight")])
def test_literal_cli_checks_without_starting_the_server(snapshot, tmp_path, entry):
    source, _, resource, run = snapshot
    run()
    result = subprocess.run([sys.executable, "-m", *entry, "--resource", str(resource),
                             "--expect-resource", hashlib.sha256(resource.read_bytes()).hexdigest(),
                             "--target-sha256", "4" * 64, "--source", str(source)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                            timeout=10, creationflags=CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["activation_authorized"] is False


@pytest.mark.parametrize("changed_after_read", [False, True])
def test_descriptor_change_time_is_compared_with_the_same_stat_source(tmp_path, monkeypatch, changed_after_read):
    """Windows 3.12 lstat ctime is creation time; fstat ctime is change time."""
    import os
    from types import SimpleNamespace

    from openavc.drivers.setup_preflight import _read_regular

    path = tmp_path / "installed.py"
    path.write_bytes(b"unchanged source")
    real_fstat = os.fstat
    calls = []

    def descriptor_stat(fd):
        value = real_fstat(fd)
        fields = {key: getattr(value, key) for key in (
            "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}
        fields["st_ctime_ns"] += 1000
        if calls and changed_after_read:
            fields["st_ctime_ns"] += 1
        calls.append(True)
        return SimpleNamespace(**fields)

    monkeypatch.setattr(os, "fstat", descriptor_stat)
    if changed_after_read:
        with pytest.raises(ValueError, match="changed during"):
            _read_regular(path)
    else:
        assert _read_regular(path) == b"unchanged source"


@pytest.mark.parametrize('replacement', ['regular', 'fifo'])
def test_opened_descriptor_cannot_borrow_restored_path_identity(tmp_path, monkeypatch, replacement):
    import os
    from openavc.drivers.setup_preflight import _read_regular
    if replacement == 'fifo' and not hasattr(os, 'mkfifo'):
        pytest.skip('POSIX FIFO behavior; regular-file identity is checked on every platform')
    path = tmp_path / 'installed.py'
    path.write_bytes(b'original installed bytes')
    alternate = tmp_path / 'alternate.py'
    if replacement == 'fifo':
        os.mkfifo(alternate)
    else:
        alternate.write_bytes(b'other bytes must not pass')
    saved = tmp_path / 'saved.py'
    real_open = os.open
    swaps = []
    def changed_open(file, flags, *args, **kwargs):
        if Path(file) != path:
            return real_open(file, flags, *args, **kwargs)
        if replacement == 'fifo':
            assert flags & os.O_NONBLOCK
        if sys.platform == 'win32':
            # Windows refuses renaming the open handle (WinError 32). Return
            # the other real descriptor while the original path is unchanged:
            # the identity guard must still reject it before reading bytes.
            swaps.append(True)
            return real_open(alternate, flags, *args, **kwargs)
        path.rename(saved)
        alternate.rename(path)
        try:
            descriptor = real_open(file, flags, *args, **kwargs)
        finally:
            path.rename(alternate)
            saved.rename(path)
        swaps.append(True)
        return descriptor
    monkeypatch.setattr(os, 'open', changed_open)
    with pytest.raises(ValueError, match='changed before'):
        _read_regular(path)
    assert swaps == [True] and path.read_bytes() == b'original installed bytes'
