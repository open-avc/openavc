"""Workload-derived scan budgets.

Discovery used to run under one flat stopwatch per depth (quick 60 /
standard 120 / thorough 180 seconds). The work, though, scales with the
network: a /20 holds 16x the addresses of a /24, and everything after the
ping sweep costs in proportion to the *alive* count, which nothing knows
until the sweep finishes. The same "standard" scan was therefore generous
on a small subnet and hopeless on a large one, and the only user-facing
lever was a number of seconds an AV integrator has no basis to pick.

The phases also starved in exactly the wrong order. The phase whose cost
is **most** predictable — the ping sweep, which is hosts / concurrency and
knowable before it runs — went first and spent the whole allowance, while
the phase whose cost is **least** predictable — driver probes, which
depend on how many hosts turn out to have a spec port open — went last and
never started. Measured on a /20 at standard depth: 80s of the 120s went
to the sweep, the scan truncated during the port scan, and the bench AV
gear came back "possible" because the probes that identify it never ran.

So this module budgets the terminal phases first. Every phase gets its own
deadline, derived from work known at the time that phase starts and capped
at a fixed share of the scan total, so no phase can spend another's
allocation. When a phase's projected cost exceeds its share, the **work is
narrowed before the phase starts** and the narrowing is named in a warning
— a bounded sweep must never read as full coverage.

Pure and stdlib-only: no I/O and no clock reads beyond the injected one, so
every projection here is unit-testable without a network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

# --- Measured per-item costs -------------------------------------------
#
# These mirror the timeouts the scanners actually use. Change one of those
# and change it here too, or the projection quietly drifts away from what
# the phase really costs and the budget stops meaning anything.

PING_TIMEOUT_SECONDS: float = 1.0          # ping_sweep()'s per-host timeout
PORT_CONNECT_TIMEOUT_SECONDS: float = 1.0  # scan_host_ports(timeout=1.0)
PORT_STAGGER_SECONDS: float = 0.020        # scan_host_ports(stagger_ms=20)
BANNER_TIMEOUT_SECONDS: float = 2.0        # grab_banners(timeout=2.0)
HOSTNAME_TIMEOUT_SECONDS: float = 1.0      # _resolve_hostnames per-host
HOSTNAME_CONCURRENCY: int = 20             # _resolve_hostnames default
NETBIOS_CONCURRENCY: int = 30              # netbios_sweep(concurrency=30)
ARP_HARVEST_SECONDS: float = 5.0           # one exec + parse, cold cache
SNMP_COLLECT_SECONDS: float = 5.0          # _collect_snmp_results wait
PROBE_RATE_PER_SECOND: float = 10.0        # the scan-wide probe RateLimiter
COMPANION_SECONDS: float = 10.0            # companion.DEFAULT_PROBE_TIMEOUT

# The passive-only follow-up port scan and the late ARP harvest sit inside
# the collect phase but their size isn't known when it is planned (they
# depend on what the listeners heard). A flat allowance keeps them from
# reading as free.
PASSIVE_FOLLOWUP_SECONDS: float = 10.0

# --- Budget keys -------------------------------------------------------
#
# One key per budgeted chunk of work. The first three match the engine's
# phase names; phase 7 splits in two because its halves narrow
# differently — collection shortens a wait, probing drops jobs.

PHASE_PING = "ping_sweep"
PHASE_ARP = "arp_harvest"
PHASE_PORT = "port_scan"
PHASE_PASSIVE = "passive_collect"
PHASE_PROBE = "protocol_probe"

BUDGET_KEYS: tuple[str, ...] = (
    PHASE_PING, PHASE_ARP, PHASE_PORT, PHASE_PASSIVE, PHASE_PROBE,
)

# Most of the scan total any one phase may claim. The shares sum to 0.95;
# the remaining 5% is reserve for the work between phases (progress
# emits, the WebSocket fan-out, the SignalIndex refresh) that belongs to
# no phase's projection.
#
# The exact numbers matter less than the ordering they encode: the sweep
# is capped at well under half the scan precisely so it cannot eat the
# phases that turn observations into identifications. That is the failure
# this module exists to prevent.
PHASE_SHARES: dict[str, float] = {
    PHASE_PING: 0.40,
    PHASE_ARP: 0.08,
    PHASE_PORT: 0.25,
    PHASE_PASSIVE: 0.10,
    PHASE_PROBE: 0.12,
}

# Smallest useful deadline per phase. A phase with almost no work still
# pays fixed costs (one `arp` exec, one connect round-trip), so a
# projection near zero must not produce a deadline that cuts the phase off
# before it starts. Floors lose to the share cap and to the time actually
# remaining — on a deliberately tiny scan total, every phase runs briefly
# rather than the early ones running fully and the late ones not at all.
PHASE_FLOORS: dict[str, float] = {
    PHASE_PING: 10.0,
    PHASE_ARP: 12.0,
    PHASE_PORT: 15.0,
    PHASE_PASSIVE: 12.0,
    PHASE_PROBE: 15.0,
}

# A ping is one packet per host, so sweeping *faster* costs burst rate but
# not coverage — always the better trade against dropping addresses. Bound
# how far that escalation can go, and never do it in gentle mode, which
# opted out of exactly this.
MAX_PING_CONCURRENCY_ESCALATION: int = 4
MAX_PING_CONCURRENCY: int = 200


@dataclass(frozen=True)
class DepthPolicy:
    """What a ``scan_depth`` *means*, beyond a number of seconds.

    ``scan_depth`` is the one discovery control an AV integrator can answer
    honestly — "how thorough should this be?" — so it selects a thoroughness
    policy (port-set width, whether NetBIOS runs, how long to listen for
    announcements, how much slack to allow) and lets the budget fall out of
    the workload. ``total_seconds`` is a ceiling the scan is very unlikely
    to reach, not a target: a /24 finishes in well under a minute at every
    depth because each phase is granted what its own work projects.
    """

    name: str
    # Ceiling on the whole scan when the request supplies no explicit
    # timeout. Also the denominator for every phase share.
    total_seconds: float
    # Multiplier on a projection before it becomes a deadline. Absorbs
    # per-host variance (a slow switch, a retransmit) without letting a
    # phase run unbounded.
    slack: float
    ping_concurrency: int
    port_host_concurrency: int
    extended_ports: bool
    netbios: bool
    snmp_entity_mib: bool
    passive_collect_seconds: float


_POLICIES: dict[str, DepthPolicy] = {
    "quick": DepthPolicy(
        name="quick",
        total_seconds=120.0,
        slack=1.2,
        ping_concurrency=50,
        port_host_concurrency=24,
        extended_ports=False,
        netbios=False,
        snmp_entity_mib=False,
        passive_collect_seconds=5.0,
    ),
    "standard": DepthPolicy(
        name="standard",
        total_seconds=300.0,
        slack=1.25,
        ping_concurrency=50,
        port_host_concurrency=16,
        extended_ports=False,
        netbios=True,
        snmp_entity_mib=True,
        passive_collect_seconds=15.0,
    ),
    "thorough": DepthPolicy(
        name="thorough",
        total_seconds=600.0,
        slack=1.4,
        ping_concurrency=50,
        port_host_concurrency=12,
        extended_ports=True,
        netbios=True,
        snmp_entity_mib=True,
        passive_collect_seconds=30.0,
    ),
}

# Gentle mode trades wall-clock for network load. It only lowers
# concurrency — the projections read concurrency, so the budgets widen to
# match automatically instead of needing a second set of numbers.
GENTLE_PING_CONCURRENCY: int = 10
GENTLE_PORT_HOST_CONCURRENCY: int = 3


def resolve_policy(depth: str, gentle: bool = False) -> DepthPolicy:
    """The policy for a depth name, with gentle-mode overrides applied.

    An unknown depth resolves to ``standard`` rather than raising — the
    value reaches here from a stored project setting as well as from a
    validated request.
    """
    policy = _POLICIES.get(depth, _POLICIES["standard"])
    if not gentle:
        return policy
    return DepthPolicy(
        name=policy.name,
        total_seconds=policy.total_seconds,
        slack=policy.slack,
        ping_concurrency=min(policy.ping_concurrency, GENTLE_PING_CONCURRENCY),
        port_host_concurrency=min(
            policy.port_host_concurrency, GENTLE_PORT_HOST_CONCURRENCY
        ),
        extended_ports=policy.extended_ports,
        netbios=policy.netbios,
        snmp_entity_mib=policy.snmp_entity_mib,
        passive_collect_seconds=policy.passive_collect_seconds,
    )


def depth_total_seconds(depth: str) -> float:
    """Ceiling for a depth, for callers that only need the number."""
    return resolve_policy(depth).total_seconds


# --- Projections -------------------------------------------------------


def project_ping_sweep(hosts: int, concurrency: int) -> float:
    """Wall-clock for a ping sweep: every host costs one timeout, and
    ``concurrency`` of them are in flight at once."""
    if hosts <= 0:
        return 0.0
    return hosts / max(1, concurrency) * PING_TIMEOUT_SECONDS


def project_arp_phase(alive: int, netbios: bool) -> float:
    """One ARP table read plus the name lookups, which run concurrently.

    Reverse DNS (concurrency 20) and NetBIOS (concurrency 30) overlap, so
    the slower of the two sets the cost — that's reverse DNS whenever both
    run, since fewer lookups are in flight.
    """
    if alive <= 0:
        return ARP_HARVEST_SECONDS
    dns = alive / HOSTNAME_CONCURRENCY * HOSTNAME_TIMEOUT_SECONDS
    nbt = alive / NETBIOS_CONCURRENCY * HOSTNAME_TIMEOUT_SECONDS if netbios else 0.0
    return ARP_HARVEST_SECONDS + max(dns, nbt)


def project_port_scan_host(ports: int) -> float:
    """Worst-case seconds for one host: the last port's connect starts
    after the full stagger and may run the whole timeout, then banner reads
    (concurrent across the banner-friendly ports) may add their own."""
    if ports <= 0:
        return 0.0
    stagger = max(0, ports - 1) * PORT_STAGGER_SECONDS
    return stagger + PORT_CONNECT_TIMEOUT_SECONDS + BANNER_TIMEOUT_SECONDS


def project_port_scan(alive: int, ports: int, host_concurrency: int) -> float:
    if alive <= 0 or ports <= 0:
        return 0.0
    return alive / max(1, host_concurrency) * project_port_scan_host(ports)


def project_probes(tcp_jobs: int, udp_sends: int, companions: int) -> float:
    """Driver probes are rate-limited scan-wide, so the send cap — not the
    per-probe timeout — sets the cost. Companions run concurrently with
    each other under their own hard timeout."""
    sends = max(0, tcp_jobs) + max(0, udp_sends)
    seconds = sends / PROBE_RATE_PER_SECOND
    if companions > 0:
        seconds += COMPANION_SECONDS
    return seconds


def project_passive_collect(wait_seconds: float) -> float:
    """The announcement wait, plus SNMP collection and the follow-up work
    that trails it inside the same phase."""
    return wait_seconds + SNMP_COLLECT_SECONDS + PASSIVE_FOLLOWUP_SECONDS


# --- The budget --------------------------------------------------------


class ScanBudget:
    """Hands out one deadline per phase and remembers what it granted.

    A grant is ``min(max(floor, projected x slack), share x total,
    time actually left)``. All three clamps matter: the projection keeps a
    phase from claiming time it doesn't need, the share keeps a predictable
    phase from starving an unpredictable one downstream, and the remaining
    time keeps the sum honest when an earlier phase overran.
    """

    def __init__(
        self,
        policy: DepthPolicy,
        total_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self.total_seconds = max(1.0, float(total_seconds))
        self._clock = clock
        self._started = clock()
        self.grants: dict[str, float] = {}
        self.projections: dict[str, float] = {}

    @property
    def elapsed(self) -> float:
        return self._clock() - self._started

    @property
    def remaining(self) -> float:
        return max(0.0, self.total_seconds - self.elapsed)

    @property
    def granted_total(self) -> float:
        """Sum of the deadlines handed out so far — the scan's budget as
        actually derived, rather than the ceiling it was capped against."""
        return sum(self.grants.values())

    def grant(self, key: str, projected: float, terminal: bool = False) -> float:
        """Deadline for one phase.

        ``terminal`` drops the share cap. A share exists to stop one phase
        spending a *later* phase's allocation, so the last budgeted phase has
        nothing to protect and may use whatever the earlier phases left.
        Without this it was cut at its share while the scan still had time on
        the clock — measured live: probes cut at 5.4s of a 45s scan that
        finished in 32s, leaving the bench switcher unidentified for want of
        time nothing else was going to use. ``projected x slack`` still bounds
        it, so a terminal phase can't idle away the remainder either.
        """
        share = PHASE_SHARES.get(key, 0.1) * self.total_seconds
        floor = PHASE_FLOORS.get(key, 10.0)
        want = max(floor, max(0.0, projected) * self.policy.slack)
        ceiling = self.remaining if terminal else min(share, self.remaining)
        allowed = max(0.0, min(want, ceiling))
        self.grants[key] = round(allowed, 2)
        self.projections[key] = round(max(0.0, projected), 2)
        return allowed


# --- Phase plans -------------------------------------------------------
#
# Each planner returns the narrowed parameters for its phase plus the
# deadline to run it under. ``warnings`` is empty when nothing was dropped;
# a non-empty list means the phase covers less than the user asked for and
# the scan must report itself ``partial``.


@dataclass(frozen=True)
class PingPlan:
    concurrency: int
    max_hosts: int | None  # None = sweep every address
    budget: float
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ArpPlan:
    name_lookup_hosts: int | None  # None = resolve for every alive host
    netbios: bool
    budget: float
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PortScanPlan:
    ports: list[int]
    host_limit: int | None  # None = scan every alive host
    concurrency: int
    budget: float
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PassiveCollectPlan:
    wait_seconds: float
    budget: float
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProbePlan:
    max_tcp_jobs: int | None  # None = run every declared probe
    budget: float
    warnings: list[str] = field(default_factory=list)


def plan_ping_sweep(
    budget: ScanBudget, total_hosts: int, gentle: bool,
) -> PingPlan:
    """Budget phase 3 from the address count, known after subnet expansion."""
    concurrency = budget.policy.ping_concurrency
    projected = project_ping_sweep(total_hosts, concurrency)
    allowed = budget.grant(PHASE_PING, projected)

    if total_hosts <= 0 or projected <= allowed:
        return PingPlan(concurrency, None, allowed)

    # Sweep faster before sweeping less.
    if not gentle:
        faster = min(
            concurrency * MAX_PING_CONCURRENCY_ESCALATION, MAX_PING_CONCURRENCY,
        )
        if faster > concurrency:
            concurrency = faster
            projected = project_ping_sweep(total_hosts, concurrency)
            if projected <= allowed:
                return PingPlan(concurrency, None, allowed)

    # Still over budget: sweep what fits and say what was skipped, so the
    # phases that identify devices still get to run on what was found.
    max_hosts = max(1, int(allowed / PING_TIMEOUT_SECONDS * concurrency))
    if max_hosts >= total_hosts:
        return PingPlan(concurrency, None, allowed)
    return PingPlan(
        concurrency,
        max_hosts,
        allowed,
        [
            f"Host sweep covered the first {max_hosts:,} of {total_hosts:,} "
            f"addresses — {total_hosts - max_hosts:,} were skipped so there "
            "was time left to identify what it found. Scan a smaller subnet, "
            "or raise the time limit, to sweep the whole range."
        ],
    )


def plan_arp_phase(budget: ScanBudget, alive: int, netbios: bool) -> ArpPlan:
    """Budget phase 4 from the alive count, known once the sweep finishes."""
    projected = project_arp_phase(alive, netbios)
    allowed = budget.grant(PHASE_ARP, projected)

    if alive <= 0 or projected <= allowed:
        return ArpPlan(None, netbios, allowed)

    # The ARP read is never dropped: it is one cheap exec and the phase's
    # highest-value signal (MAC -> OUI -> vendor). Bound the name lookups
    # instead — hostnames feed `discovery.hostname` driver hints, so losing
    # them costs identification signal, not just cosmetics, and bounding
    # beats dropping.
    spare = max(0.0, allowed - ARP_HARVEST_SECONDS)
    per_host = HOSTNAME_TIMEOUT_SECONDS / HOSTNAME_CONCURRENCY
    capped = max(0, min(alive, int(spare / per_host)))
    if capped >= alive:
        return ArpPlan(None, netbios, allowed)
    return ArpPlan(
        capped,
        netbios,
        allowed,
        [
            f"Hostname lookups covered {capped:,} of {alive:,} devices. MAC "
            "addresses and vendors were read for all of them; the rest are "
            "missing only their hostname."
        ],
    )


def plan_port_scan(
    budget: ScanBudget,
    alive: int,
    ports: list[int],
    core_ports: list[int],
    host_concurrency: int,
) -> PortScanPlan:
    """Budget phase 5 from alive hosts x ports.

    Narrows ports before hosts: dropping a port costs one signal on every
    device, while dropping a host costs every signal on one device.
    ``core_ports`` is the AV-critical subset — the universal baseline plus
    the ports declared by drivers that are actually installed.
    """
    projected = project_port_scan(alive, len(ports), host_concurrency)
    allowed = budget.grant(PHASE_PORT, projected)

    if alive <= 0 or not ports or projected <= allowed:
        return PortScanPlan(list(ports), None, host_concurrency, allowed)

    warnings: list[str] = []
    use_ports = list(ports)
    if 0 < len(core_ports) < len(ports):
        dropped = len(ports) - len(core_ports)
        use_ports = list(core_ports)
        projected = project_port_scan(alive, len(use_ports), host_concurrency)
        warnings.append(
            f"Port scan narrowed to the {len(use_ports)} ports your installed "
            f"drivers use; {dropped} ports from the community catalog were "
            "skipped. A device only that catalog knows about may show fewer "
            "open ports."
        )

    if projected <= allowed:
        return PortScanPlan(use_ports, None, host_concurrency, allowed, warnings)

    per_host = project_port_scan_host(len(use_ports))
    host_limit = max(1, int(allowed / max(per_host, 0.001) * host_concurrency))
    if host_limit >= alive:
        return PortScanPlan(use_ports, None, host_concurrency, allowed, warnings)
    warnings.append(
        f"Port scan covered {host_limit:,} of {alive:,} devices — "
        f"{alive - host_limit:,} were skipped. Those devices still carry their "
        "MAC, vendor, and any announcement they made, but no open-port signal."
    )
    return PortScanPlan(use_ports, host_limit, host_concurrency, allowed, warnings)


def plan_passive_collect(budget: ScanBudget) -> PassiveCollectPlan:
    """Budget phase 7's collection half — a fixed wait, so it narrows by
    shortening rather than by dropping work."""
    want = budget.policy.passive_collect_seconds
    projected = project_passive_collect(want)
    allowed = budget.grant(PHASE_PASSIVE, projected)

    trailing = SNMP_COLLECT_SECONDS + PASSIVE_FOLLOWUP_SECONDS
    wait = min(want, max(0.0, allowed - trailing))
    if wait <= 0.0:
        # Not even the work that trails the wait fits. Still listen briefly:
        # announcements already in flight are the cheapest identification a
        # scan can get. Half the grant, so the wait can never outlast the
        # deadline it is being run under.
        wait = min(want, allowed * 0.5)
    if wait >= want - 0.05:
        return PassiveCollectPlan(want, allowed)
    return PassiveCollectPlan(
        wait,
        allowed,
        [
            f"Listened {wait:.0f}s for device announcements instead of "
            f"{want:.0f}s, to leave time for driver probes. A device that "
            "announces itself slowly may be missing."
        ],
    )


def plan_probes(
    budget: ScanBudget, tcp_jobs: int, udp_sends: int, companions: int,
) -> ProbePlan:
    """Budget phase 7's probe half from the job count, known once the port
    scan says which hosts have a driver's port open."""
    projected = project_probes(tcp_jobs, udp_sends, companions)
    # Terminal: nothing budgeted follows (the matcher runs outside the
    # deadline entirely), so this phase inherits whatever the earlier ones
    # left rather than being held to its share.
    allowed = budget.grant(PHASE_PROBE, projected, terminal=True)

    if tcp_jobs <= 0 or projected <= allowed:
        return ProbePlan(None, allowed)

    # UDP broadcasts are kept whole: one send covers a whole subnet, so they
    # are the cheapest identification per packet. The TCP probes are per-host
    # and are what actually scales, so they take the cut.
    spare = allowed - udp_sends / PROBE_RATE_PER_SECOND
    if companions > 0:
        # A companion's allowance is its hard timeout, not its expected cost,
        # and it is one fixed block whatever the network size. Reserving all
        # of it zeroed out every per-host probe on a tight budget — measured
        # live: "Ran 0 of 26 driver probes", with the bench switcher dropping
        # from identified to possible. Companions may claim at most half, so
        # the cheaper, more targeted work always keeps a share.
        spare -= min(COMPANION_SECONDS, allowed * 0.5)
    max_jobs = max(0, int(max(0.0, spare) * PROBE_RATE_PER_SECOND))
    if max_jobs >= tcp_jobs:
        return ProbePlan(None, allowed)
    return ProbePlan(
        max_jobs,
        allowed,
        [
            f"Ran {max_jobs:,} of {tcp_jobs:,} driver probes. Devices whose "
            "probe did not run are reported from their other evidence, so "
            "some may show as possible rather than identified."
        ],
    )
