"""The network check: everything one device says to a network looking for it.

A device audit's first job. It points the discovery scanners' own functions at
one address and keeps everything they parse, then runs the real matcher on the
evidence those functions build, so its verdict is the one a Discovery scan
would reach. Nothing here re-parses a protocol; where a building block threw
data away, the block gained a capture option (``SSDPScanner(capture=True)``,
``SNMPScanner(capture=True)``, ``observe_*`` probes, ``scan_host_port_states``,
``read_greeting``, ``http_get``).

What it sends is what a Discovery scan sends, plus a ``GET /`` and a TLS
handshake on web ports only, plus connect-and-listen reads that send no bytes:

1. **The address.** Ping; reverse DNS; NetBIOS (every name, the workgroup, the
   MAC). Ping failing concludes nothing on its own: the port scan is the TCP
   check that follows, and a refused port proves the host is there.
2. **The MAC address**, from this computer's ARP table after first contact.
   A device on another network segment cannot be read that way, and that is
   recorded as a limit, not a missing value. NetBIOS and SNMP carry MACs too.
3. **Ports.** A TCP connect scan over the discovery scan's full list (or 1 to
   1024 as well, when asked), paced, refused and filtered kept apart.
4. **What each open port says**, connect and listen, nothing sent.
5. **Web pages and certificates** on web ports: status, headers, title; the
   certificate on TLS ports.
6. **Announcements.** mDNS, SSDP and AMX DDP listeners start with the session
   and run until it ends, so a beacon sent once a minute is heard. mDNS asks
   for every catalog type, runs the DNS-SD enumeration, then asks for every
   type the device lists.
7. **SNMP** v2c: ``public``, then the communities the person gave.
8. **Driver probes**: every catalog ``tcp_probe`` whose port is open, every
   ``udp_probe`` sent unicast, and the installed Python companions, every
   exchange kept, matched or not.

Then the verdict: the evidence a scan would have built, ``TierMatcher.match``
on it, and ``explain_matches`` for every driver each signal points at.

What the check could not see is written down as it goes (``limits``): a
listener that heard nobody at all, a MAC behind a router, the UDP ports no
probe covers. A report that says "no mDNS" has to say whether anybody's mDNS
could have been heard.

Order matters for a fragile device. The activities that open TCP connections
run one after another, never two to the same port at once (a device that
takes one control session would refuse the second and read as dead); UDP
work and the listening wait run beside them.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from openavc.discovery import icmp
from openavc.discovery.amx_ddp_scanner import AMXDDPScanner
from openavc.discovery.certificates import discovery_tls_context, read_peer_certificate
from openavc.discovery.companion import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    ProbeContext,
    run_companion,
)
from openavc.discovery.engine import (
    DiscoveryEngine,
    _resolve_hostnames,
    derived_evidence,
    hostname_evidence,
    mac_info_and_evidence,
)
from openavc.discovery.explain import (
    DeviceObservations,
    evaluate_driver_signals,
    explain_matches,
)
from openavc.discovery.http_fetch import HttpExchange, http_get
from openavc.discovery.mdns_scanner import BASELINE_SERVICE_TYPES, MDNSScanner
from openavc.discovery.network_scanner import (
    get_local_subnets,
    get_network_adapters,
    harvest_arp_table,
    netbios_query,
)
from openavc.discovery.port_scanner import (
    PORT_FILTERED,
    PORT_OPEN,
    PORT_REFUSED,
    PortGreeting,
    read_greeting,
    scan_host_port_states,
)
from openavc.discovery.probe_runner import (
    ProbeObservation,
    RateLimiter,
    observe_tcp_active_probe,
    observe_udp_probe,
)
from openavc.discovery.result import (
    DeviceState,
    DiscoveredDevice,
    Evidence,
    device_info_from_evidence,
    merge_device_info,
)
from openavc.discovery.snmp_scanner import SNMPScanner
from openavc.discovery.ssdp_scanner import SSDPScanner

if TYPE_CHECKING:
    from openavc.audit.session import AuditSession

log = logging.getLogger("audit.footprint")

# How long the listeners get before announcements are read: one AMX DDP
# beacon period (they come every 30 to 60 seconds), counted from when the
# listeners started, which is when the session did.
LISTEN_SECONDS = 60.0
# Connect-and-listen on each open port.
GREETING_SECONDS = 3.0
# Spacing between connection starts in the port scan. Embedded stacks have
# been seen to drop an existing control connection under a fast scan.
PORT_PACING_MS = 50.0
# How long a port gets to answer. Windows retries a connect after the reset
# a closed port sends and reports the refusal about two seconds later, so a
# shorter wait (a scan's one second) reads every closed port as filtered.
# The connects overlap, so this costs about two seconds in all.
PORT_TIMEOUT = 3.0
# Open connections at once while reading greetings and web pages (to
# different ports; never two to one port).
TCP_CONCURRENCY = 3
# Bounded SNMP walk for the extended check.
SNMP_WALK_LIMIT = 5000
# Bytes of a web page kept.
WEB_BODY_BYTES = 16384
# How long the listeners run at most. The session ends them first.
_LISTENER_CEILING_SECONDS = 24 * 3600.0

# Ports that serve a web page by convention, and the scheme each speaks.
WEB_PORTS: dict[int, str] = {
    80: "http", 443: "https", 8080: "http", 8443: "https", 8000: "http", 8888: "http",
}
# mDNS types that advertise a web server.
_WEB_MDNS_TYPES = {"_http._tcp.local": "http", "_https._tcp.local": "https"}

# The activities, in the order the wizard lists them.
ACTIVITIES: tuple[str, ...] = (
    "address", "ports", "greetings", "web", "announcements", "snmp", "probes",
)

PENDING = "pending"
RUNNING = "running"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"


def bytes_view(data: bytes) -> dict[str, str]:
    """Raw bytes as both hex and text, the way the report shows them."""
    return {"hex": data.hex(), "text": data.decode("latin-1")}


@dataclass
class Limit:
    """Something the check could not see, and why (the P4 list)."""

    id: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "text": self.text}


@dataclass
class Activity:
    """One line of the wizard's progress list."""

    key: str
    status: str = PENDING
    message: str = ""
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class Footprint:
    """Everything the network check observed about one device, raw."""

    address: str
    ip: str
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    extended: bool = False
    local_ip: str = ""
    interface: str = ""
    source_ip: str = ""
    same_subnet: bool | None = None
    local_subnets: list[str] = field(default_factory=list)
    ping: dict[str, Any] = field(default_factory=dict)
    reverse_dns: str | None = None
    netbios: dict[str, Any] | None = None
    mac: str | None = None
    mac_source: str = ""
    port_list: list[int] = field(default_factory=list)
    port_states: dict[int, str] = field(default_factory=dict)
    greetings: dict[int, PortGreeting] = field(default_factory=dict)
    web: dict[int, HttpExchange] = field(default_factory=dict)
    certificates: dict[int, dict[str, Any]] = field(default_factory=dict)
    certificate_errors: dict[int, str] = field(default_factory=dict)
    mdns: dict[str, Any] | None = None
    ssdp: dict[str, Any] | None = None
    amx_ddp: dict[str, Any] | None = None
    snmp: dict[str, Any] = field(default_factory=dict)
    probes: list[ProbeObservation] = field(default_factory=list)
    companion_evidence: list[dict[str, Any]] = field(default_factory=list)
    listeners: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    device: DiscoveredDevice | None = None
    verdict: dict[str, Any] = field(default_factory=dict)
    limits: list[Limit] = field(default_factory=list)

    def add_limit(self, limit_id: str, text: str) -> None:
        if not any(existing.id == limit_id for existing in self.limits):
            self.limits.append(Limit(limit_id, text))

    def open_ports(self) -> list[int]:
        return sorted(p for p, state in self.port_states.items() if state == PORT_OPEN)

    def observations(self) -> DeviceObservations:
        """What ``discovery.explain`` judges a driver's signals against."""
        return DeviceObservations(
            evidence=list(self.evidence),
            probes=list(self.probes),
            port_states=dict(self.port_states),
        )

    def answered(self) -> bool:
        """Did anything at this address answer anything?"""
        return bool(
            self.ping.get("result") == icmp.RESULT_ALIVE
            or any(state in (PORT_OPEN, PORT_REFUSED) for state in self.port_states.values())
            or self.mdns or self.ssdp or self.amx_ddp or self.netbios
            or self.snmp.get("answered")
            or any(p.reply for p in self.probes)
        )

    def to_dict(self) -> dict[str, Any]:
        states = self.port_states
        return {
            "address": self.address,
            "ip": self.ip,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "extended": self.extended,
            "network": {
                "local_ip": self.local_ip,
                "interface": self.interface,
                "source_ip_pinned": self.source_ip,
                "same_subnet": self.same_subnet,
                "local_subnets": list(self.local_subnets),
            },
            "ping": dict(self.ping),
            "names": {"reverse_dns": self.reverse_dns, "netbios": self.netbios},
            "mac": {"address": self.mac, "source": self.mac_source},
            "ports": {
                "checked": len(self.port_list),
                "range": "extended" if self.extended else "standard",
                "open": [p for p, s in sorted(states.items()) if s == PORT_OPEN],
                "refused": [p for p, s in sorted(states.items()) if s == PORT_REFUSED],
                "filtered": [p for p, s in sorted(states.items()) if s == PORT_FILTERED],
                "other": {
                    str(p): s for p, s in sorted(states.items())
                    if s not in (PORT_OPEN, PORT_REFUSED, PORT_FILTERED)
                },
            },
            "greetings": {str(p): g.to_dict() for p, g in sorted(self.greetings.items())},
            "web": {str(p): _web_dict(x) for p, x in sorted(self.web.items())},
            "certificates": {str(p): c for p, c in sorted(self.certificates.items())},
            "certificate_errors": {str(p): e for p, e in sorted(self.certificate_errors.items())},
            "mdns": self.mdns,
            "ssdp": self.ssdp,
            "amx_ddp": self.amx_ddp,
            "snmp": self.snmp,
            "probes": [p.to_dict() for p in self.probes],
            "companions": list(self.companion_evidence),
            "listeners": self.listeners,
            "evidence": [ev.to_dict() for ev in self.evidence],
            "device": self.device.to_dict() if self.device else None,
            "verdict": self.verdict,
            "limits": [limit.to_dict() for limit in self.limits],
        }


def _web_dict(exchange: HttpExchange) -> dict[str, Any]:
    return {
        "url": exchange.url,
        "status_line": exchange.status_line,
        "status": exchange.status,
        "headers": [[k, v] for k, v in exchange.headers],
        "title": exchange.title(),
        "server": exchange.header("server"),
        "www_authenticate": exchange.header("www-authenticate"),
        "location": exchange.header("location"),
        "body": bytes_view(exchange.body),
        "truncated": exchange.truncated,
        "error": exchange.error,
        "certificate": exchange.certificate,
    }


def local_route_ip(ip: str) -> str:
    """The local address this computer would send to ``ip`` from.

    A UDP socket picks its source address from the routing table on connect
    without sending anything.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((ip, 9))
            return sock.getsockname()[0]
    except OSError:
        return ""


async def resolve_address(address: str) -> str:
    """The IPv4 address ``address`` names, or "" when it names none."""
    address = address.strip()
    try:
        return str(ipaddress.IPv4Address(address))
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(address, None, family=socket.AF_INET, type=socket.SOCK_STREAM),
            timeout=5.0,
        )
    except (OSError, asyncio.TimeoutError):
        return ""
    for info in infos:
        return str(info[4][0])
    return ""


ProgressCallback = Callable[[Activity], None]


class NetworkCheck:
    """Runs the network check for one audit session.

    ``start_listeners`` opens the session-wide passive listeners (call it when
    the session starts; the session's teardown closes them). ``run`` does the
    rest and returns the ``Footprint``.
    """

    def __init__(
        self,
        discovery: DiscoveryEngine,
        address: str,
        ip: str,
        *,
        extended: bool = False,
        snmp_communities: list[str] | None = None,
        source_ip: str | None = None,
        listen_seconds: float = LISTEN_SECONDS,
        greeting_seconds: float = GREETING_SECONDS,
        port_pacing_ms: float = PORT_PACING_MS,
        on_progress: ProgressCallback | None = None,
        port_list: list[int] | None = None,
        web_ports: dict[int, str] | None = None,
        listeners: tuple[MDNSScanner, SSDPScanner, AMXDDPScanner] | None = None,
    ) -> None:
        """``port_list``, ``web_ports`` and ``listeners`` replace the ports
        checked, the web-port map and the passive listeners (tests, which
        cannot bind privileged ports or join multicast groups everywhere).
        Given listeners are read, never started or stopped here."""
        self.discovery = discovery
        self._port_override = port_list
        self._web_ports = dict(web_ports) if web_ports is not None else dict(WEB_PORTS)
        self._given_listeners = listeners
        self.footprint = Footprint(address=address, ip=ip, extended=extended)
        self._communities = ["public"] + [
            c for c in (snmp_communities or []) if c and c != "public"
        ]
        self._source_ip = source_ip if source_ip is not None else _control_interface()
        self.footprint.source_ip = self._source_ip
        self._listen_seconds = listen_seconds
        self._greeting_seconds = greeting_seconds
        self._pacing_ms = port_pacing_ms
        self._on_progress = on_progress
        self.activities: dict[str, Activity] = {key: Activity(key) for key in ACTIVITIES}
        self._mdns: MDNSScanner | None = None
        self._ssdp: SSDPScanner | None = None
        self._amx: AMXDDPScanner | None = None
        self._listener_tasks: list[asyncio.Task] = []
        self._listeners_started: float | None = None
        self._catalog_identity: dict[str, Any] = {}
        self._catalog_forced = False
        self._snmp_info: Any = None
        # idle, running, done, failed or cancelled; ``error`` says why it failed.
        self.status = "idle"
        self.error = ""

    # -- progress -------------------------------------------------------------

    def _set(self, key: str, status: str, message: str = "") -> None:
        act = self.activities[key]
        now = time.time()
        if status == RUNNING and act.started_at is None:
            act.started_at = now
        if status in (DONE, SKIPPED, FAILED):
            act.finished_at = now
        act.status = status
        act.message = message
        if self._on_progress is not None:
            self._on_progress(act)

    # -- the listeners --------------------------------------------------------

    async def start_listeners(self) -> None:
        """Open the mDNS, SSDP and AMX DDP listeners for the session."""
        if self._listener_tasks or self._listeners_started is not None:
            return
        if self._given_listeners is not None:
            self._mdns, self._ssdp, self._amx = self._given_listeners
            self._listeners_started = asyncio.get_running_loop().time()
            return
        ip = self.footprint.ip
        types = list(BASELINE_SERVICE_TYPES)
        for hint in self.discovery.discovery_hints:
            types.extend(fp.service for fp in hint.mdns if fp.service)
        self._mdns = MDNSScanner(
            control_ip=self._source_ip, service_types=types,
            query_enumerated_types=True, enumerated_from=[ip] if ip else [],
        )
        self._ssdp = SSDPScanner(control_ip=self._source_ip, capture=True)
        self._amx = AMXDDPScanner(control_ip=self._source_ip)
        self._listeners_started = asyncio.get_running_loop().time()
        self._listener_tasks = [
            asyncio.create_task(self._mdns.start(duration=_LISTENER_CEILING_SECONDS)),
            asyncio.create_task(self._ssdp.scan(
                timeout=_LISTENER_CEILING_SECONDS, fetch_descriptions=False,
            )),
            asyncio.create_task(self._amx.start(duration=_LISTENER_CEILING_SECONDS)),
        ]

    async def stop_listeners(self) -> None:
        """Close the listeners (the session's teardown calls this)."""
        if self._given_listeners is not None:
            return
        for scanner in (self._mdns, self._ssdp, self._amx):
            if scanner is not None:
                try:
                    await scanner.stop()
                except Exception:
                    log.debug("Listener stop failed", exc_info=True)
        tasks = [t for t in self._listener_tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- the check ------------------------------------------------------------

    async def run(self) -> Footprint:
        """Run every activity and the verdict. Returns the footprint."""
        fp = self.footprint
        await self.start_listeners()
        await self._refresh_catalog()
        self._network_facts()

        # UDP work runs beside the TCP sequence; nothing it sends opens a
        # connection the TCP activities would collide with.
        snmp_task = asyncio.create_task(self._snmp())
        try:
            await self._address()
            await self._ports()
            await self._greetings()
            await self._web()
            await self._probes()
            await self._announcements()
            await snmp_task
        finally:
            if not snmp_task.done():
                snmp_task.cancel()
                await asyncio.gather(snmp_task, return_exceptions=True)
        await self._late_mac()
        self._build_evidence()
        self._verdict()
        self._standing_limits()
        fp.finished_at = time.time()
        return fp

    async def _refresh_catalog(self) -> None:
        """Fetch the catalog now and fold it into the signal index."""
        try:
            await self.discovery.refresh_signal_index_with_catalog(force_catalog=True)
            self._catalog_forced = True
        except Exception:
            log.warning("Catalog refresh for the audit failed", exc_info=True)
        self._catalog_identity = self.discovery.community_index.identity()

    def _network_facts(self) -> None:
        fp = self.footprint
        fp.local_ip = self._source_ip or local_route_ip(fp.ip)
        for adapter in get_network_adapters():
            if adapter.get("ip") == fp.local_ip:
                fp.interface = adapter.get("name", "")
                break
        fp.local_subnets = get_local_subnets()
        try:
            addr = ipaddress.IPv4Address(fp.ip)
            fp.same_subnet = any(
                addr in ipaddress.IPv4Network(net, strict=False) for net in fp.local_subnets
            )
        except ValueError:
            fp.same_subnet = None

    async def _address(self) -> None:
        fp = self.footprint
        self._set("address", RUNNING, f"Checking {fp.ip}.")
        method = await icmp.select_ping_method()
        attempts: list[dict[str, Any]] = []
        result = icmp.RESULT_TIMEOUT
        if method == icmp.METHOD_NONE:
            result = icmp.RESULT_ERROR
            fp.add_limit(
                "ping_unavailable",
                "This computer cannot send pings (no permission and no ping "
                "command), so whether the device answers ping is unknown.",
            )
        else:
            for _ in range(3):
                began = time.monotonic()
                result = await icmp.ping_host(
                    fp.ip, timeout=1.0, source_ip=self._source_ip, method=method,
                )
                attempts.append({
                    "result": result,
                    "ms": round((time.monotonic() - began) * 1000.0, 1),
                })
                if result == icmp.RESULT_ALIVE:
                    break
        fp.ping = {"method": method, "result": result, "attempts": attempts}

        names_task = asyncio.create_task(_resolve_hostnames([fp.ip]))
        netbios_task = asyncio.create_task(
            netbios_query(fp.ip, timeout=1.5, source_ip=self._source_ip)
        )
        names, netbios = await asyncio.gather(names_task, netbios_task, return_exceptions=True)
        if isinstance(names, dict):
            fp.reverse_dns = names.get(fp.ip)
        if isinstance(netbios, dict):
            fp.netbios = netbios
        said = "answers ping" if result == icmp.RESULT_ALIVE else "did not answer ping"
        self._set("address", DONE, f"{fp.ip} {said}.")

    async def _ports(self) -> None:
        fp = self.footprint
        if self._port_override is not None:
            ports = sorted(set(self._port_override))
        else:
            community = await self.discovery.community_index.get_drivers()
            _, ports = self.discovery.port_scan_lists(community, extended=True)
            if fp.extended:
                ports = sorted(set(ports) | set(range(1, 1025)))
        fp.port_list = ports
        self._set("ports", RUNNING, f"Checking {len(ports)} TCP ports.")
        fp.port_states = await scan_host_port_states(
            fp.ip, ports, timeout=PORT_TIMEOUT, stagger_ms=self._pacing_ms,
            source_ip=self._source_ip,
        )
        open_ports = fp.open_ports()
        refused = sum(1 for s in fp.port_states.values() if s == PORT_REFUSED)
        if open_ports:
            message = f"Open: {', '.join(str(p) for p in open_ports)}."
        elif refused:
            message = "No open ports; the device refused connections, so it is there."
        else:
            message = "No port answered."
        self._set("ports", DONE, message)

    async def _greetings(self) -> None:
        fp = self.footprint
        open_ports = fp.open_ports()
        if not open_ports:
            self._set("greetings", SKIPPED, "No open ports to listen to.")
            return
        self._set(
            "greetings", RUNNING,
            f"Listening to {len(open_ports)} open port{'s' if len(open_ports) != 1 else ''}.",
        )
        sem = asyncio.Semaphore(TCP_CONCURRENCY)

        async def one(port: int) -> None:
            async with sem:
                fp.greetings[port] = await read_greeting(
                    fp.ip, port, wait=self._greeting_seconds, source_ip=self._source_ip,
                )

        await asyncio.gather(*(one(p) for p in open_ports))
        spoke = [p for p in open_ports if fp.greetings[p].data]
        message = (
            f"Sent something unprompted: {', '.join(str(p) for p in spoke)}."
            if spoke else "No port sent anything unprompted."
        )
        self._set("greetings", DONE, message)

    def _web_targets(self) -> tuple[dict[int, str], set[int]]:
        """(web port -> scheme, TLS-only ports).

        The conventional web ports that are open, plus every port the device
        itself advertised as a web server (an mDNS ``_http``/``_https``
        service, an SSDP description location), scanned or not: the device
        said a server is there, and it is often on a port no list covers.
        """
        fp = self.footprint
        open_ports = set(fp.open_ports())
        web = {p: scheme for p, scheme in self._web_ports.items() if p in open_ports}
        mdns = self._mdns.results.get(fp.ip) if self._mdns else None
        if mdns is not None:
            for svc in mdns.service_list():
                kind = _WEB_MDNS_TYPES.get((svc.service_type or "").lower().rstrip("."))
                if kind and svc.port:
                    web.setdefault(svc.port, kind)
        ssdp = self._ssdp.results.get(fp.ip) if self._ssdp else None
        if ssdp is not None and ssdp.port:
            web.setdefault(ssdp.port, "https" if (ssdp.location or "").lower().startswith(
                "https:") else "http")
        tls_only: set[int] = set()
        for hint in self.discovery.discovery_hints:
            spec = hint.tcp_probe
            if spec is not None and spec.tls and spec.port in open_ports and spec.port not in web:
                tls_only.add(spec.port)
        return web, tls_only

    async def _web(self) -> None:
        fp = self.footprint
        web, tls_only = self._web_targets()
        if not web and not tls_only:
            self._set("web", SKIPPED, "No web or TLS port is open.")
            return
        self._set("web", RUNNING, "Reading web pages and certificates.")
        sem = asyncio.Semaphore(TCP_CONCURRENCY)

        async def page(port: int, scheme: str) -> None:
            async with sem:
                exchange = await http_get(
                    f"{scheme}://{fp.ip}:{port}/", timeout=5.0, source_ip=self._source_ip,
                    max_bytes=WEB_BODY_BYTES, read_to_end=True,
                )
                if exchange is None:
                    return
                fp.web[port] = exchange
                if exchange.certificate:
                    fp.certificates[port] = exchange.certificate

        async def certificate(port: int) -> None:
            async with sem:
                details, error = await _read_certificate(fp.ip, port, self._source_ip)
                if details:
                    fp.certificates[port] = details
                else:
                    fp.certificate_errors[port] = error

        await asyncio.gather(
            *(page(p, s) for p, s in sorted(web.items())),
            *(certificate(p) for p in sorted(tls_only)),
        )
        titles = [
            f"{p}: {x.title()}" for p, x in sorted(fp.web.items()) if x.title()
        ]
        message = f"Pages: {'; '.join(titles)}." if titles else (
            f"Read {len(fp.web)} page{'s' if len(fp.web) != 1 else ''}"
            f" and {len(fp.certificates)} certificate"
            f"{'s' if len(fp.certificates) != 1 else ''}."
        )
        self._set("web", DONE, message)

    async def _announcements(self) -> None:
        """Wait out the listening window, then read what the device said."""
        fp = self.footprint
        loop = asyncio.get_running_loop()
        started = self._listeners_started or loop.time()
        while True:
            left = self._listen_seconds - (loop.time() - started)
            if left <= 0:
                break
            self._set(
                "announcements", RUNNING,
                f"Listening for announcements ({int(left + 0.999)} s left).",
            )
            await asyncio.sleep(min(1.0, left))

        if self._ssdp is not None and fp.ip in self._ssdp.results:
            try:
                await asyncio.wait_for(self._ssdp.fetch_description(fp.ip), timeout=8.0)
            except (asyncio.TimeoutError, OSError):
                log.debug("SSDP description fetch timed out", exc_info=True)

        self._read_listeners()
        heard = [
            name for name, found in (
                ("mDNS", fp.mdns), ("SSDP", fp.ssdp), ("AMX DDP", fp.amx_ddp),
            ) if found
        ]
        self._set(
            "announcements", DONE,
            f"Heard: {', '.join(heard)}." if heard else "The device announced nothing.",
        )

    def _read_listeners(self) -> None:
        fp = self.footprint
        ip = fp.ip
        for name, scanner in (("mdns", self._mdns), ("ssdp", self._ssdp), ("amx_ddp", self._amx)):
            results = scanner.results if scanner is not None else {}
            fp.listeners[name] = {
                "running": scanner is not None and not scanner.env_error,
                "error": (scanner.env_error if scanner is not None else "not started") or "",
                "other_devices_heard": len([k for k in results if k != ip]),
            }

        mdns = self._mdns.results.get(ip) if self._mdns else None
        if mdns is not None:
            enumerated = self._mdns.enumerated_service_types.get(ip, set())
            fp.mdns = {
                "hostname": mdns.hostname,
                "address_name": mdns.address_name,
                "services": [
                    {
                        "service_type": svc.service_type,
                        "instance_name": svc.instance_name,
                        "port": svc.port,
                        "target": svc.target,
                        "txt": dict(svc.txt_records),
                    }
                    for svc in mdns.service_list()
                ],
                "enumerated_types": sorted(enumerated),
            }
        elif self._mdns is not None and self._mdns.enumerated_service_types.get(ip):
            fp.mdns = {
                "hostname": None, "address_name": None, "services": [],
                "enumerated_types": sorted(self._mdns.enumerated_service_types[ip]),
            }

        ssdp = self._ssdp.results.get(ip) if self._ssdp else None
        if ssdp is not None:
            fp.ssdp = {
                "device_types": list(ssdp.device_types),
                "usn": ssdp.usn,
                "st": ssdp.st,
                "server": ssdp.server,
                "location": ssdp.location,
                "friendly_name": ssdp.friendly_name,
                "manufacturer": ssdp.manufacturer,
                "model_name": ssdp.model_name,
                "model_number": ssdp.model_number,
                "serial_number": ssdp.serial_number,
                "udn": ssdp.udn,
                "raw_headers": [dict(h) for h in ssdp.raw_headers],
                "description_head": ssdp.description_head,
                "description_xml": ssdp.description_xml,
            }

        beacon = self._amx.results.get(ip) if self._amx else None
        if beacon is not None:
            fp.amx_ddp = {
                "make": beacon.make,
                "model": beacon.model,
                "revision": beacon.revision,
                "uuid": beacon.uuid,
                "sdk_class": beacon.sdk_class,
                "config_name": beacon.config_name,
                "config_url": beacon.config_url,
                "fields": dict(beacon.fields),
                "raw": beacon.raw,
            }

    async def _snmp(self) -> None:
        fp = self.footprint
        self._set("snmp", RUNNING, "Asking SNMP.")
        scanner = SNMPScanner(source_ip=self._source_ip, capture=True)
        info = None
        used = -1
        for index, community in enumerate(self._communities):
            try:
                info = await scanner.query_device(fp.ip, community, timeout=2.0, entity_mib=True)
            except Exception:
                log.debug("SNMP query failed", exc_info=True)
                info = None
            if info is not None:
                used = index
                break
        tried = len(self._communities)
        if info is None:
            fp.snmp = {"answered": False, "communities_tried": tried}
            others = f" or the {tried - 1} other" if tried > 1 else ""
            self._set(
                "snmp", DONE,
                f"No SNMP answer with community public{others}"
                f"{' communities' if tried > 2 else ' community' if tried == 2 else ''}.",
            )
            return
        fp.snmp = {
            "answered": True,
            "community": "public" if used == 0 else f"community {used + 1} of {tried}",
            "communities_tried": used + 1,
            "values": info.to_dict(),
            "pen": info.pen,
            "if_phys_addresses": dict(info.if_phys_addresses or {}),
        }
        self._snmp_info = info
        if fp.extended:
            self._set("snmp", RUNNING, "Reading every SNMP value.")
            fp.snmp["walk"], fp.snmp["walk_complete"] = await self._snmp_walk(
                self._communities[used],
            )
        self._set("snmp", DONE, f"SNMP answered: {info.sys_descr or info.sys_name}.")

    async def _snmp_walk(self, community: str) -> tuple[list[dict[str, Any]], bool]:
        from openavc.discovery import snmp_scanner
        from openavc.transport.snmp import SNMPTransport

        # The port the scanner asked, read now so one setting moves both.
        transport = SNMPTransport(
            self.footprint.ip, port=snmp_scanner.SNMP_PORT,
            community=community, timeout=2.0, retries=1,
        )
        rows: list[dict[str, Any]] = []
        try:
            await transport.open(local_addr=self._source_ip or None)
            found = await transport.walk("1.3.6.1", limit=SNMP_WALK_LIMIT)
            rows = [{"oid": vb.oid, "type": vb.type, "value": vb.value} for vb in found]
        except Exception as exc:
            log.debug("SNMP walk stopped: %s", exc)
        finally:
            await transport.close()
        return rows, len(rows) < SNMP_WALK_LIMIT

    async def _probes(self) -> None:
        fp = self.footprint
        open_ports = set(fp.open_ports())
        limiter = RateLimiter(rate_per_sec=10.0)
        tcp_specs = [
            h.tcp_probe for h in self.discovery.discovery_hints
            if h.tcp_probe is not None and h.tcp_probe.port in open_ports
        ]
        udp_specs = [h.udp_probe for h in self.discovery.discovery_hints if h.udp_probe is not None]
        companions = self.discovery.discovery_companions
        total = len(tcp_specs) + len(udp_specs) + len(companions)
        if not total:
            self._set("probes", SKIPPED, "No driver declares a check this device could answer.")
            return
        self._set("probes", RUNNING, f"Running {total} driver identification checks.")

        async def udp(spec) -> list[ProbeObservation]:
            observed = await observe_udp_probe(
                spec, targets=[fp.ip], source_ip=self._source_ip, rate_limiter=limiter,
            )
            return [o for o in observed if o.target in ("", fp.ip)]

        udp_task = asyncio.gather(*(udp(s) for s in udp_specs), return_exceptions=True)
        # TCP probes one port at a time: a single-session device must not
        # see two of them at once.
        by_port: dict[int, list] = {}
        for spec in tcp_specs:
            by_port.setdefault(spec.port, []).append(spec)
        for port in sorted(by_port):
            for spec in by_port[port]:
                try:
                    fp.probes.append(await observe_tcp_active_probe(
                        spec, target=fp.ip, source_ip=self._source_ip, rate_limiter=limiter,
                    ))
                except Exception:
                    log.debug("TCP probe %s failed", spec.probe_id, exc_info=True)
        for result in await udp_task:
            if isinstance(result, list):
                fp.probes.extend(result)
        if companions:
            await self._companions(companions)
        matched = sorted({o.probe_id for o in fp.probes if o.matched})
        self._set(
            "probes", DONE,
            f"{len(matched)} of {total} matched." if matched
            else f"None of {total} matched.",
        )

    async def _companions(self, companions: dict[str, Any]) -> None:
        fp = self.footprint
        by_port = {port: (fp.ip,) for port in fp.open_ports()}

        async def emit(host: str, ev: Evidence) -> None:
            if host != fp.ip:
                return
            fp.companion_evidence.append(ev.to_dict())

        companion_log = logging.getLogger("discovery.companion.run")
        runs = [
            run_companion(driver_id, probe_fn, ProbeContext(
                driver_id=driver_id,
                source_ip=self._source_ip,
                target_subnets=(f"{fp.ip}/32",),
                timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
                log=companion_log,
                _emit_for_host=emit,
                hosts_by_open_port=by_port,
            ))
            for driver_id, probe_fn in companions.items()
        ]
        await asyncio.gather(*runs, return_exceptions=True)

    async def _late_mac(self) -> None:
        """The MAC address, read once this computer has talked to the device."""
        fp = self.footprint
        arp = await harvest_arp_table()
        if arp.get(fp.ip):
            fp.mac = arp[fp.ip]
            fp.mac_source = "arp"
        elif fp.netbios and fp.netbios.get("mac"):
            fp.mac = fp.netbios["mac"]
            fp.mac_source = "netbios"
        else:
            macs = fp.snmp.get("if_phys_addresses") or {}
            if macs:
                fp.mac = next(iter(macs.values()))
                fp.mac_source = "snmp"
        if not fp.mac:
            if fp.same_subnet is False:
                fp.add_limit(
                    "mac_other_segment",
                    "The device is on another network segment, so its MAC address "
                    "cannot be read from this computer. Run the audit from a "
                    "computer on the device's own network to record it.",
                )
            else:
                fp.add_limit(
                    "mac_unread",
                    "This computer's address table has no MAC address for the device.",
                )

    # -- evidence and verdict -------------------------------------------------

    def _build_evidence(self) -> None:
        """The evidence a scan would build from these observations."""
        fp = self.footprint
        index = self.discovery.signal_index
        device = DiscoveredDevice(ip=fp.ip)
        evidence: list[Evidence] = []

        info: dict[str, Any] = {}
        hostname = fp.reverse_dns
        if fp.netbios and fp.netbios.get("hostname"):
            info["device_name"] = fp.netbios["hostname"]
            if not hostname:
                hostname = fp.netbios["hostname"]
        if hostname:
            info["hostname"] = hostname
            evidence.extend(hostname_evidence(hostname, index))
        oui_info: dict[str, Any] = {}
        if fp.mac:
            mac_info, oui_ev = mac_info_and_evidence(fp.mac, self.discovery.oui_db)
            info["mac"] = mac_info.pop("mac")
            oui_info = mac_info
            evidence.append(oui_ev)
        if info:
            merge_device_info(device, info, "arp")
        if oui_info:
            merge_device_info(device, oui_info, "arp", fill_only=True)

        open_ports = fp.open_ports()
        if open_ports:
            merge_device_info(device, {"open_ports": open_ports}, "port_scan")
        banners = {
            p: g.data.decode("utf-8", errors="replace").strip()
            for p, g in fp.greetings.items() if g.data
        }
        if banners:
            merge_device_info(device, {"banners": banners}, "banner")

        if self._mdns is not None and fp.ip in self._mdns.results:
            result = self._mdns.results[fp.ip]
            evidence.extend(result.to_evidence_records())
            merge_device_info(device, result.to_device_info(), "mdns")
        if self._ssdp is not None and fp.ip in self._ssdp.results:
            result = self._ssdp.results[fp.ip]
            evidence.extend(result.to_evidence_records())
            merge_device_info(device, result.to_device_info(), "ssdp")
        if self._amx is not None and fp.ip in self._amx.results:
            beacon = self._amx.results[fp.ip]
            evidence.append(beacon.to_evidence())
            merge_device_info(device, beacon.to_device_info(), "amx_ddp")
        snmp_info = self._snmp_info
        if snmp_info is not None:
            ev = snmp_info.to_evidence()
            if ev is not None:
                evidence.append(ev)
            merge_device_info(device, snmp_info.to_device_info(), "snmp")

        for obs in fp.probes:
            if obs.evidence is None:
                continue
            evidence.append(obs.evidence)
            probe_info = device_info_from_evidence(obs.evidence)
            if probe_info:
                merge_device_info(device, probe_info, "protocol_probe", fill_only=True)
        for raw in fp.companion_evidence:
            ev = _evidence_from_dict(raw)
            evidence.append(ev)
            companion_info = device_info_from_evidence(ev)
            if companion_info:
                merge_device_info(device, companion_info, "companion", fill_only=True)

        evidence.extend(derived_evidence(evidence, device.open_ports, index))
        device.evidence_log = evidence
        fp.evidence = evidence
        fp.device = device

    def _verdict(self) -> None:
        fp = self.footprint
        match = self.discovery.tier_matcher.match(fp.evidence)
        explanation = explain_matches(fp.evidence, self.discovery.signal_index)
        if fp.device is not None:
            fp.device.identification = match

        named = [d for d in [match.driver_id, *match.candidates, *match.alternatives] if d]
        named.extend(d for d in explanation.drivers() if d not in named)
        hints = {h.driver_id: h for h in self.discovery.discovery_hints}
        checks = {
            driver_id: [c.to_dict() for c in evaluate_driver_signals(hints[driver_id], fp.observations())]
            for driver_id in named if driver_id in hints
        }

        if match.state == DeviceState.IDENTIFIED:
            state = "identified"
        elif match.state == DeviceState.POSSIBLE:
            state = "possible"
        elif fp.answered():
            state = "unknown"
        else:
            state = "nothing"

        identity = dict(self._catalog_identity)
        catalog_fresh = self._catalog_forced and not identity.get("error") and bool(
            identity.get("sha256")
        )
        identity["reachable"] = not identity.get("error")
        identity["used"] = (
            "fresh" if catalog_fresh
            else "cached" if identity.get("sha256")
            else "none"
        )
        drivers = self._driver_names(named)
        fp.verdict = {
            "state": state,
            "sentence": verdict_sentence(state, match.driver_id, match.candidates, drivers),
            "identification": match.to_dict(),
            "explanation": explanation.to_dict(),
            "drivers": drivers,
            "checks": checks,
            "catalog": identity,
            "signal_index_drivers": self.discovery.signal_index.driver_count(),
        }

    def _driver_names(self, driver_ids: list[str]) -> dict[str, dict[str, Any]]:
        from openavc.drivers.registry import list_registered_drivers

        installed = {d.get("id"): d for d in list_registered_drivers()}
        hints = {h.driver_id: h for h in self.discovery.discovery_hints}
        out: dict[str, dict[str, Any]] = {}
        for driver_id in driver_ids:
            hint = hints.get(driver_id)
            reg = installed.get(driver_id) or {}
            out[driver_id] = {
                "name": (hint.driver_name if hint else "") or reg.get("name") or driver_id,
                "manufacturer": (hint.manufacturer if hint else "") or reg.get("manufacturer", ""),
                "installed": driver_id in installed,
            }
        return out

    def _standing_limits(self) -> None:
        """The limits every check has, and the ones this run's listeners hit."""
        fp = self.footprint
        heard_names = {"mdns": "mDNS", "ssdp": "SSDP", "amx_ddp": "AMX DDP"}
        found = {"mdns": fp.mdns, "ssdp": fp.ssdp, "amx_ddp": fp.amx_ddp}
        for key, health in fp.listeners.items():
            name = heard_names[key]
            if not health.get("running"):
                fp.add_limit(
                    f"{key}_listener",
                    f"The {name} listener could not start ({health.get('error')}), so "
                    f"whether the device uses {name} is unknown.",
                )
            elif not found[key] and not health.get("other_devices_heard"):
                fp.add_limit(
                    f"{key}_silent",
                    f"This computer heard no {name} from any device, so a firewall may be "
                    f"blocking it. That the device sent none is not proven.",
                )
        catalog = fp.verdict.get("catalog", {})
        if catalog.get("used") == "none":
            fp.add_limit(
                "catalog_unreachable",
                "The driver catalog could not be fetched, so only installed drivers "
                "were checked.",
            )
        elif catalog.get("used") == "cached":
            fp.add_limit(
                "catalog_cached",
                "The driver catalog could not be fetched now; the copy fetched earlier "
                "was checked instead.",
            )
        if not fp.extended:
            fp.add_limit(
                "ports_standard",
                f"Only the standard list of {len(fp.port_list)} TCP ports was checked. "
                "The extended check adds every port from 1 to 1024.",
            )
        fp.add_limit(
            "udp_ports",
            "UDP ports were not scanned; only the drivers' UDP checks and SNMP "
            "were sent over UDP.",
        )
        fp.add_limit("ipv6", "IPv6 was not checked.")
        filtered = [p for p, s in fp.port_states.items() if s == PORT_FILTERED]
        if filtered and len(filtered) == len(fp.port_states):
            fp.add_limit(
                "ports_all_filtered",
                "No TCP port answered at all, not even to refuse. A firewall between "
                "this computer and the device would look the same as a device that "
                "is off.",
            )


def verdict_sentence(
    state: str,
    driver_id: str | None,
    candidates: list[str],
    drivers: dict[str, dict[str, Any]],
) -> str:
    """The verdict in one sentence, as the wizard and the report say it."""

    def name(d: str) -> str:
        return str(drivers.get(d, {}).get("name") or d)

    if state == "identified" and driver_id:
        return f"OpenAVC recognizes this device: {name(driver_id)}."
    if state == "possible":
        if len(candidates) == 1:
            return f"OpenAVC found a driver that might fit this device: {name(candidates[0])}."
        return "OpenAVC found a few drivers that might fit this device."
    if state == "unknown":
        return "OpenAVC can see this device but does not recognize it."
    return "Nothing answered at this address. Check the address and that the device is on."


def _control_interface() -> str:
    try:
        from openavc.system_config import get_system_config

        return get_system_config().get("network", "control_interface") or ""
    except Exception:
        return ""


async def _read_certificate(
    ip: str, port: int, source_ip: str,
) -> tuple[dict[str, Any] | None, str]:
    """A TLS handshake on ``ip:port`` and the certificate presented."""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                ip, port, local_addr=(source_ip, 0) if source_ip else None,
                ssl=discovery_tls_context(), server_hostname=ip,
            ),
            timeout=5.0,
        )
    except (asyncio.TimeoutError, TimeoutError, OSError) as exc:
        return None, str(exc) or type(exc).__name__
    try:
        return read_peer_certificate(writer), ""
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def _evidence_from_dict(raw: dict[str, Any]) -> Evidence:
    from openavc.discovery.result import SignalTier

    return Evidence(
        tier=SignalTier(raw["tier"]),
        source=raw["source"],
        data=dict(raw.get("data") or {}),
        at=raw.get("at") or time.time(),
    )


def check_state(session: "AuditSession") -> dict[str, Any]:
    """The network check as the wizard draws it: progress, then the result.

    The result carries what step 2 shows (the verdict with its sentence and
    "Why?", the evidence, the device record, the limits), redacted the way the
    report is. The complete record comes with the report.
    """
    check: NetworkCheck | None = session.check
    if check is None:
        return {"check": None}
    out: dict[str, Any] = {
        "status": check.status,
        "error": check.error,
        "activities": [check.activities[key].to_dict() for key in ACTIVITIES],
        "result": None,
    }
    fp = session.footprint
    if fp is not None:
        from openavc.audit.report import Redactor, redactions_for

        result = {
            "verdict": fp.verdict,
            "evidence": [ev.to_dict() for ev in fp.evidence],
            "device": fp.device.to_dict() if fp.device else None,
            "limits": [limit.to_dict() for limit in fp.limits],
            "open_ports": fp.open_ports(),
        }
        out["result"] = Redactor(redactions_for(session)).tree(result)
    return {"check": out}


def start_check(session: "AuditSession") -> None:
    """Run the session's network check in the background, once.

    Progress and the finished state reach the session's subscribers; the
    session's teardown cancels a check still running.
    """
    from openavc.audit.session import AuditError

    check: NetworkCheck | None = session.check
    if check is None:
        raise AuditError("This audit has no network check.")
    if check.status != "idle":
        raise AuditError("The network check has already run for this audit.")
    check.status = "running"
    session.enter_step("network_check")
    session.publish_state()

    async def run() -> None:
        try:
            session.footprint = await check.run()
            check.status = "done"
            session.add_timeline(
                "check.finished", check.footprint.verdict.get("sentence", "Network check done."),
            )
        except asyncio.CancelledError:
            check.status = "cancelled"
            raise
        except Exception as exc:
            log.exception("Network check failed")
            check.status = "failed"
            check.error = f"The network check stopped: {exc}"
            session.add_timeline("check.failed", check.error)
        finally:
            session.publish_state()

    session.track_task(asyncio.create_task(run()))


async def open_for_session(session: "AuditSession", discovery: DiscoveryEngine) -> NetworkCheck:
    """Attach a network check to ``session`` and open its listeners.

    Called as the session starts, so the listeners hear everything from then
    until the session ends; the session's teardown closes them. Progress goes
    to the session's subscribers as ``audit.progress`` and each finished
    activity into the timeline.
    """

    def progress(act: Activity) -> None:
        session.publish({"type": "audit.progress", "activity": act.to_dict()})
        if act.status in (DONE, SKIPPED, FAILED):
            session.add_timeline(f"check.{act.key}", act.message, status=act.status)

    check = NetworkCheck(
        discovery, session.target.address, session.target.ip,
        extended=session.options.extended,
        snmp_communities=session.options.snmp_communities,
        on_progress=progress,
    )
    await check.start_listeners()
    session.on_teardown(check.stop_listeners)
    session.check = check
    session.add_state_provider(lambda: check_state(session))
    return check
