"""Per-phase scan budgets derived from workload.

The defect these cover: the scan ran under one flat stopwatch per depth
while the work scaled with the network, and the phases starved in exactly
the wrong order — the most predictable phase (the ping sweep) went first
and spent everything, the least predictable one (driver probes) went last
and never started. Measured on a /20 at standard depth: 80s of 120s in the
sweep, truncation during the port scan, and AV gear reported as "possible"
because the probes that identify it never ran.

So the invariant under test throughout is not "the numbers are these
numbers" but "a phase cannot spend a later phase's allocation, and when
work is dropped it is named."
"""

from __future__ import annotations

from openavc.discovery.scan_budget import (
    PHASE_ARP,
    PHASE_PING,
    PHASE_PORT,
    PHASE_PROBE,
    PHASE_SHARES,
    BUDGET_KEYS,
    ScanBudget,
    plan_arp_phase,
    plan_passive_collect,
    plan_ping_sweep,
    plan_port_scan,
    plan_probes,
    project_arp_phase,
    project_ping_sweep,
    project_port_scan,
    project_probes,
    resolve_policy,
)


class FakeClock:
    """Monotonic clock the test drives by hand, so a budget can be examined
    at any point in a scan without one really elapsing."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# Addresses in the subnets that matter here: /24 is the common case, /20 is
# the documented maximum (`max_subnet_size` default), minus network and
# broadcast.
HOSTS_24 = 254
HOSTS_20 = 4094


def _budget(depth: str = "standard", gentle: bool = False, total: float | None = None):
    policy = resolve_policy(depth, gentle)
    clock = FakeClock()
    return ScanBudget(policy, total or policy.total_seconds, clock=clock), clock


# --- Depth as a policy, not a stopwatch --------------------------------


class TestDepthPolicy:
    def test_each_depth_selects_thoroughness_not_just_a_duration(self):
        quick = resolve_policy("quick")
        standard = resolve_policy("standard")
        thorough = resolve_policy("thorough")

        # The point of §6: depth decides what work is done, and the time
        # follows from that work rather than being the setting itself.
        assert not quick.netbios and standard.netbios and thorough.netbios
        assert not quick.snmp_entity_mib and standard.snmp_entity_mib
        assert not standard.extended_ports and thorough.extended_ports
        assert (
            quick.passive_collect_seconds
            < standard.passive_collect_seconds
            < thorough.passive_collect_seconds
        )

    def test_deeper_depth_allows_more_wall_clock(self):
        assert (
            resolve_policy("quick").total_seconds
            < resolve_policy("standard").total_seconds
            < resolve_policy("thorough").total_seconds
        )

    def test_unknown_depth_falls_back_to_standard(self):
        # The value reaches here from stored project settings too, not only
        # from a validated request.
        assert resolve_policy("blah").name == "standard"
        assert resolve_policy("").name == "standard"

    def test_gentle_mode_only_lowers_concurrency(self):
        plain = resolve_policy("standard")
        gentle = resolve_policy("standard", gentle=True)

        assert gentle.ping_concurrency < plain.ping_concurrency
        assert gentle.port_host_concurrency < plain.port_host_concurrency
        # Same thoroughness, same ceiling — gentle trades rate for time, and
        # the projections widen the budgets on their own because they read
        # concurrency.
        assert gentle.total_seconds == plain.total_seconds
        assert gentle.netbios == plain.netbios
        assert gentle.extended_ports == plain.extended_ports


class TestShares:
    def test_shares_leave_a_reserve(self):
        # Each phase is capped at a share of the total; the shares must sum
        # to less than the whole so the per-phase deadlines can't collectively
        # outrun the scan's own ceiling.
        assert sum(PHASE_SHARES.values()) < 1.0

    def test_every_budget_key_has_a_share(self):
        assert set(PHASE_SHARES) == set(BUDGET_KEYS)

    def test_the_sweep_is_capped_below_half_the_scan(self):
        # The failure this module exists to prevent, expressed as an
        # invariant: the sweep cannot claim most of the scan, whatever the
        # subnet size.
        assert PHASE_SHARES[PHASE_PING] < 0.5


# --- Projections track the scanners' real costs ------------------------


class TestProjections:
    def test_ping_sweep_scales_with_hosts_over_concurrency(self):
        assert project_ping_sweep(1000, 50) == 20.0
        assert project_ping_sweep(2000, 50) == 40.0
        assert project_ping_sweep(1000, 100) == 10.0
        assert project_ping_sweep(0, 50) == 0.0

    def test_a_20_sweep_costs_most_of_the_old_flat_budget(self):
        # The measurement that motivated this work: 4094 addresses at
        # concurrency 50 is ~82s, and the old standard budget was 120s.
        assert 75.0 < project_ping_sweep(HOSTS_20, 50) < 90.0

    def test_arp_phase_scales_with_alive_not_total(self):
        assert project_arp_phase(0, netbios=True) < project_arp_phase(
            200, netbios=True
        )
        # Reverse DNS (concurrency 20) outlasts NetBIOS (30), so adding
        # NetBIOS costs nothing extra — they overlap.
        assert project_arp_phase(100, netbios=True) == project_arp_phase(
            100, netbios=False
        )

    def test_port_scan_scales_with_alive_times_ports(self):
        one = project_port_scan(10, 50, host_concurrency=10)
        assert project_port_scan(20, 50, host_concurrency=10) > one
        assert project_port_scan(10, 100, host_concurrency=10) > one
        assert project_port_scan(10, 50, host_concurrency=20) < one

    def test_probe_cost_is_set_by_the_send_rate_limit(self):
        # 100 sends at the scan-wide 10/sec limiter is 10s, whatever each
        # probe's own timeout is.
        assert project_probes(tcp_jobs=100, udp_sends=0, companions=0) == 10.0
        assert project_probes(tcp_jobs=0, udp_sends=100, companions=0) == 10.0


# --- Grants ------------------------------------------------------------


class TestGrant:
    def test_a_cheap_phase_gets_only_what_it_projects(self):
        budget, _ = _budget()
        # 12s of work + slack, well under both the floor-beating threshold
        # and the share cap.
        granted = budget.grant(PHASE_PING, 40.0)
        assert 40.0 < granted <= 40.0 * budget.policy.slack + 0.01

    def test_an_expensive_phase_is_capped_at_its_share(self):
        budget, _ = _budget()
        granted = budget.grant(PHASE_PING, 10_000.0)
        assert granted == PHASE_SHARES[PHASE_PING] * budget.total_seconds

    def test_a_near_zero_projection_still_gets_its_floor(self):
        budget, _ = _budget()
        assert budget.grant(PHASE_ARP, 0.0) > 0.0

    def test_a_grant_never_exceeds_the_time_actually_left(self):
        budget, clock = _budget(total=100.0)
        clock.advance(95.0)
        assert budget.grant(PHASE_PORT, 10_000.0) <= 5.0

    def test_the_last_phase_may_use_what_earlier_phases_left(self):
        # A share stops a phase spending a *later* phase's allocation. The
        # last budgeted phase has no later phase, so holding it to its share
        # only wastes time nothing else will use — measured live as probes
        # cut at 5.4s of a 45s scan that then finished in 32s.
        budget, _ = _budget(total=100.0)
        share_capped = budget.grant(PHASE_PORT, 10_000.0)
        budget_two, _ = _budget(total=100.0)
        terminal = budget_two.grant(PHASE_PROBE, 10_000.0, terminal=True)
        assert share_capped == PHASE_SHARES[PHASE_PORT] * 100.0
        assert terminal > PHASE_SHARES[PHASE_PROBE] * 100.0

    def test_a_terminal_grant_is_still_bounded_by_its_projection(self):
        budget, _ = _budget(total=300.0)
        granted = budget.grant(PHASE_PROBE, 4.0, terminal=True)
        # Inheriting the remainder must not mean idling it away.
        assert granted < 300.0

    def test_grants_are_recorded_for_the_status_payload(self):
        budget, _ = _budget()
        budget.grant(PHASE_PING, 30.0)
        budget.grant(PHASE_PROBE, 5.0)
        assert set(budget.grants) == {PHASE_PING, PHASE_PROBE}
        assert budget.granted_total == sum(budget.grants.values())


# --- The regression: terminal phases are budgeted first ----------------


class TestTerminalPhasesSurvive:
    def test_a_standard_20_scan_leaves_the_probe_phase_its_budget(self):
        """The measured failure, as an assertion.

        A /20 at standard depth: the sweep still covers all 4094 addresses,
        and after it has run the probe phase — the one that was never
        reached before — still gets a real budget.
        """
        budget, clock = _budget("standard")

        ping = plan_ping_sweep(budget, HOSTS_20, gentle=False)
        assert ping.max_hosts is None, "the whole /20 should still be swept"
        assert ping.warnings == []
        clock.advance(project_ping_sweep(HOSTS_20, ping.concurrency))

        # 30 alive hosts is the order of magnitude a real /20 AV network
        # returns (24-30 on the bench /22 that produced the incident).
        arp = plan_arp_phase(budget, 30, netbios=True)
        clock.advance(project_arp_phase(30, netbios=True))
        ports = list(range(1, 61))
        port = plan_port_scan(budget, 30, ports, ports, budget.policy.port_host_concurrency)
        clock.advance(project_port_scan(30, len(ports), port.concurrency))
        collect = plan_passive_collect(budget)
        clock.advance(collect.wait_seconds + 15.0)
        probe = plan_probes(budget, tcp_jobs=40, udp_sends=6, companions=0)

        assert arp.warnings == [] and port.warnings == []
        assert collect.warnings == [] and probe.warnings == []
        assert probe.max_tcp_jobs is None
        assert probe.budget > 0
        # And the whole thing still fits inside the depth's ceiling.
        assert budget.elapsed < budget.total_seconds

    def test_the_sweep_cannot_consume_the_scan_however_big_the_subnet(self):
        budget, _ = _budget("standard")
        # A /16: 16x the documented maximum, reachable by raising
        # max_subnet_size.
        plan_ping_sweep(budget, 65_534, gentle=False)
        remaining_share = sum(
            PHASE_SHARES[k] for k in BUDGET_KEYS if k != PHASE_PING
        )
        assert budget.grants[PHASE_PING] <= budget.total_seconds * PHASE_SHARES[PHASE_PING]
        assert remaining_share * budget.total_seconds > 0


# --- Narrowing ladders -------------------------------------------------


class TestPingNarrowing:
    def test_a_24_needs_no_narrowing(self):
        budget, _ = _budget()
        plan = plan_ping_sweep(budget, HOSTS_24, gentle=False)
        assert plan.max_hosts is None
        assert plan.concurrency == budget.policy.ping_concurrency
        assert plan.warnings == []

    def test_sweeps_faster_before_it_sweeps_less(self):
        budget, _ = _budget("quick")
        plan = plan_ping_sweep(budget, HOSTS_20, gentle=False)
        # Quick's ceiling can't afford a /20 at the base rate, so it raises
        # the rate rather than dropping addresses: a ping is one packet per
        # host, so more of them in flight costs burst rate, not coverage.
        assert plan.concurrency > budget.policy.ping_concurrency
        assert plan.max_hosts is None
        assert plan.warnings == []

    def test_gentle_mode_never_escalates_the_rate(self):
        budget, _ = _budget("quick", gentle=True)
        plan = plan_ping_sweep(budget, HOSTS_20, gentle=True)
        assert plan.concurrency == budget.policy.ping_concurrency

    def test_an_unswepable_range_is_capped_and_the_cap_is_named(self):
        budget, _ = _budget("quick", gentle=True)
        plan = plan_ping_sweep(budget, 200_000, gentle=True)
        assert plan.max_hosts is not None
        assert plan.max_hosts < 200_000
        assert plan.warnings, "a dropped address range must never be silent"
        assert "200,000" in plan.warnings[0]


class TestArpNarrowing:
    def test_no_narrowing_for_a_normal_alive_count(self):
        budget, _ = _budget()
        plan = plan_arp_phase(budget, 30, netbios=True)
        assert plan.name_lookup_hosts is None
        assert plan.warnings == []

    def test_bounds_hostname_lookups_rather_than_dropping_them(self):
        budget, clock = _budget(total=60.0)
        clock.advance(40.0)  # leave very little
        plan = plan_arp_phase(budget, 5000, netbios=True)
        # Bounded, not zero: hostnames feed discovery.hostname driver hints,
        # so they are identification signal, not decoration.
        assert plan.name_lookup_hosts is not None
        assert plan.name_lookup_hosts < 5000
        assert plan.warnings
        # The ARP read itself is never dropped — the warning must say so,
        # because MAC/vendor is the phase's strongest signal.
        assert "MAC" in plan.warnings[0]


class TestPortScanNarrowing:
    def test_no_narrowing_when_the_work_fits(self):
        budget, _ = _budget()
        ports = list(range(1, 41))
        plan = plan_port_scan(budget, 20, ports, ports[:10], host_concurrency=16)
        assert plan.ports == ports
        assert plan.host_limit is None
        assert plan.warnings == []

    def test_drops_catalog_ports_before_it_drops_hosts(self):
        budget, _ = _budget()
        core = list(range(1, 21))
        full = list(range(1, 400))
        plan = plan_port_scan(budget, 400, full, core, host_concurrency=16)
        # A port costs one signal on every device; a host costs every signal
        # on one device. So ports go first.
        assert plan.ports == core
        assert plan.warnings
        assert "installed drivers" in plan.warnings[0]

    def test_caps_hosts_only_when_the_narrow_port_set_still_will_not_fit(self):
        budget, _ = _budget("quick")
        core = list(range(1, 21))
        plan = plan_port_scan(budget, 20_000, core, core, host_concurrency=16)
        assert plan.host_limit is not None
        assert plan.host_limit < 20_000
        assert any("20,000 devices" in w for w in plan.warnings)


class TestPassiveCollectNarrowing:
    def test_full_window_when_there_is_room(self):
        budget, _ = _budget("standard")
        plan = plan_passive_collect(budget)
        assert plan.wait_seconds == budget.policy.passive_collect_seconds
        assert plan.warnings == []

    def test_shortens_the_listen_rather_than_skipping_it(self):
        budget, clock = _budget("thorough", total=60.0)
        clock.advance(50.0)
        plan = plan_passive_collect(budget)
        # Announcements are the strongest signal a scan collects, so this
        # phase narrows by waiting less — never by not listening.
        assert 0 < plan.wait_seconds < budget.policy.passive_collect_seconds
        assert plan.warnings


class TestProbeNarrowing:
    def test_no_cap_when_the_probes_fit(self):
        budget, _ = _budget()
        plan = plan_probes(budget, tcp_jobs=40, udp_sends=6, companions=0)
        assert plan.max_tcp_jobs is None
        assert plan.warnings == []

    def test_caps_per_host_probes_and_names_the_shortfall(self):
        budget, _ = _budget()
        plan = plan_probes(budget, tcp_jobs=100_000, udp_sends=4, companions=0)
        assert plan.max_tcp_jobs is not None
        assert plan.max_tcp_jobs < 100_000
        assert plan.warnings
        assert "100,000" in plan.warnings[0]

    def test_broadcast_probes_are_kept_when_per_host_probes_are_cut(self):
        # One UDP send covers a whole subnet, so it is the cheapest
        # identification per packet — the per-host TCP probes take the cut.
        budget, _ = _budget()
        plan = plan_probes(budget, tcp_jobs=100_000, udp_sends=4, companions=0)
        udp_cost = 4 / 10.0
        assert plan.budget > udp_cost

    def test_a_companion_cannot_crowd_out_every_per_host_probe(self):
        """Measured live on a 45s /20: the companion's fixed 10s worst-case
        allowance consumed a 5.4s probe budget whole, so the plan ran 0 of 26
        TCP probes and the bench switcher fell from identified to possible.

        A companion's allowance is a timeout, not an expected cost. It may
        claim at most half the phase, so the cheaper targeted probes survive.
        """
        budget, clock = _budget(total=45.0)
        clock.advance(39.0)  # what was left when the phase was planned live
        plan = plan_probes(budget, tcp_jobs=26, udp_sends=1, companions=1)
        assert plan.max_tcp_jobs is None or plan.max_tcp_jobs > 0
