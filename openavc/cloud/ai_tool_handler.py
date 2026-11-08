"""
OpenAVC Cloud — AI tool call handler.

Handles AI_TOOL_CALL messages from the cloud platform: looks the tool up in
``TOOLS`` (every tool the cloud may call, and whether it runs under the
project lock), runs it as a background task, and sends the result back as an
AI_TOOL_RESULT. A tool that returned an error or raised is answered as a
failure, and what a raised exception may say is ``describe_tool_failure``'s
call, because the answer is kept in the cloud chat history. The tools
themselves are the mixins under ``cloud/tools/``, one per area.
"""

from __future__ import annotations

import asyncio
import errno
import json
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from openavc.cloud.protocol import (
    AI_TOOL_RESULT, build_ai_tool_result_payload, extract_payload,
)
from openavc.cloud.tools.catalog_tools import CatalogToolsMixin
from openavc.cloud.tools.custom_ui_tools import CustomUIToolsMixin
from openavc.cloud.tools.device_tools import DeviceToolsMixin
from openavc.cloud.tools.driver_tools import DriverToolsMixin
from openavc.cloud.tools.macro_tools import MacroToolsMixin
from openavc.cloud.tools.plugin_tools import PluginToolsMixin
from openavc.cloud.tools.project_tools import ProjectToolsMixin
from openavc.cloud.tools.script_tools import ScriptToolsMixin
from openavc.cloud.tools.system_tools import SystemToolsMixin
from openavc.cloud.tools.ui_tools import UIToolsMixin
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.cloud.agent import CloudAgent
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus

log = get_logger(__name__)


# --- Tool result classification ---

def _tool_result_is_error(result: Any) -> bool:
    """Decide whether a tool's return value represents a failure.

    The tool mixins signal failure in two ways: an explicit ``success: False``
    in the returned dict, or (the common convention) a dict whose only failure
    signal is a truthy ``error`` key. Both must be reported back to the model
    with ``success=False`` so a failed mutation isn't delivered as a success
    with the error buried in the result body (the cloud sets ``is_error`` on
    the Anthropic tool_result from this flag).

    An explicit ``success`` key always wins, so a tool that returns
    ``{"success": True, "error": None, ...}`` is never misread as a failure.
    Non-dict returns (lists, scalars) are always treated as success.
    """
    if not isinstance(result, dict):
        return False
    if "success" in result:
        return not result["success"]
    return bool(result.get("error"))


# --- What a tool that raised answers ---

#: The answer for an exception the platform did not word. Its type and text
#: are in the local log, which the Programmer's Log view shows.
TOOL_FAILED = "The tool failed with an error on the server. The Programmer's Log has the details."

# The errnos friendly_error answers with a fixed sentence of its own.
_WORDED_ERRNOS = frozenset({
    errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EADDRINUSE, errno.ENOSPC, errno.EROFS,
})


def describe_tool_failure(exc: Exception) -> str:
    """What the cloud is told when a tool raised instead of answering.

    The answer is kept in the cloud chat history, so an exception's text
    reaches it only when the platform wrote that text for a reader: a typed
    refusal, which tells the model what to change in its next call. The
    project's validation errors are reduced to each field and its message.
    Anything else (an operating system or library error that may hold a local
    path or an address, a driver's or a plugin's own text, Python's own
    message for a mistake) gets :data:`TOOL_FAILED`.
    """
    from pydantic import ValidationError

    from openavc.api.error_messages import _WORDED_CONNECTION_ERRORS, friendly_error
    from openavc.core.device_manager import DeviceNotFoundError
    from openavc.core.installed_equipment import InstalledEquipmentError
    from openavc.core.isc import ISCRemoteError
    from openavc.drivers.base import (
        CommandParamError,
        CommandPartialError,
        CommandRefusedError,
        DeviceSettingValueError,
        UnknownCommandError,
        WakeAddressError,
    )
    from openavc.drivers.command_files import FileCommandError
    from openavc.drivers.command_outcome import CommandOutcomeError
    from openavc.reach_devices import AdapterUnavailable
    from openavc.utils.community_integrity import CommunityArtifactError

    if isinstance(exc, ValidationError):
        return _field_problems(exc)
    if isinstance(exc, WakeAddressError):
        return friendly_error(exc)
    if isinstance(exc, (
        DeviceNotFoundError, UnknownCommandError, CommandParamError,
        DeviceSettingValueError, FileCommandError, CommandOutcomeError,
        CommandRefusedError, CommandPartialError, AdapterUnavailable,
        InstalledEquipmentError, CommunityArtifactError, ISCRemoteError,
    )) and str(exc).strip():
        return str(exc)
    if isinstance(exc, ConnectionError):
        # The device manager's own refusals carry the device's id.
        if getattr(exc, "device_id", None) is not None:
            return str(exc)
        return friendly_error(exc) if isinstance(exc, _WORDED_CONNECTION_ERRORS) else TOOL_FAILED
    if _is_worded_os_error(exc):
        return friendly_error(exc)
    return TOOL_FAILED


def _field_problems(exc: Any) -> str:
    """A validation error as each field and its message, never the input."""
    problems = [
        f"{'.'.join(str(part) for part in error.get('loc', ())) or 'value'}: "
        f"{error.get('msg', '')}"
        for error in exc.errors(include_url=False, include_input=False)
    ]
    return "; ".join(problems) or TOOL_FAILED


def _is_worded_os_error(exc: Exception) -> bool:
    """An operating system error friendly_error answers with a fixed sentence
    of its own: a refused permission, or one of the errnos it words."""
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in _WORDED_ERRNOS


# How a tool runs. LOCKED is the default: the tool runs under the project lock,
# so two tools never interleave on engine.project, and the conversation's one
# pre-AI backup is made under it before the first LOCKED tool. Tools that reach
# a device (send to it, test its connection, start a scan) or write state are
# LOCKED too, and so are review_ui and review_custom_ui, which only read: one needless backup was preferred to two
# reviews that run differently. READ is a tool that only reads; it runs beside
# any other, without the lock. UNLOCKED is execute_macro alone: it waits on a
# macro run for tens of seconds and never changes engine.project, so holding
# the lock would block project edits. READ and UNLOCKED run the same way;
# _READ_ONLY_TOOLS keeps them apart. A name missing from TOOLS is answered
# "Unknown tool".
READ, UNLOCKED, LOCKED = "read", "unlocked", "locked"

#: Every tool the cloud may call, and how it runs. Tool ``x`` is the handler's
#: method ``_x``, defined in one of the mixins.
TOOLS: dict[str, str] = {
    # Reading / Searching
    "get_project_summary": READ,
    "get_project_state": READ,
    "get_state_value": READ,
    "get_state_history": READ,
    "list_devices": READ,
    "get_device_info": READ,
    "list_drivers": READ,
    "search_community_drivers": READ,
    "get_community_driver_detail": READ,
    "find_driver_for_device": READ,
    "get_installed_drivers": READ,
    "get_driver_definition": READ,
    "get_script_source": READ,
    "get_script_health": READ,
    "get_logs": READ,
    "list_triggers": READ,
    "get_macro": READ,
    "get_ui_page": READ,
    # Writing / Creating
    "update_project_metadata": LOCKED,
    "update_device": LOCKED,
    "delete_device": LOCKED,
    "add_device": LOCKED,
    "add_device_group": LOCKED,
    "update_device_group": LOCKED,
    "delete_device_group": LOCKED,
    "add_variable": LOCKED,
    "update_variable": LOCKED,
    "delete_variable": LOCKED,
    "add_macro": LOCKED,
    "update_macro": LOCKED,
    "delete_macro": LOCKED,
    "add_ui_page": LOCKED,
    "update_ui_page": LOCKED,
    "delete_ui_page": LOCKED,
    "add_ui_elements": LOCKED,
    "update_ui_element": LOCKED,
    "delete_ui_elements": LOCKED,
    "add_master_element": LOCKED,
    "update_master_element": LOCKED,
    "delete_master_element": LOCKED,
    "install_community_driver": LOCKED,
    "create_driver_definition": LOCKED,
    "update_driver_definition": LOCKED,
    "create_script": LOCKED,
    "update_script_source": LOCKED,
    "delete_script": LOCKED,
    # Plugins
    "list_plugins": READ,
    "browse_community_plugins": READ,
    "install_plugin": LOCKED,
    "uninstall_plugin": LOCKED,
    "enable_plugin": LOCKED,
    "disable_plugin": LOCKED,
    "get_plugin_config": READ,
    "update_plugin_config": LOCKED,
    # Discovery
    "start_discovery_scan": LOCKED,
    "get_discovery_results": READ,
    # Themes
    "list_themes": READ,
    "get_theme": READ,
    "apply_theme": LOCKED,
    # Assets
    "list_assets": READ,
    "delete_asset": LOCKED,
    # ISC
    "get_isc_status": READ,
    "list_isc_peers": READ,
    "send_isc_command": LOCKED,
    # Device settings
    "get_device_settings": READ,
    "set_device_setting": LOCKED,
    # Custom UI: the project's own ui/ folder, and the stylesheet that
    # restyles the built-in controls. The three writers are LOCKED, so they
    # ride the one-time pre-AI backup that already covers ui/.
    "list_ui_files": READ,
    "read_ui_file": READ,
    "write_ui_file": LOCKED,
    "delete_ui_file": LOCKED,
    "get_project_stylesheet": READ,
    "set_project_stylesheet": LOCKED,
    "review_custom_ui": LOCKED,
    # UI simulation
    "simulate_ui_action": LOCKED,
    "review_ui": LOCKED,
    # Impact checking
    "check_references": READ,
    # Async / Waiting
    "wait": READ,
    # Actions
    "send_device_command": LOCKED,
    "test_device_connection": LOCKED,
    "test_driver_command": LOCKED,
    "execute_macro": UNLOCKED,
    "cancel_macro": LOCKED,
    "set_state_value": LOCKED,
    "test_trigger": LOCKED,
}


class AIToolHandler(
    ProjectToolsMixin,
    DeviceToolsMixin,
    DriverToolsMixin,
    CatalogToolsMixin,
    ScriptToolsMixin,
    UIToolsMixin,
    CustomUIToolsMixin,
    PluginToolsMixin,
    MacroToolsMixin,
    SystemToolsMixin,
):
    """
    Handles AI tool calls from the cloud and dispatches to local subsystems.

    Every tool maps to an existing local REST API operation or engine call,
    ensuring the AI has the same capabilities as the Programmer IDE.

    Tool handler methods are organized into domain-specific mixins under
    cloud/tools/; this class runs them, as ``TOOLS`` says each one runs.
    """

    # From TOOLS: the tools that change nothing, and every tool that runs
    # without the project lock (what _execute_tool asks).
    _READ_ONLY_TOOLS: set[str] = {name for name, run in TOOLS.items() if run == READ}
    _CONCURRENT_SAFE_TOOLS: set[str] = {name for name, run in TOOLS.items() if run != LOCKED}

    # Idle timeout before resetting the AI backup flag (seconds)
    _AI_BACKUP_IDLE_TIMEOUT = 300  # 5 minutes

    def __init__(
        self,
        agent: CloudAgent,
        devices: DeviceManager,
        events: EventBus,
        project_path=None,
    ):
        self._agent = agent
        self._devices = devices
        self._events = events
        self._project_path = project_path

        # Backup tracking: create one backup before the first write in a conversation
        self._ai_backup_created: bool = False
        self._ai_last_write_time: float = 0

        # Serialize project-mutating tools so concurrent tool tasks can't
        # interleave on the shared engine.project (lost updates). Hold strong
        # refs to in-flight tasks so they aren't GC'd mid-run and can be
        # cancelled on shutdown.
        self._project_lock = asyncio.Lock()
        self._pending_tasks: set[asyncio.Task] = set()

        # This handler's own copy: a test swaps one entry for a stand-in.
        self._tools: dict[str, Any] = {name: getattr(self, f"_{name}") for name in TOOLS}

    async def handle(self, msg: dict[str, Any]) -> None:
        """Route an incoming AI_TOOL_CALL message to the appropriate handler.

        Dispatches tool execution as a background task so long-running tools
        (discovery scans, wait) don't block the agent's receive loop.
        """
        payload = extract_payload(msg)
        request_id = payload.get("request_id", "")
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {})

        log.info(f"AI tool call: {tool_name} (request_id={request_id})")

        handler = self._tools.get(tool_name)
        if not handler:
            await self._send_result(
                request_id, False,
                error=f"Unknown tool: {tool_name}",
            )
            return

        # Run in background so the receive loop stays responsive to pings/acks.
        # Keep a strong ref (GC-safety) and track it so shutdown() can cancel
        # in-flight tools when the agent stops.
        task = asyncio.create_task(self._execute_tool(request_id, tool_name, handler, tool_input))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _execute_tool(
        self, request_id: str, tool_name: str, handler: Any, tool_input: dict
    ) -> None:
        """Execute a tool handler and send the result back to the cloud."""
        try:
            if tool_name in self._CONCURRENT_SAFE_TOOLS:
                result = await handler(tool_input)
            else:
                # Project-mutating tool: serialize on the project lock so two
                # concurrent tools can't interleave on engine.project, and run
                # the one-time pre-AI backup under the same lock so it happens
                # exactly once even under concurrency.
                async with self._project_lock:
                    await self._maybe_create_pre_ai_backup()
                    result = await handler(tool_input)
            # A tool that returns an {"error": ...} / {"success": False} dict
            # has failed even though it didn't raise — report it as a failure
            # (carrying the body) so the model gets a structured error signal
            # instead of a success with the error hidden in the result.
            if _tool_result_is_error(result):
                err = result.get("error") if isinstance(result, dict) else None
                await self._send_result(
                    request_id, False, result=result,
                    error=str(err) if err else "The tool reported an error.",
                )
            else:
                await self._send_result(request_id, True, result=result)
        except Exception as e:
            # The type and text in the message itself, not only the traceback:
            # the Log view shows a record's message, and the answer below
            # sends the reader there.
            log.exception(f"AI tool handler: {tool_name} failed: {type(e).__name__}: {e}")
            # Worded before it leaves the local trust boundary: the answer is
            # kept in the cloud chat history.
            await self._send_result(request_id, False, error=describe_tool_failure(e))

    async def _maybe_create_pre_ai_backup(self) -> None:
        """Create one backup before the first project-mutating tool in an AI
        conversation. The caller holds ``self._project_lock`` so the flag
        check and the backup are atomic (no double backup under concurrency).
        """
        # Reset the flag if idle for too long (treat as a new conversation).
        if (self._ai_last_write_time
                and time.monotonic() - self._ai_last_write_time > self._AI_BACKUP_IDLE_TIMEOUT):
            self._ai_backup_created = False

        if not self._ai_backup_created and self._project_path:
            try:
                from openavc.core.backup_manager import create_backup
                await asyncio.to_thread(create_backup, Path(self._project_path), "Before AI changes")
            except Exception:
                # Leave the flag unset so the next mutating tool retries the
                # backup. Latching it True on failure would silently disable
                # the safety net for the rest of the AI editing session.
                log.warning(
                    "Could not create pre-AI backup; will retry before the next change",
                    exc_info=True,
                )
            else:
                self._ai_backup_created = True

        self._ai_last_write_time = time.monotonic()

    async def shutdown(self) -> None:
        """Cancel any in-flight tool tasks.

        Called when the cloud agent stops so background tool execution doesn't
        outlive the agent, write to a torn-down engine, or leak tasks.
        """
        tasks = list(self._pending_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._pending_tasks.clear()

    # Tool handler methods are defined in the domain-specific mixins:
    # - ProjectToolsMixin: project state, metadata
    # - DeviceToolsMixin: devices, device groups, settings, commands
    # - DriverToolsMixin: installed drivers, definitions, the raw command test
    # - CatalogToolsMixin: the community driver catalog
    # - ScriptToolsMixin: scripts
    # - UIToolsMixin: UI pages, elements
    # - PluginToolsMixin: plugin management
    # - MacroToolsMixin: macros, triggers, variables
    # - SystemToolsMixin: logs, discovery, themes, assets, ISC

    # ===== HELPERS =====

    def _get_engine(self):
        try:
            from openavc.api.rest import _engine
            return _engine
        except ImportError:
            return None

    def _get_driver_repo_dir(self) -> Path:
        from openavc.system_config import DRIVER_REPO_DIR
        return DRIVER_REPO_DIR

    def _get_driver_dirs(self) -> list[Path]:
        from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
        return [
            DRIVER_DEFINITIONS_DIR,
            DRIVER_REPO_DIR,
        ]

    async def _send_result(
        self, request_id: str, success: bool,
        result: Any = None, error: str | None = None
    ) -> None:
        if not request_id:
            return
        if result is not None:
            try:
                json.dumps(result)
            except (TypeError, ValueError):
                result = str(result)
        await self._agent.send_message(AI_TOOL_RESULT, build_ai_tool_result_payload(
            request_id, success, result=result, error=error,
        ))
