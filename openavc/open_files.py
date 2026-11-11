"""How many files and connections the server may hold open at once.

Every connected network device holds one socket for as long as it is connected,
beside the panels' connections, log files and plugin sockets. A service manager
starts the server with a small soft limit on open files (1024 under systemd, 256
under launchd), and a large project reaches it long before it runs out of
processor or memory: new connections then fail with "too many open files".

The soft limit is the process's own to raise, up to the hard limit the service
manager set (524288 under systemd, unlimited under launchd), so the server raises
it once at startup. That covers every POSIX install type alike (a systemd unit,
the macOS LaunchDaemon, Docker, a hand-run server, appliance hardware) without
editing each one's service definition. A hard limit set lower on purpose (a
container's ``--ulimit nofile``) is respected. Windows has no such per-process
limit on sockets, so there is nothing to do there.

A higher limit lets descriptor numbers pass 1023, which the ``select()`` system
call cannot watch; nothing in the server may wait on sockets with it
(``selectors`` and the event loop have no such ceiling).
"""

from __future__ import annotations

# Comfortably above the largest project measured (one server held ~300 device
# connections at 18% of one Pi 4 core), and far below any hard limit a service
# manager sets by default.
WANTED = 65536

# The fallback when a platform refuses WANTED: macOS caps a process at OPEN_MAX
# in some configurations.
_FALLBACK = 10240


def raise_open_file_limit() -> tuple[int, int] | None:
    """Raise this process's soft open-files limit toward ``WANTED``.

    Never above the hard limit and never lower than it already is. Returns
    ``(before, after)``, equal when nothing changed, or ``None`` where the
    platform has no such limit (Windows).
    """
    try:
        import resource
    except ImportError:
        return None
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == resource.RLIM_INFINITY:
        return soft, soft
    for wanted in (WANTED, _FALLBACK):
        target = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
        if soft >= target:
            return soft, soft
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except (ValueError, OSError):
            continue
        return soft, target
    return soft, soft
