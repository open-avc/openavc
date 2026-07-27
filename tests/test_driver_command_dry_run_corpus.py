"""Every shipped driver's commands preview as the bytes they send.

A corpus-wide platform-contract sweep: the dry run builds each command with
the real runtime, so for the whole shipped library the preview and the wire
are the same thing by construction. This proves it by measurement rather than
by inspection, and it is the standing guard against the preview ever being
re-derived somewhere else — a second implementation would show up here as a
placeholder the wire resolved and the preview did not.

The sweep names no product and asserts nothing device-specific: it reads the
declared commands, supplies values their own schemas accept, and checks the
result against the format-spec rules the substituter documents. It resolves
the library through OPENAVC_DRIVERS_ROOT, falling back to the workspace
sibling, and a run that promised the corpus fails instead of collecting an
empty sweep.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from server.api.models import TestCommandRequest
from server.api.routes.drivers import _dry_run_command
from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_DEFINITIONS = OPENAVC_ROOT / "server" / "drivers" / "definitions"

DRIVERS_ROOT = Path(
    os.environ.get("OPENAVC_DRIVERS_ROOT")
    or OPENAVC_ROOT.parent / "openavc-drivers"
)

# {name} or {name:spec} — the substituter's own placeholder grammar.
PLACEHOLDER = re.compile(r"\{(\w+)(?::([^{}]*))?\}")

# Fields of a command that the send path substitutes and puts on the wire.
TEMPLATE_FIELDS = ("send", "address", "path", "body")


def _discover_driver_files() -> list[Path]:
    """Every shipped .avcdriver: the built-in generics plus the library."""
    files = sorted(BUILTIN_DEFINITIONS.rglob("*.avcdriver"))
    if DRIVERS_ROOT.exists():
        files += sorted(DRIVERS_ROOT.rglob("*.avcdriver"))
    return files


DRIVER_FILES = _discover_driver_files()


def _templates(cmd: dict[str, Any]) -> list[str]:
    """Every substituted string in one command definition."""
    out = [cmd[f] for f in TEMPLATE_FIELDS if isinstance(cmd.get(f), str)]
    for arg in cmd.get("args") or []:
        if isinstance(arg, dict) and isinstance(arg.get("value"), str):
            out.append(arg["value"])
    for key in ("query_params", "headers"):
        block = cmd.get(key)
        if isinstance(block, dict):
            out += [v for v in block.values() if isinstance(v, str)]
    return out


def _synthesize(pdef: Any) -> Any:
    """A value the command's own param schema accepts, or None if unclear.

    Deliberately conservative: a param this can't satisfy is simply not
    supplied, leaving its placeholder unresolved — which is what the runtime
    does too, so the command still previews and the assertions below only
    ever speak about params that were supplied.
    """
    if not isinstance(pdef, dict):
        return None
    if pdef.get("default") is not None:
        return pdef["default"]

    options = pdef.get("values")
    if isinstance(options, list) and options:
        first = options[0]
        return first.get("value") if isinstance(first, dict) else first

    ptype = pdef.get("type", "string")
    if ptype in ("integer", "number", "float"):
        low, high = pdef.get("min"), pdef.get("max")
        if isinstance(low, (int, float)):
            value = low
        elif isinstance(high, (int, float)):
            value = min(1, high)
        else:
            value = 1
        return int(value) if ptype == "integer" else value
    if ptype == "boolean":
        return True
    if pdef.get("pattern"):
        # Only supply a string when the declared regex actually accepts it.
        for candidate in ("1", "A", "test"):
            try:
                if re.fullmatch(pdef["pattern"], candidate):
                    return candidate
            except re.error:
                return None
        return None
    return "1"


def _command_params(cmd: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name, pdef in (cmd.get("params") or {}).items():
        value = _synthesize(pdef)
        if value is not None:
            params[name] = value
    return params


def _previewable_commands(doc: dict[str, Any]) -> list[tuple[str, dict]]:
    """Commands the Builder can preview: declared, and not bridge-routed.

    An `ir:` command emits through a bound bridge rather than a transport of
    its own, so there is no wire for this endpoint to record.
    """
    out = []
    for name, cmd in (doc.get("commands") or {}).items():
        if isinstance(cmd, dict) and not cmd.get("ir"):
            out.append((name, cmd))
    return out


async def _preview(doc: dict[str, Any], name: str, params: dict) -> dict:
    return await _dry_run_command(
        TestCommandRequest(
            host="192.0.2.10",
            port=1 if doc.get("transport") != "serial" else "/dev/null",
            transport=doc.get("transport", "tcp"),
            definition=doc,
            command_name=name,
            params=params,
            dry_run=True,
        )
    )


@gates.skipif_missing(
    gates.DRIVER_CORPUS,
    None if len(DRIVER_FILES) > 4 else f"no community drivers found at {DRIVERS_ROOT}",
)
@pytest.mark.parametrize("driver_path", DRIVER_FILES, ids=lambda p: p.name)
async def test_every_command_previews_as_the_runtime_builds_it(driver_path: Path):
    """Each command previews, and resolves every placeholder it was given.

    A placeholder still standing for a param that was supplied means the
    preview stopped being the send.
    """
    doc = yaml.safe_load(driver_path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{driver_path.name} is not a YAML mapping"

    failures: list[str] = []
    previewed = 0

    for name, cmd in _previewable_commands(doc):
        params = _command_params(cmd)
        result = await _preview(doc, name, params)
        if not result["success"]:
            failures.append(f"{name}: {result['error']}")
            continue
        previewed += 1

        shown = " ".join(
            str(part)
            for part in (result.get("wire"), result.get("body"), result.get("headers"))
            if part
        )
        for template in _templates(cmd):
            for match in PLACEHOLDER.finditer(template):
                if match.group(1) not in params:
                    continue  # unsupplied: the runtime leaves it standing too
                if match.group(0) in shown:
                    failures.append(
                        f"{name}: {match.group(0)} survived into the preview "
                        f"({shown!r}) — the preview is no longer the send"
                    )

    assert not failures, f"{driver_path.name}:\n  " + "\n  ".join(failures)
    assert previewed or not _previewable_commands(doc), (
        f"{driver_path.name}: no command previewed"
    )


def _format_spec_commands(path: Path) -> list[tuple[str, dict, str, str]]:
    """(command, definition, param, spec) for each format spec in a command."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        return []
    found = []
    for name, cmd in _previewable_commands(doc):
        for template in _templates(cmd):
            for match in PLACEHOLDER.finditer(template):
                if match.group(2):
                    found.append((name, doc, match.group(1), match.group(2)))
    return found


def _drivers_using_format_specs() -> list[Path]:
    return [p for p in DRIVER_FILES if _format_spec_commands(p)]


FORMAT_SPEC_DRIVERS = _drivers_using_format_specs()


@gates.skipif_missing(
    gates.DRIVER_CORPUS,
    None if len(DRIVER_FILES) > 4 else f"no community drivers found at {DRIVERS_ROOT}",
)
@pytest.mark.parametrize(
    "driver_path", FORMAT_SPEC_DRIVERS, ids=lambda p: p.name
)
async def test_format_specs_render_formatted_not_verbatim(driver_path: Path):
    """The case the deleted TypeScript preview got wrong, over real drivers.

    `{level:02X}` has to reach the preview as the runtime formats it. The
    oracle is Python's own format(), which is what the substituter calls —
    not a second parser of the spec grammar.

    The driver list is derived from the corpus at collection time, so a new
    driver using a format spec is covered the day it lands.
    """
    failures: list[str] = []
    for name, doc, param, spec in _format_spec_commands(driver_path):
        cmd = doc["commands"][name]
        params = _command_params(cmd)
        if param not in params:
            continue
        result = await _preview(doc, name, params)
        assert result["success"], f"{name}: {result['error']}"

        shown = " ".join(
            str(part)
            for part in (result.get("wire"), result.get("body"))
            if part
        )
        try:
            expected = format(params[param], spec)
        except (ValueError, TypeError):
            continue  # the driver's own spec/type pairing, not our business
        if expected not in shown:
            failures.append(
                f"{name}: expected {{{param}:{spec}}} to render as {expected!r}, "
                f"preview was {shown!r}"
            )
    assert not failures, f"{driver_path.name}:\n  " + "\n  ".join(failures)


@gates.skipif_missing(
    gates.DRIVER_CORPUS,
    None if len(DRIVER_FILES) > 4 else f"no community drivers found at {DRIVERS_ROOT}",
)
def test_the_format_spec_sweep_actually_covers_something():
    """A derived list that quietly became empty would pass every case above."""
    assert FORMAT_SPEC_DRIVERS, (
        "no shipped driver uses a format spec in a command — either the "
        "corpus changed or the detection above stopped working"
    )
