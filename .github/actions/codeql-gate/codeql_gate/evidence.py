"""Decide whether a revision's actual findings are resolved or documented.

SARIF is the snapshot of findings in that revision: an alert fixed on a later
revision still blocks this one. A successful workflow alone proves no such thing.
"""
from dataclasses import replace

from .api import Unavailable
from .revisions import candidates
from .scans import Pending, latest_run, select_analyses, snapshot_results


class Findings(RuntimeError):
    """The selected revision has findings requiring remediation or triage."""


def documented_dismissal(alert):
    if alert["state"] != "dismissed":
        return False
    return bool(alert.get("dismissed_reason")) and bool((alert.get("dismissed_comment") or "").strip())


def finding_description(result, alert):
    location = result["locations"][0]["physicalLocation"]
    filename = location["artifactLocation"]["uri"]
    line = location.get("region", {}).get("startLine", "?")
    return f"{filename}:{line} {result['ruleId']} ({alert['state']}) {alert['html_url']}"


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
        if not documented_dismissal(alert):
            blocked[number] = finding_description(result, alert)
    return list(blocked.values())


def require_triaged(api, analyses):
    blocked = unresolved(api, snapshot_results(api, analyses))
    if blocked:
        raise Findings("\n".join(blocked) + "\nFix and rescan, or record a justified dismissal with a comment. "
                       "A finding fixed only in newer code still blocks this revision.")


def verify(api, request):
    waiting = []
    for candidate in candidates(api, request):
        try:
            run = latest_run(api, candidate)
            analyses = select_analyses(api, replace(request, target=candidate), run)
        except Pending as exc:
            waiting.append(str(exc))
            continue
        require_triaged(api, analyses)
        languages = ', '.join(sorted(request.languages))
        return f"CodeQL verified {request.target} using {candidate}; {languages}; {run['html_url']}"
    raise Pending("\n".join(waiting))
