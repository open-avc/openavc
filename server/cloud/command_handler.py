"""
OpenAVC Cloud — Incoming command handler.

Handles commands from the cloud platform: device commands, config push,
restart requests, and diagnostic requests. Delegates to the appropriate
engine subsystem and sends command_result responses.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from server.cloud.protocol import (
    COMMAND, CONFIG_PUSH, RESTART, DIAGNOSTIC, SOFTWARE_UPDATE,
    GET_PROJECT,
    COMMAND_RESULT, PROJECT_DATA,
    extract_payload,
)
from server.utils.logger import get_logger

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
        update_manager=None,
        project_path=None,
    ):
        """
        Args:
            agent: The CloudAgent to send responses through.
            devices: DeviceManager for device commands.
            events: EventBus for emitting audit events.
            reload_fn: Optional async callable to trigger project reload.
            update_manager: Optional UpdateManager for cloud-triggered updates.
            project_path: Path to the active project.avc file.
        """
        self._agent = agent
        self._devices = devices
        self._events = events
        self._reload_fn = reload_fn
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

        # Audit log
        _user_id = payload.get("user_id", "")
        user_name = payload.get("user_name", "")

        try:
            if msg_type == COMMAND:
                await self._handle_device_command(payload, request_id, user_name)
            elif msg_type == CONFIG_PUSH:
                await self._handle_config_push(payload, request_id, user_name)
            elif msg_type == RESTART:
                await self._handle_restart(payload, request_id, user_name)
            elif msg_type == DIAGNOSTIC:
                await self._handle_diagnostic(payload, request_id, user_name)
            elif msg_type == SOFTWARE_UPDATE:
                await self._handle_software_update(payload, request_id, user_name)
            elif msg_type == GET_PROJECT:
                await self._handle_get_project(payload, request_id, user_name)
            else:
                await self._send_result(request_id, False, error=f"Unknown command type: {msg_type}")
        except Exception as e:
            # Catch-all: isolates command processing errors; reports failure to cloud
            log.exception(f"Command handler: error processing {msg_type}")
            await self._send_result(request_id, False, error=str(e))

    async def _handle_device_command(
        self, payload: dict[str, Any], request_id: str, user_name: str
    ) -> None:
        """Handle a device command."""
        device_id = payload.get("device_id", "")
        command = payload.get("command", "")
        params = payload.get("params", {})

        log.info(
            f"Cloud command: {user_name} → {device_id}.{command}"
            f"({params})"
        )

        await self._events.emit("cloud.command", {
            "device_id": device_id,
            "command": command,
            "params": params,
            "user_name": user_name,
            "request_id": request_id,
        })

        try:
            await self._devices.send_command(device_id, command, params)
            await self._send_result(request_id, True, result="OK")
        except Exception as e:
            # Catch-all: device commands can fail with transport, protocol, or driver errors
            await self._send_result(request_id, False, error=str(e))

    async def _handle_config_push(
        self, payload: dict[str, Any], request_id: str, user_name: str
    ) -> None:
        """Handle a project config push."""
        mode = payload.get("mode", "full_replace")
        project_json = payload.get("project_json")
        log.info(f"Cloud config push: {user_name} → mode={mode}")

        await self._events.emit("cloud.config_push", {
            "mode": mode,
            "user_name": user_name,
            "request_id": request_id,
        })

        if self._reload_fn and self._project_path:
            try:
                from pathlib import Path
                from server.core.project_loader import ProjectConfig, save_project
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

                # Write new project_json to disk if provided
                if project_json:
                    # Validate against schema before writing to disk
                    try:
                        project = ProjectConfig.model_validate(project_json)
                    except (ValueError, TypeError) as ve:
                        await self._send_result(request_id, False, error=f"Invalid project schema: {ve}")
                        return

                    # Create backup before overwriting
                    import asyncio
                    await asyncio.to_thread(create_backup, project_path.parent, "Before cloud config push")
                    save_project(project_path, project)

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
        self, payload: dict[str, Any], request_id: str, user_name: str
    ) -> None:
        """Handle a restart request."""
        import time as _time
        now = _time.time()
        if now - self._last_restart_time < 60:
            await self._send_result(request_id, False, error="Restart rate limited (max 1 per 60s)")
            return
        self._last_restart_time = now
        mode = payload.get("mode", "graceful")
        log.info(f"Cloud restart: {user_name} → mode={mode}")

        await self._events.emit("cloud.restart", {
            "mode": mode,
            "user_name": user_name,
            "request_id": request_id,
        })

        # Send result before restarting
        await self._send_result(request_id, True, result=f"Restart ({mode}) initiated")

        # Emit the restart event — the engine or service manager handles the actual restart
        await self._events.emit("system.restart_requested", {"mode": mode})

    async def _handle_diagnostic(
        self, payload: dict[str, Any], request_id: str, user_name: str
    ) -> None:
        """Handle a network diagnostic request (placeholder for future)."""
        action = payload.get("action", "")
        target = payload.get("target", "")
        log.info(f"Cloud diagnostic: {user_name} → {action} {target}")

        await self._events.emit("cloud.diagnostic", {
            "action": action,
            "target": target,
            "user_name": user_name,
            "request_id": request_id,
        })

        # Diagnostics module not yet implemented
        await self._send_result(
            request_id, False, error="Diagnostics not yet implemented"
        )

    async def _handle_software_update(
        self, payload: dict[str, Any], request_id: str, user_name: str
    ) -> None:
        """Handle a software update request from the cloud.

        Triggers a check-and-apply flow through UpdateManager.
        For non-self-updating deployments (Docker, git), acknowledges
        but does not attempt to apply.
        """
        target_version = payload.get("target_version", "")
        auto_restart = payload.get("auto_restart", True)
        log.info(
            f"Cloud software update: {user_name} → version={target_version}"
        )

        await self._events.emit("cloud.software_update", {
            "target_version": target_version,
            "auto_restart": auto_restart,
            "user_name": user_name,
            "request_id": request_id,
        })

        # Use UpdateManager to check and apply
        try:
            from server.updater.platform import can_self_update, detect_deployment_type

            deployment_type = detect_deployment_type()

            if self._update_manager is None:
                await self._send_result(
                    request_id, True,
                    result=f"Update to {target_version} noted. Check the Programmer IDE for update status.",
                )
                return

            # Run check
            check_result = await self._update_manager.check_for_updates()

            if not can_self_update(deployment_type):
                await self._send_result(
                    request_id, True,
                    result=f"Update to {target_version} available but this deployment type does not support self-update.",
                )
                return

            if not check_result.get("update_available"):
                await self._send_result(
                    request_id, True,
                    result="System is already up to date.",
                )
                return

            # Apply the update
            if auto_restart:
                apply_result = await self._update_manager.apply_update()
                await self._send_result(
                    request_id, apply_result.get("success", False),
                    result=apply_result.get("message"),
                    error=apply_result.get("error"),
                )
            else:
                await self._send_result(
                    request_id, True,
                    result=f"Update to {target_version} available. auto_restart=false, waiting for manual apply.",
                )

        except Exception as e:
            log.exception("Cloud software update failed")
            await self._send_result(
                request_id, False,
                error=f"Update failed: {e}",
            )

    async def _handle_get_project(
        self, payload: dict[str, Any], request_id: str, user_name: str
    ) -> None:
        """Handle a get_project request — read the current project file and send it back."""
        log.info(f"Cloud get_project: {user_name}")

        if not self._project_path:
            await self._agent.send_message(PROJECT_DATA, {
                "request_id": request_id,
                "success": False,
                "error": "Project path not configured",
            })
            return

        try:
            import json
            from pathlib import Path

            project_path = Path(self._project_path)
            if not project_path.exists():
                await self._agent.send_message(PROJECT_DATA, {
                    "request_id": request_id,
                    "success": False,
                    "error": "Project file not found",
                })
                return

            project_json = json.loads(project_path.read_text(encoding="utf-8"))
            await self._agent.send_message(PROJECT_DATA, {
                "request_id": request_id,
                "success": True,
                "project_json": project_json,
            })
        except Exception as e:
            log.exception("Error reading project file")
            await self._agent.send_message(PROJECT_DATA, {
                "request_id": request_id,
                "success": False,
                "error": str(e),
            })

    async def _send_result(
        self, request_id: str, success: bool,
        result: Any = None, error: str | None = None
    ) -> None:
        """Send a command_result response to the cloud."""
        if not request_id:
            return

        await self._agent.send_message(COMMAND_RESULT, {
            "request_id": request_id,
            "success": success,
            "result": result,
            "error": error,
        })
