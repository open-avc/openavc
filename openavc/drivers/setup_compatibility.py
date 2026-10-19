"""Canonical immutable descriptors for finite legacy setup result ingress.

This is shared by the offline resource generator and runtime action resolver.
It validates data, not installed code: eligibility additionally requires the
actual loader receipt, declared action identity and qualified dependencies.
There is no device lookup, catalog request, effect, result store or interpreter.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

from openavc.drivers.spec import SETUP_COMPATIBILITY_ROLES, SETUP_DIAGNOSTIC_TYPES

MAX_DIAGNOSTICS = 64
MAX_ITEMS = 512
MAX_SAFE_INTEGER = 2**53 - 1
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def declaration_json(value: dict) -> str:
    """The loader and descriptor generator hash exactly this representation."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def declaration_digest(value: dict) -> str:
    return hashlib.sha256(declaration_json(value).encode("utf-8")).hexdigest()


def _object(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Unsupported setup compatibility descriptor shape")
    return value


def _text(value, *, key=False):
    if type(value) is not str or not value.strip() or len(value) > 160 or (key and not _KEY.fullmatch(value)):
        raise ValueError("Invalid setup compatibility name")
    return value


def _digest(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ValueError("An exact SHA-256 identity is required")
    return value


def checked_value(value, kind: str, *, nullable=False):
    """Strict, bounded diagnostic primitives; boolean is never a count."""
    if value is None and nullable:
        return None
    if kind == "string" and type(value) is str:
        return value
    if kind == "boolean" and type(value) is bool:
        return value
    if kind == "integer" and type(value) is int and 0 <= value <= MAX_SAFE_INTEGER:
        return value
    if kind == "number" and type(value) in (int, float) and abs(value) <= MAX_SAFE_INTEGER and math.isfinite(value):
        return value
    if kind == "strings" and type(value) is list and len(value) <= MAX_ITEMS and all(type(v) is str for v in value):
        return tuple(value)
    raise ValueError("Setup result does not match its qualified field type")


@dataclass(frozen=True)
class DiagnosticField:
    key: str
    label: str
    kind: str
    required: bool
    nullable: bool

    def read(self, result: dict):
        if self.key not in result:
            if self.required:
                raise ValueError("Required setup diagnostic field is absent")
            return False, None
        return True, checked_value(result[self.key], self.kind, nullable=self.nullable)


@dataclass(frozen=True)
class SetupCompatibility:
    driver_id: str
    driver_version: str
    action_id: str
    source_sha256: str
    declaration_sha256: str
    action_sha256: str
    profile: str
    roles: tuple[tuple[str, str], ...]
    diagnostics: tuple[DiagnosticField, ...]
    generated_secret: str | None
    descriptor_sha256: str

    @classmethod
    def parse(cls, value) -> SetupCompatibility:
        _object(value, ("schema", "driver_id", "driver_version", "action_id", "source_sha256",
                        "declaration_sha256", "action_sha256", "profile", "roles", "diagnostics"),
                ("generated_secret",))
        if value["schema"] != "setup-compatibility/v1":
            raise ValueError("Unsupported setup compatibility schema")
        profile = value["profile"]
        if type(profile) is not str or profile not in SETUP_COMPATIBILITY_ROLES:
            raise ValueError("Unsupported setup compatibility profile")
        role_types = SETUP_COMPATIBILITY_ROLES[profile]
        _object(value["roles"], [r for r in role_types if not r.endswith("?")],
                [r[:-1] for r in role_types if r.endswith("?")])
        roles = tuple((role, _text(key, key=True)) for role, key in value["roles"].items())
        if len({key for _, key in roles}) != len(roles):
            raise ValueError("Distinct setup evidence roles cannot share one result field")
        fields = value["diagnostics"]
        if type(fields) is not list or len(fields) > MAX_DIAGNOSTICS:
            raise ValueError("Bounded setup diagnostics required")
        diagnostics = []
        for field in fields:
            _object(field, ("key", "label", "type", "required", "nullable"))
            if field["type"] not in SETUP_DIAGNOSTIC_TYPES or type(field["required"]) is not bool or type(field["nullable"]) is not bool:
                raise ValueError("Explicit setup diagnostic types required")
            diagnostics.append(DiagnosticField(_text(field["key"], key=True), _text(field["label"]),
                                               field["type"], field["required"], field["nullable"]))
        if len({field.key for field in diagnostics}) != len(diagnostics):
            raise ValueError("Duplicate setup diagnostic field")
        secret = value.get("generated_secret")
        if "generated_secret" in value:
            _text(secret, key=True)
            if profile != "pairing/v1" or secret in {key for _, key in roles} | {field.key for field in diagnostics}:
                raise ValueError("Generated credentials must have a separate pairing role")
        elif profile == "pairing/v1":
            raise ValueError("Pairing requires a declared generated credential")
        return cls(_text(value["driver_id"], key=True), _text(value["driver_version"]),
                   _text(value["action_id"], key=True), _digest(value["source_sha256"]),
                   _digest(value["declaration_sha256"]), _digest(value["action_sha256"]),
                   profile, roles, tuple(diagnostics), secret, declaration_digest(value))

    def read_roles(self, result: dict) -> dict:
        if type(result) is not dict:
            raise ValueError("A declared legacy setup result is required")
        types = {key.rstrip("?"): kind for key, kind in SETUP_COMPATIBILITY_ROLES[self.profile].items()}
        return {role: checked_value(result[key], types[role]) for role, key in self.roles}


def parse_descriptors(values) -> tuple[SetupCompatibility, ...]:
    """Reject ambiguous exact tuples rather than selecting the last declaration."""
    if type(values) is not list or len(values) > 4096:
        raise ValueError("A bounded setup compatibility resource is required")
    result, seen = [], set()
    for value in values:
        item = SetupCompatibility.parse(value)
        key = (item.driver_id, item.driver_version, item.action_id, item.source_sha256)
        if key in seen:
            raise ValueError("Duplicate setup compatibility tuple")
        seen.add(key)
        result.append(item)
    return tuple(result)


def descriptor_schema() -> dict:
    """Generate editor schema from the same finite profile/diagnostic vocabulary."""
    text = {"type": "string", "minLength": 1, "maxLength": 160}
    key = {**text, "pattern": "^" + _KEY.pattern.replace("\\Z", "$")}
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    required = ("schema", "driver_id", "driver_version", "action_id", "source_sha256",
                "declaration_sha256", "action_sha256", "profile", "roles", "diagnostics")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Source-qualified setup compatibility descriptor", "type": "object",
            "additionalProperties": False, "required": list(required), "properties": {
                "schema": {"const": "setup-compatibility/v1"}, "driver_id": key, "driver_version": text,
                "action_id": key, "source_sha256": digest, "declaration_sha256": digest,
                "action_sha256": digest, "profile": {"enum": list(SETUP_COMPATIBILITY_ROLES)},
                "roles": {"type": "object"}, "generated_secret": key,
                "diagnostics": {"type": "array", "maxItems": MAX_DIAGNOSTICS, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["key", "label", "type", "required", "nullable"], "properties": {
                        "key": key, "label": text, "type": {"enum": list(SETUP_DIAGNOSTIC_TYPES)},
                        "required": {"type": "boolean"}, "nullable": {"type": "boolean"}}}},
            }, "allOf": [{"if": {"properties": {"profile": {"const": name}}}, "then": {
                "properties": {"roles": {"type": "object", "additionalProperties": False,
                    "required": [r for r in roles if not r.endswith("?")],
                    "properties": {r.rstrip("?"): key for r in roles}}},
                **({"required": ["generated_secret"]} if name == "pairing/v1" else
                   {"not": {"required": ["generated_secret"]}})}}
                for name, roles in SETUP_COMPATIBILITY_ROLES.items()]}
