"""Which scanned revisions may prove the requested source revision safe."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Request:
    target: str
    branch: str
    languages: frozenset
    allow_descendant: bool = False


def identical_parents(api, commit):
    tree = commit["commit"]["tree"]["sha"]
    parents = [api.get(f"commits/{parent['sha']}") for parent in commit["parents"]]
    return [parent["sha"] for parent in parents if parent["commit"]["tree"]["sha"] == tree]


def descendant(api, request):
    tip = api.get(f"commits/{request.branch}")["sha"]
    comparison = api.get(f"compare/{request.target}...{tip}")
    return [tip] if comparison["status"] == "ahead" else []


def candidates(api, request):
    commit = api.get(f"commits/{request.target}")
    choices = [commit["sha"], *identical_parents(api, commit)]
    if request.allow_descendant:
        choices.extend(descendant(api, request))
    return list(dict.fromkeys(choices))
