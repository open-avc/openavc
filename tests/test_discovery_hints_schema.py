"""Tests for the Phase 6 ``discovery:`` schema parser + signal-index builder.

Validates that ``parse_driver_discovery`` accepts well-formed blocks,
rejects malformed ones, and that ``build_signal_index`` raises on
strong-signal collisions.
"""

import pytest

from server.discovery.hints import (
    DiscoveryHintError,
    build_signal_index,
    load_discovery_hints,
    parse_driver_discovery,
)
from server.discovery.tier_matcher import (
    KIND_ACTIVE_PROBE,
    KIND_BROADCAST,
    KIND_MDNS,
    KIND_SSDP,
)


def _drv(driver_id: str, **discovery) -> dict:
    return {
        "id": driver_id,
        "name": driver_id.replace("_", " ").title(),
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "tcp",
        "discovery": discovery or {"manual_only": True},
    }


class TestSignalRequirements:
    def test_soft_only_loads_without_error(self):
        # Phase 8 Task 8.3: a driver with only soft signals (and no
        # manual_only flag) used to raise. The new rule: any signal —
        # strong or soft — is enough for the driver to participate in
        # matching, so soft-only drivers load fine.
        h = parse_driver_discovery({
            "id": "lonely_driver",
            "name": "Lonely",
            "discovery": {"oui_prefixes": ["00:11:22"]},
        })
        assert h is not None
        assert h.oui_prefixes == ["00:11:22"]
        assert h.manual_only is False

    def test_no_signals_at_all_warns(self, caplog):
        # A driver with no signals AND no manual_only flag is almost
        # certainly a mistake — log a warning, but don't reject.
        import logging
        with caplog.at_level(logging.WARNING, logger="discovery.hints"):
            h = parse_driver_discovery({
                "id": "ghost_driver",
                "name": "Ghost",
                "discovery": {},
            })
        assert h is not None
        assert "ghost_driver" in caplog.text
        assert "never participate in matching" in caplog.text

    def test_no_signals_with_manual_only_is_silent(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="discovery.hints"):
            h = parse_driver_discovery(_drv("manual_widget", manual_only=True))
        assert h is not None
        assert h.manual_only is True
        assert "never participate in matching" not in caplog.text

    def test_one_strong_signal_satisfies(self):
        h = parse_driver_discovery(_drv(
            "extron_sis_driver", active_probes=["extron_sis"],
        ))
        assert h is not None
        assert h.active_probes == ["extron_sis"]


class TestSchemaParsing:
    def test_mdns_string_or_dict(self):
        h = parse_driver_discovery(_drv(
            "ndi_source",
            mdns_services=[
                "_ndi._tcp.local.",
                {"service": "_http._tcp.local.", "txt_match": {"manufacturer": "QSC"}},
            ],
        ))
        assert h is not None
        assert h.mdns_services[0] == {"service": "_ndi._tcp.local.", "txt_match": {}}
        assert h.mdns_services[1]["txt_match"] == {"manufacturer": "QSC"}

    def test_amx_ddp_required_make(self):
        with pytest.raises(DiscoveryHintError, match="amx_ddp.make"):
            parse_driver_discovery(_drv("polycom_ssc", amx_ddp={"model_pattern": "Sound*"}))

    def test_amx_ddp_default_model_pattern(self):
        h = parse_driver_discovery(_drv("polycom_ssc", amx_ddp={"make": "Polycom"}))
        assert h is not None
        assert h.amx_ddp == {"make": "Polycom", "model_pattern": "*"}

    def test_onvif_bool_or_dict(self):
        h1 = parse_driver_discovery(_drv("axis_camera", onvif=True))
        assert h1 is not None
        assert "onvif" in h1.broadcast_probes
        h2 = parse_driver_discovery(_drv(
            "axis_camera_specific", onvif={"manufacturer": "Axis"},
        ))
        assert h2 is not None
        assert h2.onvif_manufacturer == "Axis"

    def test_unknown_active_probe_raises(self):
        with pytest.raises(DiscoveryHintError, match="unknown Tier 3 active probe"):
            parse_driver_discovery(_drv("bad", active_probes=["http_banner"]))

    def test_unknown_broadcast_probe_via_explicit_dict(self):
        with pytest.raises(DiscoveryHintError, match="onvif must be a bool"):
            parse_driver_discovery(_drv("bad_onvif", onvif="please"))

    def test_snmp_pen_must_be_positive_int(self):
        with pytest.raises(DiscoveryHintError, match="snmp_pen"):
            parse_driver_discovery(_drv(
                "bad_pen",
                snmp_pen="17049",
                active_probes=["extron_sis"],
            ))

    def test_template_drivers_skipped(self):
        h = parse_driver_discovery({"id": "generic_tcp", "discovery": {}})
        assert h is None

    def test_open_ports_accepted(self):
        h = parse_driver_discovery(_drv(
            "qsc_qrc",
            active_probes=["qrc"],
            open_ports=[1710, 4352],
        ))
        assert h is not None
        assert h.open_ports == [1710, 4352]

    def test_open_ports_must_be_list(self):
        with pytest.raises(DiscoveryHintError, match="open_ports must be a list"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["qrc"], open_ports="1710",
            ))

    def test_open_ports_rejects_non_int(self):
        with pytest.raises(DiscoveryHintError, match="must be integers"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["qrc"], open_ports=["1710"],
            ))

    def test_open_ports_rejects_bool(self):
        # Bool is an int subclass — explicitly rejected.
        with pytest.raises(DiscoveryHintError, match="must be integers"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["qrc"], open_ports=[True],
            ))

    def test_open_ports_rejects_out_of_range(self):
        with pytest.raises(DiscoveryHintError, match="out of range"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["qrc"], open_ports=[0],
            ))
        with pytest.raises(DiscoveryHintError, match="out of range"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["qrc"], open_ports=[70000],
            ))

    @pytest.mark.parametrize("port", [22, 80, 443])
    def test_open_ports_rejects_disallowed(self, port):
        with pytest.raises(DiscoveryHintError, match="disallowed"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["qrc"], open_ports=[port],
            ))

    def test_vendor_aliases_accepted(self):
        h = parse_driver_discovery(_drv(
            "sharp_nec_projector",
            active_probes=["pjlink_class1"],
            vendor_aliases=["NEC", "Sharp NEC", "Sharp"],
        ))
        assert h is not None
        # Aliases are normalized to lowercase + stripped at parse time so
        # the matcher's case-insensitive lookup is a plain dict hit.
        assert h.vendor_aliases == ["nec", "sharp nec", "sharp"]

    def test_vendor_aliases_strips_whitespace(self):
        h = parse_driver_discovery(_drv(
            "vendor_widget",
            active_probes=["pjlink_class1"],
            vendor_aliases=["  Sony  "],
        ))
        assert h is not None
        assert h.vendor_aliases == ["sony"]

    def test_vendor_aliases_dedup_case_insensitive(self):
        h = parse_driver_discovery(_drv(
            "vendor_widget",
            active_probes=["pjlink_class1"],
            vendor_aliases=["NEC", "nec", " NEC "],
        ))
        assert h is not None
        assert h.vendor_aliases == ["nec"]

    def test_vendor_aliases_must_be_list(self):
        with pytest.raises(DiscoveryHintError, match="vendor_aliases must be a list"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["pjlink_class1"], vendor_aliases="NEC",
            ))

    def test_vendor_aliases_rejects_non_string(self):
        with pytest.raises(DiscoveryHintError, match="must be strings"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["pjlink_class1"], vendor_aliases=[123],
            ))

    def test_vendor_aliases_rejects_empty_string(self):
        with pytest.raises(DiscoveryHintError, match="non-empty"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["pjlink_class1"], vendor_aliases=[""],
            ))

    def test_vendor_aliases_rejects_whitespace_only(self):
        with pytest.raises(DiscoveryHintError, match="non-empty"):
            parse_driver_discovery(_drv(
                "bad", active_probes=["pjlink_class1"], vendor_aliases=["   "],
            ))

    def test_vendor_aliases_alone_satisfies_signal_requirement(self, caplog):
        # A driver with only vendor_aliases (no strong signal, no other
        # soft signal) must load without warning — vendor_aliases is a
        # legitimate Tier 4 soft signal once Phase 8.6 wires the matcher
        # branch. Pins the has_any_signal contract added in Task 8.6.1.
        import logging
        with caplog.at_level(logging.WARNING, logger="discovery.hints"):
            h = parse_driver_discovery({
                "id": "alias_only_driver",
                "name": "Alias Only",
                "discovery": {"vendor_aliases": ["AcmeCorp"]},
            })
        assert h is not None
        assert h.vendor_aliases == ["acmecorp"]
        assert "never participate in matching" not in caplog.text


class TestSignalIndexBuilder:
    def test_strong_collision_raises(self):
        registry = [
            _drv("a", crestron_cip=True),
            _drv("b", crestron_cip=True),
        ]
        hints = load_discovery_hints(registry)
        with pytest.raises(ValueError, match="Signal collision"):
            build_signal_index(hints)

    def test_oui_collision_allowed(self):
        # Soft signals (Tier 4) are allowed to overlap — produces possible state.
        registry = [
            _drv("dsp_a", active_probes=["tesira_ttp"], oui_prefixes=["78:45:01"]),
            _drv("dsp_b", active_probes=["qrc"], oui_prefixes=["78:45:01"]),
        ]
        hints = load_discovery_hints(registry)
        idx = build_signal_index(hints)
        assert sorted(idx.find_soft_oui("78:45:01:11:22:33")) == ["dsp_a", "dsp_b"]

    def test_mdns_txt_filter_disambiguates(self):
        registry = [
            _drv("shure_p300", mdns_services=[
                {"service": "_http._tcp.local.", "txt_match": {"manufacturer": "Shure"}},
            ]),
            _drv("qsc_core", mdns_services=[
                {"service": "_http._tcp.local.", "txt_match": {"manufacturer": "QSC"}},
            ]),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        rule = idx.find_strong(KIND_MDNS, "_http._tcp.local.", txt={"manufacturer": "QSC"})
        assert rule is not None and rule.driver_id == "qsc_core"

    def test_active_probe_collision_raises(self):
        registry = [
            _drv("a", active_probes=["pjlink_class1"]),
            _drv("b", active_probes=["pjlink_class1"]),
        ]
        with pytest.raises(ValueError, match="Signal collision"):
            build_signal_index(load_discovery_hints(registry))

    def test_manual_only_with_no_signals_registers_nothing(self):
        # A manual_only driver that declares no signals still contributes
        # nothing to the index — but only because there is nothing to
        # register, not because manual_only filters it out.
        registry = [
            _drv("manual_widget", manual_only=True),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        assert idx.driver_count() == 0

    def test_manual_only_soft_signals_register(self):
        # Regression for the Phase 8 Task 8.1 fix: manual_only drivers
        # used to have *all* their signals discarded by build_signal_index,
        # so devices with a known OUI (e.g. Barco ClickShare) fell through
        # to `unknown` even though we had Tier 4 enrichment for them.
        # After the fix, soft signals on manual_only drivers register
        # normally and surface the device as `possible` with the right
        # candidate driver.
        registry = [
            _drv(
                "barco_clickshare_cx",
                manual_only=True,
                oui_prefixes=["00:04:a5"],
            ),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        assert idx.find_soft_oui("00:04:a5:11:22:33") == ["barco_clickshare_cx"]

    def test_onvif_manufacturer_disambiguates(self):
        # Two camera drivers can both opt in to onvif so long as each
        # constrains by manufacturer; the matcher resolves to the right
        # driver from the responder's scope value.
        registry = [
            _drv("axis_camera", onvif={"manufacturer": "Axis"}),
            _drv("sony_camera", onvif={"manufacturer": "Sony"}),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        sony = idx.find_strong(KIND_BROADCAST, "onvif", txt={"manufacturer": "Sony"})
        axis = idx.find_strong(KIND_BROADCAST, "onvif", txt={"manufacturer": "Axis"})
        assert sony is not None and sony.driver_id == "sony_camera"
        assert axis is not None and axis.driver_id == "axis_camera"

    def test_onvif_unfiltered_collision_raises(self):
        # Unfiltered claims still collide.
        registry = [
            _drv("a", onvif=True),
            _drv("b", onvif=True),
        ]
        with pytest.raises(ValueError, match="Signal collision"):
            build_signal_index(load_discovery_hints(registry))

    def test_indexing_covers_all_signal_kinds(self):
        registry = [
            _drv(
                "kitchen_sink",
                mdns_services=["_pjlink._tcp.local."],
                ssdp_device_types=["urn:schemas-upnp-org:device:MediaRenderer:1"],
                amx_ddp={"make": "Polycom", "model_pattern": "Sound*"},
                pjlink_class2=True,
                active_probes=["pjlink_class1"],
                snmp_pen=17049,
                oui_prefixes=["00:05:a6"],
                hostname_patterns=["^kitchen-"],
                open_ports=[1710],
            ),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        assert idx.find_strong(KIND_MDNS, "_pjlink._tcp.local.") is not None
        assert idx.find_strong(KIND_SSDP, "urn:schemas-upnp-org:device:MediaRenderer:1") is not None
        assert idx.find_strong(KIND_BROADCAST, "pjlink_class2") is not None
        assert idx.find_strong(KIND_ACTIVE_PROBE, "pjlink_class1") is not None
        assert idx.find_soft_pen(17049) == ["kitchen_sink"]
        assert idx.find_soft_oui("00:05:a6:aa:bb:cc") == ["kitchen_sink"]
        assert idx.find_soft_hostname("kitchen-pjlink-1") == ["kitchen_sink"]
        assert idx.find_soft_open_port(1710) == ["kitchen_sink"]

    def test_open_port_collision_allowed(self):
        # Soft signals — multiple drivers can claim the same port. Two
        # drivers both watching for open 1710 produce a `possible` state
        # with both as candidates.
        registry = [
            _drv("qsc_qrc", active_probes=["qrc"], open_ports=[1710]),
            _drv("qsc_qsys_external", manual_only=True, open_ports=[1710]),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        assert sorted(idx.find_soft_open_port(1710)) == [
            "qsc_qrc", "qsc_qsys_external",
        ]
