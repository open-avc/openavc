"""
OpenAVC SSH Transport — interactive CLI session over the OS OpenSSH client.

Many managed devices (network switches, some DSPs, headends) expose their
control surface only as a text CLI reached over SSH. There is no MIT-compatible
Python SSH library — paramiko and ssh2-python are LGPL, asyncssh is EPL — so
this transport shells out to the operating system's OpenSSH client (``ssh``),
which is BSD-licensed. Running ``ssh`` as a separate process keeps OpenAVC's
MIT licence clean (no linking) and avoids any binary-wheel/ABI risk on ARM.
The client is not on every host by default (slim Linux images and containers
leave it out), so every install type ships or installs it: the table and the
per-install-type rules are ``openavc/system/os_programs.py``.

Design — deliberately mirrors :class:`openavc.transport.tcp.TCPTransport`:

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
  set ``SSH_ASKPASS_REQUIRE=force`` (OpenSSH 8.4+), plus ``DISPLAY``, which an
  older client checks before it will use the helper. This is pty-free. On
  Windows it is measured against password and keyboard-interactive servers
  with the built-in client of Windows 11 (OpenSSH_for_Windows 9.5) and with
  Microsoft's 8.1 build, the client Windows 10 ships.

Host keys use trust-on-first-use by default (``StrictHostKeyChecking=accept-new``)
against a managed ``known_hosts`` file, so a changed key (a possible MITM) is
refused while a brand-new device connects without a prompt.

Why a session failed comes from ssh's own log, which it writes to a file of
its own (``-E``) at ``LogLevel=INFO``, never from stderr. Some failures are
logged only at INFO: a refused port on Windows' built-in client, a key
exchange that finds no common algorithm and a session the device stopped
answering on every client. At INFO, ssh also prints the device's pre-login
banner, to stderr and outside the log. A banner is device text ("If you are
not authorized to access this system, disconnect now") that the classifier
would read as a rejected login, so stderr is drained and logged at debug
only. The one stderr line kept as a reason is ssh refusing an option it was
given (a device's SSH options, or an algorithm the client no longer has):
ssh writes that while it reads its arguments, before it opens the log and
before it dials, so no banner can be one. The log is read once ssh has
exited: Windows' client holds it open, unreadable, until then.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import sys
import tempfile

from openavc.core.connection_fault import (
    INVALID_CONFIG,
    ConnectionFault,
    ConnectionFaultError,
    typed_fault_from_exc,
)
from openavc.core.device_traffic import RX, TX, record_traffic
from openavc.transport.pacing import sleep_at_least
from openavc.transport.write_drain import drain_or_stalled
from openavc.transport.handler_runs import HandlerRuns
from openavc.utils.logger import get_logger
from openavc.utils.spawn import CREATE_NO_WINDOW

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


# Algorithms modern OpenSSH has dropped from its defaults but AV gear still
# ships. Appended with "+", which ADDS to the built-in lists rather than
# replacing them, so the strongest mutual option still wins and a current
# device negotiates exactly as it did before. The only sessions whose
# behaviour changes are the ones that could not connect at all.
#
# Measured, not guessed: an AC-MXNET-SW8P (2026-08-27) offers exactly one MAC,
# hmac-sha1, which OpenSSH removed from its defaults in 8.8. Against
# OpenSSH_for_Windows_9.5p2 the handshake dies with "no matching MAC found"
# while KEX (curve25519-sha256), host key (ecdsa-sha2-nistp256) and ciphers
# (aes128-ctr) all agree -- one absent MAC is the whole failure. The switch
# ships ssh-server enable, so its own documentation says SSH works.
#
# The trade-off is real and deliberate: OpenAVC will speak SHA-1 to a device
# that offers nothing better. For AV control gear that is the right call --
# the alternative is not a safer connection, it is no connection -- and a
# device that supports better is unaffected because "+" never downgrades it.
#
# Every keyword here has to be one the OLDEST client we run on knows: ssh
# refuses to start on an option it does not recognise ("Bad configuration
# option"), so one new name fails every connection, key sign-in included.
# The last entry is spelled PubkeyAcceptedKeyTypes for that reason. OpenSSH
# 8.5 renamed it PubkeyAcceptedAlgorithms and still accepts the old name, but
# a client before 8.5 knows only the old one: the client in Windows 10
# (8.1), Ubuntu 20.04 (8.2), Debian 11 (8.4).
LEGACY_SSH_ALGORITHMS: tuple[str, ...] = (
    "MACs=+hmac-sha1",
    "KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1",
    "HostkeyAlgorithms=+ssh-rsa",
    "PubkeyAcceptedKeyTypes=+ssh-rsa",
)

# ssh refusing an option on its command line, on stderr: "command-line line
# 0: Bad SSH2 cipher spec '+x'." (OpenSSH 10.2; the same build also writes
# "command-line: line 0: Bad configuration option: x").
_OPTION_REFUSED = re.compile(r"^command-line:? line \d+: ")

# The settings a device's own SSH options may carry: how the connection is
# negotiated, kept up and signed in to. Nothing that runs a program
# (ProxyCommand, LocalCommand, KnownHostsCommand), loads one
# (PKCS11Provider), forwards a port or an agent, or reads another config
# file. Running code on this computer needs the Programmer password even on
# an instance open to everyone (api/auth.require_claimed_auth), and editing
# a device does not, so this list is what keeps the field on the right side
# of that rule. An allowlist rather than a denylist, because OpenSSH keeps
# adding keywords (KnownHostsCommand arrived in 8.5) and a new one is safe
# here only once somebody has read what it does. ssh reads a keyword in any
# case, so this does too.
DEVICE_SSH_OPTIONS = frozenset(keyword.lower() for keyword in (
    # Algorithms: what old gear needs.
    "Ciphers", "MACs", "KexAlgorithms", "HostKeyAlgorithms",
    "PubkeyAcceptedAlgorithms", "PubkeyAcceptedKeyTypes",
    "CASignatureAlgorithms", "RequiredRSASize", "Compression", "RekeyLimit",
    # Connecting and staying connected.
    "ConnectTimeout", "ConnectionAttempts", "ServerAliveInterval",
    "ServerAliveCountMax", "TCPKeepAlive", "IPQoS", "AddressFamily",
    "BindAddress", "BindInterface", "ObscureKeystrokeTiming", "EscapeChar",
    # Signing in, and the device's host key.
    "PreferredAuthentications", "PasswordAuthentication",
    "KbdInteractiveAuthentication", "PubkeyAuthentication",
    "NumberOfPasswordPrompts", "IdentitiesOnly", "GSSAPIAuthentication",
    "CheckHostIP", "UpdateHostKeys", "HashKnownHosts",
))

_OPTION_KEYWORD = re.compile(r"\s*([A-Za-z][A-Za-z0-9]*)\s*(=|\s)")


def refused_ssh_option(option: str) -> str | None:
    """Why a device's SSH option is not passed to ssh, or None to pass it.

    The sentence is the device card's (an ``invalid_config`` fault)."""
    if any(ch in option for ch in "\r\n\0"):
        return "Each SSH option has to be on a line of its own."
    match = _OPTION_KEYWORD.match(option)
    if match is None:
        return (
            f"SSH options take one Key=Value per line, and '{option.strip()}' "
            f"is not one."
        )
    keyword = match.group(1)
    if keyword.lower() not in DEVICE_SSH_OPTIONS:
        return (
            f"SSH options can't include {keyword}. Use them for connection "
            f"settings such as Ciphers, MACs, KexAlgorithms or ConnectTimeout."
        )
    return None


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
        legacy_algorithms: bool = True,
        local_address: str | None = None,
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
        # The device id traffic is recorded under (core/device_traffic.py).
        self._traffic_name = name or ""
        self._ssh_binary = ssh_binary
        self._extra_ssh_options = list(extra_ssh_options or [])
        self._legacy_algorithms = bool(legacy_algorithms)
        # The local IP the connection leaves from (Reach devices through).
        self._local_address = local_address or None

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._connected = False
        self._askpass_path: str | None = None
        # ssh's own log (-E): its path, and its last 500 characters once read,
        # which is what last_error reports (auth, host key, refused port).
        self._log_path: str | None = None
        self._log_tail = ""
        self._log_read = False
        # ssh's refusal of an option it was given (stderr, _OPTION_REFUSED).
        self._option_refused = ""
        # A typed fault the classifier should prefer over matching the log —
        # set when the failure already knows its own code (a stalled write).
        # Cleared on every connect so a healed link can't report it.
        self._last_fault: ConnectionFault | None = None
        # The receive handler's runs: one message at a time, in arrival order,
        # each held until it ends (handler_runs).
        self._handler_runs = HandlerRuns(_log_task_exception)

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
        legacy_algorithms: bool = True,
        local_address: str | None = None,
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
            legacy_algorithms=legacy_algorithms, local_address=local_address,
        )
        await transport._spawn()
        return transport

    # --- argv / env construction (pure; unit-tested without spawning) ----

    def _resolve_binary(self) -> str:
        binary = self._ssh_binary or shutil.which("ssh")
        if not binary:
            raise ConnectionError(
                "OpenSSH client ('ssh') not found on PATH. Install the OpenSSH "
                "client to use the SSH transport."
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
        """Build the full ``ssh`` argument vector for this connection.

        The destination is checked first, because the two fields that go into
        it are the two a person fills in by hand, and OpenSSH's answer to
        either being blank points at the network rather than at the field. A
        blank username makes the destination a bare ``@host``, which ssh
        refuses by printing its **usage message** and exiting -- the transport
        then sees a process that died without connecting, and the device card
        says the device is not responding. A blank host is worse for being
        quieter: ``ssh admin@`` reports ``connect to host  port 22: Connection
        refused``, which is a network verdict about an address nobody entered.
        Both are stated as ``invalid_config`` so the card names the field to
        fill in instead.

        Whitespace counts as blank. It cannot be a real account or address, and
        the alternative is an authentication failure that reads like a wrong
        password. Neither value is otherwise altered: what the integrator typed
        is what gets sent.
        """
        if not (self.username or "").strip():
            raise ConnectionFaultError(
                "SSH needs a username. Set this device's Username to the "
                "account it logs in with (many devices ship with a documented "
                "default, often 'admin').",
                code=INVALID_CONFIG,
            )
        if not (self.host or "").strip():
            raise ConnectionFaultError(
                "SSH needs an address. Set this device's Host to the device's "
                "IP address or hostname.",
                code=INVALID_CONFIG,
            )
        for opt in self._extra_ssh_options:
            refused = refused_ssh_option(opt)
            if refused is not None:
                raise ConnectionFaultError(refused, code=INVALID_CONFIG)
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
            "-o", "LogLevel=INFO",
        ]
        if self._log_path:
            argv += ["-E", self._log_path]
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
        # Device-supplied options go FIRST on purpose: ssh takes the first
        # value it sees for a keyword, so anything set per device overrides the
        # compat list below rather than fighting it.
        for opt in self._extra_ssh_options:
            argv += ["-o", opt]
        # The adapter Reach devices through chose, after the device's own
        # options so a BindAddress set per device still wins.
        if self._local_address:
            argv += ["-o", f"BindAddress={self._local_address}"]
        if self._legacy_algorithms:
            for opt in LEGACY_SSH_ALGORITHMS:
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
        # Clean slate: a previous attempt's typed fault must not be read as
        # this one's cause.
        self._last_fault = None
        self._remove_log()
        self._log_tail = ""
        self._log_read = False
        self._option_refused = ""
        try:
            fd, self._log_path = tempfile.mkstemp(prefix="openavc-ssh-", suffix=".log")
            os.close(fd)
        except OSError as e:
            raise ConnectionError(
                f"Failed to launch ssh for {self.host}:{self.port}: {e}"
            ) from e
        try:
            argv = self.build_argv()
        except BaseException:
            self._remove_log()
            raise
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
            self._remove_log()
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
                # ssh writes why it failed just before it exits: let it exit,
                # so the drop is reported with its reason.
                await self._await_exit(2.0)
                await self._await_stderr(1.0)
                self._read_log()
                await self._handle_disconnect()

    async def _stderr_loop(self) -> None:
        """Drain ssh's stderr: the device's pre-login banner and ssh's own
        closing lines, logged at debug and never read as a failure's cause,
        except ssh refusing an option it was given, which is kept."""
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log.debug(f"[{self._name}] ssh (stderr): {text}")
                    if _OPTION_REFUSED.match(text) and not self._option_refused:
                        self._option_refused = text
        except (asyncio.CancelledError, ConnectionError, OSError):
            return

    async def _await_stderr(self, timeout: float) -> None:
        """Once ssh has exited, let the stderr drain reach the end, so an
        option ssh refused is in hand before anyone asks why it stopped."""
        task = self._stderr_task
        if task is not None and not task.done():
            await asyncio.wait((task,), timeout=timeout)

    async def _await_exit(self, timeout: float) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

    def _read_log(self) -> None:
        """Read ssh's log once ssh has exited. Until then it may still grow,
        and Windows' client holds it unreadable. A read that fails is tried
        again on the next ``last_error``."""
        if self._log_read or not self._log_path:
            return
        if self._proc is None or self._proc.returncode is None:
            return
        try:
            with open(self._log_path, "rb") as f:
                data = f.read()
        except OSError:
            return
        self._log_read = True
        lines = [
            line.rstrip()
            for line in data.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        for text in lines:
            log.debug(f"[{self._name}] ssh: {text}")
        self._log_tail = "\n".join(lines)[-500:]

    def _remove_log(self) -> None:
        if self._log_path:
            try:
                os.remove(self._log_path)
            except OSError:
                pass
            self._log_path = None

    def _deliver(self, data: bytes) -> None:
        record_traffic(self._traffic_name, RX, data, channel="ssh")
        try:
            # A coroutine runs once the one before it has ended (handler_runs).
            self._handler_runs.run(self._on_data(data))
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
                await drain_or_stalled(self._proc.stdin, self._name)
                record_traffic(self._traffic_name, TX, data, channel="ssh")
                if self._inter_command_delay > 0:
                    await sleep_at_least(self._inter_command_delay)
            except (ConnectionError, OSError, BrokenPipeError) as e:
                self._last_fault = typed_fault_from_exc(
                    e, host=self.host, port=self.port
                )
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
            await self._await_stderr(1.0)
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
            # Keep why ssh failed, if it already exited on its own, before
            # the log goes; never what it says about being stopped here.
            self._read_log()
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
        self._remove_log()
        log.info(f"[{self._name}] SSH disconnected")

    @property
    def connected(self) -> bool:
        if not self._connected or self._proc is None:
            return False
        return self._proc.returncode is None

    @property
    def last_error(self) -> str:
        """The end of ssh's own log (auth, host key, refused port), trimmed,
        after any option ssh refused. Empty while ssh is still running."""
        self._read_log()
        return "\n".join(p for p in (self._option_refused, self._log_tail.strip()) if p)

    @property
    def last_fault(self) -> ConnectionFault | None:
        """A typed fault for this failure, when the transport knew its own
        cause. Preferred over string-matching ``last_error``."""
        return self._last_fault

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

    On Windows the helper is a cmd script, and cmd must print the password
    without ever parsing it. ``echo %VAR%`` substitutes before parsing, so
    ``& | < >`` in a password run as command syntax (``Pass&word`` sends
    ``Pass`` and then runs ``word``), ``^`` is dropped, and ``on``, ``off``,
    ``/?`` or a blank password are read as echo's own arguments (a blank one
    sends the text ``ECHO is off.``). Delayed expansion (``!VAR!``) substitutes
    after parsing, and ``echo(`` prints whatever follows it, nothing included.
    """
    if sys.platform == "win32":
        fd, path = tempfile.mkstemp(prefix="openavc-askpass-", suffix=".cmd")
        with os.fdopen(fd, "w", newline="") as f:
            f.write(
                "@echo off\r\n"
                "setlocal EnableDelayedExpansion\r\n"
                "echo(!OPENAVC_SSH_PASSWORD!\r\n"
            )
        return path
    fd, path = tempfile.mkstemp(prefix="openavc-askpass-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write('#!/bin/sh\nprintf \'%s\\n\' "$OPENAVC_SSH_PASSWORD"\n')
    os.chmod(path, stat.S_IRWXU)  # 0700
    return path
