"""Host network configuration backends.

Lets OpenAVC view and change the network configuration of the machine it
runs on — IP address, gateway, DNS, WiFi — so an appliance can be brought
onto a network from its own screen (the /setup page) or from the Programmer's
Network settings, with no SSH or desktop required.

Backends are capability-detected at runtime:

- A deployment-provided backend: an image can ship its own implementation
  and point ``network.backend_module`` in system.json at it (a module
  exposing ``create_backend() -> NetworkBackend | None``). Checked first so
  appliance images take precedence over the generic probes.
- ``NmcliBackend`` — any POSIX host with NetworkManager running (the Pi
  appliance image, most desktop Linux). Talks to ``nmcli``; on the Pi image a
  polkit rule authorizes the unprivileged service user for NetworkManager and
  hostnamed actions (``sudo`` is unusable under ``NoNewPrivileges``).

Where no backend is available (Windows, Docker, generic servers without
NetworkManager) ``get_backend()`` returns ``None``, the API answers 404, and
every UI surface hides itself.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import shutil
import subprocess
import sys

from server.utils.logger import get_logger

log = get_logger(__name__)

# How long to give `nmcli connection up` before treating the activation as
# failed and rolling back. NetworkManager's own default (90 s) is far past
# the point where a human concludes the box is bricked.
_ACTIVATION_WAIT_S = 20
_SCAN_TIMEOUT_S = 45
_CONNECT_TIMEOUT_S = 60


# --- Terse-output parsing (nmcli -t) ---


def split_terse_line(line: str) -> list[str]:
    """Split one ``nmcli -t`` line on unescaped colons.

    Terse mode escapes literal colons and backslashes in values (``\\:`` and
    ``\\\\``), e.g. MAC addresses in ``GENERAL.HWADDR``.
    """
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


_INDEXED_KEY = re.compile(r"^(?P<key>.+?)\[\d+\]$")


def parse_keyed_terse(output: str) -> dict[str, list[str]]:
    """Parse ``nmcli -t`` KEY:VALUE output into ``{key: [values...]}``.

    Multi-value fields arrive as indexed keys (``IP4.DNS[1]``, ``IP4.DNS[2]``)
    — the index is stripped and values collect in order. Values containing
    colons (MACs) are unescaped by :func:`split_terse_line`.
    """
    result: dict[str, list[str]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = split_terse_line(line)
        if len(parts) < 2:
            continue
        key, value = parts[0], ":".join(parts[1:])
        m = _INDEXED_KEY.match(key)
        if m:
            key = m.group("key")
        result.setdefault(key, []).append(value)
    return result


def _split_list_value(value: str) -> list[str]:
    """Split a connection-profile list value ("8.8.8.8,1.1.1.1" or with
    spaces) into clean entries."""
    return [v.strip() for v in value.split(",") if v.strip()]


# --- Validation (pure, shared by every backend) ---


_HOSTNAME_LABEL = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


def validate_hostname(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 253:
        raise ValueError("Hostname must be 1-253 characters.")
    for label in name.split("."):
        if not _HOSTNAME_LABEL.match(label):
            raise ValueError(
                "Hostname may contain letters, digits, and hyphens, and "
                "must not start or end with a hyphen."
            )
    return name


def validate_static_ipv4(
    address: str, gateway: str | None, dns: list[str] | None
) -> tuple[str, str | None, list[str], list[str]]:
    """Validate a static IPv4 request.

    Returns ``(address_cidr, gateway, dns_list, warnings)``. Raises
    ``ValueError`` for anything unusable; soft concerns (gateway outside the
    subnet) come back as warnings for the confirmation step instead.
    """
    address = (address or "").strip()
    if "/" not in address:
        raise ValueError(
            "Address must include a prefix length, e.g. 192.168.1.50/24."
        )
    try:
        iface = ipaddress.IPv4Interface(address)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        raise ValueError(f"'{address}' is not a valid IPv4 address/prefix.")
    if iface.ip.is_loopback or iface.ip.is_multicast:
        raise ValueError("Address must be a routable host address.")

    warnings: list[str] = []
    gw: str | None = None
    if gateway and gateway.strip():
        try:
            gw_addr = ipaddress.IPv4Address(gateway.strip())
        except (ipaddress.AddressValueError, ValueError):
            raise ValueError(f"'{gateway}' is not a valid gateway address.")
        gw = str(gw_addr)
        if gw_addr not in iface.network:
            warnings.append(
                f"Gateway {gw} is outside the {iface.network} subnet."
            )

    dns_list: list[str] = []
    for entry in dns or []:
        entry = entry.strip()
        if not entry:
            continue
        try:
            dns_list.append(str(ipaddress.IPv4Address(entry)))
        except (ipaddress.AddressValueError, ValueError):
            raise ValueError(f"'{entry}' is not a valid DNS server address.")

    return str(iface), gw, dns_list, warnings


# --- Backend interface ---


class NetworkBackend:
    """One implementation per OS family. All methods are async; backends run
    their OS tooling off-loop. Methods return plain dicts ready for the API.
    """

    name = "none"

    async def get_status(self) -> dict:
        raise NotImplementedError

    async def set_ipv4(
        self,
        connection: str,
        method: str,
        address: str | None = None,
        gateway: str | None = None,
        dns: list[str] | None = None,
    ) -> dict:
        raise NotImplementedError

    async def wifi_scan(self) -> list[dict]:
        raise NotImplementedError

    async def wifi_connect(self, ssid: str, psk: str | None = None) -> dict:
        raise NotImplementedError

    async def set_hostname(self, name: str) -> dict:
        raise NotImplementedError


class NmcliBackend(NetworkBackend):
    """NetworkManager via ``nmcli`` (Pi image, desktop Linux)."""

    name = "nmcli"

    async def _run(self, *args: str, timeout: float = 20) -> tuple[int, str, str]:
        """Run nmcli with the given args. Never a shell; args go straight to
        exec, so values from requests cannot be interpreted."""
        proc = await asyncio.create_subprocess_exec(
            "nmcli",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "", f"nmcli timed out after {timeout}s"
        return (
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    @staticmethod
    def _error_text(err: str, out: str) -> str:
        text = (err or out).strip()
        return text.splitlines()[0] if text else "nmcli failed"

    # --- status ---

    async def get_status(self) -> dict:
        rc, out, err = await self._run(
            "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"
        )
        if rc != 0:
            raise RuntimeError(self._error_text(err, out))

        interfaces = []
        has_wifi = False
        for line in out.splitlines():
            parts = split_terse_line(line)
            if len(parts) < 4:
                continue
            device, dev_type, state, connection = parts[0], parts[1], parts[2], parts[3]
            if dev_type not in ("ethernet", "wifi"):
                continue
            if dev_type == "wifi":
                has_wifi = True
            entry: dict = {
                "device": device,
                "type": dev_type,
                "state": state,
                "connection": connection or None,
                "mac": None,
                "ip4": {"addresses": [], "gateway": None, "dns": []},
                "config": None,
            }

            rc2, out2, _ = await self._run(
                "-t",
                "-f",
                "GENERAL.HWADDR,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS",
                "device",
                "show",
                device,
            )
            if rc2 == 0:
                fields = parse_keyed_terse(out2)
                mac = fields.get("GENERAL.HWADDR", [""])[0]
                entry["mac"] = mac or None
                entry["ip4"] = {
                    "addresses": fields.get("IP4.ADDRESS", []),
                    "gateway": (fields.get("IP4.GATEWAY", [""])[0] or None),
                    "dns": fields.get("IP4.DNS", []),
                }

            if connection:
                entry["config"] = await self._connection_config(connection)

            interfaces.append(entry)

        hostname = None
        rc3, out3, _ = await self._run("general", "hostname")
        if rc3 == 0:
            hostname = out3.strip() or None

        return {
            "backend": self.name,
            "hostname": hostname,
            "capabilities": {"ipv4": True, "wifi": has_wifi, "hostname": True},
            "interfaces": interfaces,
        }

    async def _connection_config(self, connection: str) -> dict | None:
        rc, out, _ = await self._run(
            "-t",
            "-f",
            "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns",
            "connection",
            "show",
            connection,
        )
        if rc != 0:
            return None
        fields = parse_keyed_terse(out)

        def first(key: str) -> str:
            return fields.get(key, [""])[0]

        return {
            "method": first("ipv4.method") or "auto",
            "addresses": _split_list_value(first("ipv4.addresses")),
            "gateway": first("ipv4.gateway") or None,
            "dns": _split_list_value(first("ipv4.dns")),
        }

    # --- ipv4 ---

    async def set_ipv4(
        self,
        connection: str,
        method: str,
        address: str | None = None,
        gateway: str | None = None,
        dns: list[str] | None = None,
    ) -> dict:
        snapshot = await self._connection_config(connection)
        if snapshot is None:
            return {"ok": False, "error": f"Connection '{connection}' not found."}

        if method == "manual":
            mod_args = [
                "connection", "modify", connection,
                "ipv4.method", "manual",
                "ipv4.addresses", address or "",
                "ipv4.gateway", gateway or "",
                "ipv4.dns", " ".join(dns or []),
            ]
        else:
            mod_args = [
                "connection", "modify", connection,
                "ipv4.method", "auto",
                "ipv4.addresses", "",
                "ipv4.gateway", "",
                "ipv4.dns", "",
            ]

        rc, out, err = await self._run(*mod_args)
        if rc != 0:
            return {"ok": False, "error": self._error_text(err, out)}

        rc, out, err = await self._activate(connection)
        if rc == 0:
            return {"ok": True, "rolled_back": False}

        # Activation failed — the address never came up. Put the previous
        # configuration back so the device stays reachable.
        failure = self._error_text(err, out)
        log.warning(
            f"Network change on '{connection}' failed to activate "
            f"({failure}); rolling back"
        )
        restore_args = [
            "connection", "modify", connection,
            "ipv4.method", snapshot["method"],
            "ipv4.addresses", ",".join(snapshot["addresses"]),
            "ipv4.gateway", snapshot["gateway"] or "",
            "ipv4.dns", " ".join(snapshot["dns"]),
        ]
        rc2, out2, err2 = await self._run(*restore_args)
        rc3 = 1
        if rc2 == 0:
            rc3, _, _ = await self._activate(connection)
        if rc2 != 0 or rc3 != 0:
            log.error(
                f"Rollback of '{connection}' also failed; device may be "
                "unreachable until the network is fixed locally"
            )
            return {
                "ok": False,
                "rolled_back": False,
                "error": f"{failure} (rollback also failed)",
            }
        return {"ok": False, "rolled_back": True, "error": failure}

    async def _activate(self, connection: str) -> tuple[int, str, str]:
        return await self._run(
            "--wait", str(_ACTIVATION_WAIT_S),
            "connection", "up", connection,
            timeout=_ACTIVATION_WAIT_S + 15,
        )

    # --- wifi ---

    async def wifi_scan(self) -> list[dict]:
        rc, out, err = await self._run(
            "-t",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            "--rescan",
            "yes",
            timeout=_SCAN_TIMEOUT_S,
        )
        if rc != 0:
            raise RuntimeError(self._error_text(err, out))

        by_ssid: dict[str, dict] = {}
        for line in out.splitlines():
            parts = split_terse_line(line)
            if len(parts) < 4:
                continue
            in_use, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
            if not ssid:  # hidden networks
                continue
            try:
                strength = int(signal)
            except ValueError:
                strength = 0
            secured = bool(security and security != "--")
            existing = by_ssid.get(ssid)
            if existing is None or strength > existing["signal"]:
                by_ssid[ssid] = {
                    "ssid": ssid,
                    "signal": strength,
                    "secured": secured,
                    "in_use": in_use.strip() == "*" or bool(existing and existing["in_use"]),
                }
            elif in_use.strip() == "*":
                existing["in_use"] = True
        return sorted(by_ssid.values(), key=lambda n: -n["signal"])

    async def wifi_connect(self, ssid: str, psk: str | None = None) -> dict:
        args = ["device", "wifi", "connect", ssid]
        if psk:
            args += ["password", psk]
        rc, out, err = await self._run(
            "--wait", str(_ACTIVATION_WAIT_S), *args, timeout=_CONNECT_TIMEOUT_S
        )
        if rc != 0:
            return {"ok": False, "error": self._error_text(err, out)}
        return {"ok": True}

    # --- hostname ---

    async def set_hostname(self, name: str) -> dict:
        rc, out, err = await self._run("general", "hostname", name)
        if rc != 0:
            return {"ok": False, "error": self._error_text(err, out)}
        return {"ok": True}


# --- Backend resolution ---


_backend_resolved = False
_backend: NetworkBackend | None = None


def _nmcli_running() -> bool:
    if sys.platform == "win32":
        return False
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return False
    try:
        proc = subprocess.run(
            [nmcli, "-g", "RUNNING", "general"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip().lower() == "running"


def _deployment_backend() -> NetworkBackend | None:
    """Load a deployment-provided backend, if one is configured.

    ``network.backend_module`` names an importable module exposing
    ``create_backend() -> NetworkBackend | None``. Only an administrator can
    set it (system.json / config PATCH are credentialed surfaces), and it
    runs with the server's own privileges — same trust level as a plugin.
    """
    from server.system_config import get_system_config

    module_name = str(
        get_system_config().get("network", "backend_module", "") or ""
    ).strip()
    if not module_name:
        return None
    try:
        import importlib

        module = importlib.import_module(module_name)
        backend = module.create_backend()
    except Exception:
        log.exception(
            f"network.backend_module '{module_name}' failed to load; "
            "falling back to built-in detection"
        )
        return None
    if backend is not None and not isinstance(backend, NetworkBackend):
        log.error(
            f"network.backend_module '{module_name}' returned "
            f"{type(backend).__name__}, not a NetworkBackend; ignoring"
        )
        return None
    return backend


def get_backend() -> NetworkBackend | None:
    """Resolve the host network backend, once per process.

    Blocking (probes the OS on first call) — call via ``asyncio.to_thread``
    from request handlers.
    """
    global _backend_resolved, _backend
    if _backend_resolved:
        return _backend
    backend: NetworkBackend | None = _deployment_backend()
    if backend is None and _nmcli_running():
        backend = NmcliBackend()
    _backend = backend
    _backend_resolved = True
    if backend:
        log.info(f"Host network configuration available (backend: {backend.name})")
    return _backend


def reset_backend_cache() -> None:
    """Test hook: force re-detection on the next get_backend() call."""
    global _backend_resolved, _backend
    _backend_resolved = False
    _backend = None
