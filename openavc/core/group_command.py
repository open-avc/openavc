"""Sending one command to every device in a group: the ``group.command`` step.

The project's device groups are kept here (``DeviceGroups``), and so are the
rules a fan-out follows: a member the project does not have is reported as
missing, never as offline; a member the command cannot reach now is skipped
and reported offline; the rest are sent the command together; and the
per-device results come back in the order the group lists its members, the
order somebody wrote them in and the order the IDE shows.

One dead member out of eight is not a failed step -- the room did most of what
was asked. Reaching nobody is (``reached_nobody``), and it is reported in the
first member's own words, because a name somebody can go and look at beats a
count.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from collections.abc import Collection
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from openavc.core.macro_text import StepWording
from openavc.core.state_store import StateStore
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.device_manager import DeviceManager

# The macro engine's own logger: the log view files these lines under Macros
# by that name (utils/log_buffer.py).
log = get_logger("openavc.core.macro_engine")


class DeviceGroups:
    """The project's device groups: each one's members, and its name."""

    def __init__(self) -> None:
        self._members: dict[str, list[str]] = {}
        self._names: dict[str, str] = {}

    def load(self, groups: list[dict[str, Any]]) -> None:
        """Replace the groups with the project's."""
        self._members.clear()
        self._names.clear()
        for group in groups:
            group_id = group.get("id", "")
            if group_id:
                self._members[group_id] = group.get("device_ids", [])
                # Kept so a fan-out can name the group the way the IDE lists
                # it. The id is what the macro step carries; the name is what
                # somebody scanning the Groups list will recognise.
                self._names[group_id] = group.get("name", "") or group_id
        if self._members:
            log.info(f"Loaded {len(self._members)} device group(s)")

    def members(self, group_id: str) -> list[str] | None:
        """The group's member ids, or None for a group the project lacks."""
        return self._members.get(group_id)

    def name(self, group_id: str) -> str:
        return self._names.get(group_id) or group_id


@dataclass
class _Members:
    """A group's members, sorted by what the fan-out does with each."""

    sendable: list[str] = field(default_factory=list)
    offline: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class GroupCommand:
    """One ``group.command`` step, from its members to its per-device results."""

    def __init__(
        self,
        devices: DeviceManager,
        state: StateStore,
        wording: StepWording,
        groups: DeviceGroups,
    ) -> None:
        self._devices = devices
        self._state = state
        self._wording = wording
        self._groups = groups

    async def send(
        self, group_id: str, command: str, params: dict[str, Any],
        project_device_ids: Callable[[], set[str] | None] | None,
        *, saved: Collection[str] = (),
    ) -> list[dict[str, Any]] | None:
        """Send ``command`` to the group's members; return each member's result.

        None for a group with no members, which is skipped with nothing to
        report. Raises ``ValueError`` for a group the project does not have.
        ``project_device_ids`` answers which devices the project has; when it
        is unwired, or answers None, no member is called missing. ``saved``
        names the parameters the step saved on this system, filled in by each
        member's send (``DeviceManager.send_command``).
        """
        device_ids = self._groups.members(group_id)
        if device_ids is None:
            raise ValueError(f"Device group '{group_id}' not found. Check that the group exists in your project.")
        if not device_ids:
            log.debug(f"  Macro step: group '{group_id}' is empty, skipping")
            return None
        known_device_ids = project_device_ids() if project_device_ids else None
        members = self._sort(group_id, device_ids, command, known_device_ids)
        extra = {"saved": saved} if saved else {}
        outcome = await self._sent(
            group_id, command, members.sendable,
            lambda did: self._devices.send_command(did, command, params, **extra),
        )
        for did in members.offline:
            outcome[did] = self._offline(did)
        for did in members.missing:
            outcome[did] = self._missing(group_id, did)
        return [outcome[did] for did in device_ids if did in outcome]

    def _sort(
        self, group_id: str, device_ids: list[str], command: str,
        known_device_ids: set[str] | None,
    ) -> _Members:
        members = _Members()
        for did in device_ids:
            # A member id that names nothing is not an offline device: state
            # holds nothing for a device that was never configured, so it used
            # to read as one, and an integrator who typo'd a member id, or whose
            # device was deleted months ago, went looking for a network problem
            # on a device that does not exist. Asked of the project's devices
            # rather than the live drivers on purpose -- a disabled or orphaned
            # device has no driver instance but is very much in the project,
            # and calling it missing would be the same lie pointed the other way.
            if known_device_ids is not None and did not in known_device_ids:
                log.warning(
                    f"  Group command: group '{group_id}' lists device "
                    f"'{did}', which is not in this project"
                )
                members.missing.append(did)
            elif not self._devices.command_goes_out(did, command):
                log.debug(f"  Group command: skipping offline device '{did}'")
                members.offline.append(did)
            else:
                members.sendable.append(did)
        return members

    async def _sent(
        self, group_id: str, command: str, sendable: list[str],
        send: Callable[[str], Awaitable[Any]],
    ) -> dict[str, dict[str, Any]]:
        """Send to every reachable member at once (``send`` sends to one);
        each one's result by id."""
        tasks = [send(did) for did in sendable]
        results: list[Any] = []
        if tasks:
            log.debug(f"  Macro step: group '{group_id}'.{command} -> {len(tasks)} device(s)")
            results = await asyncio.gather(*tasks, return_exceptions=True)
        outcome: dict[str, dict[str, Any]] = {}
        for j, result in enumerate(results):
            did = sendable[j] if j < len(sendable) else "unknown"
            device_name = self._device_name(did)
            if isinstance(result, Exception):
                log.error(f"  Group command error on '{device_name}': {result}")
                failure = self._wording.command_failure(did, command, result)
                outcome[did] = {
                    "device_id": did, "name": device_name, "success": False,
                    "error": failure.programmer,
                    "message": failure.panel,
                }
            else:
                outcome[did] = {"device_id": did, "name": device_name, "success": True}
        return outcome

    def _offline(self, did: str) -> dict[str, Any]:
        # Synthesised so an offline member reads the same way it would from a
        # device.command step -- the panel must not learn a second sentence
        # for the same fact.
        offline = ConnectionError(f"Device '{did}' is not connected")
        return {
            "device_id": did, "name": self._device_name(did), "success": False,
            "error": "Device offline",
            "message": self._wording.device_message(did, offline),
        }

    def _missing(self, group_id: str, did: str) -> dict[str, Any]:
        # The one place a group can say more than device.command can: it knows
        # WHICH group holds the dead id, and that is the fact somebody needs to
        # go and fix it. The error code stays aligned with what a
        # device.command step raises for the same cause, so anything matching
        # on the code sees one answer.
        group_name = self._groups.name(group_id)
        return {
            "device_id": did, "name": did, "success": False,
            "error": "Device not found",
            "message": (
                f"Device '{did}' is listed in group '{group_name}' but is not in "
                f"this project. Remove it from the group, or add the device back."
            ),
        }

    def _device_name(self, did: str) -> str:
        return self._state.get(f"device.{did}.name") or did


def reached_nobody(device_results: list[dict[str, Any]]) -> tuple[str, str] | None:
    """``(error, message)`` when no member took the command, else None.

    The first member's own reason, in the order the group lists them.
    """
    failures = [r for r in device_results if not r["success"]]
    if failures and len(failures) == len(device_results):
        first = failures[0]
        return (str(first.get("error") or ""), str(first.get("message") or ""))
    return None
