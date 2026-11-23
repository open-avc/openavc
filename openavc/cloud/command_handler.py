"""
OpenAVC Cloud — Incoming command handler.

Handles commands from the cloud platform: device commands, config push,
restart requests, and diagnostic requests. Delegates to the appropriate
engine subsystem and sends command_result responses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from openavc import restart
from openavc.cloud.protocol import (
    COMMAND, CONFIG_PUSH, RESTART, DIAGNOSTIC, SOFTWARE_UPDATE,
    PANEL_SOFTWARE_UPDATE, GET_PROJECT, GET_DEVICE_COMMANDS,
    COMMAND_RESULT, PROJECT_DATA, DEVICE_COMMANDS_DATA,
    DIAGNOSTIC_RESULT, MAX_EMBED_BYTES,
    build_command_result_payload, build_diagnostic_result_payload,
    build_project_data_payload, build_device_commands_data_payload,
    read_message, read_payload,
)
from openavc.core.project_update import MigrationNotes
from openavc.drivers.spec import is_file_command
from openavc.updater.checker import not_newer_refusal
from openavc.utils.logger import get_logger
from openavc.utils.spawn import CREATE_NO_WINDOW
from openavc.version import __version__

if TYPE_CHECKING:
    from openavc.cloud.agent import CloudAgent
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.discovery.port_guard import PortGuard

log = get_logger(__name__)


#: Why a push was not applied when it failed before the project was handed
#: to the running system (its backup, say). The cause is in the local log.
_NOT_REACHED = "the system could not prepare the change. The Programmer's Log has the details."


def _not_applied_answer(exc: Exception) -> str:
    """What the cloud is told about a config push that was not applied, in
    the words every door that changes the project uses
    (``describe_change_failure``): never the exception's own text or a path
    on this system, which the cloud keeps."""
    from openavc.api.error_messages import describe_change_failure, not_applied

    failure = describe_change_failure(exc)
    if failure is None:
        log.error(f"Config push not applied: {type(exc).__name__}: {exc}")
        return not_applied(_NOT_REACHED)
    if failure.code == "not_applied":
        return failure.sentence
    if failure.code == "conflict":
        return not_applied(_CHANGED_SINCE_READ)
    return f"Not applied: {failure.sentence}"


def _schema_problems(exc: Exception) -> str:
    """A pushed project that is not valid, as each field and its message:
    never the value, which may be a login."""
    from pydantic import ValidationError

    from openavc.api.errors import validation_error_lines

    if isinstance(exc, ValidationError):
        return "; ".join(validation_error_lines(exc.errors(include_url=False, include_input=False)))
    return str(exc)


#: The reason the backup a push takes of the project it replaces is filed under.
_PUSH_BACKUP = "Before cloud config push"

#: Why a push built on a read of the project (``expected_revision``) was not
#: applied: a save landed on this system after the read.
_CHANGED_SINCE_READ = (
    "this system's project changed after the cloud read it, and this push would have "
    "replaced that change. Push again to merge into the project as it is now."
)

#: Why a push with no openavc_version was not applied.
_NO_VERSION = (
    "the project has no openavc_version, so this system cannot tell which format it "
    "was saved in. Push the whole project as a system exports it, openavc_version included."
)


class _PushRefused(Exception):
    """A push refused before the running system was given it; its text is
    the answer."""


def _read_push(project_json: dict[str, Any]) -> tuple[Any, MigrationNotes]:
    """The pushed project as Save reads a whole project
    (``core/sent_project.py``), repaired as an import is, and what its
    migration changed; a refusal in the push's own words."""
    from openavc.api.error_messages import not_applied
    from openavc.core import sent_project

    try:
        return sent_project.read_sent_project(project_json, repair=True)
    except sent_project.ProjectVersionMissingError as missing:
        raise _PushRefused(not_applied(_NO_VERSION)) from missing
    except (ValueError, TypeError) as invalid:
        raise _PushRefused(f"Invalid project schema: {_schema_problems(invalid)}") from invalid


class _RollbackCopy:
    """The project file a push replaces, read as JSON under the project lock
    once the push has its turn (``Engine.replace_project``'s ``before``), so
    an edit saved while the push waited is in it. It goes back in the push's
    answer for the cloud's rollback (``previous``; ``None`` when the file
    could not be read), so it has to fit the cloud message limit: one too
    large refuses the push before anything changes. Applied first, the
    answer could not be sent, and the fleet operation would be left on a
    system running the push with no copy on either side."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.previous: dict[str, Any] | None = None

    def take(self) -> None:
        try:
            previous = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # Best-effort: the push goes on without a rollback copy
        size = len(json.dumps(previous))
        if size > MAX_EMBED_BYTES:
            raise _PushRefused(
                f"rollback_snapshot_too_large: current project serializes to {size} bytes, "
                f"which exceeds the {MAX_EMBED_BYTES}-byte cloud message limit; "
                f"config push refused so rollback stays possible"
            )
        self.previous = previous


class CommandHandler:
    """
    Handles incoming commands from the cloud and delegates to local subsystems.

    Commands are dispatched by type:
    - command: Send a command to a device via DeviceManager
    - config_push: Replace the project with the one the cloud sent
    - restart: Restart the OpenAVC service
    - diagnostic: Run a network diagnostic (future)
    """

    def __init__(
        self,
        agent: CloudAgent,
        devices: DeviceManager,
        events: EventBus,
        apply_fn=None,
        read_fn=None,
        update_manager=None,
        project_path=None,
        panel_software=None,
    ):
        """
        Args:
            agent: The CloudAgent to send responses through.
            devices: DeviceManager for device commands.
            events: EventBus for emitting audit events.
            apply_fn: Optional async callable (engine.replace_project) that
                persists and reconciles a pushed project through the one seam,
                taking what its migration changed as ``update_notes`` and the
                revision a push was built on as ``expected_revision``.
            read_fn: Optional async callable (engine.read_saved_project) that
                answers get_project: the project file and the revision it
                holds, read together under the project lock.
            update_manager: Optional UpdateManager for cloud-triggered updates.
            project_path: Path to the active project.avc file.
            panel_software: Optional PanelSoftwareUpdates for panel software
                updates on appliance hardware.
        """
        self._agent = agent
        self._devices = devices
        self._events = events
        self._apply_fn = apply_fn
        self._read_fn = read_fn
        self._update_manager = update_manager
        self._project_path = project_path
        self._panel_software = panel_software

    async def handle(self, msg: dict[str, Any]) -> None:
        """
        Route an incoming command message to the appropriate handler.

        Args:
            msg: The parsed, verified message dict.
        """
        msg_type = msg.get("type", "")
        payload = read_message(msg)
        request_id = payload["request_id"]

        # Audit attribution: the display name plus the durable user id, so
        # local logs identify the cloud user unambiguously even if the name
        # changes or collides. The two reads (get_project, get_device_commands)
        # name nobody.
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
            elif msg_type in (SOFTWARE_UPDATE, PANEL_SOFTWARE_UPDATE):
                await self._handle_update(msg_type, payload, request_id, actor)
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
        payload = read_payload(COMMAND, payload)
        device_id = payload["device_id"]
        command = payload["command"]
        params = payload["params"]
        # What the log and the announcement show: a secret parameter as ***.
        shown = self._devices.params_shown(device_id, command, params)

        log.info(
            f"Cloud command: {actor} → {device_id}.{command}"
            f"({shown})"
        )

        await self._events.emit("cloud.command", {
            "device_id": device_id,
            "command": command,
            "params": shown,
            "user_name": payload["user_name"],
            "user_id": payload["user_id"],
            "request_id": request_id,
        })

        try:
            await self._devices.send_command(device_id, command, params)
            await self._send_result(request_id, True, result="OK")
        except Exception as e:
            # Catch-all: device commands can fail with transport, protocol, or
            # driver errors. Worded as Send Command words it before it leaves
            # the building.
            from openavc.api.error_messages import describe_command_failure

            failure = describe_command_failure(
                e, device_id, command, state=self._devices.state,
                driver=self._devices.get_driver(device_id),
            )
            await self._send_result(request_id, False, error=failure.programmer)

    async def _handle_config_push(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a project config push."""
        payload = read_payload(CONFIG_PUSH, payload)
        mode = payload["mode"]
        project_json = payload["project_json"]
        log.info(f"Cloud config push: {actor} → mode={mode}")

        await self._events.emit("cloud.config_push", {
            "mode": mode,
            "user_name": payload["user_name"],
            "user_id": payload["user_id"],
            "request_id": request_id,
        })

        if not project_json:
            # Missing, null or {}: there is nothing to apply, and answering
            # success would report a push that changed nothing.
            from openavc.api.error_messages import not_applied

            await self._send_result(
                request_id, False, error=not_applied("the push carried no project."),
            )
            return

        if not (self._apply_fn and self._project_path):
            await self._send_result(request_id, False, error="Apply not available")
            return
        from openavc.core.project_changes import KeepReplaced

        kept = _RollbackCopy(Path(self._project_path))
        try:
            project, update_notes = _read_push(project_json)
            # Through the door a replace takes: it persists the pushed bytes,
            # fully reconciles, bumps the revision and broadcasts
            # project.reloaded, so an open IDE 409s on its next save instead
            # of reverting it. A push built on a read (a template merged into
            # this system's project) names the revision it read, and a save
            # since refuses it; one built on no read names none and wins.
            await self._apply_fn(
                project, update_notes=update_notes,
                expected_revision=payload["expected_revision"],
                keep=KeepReplaced(before=kept.take, backup=_PUSH_BACKUP),
            )
        except _PushRefused as refusal:
            await self._send_result(request_id, False, error=str(refusal))
            return
        except Exception as e:
            # A push that fails anywhere (the backup, the apply, its write)
            # was not taken in: the project before it still runs.
            await self._send_result(request_id, False, error=_not_applied_answer(e))
            return
        result = {"previous_project_json": kept.previous} if kept.previous else "Config applied"
        await self._send_result(request_id, True, result=result)

    async def _handle_restart(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """A restart from the cloud, decided by the restart door
        (openavc/restart.py), which refuses it the way it refuses any other
        asker's. The answer says what the door decided, and is sent before
        anything else here: a graceful restart's stop waits 2 seconds for
        it, a hard one's does not."""
        payload = read_payload(RESTART, payload)
        answer = restart.request_exit(payload["mode"], "cloud")
        if not answer.accepted:
            await self._send_result(request_id, False, error=answer.sentence)
            return
        log.info(f"Cloud restart: {actor} → mode={answer.mode}")
        await self._send_result(request_id, True, result=f"Restart ({answer.mode}) initiated")
        await self._events.emit("cloud.restart", {
            "mode": answer.mode,
            "user_name": payload["user_name"],
            "user_id": payload["user_id"],
            "request_id": request_id,
        })

    async def _handle_diagnostic(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Handle a network diagnostic request from the cloud.

        Implements the five diagnostic action types:
            ping, tcp_check, traceroute, dns_lookup, port_scan
        """
        payload = read_payload(DIAGNOSTIC, payload)
        action = payload["action"]
        target = payload["target"]
        params = payload["params"] or {}
        log.info(f"Cloud diagnostic: {actor} → {action} {target}")

        await self._events.emit("cloud.diagnostic", {
            "action": action,
            "target": target,
            "user_name": payload["user_name"],
            "user_id": payload["user_id"],
            "request_id": request_id,
        })

        try:
            if action == "ping":
                result = await _diagnostic_ping(target, params)
            elif action == "tcp_check":
                result = await _diagnostic_tcp_check(
                    target, params, guard=await self._held_port_guard(),
                )
            elif action == "dns_lookup":
                result = await _diagnostic_dns_lookup(target, params)
            elif action == "traceroute":
                result = await _diagnostic_traceroute(target, params)
            elif action == "port_scan":
                result = await _diagnostic_port_scan(
                    target, params, guard=await self._held_port_guard(),
                )
            else:
                await self._send_diagnostic_result(
                    request_id, False, error=f"Unknown diagnostic action: {action}",
                )
                return
            await self._send_diagnostic_result(request_id, True, result=result)
        except Exception as e:
            log.exception("Diagnostic %s failed", action)
            await self._send_diagnostic_result(request_id, False, error=str(e))

    async def _held_port_guard(self) -> "PortGuard":
        """The ports connected project devices' drivers hold, which
        ``tcp_check`` and ``port_scan`` report as in use without connecting
        (``discovery/port_guard.py``): a device that serves one connection at
        a time gives its port to the newest one. A device connected to the
        simulator holds nothing at its own address
        (``DeviceManager.network_connections``)."""
        from openavc.discovery.port_guard import PortGuard, held_ports

        connections = getattr(self._devices, "network_connections", None)
        if connections is None:
            return PortGuard()
        try:
            held, _unresolved = await held_ports(connections())
        except Exception:
            log.exception("Could not read the project's connected devices")
            return PortGuard()
        return PortGuard({key: conn.name for key, conn in held.items()})

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
        checking GitHub Releases independently. A target that is not newer is
        refused, staged or not, unless the cloud marks it a rollback.
        """
        payload = read_payload(SOFTWARE_UPDATE, payload)
        from openavc.updater.host import get_firmware_host
        firmware_host = get_firmware_host()
        if firmware_host:
            await self._send_result(request_id, False, error="firmware_managed", result=firmware_host.guidance)
            return
        target_version = payload["target_version"]
        auto_restart = payload["auto_restart"]
        log.info(
            f"Cloud software update: {actor} → version={target_version}"
        )

        await self._events.emit("cloud.software_update", {
            "target_version": target_version,
            "auto_restart": auto_restart,
            "user_name": payload["user_name"],
            "user_id": payload["user_id"],
            "request_id": request_id,
        })

        try:
            from openavc.updater.platform import can_self_update, detect_deployment_type

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

            refusal = not_newer_refusal(target_version, __version__, payload["rollback"])
            if refusal:
                await self._send_apply_result(request_id, refusal)
                return

            if not auto_restart:
                await self._stage_software_update(request_id, payload)
            else:
                await self._install_software_update(request_id, payload)

        except Exception as e:
            log.exception("Cloud software update failed")
            await self._send_result(
                request_id, False,
                error=f"Update failed: {e}",
            )

    async def _stage_software_update(self, request_id: str, payload: dict[str, Any]) -> None:
        """A63: persist the cloud-provided URL + checksum (and a rollback's
        mark) so a later manual apply uses this exact target instead of
        falling back to GitHub. Surfaces system.update_staged_version so the
        IDE can show "Update v0.6.0 staged by cloud". Refused, with the
        updater's sentence, while an update or rollback is in flight."""
        target_version, update_url = payload["target_version"], payload["update_url"]
        if update_url:
            refused = self._update_manager.stage_update(
                target_version, update_url, payload["checksum_sha256"], rollback=payload["rollback"],
            )
            if refused:
                await self._send_apply_result(request_id, refused)
                return
            result_text = f"Update to {target_version} staged. Apply from the Programmer IDE when ready."
        else:
            result_text = f"Update to {target_version} available. auto_restart=false, waiting for manual apply."
        await self._send_result(request_id, True, result=result_text)

    async def _install_software_update(self, request_id: str, payload: dict[str, Any]) -> None:
        """From the cloud's URL when it sent one; otherwise the newest release
        GitHub offers, checked for here."""
        update_url = payload["update_url"]
        if update_url:
            apply_result = await self._update_manager.apply_cloud_update(
                payload["target_version"], update_url, payload["checksum_sha256"],
                rollback=payload["rollback"],
            )
        else:
            # Refused before the check: a check that finds nothing would
            # answer "up to date" while an update is being installed.
            busy = self._update_manager.busy_refusal()
            if busy:
                await self._send_apply_result(request_id, busy)
                return
            check_result = await self._update_manager.check_for_updates()
            if not check_result.get("update_available"):
                await self._send_result(request_id, True, result="System is already up to date.")
                return
            apply_result = await self._update_manager.apply_update()
        await self._send_apply_result(request_id, apply_result)

    async def _send_apply_result(self, request_id: str, apply_result: dict[str, Any]) -> None:
        """Answer with what the updater said: its message, or what to do instead of updating."""
        await self._send_result(
            request_id, apply_result.get("success", False),
            result=apply_result.get("message") or apply_result.get("instructions"),
            error=apply_result.get("error"),
        )

    async def _handle_update(
        self, msg_type: str, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """An update for OpenAVC itself (software_update) or for the panel
        software on appliance hardware (panel_software_update)."""
        if msg_type == PANEL_SOFTWARE_UPDATE:
            await self._handle_panel_software_update(payload, request_id, actor)
        else:
            await self._handle_software_update(payload, request_id, actor)

    async def _handle_panel_software_update(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Stage a panel software update the cloud sent.

        The result says whether the file is staged. The install happens later,
        on the panel, and its outcome arrives as ``system.panel_software.*``
        state. A refusal answers ``error`` as ``<code>: <sentence>``. Under a
        product firmware host it is refused like a software update: firmware
        owns every update there.
        """
        payload = read_payload(PANEL_SOFTWARE_UPDATE, payload)
        panel = self._panel_software
        if panel is None:
            await self._send_result(
                request_id, False,
                error="subsystem_unavailable: panel software updates are not running on this system",
            )
            return
        target_version = payload["target_version"]
        log.info(f"Cloud panel software update: {actor} → version={target_version}")
        await self._events.emit("cloud.panel_software_update", {
            "target_version": target_version,
            "install": payload["install"],
            "user_name": payload["user_name"],
            "user_id": payload["user_id"],
            "request_id": request_id,
        })
        outcome = await panel.stage(
            update_url=payload["update_url"],
            checksum_sha256=payload["checksum_sha256"],
            target_version=target_version,
            target_version_code=payload["target_version_code"],
            signer_sha256=payload["signer_sha256"],
            install=payload["install"],
        )
        if outcome.success:
            await self._send_result(request_id, True, result=outcome.message)
        else:
            await self._send_result(request_id, False, error=f"{outcome.code}: {outcome.message}")

    async def _handle_get_project(
        self, payload: dict[str, Any], request_id: str, actor: str
    ) -> None:
        """Answer get_project with the project file and the revision it holds,
        read together (``read_fn``): a push built on the read sends the
        revision back, and a save made since refuses it."""
        log.info(f"Cloud get_project: {actor}")

        if self._read_fn is None:
            await self._send_project_data(request_id, error="Project path not configured")
            return
        try:
            read = await self._read_fn()
        except FileNotFoundError:
            await self._send_project_data(request_id, error="Project file not found")
            return
        except Exception as e:
            log.exception("Error reading project file")
            await self._send_project_data(request_id, error=str(e))
            return

        # The reply must fit the 1 MB wire cap or the cloud drops it and
        # the ProjectFetcher burns its full timeout with no explanation.
        # Measure the embed as it will actually serialize on the wire.
        embed_size = len(json.dumps(read.project_json))
        if embed_size > MAX_EMBED_BYTES:
            await self._send_project_data(request_id, error=(
                f"project_too_large: project serializes to {embed_size} bytes, "
                f"which exceeds the {MAX_EMBED_BYTES}-byte cloud message limit"
            ))
            return
        await self._agent.send_message(PROJECT_DATA, build_project_data_payload(
            request_id, True, project_json=read.project_json, revision=read.revision,
        ))

    async def _send_project_data(self, request_id: str, *, error: str) -> None:
        """Answer get_project with why the project was not read."""
        await self._agent.send_message(PROJECT_DATA, build_project_data_payload(
            request_id, False, error=error,
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
                    # Simplify command definitions for the cloud. A command
                    # that takes or hands back a file is left out: the portal
                    # has no file to give, and sending it is refused.
                    entry["commands"] = {
                        cmd_name: {
                            "params": cmd_def.get("params", []) if isinstance(cmd_def, dict) else [],
                        }
                        for cmd_name, cmd_def in commands.items()
                        if not is_file_command(cmd_def)
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
    """Ping a host with the OS ``ping`` program, as it prints it. An install
    with no ``ping`` program (appliance hardware, a minimal Linux) sends the
    echoes itself instead (:func:`_ping_without_program`)."""
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

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return await _ping_without_program(target, count, timeout_s)
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


async def _ping_without_program(target: str, count: int, timeout_s: int) -> dict:
    """The ping diagnostic on an install with no ``ping`` program: ``count``
    echoes sent one at a time over the ICMP socket a Discovery scan would use
    (``discovery/icmp.py``), each reported on its own line.

    An install that cannot send an echo either answers why, never "no reply":
    that would read as a dead host. IPv4 only, as Discovery is.
    """
    from openavc.discovery import icmp
    from openavc.discovery.network_scanner import resolve_address

    result: dict[str, Any] = {"exit_code": None, "reachable": False, "output": "", "count": count}
    # Looked up here: the socket would look a host name up on the engine's loop.
    address = await resolve_address(target)
    if not address:
        return {**result, "error": f"Could not find an IPv4 address for {target}"}
    method = await icmp.select_ping_method()
    if method not in (icmp.METHOD_DGRAM, icmp.METHOD_RAW):
        return {
            **result,
            "error": "This system has no ping program and is not allowed to send a ping itself",
        }
    lines = [f"PING {target} ({address}), sent by OpenAVC: this system has no ping program"]
    replies = 0
    for _ in range(count):
        line, answered = await _one_echo(address, timeout_s, method)
        lines.append(line)
        replies += answered
    lines.append(f"{count} sent, {replies} received")
    return {**result, "reachable": replies > 0, "output": "\n".join(lines)}


async def _one_echo(address: str, timeout_s: int, method: str) -> tuple[str, bool]:
    """One echo to ``address``: the line that reports it, and whether it was answered."""
    import asyncio

    from openavc.discovery import icmp

    loop = asyncio.get_running_loop()
    started = loop.time()
    answer = await icmp.ping_host(address, timeout=timeout_s, method=method)
    if answer == icmp.RESULT_ALIVE:
        return f"Reply from {address}: time={(loop.time() - started) * 1000:.0f} ms", True
    if answer == icmp.RESULT_TIMEOUT:
        return f"No reply from {address} within {timeout_s} s", False
    return f"Could not send a ping to {address}", False


async def _diagnostic_tcp_check(
    target: str, params: dict, guard: "PortGuard | None" = None,
) -> dict:
    """Probe a TCP port. Target is the host; port comes from params.

    A port ``guard`` says a connected project device holds is reported open
    and ``in_use_by`` that device, and never connected to.
    """
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

    if guard is not None:
        from openavc.discovery.network_scanner import resolve_address

        holder = guard.held_by(await resolve_address(target) or target, port)
        if holder is not None:
            return {"open": True, "host": target, "port": port, "in_use_by": holder}

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


async def _diagnostic_port_scan(
    target: str, params: dict, guard: "PortGuard | None" = None,
) -> dict:
    """Scan a host's TCP ports. Reuses the discovery PortScanner.

    A port ``guard`` says a connected project device holds is listed in
    ``open`` and named in ``in_use_by`` (port -> device), and never
    connected to.
    """
    from openavc.discovery.network_scanner import resolve_address
    from openavc.discovery.port_scanner import (
        PORT_IN_USE, PORT_OPEN, scan_host_port_states,
    )

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

    # The guard knows devices by address, so a host name is looked up first.
    address = await resolve_address(target) or target
    states = await scan_host_port_states(address, ports, timeout=1.5, guard=guard)
    result: dict[str, Any] = {
        "host": target,
        "scanned": len(ports),
        "open": sorted(p for p, s in states.items() if s in (PORT_OPEN, PORT_IN_USE)),
    }
    in_use = {
        str(p): guard.held_by(address, p)
        for p, s in states.items() if s == PORT_IN_USE and guard is not None
    }
    if in_use:
        result["in_use_by"] = in_use
    return result
