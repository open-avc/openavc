"""
Plugin registration tracking and automatic cleanup.

Every PluginAPI call that creates a registration (subscription, state key,
background task) is recorded here. On plugin stop or uninstall, all
registrations are cleaned up automatically — plugin authors never manage this.
"""

import asyncio

from server.utils.logger import get_logger

log = get_logger(__name__)


class PluginRegistry:
    """Tracks all registrations made by a single plugin instance."""

    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        self.state_subscriptions: list[str] = []
        self.event_subscriptions: list[str] = []
        self.state_keys_set: set[str] = set()
        self.managed_tasks: list[asyncio.Task] = []
        self.periodic_task_ids: list[str] = []

    def track_state_subscription(self, sub_id: str) -> None:
        self.state_subscriptions.append(sub_id)

    def track_event_subscription(self, handler_id: str) -> None:
        self.event_subscriptions.append(handler_id)

    def track_state_key(self, key: str) -> None:
        self.state_keys_set.add(key)

    def track_task(self, task: asyncio.Task) -> None:
        self.managed_tasks.append(task)

    def untrack_task(self, task: asyncio.Task) -> None:
        try:
            self.managed_tasks.remove(task)
        except ValueError:
            pass

    def track_periodic_task(self, task_id: str) -> None:
        self.periodic_task_ids.append(task_id)

    async def cleanup(self, state_store, event_bus) -> None:
        """Remove all tracked registrations. Called on plugin stop/uninstall."""
        log.debug(f"Cleaning up plugin '{self.plugin_id}': "
                  f"{len(self.state_subscriptions)} state subs, "
                  f"{len(self.event_subscriptions)} event subs, "
                  f"{len(self.state_keys_set)} state keys, "
                  f"{len(self.managed_tasks)} tasks")

        for sub_id in self.state_subscriptions:
            try:
                state_store.unsubscribe(sub_id)
            except (KeyError, ValueError):
                log.debug(f"Failed to unsubscribe state sub {sub_id}")
        self.state_subscriptions.clear()

        for handler_id in self.event_subscriptions:
            try:
                event_bus.off(handler_id)
            except (KeyError, ValueError):
                log.debug(f"Failed to remove event handler {handler_id}")
        self.event_subscriptions.clear()

        for key in list(self.state_keys_set):
            try:
                state_store.delete(key)
            except (KeyError, ValueError):
                log.debug(f"Failed to clear state key {key}")
        self.state_keys_set.clear()

        for task in self.managed_tasks:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
        self.managed_tasks.clear()
        self.periodic_task_ids.clear()

        log.info(f"Plugin '{self.plugin_id}' cleanup complete")
