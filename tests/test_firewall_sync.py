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


def run_sync(tmp_path, config=None, state=None, backend="ufw", plugin_ports=None):
    """Run the helper in --dry-run mode; return (fields, planned_commands)."""
    if plugin_ports is not None:
        (tmp_path / "plugin_ports.json").write_text(
            plugin_ports if isinstance(plugin_ports, str)
            else json.dumps({"ports": plugin_ports})
        )
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
    assert fields["DESIRED"] == "8080/tcp"
    assert fields["ADD"] == "8080/tcp"
    assert fields["REMOVE"] == ""
    assert planned == ["ufw allow 8080/tcp comment OpenAVC (managed)"]


def test_tls_enabled_adds_tls_port(tmp_path):
    fields, _ = run_sync(tmp_path, config={"tls": {"enabled": True, "port": 9443}})
    assert fields["DESIRED"] == "8080/tcp 9443/tcp"


def test_port80_redirect_adds_port_80(tmp_path):
    fields, _ = run_sync(
        tmp_path,
        config={"network": {"port80_redirect": True}, "tls": {"enabled": True}},
    )
    assert fields["DESIRED"] == "80/tcp 8080/tcp 8443/tcp"


def test_custom_http_port_honored(tmp_path):
    fields, _ = run_sync(tmp_path, config={"network": {"http_port": 9090}})
    assert fields["DESIRED"] == "9090/tcp"


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
    assert set(fields["REMOVE"].split()) == {"8443/tcp", "80/tcp"}
    assert "firewall-cmd --permanent --remove-port=8443/tcp" in planned
    assert "firewall-cmd --permanent --remove-port=80/tcp" in planned
    assert "firewall-cmd --reload" in planned
    assert not any("--add-port" in cmd for cmd in planned)


def test_in_sync_plans_nothing(tmp_path):
    fields, planned = run_sync(tmp_path, config={}, state="8080/tcp")
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
    assert fields["DESIRED"] == "8080/tcp"


def test_dry_run_leaves_no_state_file(tmp_path):
    run_sync(tmp_path, config={"tls": {"enabled": True}})
    assert not (tmp_path / ".firewall_ports").exists()


# ── Ports a plugin declared ──────────────────────────────────────────────────
#
# A plugin that bundles a sidecar listens on a port nothing else here knows
# about. The Video Panel's MediaMTX binds UDP 8189 for WebRTC, which is the only
# path a panel in the room has, and until it is open that panel gets no picture
# and no explanation. The server writes the declaration to plugin_ports.json;
# this is the half that acts on it.


def test_a_declared_udp_port_is_opened(tmp_path):
    fields, planned = run_sync(
        tmp_path,
        config={"network": {"http_port": 8080}},
        plugin_ports=[{"port": 8189, "protocol": "udp", "reason": "WebRTC video"}],
    )
    assert fields["DESIRED"] == "8080/tcp 8189/udp"
    assert "ufw allow 8189/udp comment OpenAVC (managed)" in planned


def test_a_declared_port_defaults_to_tcp(tmp_path):
    fields, _ = run_sync(
        tmp_path,
        config={"network": {"http_port": 8080}},
        plugin_ports=[{"port": 9999, "reason": "r"}],
    )
    assert "9999/tcp" in fields["DESIRED"]


def test_disabling_the_plugin_closes_its_port(tmp_path):
    """An empty list is how the server says "nobody needs this any more". A rule
    that outlived the plugin that asked for it is worse than never opening one."""
    fields, planned = run_sync(
        tmp_path,
        config={"network": {"http_port": 8080}},
        state="8080/tcp 8189/udp",
        plugin_ports=[],
    )
    assert fields["REMOVE"] == "8189/udp"
    assert "ufw delete allow 8189/udp" in planned


def test_a_malformed_entry_is_skipped_not_fatal(tmp_path):
    """One bad plugin manifest must not close the HTTP port."""
    fields, _ = run_sync(
        tmp_path,
        config={"network": {"http_port": 8080}},
        plugin_ports=[
            {"port": 8189, "protocol": "udp", "reason": "good"},
            {"port": 70000, "reason": "out of range"},
            {"port": "nonsense"},
            {"port": 5000, "protocol": "sctp", "reason": "not a protocol we open"},
        ],
    )
    assert fields["DESIRED"] == "8080/tcp 8189/udp"


def test_a_corrupt_plugin_ports_file_leaves_the_listeners_alone(tmp_path):
    fields, _ = run_sync(
        tmp_path,
        config={"network": {"http_port": 8080}},
        plugin_ports="{not json at all",
    )
    assert fields["DESIRED"] == "8080/tcp"


def test_a_missing_plugin_ports_file_is_normal(tmp_path):
    """Most instances have no plugin declaring a port, and never write the file."""
    fields, _ = run_sync(tmp_path, config={"network": {"http_port": 8080}})
    assert fields["DESIRED"] == "8080/tcp"


def test_a_state_file_from_before_udp_existed_is_read_as_tcp(tmp_path):
    """Bare port numbers are what the old version wrote, and they were all TCP.
    Reading them any other way makes every managed port look unmanaged: the next
    run re-adds rules that are already there and never closes the ones that
    should go."""
    fields, _ = run_sync(
        tmp_path,
        config={"network": {"http_port": 8080}, "tls": {"enabled": True, "port": 8443}},
        state="8080 8443",
    )
    assert fields["ADD"] == ""
    assert fields["REMOVE"] == ""
