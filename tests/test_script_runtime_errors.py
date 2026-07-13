"""Regression tests for the script editor's runtime-error markers (ScriptView).

The inline error markers exist to surface script runtime errors in the editor,
but the memo that built them read the log store via getState() and left
logEntries out of its dependency array, so it never recomputed when a new error
was logged — the markers were stale and integrators debugged blind. The memo now
subscribes to a narrow primitive (latestScriptErrorId — the id of the most
recent script error, which changes only when a new one is logged, so the view
doesn't re-render on every log line) and depends on it.

Two layers: the harness bundles the extracted pure helpers (scriptRuntimeErrors)
with the esbuild in web/programmer/node_modules and checks the marker extraction
and the reactive-trigger selector; source-level checks pin ScriptView.tsx to the
subscription + dependency so the staleness can't silently return. Skips when the
Node toolchain or esbuild is absent rather than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_script_runtime_errors.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "script_runtime_errors_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "scripts" / "scriptRuntimeErrors.ts"
)
SCRIPT_VIEW = OPENAVC_ROOT / "web" / "programmer" / "src" / "views" / "ScriptView.tsx"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "script runtime errors harness missing"
    if not HELPERS.is_file():
        return "scriptRuntimeErrors.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"script runtime errors harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "m309_extract_matches_by_id",
    "m309_extract_matches_by_file",
    "m309_extract_filters_non_error",
    "m309_extract_filters_other_script",
    "m309_extract_needs_line_number",
    "m309_extract_message_first_line",
    "m309_latest_id_returns_last_script_error",
    "m309_latest_id_zero_when_none",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_script_runtime_error_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


# --- Source pins for the reactivity fix (the actual bug) -----------------------

def test_scriptview_subscribes_to_latest_script_error_id() -> None:
    src = SCRIPT_VIEW.read_text(encoding="utf-8")
    assert "latestScriptErrorId(s.logEntries)" in src and "useLogStore((s) =>" in src, (
        "ScriptView must subscribe to a narrow primitive derived from the log store "
        "so the marker memo re-runs when a new script error is logged"
    )


def test_runtime_errors_memo_depends_on_the_subscription() -> None:
    src = SCRIPT_VIEW.read_text(encoding="utf-8")
    match = re.search(r"const runtimeErrors = useMemo\(.*?\}, \[(.*?)\]\);", src, re.DOTALL)
    assert match, "could not find the runtimeErrors memo in ScriptView"
    deps = match.group(1)
    assert "scriptErrorId" in deps, (
        "the runtimeErrors memo must depend on the log-store subscription "
        f"(deps were: {deps!r})"
    )
