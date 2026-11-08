"""A driver's setup actions: the provisioning wizards it declares.

A setup action is an action of ``kind: setup`` (drivers/actions.py). Unlike a
command it can run while the device is offline, brings its own out-of-band
transport, reports multi-step progress, and may rewrite the device's
connection config and reconnect on success. Everything device-specific
(which transport, which commands, what the config delta is) lives in the
driver's ``run_setup_action`` handler; the platform stays generic.

The platform (core/setup_actions.py) calls ``run_setup_action`` with a live
``progress`` callback and, for the length of that call only, installs the
setup context (``_set_setup_context``) that backs ``request_config_update``
and ``request_reconnect``; outside a run both refuse.

``SetupWizard`` is a mixin ``BaseDriver`` inherits, so every name stays on
the driver object, where a driver overrides ``run_setup_action``. From its
host this needs ``DRIVER_INFO`` and the ``_setup_context`` its constructor
sets to None.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class SetupWizard:
    """A driver's setup actions, for ``BaseDriver`` (see the module docstring)."""

    # Supplied by BaseDriver; declared here for the methods.
    DRIVER_INFO: dict[str, Any]
    _setup_context: Any

    async def run_setup_action(
        self,
        action_id: str,
        params: dict[str, Any],
        progress: Callable[..., Awaitable[None]],
    ) -> dict[str, Any]:
        """Run a declared setup action. May run while the device is offline.

        ``progress(step, pct=None)`` is awaitable and streams a live progress
        line to the UI (``pct`` is an optional 0-100 percentage). The handler
        may open its own transports independent of the device's normal one, and
        may call ``await self.request_config_update({...})`` to persist new
        connection settings and ``await self.request_reconnect()`` to bring the
        device back online over them. Default raises NotImplementedError.

        Return a result dict. A ``"message"`` in it is the sentence the wizard
        shows the operator when the run finishes, so write it for them: what
        was found, and any count worth sanity-checking ("Found 5 endpoints —
        2 encoders, 3 decoders").

        Two ways to report failure, and they are not interchangeable:

        - **Raise** when the action could not run — the host was unreachable,
          a transport blew up, the driver has nothing to do for this action id.
          The exception is logged with a traceback.
        - **Return ``{"success": False, "message": ...}``** when the action ran
          and the answer is no: a community string the device ignores, a model
          this driver doesn't drive, a declared value the device has no OID
          for. The message is the whole report, so say what to check. There is
          no traceback — an operator typing the wrong IP is not a platform
          fault.

        Either way the run terminates as ``error`` on the ``action.progress``
        channel and the wizard renders it as a failure; only the log differs.
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement "
            f"run_setup_action"
        )

    async def request_config_update(self, delta: dict[str, Any]) -> None:
        """Persist a connection/config delta for this device (from a setup
        action handler). Connection fields land in the project's connections
        table, the rest in the device config; the live driver's ``self.config``
        is updated so the next connect() uses the new settings.

        Only callable from inside ``run_setup_action`` — the platform installs
        the backing context for the duration of the run.
        """
        if self._setup_context is None:
            raise RuntimeError(
                "request_config_update is only available during a setup action"
            )
        await self._setup_context.apply_config_update(delta)

    async def request_reconnect(self) -> None:
        """Reconnect the device using its current (possibly just-updated)
        config. Typically the final step of a setup action. Only callable from
        inside ``run_setup_action``.
        """
        if self._setup_context is None:
            raise RuntimeError(
                "request_reconnect is only available during a setup action"
            )
        await self._setup_context.reconnect()

    def _set_setup_context(self, context: Any) -> None:
        """Platform-only: install (or clear, with None) the setup context that
        backs request_config_update / request_reconnect for one setup run.
        """
        self._setup_context = context
