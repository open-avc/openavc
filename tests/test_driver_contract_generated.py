"""The generated driver-contract artifacts must match the registry.

The field registry in ``server/drivers/spec.py`` is the single source of
the .avcdriver contract. ``python -m server.drivers.contract_gen`` renders
it into three committed artifacts: the published JSON Schema, its
Python-driver variant, and the Programmer IDE's generated types. These
tests re-render each artifact and compare byte-for-byte, so editing the
registry without regenerating (or hand-editing a generated file) fails CI
instead of drifting the surfaces apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.api.models import DriverDefinitionRequest
from server.drivers import spec
from server.drivers.contract_gen import artifacts, build_schema

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "rel_path",
    [
        "server/drivers/avcdriver.schema.json",
        "server/drivers/pythondriver.schema.json",
        "web/programmer/src/api/types.gen.ts",
    ],
)
def test_committed_artifact_matches_registry(rel_path: str) -> None:
    rendered = artifacts(REPO_ROOT)
    path = REPO_ROOT / rel_path
    assert path.is_file(), f"{rel_path} is missing — run the generator"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == rendered[path], (
        f"{rel_path} does not match the registry — regenerate with "
        f"'python -m server.drivers.contract_gen' (never edit it by hand)"
    )


def test_python_variant_differs_only_in_the_python_tier() -> None:
    """The Python-driver schema is the YAML schema plus exactly the
    Python-tier widenings: ssh/mqtt transports and kind:"setup" actions."""
    yaml_schema = build_schema("yaml")
    python_schema = build_schema("python")

    t = yaml_schema["properties"]["transport"]
    t["enum"] = t["enum"] + list(spec.PYTHON_ONLY_TRANSPORTS)
    kind = yaml_schema["$defs"]["actionEntry"]["properties"]["kind"]
    kind["enum"] = list(spec.ACTION_KINDS)
    yaml_schema["$id"] = python_schema["$id"]
    yaml_schema["title"] = python_schema["title"]
    yaml_schema["description"] = python_schema["description"]

    assert yaml_schema == python_schema


def test_yaml_schema_required_matches_the_tier_tables() -> None:
    schema = build_schema("yaml")
    assert set(spec.REQUIRED_FIELDS) | set(spec.CATALOG_REQUIRED_FIELDS) == set(
        schema["required"]
    )
    # Platform-required fields are exactly the loader's REQUIRED_FIELDS.
    platform = [n for n, node in spec.FIELDS.items() if node.get("req") == "platform"]
    assert tuple(platform) == spec.REQUIRED_FIELDS


def test_push_tables_agree() -> None:
    assert set(spec.PUSH_TYPE_KEYS) == set(spec.PUSH_TYPE_REQUIRED_KEYS)
    for ptype, required in spec.PUSH_TYPE_REQUIRED_KEYS.items():
        assert set(required) <= spec.PUSH_TYPE_KEYS[ptype], ptype


def test_schema_is_valid_json_and_declares_every_registry_field() -> None:
    doc = json.loads(
        (REPO_ROOT / "server/drivers/avcdriver.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(doc["properties"]) == set(spec.FIELDS)
    assert set(doc["$defs"]) == set(spec.DEFS)


def test_api_model_declares_only_registry_fields() -> None:
    """Every explicit field on the driver-save request model must exist in
    the registry (no phantom or renamed fields), and the model must allow
    extras so undeclared registry fields survive the round-trip to disk."""
    declared = set(DriverDefinitionRequest.model_fields)
    unknown = declared - set(spec.FIELDS)
    assert not unknown, f"model fields not in the contract registry: {sorted(unknown)}"
    assert DriverDefinitionRequest.model_config.get("extra") == "allow"
