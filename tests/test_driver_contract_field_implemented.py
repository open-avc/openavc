"""Every field the driver contract declares must have code behind it.

The field registry in ``openavc/drivers/spec.py`` is the single source of the
.avcdriver contract, and the generator renders it into the published schemas
and the Programmer IDE's types automatically. That automation is exactly what
makes this check necessary: a field can be added to the registry, generated
into every artifact, validated, published and documented, and still do
absolutely nothing, because nothing generated can tell whether anyone wrote
the runtime that reads it or the editor an author sets it with. A driver
author then finds the field in the schema, sets it, and it is quietly inert.

So this walks the registry and asserts each declared field has code behind it
in the runtime and in the Programmer IDE outside the generated types, with an
explicit opt-out list per half for fields only one side is meant to own.

It asks about a field **at its declared position**, not about its bare name.
That distinction is the whole point, and it was bought with a real bug: nine
shipped drivers set ``secret`` on a command parameter and the Programmer
masked it, but the contract declared that key only on a config-schema entry,
so ``secret`` "passed" this check on an implementation belonging to a
different field. A right-name-wrong-place field was invisible here. The names
are not rare: ``help``, ``values``, ``regex`` and ``secret`` are each declared
in five to seven different blocks, and a name-keyed check collapses all of
them into one obligation that any single implementation satisfies.

The unit is therefore ``(block, field)`` — the field as the registry declares
it, where the block is the ``DEFS`` entry it lives in. Two contract paths that
``$ref`` the same block are the same field (``commands.*.params.*.secret`` and
``actions[].params.*.secret`` are one obligation, satisfied by one editor),
which is why this keys on the block rather than on every path string. Failures
report the paths so the message still points at something an author writes.

Evidence is scoped where scoping does work, and nowhere else. A name the
registry declares in only one block is unambiguous, so a plain match settles
it exactly as before. For a name declared in several blocks — the ambiguous
case, the one this exists for — a match only counts when the same file also
mentions a **distinctive sibling**: a field name declared in exactly one
block, and rare enough in the source to locate anything. Requiring an anchor
everywhere was measured and rejected; it failed fields that are plainly
implemented (``commands.*.sets``, ``device_settings.*.state_key``) because
the implementing file happened to name no distinctive sibling, and a check
that cries wolf gets muted.

It is still deliberately crude — a scoped grep, not a manifest. A per-field
"which surfaces implement me" table in the registry would be new
hand-maintained surface bought in exchange for what a grep already does, and
every entry would need keeping honest by hand: the same class of problem as
the drift it would be trying to catch. If this ever gets noisy, shrink its
scope; do not promote it to a manifest.

Four limits, recorded so nobody re-derives them:

* **An unambiguous name still rides on a plain grep**, so a field whose name
  is ordinary vocabulary can pass on an unrelated file.
  ``simulator.state_machines.*.states`` and ``.initial`` have no editor and
  are deliberate — the block is rendered read-only — but both pass on the UI
  Builder's own use of those words, and ``help.overview`` passes on the
  server's. They are not opted out below, because an entry for a field the
  check believes is covered is stale the day it is written and the staleness
  test rejects it. They are recorded here instead. Scoping only ever engages
  where a name is ambiguous; it cannot rescue a common word declared once.
* **A block whose only distinctive fields are absent from the implementing
  file cannot be scoped either way.** That is what the ``*_BLIND_SPOTS`` lists
  hold, per half, each with the code that proves the field is implemented.
  Keep them near empty: entries there are places this check is blind, not
  fields anyone is excused from owning.
* **Scoping narrows coincidence, it does not remove it.** Two of the same
  words in one unrelated file will still pass a field. The check is a floor,
  not a ceiling.
* "Mentioned" is not "implemented correctly". This catches the field nobody
  wired up, which is a real and recurring bug; it cannot catch one wired up
  wrong. The corpus and behavior suites are what cover that.

It found two real gaps the day it was written, both invisible to the
name-keyed version: ``simulator.controls`` had no editor and passed on the UI
Builder's unrelated "controls", and ``liveness.args`` had none and passed on
the four other blocks that declare ``args``. Both now have one.

The four collisions this file used to list — ``max_length``, ``pattern``,
``trim`` and ``dynamic`` — are **gone, not forgotten**: each was a field whose
frontend half passed on a name belonging to something else, and all four now
pass on their own block's implementation once evidence is scoped. They needed
recording while the check could not tell the difference. It can.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

from openavc.drivers import spec

REPO_ROOT = Path(__file__).resolve().parents[1]

# The registry declares the contract and the generators render it. A name
# matching itself in these three files proves nothing, so they are not part
# of the searched runtime.
NOT_AN_IMPLEMENTATION = frozenset({
    "openavc/drivers/spec.py",
    "openavc/drivers/contract_gen.py",
    "openavc/drivers/contract_gen_ts.py",
    # The project-file format migration. It reads the 0.7.0 UI page grid,
    # whose `columns`/`rows` share a spelling with a config table's, so it
    # supplies configSchemaEntry's anchor while implementing nothing in the
    # driver contract -- and a dict `.values()` call beside it was enough to
    # make config_schema.*.values look read by the runtime.
    "openavc/core/project_migration.py",
    # The PANEL-authoring generators. They render the control minimums, the
    # binding-reach table and the authoring guide, none of which is a driver
    # contract field -- and they talk about a page's `columns`, `rows` and
    # `values` in exactly the words a config table uses, which is the same
    # collision the migration above hit. A tile wall's column count was enough
    # to make config_schema.*.values look read by the runtime.
    "openavc/ui/guide_gen.py",
    "openavc/ui/minimums_gen.py",
    "openavc/ui/review_gen.py",
})

# The runtime half is the whole package, simulator included: a contract field
# is implemented wherever its consumer lives, and they are spread wider than the
# driver package. Discovery hints are read in openavc/discovery/, frame-parser
# geometry in openavc/transport/, and the simulator: block in openavc/simulator/.
# One root covers all of them now.
RUNTIME_ROOTS = ("openavc",)

FRONTEND_ROOT = "openavc/web/programmer/src"
GENERATED_TYPES = "types.gen.ts"

# Opt-out keys are "<block>.<field>", or a bare field name for a top-level
# driver field, so an entry excuses one position and not every same-named
# field elsewhere. test_opt_out_lists_name_only_real_fields rejects a key the
# registry does not declare, which is what keeps a renamed block from leaving
# a dead entry that silently excuses nothing.

# Fields the runtime is not meant to read: presentation and catalog metadata
# that only an authoring or display surface consumes. Each entry says who does
# own it, so a wrong one is visible rather than inherited.
RUNTIME_OPT_OUT = {
    # childEntityType.label_field was here until 2026-08-16, excused as
    # presentation the Builder owns. That was true and it was the bug: because
    # the runtime never resolved it, every picker had to, and the matrix picker
    # didn't -- so a device-enumerated roster showed real names in one dropdown
    # and "Encoder 1" in the next. The runtime reads it now
    # (drivers/child_ids.child_display_name) and serves the answer to both.
    "childEntityType.summary_fields":
        "Child presentation: which fields show on a child's summary row. "
        "Read by the Builder (ChildEntityTypesEditor).",
    "configSchemaEntry.row_label":
        "Table presentation: the singular noun on a config table's "
        "Add-row button. Read by ConfigTableEditor.",
    "source_url":
        "Catalog metadata: a link to the protocol document. Shown "
        "and edited in the Builder; nothing on the wire uses it.",
    "deviceSettingEntry.unique":
        "Authoring-time behavior: the setup dialog generates a "
        "non-clashing default from it (DeviceSettingsSetupDialog).",
    # The rest of config_schema's members, on the same footing as row_label
    # above: the runtime hands the whole block to the IDE
    # (configurable.py's driver_info, device_manager's driver listing) and
    # never reads a member. These passed the old name-keyed check only
    # because words like "values" and "help" are everywhere in the server.
    "configSchemaEntry.values":
        "Connection-form presentation: the dropdown's options. Read by the "
        "device config form (configFieldKind -> select), not by the runtime.",
    "configSchemaEntry.help":
        "Connection-form presentation: help text under the input.",
    "configSchemaEntry.regex":
        "Connection-form validation, applied by the IDE while editing.",
    "configSchemaEntry.secret":
        "Connection-form presentation: render masked "
        "(configFieldKind -> password). The value is stored as typed.",
    "configSchemaEntry.advanced":
        "Connection-form presentation: collapse the field behind Advanced "
        "when the device is added or edited (DeviceDialogs). Nothing on the "
        "wire changes; the value is read exactly as it always was.",
    # These four were passing on a coincidence until 2026-08-01. The scoped
    # search wants `columns` -- configSchemaEntry's distinctive sibling -- in
    # the same file as the field, and the only `columns` in reach was
    # GridConfig.columns, the UI page grid's column count, which has nothing
    # to do with a config table. Deleting the grid for the layout engine took
    # the anchor with it and the search correctly reported it had no evidence.
    # It never had any: the runtime hands config_schema to the IDE whole and
    # reads only `secret` and `default` (avcdriver_semantic's masked-default
    # warning), so these belong here beside the rest of the block.
    "configSchemaEntry.required":
        "Connection-form validation: the field is mandatory in the device "
        "config form. Nothing on the wire consults it.",
    "configSchemaEntry.description":
        "Connection-form presentation: prose shown beside the input.",
    "configSchemaEntry.min":
        "Connection-form validation: numeric lower bound applied by the IDE "
        "while editing.",
    "configSchemaEntry.max":
        "Connection-form validation: numeric upper bound applied by the IDE "
        "while editing.",
    # config_schema's `columns` and help's `overview`/`connection` belong here
    # on the same reasoning, and are deliberately NOT listed: each is declared
    # in only one block, so it takes the unscoped grep and passes on ordinary
    # server vocabulary ("connection"). Listing them would be stale on the day
    # it was written; the staleness test rejects exactly that.
}

# Fields that ARE implemented but that the scoped search cannot see, because
# the implementing file names no distinctive sibling of their block. These are
# NOT opt-outs — nobody is excused from owning them, this check is simply
# blind there — so they are kept separate, per half (a field can be invisible
# to one search and plain to the other), and each entry points at the code
# that proves it. Keep them near-empty: a growing list means the scoping rule
# needs rethinking, not more entries.
RUNTIME_BLIND_SPOTS = {
    "configSchemaEntry.default":
        "Read by avcdriver_semantic's masked-default check "
        "(`field_def.get(\"default\")` inside its config_schema loop), which "
        "is what refuses to ship a real password baked into a driver file. "
        "That file names neither `columns` nor `row_label`, configSchemaEntry's "
        "only two anchors, so the scoped search cannot see it. It passed until "
        "2026-08-01 on the UI page grid's `columns` living in project_loader.py.",
    "deviceSettingEntry.regex":
        "Read by base.py's _coerce_device_setting_value "
        "(`sdef.get(\"regex\") or sdef.get(\"pattern\")`). deviceSettingEntry "
        "declares only three distinctive fields (state_key, unique, write) "
        "and that function names none of them.",
}

FRONTEND_BLIND_SPOTS = {
    "configSchemaEntry.secret":
        "Edited by ConfigSchemaEditor (the 'Secret' checkbox writes "
        "field.secret). That file names no distinctive sibling of "
        "configSchemaEntry -- its anchors are `columns` and `row_label`, "
        "both of which live in ConfigTableEditor instead -- so the scoped "
        "search cannot see it. It read as implemented until 2026-08-01 only "
        "because api/types.ts happened to declare `secret` beside the UI "
        "page grid's `columns`.",
}

# Fields with no editor in the Programmer IDE. An entry here is a statement
# that an author has to write this field by hand in YAML, so keep it short
# and keep the reason honest: "deliberate" and "not built yet" are different
# things and the difference is what a later reader needs.
FRONTEND_OPT_OUT = {
    # Not built yet, as opposed to deliberate. ConfigSchemaEditor writes
    # label, description, required, secret and values; a config field's
    # `regex` has no control anywhere in the IDE, so an author has to add it
    # by hand in YAML. This was masked the same way secret was: types.ts
    # carried `regex` next to the UI grid's `columns`, and deleting the grid
    # for the layout engine took the false evidence with it.
    "configSchemaEntry.regex":
        "Not built yet: no control writes a config field's regex. Authors "
        "add it by hand in YAML. Tracked in the backlog.",
    "simulatorSection.receive":
        "Deliberate: simulator command handlers have no form UI. The "
        "editor shows a count and says to edit the file directly.",
    "simulatorSection.respond":
        "Deliberate: simulator command handlers have no form UI. The "
        "editor shows a count and says to edit the file directly.",
    # The three members of a state machine. SimulatorEditor renders the block
    # read-only (AdvancedSimBlockSummary: count + names + a pointer at the
    # YAML view), which is the deliberate choice for advanced simulator
    # behavior — so no member of it has a control.
    "simulatorSection.transitions":
        "Deliberate: simulator state machines have no form UI. The "
        "editor shows them read-only; they are authored in YAML.",
    # `states` and `initial`, the machine's other two members, are deliberate
    # for the same reason but are NOT listed: each is declared in one block
    # only, so it takes the unscoped grep and passes on an unrelated file
    # (`states` and `initial` are both ordinary words in the UI Builder). An
    # opt-out for something the check believes is covered is stale the day it
    # is written, and the staleness test rejects it — correctly.
}

# Node keys whose values are themselves nodes, and the combinator keys whose
# values are tuples of nodes. Mirrors contract_gen._node_to_schema's recursion
# so the walk sees exactly what the generator renders.
_CHILD_NODE_KEYS = ("extra", "prop_names", "items")
_COMBINATOR_KEYS = ("one_of", "any_of", "all_of")


def _walk(
    node: object,
    block: str | None,
    path: str,
    fields: dict[tuple[str | None, str], set[str]],
    seen: frozenset[tuple[str, str]],
) -> None:
    """Collect ``(block, field) -> {contract paths}`` from one node, depth-first.

    Names come only from the keys of a ``fields`` dict. Every other key in a
    node is registry metadata ('type', 'doc', 'enum', ...) and naming one of
    those is not declaring a field — which matters, because 'raw' is both a
    node key (a raw schema fragment) and a real command field (send the string
    unframed).

    Following a ``$ref`` switches the current block, so a field is attributed
    to the block that declares it however many places reference that block.
    ``seen`` guards the self-referential shapes (a child set inside a response
    inside a child set) that would otherwise recurse forever.
    """
    if not isinstance(node, dict):
        return
    ref = node.get("ref")
    if isinstance(ref, str):
        if (ref, path) not in seen:
            _walk(spec.DEFS.get(ref), ref, path, fields, seen | {(ref, path)})
        return
    declared = node.get("fields")
    if isinstance(declared, dict):
        for name, child in declared.items():
            sub = f"{path}.{name}" if path else name
            fields.setdefault((block, name), set()).add(sub)
            _walk(child, block, sub, fields, seen)
    for key in _CHILD_NODE_KEYS:
        if key in node:
            suffix = "[]" if key == "items" else ".*"
            _walk(node[key], block, f"{path}{suffix}", fields, seen)
    for key in _COMBINATOR_KEYS:
        for branch in node.get(key, ()):
            _walk(branch, block, path, fields, seen)
    # A raw fragment can declare properties the registry keys cannot model:
    # push:'s per-type branches narrow the legal key set that way.
    raw = node.get("raw")
    if isinstance(raw, dict):
        for fragment in (raw, raw.get("if"), raw.get("then")):
            if isinstance(fragment, dict):
                properties = fragment.get("properties")
                if isinstance(properties, dict):
                    for name in properties:
                        sub = f"{path}.{name}" if path else name
                        fields.setdefault((block, name), set()).add(sub)


@lru_cache(maxsize=1)
def _contract_fields() -> dict[tuple[str | None, str], frozenset[str]]:
    """Every field the contract declares, keyed by ``(block, name)``.

    The value is the set of contract paths that field is reachable at — one
    entry may have several when more than one node ``$ref``s its block.
    Walking starts from FIELDS only: DEFS entries are reached through the
    refs that use them, which is what attributes each field to its own block
    and gives it a real path rather than a bare name.
    """
    fields: dict[tuple[str | None, str], set[str]] = {}
    for name, node in spec.FIELDS.items():
        fields.setdefault((None, name), set()).add(name)
        _walk(node, None, name, fields, frozenset())
    return {key: frozenset(paths) for key, paths in fields.items()}


@lru_cache(maxsize=1)
def _contract_field_names() -> frozenset[str]:
    """Every field name the contract declares, flattened across blocks.

    Kept for the prose floors, which ask whether the guides *document* a
    field: prose names a field, it does not sit at a path, so the name is the
    right unit there. This check itself uses _contract_fields() — collapsing
    positions is precisely what it must not do.
    """
    return frozenset(name for _block, name in _contract_fields())


def _key(block: str | None, name: str) -> str:
    """The opt-out key for a field: '<block>.<name>', bare for top-level."""
    return f"{block}.{name}" if block else name


@lru_cache(maxsize=1)
def _anchors_by_block() -> dict[str | None, frozenset[str]]:
    """Per block, the field names declared in that block and no other.

    These are what make a match in a file mean something: a file that
    implements a block names several of its fields, so a distinctive one
    appearing alongside is evidence the match is about this block rather than
    a word that happens to be spelled the same.
    """
    per_block: dict[str | None, set[str]] = {}
    blocks_by_name: dict[str, set[str | None]] = {}
    for block, name in _contract_fields():
        per_block.setdefault(block, set()).add(name)
        blocks_by_name.setdefault(name, set()).add(block)
    return {
        block: frozenset(n for n in names if len(blocks_by_name[n]) == 1)
        for block, names in per_block.items()
    }


@lru_cache(maxsize=1)
def _runtime_source() -> tuple[str, ...]:
    """One string per file — scoping asks whether a SINGLE file holds both
    the field and a distinctive sibling, which a concatenated blob cannot
    answer."""
    paths = [
        path
        for root in RUNTIME_ROOTS
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if path.relative_to(REPO_ROOT).as_posix() not in NOT_AN_IMPLEMENTATION
    ]
    assert len(paths) > 100, "runtime source went missing — the search is wrong"
    return tuple(path.read_text(encoding="utf-8") for path in paths)


@lru_cache(maxsize=1)
def _frontend_source() -> tuple[str, ...]:
    paths = [
        path
        for path in sorted((REPO_ROOT / FRONTEND_ROOT).rglob("*.ts*"))
        if path.name != GENERATED_TYPES
    ]
    assert len(paths) > 100, "IDE source went missing — the search is wrong"
    return tuple(path.read_text(encoding="utf-8") for path in paths)


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


# An anchor has to be rare in the source to locate anything. A field name
# that is also ordinary vocabulary appears everywhere and would wave any file
# through: `trim` is a JavaScript string method and matches 28% of the IDE's
# files, which is precisely how `params.*.trim` used to pass on nothing. So a
# name matching more of the source than this is not usable as an anchor —
# measured against the source rather than guessed at, so it self-corrects as
# the code changes.
_ANCHOR_MAX_FILE_SHARE = 0.10


@lru_cache(maxsize=1)
def _too_common_to_anchor() -> frozenset[str]:
    """Field names that are ordinary vocabulary in the source they'd anchor in.

    Always calibrated against the FULL runtime and IDE corpora, never against
    whatever subset a caller passes — the question "is this word rare?" is a
    property of the codebase, not of the files currently being examined. A
    name too common in either half is dropped from anchors in both: it is
    weak evidence wherever it appears.
    """
    names = {name for _block, name in _contract_fields()}
    common: set[str] = set()
    for corpus in (_runtime_source(), _frontend_source()):
        limit = len(corpus) * _ANCHOR_MAX_FILE_SHARE
        common |= {
            name
            for name in names
            if sum(1 for src in corpus if _mentions(src, name)) > limit
        }
    return frozenset(common)


@lru_cache(maxsize=1)
def _usable_anchors_by_block() -> dict[str | None, frozenset[str]]:
    """Distinctive siblings that are also rare enough to mean something."""
    common = _too_common_to_anchor()
    return {
        block: frozenset(names - common)
        for block, names in _anchors_by_block().items()
    }


@lru_cache(maxsize=1)
def _blocks_by_name() -> dict[str, frozenset[str | None]]:
    """Which blocks declare each field name."""
    out: dict[str, set[str | None]] = {}
    for block, name in _contract_fields():
        out.setdefault(name, set()).add(block)
    return {name: frozenset(blocks) for name, blocks in out.items()}


def _implemented(sources: tuple[str, ...], block: str | None, name: str) -> bool:
    """Is this field, at its declared position, implemented in ``sources``?

    Scoping is applied where it does work and nowhere else. A name the
    registry declares in only ONE block is unambiguous — there is no other
    contract field it could be confused with, so a plain match settles it,
    exactly as this check always did. A name declared in SEVERAL blocks is
    the ambiguous case this exists for, and there a match only counts when
    the same file also mentions a usable distinctive sibling of the block.

    Requiring an anchor everywhere was measured and rejected: it failed three
    fields that are demonstrably implemented (``commands.*.sets`` in
    yaml_auto, ``device_settings.*.state_key`` in device_manager) purely
    because the implementing file happens to name no distinctive sibling.
    Paying in false alarms for ambiguity that isn't there is how a check like
    this gets muted.
    """
    if len(_blocks_by_name()[name]) == 1:
        return any(_mentions(src, name) for src in sources)
    anchors = _usable_anchors_by_block().get(block, frozenset()) - {name}
    if not anchors:
        return any(_mentions(src, name) for src in sources)
    return any(
        _mentions(src, name) and any(_mentions(src, a) for a in anchors)
        for src in sources
    )


def _unimplemented(
    sources: tuple[str, ...],
    opt_out: dict[str, str],
    blind: dict[str, str],
) -> list[str]:
    """Failing fields, reported as the contract paths an author would write."""
    missing: list[str] = []
    for (block, name), paths in _contract_fields().items():
        key = _key(block, name)
        if key in opt_out or key in blind:
            continue
        if not _implemented(sources, block, name):
            missing.append(" / ".join(sorted(paths)))
    return sorted(missing)


# Sweeps every registry field over the runtime's source; the suite default of
# 120s is not enough headroom on GitHub's macOS runners, which run this several
# times slower than Linux and killed two release runs at exactly this line.
@pytest.mark.timeout(300)
def test_every_contract_field_is_read_by_the_runtime() -> None:
    missing = _unimplemented(
        _runtime_source(), RUNTIME_OPT_OUT, RUNTIME_BLIND_SPOTS
    )
    assert not missing, (
        f"declared in the driver contract but never read by the runtime: "
        f"{missing}. Implement the field, or — if only an authoring or "
        f"display surface is meant to own it — add it to RUNTIME_OPT_OUT "
        f"with the reason."
    )


def test_every_contract_field_has_an_editor() -> None:
    missing = _unimplemented(
        _frontend_source(), FRONTEND_OPT_OUT, FRONTEND_BLIND_SPOTS
    )
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
    by_key = {_key(b, n): (b, n) for b, n in _contract_fields()}
    stale_runtime = sorted(
        key for key in RUNTIME_OPT_OUT
        if key in by_key and _implemented(_runtime_source(), *by_key[key])
    )
    stale_frontend = sorted(
        key for key in FRONTEND_OPT_OUT
        if key in by_key and _implemented(_frontend_source(), *by_key[key])
    )
    assert not stale_runtime, (
        f"now read by the runtime — delete from RUNTIME_OPT_OUT: {stale_runtime}"
    )
    assert not stale_frontend, (
        f"now editable in the IDE — delete from FRONTEND_OPT_OUT: {stale_frontend}"
    )


def test_scoping_blind_spots_are_still_blind() -> None:
    """A blind spot may only shrink, and only for the stated reason.

    An entry claims two things: the field IS implemented, and this check
    cannot see it. The second is checkable — if scoping starts finding it
    (the implementing file gains a distinctive sibling, or the field stops
    being ambiguous), the entry is obsolete and must go rather than sit
    there excusing a field that no longer needs excusing.
    """
    by_key = {_key(b, n): (b, n) for b, n in _contract_fields()}
    for label, blind, sources in (
        ("RUNTIME_BLIND_SPOTS", RUNTIME_BLIND_SPOTS, _runtime_source()),
        ("FRONTEND_BLIND_SPOTS", FRONTEND_BLIND_SPOTS, _frontend_source()),
    ):
        visible = sorted(
            key
            for key in blind
            if key in by_key and _implemented(sources, *by_key[key])
        )
        assert not visible, (
            f"no longer a blind spot — this check now finds it, so delete "
            f"from {label}: {visible}"
        )


def test_opt_out_lists_name_only_real_fields() -> None:
    keys = {_key(block, name) for block, name in _contract_fields()}
    named = (set(RUNTIME_OPT_OUT) | set(FRONTEND_OPT_OUT)
             | set(RUNTIME_BLIND_SPOTS) | set(FRONTEND_BLIND_SPOTS))
    unknown = sorted(named - keys)
    assert not unknown, (
        f"opt-out lists name fields the contract does not declare (renamed, "
        f"removed, or in a different block now?): {unknown}. Keys are "
        f"'<block>.<field>', or a bare name for a top-level driver field."
    )


def test_the_walk_reaches_every_depth_of_the_registry() -> None:
    """Guard the extractor itself.

    Everything above passes vacuously if the walk stops returning names, and
    a nesting change in the registry could do that silently. These are one
    known field per shape the walk has to descend through.
    """
    fields = _contract_fields()
    names = {name for _block, name in fields}
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


def test_the_walk_separates_a_name_declared_in_more_than_one_block() -> None:
    """The property the whole check rests on.

    ``secret`` is declared on a config-schema entry and on a command
    parameter. If those collapse into one entry, an implementation of either
    satisfies both — which is exactly how a masked command parameter stayed
    invisible here while nine drivers shipped one.
    """
    fields = _contract_fields()
    blocks = sorted(b for b, n in fields if n == "secret" and b)
    assert blocks == ["configSchemaEntry", "paramEntry"], blocks

    param_paths = fields[("paramEntry", "secret")]
    assert param_paths == frozenset({
        "actions[].params.*.secret",
        "commands.*.params.*.secret",
    }), param_paths
    # ...and one obligation, not two: both paths $ref the same block, so one
    # editor covers them.
    assert ("paramEntry", "secret") in fields


def test_evidence_is_scoped_to_the_block_that_declares_the_field() -> None:
    """A distinctive sibling must be present for a match to count."""
    param_anchors = _anchors_by_block()["paramEntry"]
    assert "options_state" in param_anchors, sorted(param_anchors)[:10]

    # A file that says "secret" and nothing else about params is not evidence
    # for paramEntry.secret; add a distinctive sibling and it becomes evidence.
    assert not _implemented(('const secret = 1;',), "paramEntry", "secret")
    assert _implemented(
        ('def.secret; def.options_state;',), "paramEntry", "secret"
    )
