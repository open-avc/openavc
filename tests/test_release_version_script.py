"""The release workflow decides what a tag is by the version rule.

``installer/release-version.py`` is what the ``version`` job of
``.github/workflows/release.yml`` runs: it refuses a tag that is no version or
that differs from ``pyproject.toml``, and says whether the release is a
pre-release (a GitHub pre-release, no Docker ``latest``). A pre-release used
to be "the tag has a hyphen", so ``0.41.0rc1`` or ``0.41.0.dev1`` would have
been published as the newest stable release. Every row of the version rule's
corpus is run here.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "installer" / "release-version.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "version_rule_cases.json").read_text(encoding="utf-8")
)

ROWS = [
    (text, cls["prerelease"])
    for cls in CORPUS["ascending"] for text in cls["versions"]
]
UNREADABLE = [text for text in CORPUS["unreadable"] if isinstance(text, str)]


def _script():
    spec = importlib.util.spec_from_file_location("release_version_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_outputs = _script().release_outputs


@pytest.mark.parametrize("tag,prerelease", ROWS, ids=[repr(t) for t, _ in ROWS])
def test_a_tag_is_a_prerelease_when_the_rule_says_so(tag, prerelease):
    outputs, error = release_outputs(tag, tag)
    assert error == ""
    assert outputs == {"version": tag, "prerelease": "true" if prerelease else "false"}


@pytest.mark.parametrize("tag", UNREADABLE[:40], ids=[repr(t) for t in UNREADABLE[:40]])
def test_a_tag_that_is_no_version_is_refused(tag):
    outputs, error = release_outputs(tag, tag)
    assert outputs is None
    assert "is not a version OpenAVC reads" in error


def test_pythons_spelling_of_a_release_candidate_is_a_prerelease():
    assert release_outputs("0.41.0rc1", "0.41.0rc1")[0]["prerelease"] == "true"
    assert release_outputs("0.41.0.dev1", "0.41.0.dev1")[0]["prerelease"] == "true"


def test_a_tag_spelled_differently_from_pyproject_is_refused():
    """One release, but the file names carry the tag and the server reports
    pyproject's version, so they are spelled the same."""
    outputs, error = release_outputs("0.41.0-rc1", "0.41.0rc1")
    assert outputs is None
    assert "does not match pyproject.toml version 0.41.0rc1" in error


def _run(tag: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), tag], capture_output=True, text=True, cwd=ROOT,
    )


def test_the_script_prints_the_jobs_outputs_for_this_checkouts_version():
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    result = _run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"version={version}"
    assert lines[1] in ("prerelease=true", "prerelease=false")


def test_the_script_refuses_a_tag_that_is_not_this_checkouts_version():
    result = _run("999.0.0")
    assert result.returncode == 1
    assert result.stdout.startswith("::error::Release tag v999.0.0 does not match")


def _job(name: str) -> str:
    """One job's block of release.yml, as text."""
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)", text, re.M | re.S)
    assert match, f"release.yml has no {name} job"
    return match.group(1)


def test_the_version_job_asks_the_script():
    job = _job("version")
    assert "installer/release-version.py" in job
    assert 'echo "prerelease=' not in job


def test_no_job_decides_a_prerelease_by_a_hyphen():
    assert "*-*" not in WORKFLOW.read_text(encoding="utf-8")


def test_docker_tags_latest_from_the_version_jobs_answer():
    job = _job("docker")
    assert "needs.version.outputs.prerelease" in job
