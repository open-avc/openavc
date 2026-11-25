"""
OpenAVC Cloud — AI tool call handler.

Handles AI_TOOL_CALL messages from the cloud platform: looks the tool up in
``TOOLS`` (``cloud/tool_declarations.py``: every tool the cloud may call,
whether it runs under the project lock, and what it takes), runs it as a
background task, and sends the result back as an AI_TOOL_RESULT. An input of
the wrong shape, or a required one left out, is refused by name before the
tool runs (``cloud/tools/input_shapes.py``). A tool that returned
an error or raised is answered as a failure, and what a raised exception may
say is ``describe_tool_failure``'s call, because the answer is kept in the
cloud chat history. The tools
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
    AI_TOOL_CALL, AI_TOOL_RESULT, build_ai_tool_result_payload, read_as,
)
from openavc.api.error_messages import describe_not_running
from openavc.cloud.tools import collecting_not_running
from openavc.cloud.tools.catalog_tools import CatalogToolsMixin
from openavc.cloud.tools.custom_ui_tools import CustomUIToolsMixin
from openavc.cloud.tools.device_tools import DeviceToolsMixin
from openavc.cloud.tools.driver_tools import DriverToolsMixin
from openavc.cloud.tools.input_shapes import input_problem
from openavc.cloud.tools.macro_tools import MacroToolsMixin
from openavc.cloud.tools.plugin_tools import PluginToolsMixin
from openavc.cloud.tools.project_tools import ProjectToolsMixin
from openavc.cloud.tools.script_tools import ScriptToolsMixin
from openavc.cloud.tools.system_tools import SystemToolsMixin
from openavc.cloud.tools.ui_tools import UIToolsMixin
from openavc.cloud.tool_declarations import LOCKED, READ, TOOLS
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


#: The backup taken before the cloud AI's first change in a conversation.
_AI_BACKUP = "Before AI changes"


def _no_backup_note(error: BaseException) -> str:
    """What a tool's answer says when its change was made without the
    conversation's backup, which could not be saved: no path, none of the
    operating system's text, since the answer is kept in the cloud chat
    history."""
    from openavc.api.error_messages import backup_failure_why

    return (
        f'No backup was saved before this change, so the Backups list has no "{_AI_BACKUP}" '
        f"backup to go back to. {backup_failure_why(error)}"
    )


def describe_tool_failure(exc: Exception) -> str:
    """What the cloud is told when a tool raised instead of answering.

    The answer is kept in the cloud chat history, so an exception's text
    reaches it only when the platform wrote that text for a reader: a typed
    refusal, which tells the model what to change in its next call. The
    project's validation errors are reduced to each field and its message,
    a project change that did not run is said in the words every door uses
    (``describe_change_failure``), and a failed project save says why
    without the file's path. Anything
    else (an operating system or library error that may hold a local path or
    an address, a driver's or a plugin's own text, Python's own message for a
    mistake) gets :data:`TOOL_FAILED`.
    """
    from pydantic import ValidationError

    from openavc.api.error_messages import describe_change_failure, friendly_error
    from openavc.core.device_manager import DeviceNotFoundError
    from openavc.core.installed_equipment import InstalledEquipmentError
    from openavc.core.isc import ISCRemoteError
    from openavc.core.project_loader import ProjectSaveError
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
    change = describe_change_failure(exc)
    if change is not None:
        return change.sentence
    if isinstance(exc, ProjectSaveError):
        return exc.reason
    if isinstance(exc, WakeAddressError):
        return friendly_error(exc)
    if isinstance(exc, (
        DeviceNotFoundError, UnknownCommandError, CommandParamError,
        DeviceSettingValueError, FileCommandError, CommandOutcomeError,
        CommandRefusedError, CommandPartialError, AdapterUnavailable,
        InstalledEquipmentError, CommunityArtifactError, ISCRemoteError,
    )) and str(exc).strip():
        return str(exc)
    return _system_error_sentence(exc)


def _system_error_sentence(exc: Exception) -> str:
    """A connection or operating-system error: the device manager's own
    refusal as written, one ``friendly_error`` answers with a fixed sentence,
    and :data:`TOOL_FAILED` for the rest."""
    from openavc.api.error_messages import _WORDED_CONNECTION_ERRORS, friendly_error

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
    _READ_ONLY_TOOLS: set[str] = {name for name, tool in TOOLS.items() if tool.run == READ}
    _CONCURRENT_SAFE_TOOLS: set[str] = {name for name, tool in TOOLS.items() if tool.run != LOCKED}

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
        payload = read_as(AI_TOOL_CALL, msg)
        request_id = payload["request_id"]
        tool_name = payload["tool_name"]
        tool_input = payload["tool_input"]
        if tool_input is None:  # a tool that takes nothing may be sent null
            tool_input = {}

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
        # A field of the wrong JSON type is answered by name before the tool
        # (or the lock and the pre-AI backup) is reached: inside the tool it
        # fails with Python's own error, which the cloud is not told.
        problem = input_problem(tool_name, tool_input)
        if problem:
            await self._send_result(request_id, False, result={"error": problem}, error=problem)
            return
        try:
            result = await self._run_tool(tool_name, handler, tool_input)
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

    async def _run_tool(self, tool_name: str, handler: Any, tool_input: dict) -> Any:
        """The tool's own answer. A project change it made that could not
        start a part is still applied, so the answer is not a failure: it
        names each such part under ``not_running``, as Save does. A tool run
        without the conversation's backup, which could not be saved, says
        so under ``backup_not_saved``."""
        no_backup = None
        with collecting_not_running() as not_running:
            if tool_name in self._CONCURRENT_SAFE_TOOLS:
                result = await handler(tool_input)
            else:
                # Project-mutating tool: serialize on the project lock so two
                # concurrent tools can't interleave on engine.project, and run
                # the one-time pre-AI backup under the same lock so it happens
                # exactly once even under concurrency.
                async with self._project_lock:
                    no_backup = await self._maybe_create_pre_ai_backup()
                    result = await handler(tool_input)
        if not (not_running or no_backup):
            return result
        # The missing backup first: the cloud cuts a long answer off at its
        # end before the model reads it.
        first = {"backup_not_saved": no_backup} if no_backup else {}
        last = {"not_running": describe_not_running(not_running)} if not_running else {}
        body = result if isinstance(result, dict) else {"result": result}
        return {**first, **body, **last}

    async def _maybe_create_pre_ai_backup(self) -> str | None:
        """Create one backup before the first project-mutating tool in an AI
        conversation (``backup_manager.capture``). The caller holds
        ``self._project_lock`` so the flag check and the backup are atomic
        (no double backup under concurrency).

        A backup that could not be saved does not stop the tool, whose first
        call in a conversation may be a device command or a review as well as
        an edit: the answer is the sentence the tool's answer carries, and
        the next locked tool tries the backup again. None otherwise.
        """
        # Reset the flag if idle for too long (treat as a new conversation).
        if (self._ai_last_write_time
                and time.monotonic() - self._ai_last_write_time > self._AI_BACKUP_IDLE_TIMEOUT):
            self._ai_backup_created = False

        no_backup = None
        if not self._ai_backup_created and self._project_path:
            try:
                await self._capture_before_ai()
            except Exception as error:
                # Leave the flag unset so the next mutating tool retries the
                # backup. Latching it True on failure would silently disable
                # the safety net for the rest of the AI editing session.
                log.warning(
                    "Could not create pre-AI backup; will retry before the next change",
                    exc_info=True,
                )
                no_backup = _no_backup_note(error)
            else:
                self._ai_backup_created = True

        self._ai_last_write_time = time.monotonic()
        return no_backup

    async def _capture_before_ai(self) -> None:
        """The pre-AI backup, read as one project through the engine
        (``read_project_files``), so a replace running meanwhile never
        swaps the files it has yet to read. With no engine, nothing can
        replace the project, and the backup is taken as it is."""
        from openavc.core.backup_manager import capture

        def take():
            return capture(Path(self._project_path), _AI_BACKUP)

        engine = self._get_engine()
        await (engine.read_project_files(take) if engine is not None else take())

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
