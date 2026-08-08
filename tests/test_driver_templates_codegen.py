"""Regression tests for the Python driver scaffolding templates (driverTemplates.ts).

The templates run in the Programmer IDE (Create Python Driver dialog), so the
generated sources come from bundling the real driverTemplates.ts with the
esbuild already in openavc/web/programmer/node_modules
(tests/fixtures/driver_templates_harness.cjs). Each generated source is then
compiled here and its DRIVER_INFO parsed from the AST, proving the
scaffolding is valid Python with the intended metadata — not just
string-matched. Skips when the Node toolchain is absent rather than failing
the Python-only CI gate.

Covers the audit findings fixed in the driverTemplates.ts group:
  free-text name/manufacturer are escaped so quotes/backslashes/newlines
  survive as inert text (source compiles, values round-trip exactly); the
  minimal template emits a config block matched to the selected transport
  (string device path for serial, listen_port for OSC, ssl for HTTP) instead
  of TCP's host + port 23 for everything; the OSC template exposes
  poll_interval in config_schema so the device dialog can edit it; the OSC
  template's class name gets a single Driver suffix.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "driver_templates_harness.cjs"
TEMPLATES = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "scripts"
    / "driverTemplates.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"

TEMPLATE_IDS = ["tcp", "http", "serial", "polling", "minimal", "osc"]

# Must match HOSTILE in tests/fixtures/driver_templates_harness.cjs.
HOSTILE_NAME = 'Acme "Pro" \\ Series """ x\nLine2'
HOSTILE_MANUFACTURER = 'O"Corp\\'


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "driver templates harness missing"
    if not TEMPLATES.is_file():
        return "driverTemplates.ts missing"
    return None


@pytest.fixture(scope="module")
def generated() -> dict[str, str]:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(TEMPLATES)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"driver templates harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"harness printed invalid JSON: {e}\n{proc.stdout[:500]}")


def _driver_info(source: str) -> dict:
    """Parse DRIVER_INFO out of generated source via the AST (literals only)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DRIVER_INFO":
                    return ast.literal_eval(node.value)
    raise AssertionError("DRIVER_INFO assignment not found in generated source")


def _class_name(source: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            return node.name
    raise AssertionError("no class definition in generated source")


def test_every_generated_source_compiles(generated):
    for scenario, source in generated.items():
        try:
            compile(source, f"<{scenario}>", "exec")
        except SyntaxError as e:
            raise AssertionError(f"{scenario} generated invalid Python: {e}")


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_hostile_free_text_round_trips(generated, template_id):
    info = _driver_info(generated[f"{template_id}__hostile"])
    assert info["name"] == HOSTILE_NAME
    assert info["manufacturer"] == HOSTILE_MANUFACTURER


def test_minimal_template_serial_config(generated):
    info = _driver_info(generated["minimal__serial"])
    cfg = info["default_config"]
    schema = info["config_schema"]
    assert isinstance(cfg["port"], str), "serial port is a device path, not a number"
    assert "host" not in cfg
    assert "baudrate" in cfg
    assert schema["port"]["type"] == "string"
    assert "baudrate" in schema


def test_minimal_template_udp_config(generated):
    info = _driver_info(generated["minimal__udp"])
    assert "host" in info["default_config"]
    assert "port" in info["default_config"]
    assert info["config_schema"]["port"]["type"] == "integer"


def test_minimal_template_osc_config(generated):
    info = _driver_info(generated["minimal__osc"])
    assert "listen_port" in info["default_config"]
    assert "listen_port" in info["config_schema"]


def test_minimal_template_http_config(generated):
    info = _driver_info(generated["minimal__http"])
    assert info["default_config"]["ssl"] is False
    assert info["config_schema"]["ssl"]["type"] == "boolean"


def test_minimal_template_tcp_config_unchanged(generated):
    info = _driver_info(generated["minimal__tcp"])
    assert info["default_config"] == {"host": "", "port": 23}
    assert info["config_schema"]["host"]["required"] is True


def test_minimal_template_stamps_selected_transport(generated):
    for transport in ["tcp", "serial", "http", "udp", "osc"]:
        info = _driver_info(generated[f"minimal__{transport}"])
        assert info["transport"] == transport


def test_osc_template_poll_interval_editable(generated):
    info = _driver_info(generated["osc__normal"])
    schema = info["config_schema"]
    assert "poll_interval" in schema, "device dialog renders from config_schema"
    assert schema["poll_interval"]["default"] == info["default_config"]["poll_interval"]


def test_class_name_single_driver_suffix(generated):
    for template_id in TEMPLATE_IDS:
        assert _class_name(generated[f"{template_id}__normal"]) == "AcmeWidgetDriver"
