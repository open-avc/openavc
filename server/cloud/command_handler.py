"""
OpenAVC Cloud — Incoming command handler.

Handles commands from the cloud platform: device commands, config push,
restart requests, and diagnostic requests. Delegates to the appropriate
engine subsystem and sends command_result responses.
"""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

from server.cloud.protocol import (
    COMMAND, CONFIG_PUSH, RESTART, DIAGNOSTIC, SOFTWARE_UPDATE,
    GET_PROJECT, GET_DEVICE_COMMANDS,
    COMMAND_RESULT, PROJECT_DATA, DEVICE_COMMANDS_DATA,
    DIAGNOSTIC_RESULT,
    build_command_result_payload, build_diagnostic_result_payload,
    build_project_data_payload, build_device_commands_data_payload,
    extract_payload,
)
from server.utils.logger import get_logger
from server.utils.spawn import CREATE_NO_WINDOW

if TYPE_CHECKING:
    from server.cloud.agent import CloudAgent
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus

log = get_logger(__name__)


class CommandHandler:
    """
    Handles incoming commands from the cloud and delegates to local subsystems.

    Commands are dispatched by type:
    - command: Send a command to a device via DeviceManager
    - config_push: Reload the project configuration
    - restart: Restart the OpenAVC service
    - diagnostic: Run a network diagnostic (future)
    """

    def __init__(
        self,
        agent: CloudAgent,
        devices: DeviceManager,
        events: EventBus,
        reload_fn=None,
        apply_fn=None,
        update_manager=None,
        project_path=None,
    ):
        """
        Args:
            agent: The CloudAgent to send responses through.
            devices: DeviceManager for device commands.
            events: EventBus for emitting audit events.
            reload_fn: Optional async callable to reload the project from disk
                (used by a bare config_push that carries no project_json).
            apply_fn: Optional async callable (engine.apply_project) that
                persists and reconciles a pushed project through the one seam.
            update_manager: Optional UpdateManager for cloud-triggered updates.
            project_path: Path to the active project.avc file.
        """
        self._agent = agent
        self._devices = devices
        self._events = events
        self._reload_fn = reload_fn
        self._apply_fn = apply_fn
        self._update_manager = update_manager
        self._project_path = project_path

    async def handle(self, msg: dict[str, Any]) -> None:
        """
        Route an incoming command message to the appropriate handler.

        Args:
            msg: The parsed, verified message dict.
        """
        msg_type = msg.get("type", "")
        payload = extract_payload(msg)
        request_id = payload.get("request_id", "")

        # Audit attribution: the display name plus the durable user id, so
        # local logs identify the cloud user unambiguously even if the name
        # changes or collides.
        user_id = payload.get("user_id", "")
        user_name = payload.get("user_name", "")
        actor = f"{user_name} ({user_id})" if user_id else user_name

        try:
            if msg_type == COMMAND:
                await self._handle_device_command(payload, request_id, actor)
            elif msg_type == CONFIG_PUSH:
                await self._handle_config_push(payload, request_id, actor)
            elif msg_type == RESTART:
                await self._handle_restart(payload, request_id, actor)
            elif msg_type == DIAGNOSTIC:
                await self._handle_diagnostic(payload, request_id, actor)
            elif msg_type == SOFTWARE_UPDATE:
                await self._handle_software_update(payload, request_id, actor)
            elif msg_type == GET_PROJECT:
                await self._handle_get_project(payload, request_id, actor)
            elif msg_type == GET_DEVICE_COMMANDS:
                await self._handle_get_device_commands(payload, request_id, actor)
            else:
                await self._send_result(request_id, False, error=f"Unknown command type: {msg_type}")
        except Exception as e:
            # Catch-all: isolates command processing errors; reports failure to cloud
            log.exception(f"Command handler: error processing {msg_type}")
            await self._send_result(request_id, False, error=str(e))

    async def _handle_device_command(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a device command."""
        device_id = payload.get("device_id", "")
        command = payload.get("command", "")
        params = payload.get("params", {})

        log.info(
            f"Cloud command: {actor} → {device_id}.{command}"
            f"({params})"
        )

        await self._events.emit("cloud.command", {
            "device_id": device_id,
            "command": command,
            "params": params,
            "user_name": payload.get("user_name", ""),
            "user_id": payload.get("user_id", ""),
            "request_id": request_id,
        })

        try:
            await self._devices.send_command(device_id, command, params)
            await self._send_result(request_id, True, result="OK")
        except Exception as e:
            # Catch-all: device commands can fail with transport, protocol, or driver errors
            await self._send_result(request_id, False, error=str(e))

    async def _handle_config_push(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a project config push."""
        mode = payload.get("mode", "full_replace")
        project_json = payload.get("project_json")
        log.info(f"Cloud config push: {actor} → mode={mode}")

        await self._events.emit("cloud.config_push", {
            "mode": mode,
            "user_name": payload.get("user_name", ""),
            "user_id": payload.get("user_id", ""),
            "request_id": request_id,
        })

        if (self._apply_fn or self._reload_fn) and self._project_path:
            try:
                from pathlib import Path
                from server.core.project_loader import ProjectConfig
                from server.core.project_migration import migrate_project
                from server.core.backup_manager import create_backup

                project_path = Path(self._project_path)

                # Capture previous config for rollback support
                previous_json = None
                try:
                    import json
                    if project_path.exists():
                        previous_json = json.loads(project_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, ValueError):
                    pass  # Best-effort: previous config capture is optional for rollback

                if project_json:
                    if self._apply_fn is None:
                        await self._send_result(request_id, False, error="Apply not available")
                        return
                    # Migrate an old-schema push to the current format BEFORE
                    # validating/saving, matching load_project — otherwise a
                    # pre-current-schema cloud project is persisted with stale
                    # field placement and misread until the next disk reload.
                    try:
                        migrated_json, _ = migrate_project(project_json)
                        project = ProjectConfig.model_validate(migrated_json)
                    except (ValueError, TypeError) as ve:
                        await self._send_result(request_id, False, error=f"Invalid project schema: {ve}")
                        return

                    # Create backup before overwriting
                    import asyncio
                    await asyncio.to_thread(create_backup, project_path.parent, "Before cloud config push")

                    # A fleet push wins by design — no expected_revision — but
                    # it goes through the one seam: LOAD-origin apply persists
                    # the pushed bytes, fully reconciles, bumps the revision,
                    # and broadcasts project.reloaded, so an open IDE 409s on
                    # its next save instead of silently reverting the push.
                    from server.core.project_diff import ProjectOrigin
                    await self._apply_fn(
                        project, origin=ProjectOrigin.LOAD, persist=True
                    )
                else:
                    if self._reload_fn is None:
                        await self._send_result(request_id, False, error="Reload not available")
                        return
                    # Bare push: re-read whatever is on disk (LOAD reload).
                    await self._reload_fn()

                # Include previous config in result for rollback
                result_data = {"previous_project_json": previous_json} if previous_json else "Config applied"
                await self._send_result(request_id, True, result=result_data)
            except Exception as e:
                # Catch-all: config push involves file I/O, validation, and engine reload
                await self._send_result(request_id, False, error=str(e))
        else:
            await self._send_result(request_id, False, error="Reload not available")

    _last_restart_time: float = 0

    async def _handle_restart(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a restart request."""
        import time as _time
        now = _time.time()
        if now - self._last_restart_time < 60:
            await self._send_result(request_id, False, error="Restart rate limited (max 1 per 60s)")
            return
        self._last_restart_time = now
        mode = payload.get("mode", "graceful")
        log.info(f"Cloud restart: {actor} → mode={mode}")

        await self._events.emit("cloud.restart", {
            "mode": mode,
            "user_name": payload.get("user_name", ""),
            "user_id": payload.get("user_id", ""),
            "request_id": request_id,
        })

        # Send result before restarting
        await self._send_result(request_id, True, result=f"Restart ({mode}) initiated")

        # Emit the restart event — the engine or service manager handles the actual restart
        await self._events.emit("system.restart_requested", {"mode": mode, "source": "cloud"})

    async def _handle_diagnostic(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a network diagnostic request from the cloud.

        Implements the five action types from spec §13.12:
            ping, tcp_check, traceroute, dns_lookup, port_scan
        """
        action = payload.get("action", "")
        target = payload.get("target", "")
        params = payload.get("params") or {}
        log.info(f"Cloud diagnostic: {actor} → {action} {target}")

        await self._events.emit("cloud.diagnostic", {
            "action": action,
            "target": target,
            "user_name": payload.get("user_name", ""),
            "user_id": payload.get("user_id", ""),
            "request_id": request_id,
        })

        try:
            if action == "ping":
                result = await _diagnostic_ping(target, params)
            elif action == "tcp_check":
                result = await _diagnostic_tcp_check(target, params)
            elif action == "dns_lookup":
                result = await _diagnostic_dns_lookup(target, params)
            elif action == "traceroute":
                result = await _diagnostic_traceroute(target, params)
            elif action == "port_scan":
                result = await _diagnostic_port_scan(target, params)
            else:
                await self._send_diagnostic_result(
                    request_id, False, error=f"Unknown diagnostic action: {action}",
                )
                return
            await self._send_diagnostic_result(request_id, True, result=result)
        except Exception as e:
            log.exception("Diagnostic %s failed", action)
            await self._send_diagnostic_result(request_id, False, error=str(e))

    async def _send_diagnostic_result(
        self, request_id: str, success: bool,
        result: Any = None, error: str | None = None,
    ) -> None:
        """Send a diagnostic_result message (separate from generic command_result)."""
        if not request_id:
            return
        await self._agent.send_message(
            DIAGNOSTIC_RESULT,
            build_diagnostic_result_payload(request_id, success, result=result, error=error),
        )

    async def _handle_software_update(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a software update request from the cloud.

        When the cloud provides an update_url, downloads directly from that URL
        and verifies against the provided checksum. Otherwise falls back to
        checking GitHub Releases independently.
        """
        target_version = payload.get("target_version", "")
        auto_restart = payload.get("auto_restart", True)
        update_url = payload.get("update_url", "")
        checksum_sha256 = payload.get("checksum_sha256")
        log.info(
            f"Cloud software update: {actor} → version={target_version}"
        )

        await self._events.emit("cloud.software_update", {
            "target_version": target_version,
            "auto_restart": auto_restart,
            "user_name": payload.get("user_name", ""),
            "user_id": payload.get("user_id", ""),
            "request_id": request_id,
        })

        try:
            from server.updater.platform import can_self_update, detect_deployment_type

            deployment_type = detect_deployment_type()

            if self._update_manager is None:
                await self._send_result(
                    request_id, True,
                    result=f"Update to {target_version} noted. Check the Programmer IDE for update status.",
                )
                return

            if not can_self_update(deployment_type):
                # A64 — fleet operations need to tell immutable deployments
                # (Docker, git_dev) apart from a real failure. Return failure
                # with a structured error tag so the cloud bucket isn't a
                # green "applied" for systems that haven't moved.
                await self._send_result(
                    request_id, False,
                    error="deployment_immutable",
                    result=(
                        f"Update to {target_version} cannot be applied: this "
                        f"deployment ({deployment_type}) is immutable. Update "
                        "via the deployment's native channel."
                    ),
                )
                return

            if not auto_restart:
                # A63 — persist the cloud-provided URL + checksum so a later
                # manual apply uses this exact target instead of falling
                # back to GitHub. Surface system.update_staged_version so the
                # IDE can show "Update v0.6.0 staged by cloud".
                if update_url:
                    self._update_manager.stage_update(
                        target_version, update_url, checksum_sha256,
                    )
                    result_text = (
                        f"Update to {target_version} staged. Apply from the "
                        "Programmer IDE when ready."
                    )
                else:
                    result_text = (
                        f"Update to {target_version} available. "
                        "auto_restart=false, waiting for manual apply."
                    )
                await self._send_result(
                    request_id, True,
                    result=result_text,
                )
                return

            # Cloud provided a direct URL — use it instead of checking GitHub
            if update_url:
                apply_result = await self._update_manager.apply_cloud_update(
                    target_version, update_url, checksum_sha256,
                )
                await self._send_result(
                    request_id, apply_result.get("success", False),
                    result=apply_result.get("message"),
                    error=apply_result.get("error"),
                )
            else:
                # Fallback: no URL provided, check GitHub independently
                check_result = await self._update_manager.check_for_updates()
                if not check_result.get("update_available"):
                    await self._send_result(
                        request_id, True,
                        result="System is already up to date.",
                    )
                    return

                apply_result = await self._update_manager.apply_update()
                await self._send_result(
                    request_id, apply_result.get("success", False),
                    result=apply_result.get("message"),
                    error=apply_result.get("error"),
                )

        except Exception as e:
            log.exception("Cloud software update failed")
            await self._send_result(
                request_id, False,
                error=f"Update failed: {e}",
            )

    async def _handle_get_project(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a get_project request — read the current project file and send it back."""
        log.info(f"Cloud get_project: {actor}")

        if not self._project_path:
            await self._agent.send_message(PROJECT_DATA, build_project_data_payload(
                request_id, False, error="Project path not configured",
            ))
            return

        try:
            import json
            from pathlib import Path

            project_path = Path(self._project_path)
            if not project_path.exists():
                await self._agent.send_message(PROJECT_DATA, build_project_data_payload(
                    request_id, False, error="Project file not found",
                ))
                return

            project_json = json.loads(project_path.read_text(encoding="utf-8"))
            await self._agent.send_message(PROJECT_DATA, build_project_data_payload(
                request_id, True, project_json=project_json,
            ))
        except Exception as e:
            log.exception("Error reading project file")
            await self._agent.send_message(PROJECT_DATA, build_project_data_payload(
                request_id, False, error=str(e),
            ))

    async def _handle_get_device_commands(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a get_device_commands request — return device list with available commands."""
        log.info(f"Cloud get_device_commands: {actor}")

        try:
            devices = self._devices.list_devices()
            # For each device with commands, include the full command definitions
            result_devices = []
            for dev in devices:
                entry: dict[str, Any] = {
                    "id": dev["id"],
                    "name": dev.get("name", dev["id"]),
                    "driver": dev.get("driver", ""),
                    "connected": dev.get("connected", False),
                }
                # Get full command info (not just names) from get_device_info
                try:
                    info = self._devices.get_device_info(dev["id"])
                    commands = info.get("commands", {})
                    # Simplify command definitions for the cloud
                    entry["commands"] = {
                        cmd_name: {
                            "params": cmd_def.get("params", []) if isinstance(cmd_def, dict) else [],
                        }
                        for cmd_name, cmd_def in commands.items()
                    }
                except Exception:
                    entry["commands"] = {}
                result_devices.append(entry)

            await self._agent.send_message(
                DEVICE_COMMANDS_DATA,
                build_device_commands_data_payload(request_id, True, devices=result_devices),
            )
        except Exception as e:
            log.exception("Error getting device commands")
            await self._agent.send_message(
                DEVICE_COMMANDS_DATA,
                build_device_commands_data_payload(request_id, False, error=str(e)),
            )

    async def _send_result(
        self, request_id: str, success: bool,
        result: Any = None, error: str | None = None
    ) -> None:
        """Send a command_result response to the cloud."""
        if not request_id:
            return

        await self._agent.send_message(
            COMMAND_RESULT,
            build_command_result_payload(request_id, success, result=result, error=error),
        )


# --- Diagnostic action implementations (module-level so they're easy to test) ---


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    """Clamp an int-like value to [lo, hi], falling back to `default`."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# Hostname / IP characters only. ping/traceroute receive the cloud-supplied
# target as a bare argv element, so a value starting with '-' would be read as
# an option flag (option injection). exec() avoids the shell, so the leading-dash
# case is the real risk; the character whitelist is cheap defence in depth.
_EXEC_TARGET_RE = re.compile(r"[A-Za-z0-9._:%-]+")


def _validate_exec_target(target: str) -> str | None:
    """Return an error message if `target` is unsafe to pass as a bare argv
    element to ping/traceroute, else None."""
    if not target:
        return "target required"
    if target.startswith("-"):
        return "invalid target: must not start with '-'"
    if not _EXEC_TARGET_RE.fullmatch(target):
        return "invalid target: only hostname/IP characters are allowed"
    return None


async def _communicate_bounded(proc, timeout_s: float) -> tuple[bytes, bytes]:
    """Run ``proc.communicate()`` under an overall wall-clock bound, killing and
    reaping the subprocess if it overruns. Without this a ping/traceroute whose
    binary stalls on an unresponsive target hangs the task far past any per-hop
    timeout (tcp_check already bounds itself with asyncio.wait_for). Raises
    ``asyncio.TimeoutError`` on overrun."""
    import asyncio
    import contextlib
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()  # best-effort reap so the child isn't left as a zombie
        raise


async def _diagnostic_ping(target: str, params: dict) -> dict:
    """ICMP-ish ping using the OS `ping` binary (cross-platform)."""
    import asyncio
    import sys
    err = _validate_exec_target(target)
    if err:
        return {"reachable": False, "error": err}
    count = _clamp(params.get("count"), 1, 20, 4)
    timeout_s = _clamp(params.get("timeout"), 1, 30, 2)

    if sys.platform == "win32":
        cmd = ["ping", "-n", str(count), "-w", str(timeout_s * 1000), target]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout_s), target]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )
    # Overall bound: each of `count` pings can wait up to timeout_s, plus slack.
    overall_timeout = count * timeout_s + 5
    try:
        stdout, stderr = await _communicate_bounded(proc, overall_timeout)
    except asyncio.TimeoutError:
        return {
            "exit_code": None,
            "reachable": False,
            "output": "",
            "error": f"ping exceeded {overall_timeout}s and was terminated",
            "count": count,
        }
    output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
    return {
        "exit_code": proc.returncode,
        "reachable": proc.returncode == 0,
        "output": output.strip(),
        "count": count,
    }


async def _diagnostic_tcp_check(target: str, params: dict) -> dict:
    """Probe a TCP port. Target is the host; port comes from params."""
    import asyncio
    raw_port = params.get("port")
    if raw_port is None:
        return {"open": False, "error": "port required for tcp_check"}
    # Validate rather than clamp: a literal port 0 (or out-of-range) is an
    # invalid request and must yield a clear error, not a misleading probe of
    # port 1 (which clamping produced).
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return {"open": False, "error": f"invalid port {raw_port!r} (must be an integer 1-65535)"}
    if not 1 <= port <= 65535:
        return {"open": False, "error": f"invalid port {port} (must be 1-65535)"}
    timeout_s = _clamp(params.get("timeout"), 1, 30, 3)

    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(target, port), timeout=timeout_s,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return {"open": True, "host": target, "port": port}
    except asyncio.TimeoutError:
        return {"open": False, "host": target, "port": port, "error": "timeout"}
    except (ConnectionRefusedError, OSError) as e:
        return {"open": False, "host": target, "port": port, "error": str(e)}


async def _diagnostic_dns_lookup(target: str, params: dict) -> dict:
    """Resolve a hostname. Optional record_type narrows the family (A/AAAA)."""
    import asyncio
    import socket
    record_type = (params.get("record_type") or "A").upper()
    family = {
        "A": socket.AF_INET,
        "AAAA": socket.AF_INET6,
    }.get(record_type, 0)

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            target, None, family=family, type=socket.SOCK_STREAM,
        )
    except socket.gaierror as e:
        return {"resolved": False, "host": target, "error": str(e)}

    addrs = sorted({info[4][0] for info in infos})
    return {
        "resolved": True,
        "host": target,
        "record_type": record_type,
        "addresses": addrs,
    }


async def _diagnostic_traceroute(target: str, params: dict) -> dict:
    """Trace the route to a host. Uses the OS `traceroute`/`tracert` binary."""
    import asyncio
    import sys
    err = _validate_exec_target(target)
    if err:
        return {"error": err}
    max_hops = _clamp(params.get("max_hops"), 1, 64, 30)
    timeout_s = _clamp(params.get("timeout"), 1, 30, 2)

    if sys.platform == "win32":
        cmd = ["tracert", "-h", str(max_hops), "-w", str(timeout_s * 1000), target]
    else:
        cmd = ["traceroute", "-m", str(max_hops), "-w", str(timeout_s), target]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return {"error": f"{cmd[0]} not installed on this system"}
    # Overall bound: up to max_hops probes each waiting timeout_s, plus slack.
    overall_timeout = max_hops * timeout_s + 10
    try:
        stdout, stderr = await _communicate_bounded(proc, overall_timeout)
    except asyncio.TimeoutError:
        return {
            "error": f"traceroute exceeded {overall_timeout}s and was terminated",
            "max_hops": max_hops,
        }
    output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
    return {
        "exit_code": proc.returncode,
        "output": output.strip(),
        "max_hops": max_hops,
    }


# Common AV ports baseline; cloud `params.ports` can override.
_DEFAULT_AV_PORTS = [22, 23, 80, 443, 4352, 5000, 8080, 8443, 1515, 1702, 2001, 9090]


async def _diagnostic_port_scan(target: str, params: dict) -> dict:
    """Scan a host's TCP ports. Reuses the discovery PortScanner."""
    from server.discovery.port_scanner import scan_host_ports

    raw_ports = params.get("ports") or _DEFAULT_AV_PORTS
    ports: list[int] = []
    for p in raw_ports:
        try:
            n = int(p)
            if 1 <= n <= 65535:
                ports.append(n)
        except (TypeError, ValueError):
            continue
    if not ports:
        return {"host": target, "open": [], "scanned": 0}

    open_ports = await scan_host_ports(target, ports, timeout=1.5)
    return {
        "host": target,
        "scanned": len(ports),
        "open": open_ports,
    }
