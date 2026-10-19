"""Committed historical bytes and actual loaded declarations drive generation."""
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from openavc.drivers import setup_compatibility_gen as generator
from openavc.drivers.setup_compatibility import declaration_digest, parse_descriptors
from openavc.utils.spawn import CREATE_NO_WINDOW
from tests import gates


SOURCE = b'''from openavc.drivers.base import BaseDriver
COUNT = 3
COMMANDS = {"choose": {"params": {"slot": {"type": "integer", "max": COUNT}}}}
class AcmeHistorical(BaseDriver):
    DRIVER_INFO = {"id": "acme_historical", "version": "1.0.0", "name": "Acme Historical",
                   "commands": COMMANDS, "state_variables": {},
                   "actions": [{"id": "check", "kind": "setup", "params": {}}]}
    async def send_command(self, command, params=None):
        raise AssertionError("generation must not execute device commands")
    async def run_setup_action(self, action, params, progress):
        raise AssertionError("generation must not run setup")
'''


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, timeout=30, creationflags=CREATE_NO_WINDOW).stdout.strip()


def commit_manifest(rig, value):
    path = rig.repo / generator.MANIFEST_PATH
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    git(rig.repo, "add", generator.MANIFEST_PATH)
    git(rig.repo, "commit", "-m", "Fixture mapping")
    return git(rig.repo, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path):
    if shutil.which("git") is None:
        gates.skip_or_fail(gates.GIT, "git is unavailable")
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "core.autocrlf", "false")
    path = tmp_path / "acme_historical.py"
    path.write_bytes(SOURCE)
    git(tmp_path, "add", path.name)
    git(tmp_path, "commit", "-m", "Fixture source")
    source_revision = git(tmp_path, "rev-parse", "HEAD")
    manifest = {"schema": "setup-compatibility-input/v1", "sources": [{
        "revision": source_revision, "path": path.name,
        "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
        "driver_id": "acme_historical", "driver_version": "1.0.0", "actions": [{
            "action_id": "check", "profile": "boolean-check/v1", "roles": {
                "success": "ok", "message": "message"}, "diagnostics": []}]}]}
    rig = SimpleNamespace(repo=tmp_path, source=path, manifest=manifest)
    rig.revision = commit_manifest(rig, manifest)
    return rig


def test_actual_computed_declaration_from_historical_blob_ignores_checkout_edits(history):
    history.source.write_bytes(b"invalid current checkout")
    (history.repo / generator.MANIFEST_PATH).write_text("invalid working mapping", encoding="utf-8")
    result = generator.build_resource(history.repo, history.revision)
    descriptor, = parse_descriptors(result["descriptors"])
    assert descriptor.source_sha256 == hashlib.sha256(SOURCE).hexdigest()
    expected = {"id": "acme_historical", "version": "1.0.0", "name": "Acme Historical",
                "commands": {"choose": {"params": {"slot": {"type": "integer", "max": 3}}}},
                "state_variables": {}, "actions": [{"id": "check", "kind": "setup", "params": {}}]}
    assert descriptor.declaration_sha256 == declaration_digest(expected)
    assert descriptor.action_sha256 == declaration_digest(expected["actions"][0])
    assert result["source_revision"] == history.revision


@pytest.mark.parametrize("field,value,reason", [
    ("source_sha256", "0" * 64, "SHA-256"),
    ("driver_id", "acme_other", "identity"),
    ("driver_version", "2.0.0", "identity"),
    ("path", "../acme_historical.py", "relative"),
    ("revision", "main", "forty-character"),
])
def test_mismatched_or_mutable_source_identity_refuses(history, field, value, reason):
    data = copy.deepcopy(history.manifest)
    data["sources"][0][field] = value
    revision = commit_manifest(history, data)
    with pytest.raises(ValueError, match=reason):
        generator.build_resource(history.repo, revision)


def test_conflicting_exact_tuples_cannot_be_generated(history):
    data = copy.deepcopy(history.manifest)
    other = copy.deepcopy(data["sources"][0]["actions"][0])
    other["roles"]["success"] = "different_boolean"
    data["sources"][0]["actions"].append(other)
    revision = commit_manifest(history, data)
    with pytest.raises(ValueError, match="Duplicate"):
        generator.build_resource(history.repo, revision)


def test_literal_cli_generation_and_check_detects_modified_output(history, tmp_path):
    output = tmp_path / "output.json"
    args = ["--drivers", str(history.repo), "--revision", history.revision, "--output", str(output)]
    def command(*extra):
        return subprocess.run([sys.executable, "-m", "openavc.drivers.setup_compatibility_gen", *args, *extra],
                              capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    created = command()
    assert created.returncode == 0, created.stderr
    original = output.read_bytes()
    checked = command("--check")
    assert checked.returncode == 0, checked.stderr
    output.write_bytes(original.replace(b"acme_historical", b"acme_other"))
    altered = command("--check")
    assert altered.returncode != 0 and "differs" in altered.stderr
    assert output.read_bytes() != original  # check never overwrites a bad artifact
