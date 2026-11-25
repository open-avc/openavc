"""The release's test job runs what CI's test job runs, with the same promises.

release.yml builds and publishes only once its own test job passes, so that job
is the last check in front of every installed system. A test that needs a tool
beyond the Python packages skips when the tool is missing, unless the run
promises it (tests/gates.py). A release job that promised nothing, or made a
promise without the step that installs the tool, would pass with those tests
skipped. For a long time it did: it set no promise at all.

So the two jobs are held equal, promise for promise and step for step. The one
difference allowed is where the Python version is written: ci.yml's matrix, and
the release workflow's own setting that its build jobs also read. A step either
job adds has to be added to both.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
SETUP_PYTHON = "actions/setup-python"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _test_job(name: str) -> dict:
    return _workflow(name)["jobs"]["test"]


def _steps(job: dict) -> list[dict]:
    """The job's steps, with the setup-python step reduced to the action it uses."""
    return [
        {"uses": step["uses"]} if str(step.get("uses", "")).startswith(SETUP_PYTHON) else step
        for step in job["steps"]
    ]


def test_the_release_tests_make_the_same_promises_as_ci():
    assert _test_job("release.yml")["env"] == _test_job("ci.yml")["env"]


def test_the_release_tests_run_the_same_steps_as_ci():
    ci, release = _steps(_test_job("ci.yml")), _steps(_test_job("release.yml"))
    assert [s.get("name", s.get("uses")) for s in release] == [s.get("name", s.get("uses")) for s in ci]
    for ours, theirs in zip(release, ci):
        assert ours == theirs, f"release.yml's '{ours.get('name', ours.get('uses'))}' step differs from ci.yml's"


def test_the_release_tests_run_on_the_python_ci_runs_on():
    ci_versions = _test_job("ci.yml")["strategy"]["matrix"]["python-version"]
    release = _workflow("release.yml")
    setup = next(s for s in release["jobs"]["test"]["steps"] if str(s.get("uses", "")).startswith(SETUP_PYTHON))
    assert setup["with"]["python-version"] == "${{ env.PYTHON_VERSION }}"
    assert ci_versions == [release["env"]["PYTHON_VERSION"]]


def test_the_release_tests_cover_every_system_ci_tests_on():
    ci_systems = set(_test_job("ci.yml")["strategy"]["matrix"]["os"])
    assert ci_systems <= set(_test_job("release.yml")["strategy"]["matrix"]["os"])
