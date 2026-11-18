"""Generate ``wire_contract.json`` from the wire contract's tables.

``openavc/cloud/wire_tables.py`` is the one source of every message, field,
vocabulary and limit the agent and the cloud exchange. This module renders it
into ``openavc/cloud/wire_contract.json``: a JSON Schema and a whole example
per message, the vocabularies and the limits, each entry with the release it
arrived in, the crypto vectors (``wire_vectors.py``) both sides run, and the
report contract (``openavc/support/report_contract.py``): each kind of report's
version, the fields its readers rely on, the caps and an example. The cloud
copies that file and tests its own builders and readers against it.

Regenerate after any change to the tables:

    python -m openavc.cloud.contract_gen

A test compares the committed file against a fresh render, so a table edit
without a regen fails CI rather than drifting.

Before tagging a release, check that no entry names a release that never
shipped:

    python -m openavc.cloud.contract_gen --cut 0.41.0

It fails when an entry's ``since`` is newer than the previous final release
and is not the one being cut. A release candidate (``0.41.0-rc1``) is checked
as the final it leads to. Pass ``--previous`` when the checkout has no tags.

Pure stdlib on purpose.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

from openavc.cloud import wire_tables, wire_vectors
from openavc.cloud.crypto import sign_message
from openavc.cloud.wire_tables import ABSENT, Field, Message
from openavc.support import report_contract
from openavc.utils.spawn import CREATE_NO_WINDOW

SCHEMA_META = "https://json-schema.org/draft/2020-12/schema"

# This file's own layout; bumps only when the file's shape changes.
CONTRACT_LAYOUT = 1

# Every example is stamped and signed the same way, so the file is stable and
# the cloud can verify an example's signature with the key written beside it.
EXAMPLE_TS = "2026-11-17T15:46:00.000Z"
EXAMPLE_SEQ = 1
EXAMPLE_SESSION = "st_7c1e9a3b5d"
EXAMPLE_SIGNING_KEY = bytes(range(32))

_SCALAR_TYPES = ("string", "integer", "number", "boolean", "object", "array", "null")
_ARRAY_OF = re.compile(r"^array<(\w+)>$")


# --- Schemas ---

def _type_schema(type_expr: str) -> dict[str, Any]:
    """``"string|null"``, ``"array<string>"`` or ``"any"`` as a JSON Schema."""
    if type_expr == "any":
        return {}
    types: list[str] = []
    items: dict[str, Any] | None = None
    for part in type_expr.split("|"):
        match = _ARRAY_OF.match(part)
        if match:
            types.append("array")
            items = {"type": match.group(1)}
        elif part in _SCALAR_TYPES:
            types.append(part)
        else:
            raise ValueError(f"unknown type {part!r} in {type_expr!r}")
    schema: dict[str, Any] = {"type": types[0] if len(types) == 1 else types}
    if items is not None:
        schema["items"] = items
    return schema


def _vocabulary_values(name: str) -> list[Any]:
    return [entry.value for entry in wire_tables.VOCABULARIES[name]]


def _apply_vocabulary(schema: dict[str, Any], f: Field) -> None:
    """The field's values, and the vocabulary they come from, so a reader can
    drop the values a release did not have yet."""
    values = _vocabulary_values(f.vocabulary)
    schema["x-vocabulary"] = f.vocabulary
    if "items" in schema:
        schema["items"]["enum"] = values
        return
    types = schema.get("type")
    nullable = types == "null" or (isinstance(types, list) and "null" in types)
    schema["enum"] = values + ([None] if nullable else [])


def _object_schema(fields: tuple[Field, ...], open_: bool, down: bool) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {f.name: field_schema(f, down) for f in fields},
        "required": [f.name for f in fields if f.required],
        "additionalProperties": open_,
    }


def field_schema(f: Field, down: bool = False) -> dict[str, Any]:
    """One field's JSON Schema, with its doc, default, since and marks."""
    schema = _shape_schema(f, down)
    _annotate(schema, f, down)
    return schema


def _shape_schema(f: Field, down: bool) -> dict[str, Any]:
    """The field's type, its described properties or items, and its values."""
    schema = _type_schema(f.type)
    if f.fields:
        schema.update({k: v for k, v in _object_schema(f.fields, f.open, down).items()
                       if k != "type"})
    if f.items:
        schema["items"] = _object_schema(f.items, False, down)
    if f.vocabulary:
        _apply_vocabulary(schema, f)
    return schema


def _annotate(schema: dict[str, Any], f: Field, down: bool) -> None:
    """The field's doc, default, since, and on a downstream field its mark."""
    if f.doc:
        schema["description"] = f.doc
    if f.default is not ABSENT:
        schema["default"] = f.default
    schema["x-since"] = f.since
    if down and f.ignorable:
        schema["x-ignorable"] = True
        schema["x-why"] = f.why


def payload_schema(m: Message) -> dict[str, Any]:
    """A message's payload (a tunnel message's fields beside ``type``). An
    upstream payload is open: an older agent may still send a field this one
    no longer does, and the cloud reads past it."""
    return _object_schema(m.fields, m.open or m.direction == "up", m.direction in ("down", "both"))


def envelope_schemas() -> dict[str, Any]:
    type_ = {"type": "string"}
    handshake = {
        "type": "object",
        "properties": {"type": type_, "ts": {"type": "string"}, "payload": {"type": "object"}},
        "required": ["type", "ts", "payload"],
        "additionalProperties": False,
    }
    session = {
        "type": "object",
        "properties": {
            **handshake["properties"],
            "seq": {"type": "integer"},
            "session": {"type": "string"},
            "sig": {"type": "string", "description": "HMAC-SHA256 of every other field, hex."},
        },
        "required": ["type", "ts", "seq", "session", "payload", "sig"],
        "additionalProperties": False,
    }
    tunnel_data = {
        "type": "object",
        "description": "A message's fields sit beside type; there is no payload.",
        "properties": {"type": type_},
        "required": ["type"],
    }
    return {"handshake": handshake, "session": session, "tunnel_data": tunnel_data}


# --- Examples ---

def example_payload(m: Message) -> dict[str, Any]:
    return {f.name: f.example for f in m.fields if f.example is not ABSENT}


def example_message(m: Message) -> dict[str, Any]:
    """One whole message as it crosses the wire, envelope and signature included."""
    payload = example_payload(m)
    if m.phase == "tunnel_data":
        return {"type": m.type, **payload}
    msg: dict[str, Any] = {"type": m.type, "ts": EXAMPLE_TS}
    if m.phase == "session":
        msg.update({"seq": EXAMPLE_SEQ, "session": EXAMPLE_SESSION})
    msg["payload"] = payload
    if m.phase == "session":
        msg["sig"] = sign_message(EXAMPLE_SIGNING_KEY, msg)
    return msg


# --- The file ---

def _message_entry(m: Message) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "direction": m.direction,
        "phase": m.phase,
        "since": m.since,
        "description": m.doc,
    }
    for key in ("priority", "requires", "announced_by", "follows", "answer"):
        value = getattr(m, key)
        if value is not None:
            entry[key] = value
    if m.ignorable:
        entry["ignorable"] = True
        entry["why"] = m.why
    entry["payload"] = payload_schema(m)
    entry["example"] = example_message(m)
    return entry


def _vocabulary_entry(e: wire_tables.Entry) -> dict[str, Any]:
    out: dict[str, Any] = {"value": e.value, "since": e.since, "description": e.doc}
    if e.flags:
        out["flags"] = sorted(e.flags)
    return out


# --- Reports ---

_TYPE_PLAIN: dict[str, Any] = {
    "string": "", "integer": 0, "number": 0, "boolean": False, "object": {}, "array": [],
    "null": None, "any": None,
}


def _plain(type_expr: str) -> Any:
    """The plain value of a field's first type, fresh each time."""
    return copy.deepcopy(_TYPE_PLAIN[type_expr.split("|")[0]])


def _fresh(after: str) -> Any:
    return [] if after == "[]" else {}


def _into_item(node: list[Any], after: str) -> Any:
    if not node:
        node.append(_fresh(after))
    return node[0]


def _into_entry(node: dict[str, Any], after: str) -> Any:
    if not node:
        node["example"] = _fresh(after)
    return next(iter(node.values()))


def _into_named(node: dict[str, Any], tok: str, after: str) -> Any:
    if not isinstance(node.get(tok), (dict, list)):
        node[tok] = _fresh(after)
    return node[tok]


def _into(node: Any, tok: str, after: str) -> Any:
    """The container one step down a field's path, made when it is not there."""
    if tok == "[]":
        return _into_item(node, after)
    if tok == "*":
        return _into_entry(node, after)
    return _into_named(node, tok, after)


def _example_value(f: report_contract.ReportField) -> Any:
    return _plain(f.type) if f.example is report_contract.ABSENT else f.example


def _place(out: dict[str, Any], f: report_contract.ReportField) -> None:
    """One declared field put into the example, with what holds it."""
    toks = report_contract.tokens(f.path)
    node: Any = out
    for i, tok in enumerate(toks[:-1]):
        node = _into(node, tok, toks[i + 1])
    if not isinstance(node.get(toks[-1]), (dict, list)):
        node[toks[-1]] = _example_value(f)


def report_example(kind: report_contract.ReportKind) -> dict[str, Any]:
    """One report of ``kind`` holding every declared field: each its own
    example, else its type's plain value; a list holds one item and a map one
    key, ``example``."""
    out: dict[str, Any] = {}
    for f in kind.fields:
        _place(out, f)
    out["report_version"] = kind.report_version
    return out


def _report_field(kind: report_contract.ReportKind, f: report_contract.ReportField) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": f.path, "type": f.type, "required": f.required,
        "since": report_contract.field_since(kind, f), "readers": list(f.readers),
    }
    if f.doc:
        entry["description"] = f.doc
    return entry


def _report_kind(kind: report_contract.ReportKind) -> dict[str, Any]:
    return {
        "since": kind.since,
        "description": kind.doc,
        "report_version": kind.report_version,
        "versions": [
            {"version": v.version, "since": v.since, "description": v.doc, "shape": v.shape}
            for v in kind.versions
        ],
        "fields": [_report_field(kind, f) for f in kind.fields],
        "example": report_example(kind),
    }


def build_reports() -> dict[str, Any]:
    return {
        "limits": {name: {"value": value, "description": doc}
                   for name, (value, doc) in report_contract.LIMITS.items()},
        "readers": list(report_contract.READERS),
        "kinds": {name: _report_kind(kind) for name, kind in report_contract.KINDS.items()},
    }


def build_contract() -> dict[str, Any]:
    limits = wire_tables.LIMITS
    return {
        "$comment": "Generated by python -m openavc.cloud.contract_gen; never edit by hand.",
        "contract": CONTRACT_LAYOUT,
        "schema_dialect": SCHEMA_META,
        "baseline": wire_tables.BASELINE,
        "first_contract_release": wire_tables.FIRST_CONTRACT_RELEASE,
        "protocol_version": limits["protocol_version"][0],
        "min_protocol_version": limits["min_protocol_version"][0],
        "limits": {name: {"value": value, "description": doc}
                   for name, (value, doc) in limits.items()},
        "example_signing_key": EXAMPLE_SIGNING_KEY.hex(),
        "envelope": envelope_schemas(),
        "messages": {m.type: _message_entry(m) for m in wire_tables.MESSAGES},
        "vocabularies": {name: [_vocabulary_entry(e) for e in entries]
                         for name, entries in wire_tables.VOCABULARIES.items()},
        "vectors": wire_vectors.VECTORS,
        "reports": build_reports(),
    }


def render() -> str:
    return json.dumps(build_contract(), indent=2, ensure_ascii=False) + "\n"


def artifact_path(repo_root: Path) -> Path:
    return repo_root / "openavc" / "cloud" / "wire_contract.json"


# --- The cut check ---

_FINAL = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_CANDIDATE = re.compile(r"^v?(\d+\.\d+\.\d+)[-.]?(?:rc|a|b)\d*$")


def _version(text: str) -> tuple[int, int, int]:
    match = _FINAL.match(text)
    if not match:
        raise ValueError(f"{text!r} is not a release number like 0.41.0")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def final_of(version: str) -> str:
    """The final release a version leads to: ``0.41.0-rc1`` gives ``0.41.0``."""
    match = _CANDIDATE.match(version)
    return match.group(1) if match else version.lstrip("v")


def sinces() -> Iterator[tuple[str, str]]:
    """Every (entry, since) in the tables."""
    def walk(prefix: str, fields: tuple[Field, ...]) -> Iterator[tuple[str, str]]:
        for f in fields:
            yield f"{prefix}.{f.name}", f.since
            yield from walk(f"{prefix}.{f.name}", f.fields)
            yield from walk(f"{prefix}.{f.name}[]", f.items)

    yield "first_contract_release", wire_tables.FIRST_CONTRACT_RELEASE
    for m in wire_tables.MESSAGES:
        yield m.type, m.since
        yield from walk(m.type, m.fields)
    for name, entries in wire_tables.VOCABULARIES.items():
        for e in entries:
            yield f"{name}: {e.value}", e.since
    yield from _report_sinces()


def _report_sinces() -> Iterator[tuple[str, str]]:
    """Every (entry, since) in the report contract."""
    for kind in report_contract.KINDS.values():
        yield f"report {kind.kind}", kind.since
        yield from ((f"report {kind.kind} version {v.version}", v.since) for v in kind.versions)
        yield from ((f"report {kind.kind}: {f.path}", report_contract.field_since(kind, f))
                    for f in kind.fields)


def unshipped_sinces(cutting: str, previous: str) -> list[str]:
    """Entries naming a release newer than ``previous`` that is not ``cutting``."""
    cut, prev = _version(final_of(cutting)), _version(previous)
    return [
        f"{entry} says since {since}"
        for entry, since in sinces()
        if _version(since) > prev and _version(since) != cut
    ]


def latest_final_tag(repo_root: Path, below: str) -> str:
    """The newest final release tag older than ``below``."""
    tags = subprocess.run(
        ["git", "-C", str(repo_root), "tag", "-l", "v*"],
        capture_output=True, text=True, check=True, creationflags=CREATE_NO_WINDOW,
    ).stdout.split()
    limit = _version(final_of(below))
    finals = [t.lstrip("v") for t in tags if _FINAL.match(t) and _version(t) < limit]
    if not finals:
        raise ValueError("no earlier final release tag found; pass --previous")
    return max(finals, key=_version)


# --- Command line ---

def write(repo_root: Path) -> bool:
    """Write the file; True when it changed."""
    path = artifact_path(repo_root)
    content = render()
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current == content:
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return True


def _cut(repo_root: Path, cutting: str, previous: str | None) -> int:
    try:
        previous = previous or latest_final_tag(repo_root, cutting)
        problems = unshipped_sinces(cutting, previous)
    except (ValueError, subprocess.CalledProcessError, OSError) as exc:
        print(f"wire contract: cannot check the cut: {exc}", file=sys.stderr)
        return 2
    if problems:
        print(
            f"wire contract: cutting {final_of(cutting)} after {previous}, these name a "
            f"release that is neither shipped nor this one. Set each since to "
            f"{final_of(cutting)} in openavc/cloud/wire_tables.py (a report's in "
            f"openavc/support/report_contract.py) and regenerate:",
            file=sys.stderr,
        )
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"wire contract: every since is {previous} or older, or {final_of(cutting)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openavc.cloud.contract_gen")
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed file differs from a fresh render")
    parser.add_argument("--cut", metavar="VERSION",
                        help="check every since against the release being cut")
    parser.add_argument("--previous", metavar="VERSION",
                        help="the previous final release (default: the newest final tag)")
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    if args.cut:
        return _cut(repo_root, args.cut, args.previous)
    path = artifact_path(repo_root)
    if args.check:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current.replace("\r\n", "\n") != render():
            print(f"{path.relative_to(repo_root)} is stale: run "
                  f"python -m openavc.cloud.contract_gen", file=sys.stderr)
            return 1
        return 0
    changed = write(repo_root)
    print(f"  {'wrote' if changed else 'unchanged'}  {path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
