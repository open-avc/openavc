"""Generate the driver-contract artifacts from the spec registry.

The field registry in ``openavc/drivers/spec.py`` is the single source of
the .avcdriver contract. This module renders it into the committed
artifacts every other surface consumes:

* ``openavc/drivers/avcdriver.schema.json`` — the published JSON Schema for
  YAML (.avcdriver) driver files. The community driver repo vendors a
  byte-identical copy at its root for editor validation and catalog CI.
* ``openavc/drivers/pythondriver.schema.json`` — the Python-driver variant,
  identical except where the Python tier is wider (ssh/mqtt transports,
  ``kind: setup`` actions). The catalog validates extracted DRIVER_INFO
  dicts against it.
* ``openavc/web/programmer/src/api/types.gen.ts`` — the Programmer IDE's driver
  definition types and validator constant tables.
* ``openavc/web/simulator/src/store/controls.gen.ts`` — the Simulator UI's
  ``ControlDef`` union, one interface per control type it draws
  (``contract_gen_sim_ts``).

Regenerate after any spec change:

    python -m openavc.drivers.contract_gen

A test compares the committed artifacts against a fresh render, so a spec
edit without a regen fails CI rather than drifting.

Pure stdlib on purpose — importable anywhere the spec is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openavc.drivers import spec

SCHEMA_META = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_BASE_URL = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/"

YAML_SCHEMA_TITLE = "OpenAVC Driver Definition (.avcdriver)"
YAML_SCHEMA_DESCRIPTION = (
    "The schema for OpenAVC .avcdriver device driver definitions. Use it for "
    "live validation and autocompletion in your editor while authoring "
    "drivers. The required fields and constraints match what the OpenAVC "
    "community driver catalog enforces. Loaded on its own, the OpenAVC "
    "platform requires only id, name, and transport; the remaining metadata "
    "fields (manufacturer, category, version, author, description, "
    "source_url) are required to publish a driver to the community catalog."
)
PYTHON_SCHEMA_TITLE = "OpenAVC Python Driver Metadata (DRIVER_INFO)"
PYTHON_SCHEMA_DESCRIPTION = (
    "The schema for the DRIVER_INFO metadata dict of an OpenAVC Python "
    "driver. Identical to the .avcdriver schema except where Python drivers "
    "can do more: the ssh and mqtt transports (driver-managed sessions the "
    "declarative runtime doesn't model) and kind \"setup\" actions (offline "
    "provisioning wizards backed by a run_setup_action handler). The "
    "community driver catalog validates extracted DRIVER_INFO dicts "
    "against it."
)

# Registry keys that map 1:1 onto a JSON Schema keyword, in emission order.
_DIRECT_KEYS: tuple[tuple[str, str], ...] = (
    ("type", "type"),
    ("const", "const"),
    ("pattern", "pattern"),
    ("format", "format"),
    ("min", "minimum"),
    ("max", "maximum"),
    ("emin", "exclusiveMinimum"),
    ("min_len", "minLength"),
    ("min_items", "minItems"),
    ("min_props", "minProperties"),
)
_HANDLED_KEYS = frozenset(
    {k for k, _ in _DIRECT_KEYS}
    | {
        "any", "ref", "enum", "python_enum", "doc", "req", "fields",
        "required", "extra", "prop_names", "items", "one_of", "any_of",
        "all_of", "not_", "raw",
        # Platform floors are rendered into the description (spec.node_doc)
        # rather than emitted as keywords of their own — JSON Schema has no
        # way to say "reading this field needs a newer platform".
        "since", "since_values", "yaml_since_values", "since_with",
        "enhancement_since",
    }
)


def _node_to_schema(node: dict[str, Any], tier: str) -> Any:
    """Render one registry node into its JSON Schema form."""
    unknown = set(node) - _HANDLED_KEYS
    if unknown:
        raise ValueError(f"registry node has unknown key(s) {sorted(unknown)}: {node}")
    if node.get("any") is True:
        return True
    out: dict[str, Any] = {}
    if "ref" in node:
        out["$ref"] = f"#/$defs/{node['ref']}"
    for part in (_direct, _enum_and_doc, _members, _combinations, _raw):
        part(node, tier, out)
    return out


def _direct(node: dict[str, Any], tier: str, out: dict[str, Any]) -> None:
    """The keys that map one to one onto a JSON Schema keyword."""
    for reg_key, schema_key in [k for k in _DIRECT_KEYS if k[0] in node]:
        value = node[reg_key]
        out[schema_key] = list(value) if isinstance(value, tuple) else value


def _enum_and_doc(node: dict[str, Any], tier: str, out: dict[str, Any]) -> None:
    """The value set (the tier's own, where it is wider) and the description
    the floors are rendered into."""
    enum = None
    if "enum" in node:
        enum = node.get("python_enum", node["enum"]) if tier == "python" else node["enum"]
        out["enum"] = list(enum)
    doc = spec.node_doc(node, enum, tier)
    if doc:
        out["description"] = doc
    if "required" in node:
        out["required"] = list(node["required"])


def _members(node: dict[str, Any], tier: str, out: dict[str, Any]) -> None:
    """What a block or a list holds: its fields, its other keys' values, its
    key names and its items."""
    if "fields" in node:
        out["properties"] = {
            name: _node_to_schema(sub, tier) for name, sub in node["fields"].items()
        }
    if "extra" in node:
        extra = node["extra"]
        out["additionalProperties"] = (
            extra if isinstance(extra, bool) else _node_to_schema(extra, tier)
        )
    for reg_key, schema_key in (("prop_names", "propertyNames"), ("items", "items")):
        if reg_key in node:
            out[schema_key] = _node_to_schema(node[reg_key], tier)


def _combinations(node: dict[str, Any], tier: str, out: dict[str, Any]) -> None:
    for reg_key, schema_key in (
        ("one_of", "oneOf"),
        ("any_of", "anyOf"),
        ("all_of", "allOf"),
    ):
        if reg_key in node:
            out[schema_key] = [_node_to_schema(sub, tier) for sub in node[reg_key]]
    if "not_" in node:
        out["not"] = node["not_"]


def _raw(node: dict[str, Any], tier: str, out: dict[str, Any]) -> None:
    """The escape hatch, merged in verbatim; it never overwrites a key."""
    for key, value in (node.get("raw") or {}).items():
        if key in out:
            raise ValueError(f"raw fragment overwrites emitted key {key!r}")
        out[key] = value


_JUDGES_A_NULL = frozenset({"const", "not", "allOf", "anyOf", "oneOf", "$ref", "if"})


def _takes_null(name: str, tier: str) -> bool:
    """A YAML definition reads a null as absent for this top-level field
    (``spec.NULL_AS_ABSENT``); a Python driver's DRIVER_INFO is read as
    written."""
    return tier == "yaml" and name in spec.NULL_AS_ABSENT


def _top_level(name: str, node: dict[str, Any], tier: str) -> Any:
    """A top-level field's schema, taking a null where a reader reads one as
    absent."""
    out = _node_to_schema(node, tier)
    return _with_null(out) if _takes_null(name, tier) else out


def _with_null(out: dict[str, Any]) -> dict[str, Any]:
    """``out`` taking a null too: in its own kinds when it names them, else
    (or when a keyword of its own also judges a null: a fixed value, a
    reference, a combination) whole beside a null."""
    if _judged_whole(out):
        return _beside_null(out)
    out["type"] = _also(out["type"], "null")
    if "enum" in out:
        out["enum"] = _also(out["enum"], None)
    return out


def _judged_whole(out: dict[str, Any]) -> bool:
    return "type" not in out or bool(set(out) & _JUDGES_A_NULL)


def _also(values: Any, extra: Any) -> list[Any]:
    """``values`` (one, or a list) with ``extra`` at the end, once."""
    listed = values if isinstance(values, list) else [values]
    return list(dict.fromkeys([*listed, extra]))


def _beside_null(out: dict[str, Any]) -> dict[str, Any]:
    described = {"description": out.pop("description")} if "description" in out else {}
    return {**described, "anyOf": [out, {"type": "null"}]}


def _cross_field_rule(rule: dict[str, Any], tier: str) -> dict[str, Any]:
    """One cross-field rule; in a YAML driver, a field the rule requires that
    a null reads as absent for must not be that null, so an empty one neither
    asks for nor answers the rule."""
    out = json.loads(json.dumps(rule))
    if tier != "yaml":
        return out
    for part in [p for p in (out.get("if"), out.get("then")) if isinstance(p, dict)]:
        _required_not_null(part)
    return out


def _required_not_null(part: dict[str, Any]) -> None:
    for name in [n for n in part.get("required", ()) if n in spec.NULL_AS_ABSENT]:
        held = part.setdefault("properties", {}).setdefault(name, {})
        held["not"] = {"type": "null"}


def build_schema(tier: str) -> dict[str, Any]:
    """Build the full JSON Schema document for one tier ("yaml" or "python")."""
    if tier == "yaml":
        file_name = "avcdriver.schema.json"
        title, description = YAML_SCHEMA_TITLE, YAML_SCHEMA_DESCRIPTION
    elif tier == "python":
        file_name = "pythondriver.schema.json"
        title, description = PYTHON_SCHEMA_TITLE, PYTHON_SCHEMA_DESCRIPTION
    else:
        raise ValueError(f"unknown tier: {tier!r}")

    required = [name for name, node in spec.FIELDS.items() if node.get("req")]
    return {
        "$schema": SCHEMA_META,
        "$id": SCHEMA_BASE_URL + file_name,
        "title": title,
        "description": description,
        "type": "object",
        "required": required,
        # Closed, like every $defs block: a key the registry doesn't declare is
        # a typo, and catching it in the author's editor is the earliest a typo
        # can be caught. FIELDS is the whole top-level contract, so nothing
        # legitimate lands outside it.
        "additionalProperties": False,
        "properties": {
            name: _top_level(name, node, tier) for name, node in spec.FIELDS.items()
        },
        "allOf": [_cross_field_rule(rule, tier) for rule in spec.CROSS_FIELD_RULES],
        "$defs": {name: _definition(name, node, tier) for name, node in spec.DEFS.items()},
    }


def _definition(name: str, node: dict[str, Any], tier: str) -> Any:
    """One ``$defs`` block; in a YAML driver, a block whose keys a null reads
    as absent for (``spec.NULL_AS_ABSENT_WITHIN``) lets each of them take one."""
    out = _node_to_schema(node, tier)
    if tier == "yaml" and name in spec.NULL_AS_ABSENT_WITHIN.values():
        out["properties"] = {key: _with_null(prop) for key, prop in out["properties"].items()}
    return out


def render_schema(tier: str) -> str:
    return json.dumps(build_schema(tier), indent=2, ensure_ascii=False) + "\n"


def artifacts(repo_root: Path) -> dict[Path, str]:
    """Map of output path -> rendered content for every generated artifact."""
    from openavc.drivers.contract_gen_sim_ts import render_controls_ts
    from openavc.drivers.contract_gen_ts import render_types_ts

    return {
        repo_root / "openavc" / "drivers" / "avcdriver.schema.json":
            render_schema("yaml"),
        repo_root / "openavc" / "drivers" / "pythondriver.schema.json":
            render_schema("python"),
        repo_root / "openavc" / "web" / "programmer" / "src" / "api" / "types.gen.ts":
            render_types_ts(),
        repo_root / "openavc" / "web" / "simulator" / "src" / "store" / "controls.gen.ts":
            render_controls_ts(),
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    for path, content in artifacts(repo_root).items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == content:
            print(f"  unchanged  {path.relative_to(repo_root)}")
            continue
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        print(f"  wrote      {path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
