"""Regression tests for the script editor's runtime-error markers (ScriptView).

The inline error markers exist to surface script runtime errors in the editor.
They once went stale: the memo that built them read the log store via
getState() and never recomputed when a new error was logged, so integrators
debugged blind. The mark now comes from the server's record of the script's
last failure (``GET /api/scripts/errors``, its traceback), and the view
re-asks that record while it is open (``useScriptErrorsRefresh``): on the
server's ``script.error`` message, and on a narrow primitive from the log store
(latestScriptErrorId, the id of the most recent script error, which changes
only when a new one is logged, so the view does not re-render on every line).

Two layers: the harness bundles the extracted pure helpers (scriptRuntimeErrors)
with the esbuild in openavc/web/programmer/node_modules and checks the stored
marker, the row's readable error and the log selector; source-level checks pin
ScriptView.tsx to the re-ask and the memo to the record, so the staleness
cannot silently return. Skips when the Node toolchain or esbuild is absent
rather than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_script_runtime_errors.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "script_runtime_errors_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "scripts" / "scriptRuntimeErrors.ts"
)
SCRIPT_VIEW = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "views" / "ScriptView.tsx"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "script runtime errors harness missing"
    if not HELPERS.is_file():
        return "scriptRuntimeErrors.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
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
    "m309_latest_id_returns_last_script_error",
    "m309_latest_id_zero_when_none",
    # The server's own record of a runtime failure, the only place a script's
    # line is to be had: a log line carries the sentence, never the traceback.
    "stored_marker_takes_the_line_in_this_file",
    "stored_marker_takes_the_deepest_frame",
    "stored_marker_matches_a_windows_path",
    "stored_marker_ignores_another_file",
    "stored_marker_silent_without_a_traceback",
    "stored_marker_silent_without_a_record",
    "stored_marker_carries_the_readable_error",
    "described_error_puts_the_type_in_front",
    "described_error_falls_back_without_a_traceback",
    "described_error_refuses_an_unrelated_last_line",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_script_runtime_error_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


# --- Source pins for the reactivity fix (the actual bug) -----------------------

REFRESH_HOOK = SCRIPT_VIEW.parents[1] / "components" / "scripts" / "useScriptErrorsRefresh.ts"


def test_scriptview_reasks_the_record_while_open() -> None:
    src = SCRIPT_VIEW.read_text(encoding="utf-8")
    assert "useScriptErrorsRefresh(refreshScriptErrors)" in src, (
        "ScriptView must re-ask the server's record of script failures while it "
        "is open, or a failure after it opened never marks the editor"
    )
    hook = REFRESH_HOOK.read_text(encoding="utf-8")
    assert "latestScriptErrorId(s.logEntries)" in hook and "useLogStore((s) =>" in hook, (
        "the re-ask must follow a narrow primitive derived from the log store, "
        "not the whole log"
    )
    assert "useScriptErrorsStore((s) => s.heard)" in hook, (
        "the re-ask must follow the server's script.error message too"
    )


def test_runtime_errors_memo_depends_on_the_record() -> None:
    src = SCRIPT_VIEW.read_text(encoding="utf-8")
    match = re.search(r"const runtimeErrors = useMemo\(.*?\}, \[(.*?)\]\);", src, re.DOTALL)
    assert match, "could not find the runtimeErrors memo in ScriptView"
    deps = match.group(1)
    assert "scriptRuntimeErrors" in deps, (
        "the runtimeErrors memo must depend on the server's record it reads "
        f"(deps were: {deps!r})"
    )
