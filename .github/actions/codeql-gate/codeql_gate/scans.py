"""Successful default-setup runs and complete, matching analysis snapshots."""
import json

from .api import Unavailable


class Pending(RuntimeError):
    """A complete, successful scan is not available yet."""


def latest_run(api, sha):
    runs = [r for r in api.pages("actions/runs", "workflow_runs", head_sha=sha)
            if r.get("path") == "dynamic/github-code-scanning/codeql"]
    if not runs:
        raise Pending(f"{sha[:12]}: CodeQL run not available")
    run = max(runs, key=lambda r: (r["created_at"], r["id"], r["run_attempt"]))
    require_success(run, sha)
    return run


def require_success(run, sha):
    if run["status"] != "completed" or run["conclusion"] != "success":
        raise Pending(f"{sha[:12]}: CodeQL {run['status']}/{run.get('conclusion')}; {run['html_url']}")


def matching_analyses(api, request, run):
    for row in api.pages("code-scanning/analyses", ref=f"refs/heads/{request.branch}", tool_name="CodeQL"):
        if row["created_at"] < run["run_started_at"]:
            break  # API order is newest first; older runs cannot supply evidence.
        if row["commit_sha"] == request.target:
            yield row


def select_analyses(api, request, run):
    selected = {}
    for row in matching_analyses(api, request, run):
        language = json.loads(row["environment"]).get("language")
        if language in request.languages:
            selected.setdefault(language, row)  # Never replace with an older result.
    missing = request.languages - set(selected)
    if missing:
        raise Pending(f"{request.target[:12]}: missing analysis for {', '.join(sorted(missing))}")
    for language, row in selected.items():
        validate_analysis(row, language, run)
    return selected.values()


def validate_analysis(row, language, run):
    if row["analysis_key"] != "dynamic/github-code-scanning/codeql:analyze":
        raise Unavailable(f"{language}: unsupported analysis configuration")
    if not run["run_started_at"] <= row["created_at"] <= run["updated_at"]:
        raise Pending(f"{language}: analysis does not belong to the latest completed run")
    require_complete(row, language)


def require_complete(row, language):
    if row["error"] or row["warning"]:
        raise Unavailable(f"{language}: analysis has errors or warnings")
    if row["rules_count"] <= 0:
        raise Unavailable(f"{language}: analysis ran no rules")


def analysis_results(api, row):
    sarif = api.get(f"code-scanning/analyses/{row['id']}", sarif=True)
    if sarif["version"] != "2.1.0" or not sarif["runs"]:
        raise Unavailable(f"Analysis {row['id']}: invalid SARIF snapshot")
    found = []
    for run in sarif["runs"]:
        found.extend(run.get("results", []))
    if len(found) != row["results_count"]:
        raise Unavailable(f"Analysis {row['id']}: incomplete finding snapshot")
    return found


def snapshot_results(api, analyses):
    results = []
    for row in analyses:
        results.extend(analysis_results(api, row))
    return results
