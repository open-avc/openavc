"""Which drivers a device's signals point at, and one driver's declared
signals judged against what a device showed.

``explain_matches`` lists every driver each signal points at and picks no
winner. ``evaluate_driver_signals`` says of each declared signal whether the
device matched it, did not (and what it said instead), or was never seen
doing that kind of thing. Both ask the matcher's own lookups, so they agree
with a scan.
"""

from __future__ import annotations

import asyncio

from openavc.discovery.explain import (
    MATCHED,
    NOT_MATCHED,
    NOT_OBSERVED,
    DeviceObservations,
    evaluate_driver_signals,
    explain_matches,
)
from openavc.discovery.hints import build_signal_index, parse_driver_discovery
from openavc.discovery.port_scanner import PORT_FILTERED, PORT_OPEN, PORT_REFUSED
from openavc.discovery.probe_runner import (
    MISS_FOLLOW_UP,
    ProbeObservation,
    RateLimiter,
    observe_tcp_active_probe,
    observe_udp_probe,
)
from openavc.discovery.result import DeviceState
from openavc.discovery.tier_matcher import (
    SignalIndex,
    SignalRule,
    TierMatcher,
    evidence_active_probe,
    evidence_amx_ddp,
    evidence_hostname,
    evidence_mdns,
    evidence_open_port,
    evidence_oui,
    evidence_snmp_pen,
)


def _hint(driver_id: str, **discovery):
    return parse_driver_discovery({
        "id": driver_id,
        "name": driver_id.replace("_", " ").title(),
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "tcp",
        "discovery": discovery,
    })


def _by_kind(checks, kind):
    found = [c for c in checks if c.kind == kind]
    assert len(found) == 1, [c.kind for c in checks]
    return found[0]


async def _tcp_server(replies: list[bytes]):
    """Loopback TCP server: after each read, send the next reply."""
    async def handle(reader, writer):
        for reply in replies:
            try:
                await asyncio.wait_for(reader.read(1024), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            writer.write(reply)
            await writer.drain()
        await asyncio.sleep(0.3)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


# ---------------------------------------------------------------------------
# explain_matches
# ---------------------------------------------------------------------------


class TestExplainMatches:
    def test_two_drivers_fingerprinting_one_device_are_both_listed(self):
        # Two drivers whose TCP probes both matched the same device: the
        # matcher offers the first in the log; explain lists both, in either
        # log order.
        index = build_signal_index([
            _hint("acme_widget", tcp_probe={"port": 5000, "send_ascii": "ID?\r", "expect": "ACME"}),
            _hint("bolt_panel", tcp_probe={"port": 5000, "send_ascii": "ID?\r", "expect": "ACME"}),
        ])
        a = evidence_active_probe("custom_acme_widget_tcp", {"text": "ACME 3000"}, port=5000)
        b = evidence_active_probe("custom_bolt_panel_tcp", {"text": "ACME 3000"}, port=5000)
        matcher = TierMatcher(index=index)
        for log in ([a, b], [b, a]):
            verdict = matcher.match(log)
            explained = explain_matches(log, index)
            assert set(explained.strong_drivers()) == {"acme_widget", "bolt_panel"}
            # The matcher's pick is the first driver explain lists.
            assert verdict.driver_id == explained.strong_drivers()[0]

    def test_a_shared_service_two_filters_both_satisfied(self):
        index = SignalIndex()
        index.add_rule(SignalRule.for_mdns("acme_widget", "_http._tcp", {"model": "W3000"}))
        index.add_rule(SignalRule.for_mdns(
            "acme_widget_pro", "_http._tcp", {"model": "W3000", "vendor": "acme"},
        ))
        ev = evidence_mdns("_http._tcp", {"model": "W3000", "vendor": "acme"})
        rules = index.find_strong_all("mdns", "_http._tcp.", ev.data["txt"])
        assert [r.driver_id for r in rules] == ["acme_widget_pro", "acme_widget"]
        # find_strong keeps returning the most specific, as the matcher uses it.
        assert index.find_strong("mdns", "_http._tcp.", ev.data["txt"]).driver_id == "acme_widget_pro"
        explained = explain_matches([ev], index)
        assert explained.signals[0].drivers == ["acme_widget_pro", "acme_widget"]

    def test_two_amx_globs_both_match(self):
        index = SignalIndex()
        index.add_rule(SignalRule.for_amx_ddp("acme_widget", "Acme", "Widget*"))
        index.add_rule(SignalRule.for_amx_ddp("acme_widget_3k", "Acme", "Widget3*"))
        ev = evidence_amx_ddp("Acme", "Widget3000")
        explained = explain_matches([ev], index)
        assert explained.signals[0].drivers == ["acme_widget_3k", "acme_widget"]

    def test_strong_first_then_soft_and_unclaimed_signals_kept(self):
        index = build_signal_index([
            _hint("acme_widget", mdns=["_acme-ctl._tcp"], oui=["00:11:22"]),
            _hint("acme_panel", oui=["00:11:22"], port_open=[4352]),
        ])
        log = [
            evidence_oui("00:11:22:33:44:55"),
            evidence_mdns("_http._tcp"),        # nobody claims it
            evidence_mdns("_acme-ctl._tcp"),
            evidence_open_port(4352),
        ]
        explained = explain_matches(log, index)
        drivers = explained.drivers()
        assert list(drivers) == ["acme_widget", "acme_panel"]
        assert drivers["acme_widget"] == ["mdns:_acme-ctl._tcp.", "oui:00:11:22"]
        assert drivers["acme_panel"] == ["oui:00:11:22", "open_port:4352"]
        assert explained.strong_drivers() == ["acme_widget"]
        assert [s.source for s in explained.unclaimed()] == ["mdns:_http._tcp."]

    def test_a_repeated_host_name_is_listed_once(self):
        # A scan records one host-name record per matching pattern.
        index = build_signal_index([
            _hint("acme_widget", hostname=["^widget-"]),
            _hint("acme_panel", hostname=["-3000$"]),
        ])
        log = [
            evidence_hostname("widget-3000", matched_pattern="^widget-"),
            evidence_hostname("widget-3000", matched_pattern="-3000$"),
        ]
        explained = explain_matches(log, index)
        assert len(explained.signals) == 1
        assert explained.signals[0].source == "hostname:widget-3000"
        assert explained.signals[0].drivers == ["acme_widget", "acme_panel"]

    def test_soft_only_device_agrees_with_the_possible_verdict(self):
        index = build_signal_index([
            _hint("acme_widget", snmp_pen=99999),
            _hint("acme_panel", snmp_pen=99999),
        ])
        log = [evidence_snmp_pen(99999)]
        verdict = TierMatcher(index=index).match(log)
        assert verdict.state == DeviceState.POSSIBLE
        explained = explain_matches(log, index)
        assert explained.strong_drivers() == []
        assert set(explained.drivers()) == set(verdict.candidates)

    def test_to_dict_is_plain_data(self):
        index = build_signal_index([_hint("acme_widget", mdns=["_acme-ctl._tcp"])])
        view = explain_matches([evidence_mdns("_acme-ctl._tcp")], index).to_dict()
        assert view["strong_drivers"] == ["acme_widget"]
        assert view["signals"][0]["tier"] == "passive_listener"
        assert view["drivers"] == {"acme_widget": ["mdns:_acme-ctl._tcp."]}


# ---------------------------------------------------------------------------
# evaluate_driver_signals
# ---------------------------------------------------------------------------


class TestPassiveFingerprints:
    HINT = _hint("acme_widget", mdns=[{"service": "_http._tcp", "txt": {"model": "W3000"}}])

    def test_matched(self):
        seen = DeviceObservations(evidence=[evidence_mdns("_http._tcp", {"model": "W3000"})])
        check = _by_kind(evaluate_driver_signals(self.HINT, seen), "mdns")
        assert check.status == MATCHED and check.strong

    def test_the_right_service_with_the_wrong_txt_says_what_it_carried(self):
        seen = DeviceObservations(evidence=[
            evidence_mdns("_http._tcp", {"model": "W2000"}),
            evidence_mdns("_printer._tcp"),
        ])
        check = _by_kind(evaluate_driver_signals(self.HINT, seen), "mdns")
        assert check.status == NOT_MATCHED
        assert check.observed == ["_http._tcp. (model=W2000)", "_printer._tcp. (no model)"]
        assert "model=W3000" in check.detail

    def test_no_mdns_at_all_is_not_observed(self):
        seen = DeviceObservations(evidence=[evidence_oui("00:11:22:33:44:55")])
        check = _by_kind(evaluate_driver_signals(self.HINT, seen), "mdns")
        assert check.status == NOT_OBSERVED and check.observed == []

    def test_amx_beacon(self):
        hint = _hint("acme_widget", amx_ddp=[{"make": "Acme", "model_pattern": "Widget*"}])
        hit = evaluate_driver_signals(hint, DeviceObservations(evidence=[evidence_amx_ddp("Acme", "Widget3000")]))
        miss = evaluate_driver_signals(hint, DeviceObservations(evidence=[evidence_amx_ddp("Acme", "Panel10")]))
        assert hit[0].status == MATCHED
        assert miss[0].status == NOT_MATCHED and miss[0].observed == ["Acme/Panel10"]


class TestProbes:
    async def test_a_miss_says_what_was_sent_expected_and_replied(self):
        hint_block = {"port": 0, "send_ascii": "ID?\r", "expect_regex": "^ACME", "timeout_ms": 1500}
        server, port = await _tcp_server([b"HELLO FROM SOMETHING ELSE\r\n"])
        hint = _hint("acme_widget", tcp_probe={**hint_block, "port": port})
        async with server:
            obs = await observe_tcp_active_probe(hint.tcp_probe, target="127.0.0.1", source_ip="")
        check = _by_kind(
            evaluate_driver_signals(hint, DeviceObservations(probes=[obs], port_states={port: PORT_OPEN})),
            "probe",
        )
        assert check.status == NOT_MATCHED
        assert check.detail == (
            f'Sent "ID?\\r" to TCP port {port}, expected regex:^ACME; '
            'the device replied "HELLO FROM SOMETHING ELSE\\r\\n".'
        )
        assert check.probes == [obs]
        assert check.to_dict()["probes"][0]["reply"]["text"].startswith("HELLO")

    async def test_a_match(self):
        server, port = await _tcp_server([b"ACME WIDGET 3000\r\n"])
        hint = _hint("acme_widget", tcp_probe={
            "port": port, "send_ascii": "ID?\r", "expect_regex": "^ACME", "timeout_ms": 1500,
        })
        async with server:
            obs = await observe_tcp_active_probe(hint.tcp_probe, target="127.0.0.1", source_ip="")
        check = evaluate_driver_signals(hint, DeviceObservations(probes=[obs]))[0]
        assert check.status == MATCHED
        assert check.observed == ['"ACME WIDGET 3000\\r\\n"']

    async def test_udp_silence_is_a_miss_not_a_blind_spot(self):
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0),
        )
        port = transport.get_extra_info("sockname")[1]
        hint = _hint("acme_widget", udp_probe={
            "port": port, "send_hex": "0001", "expect_regex": "^ACME", "timeout_ms": 300,
        })
        try:
            observations = await observe_udp_probe(
                hint.udp_probe, targets=["127.0.0.1"], source_ip="127.0.0.1",
                rate_limiter=RateLimiter(100.0),
            )
        finally:
            transport.close()
        check = evaluate_driver_signals(hint, DeviceObservations(probes=observations))[0]
        assert check.kind == "broadcast"
        assert check.status == NOT_MATCHED
        assert check.detail == f"Sent hex 00 01 to UDP port {port}; the device did not reply."

    def test_a_closed_port_is_a_miss_and_an_unscanned_one_is_not_observed(self):
        hint = _hint("acme_widget", tcp_probe={"port": 5000, "send_ascii": "ID?\r", "expect": "ACME"})
        refused = evaluate_driver_signals(hint, DeviceObservations(port_states={5000: PORT_REFUSED}))[0]
        assert refused.status == NOT_MATCHED
        assert refused.detail == "TCP port 5000 was refused, so the probe was not sent."
        unscanned = evaluate_driver_signals(hint, DeviceObservations())[0]
        assert unscanned.status == NOT_OBSERVED
        assert unscanned.detail == "The TCP probe on port 5000 was not sent."

    def test_a_failed_then_step_names_both_halves(self):
        hint = _hint("acme_widget", tcp_probe={
            "port": 5000, "send_ascii": "ID?\r", "expect": "ACME",
            "then": {"send_ascii": "BIG?\r", "expect_silence": True, "timeout_ms": 500},
        })
        obs = ProbeObservation(
            probe_id="custom_acme_widget_tcp", kind="tcp", port=5000, target="192.0.2.10",
            sent=b"ID?\r", reply=b"ACME\r\n", follow_up_sent=b"BIG?\r",
            follow_up_reply=b"OK\r\n", follow_up_ok=False, miss=MISS_FOLLOW_UP,
        )
        check = evaluate_driver_signals(hint, DeviceObservations(probes=[obs]))[0]
        assert check.status == NOT_MATCHED
        assert check.declared.endswith('then send "BIG?\\r", expect silence')
        assert check.detail == (
            'Sent "ID?\\r" to TCP port 5000 and the first reply matched; then sent '
            '"BIG?\\r", expected silence, and the device replied "OK\\r\\n".'
        )

    def test_a_scan_match_without_an_exchange_still_counts(self):
        hint = _hint("acme_widget", tcp_probe={"port": 5000, "send_ascii": "ID?\r", "expect": "ACME"})
        ev = evidence_active_probe("custom_acme_widget_tcp", {"text": "ACME 3000"}, port=5000)
        check = evaluate_driver_signals(hint, DeviceObservations(evidence=[ev]))[0]
        assert check.status == MATCHED

    def test_the_companion_is_one_check(self):
        hint = _hint("acme_widget", python="acme_widget_discovery.py")
        [check] = evaluate_driver_signals(hint, DeviceObservations())
        assert check.kind == "companion" and check.status == NOT_OBSERVED
        ev = evidence_active_probe("custom_acme_widget_companion_tcp", {"model": "W3000"})
        [check] = evaluate_driver_signals(hint, DeviceObservations(evidence=[ev]))
        assert check.status == MATCHED


class TestHints:
    def test_open_port_three_ways(self):
        hint = _hint("acme_widget", port_open=[4352])
        opened = evaluate_driver_signals(hint, DeviceObservations(port_states={4352: PORT_OPEN}))[0]
        filtered = evaluate_driver_signals(hint, DeviceObservations(port_states={4352: PORT_FILTERED}))[0]
        unscanned = evaluate_driver_signals(hint, DeviceObservations(port_states={23: PORT_OPEN}))[0]
        assert opened.status == MATCHED
        assert filtered.status == NOT_MATCHED and filtered.detail == "TCP port 4352 was filtered."
        assert unscanned.status == NOT_OBSERVED

    def test_manufacturer_name_is_lifted_from_a_probe_reply(self):
        # A scan derives manufacturer strings in its last phase; the check
        # derives them the same way from the evidence it is handed.
        hint = _hint("acme_widget", manufacturer_alias=["Acme Corp"])
        ev = evidence_active_probe("custom_other_tcp", {"text": "x", "manufacturer": "ACME CORP"})
        check = evaluate_driver_signals(hint, DeviceObservations(evidence=[ev]))[0]
        assert check.status == MATCHED and check.observed == ["ACME CORP"]
        none = evaluate_driver_signals(hint, DeviceObservations())[0]
        assert none.status == NOT_OBSERVED

    def test_oui_pen_and_hostname(self):
        hint = _hint("acme_widget", oui=["00:11:22"], snmp_pen=99999, hostname=["^widget-"])
        seen = DeviceObservations(evidence=[
            evidence_oui("AA:BB:CC:00:00:01", vendor="Other Co"),
            evidence_snmp_pen(99999),
            evidence_hostname("widget-3000"),
        ])
        checks = evaluate_driver_signals(hint, seen)
        oui = _by_kind(checks, "oui")
        assert oui.status == NOT_MATCHED and oui.observed == ["aa:bb:cc (Other Co)"]
        assert _by_kind(checks, "snmp_pen").status == MATCHED
        assert _by_kind(checks, "hostname").status == MATCHED
        assert not any(c.strong for c in checks)

    def test_every_declaration_gets_one_check_in_rule_order(self):
        hint = _hint(
            "acme_widget",
            mdns=["_acme-ctl._tcp"],
            tcp_probe={"port": 5000, "send_ascii": "ID?\r", "expect": "ACME", "cross_vendor": True},
            snmp_pen=99999, oui=["00:11:22"], port_open=[4352],
        )
        checks = evaluate_driver_signals(hint, DeviceObservations())
        assert [c.kind for c in checks] == ["mdns", "probe", "snmp_pen", "oui", "open_port"]
        assert _by_kind(checks, "probe").cross_vendor is True
        assert all(c.status == NOT_OBSERVED for c in checks)
