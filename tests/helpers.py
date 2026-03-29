"""
Test helper utilities for OpenAVC tests.

Provides event-driven assertion helpers to replace raw asyncio.sleep() calls
with condition-based waiting, improving test reliability on slow systems.
"""

import asyncio
from typing import Any, Callable


async def wait_for_state(
    state,
    key: str,
    expected: Any = None,
    timeout: float = 5.0,
    interval: float = 0.05,
) -> Any:
    """Wait until a state key has the expected value, or any value if expected is None.

    Returns the value when matched. Raises TimeoutError if not matched within timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        value = state.get(key)
        if expected is None and value is not None:
            return value
        if value == expected:
            return value
        await asyncio.sleep(interval)
    raise TimeoutError(f"State key '{key}' did not reach {expected!r} within {timeout}s (current: {state.get(key)!r})")


async def wait_for_condition(
    condition: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.05,
    message: str = "Condition not met",
) -> None:
    """Wait until a callable returns True. Raises TimeoutError otherwise."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"{message} within {timeout}s")
