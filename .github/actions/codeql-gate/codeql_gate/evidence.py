"""Bind a successful scan and its findings to a specific source revision.

A successful workflow only means analysis ran. SARIF is the snapshot of findings
in that revision; an alert fixed on a later revision still blocks this one.
Documented GitHub dismissals are the only accepted findings in the snapshot.
"""
import json

from .api import Unavailable


class Pending(RuntimeError):
    """A complete, successful scan is not available yet."""


class Findings(RuntimeError):
    """The selected revision has findings that require remediation or triage."""


def candidates(api, target, branch, allow_descendant):
    commit = api.get(f"commits/{target}")
    sha = commit["sha"]
    result = [sha]
    # Deployment merges can have the identical source tree as a scanned parent.
    for parent in commit["parents"]:
        source = api.get(f"commits/{parent['sha']}")
        if source["commit"]["tree"]["sha"] == commit["commit"]["tree"]["sha"]:
            result.append(source["sha"])
    if allow_descendant:
        tip = api.get(f"commits/{branch}")["sha"]
        if tip not in result:
            comparison = api.get(f"compare/{sha}...{tip}")
            if comparison["status"] == "ahead":
                result.append(tip)
    return sha, result


def latest_run(api, sha):
    runs = [r for r in api.pages("actions/runs", "workflow_runs", head_sha=sha)
            if r.get("path") == "dynamic/github-code-scanning/codeql"]
    if not runs:
        raise Pending(f"{sha[:12]}: CodeQL run not available")
    run = max(runs, key=lambda r: (r["created_at"], r["id"], r["run_attempt"]))
    if run["status"] != "completed" or run["conclusion"] != "success":
        raise Pending(f"{sha[:12]}: CodeQL {run['status']}/{run.get('conclusion')}; {run['html_url']}")
    return run


def select_analyses(api, sha, branch, languages, run):
    selected = {}
    for row in api.pages("code-scanning/analyses", ref=f"refs/heads/{branch}", tool_name="CodeQL"):
        if row["created_at"] < run["run_started_at"]:
            break  # Analyses are newest first; earlier runs cannot supply evidence.
        if row["commit_sha"] != sha:
            continue
        language = json.loads(row["environment"]).get("language")
        if language in languages and language not in selected:
            selected[language] = row  # API order: newest first, never an older green result.
        if set(selected) == languages:
            break
    missing = languages - set(selected)
    if missing:
        raise Pending(f"{sha[:12]}: missing analysis for {', '.join(sorted(missing))}")
    for language, row in selected.items():
        validate_analysis(row, language, run)
    return selected.values()


def validate_analysis(row, language, run):
    if row["analysis_key"] != "dynamic/github-code-scanning/codeql:analyze":
        raise Unavailable(f"{language}: unsupported analysis configuration")
    if not run["run_started_at"] <= row["created_at"] <= run["updated_at"]:
        raise Pending(f"{language}: analysis does not belong to the latest completed run")
    if row["error"] or row["warning"] or row["rules_count"] <= 0:
        raise Unavailable(f"{language}: analysis has errors, warnings, or no rules")


def snapshot_results(api, analyses):
    results = []
    for row in analyses:
        sarif = api.get(f"code-scanning/analyses/{row['id']}", sarif=True)
        if sarif["version"] != "2.1.0" or not sarif["runs"]:
            raise Unavailable(f"Analysis {row['id']}: invalid SARIF snapshot")
        found = [result for run in sarif["runs"] for result in run.get("results", [])]
        if len(found) != row["results_count"]:
            raise Unavailable(f"Analysis {row['id']}: incomplete finding snapshot")
        results.extend(found)
    return results


def unresolved(api, results):
    if not results:
        return []
    alerts = {a["number"]: a for a in api.pages("code-scanning/alerts", tool_name="CodeQL")}
    blocked = {}
    for result in results:
        number = result["properties"]["github/alertNumber"]
        if number not in alerts:
            raise Unavailable(f"Finding #{number}: triage state unavailable")
        alert = alerts[number]
        if documented_dismissal(alert):
            continue
        location = result["locations"][0]["physicalLocation"]
        filename = location["artifactLocation"]["uri"]
        line = location.get("region", {}).get("startLine", "?")
        blocked[number] = (f"{filename}:{line} {result['ruleId']} "
                           f"({alert['state']}) {alert['html_url']}")
    return list(blocked.values())


def documented_dismissal(alert):
    return (alert["state"] == "dismissed"
            and bool(alert.get("dismissed_reason"))
            and bool((alert.get("dismissed_comment") or "").strip()))


def verify(api, target, branch, languages, allow_descendant=False):
    sha, choices = candidates(api, target, branch, allow_descendant)
    waiting = []
    for candidate in choices:
        try:
            run = latest_run(api, candidate)
            analyses = select_analyses(api, candidate, branch, languages, run)
        except Pending as exc:
            waiting.append(str(exc))
            continue
        blocked = unresolved(api, snapshot_results(api, analyses))
        if blocked:
            raise Findings("\n".join(blocked) + "\nFix and rescan, or record a justified dismissal with a comment. "
                           "A finding fixed only in newer code still blocks this revision.")
        return f"CodeQL verified {sha} using {candidate}; {', '.join(sorted(languages))}; {run['html_url']}"
    raise Pending("\n".join(waiting))
