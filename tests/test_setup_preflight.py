"""Eligibility checks read inventoried files without executing installed code."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from openavc.drivers.setup_preflight import check_sources
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


def test_literal_cli_checks_without_starting_the_server(snapshot, tmp_path):
    source, _, resource, run = snapshot
    run()
    result = subprocess.run([sys.executable, "-m", "openavc.drivers.setup_preflight", "--resource", str(resource),
                             "--expect-resource", hashlib.sha256(resource.read_bytes()).hexdigest(),
                             "--target-sha256", "4" * 64, "--source", str(source)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                            timeout=10, creationflags=CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["activation_authorized"] is False
