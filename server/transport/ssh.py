"""
OpenAVC SSH Transport — interactive CLI session over the OS OpenSSH client.

Many managed devices (network switches, some DSPs, headends) expose their
control surface only as a text CLI reached over SSH. There is no MIT-compatible
Python SSH library — paramiko and ssh2-python are LGPL, asyncssh is EPL — so
this transport shells out to the operating system's OpenSSH client (``ssh``),
which is BSD-licensed and ships by default on Windows 10+, every Linux, and
macOS. Running ``ssh`` as a separate process keeps OpenAVC's MIT licence clean
(no linking) and avoids any binary-wheel/ABI risk on ARM.

Design — deliberately mirrors :class:`server.transport.tcp.TCPTransport`:

* It is a *raw byte pipe*. Bytes from the remote shell are delivered to
  ``on_data`` as they arrive; ``send(bytes)`` writes to the shell's stdin.
  There is no framing here — a CLI driver accumulates the stream and frames on
  its device prompt (exactly as the Chazy/Darwin telnet drivers already do over
  TCPTransport). The same driver code therefore works unchanged whether the
  bytes flow over SSH (production) or raw TCP (a CLI simulator / telnet).
* ``-tt`` forces remote pseudo-terminal allocation so the device presents its
  interactive CLI (and echoes commands, which the driver's echo-strip handles).

Authentication:

* **Public key (recommended, default):** ``ssh -i <key> -o BatchMode=yes``.
  Fully non-interactive and identical on every OS. The integrator installs the
  OpenAVC public key on the device once.
* **Password:** OpenSSH never reads a password from stdin — it uses an askpass
  helper. We point ``SSH_ASKPASS`` at a tiny generated helper that echoes the
  password (sourced from the subprocess environment, never written to disk) and
  set ``SSH_ASKPASS_REQUIRE=force`` (OpenSSH 8.4+). This is pty-free and works
  on POSIX. On Windows, key auth is recommended; password auth is best-effort
  (older Windows OpenSSH builds ignore SSH_ASKPASS).

Host keys use trust-on-first-use by default (``StrictHostKeyChecking=accept-new``)
against a managed ``known_hosts`` file, so a changed key (a possible MITM) is
refused while a brand-new device connects without a prompt.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
import tempfile

from server.utils.logger import get_logger
from server.utils.spawn import CREATE_NO_WINDOW

from .types import Callback

log = get_logger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback for fire-and-forget on_data tasks: surface failures that
    would otherwise vanish as a 'Task exception was never retrieved' GC warning."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in SSH on_data task: %s", exc, exc_info=exc)


def default_known_hosts_path() -> str:
    """Per-user managed known_hosts file (kept out of the user's ~/.ssh)."""
    base = os.path.join(os.path.expanduser("~"), ".openavc", "ssh")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "known_hosts")


class SSHTransport:
    """Async byte-pipe transport over the OS OpenSSH client (``ssh``)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        on_data: Callback[[bytes], None],
        on_disconnect: Callback[[], None],
        *,
        auth_method: str = "key",
        password: str | None = None,
        key_path: str | None = None,
        known_hosts_path: str | None = None,
        host_key_policy: str = "accept-new",
        connect_timeout: float = 15.0,
        inter_command_delay: float = 0.0,
        name: str | None = None,
        ssh_binary: str | None = None,
        extra_ssh_options: list[str] | None = None,
    ):
        self.host = host
        self.port = int(port)
        self.username = username
        self._on_data = on_data
        self._on_disconnect = on_disconnect
        self._auth_method = (auth_method or "key").lower()
        self._password = password
        self._key_path = key_path
        self._known_hosts_path = known_hosts_path or default_known_hosts_path()
        self._host_key_policy = host_key_policy
        self._connect_timeout = connect_timeout
        self._inter_command_delay = inter_command_delay
        self._name = name or f"{username}@{host}:{port}"
        self._ssh_binary = ssh_binary
        self._extra_ssh_options = list(extra_ssh_options or [])

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._connected = False
        self._askpass_path: str | None = None
        # Last few stderr lines from ssh, for surfacing auth/host-key failures
        # to the driver when the session dies during the prompt read.
        self._stderr_tail = ""
        # Strong refs for async on_data callback tasks (asyncio only weakly
        # references tasks) — same supervision pattern as the other transports.
        self._bg_tasks: set[asyncio.Task] = set()

    # --- Factory ---------------------------------------------------------

    @classmethod
    async def create(
        cls,
        host: str,
        port: int,
        username: str,
        on_data: Callback[[bytes], None],
        on_disconnect: Callback[[], None],
        *,
        auth_method: str = "key",
        password: str | None = None,
        key_path: str | None = None,
        known_hosts_path: str | None = None,
        host_key_policy: str = "accept-new",
        connect_timeout: float = 15.0,
        inter_command_delay: float = 0.0,
        name: str | None = None,
        ssh_binary: str | None = None,
        extra_ssh_options: list[str] | None = None,
    ) -> "SSHTransport":
        """Spawn the ``ssh`` client and return a started transport.

        Does NOT wait for a device prompt — a CLI driver reads the login banner
        and detects readiness itself. Raises ConnectionError if the ``ssh``
        binary is missing or the process can't be launched.
        """
        transport = cls(
            host, port, username, on_data, on_disconnect,
            auth_method=auth_method, password=password, key_path=key_path,
            known_hosts_path=known_hosts_path, host_key_policy=host_key_policy,
            connect_timeout=connect_timeout,
            inter_command_delay=inter_command_delay, name=name,
            ssh_binary=ssh_binary, extra_ssh_options=extra_ssh_options,
        )
        await transport._spawn()
        return transport

    # --- argv / env construction (pure; unit-tested without spawning) ----

    def _resolve_binary(self) -> str:
        binary = self._ssh_binary or shutil.which("ssh")
        if not binary:
            raise ConnectionError(
                "OpenSSH client ('ssh') not found on PATH. Install OpenSSH "
                "(bundled with Windows 10+/Linux/macOS) to use the SSH transport."
            )
        return binary

    def _known_hosts_option(self) -> tuple[str, str]:
        """Return (StrictHostKeyChecking value, UserKnownHostsFile path)."""
        policy = (self._host_key_policy or "accept-new").lower()
        if policy in ("off", "no", "none"):
            return "no", os.devnull
        if policy in ("strict", "yes"):
            return "yes", self._known_hosts_path
        return "accept-new", self._known_hosts_path

    def build_argv(self) -> list[str]:
        """Build the full ``ssh`` argument vector for this connection."""
        binary = self._resolve_binary()
        strict, known_hosts = self._known_hosts_option()
        argv = [
            binary,
            "-tt",  # force remote PTY so the device presents its interactive CLI
            "-p", str(self.port),
            "-o", f"ConnectTimeout={int(self._connect_timeout)}",
            "-o", f"StrictHostKeyChecking={strict}",
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-o", "LogLevel=ERROR",
        ]
        if self._auth_method == "password":
            argv += [
                "-o", "PubkeyAuthentication=no",
                "-o", "PreferredAuthentications=password,keyboard-interactive",
                "-o", "NumberOfPasswordPrompts=1",
                "-o", "BatchMode=no",
            ]
        else:  # key auth (default)
            argv += [
                "-o", "PasswordAuthentication=no",
                "-o", "PreferredAuthentications=publickey",
                "-o", "BatchMode=yes",
            ]
            if self._key_path:
                argv += ["-i", self._key_path, "-o", "IdentitiesOnly=yes"]
        for opt in self._extra_ssh_options:
            argv += ["-o", opt]
        argv.append(f"{self.username}@{self.host}")
        return argv

    def build_env(self) -> dict[str, str]:
        """Environment for the ``ssh`` subprocess (askpass wiring for password)."""
        env = dict(os.environ)
        if self._auth_method == "password":
            self._askpass_path = _write_askpass_helper()
            env["SSH_ASKPASS"] = self._askpass_path
            env["SSH_ASKPASS_REQUIRE"] = "force"  # OpenSSH 8.4+: use askpass w/o tty
            env.setdefault("DISPLAY", "localhost:0")  # older ssh gates askpass on DISPLAY
            env["OPENAVC_SSH_PASSWORD"] = self._password or ""
        return env

    # --- lifecycle -------------------------------------------------------

    async def _spawn(self) -> None:
        argv = self.build_argv()
        env = self.build_env()
        # ssh creates known_hosts itself but not its parent directory.
        _, known_hosts = self._known_hosts_option()
        if known_hosts != os.devnull:
            os.makedirs(os.path.dirname(known_hosts), exist_ok=True)
        log.info(f"[{self._name}] SSH connecting ({self._auth_method} auth)")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, ValueError) as e:
            self._cleanup_askpass()
            raise ConnectionError(
                f"Failed to launch ssh for {self.host}:{self.port}: {e}"
            ) from e
        self._connected = True
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def _reader_loop(self) -> None:
        """Read the shell's stdout and deliver every chunk to ``on_data``."""
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while self._connected:
                data = await self._proc.stdout.read(4096)
                if not data:
                    break  # remote closed the channel / process exiting
                self._deliver(data)
        except asyncio.CancelledError:
            return
        except (ConnectionError, OSError) as e:
            log.debug(f"[{self._name}] SSH reader error: {e}")
        finally:
            if self._connected:
                await self._handle_disconnect()

    async def _stderr_loop(self) -> None:
        """Collect ssh's stderr so auth/host-key failures can be surfaced."""
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._stderr_tail = (self._stderr_tail + "\n" + text)[-500:]
                    log.debug(f"[{self._name}] ssh: {text}")
        except (asyncio.CancelledError, ConnectionError, OSError):
            return

    def _deliver(self, data: bytes) -> None:
        try:
            result = self._on_data(data)
            if asyncio.iscoroutine(result):
                # Hold a strong ref (GC-safety) and log any failure that an
                # async handler would otherwise swallow.
                task = asyncio.create_task(result)
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
                task.add_done_callback(_log_task_exception)
        except Exception:
            log.exception(
                f"[{self._name}] Error in SSH on_data callback — continuing"
            )

    async def send(self, data: bytes) -> None:
        """Write bytes to the remote shell's stdin."""
        async with self._send_lock:
            if not self._connected or self._proc is None or self._proc.stdin is None:
                raise ConnectionError("Not connected")
            try:
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
                if self._inter_command_delay > 0:
                    await asyncio.sleep(self._inter_command_delay)
            except (ConnectionError, OSError, BrokenPipeError) as e:
                log.error(f"[{self._name}] SSH send error: {e}")
                await self._handle_disconnect()
                raise

    async def verify(self, timeout: float = 3.0) -> bool:
        """Best-effort liveness check used by the generic connect() path.

        A bad password / refused connection makes ``ssh`` exit almost
        immediately, so a short settle that finds the process still running is a
        reasonable "authenticated" signal. CLI drivers that override connect()
        confirm readiness by reading the device prompt instead and don't rely
        on this.
        """
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=timeout)  # type: ignore[union-attr]
            return False  # process exited within the window -> failed
        except asyncio.TimeoutError:
            return self.connected  # still running -> good
        except (AttributeError, ProcessLookupError):
            return False

    async def close(self) -> None:
        """Terminate the ssh process and clean up."""
        self._connected = False
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._proc is not None:
            try:
                if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                    self._proc.stdin.close()
            except (OSError, RuntimeError):
                pass
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
            self._proc = None
        self._cleanup_askpass()
        log.info(f"[{self._name}] SSH disconnected")

    @property
    def connected(self) -> bool:
        if not self._connected or self._proc is None:
            return False
        return self._proc.returncode is None

    @property
    def last_error(self) -> str:
        """Recent ssh stderr (auth/host-key diagnostics), trimmed."""
        return self._stderr_tail.strip()

    def _cleanup_askpass(self) -> None:
        if self._askpass_path:
            try:
                os.remove(self._askpass_path)
            except OSError:
                pass
            self._askpass_path = None

    async def _handle_disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        err = self.last_error
        log.warning(
            f"[{self._name}] SSH connection lost"
            + (f": {err.splitlines()[-1]}" if err else "")
        )
        try:
            self._on_disconnect()
        except Exception:
            log.exception(f"[{self._name}] Error in SSH on_disconnect callback")


def _write_askpass_helper() -> str:
    """Write a tiny askpass helper that echoes ``$OPENAVC_SSH_PASSWORD``.

    The password is only ever read from the (subprocess) environment, never
    written to the file, so it does not land on disk. The helper is created
    0700 and removed on transport close.
    """
    if sys.platform == "win32":
        fd, path = tempfile.mkstemp(prefix="openavc-askpass-", suffix=".cmd")
        with os.fdopen(fd, "w", newline="\r\n") as f:
            f.write("@echo off\r\necho %OPENAVC_SSH_PASSWORD%\r\n")
        return path
    fd, path = tempfile.mkstemp(prefix="openavc-askpass-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write('#!/bin/sh\nprintf \'%s\\n\' "$OPENAVC_SSH_PASSWORD"\n')
    os.chmod(path, stat.S_IRWXU)  # 0700
    return path
