"""Generate the driver-contract artifacts from the spec registry.

The field registry in ``server/drivers/spec.py`` is the single source of
the .avcdriver contract. This module renders it into the committed
artifacts every other surface consumes:

* ``server/drivers/avcdriver.schema.json`` — the published JSON Schema for
  YAML (.avcdriver) driver files. The community driver repo vendors a
  byte-identical copy at its root for editor validation and catalog CI.
* ``server/drivers/pythondriver.schema.json`` — the Python-driver variant,
  identical except where the Python tier is wider (ssh/mqtt transports,
  ``kind: setup`` actions). The catalog validates extracted DRIVER_INFO
  dicts against it.
* ``web/programmer/src/api/types.gen.ts`` — the Programmer IDE's driver
  definition types and validator constant tables.

Regenerate after any spec change:

    python -m server.drivers.contract_gen

A test compares the committed artifacts against a fresh render, so a spec
edit without a regen fails CI rather than drifting.

Pure stdlib on purpose — importable anywhere the spec is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.drivers import spec

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
    for reg_key, schema_key in _DIRECT_KEYS:
        if reg_key in node:
            value = node[reg_key]
            out[schema_key] = list(value) if isinstance(value, tuple) else value
    if "enum" in node:
        enum = node["enum"]
        if tier == "python" and "python_enum" in node:
            enum = node["python_enum"]
        out["enum"] = list(enum)
    if "doc" in node:
        out["description"] = node["doc"]
    if "required" in node:
        out["required"] = list(node["required"])
    if "fields" in node:
        out["properties"] = {
            name: _node_to_schema(sub, tier) for name, sub in node["fields"].items()
        }
    if "extra" in node:
        extra = node["extra"]
        out["additionalProperties"] = (
            extra if isinstance(extra, bool) else _node_to_schema(extra, tier)
        )
    if "prop_names" in node:
        out["propertyNames"] = _node_to_schema(node["prop_names"], tier)
    if "items" in node:
        out["items"] = _node_to_schema(node["items"], tier)
    for reg_key, schema_key in (
        ("one_of", "oneOf"),
        ("any_of", "anyOf"),
        ("all_of", "allOf"),
    ):
        if reg_key in node:
            out[schema_key] = [_node_to_schema(sub, tier) for sub in node[reg_key]]
    if "not_" in node:
        out["not"] = node["not_"]
    if "raw" in node:
        for key, value in node["raw"].items():
            if key in out:
                raise ValueError(f"raw fragment overwrites emitted key {key!r}")
            out[key] = value
    return out


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
        "properties": {
            name: _node_to_schema(node, tier) for name, node in spec.FIELDS.items()
        },
        "allOf": [dict(rule) for rule in spec.CROSS_FIELD_RULES],
        "$defs": {
            name: _node_to_schema(node, tier) for name, node in spec.DEFS.items()
        },
    }


def render_schema(tier: str) -> str:
    return json.dumps(build_schema(tier), indent=2, ensure_ascii=False) + "\n"


def artifacts(repo_root: Path) -> dict[Path, str]:
    """Map of output path -> rendered content for every generated artifact."""
    from server.drivers.contract_gen_ts import render_types_ts

    return {
        repo_root / "server" / "drivers" / "avcdriver.schema.json":
            render_schema("yaml"),
        repo_root / "server" / "drivers" / "pythondriver.schema.json":
            render_schema("python"),
        repo_root / "web" / "programmer" / "src" / "api" / "types.gen.ts":
            render_types_ts(),
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
