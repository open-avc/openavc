"""The firewall-sync helper keeps ufw/firewalld in step with configured ports.

Exercises installer/firewall-sync.sh in --dry-run mode: desired-port
computation from system.json, add/remove diffing against the state file, and
backend gating. Platform-level — no firewall is touched. Needs a bash
interpreter (present on Linux CI and on Windows runners via Git Bash;
skipped otherwise).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests import gates

SCRIPT = Path(__file__).resolve().parents[1] / "installer" / "firewall-sync.sh"
BASH = shutil.which("bash")

pytestmark = gates.skipif_missing(
    gates.BASH, None if BASH else "bash not available"
)


def run_sync(tmp_path, config=None, state=None, backend="ufw"):
    """Run the helper in --dry-run mode; return (fields, planned_commands)."""
    if config is not None:
        (tmp_path / "system.json").write_text(
            config if isinstance(config, str) else json.dumps(config)
        )
    if state is not None:
        (tmp_path / ".firewall_ports").write_text(state)
    result = subprocess.run(
        [BASH, str(SCRIPT), str(tmp_path), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "FIREWALL_SYNC_BACKEND": backend,
            "PYTHON": sys.executable,
        },
    )
    # ExecStartPre contract: the helper must NEVER exit non-zero.
    assert result.returncode == 0, result.stderr
    fields = {}
    planned = []
    for line in result.stdout.splitlines():
        if line.startswith("WOULD RUN: "):
            planned.append(line.removeprefix("WOULD RUN: "))
        elif "=" in line:
            key, _, value = line.partition("=")
            fields[key] = value
    return fields, planned


def test_defaults_open_http_port_only(tmp_path):
    fields, planned = run_sync(tmp_path)  # no system.json at all
    assert fields["DESIRED"] == "8080"
    assert fields["ADD"] == "8080"
    assert fields["REMOVE"] == ""
    assert planned == ["ufw allow 8080/tcp comment OpenAVC (managed)"]


def test_tls_enabled_adds_tls_port(tmp_path):
    fields, _ = run_sync(tmp_path, config={"tls": {"enabled": True, "port": 9443}})
    assert fields["DESIRED"] == "8080 9443"


def test_port80_redirect_adds_port_80(tmp_path):
    fields, _ = run_sync(
        tmp_path,
        config={"network": {"port80_redirect": True}, "tls": {"enabled": True}},
    )
    assert fields["DESIRED"] == "80 8080 8443"


def test_custom_http_port_honored(tmp_path):
    fields, _ = run_sync(tmp_path, config={"network": {"http_port": 9090}})
    assert fields["DESIRED"] == "9090"


def test_disabled_features_close_previously_opened_ports(tmp_path):
    """Ports the helper opened earlier are removed once no longer configured;
    ports it never opened are left alone (admin rules are not ours to touch)."""
    fields, planned = run_sync(
        tmp_path,
        config={},
        state="8080 8443 80",
        backend="firewalld",
    )
    assert fields["ADD"] == ""
    assert set(fields["REMOVE"].split()) == {"8443", "80"}
    assert "firewall-cmd --permanent --remove-port=8443/tcp" in planned
    assert "firewall-cmd --permanent --remove-port=80/tcp" in planned
    assert "firewall-cmd --reload" in planned
    assert not any("--add-port" in cmd for cmd in planned)


def test_in_sync_plans_nothing(tmp_path):
    fields, planned = run_sync(tmp_path, config={}, state="8080")
    assert fields["ADD"] == ""
    assert fields["REMOVE"] == ""
    assert planned == []


def test_no_active_firewall_is_a_noop(tmp_path):
    fields, planned = run_sync(tmp_path, config={"tls": {"enabled": True}}, backend="none")
    assert fields["BACKEND"] == "none"
    assert "ADD" not in fields  # gate exits before planning any changes
    assert planned == []


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    fields, _ = run_sync(tmp_path, config="{not json", backend="ufw")
    assert fields["DESIRED"] == "8080"


def test_dry_run_leaves_no_state_file(tmp_path):
    run_sync(tmp_path, config={"tls": {"enabled": True}})
    assert not (tmp_path / ".firewall_ports").exists()
