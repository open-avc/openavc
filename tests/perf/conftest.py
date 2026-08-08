"""Perf-test conftest.

OpenAVC's ``openavc.utils.logger`` pins the root logger to DEBUG on first
``get_logger`` call. Production deployments treat this as "verbose mode
when a developer is debugging," but inside the perf suite it injects a
~10-40x slowdown on hot paths (every ``set`` / ``set_batch`` call emits
a formatted DEBUG record per change, through 3 attached handlers).

To measure the StateStore primitive's actual throughput we silence
chatty per-record logging for the duration of this suite. The same hot
paths still gate on ``log.isEnabledFor(DEBUG)`` in production, so this
mirrors real customer behavior (DEBUG off by default).
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _silence_logging_for_perf():
    """Raise log level to WARNING for the perf test (auto-applied)."""
    state_logger = logging.getLogger("openavc.core.state_store")
    root_logger = logging.getLogger()
    prior_state = state_logger.level
    prior_root = root_logger.level
    state_logger.setLevel(logging.WARNING)
    root_logger.setLevel(logging.WARNING)
    yield
    state_logger.setLevel(prior_state)
    root_logger.setLevel(prior_root)


def pytest_configure(config):
    """Register the ``perf`` mark so it doesn't warn."""
    config.addinivalue_line(
        "markers",
        "perf: state store / engine performance regression tests",
    )
