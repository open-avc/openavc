"""Tests for the new-schema ``discovery:`` parser + signal-index builder.

Validates that ``parse_driver_discovery`` accepts well-formed blocks
in the new fingerprints + hints shape, rejects malformed ones, and
that ``build_signal_index`` raises on fingerprint collisions.

Schema reference: ``OpenAVC-Discovery-Spec.md`` §2 (workspace root).
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
    KIND_AMX_DDP,
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
        "discovery": discovery,
    }


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


class TestMdnsFingerprint:
    def test_string_form(self):
        h = parse_driver_discovery(_drv("widget", mdns="_widget._tcp.local."))
        assert h is not None
        assert len(h.mdns) == 1
        assert h.mdns[0].service == "_widget._tcp.local."
        assert h.mdns[0].txt == ()
        assert h.mdns[0].cross_vendor is False

    def test_normalizes_to_trailing_dot_and_lowercase(self):
        h = parse_driver_discovery(_drv("widget", mdns="_Widget._TCP.Local"))
        assert h is not None
        assert h.mdns[0].service == "_widget._tcp.local."

    def test_mapping_form(self):
        h = parse_driver_discovery(_drv("widget", mdns={
            "service": "_http._tcp.local.",
            "txt": {"manufacturer": "AcmeCorp"},
            "cross_vendor": True,
        }))
        assert h is not None
        assert h.mdns[0].service == "_http._tcp.local."
        assert dict(h.mdns[0].txt) == {"manufacturer": "AcmeCorp"}
        assert h.mdns[0].cross_vendor is True

    def test_list_of_mixed_entries(self):
        h = parse_driver_discovery(_drv("widget", mdns=[
            "_widget._tcp.local.",
            {"service": "_http._tcp.local.", "txt": {"manufacturer": "AcmeCorp"}},
        ]))
        assert h is not None
        assert len(h.mdns) == 2
        assert h.mdns[0].service == "_widget._tcp.local."
        assert h.mdns[1].txt == (("manufacturer", "AcmeCorp"),)

    def test_mapping_requires_service(self):
        with pytest.raises(DiscoveryHintError, match="mdns.service"):
            parse_driver_discovery(_drv("bad", mdns={"txt": {"manufacturer": "X"}}))

    def test_txt_must_be_mapping(self):
        with pytest.raises(DiscoveryHintError, match="mdns.txt"):
            parse_driver_discovery(_drv("bad", mdns={
                "service": "_x._tcp.local.", "txt": "manufacturer=AcmeCorp",
            }))


class TestSsdpFingerprint:
    def test_string_form(self):
        h = parse_driver_discovery(_drv(
            "widget",
            ssdp="urn:schemas-upnp-org:device:MediaRenderer:1",
        ))
        assert h is not None
        assert len(h.ssdp) == 1
        assert h.ssdp[0].device_type == "urn:schemas-upnp-org:device:MediaRenderer:1"
        assert h.ssdp[0].cross_vendor is False

    def test_mapping_form(self):
        h = parse_driver_discovery(_drv("widget", ssdp={
            "device_type": "urn:schemas-upnp-org:device:Basic:1",
            "cross_vendor": True,
        }))
        assert h is not None
        assert h.ssdp[0].cross_vendor is True

    def test_list_of_strings(self):
        h = parse_driver_discovery(_drv("widget", ssdp=[
            "urn:foo:device:Bar:1",
            "urn:foo:device:Baz:1",
        ]))
        assert h is not None
        assert len(h.ssdp) == 2

    def test_mapping_requires_device_type(self):
        with pytest.raises(DiscoveryHintError, match="ssdp.device_type"):
            parse_driver_discovery(_drv("bad", ssdp={"cross_vendor": True}))

    def test_device_description_filters_parsed(self):
        h = parse_driver_discovery(_drv("widget", ssdp={
            "device_type": "urn:foo:device:AcmeFamily:1",
            "model": "Widget-6a",
            "manufacturer": "AcmeCorp",
        }))
        assert h is not None
        assert h.ssdp[0].fields == (
            ("manufacturer", "AcmeCorp"), ("model", "Widget-6a"),
        )

    def test_filter_value_must_be_string(self):
        with pytest.raises(DiscoveryHintError, match="ssdp.model"):
            parse_driver_discovery(_drv("bad", ssdp={
                "device_type": "urn:foo:device:AcmeFamily:1",
                "model": 604,
            }))


class TestAmxDdpFingerprint:
    def test_basic_mapping(self):
        h = parse_driver_discovery(_drv(
            "widget",
            amx_ddp={"make": "AcmeCorp", "model_pattern": "Widget*"},
        ))
        assert h is not None
        assert len(h.amx_ddp) == 1
        assert h.amx_ddp[0].make == "AcmeCorp"
        assert h.amx_ddp[0].model_pattern == "Widget*"

    def test_default_model_pattern_star(self):
        h = parse_driver_discovery(_drv("widget", amx_ddp={"make": "AcmeCorp"}))
        assert h is not None
        assert h.amx_ddp[0].model_pattern == "*"

    def test_make_required(self):
        with pytest.raises(DiscoveryHintError, match="amx_ddp.make"):
            parse_driver_discovery(_drv("bad", amx_ddp={"model_pattern": "X*"}))

    def test_list_form(self):
        h = parse_driver_discovery(_drv("widget", amx_ddp=[
            {"make": "AcmeCorp", "model_pattern": "Widget*"},
            {"make": "OtherCorp"},
        ]))
        assert h is not None
        assert len(h.amx_ddp) == 2


class TestTcpProbe:
    def _basic(self, **overrides):
        block = {
            "port": 12345,
            "send_ascii": "QUERY\r",
            "expect": "RESPONSE_PREFIX",
        }
        block.update(overrides)
        return _drv("widget", tcp_probe=block)

    def test_basic(self):
        h = parse_driver_discovery(self._basic())
        assert h is not None
        spec = h.tcp_probe
        assert spec is not None
        assert spec.kind == "tcp"
        assert spec.port == 12345
        assert spec.send == b"QUERY\r"
        assert spec.response_match.contains == "RESPONSE_PREFIX"
        assert spec.timeout_ms == 3000  # default
        assert spec.cross_vendor is False
        assert spec.probe_id == "custom_widget_tcp"

    def test_send_hex(self):
        h = parse_driver_discovery(self._basic(send_ascii=None, send_hex="AA55BB"))
        assert h is not None
        assert h.tcp_probe.send == bytes.fromhex("AA55BB")

    def test_expect_regex(self):
        h = parse_driver_discovery(self._basic(expect=None, expect_regex=r"^Hello (\w+)$"))
        assert h is not None
        assert h.tcp_probe.response_match.regex.pattern == r"^Hello (\w+)$"

    def test_expect_hex_prefix(self):
        h = parse_driver_discovery(self._basic(expect=None, expect_hex="AA55"))
        assert h is not None
        assert h.tcp_probe.response_match.starts_with == bytes.fromhex("AA55")

    def test_cross_vendor_flag(self):
        h = parse_driver_discovery(self._basic(cross_vendor=True))
        assert h is not None
        assert h.tcp_probe.cross_vendor is True

    def test_extract_manufacturer_sugar(self):
        h = parse_driver_discovery(self._basic(extract_manufacturer="AcmeCorp"))
        assert h is not None
        rules = {r.field_name: r for r in h.tcp_probe.extract}
        assert rules["manufacturer"].value == "AcmeCorp"

    def test_extract_dynamic_capture(self):
        h = parse_driver_discovery(self._basic(extract={
            "model": {"regex": r"model=(\S+)", "group": 1},
        }))
        assert h is not None
        rules = {r.field_name: r for r in h.tcp_probe.extract}
        assert rules["model"].regex.pattern == r"model=(\S+)"

    def test_extract_manufacturer_collision_with_extract(self):
        with pytest.raises(DiscoveryHintError, match="collides with extract.manufacturer"):
            parse_driver_discovery(self._basic(
                extract_manufacturer="AcmeCorp",
                extract={"manufacturer": "OtherCorp"},
            ))

    def test_connect_only_probe(self):
        # No send + no expect = connect-only TCP probe (matches every
        # responding port). Useful for "the box answers on this port"
        # signal — narrower than just open_ports.
        h = parse_driver_discovery(_drv(
            "widget", tcp_probe={"port": 12345},
        ))
        assert h is not None
        assert h.tcp_probe is not None
        assert h.tcp_probe.send == b""

    def test_send_without_match_rejected(self):
        # A TCP probe that sends bytes but no matcher would emit
        # evidence for any responding host. Useless, so rejected.
        with pytest.raises(DiscoveryHintError, match="declares no matcher"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "port": 12345, "send_ascii": "x",
            }))

    def test_port_required(self):
        with pytest.raises(DiscoveryHintError, match="tcp_probe.port"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "send_ascii": "x", "expect": "y",
            }))

    @pytest.mark.parametrize("port", [0, -1, 70000])
    def test_port_out_of_range(self, port):
        with pytest.raises(DiscoveryHintError, match="tcp_probe.port"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "port": port, "send_ascii": "x", "expect": "y",
            }))

    def test_send_hex_and_ascii_both_rejected(self):
        with pytest.raises(DiscoveryHintError, match="send_hex and send_ascii"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "port": 12345, "send_hex": "aa", "send_ascii": "x",
                "expect": "y",
            }))

    def test_invalid_hex_send(self):
        with pytest.raises(DiscoveryHintError, match="send_hex.*not valid hex"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "port": 12345, "send_hex": "zz", "expect": "y",
            }))

    def test_invalid_regex(self):
        with pytest.raises(DiscoveryHintError, match="expect_regex failed to compile"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "port": 12345, "send_ascii": "x", "expect_regex": "[",
            }))

    def test_invalid_expect_hex(self):
        with pytest.raises(DiscoveryHintError, match="expect_hex.*not valid hex"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "port": 12345, "send_ascii": "x", "expect_hex": "zz",
            }))

    def test_timeout_capped(self):
        with pytest.raises(DiscoveryHintError, match="timeout_ms"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "port": 12345, "send_ascii": "x", "expect": "y",
                "timeout_ms": 99999,
            }))

    def test_unknown_keys_rejected(self):
        with pytest.raises(DiscoveryHintError, match="unknown keys"):
            parse_driver_discovery(_drv("bad", tcp_probe={
                "port": 12345, "send_ascii": "x", "expect": "y",
                "weird_field": 42,
            }))


class TestUdpProbe:
    def test_basic(self):
        h = parse_driver_discovery(_drv("widget", udp_probe={
            "port": 6454, "send_hex": "417274", "expect_regex": "AcmeCorp",
        }))
        assert h is not None
        spec = h.udp_probe
        assert spec is not None
        assert spec.kind == "udp"
        assert spec.port == 6454
        assert spec.send == bytes.fromhex("417274")
        assert spec.timeout_ms == 2000  # default
        assert spec.probe_id == "custom_widget_udp"

    def test_udp_requires_send(self):
        # UDP probes need a query payload; without one there's nothing to
        # broadcast. Reject at parse time.
        with pytest.raises(DiscoveryHintError, match="must declare send_ascii or send_hex"):
            parse_driver_discovery(_drv("bad", udp_probe={
                "port": 6454, "expect": "y",
            }))

    def test_udp_requires_matcher(self):
        # UDP without an expect would emit evidence for any reply at all,
        # which is too noisy.
        with pytest.raises(DiscoveryHintError, match="needs exactly one"):
            parse_driver_discovery(_drv("bad", udp_probe={
                "port": 6454, "send_ascii": "x",
            }))

    def test_udp_rejects_multiple_matchers(self):
        # B9: declaring more than one expect_* silently AND-matches at
        # runtime; reject at parse time so the author has to pick.
        with pytest.raises(DiscoveryHintError, match="declares multiple matchers"):
            parse_driver_discovery(_drv("bad", udp_probe={
                "port": 6454, "send_ascii": "x",
                "expect_hex": "AB CD", "expect_regex": "Sony",
            }))

    def test_cross_vendor_flag_default_false(self):
        h = parse_driver_discovery(_drv("widget", udp_probe={
            "port": 6454, "send_ascii": "x", "expect": "y",
        }))
        assert h.udp_probe.cross_vendor is False

    def test_cross_vendor_must_be_bool(self):
        with pytest.raises(DiscoveryHintError, match="cross_vendor"):
            parse_driver_discovery(_drv("bad", udp_probe={
                "port": 6454, "send_ascii": "x", "expect": "y",
                "cross_vendor": "yes",
            }))


class TestPythonProbe:
    def test_string_path(self):
        h = parse_driver_discovery(_drv(
            "widget", python="./widget_discovery.py",
        ))
        assert h is not None
        assert h.python_probe is not None
        assert h.python_probe.file_path == "./widget_discovery.py"
        assert h.python_probe.cross_vendor is False
        assert h.python_probe.broadcast_probe_id == "custom_widget_companion_udp"
        assert h.python_probe.active_probe_id == "custom_widget_companion_tcp"

    def test_mapping_form(self):
        h = parse_driver_discovery(_drv("widget", python={
            "file": "./widget_discovery.py",
            "cross_vendor": True,
        }))
        assert h is not None
        assert h.python_probe.cross_vendor is True

    def test_empty_path_rejected(self):
        with pytest.raises(DiscoveryHintError, match="non-empty"):
            parse_driver_discovery(_drv("bad", python=""))

    def test_mapping_requires_file(self):
        with pytest.raises(DiscoveryHintError, match="python.file"):
            parse_driver_discovery(_drv("bad", python={"cross_vendor": True}))

    def test_mapping_unknown_keys_rejected(self):
        with pytest.raises(DiscoveryHintError, match="unknown keys"):
            parse_driver_discovery(_drv("bad", python={
                "file": "./x.py", "extra_field": "junk",
            }))


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------


class TestOuiHint:
    def test_list_of_prefixes(self):
        h = parse_driver_discovery(_drv("widget", oui=["00:0e:dd", "d8:34:ee"]))
        assert h is not None
        assert h.oui == ["00:0e:dd", "d8:34:ee"]

    def test_must_be_list(self):
        with pytest.raises(DiscoveryHintError, match="oui must be a list"):
            parse_driver_discovery(_drv("bad", oui="00:0e:dd"))

    def test_rejects_empty_string(self):
        with pytest.raises(DiscoveryHintError, match="oui entries"):
            parse_driver_discovery(_drv("bad", oui=[""]))


class TestHostnameHint:
    def test_list_of_patterns(self):
        h = parse_driver_discovery(_drv("widget", hostname=["^MXA", "^ANI"]))
        assert h is not None
        assert h.hostname == ["^MXA", "^ANI"]

    def test_must_be_list(self):
        with pytest.raises(DiscoveryHintError, match="hostname must be a list"):
            parse_driver_discovery(_drv("bad", hostname="^MXA"))


class TestPortOpenHint:
    def test_list_of_ports(self):
        h = parse_driver_discovery(_drv("widget", port_open=[1710, 4352]))
        assert h is not None
        assert h.port_open == [1710, 4352]

    def test_must_be_list(self):
        with pytest.raises(DiscoveryHintError, match="port_open must be a list"):
            parse_driver_discovery(_drv("bad", port_open="1710"))

    def test_rejects_non_int(self):
        with pytest.raises(DiscoveryHintError, match="must be integers"):
            parse_driver_discovery(_drv("bad", port_open=["1710"]))

    def test_rejects_bool(self):
        # Bool is an int subclass — explicitly rejected.
        with pytest.raises(DiscoveryHintError, match="must be integers"):
            parse_driver_discovery(_drv("bad", port_open=[True]))

    def test_rejects_out_of_range(self):
        with pytest.raises(DiscoveryHintError, match="out of range"):
            parse_driver_discovery(_drv("bad", port_open=[0]))
        with pytest.raises(DiscoveryHintError, match="out of range"):
            parse_driver_discovery(_drv("bad", port_open=[70000]))

    @pytest.mark.parametrize("port", [22, 80, 443, 8000, 8080, 8443, 8888])
    def test_rejects_too_generic(self, port):
        with pytest.raises(DiscoveryHintError, match="too generic"):
            parse_driver_discovery(_drv("bad", port_open=[port]))


class TestManufacturerAliasHint:
    def test_list_of_aliases(self):
        h = parse_driver_discovery(_drv(
            "widget",
            manufacturer_alias=["AcmeCorp", "Acme Corporation", "Acme"],
        ))
        assert h is not None
        # Aliases normalize to lowercase + stripped.
        assert h.manufacturer_alias == ["acmecorp", "acme corporation", "acme"]

    def test_strips_whitespace(self):
        h = parse_driver_discovery(_drv(
            "widget", manufacturer_alias=["  AcmeCorp  "],
        ))
        assert h is not None
        assert h.manufacturer_alias == ["acmecorp"]

    def test_dedup_case_insensitive(self):
        h = parse_driver_discovery(_drv(
            "widget", manufacturer_alias=["AcmeCorp", "acmecorp", " ACMECORP "],
        ))
        assert h is not None
        assert h.manufacturer_alias == ["acmecorp"]

    def test_must_be_list(self):
        with pytest.raises(DiscoveryHintError, match="manufacturer_alias must be a list"):
            parse_driver_discovery(_drv("bad", manufacturer_alias="AcmeCorp"))

    def test_rejects_non_string(self):
        with pytest.raises(DiscoveryHintError, match="must be strings"):
            parse_driver_discovery(_drv("bad", manufacturer_alias=[123]))

    def test_rejects_empty_string(self):
        with pytest.raises(DiscoveryHintError, match="non-empty"):
            parse_driver_discovery(_drv("bad", manufacturer_alias=[""]))


class TestSnmpPenHint:
    def test_positive_int(self):
        h = parse_driver_discovery(_drv("widget", snmp_pen=17049))
        assert h is not None
        assert h.snmp_pen == 17049

    def test_must_be_positive_int(self):
        with pytest.raises(DiscoveryHintError, match="snmp_pen"):
            parse_driver_discovery(_drv("bad", snmp_pen="17049"))


# ---------------------------------------------------------------------------
# Top-level parser behavior
# ---------------------------------------------------------------------------


class TestTopLevelParser:
    def test_template_drivers_skipped(self):
        h = parse_driver_discovery({"id": "generic_tcp", "discovery": {}})
        assert h is None

    def test_missing_id_rejected(self):
        with pytest.raises(DiscoveryHintError, match="missing required 'id'"):
            parse_driver_discovery({"discovery": {}})

    def test_discovery_must_be_mapping(self):
        with pytest.raises(DiscoveryHintError, match="must be a mapping"):
            parse_driver_discovery({"id": "x", "discovery": "garbage"})

    def test_unknown_top_level_keys_rejected(self):
        with pytest.raises(DiscoveryHintError, match="unknown keys"):
            parse_driver_discovery(_drv("bad", typo_field=["x"]))

    def test_no_signals_warns_but_loads(self, caplog):
        # A driver with no fingerprints and no hints is almost
        # certainly a mistake — log a warning, but don't reject.
        import logging
        with caplog.at_level(logging.WARNING, logger="discovery.hints"):
            h = parse_driver_discovery(_drv("ghost"))
        assert h is not None
        assert "ghost" in caplog.text
        assert "never participate in matching" in caplog.text

    def test_fingerprint_alone_silences_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="discovery.hints"):
            parse_driver_discovery(_drv("widget", mdns="_widget._tcp.local."))
        assert "never participate in matching" not in caplog.text

    def test_hint_alone_silences_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="discovery.hints"):
            parse_driver_discovery(_drv("widget", oui=["00:0e:dd"]))
        assert "never participate in matching" not in caplog.text


# ---------------------------------------------------------------------------
# Signal-index builder
# ---------------------------------------------------------------------------


class TestSignalIndexBuilder:
    def test_mdns_collision_raises(self):
        registry = [
            _drv("a", mdns="_widget._tcp.local."),
            _drv("b", mdns="_widget._tcp.local."),
        ]
        hints = load_discovery_hints(registry)
        with pytest.raises(ValueError, match="Signal collision"):
            build_signal_index(hints)

    def test_mdns_txt_filter_disambiguates(self):
        registry = [
            _drv("a", mdns={
                "service": "_http._tcp.local.",
                "txt": {"manufacturer": "AcmeCorp"},
            }),
            _drv("b", mdns={
                "service": "_http._tcp.local.",
                "txt": {"manufacturer": "OtherCorp"},
            }),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        rule = idx.find_strong(
            KIND_MDNS, "_http._tcp.local.", txt={"manufacturer": "OtherCorp"},
        )
        assert rule is not None and rule.driver_id == "b"

    def test_ssdp_collision_raises(self):
        registry = [
            _drv("a", ssdp="urn:foo:device:Bar:1"),
            _drv("b", ssdp="urn:foo:device:Bar:1"),
        ]
        with pytest.raises(ValueError, match="Signal collision"):
            build_signal_index(load_discovery_hints(registry))

    def test_ssdp_shared_urn_split_by_model_filter(self):
        # A vendor family advertising one URN: each driver filters on the
        # modelName from the UPnP device description.
        registry = [
            _drv("widget_a", ssdp={
                "device_type": "urn:foo:device:AcmeFamily:1",
                "model": "Widget-6",
            }),
            _drv("widget_b", ssdp={
                "device_type": "urn:foo:device:AcmeFamily:1",
                "model": "Widget-6a",
            }),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        rule = idx.find_strong(
            KIND_SSDP, "urn:foo:device:AcmeFamily:1", txt={"model": "Widget-6a"},
        )
        assert rule is not None and rule.driver_id == "widget_b"
        # Exact match — "Widget-6" must not claim a "Widget-6a" observation.
        rule = idx.find_strong(
            KIND_SSDP, "urn:foo:device:AcmeFamily:1", txt={"model": "Widget-6"},
        )
        assert rule is not None and rule.driver_id == "widget_a"
        # No description fields observed -> no filtered rule can match.
        assert idx.find_strong(KIND_SSDP, "urn:foo:device:AcmeFamily:1") is None

    def test_ssdp_identical_model_filters_collide(self):
        registry = [
            _drv("a", ssdp={
                "device_type": "urn:foo:device:AcmeFamily:1",
                "model": "Widget-6",
            }),
            _drv("b", ssdp={
                "device_type": "urn:foo:device:AcmeFamily:1",
                "model": "Widget-6",
            }),
        ]
        with pytest.raises(ValueError, match="Signal collision"):
            build_signal_index(load_discovery_hints(registry))

    def test_ssdp_unfiltered_claim_cannot_shadow_filtered(self):
        registry = [
            _drv("a", ssdp={
                "device_type": "urn:foo:device:AcmeFamily:1",
                "model": "Widget-6",
            }),
            _drv("b", ssdp="urn:foo:device:AcmeFamily:1"),
        ]
        with pytest.raises(ValueError, match="Signal collision"):
            build_signal_index(load_discovery_hints(registry))

    def test_amx_ddp_indexed(self):
        registry = [_drv("widget", amx_ddp={"make": "AcmeCorp"})]
        idx = build_signal_index(load_discovery_hints(registry))
        rule = idx.find_strong(KIND_AMX_DDP, "AcmeCorp/*")
        assert rule is not None and rule.driver_id == "widget"

    def test_tcp_probe_registered_as_active_probe(self):
        registry = [_drv("widget", tcp_probe={
            "port": 12345, "send_ascii": "x", "expect": "y",
        })]
        idx = build_signal_index(load_discovery_hints(registry))
        rule = idx.find_strong(KIND_ACTIVE_PROBE, "custom_widget_tcp")
        assert rule is not None and rule.driver_id == "widget"
        assert rule.generic is False

    def test_udp_probe_registered_as_broadcast(self):
        registry = [_drv("widget", udp_probe={
            "port": 6454, "send_ascii": "x", "expect": "y",
        })]
        idx = build_signal_index(load_discovery_hints(registry))
        rule = idx.find_strong(KIND_BROADCAST, "custom_widget_udp")
        assert rule is not None and rule.driver_id == "widget"

    def test_cross_vendor_flag_flows_to_signal_rule(self):
        registry = [_drv("widget", tcp_probe={
            "port": 12345, "send_ascii": "x", "expect": "y",
            "cross_vendor": True,
        })]
        idx = build_signal_index(load_discovery_hints(registry))
        rule = idx.find_strong(KIND_ACTIVE_PROBE, "custom_widget_tcp")
        assert rule is not None
        assert rule.generic is True

    def test_python_probe_registers_two_synthetic_ids(self):
        registry = [_drv("widget", python={
            "file": "./widget_discovery.py", "cross_vendor": True,
        })]
        idx = build_signal_index(load_discovery_hints(registry))
        bcast = idx.find_strong(KIND_BROADCAST, "custom_widget_companion_udp")
        assert bcast is not None
        assert bcast.generic is True
        active = idx.find_strong(KIND_ACTIVE_PROBE, "custom_widget_companion_tcp")
        assert active is not None
        assert active.generic is True

    def test_oui_collision_allowed(self):
        # Hints (soft signals) are allowed to overlap — produces possible state.
        registry = [
            _drv("dsp_a", tcp_probe={
                "port": 1234, "send_ascii": "x", "expect": "y",
            }, oui=["78:45:01"]),
            _drv("dsp_b", tcp_probe={
                "port": 5678, "send_ascii": "x", "expect": "y",
            }, oui=["78:45:01"]),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        assert sorted(idx.find_soft_oui("78:45:01:11:22:33")) == ["dsp_a", "dsp_b"]

    def test_indexing_covers_all_fingerprints_and_hints(self):
        registry = [
            _drv(
                "kitchen_sink",
                mdns="_widget._tcp.local.",
                ssdp="urn:foo:device:Bar:1",
                amx_ddp={"make": "AcmeCorp", "model_pattern": "Widget*"},
                tcp_probe={
                    "port": 12345, "send_ascii": "x", "expect": "y",
                },
                udp_probe={
                    "port": 6454, "send_ascii": "x", "expect": "y",
                },
                python={"file": "./kitchen_sink_discovery.py"},
                snmp_pen=17049,
                oui=["00:05:a6"],
                hostname=["^kitchen-"],
                port_open=[1710],
                manufacturer_alias=["AcmeCorp", "AcmeWidgets"],
            ),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        assert idx.find_strong(KIND_MDNS, "_widget._tcp.local.") is not None
        assert idx.find_strong(KIND_SSDP, "urn:foo:device:Bar:1") is not None
        assert idx.find_strong(KIND_AMX_DDP, "AcmeCorp/Widget*") is not None
        assert idx.find_strong(KIND_ACTIVE_PROBE, "custom_kitchen_sink_tcp") is not None
        assert idx.find_strong(KIND_BROADCAST, "custom_kitchen_sink_udp") is not None
        assert idx.find_strong(KIND_BROADCAST, "custom_kitchen_sink_companion_udp") is not None
        assert idx.find_strong(KIND_ACTIVE_PROBE, "custom_kitchen_sink_companion_tcp") is not None
        assert idx.find_soft_pen(17049) == ["kitchen_sink"]
        assert idx.find_soft_oui("00:05:a6:aa:bb:cc") == ["kitchen_sink"]
        assert idx.find_soft_hostname("kitchen-widget-1") == ["kitchen_sink"]
        assert idx.find_soft_open_port(1710) == ["kitchen_sink"]
        assert idx.find_soft_vendor_string("AcmeCorp") == ["kitchen_sink"]
        assert idx.find_soft_vendor_string("acmewidgets") == ["kitchen_sink"]

    def test_port_open_collision_allowed(self):
        # Hints are soft — multiple drivers can claim the same port.
        registry = [
            _drv("a", tcp_probe={
                "port": 1234, "send_ascii": "x", "expect": "y",
            }, port_open=[1710]),
            _drv("b", oui=["00:11:22"], port_open=[1710]),
        ]
        idx = build_signal_index(load_discovery_hints(registry))
        assert sorted(idx.find_soft_open_port(1710)) == ["a", "b"]

    def test_no_signals_loads_but_registers_nothing(self, caplog):
        # The warning path runs and the index doesn't register the driver.
        import logging
        registry = [_drv("ghost")]
        with caplog.at_level(logging.WARNING, logger="discovery.hints"):
            hints = load_discovery_hints(registry)
        idx = build_signal_index(hints)
        assert idx.driver_count() == 0
