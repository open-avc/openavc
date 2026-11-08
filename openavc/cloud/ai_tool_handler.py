"""
OpenAVC Cloud — AI tool call handler.

Handles AI_TOOL_CALL messages from the cloud platform. Each tool call
is dispatched to the appropriate local function — reading project state,
controlling devices, modifying configurations, installing drivers, etc.
Results are sent back as AI_TOOL_RESULT messages.
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
        problems = [
            f"{'.'.join(str(part) for part in error.get('loc', ())) or 'value'}: "
            f"{error.get('msg', '')}"
            for error in exc.errors(include_url=False, include_input=False)
        ]
        return "; ".join(problems) or TOOL_FAILED
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
    if isinstance(exc, PermissionError) or (
        isinstance(exc, OSError) and getattr(exc, "errno", None) in _WORDED_ERRNOS
    ):
        return friendly_error(exc)
    return TOOL_FAILED


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
    cloud/tools/ — this class provides the dispatch table and execution
    infrastructure.
    """

    # Tools that only read data and never modify the project
    _READ_ONLY_TOOLS: set[str] = {
        "get_project_summary", "get_project_state", "get_state_value",
        "get_state_history", "list_devices", "get_device_info",
        "list_drivers", "search_community_drivers", "get_community_driver_detail",
        "find_driver_for_device", "get_installed_drivers",
        "get_driver_definition", "get_script_source", "get_script_health",
        "get_logs",
        "list_triggers", "get_macro", "get_ui_page",
        "list_plugins", "browse_community_plugins", "get_plugin_config",
        "list_themes", "get_theme", "list_assets",
        "get_isc_status", "list_isc_peers",
        "get_device_settings", "check_references",
        "get_discovery_results", "wait",
        "list_ui_files", "read_ui_file", "get_project_stylesheet",
    }

    # Tools that may run without the project-serialization lock: every
    # read-only tool, plus execute_macro — it waits on a macro run (bounded
    # now, but still tens of seconds) and never mutates engine.project, so
    # holding the lock would needlessly block project edits. Everything else
    # (all project-mutating tools, and unclassified tools) takes the lock by
    # default, so a new write tool is serialized automatically.
    _CONCURRENT_SAFE_TOOLS = _READ_ONLY_TOOLS | {"execute_macro"}

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

        # Tool dispatch table
        self._tools: dict[str, Any] = {
            # Reading / Searching
            "get_project_summary": self._get_project_summary,
            "get_project_state": self._get_project_state,
            "get_state_value": self._get_state_value,
            "get_state_history": self._get_state_history,
            "list_devices": self._list_devices,
            "get_device_info": self._get_device_info,
            "list_drivers": self._list_drivers,
            "search_community_drivers": self._search_community_drivers,
            "get_community_driver_detail": self._get_community_driver_detail,
            "find_driver_for_device": self._find_driver_for_device,
            "get_installed_drivers": self._get_installed_drivers,
            "get_driver_definition": self._get_driver_definition,
            "get_script_source": self._get_script_source,
            "get_script_health": self._get_script_health,
            "get_logs": self._get_logs,
            "list_triggers": self._list_triggers,
            "get_macro": self._get_macro,
            "get_ui_page": self._get_ui_page,
            # Writing / Creating
            "update_project_metadata": self._update_project_metadata,
            "update_device": self._update_device,
            "delete_device": self._delete_device,
            "add_device": self._add_device,
            "add_device_group": self._add_device_group,
            "update_device_group": self._update_device_group,
            "delete_device_group": self._delete_device_group,
            "add_variable": self._add_variable,
            "update_variable": self._update_variable,
            "delete_variable": self._delete_variable,
            "add_macro": self._add_macro,
            "update_macro": self._update_macro,
            "delete_macro": self._delete_macro,
            "add_ui_page": self._add_ui_page,
            "update_ui_page": self._update_ui_page,
            "delete_ui_page": self._delete_ui_page,
            "add_ui_elements": self._add_ui_elements,
            "update_ui_element": self._update_ui_element,
            "delete_ui_elements": self._delete_ui_elements,
            "add_master_element": self._add_master_element,
            "update_master_element": self._update_master_element,
            "delete_master_element": self._delete_master_element,
            "install_community_driver": self._install_community_driver,
            "create_driver_definition": self._create_driver_definition,
            "update_driver_definition": self._update_driver_definition,
            "create_script": self._create_script,
            "update_script_source": self._update_script_source,
            "delete_script": self._delete_script,
            # Plugins
            "list_plugins": self._list_plugins,
            "browse_community_plugins": self._browse_community_plugins,
            "install_plugin": self._install_plugin,
            "uninstall_plugin": self._uninstall_plugin,
            "enable_plugin": self._enable_plugin,
            "disable_plugin": self._disable_plugin,
            "get_plugin_config": self._get_plugin_config,
            "update_plugin_config": self._update_plugin_config,
            # Discovery
            "start_discovery_scan": self._start_discovery_scan,
            "get_discovery_results": self._get_discovery_results,
            # Themes
            "list_themes": self._list_themes,
            "get_theme": self._get_theme,
            "apply_theme": self._apply_theme,
            # Assets
            "list_assets": self._list_assets,
            "delete_asset": self._delete_asset,
            # ISC
            "get_isc_status": self._get_isc_status,
            "list_isc_peers": self._list_isc_peers,
            "send_isc_command": self._send_isc_command,
            # Device settings
            "get_device_settings": self._get_device_settings,
            "set_device_setting": self._set_device_setting,
            # Custom UI: the project's own ui/ folder, and the stylesheet that
            # restyles the built-in controls. The three writers are deliberately
            # outside _CONCURRENT_SAFE_TOOLS, so they take the project lock and
            # ride the one-time pre-AI backup that already covers ui/.
            "list_ui_files": self._list_ui_files,
            "read_ui_file": self._read_ui_file,
            "write_ui_file": self._write_ui_file,
            "delete_ui_file": self._delete_ui_file,
            "get_project_stylesheet": self._get_project_stylesheet,
            "set_project_stylesheet": self._set_project_stylesheet,
            "review_custom_ui": self._review_custom_ui,
            # UI simulation
            "simulate_ui_action": self._simulate_ui_action,
            "review_ui": self._review_ui,
            # Impact checking
            "check_references": self._check_references,
            # Async / Waiting
            "wait": self._wait,
            # Actions
            "send_device_command": self._send_device_command,
            "test_device_connection": self._test_device_connection,
            "test_driver_command": self._test_driver_command,
            "execute_macro": self._execute_macro,
            "cancel_macro": self._cancel_macro,
            "set_state_value": self._set_state_value,
            "test_trigger": self._test_trigger,
        }

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
