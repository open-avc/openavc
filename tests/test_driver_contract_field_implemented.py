"""Every field the driver contract declares must have code behind it.

The field registry in ``server/drivers/spec.py`` is the single source of the
.avcdriver contract, and the generator renders it into the published schemas
and the Programmer IDE's types automatically. That automation is exactly what
makes this check necessary: a field can be added to the registry, generated
into every artifact, validated, published and documented, and still do
absolutely nothing, because nothing generated can tell whether anyone wrote
the runtime that reads it or the editor an author sets it with. A driver
author then finds the field in the schema, sets it, and it is quietly inert.

So this walks the registry and asserts each leaf field name is mentioned both
in the runtime and somewhere in the Programmer IDE outside the generated
types, with an explicit opt-out list per half for fields only one side is
meant to own.

It is deliberately crude — a grep, not a manifest. A per-field "which
surfaces implement me" table in the registry would be new hand-maintained
surface bought in exchange for what a grep already does, and every entry
would need keeping honest by hand: the same class of problem as the drift it
would be trying to catch. If this ever gets noisy, shrink its scope; do not
promote it to a manifest.

Two limits, recorded so nobody re-derives them:

* A field name that is also an ordinary identifier can pass on a coincidence
  somewhere unrelated. ``tls`` on a discovery probe has no Builder editor at
  all, but the name matches the HTTPS settings client, so this check reports
  it as covered. The check is a floor, not a ceiling.
* "Mentioned" is not "implemented correctly". This catches the field nobody
  wired up, which is a real and recurring bug; it cannot catch one wired up
  wrong. The corpus and behavior suites are what cover that.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from server.drivers import spec

REPO_ROOT = Path(__file__).resolve().parents[1]

# The registry declares the contract and the generators render it. A name
# matching itself in these three files proves nothing, so they are not part
# of the searched runtime.
NOT_AN_IMPLEMENTATION = frozenset({
    "server/drivers/spec.py",
    "server/drivers/contract_gen.py",
    "server/drivers/contract_gen_ts.py",
})

# The runtime half is the whole server plus the simulator: a contract field is
# implemented wherever its consumer lives, and they are spread wider than the
# driver package. Discovery hints are read in server/discovery/, frame-parser
# geometry in server/transport/, and the simulator: block in simulator/.
RUNTIME_ROOTS = ("server", "simulator")

FRONTEND_ROOT = "web/programmer/src"
GENERATED_TYPES = "types.gen.ts"

# Fields the runtime is not meant to read: presentation and catalog metadata
# that only an authoring or display surface consumes. Each entry says who does
# own it, so a wrong one is visible rather than inherited.
RUNTIME_OPT_OUT = {
    "label_field": "Child presentation: which state field names a child. "
                   "Read by the Builder (ChildEntityTypesEditor, validateDriver).",
    "label_plural": "Child presentation: plural noun for a child type. "
                    "Read by the Builder (ChildEntityTypesEditor).",
    "summary_fields": "Child presentation: which fields show on a child's "
                      "summary row. Read by the Builder (ChildEntityTypesEditor).",
    "row_label": "Table presentation: the singular noun on a config table's "
                 "Add-row button. Read by ConfigTableEditor.",
    "source_url": "Catalog metadata: a link to the protocol document. Shown "
                  "and edited in the Builder; nothing on the wire uses it.",
    "unique": "Authoring-time behavior: the setup dialog generates a "
              "non-clashing default from it (DeviceSettingsSetupDialog).",
}

# Fields with no editor in the Programmer IDE. An entry here is a statement
# that an author has to write this field by hand in YAML, so keep it short
# and keep the reason honest: "deliberate" and "not built yet" are different
# things and the difference is what a later reader needs.
FRONTEND_OPT_OUT = {
    "available_offline": "No editor yet: whether a command runs while the "
                         "device is offline can only be set by hand in YAML.",
    "cert_subject": "No editor yet: the discovery probe's TLS pair (tls + "
                    "cert_subject) is not in the discovery hints editor.",
    "json_path": "No editor yet: a response mapping that pulls its value out "
                 "of a JSON string can only be written by hand in YAML.",
    "receive": "Deliberate: simulator command handlers have no form UI. The "
               "editor shows a count and says to edit the file directly.",
    "respond": "Deliberate: simulator command handlers have no form UI. The "
               "editor shows a count and says to edit the file directly.",
    "transitions": "Deliberate: simulator state machines have no form UI. The "
                   "editor shows them read-only; they are authored in YAML.",
}

# Node keys whose values are themselves nodes, and the combinator keys whose
# values are tuples of nodes. Mirrors contract_gen._node_to_schema's recursion
# so the walk sees exactly what the generator renders.
_CHILD_NODE_KEYS = ("extra", "prop_names", "items")
_COMBINATOR_KEYS = ("one_of", "any_of", "all_of")


def _walk(node: object, names: set[str]) -> None:
    """Collect field names from one registry node, depth-first.

    Names come only from the keys of a ``fields`` dict. Every other key in a
    node is registry metadata ('type', 'doc', 'enum', ...) and naming one of
    those is not declaring a field — which matters, because 'raw' is both a
    node key (a raw schema fragment) and a real command field (send the string
    unframed).
    """
    if not isinstance(node, dict):
        return
    fields = node.get("fields")
    if isinstance(fields, dict):
        for name, child in fields.items():
            names.add(name)
            _walk(child, names)
    for key in _CHILD_NODE_KEYS:
        _walk(node.get(key), names)
    for key in _COMBINATOR_KEYS:
        for branch in node.get(key, ()):
            _walk(branch, names)
    # A raw fragment can declare properties the registry keys cannot model:
    # push:'s per-type branches narrow the legal key set that way.
    raw = node.get("raw")
    if isinstance(raw, dict):
        for fragment in (raw, raw.get("if"), raw.get("then")):
            if isinstance(fragment, dict):
                properties = fragment.get("properties")
                if isinstance(properties, dict):
                    names.update(properties)


@lru_cache(maxsize=1)
def _contract_field_names() -> frozenset[str]:
    """Every leaf field name the contract declares.

    FIELDS' own keys are top-level driver fields. DEFS' keys are *block*
    names (registry metadata, not something an author writes), so only their
    values are walked.
    """
    names: set[str] = set()
    for name, node in spec.FIELDS.items():
        names.add(name)
        _walk(node, names)
    for node in spec.DEFS.values():
        _walk(node, names)
    return frozenset(names)


def _read_all(paths: list[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


@lru_cache(maxsize=1)
def _runtime_source() -> str:
    paths = [
        path
        for root in RUNTIME_ROOTS
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if path.relative_to(REPO_ROOT).as_posix() not in NOT_AN_IMPLEMENTATION
    ]
    assert len(paths) > 100, "runtime source went missing — the search is wrong"
    return _read_all(paths)


@lru_cache(maxsize=1)
def _frontend_source() -> str:
    paths = [
        path
        for path in sorted((REPO_ROOT / FRONTEND_ROOT).rglob("*.ts*"))
        if path.name != GENERATED_TYPES
    ]
    assert len(paths) > 100, "IDE source went missing — the search is wrong"
    return _read_all(paths)


def _mentions(source: str, name: str) -> bool:
    """True when ``source`` uses ``name`` as a field rather than as prose.

    Matches an attribute access, a quoted literal, an object key or type
    member, or an assignment. Requiring one of those shapes is what keeps an
    English word like "respond" from passing on a sentence in a help string.
    """
    escaped = re.escape(name)
    pattern = (
        rf"\.{escaped}\b"
        rf"|[\"']{escaped}[\"']"
        rf"|\b{escaped}\s*[:?]"
        rf"|\b{escaped}\s*="
    )
    return re.search(pattern, source) is not None


def _unimplemented(source: str, opt_out: dict[str, str]) -> list[str]:
    return sorted(
        name
        for name in _contract_field_names()
        if name not in opt_out and not _mentions(source, name)
    )


def test_every_contract_field_is_read_by_the_runtime() -> None:
    missing = _unimplemented(_runtime_source(), RUNTIME_OPT_OUT)
    assert not missing, (
        f"declared in the driver contract but never read by the runtime: "
        f"{missing}. Implement the field, or — if only an authoring or "
        f"display surface is meant to own it — add it to RUNTIME_OPT_OUT "
        f"with the reason."
    )


def test_every_contract_field_has_an_editor() -> None:
    missing = _unimplemented(_frontend_source(), FRONTEND_OPT_OUT)
    assert not missing, (
        f"declared in the driver contract with no way to set it in the "
        f"Programmer IDE: {missing}. A field an author can only reach by "
        f"hand-editing YAML is a gap — add the editor, or add it to "
        f"FRONTEND_OPT_OUT with the reason it is deliberate."
    )


def test_opt_out_lists_hold_nothing_that_is_now_implemented() -> None:
    """An opt-out list may only shrink.

    Shipping the missing editor makes its entry stale, and a stale entry is
    how a list like this rots into a place fields get parked. Failing the
    moment an entry stops being true means the entry has to be deleted in the
    same change that makes it untrue.
    """
    stale_runtime = sorted(
        name for name in RUNTIME_OPT_OUT if _mentions(_runtime_source(), name)
    )
    stale_frontend = sorted(
        name for name in FRONTEND_OPT_OUT if _mentions(_frontend_source(), name)
    )
    assert not stale_runtime, (
        f"now read by the runtime — delete from RUNTIME_OPT_OUT: {stale_runtime}"
    )
    assert not stale_frontend, (
        f"now editable in the IDE — delete from FRONTEND_OPT_OUT: {stale_frontend}"
    )


def test_opt_out_lists_name_only_real_fields() -> None:
    names = _contract_field_names()
    unknown = sorted((set(RUNTIME_OPT_OUT) | set(FRONTEND_OPT_OUT)) - names)
    assert not unknown, (
        f"opt-out lists name fields the contract does not declare (renamed or "
        f"removed?): {unknown}"
    )


def test_the_walk_reaches_every_depth_of_the_registry() -> None:
    """Guard the extractor itself.

    Everything above passes vacuously if the walk stops returning names, and
    a nesting change in the registry could do that silently. These are one
    known field per shape the walk has to descend through.
    """
    names = _contract_field_names()
    for name, shape in (
        ("transport", "a top-level field"),
        ("commands", "a top-level field holding a $defs block"),
        ("available_offline", "a field of a $defs block"),
        ("passthrough_port", "a field inside an array's items"),
        ("max_length", "a field nested two objects deep"),
        ("idle_timeout", "a field declared only in a raw schema fragment"),
        ("transitions", "a field inside an open map's value node"),
    ):
        assert name in names, f"the registry walk no longer reaches {shape}: {name}"
    assert len(names) > 150, f"the registry walk collapsed: only {len(names)} names"
