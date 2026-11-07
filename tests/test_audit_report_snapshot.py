"""summary.html and timeline.txt, byte for byte, for three synthetic reports.

Between them the reports reach every section the two renderers write and the
branches inside them: each kind of discovery signal, each web page, port,
announcement and SNMP outcome, a driver test with every row an attempt can
carry, the commands, settings and outages written, and the traffic lines of
every channel. The expected files are in ``tests/fixtures/audit_report_snapshot/``;
clock times and the UTC offset depend on the machine's time zone, so both
sides are compared with those written as placeholders.

Rewrite the expected files after a deliberate change to the report's words:

    python tests/test_audit_report_snapshot.py --regen
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from openavc.audit.report import render_summary, render_timeline

FIXTURES = Path(__file__).parent / "fixtures" / "audit_report_snapshot"

T0 = 1_780_000_000.0

_LOCAL_TIMES = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?"), "<date>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\.\d{3}\b"), "<clock>"),
    (re.compile(r"UTC[+-]\d{2}:\d{2}"), "UTC<offset>"),
)


def _placeholders(text: str) -> str:
    for pattern, placeholder in _LOCAL_TIMES:
        text = pattern.sub(placeholder, text)
    return text


def _signal(kind: str | None = None, *, drivers=(), strong=False, source="", **data) -> dict:
    if kind is not None:
        data["kind"] = kind
    return {
        "source": source,
        "drivers": list(drivers),
        "strong": strong,
        "evidence": {"data": data},
    }


SIGNALS = [
    _signal("mdns", drivers=["acme_widget"], strong=True, source_id="_acme._tcp"),
    _signal("mdns"),
    _signal("ssdp", drivers=["acme_widget", "acme_other"], source_id="urn:acme-com:device:Widget:1"),
    _signal("ssdp"),
    _signal("amx_ddp", make="Acme", model="Widget 3000"),
    _signal("amx_ddp"),
    _signal("broadcast", port=9131, matched_pattern="WIDGET"),
    _signal("broadcast"),
    _signal("probe", drivers=["acme_widget"], strong=True, port=23,
            response={"text": "\x00\x01Acme Widget v2\r\n"}),
    _signal("probe", port=23, response={"text": 'say "hi"'}),
    _signal("probe", port=4000, matched_pattern="^\\x02", response={"text": "\x02\x03\x04"}),
    _signal("probe", response={"text": "\x02\x03", "hex": "0203"}),
    _signal("probe", port="23", response={}),
    _signal("probe", port=23, response={"text": "\x00Hi there\x01\x02\x03\x04trailing"}),
    _signal("oui", value="00:11:22", vendor="Acme Corp"),
    _signal("oui", value="00:11:23"),
    _signal("hostname", value="widget-1", matched_pattern="^widget-"),
    _signal("hostname", value="widget-2"),
    _signal("snmp_pen", value=99999),
    _signal("vendor_string", drivers=["acme_widget"], value="Acme", from_driver=True,
            supplied_by="acme_widget"),
    _signal("vendor_string", drivers=["acme_widget"], value="Acme", from_driver=True,
            supplied_by="nobody"),
    _signal("vendor_string", drivers=["acme_widget"], value="Acme", source_probe_id="greeting:23"),
    _signal("vendor_string", value="Acme", source_probe_id="http_server:80"),
    _signal("vendor_string", value="Acme", source_probe_id="ssdp_server"),
    _signal("vendor_string", value="Acme", from_kind="ssdp"),
    _signal("vendor_string", value="Acme", from_kind="mdns"),
    _signal("vendor_string", value="Acme", from_kind="amx_ddp"),
    _signal("vendor_string", value="Acme", source_probe_id="greeting"),
    _signal("open_port", value=8080),
    _signal(source="mdns:_widget._tcp"),
    _signal(source="hostname:widget-3"),
    _signal(source="probe:custom_acme_widget_tcp"),
    _signal(source="broadcast:custom_acme_widget_udp"),
    _signal(source="mystery:thing"),
    _signal(source="oui:"),
    _signal(),
]


def _attempt_full() -> dict[str, Any]:
    return {
        "status": "stopped",
        "started_at": T0 + 100,
        "connected_at": T0 + 101.5,
        "finished_at": T0 + 131.5,
        "first_tx_at": T0 + 101.6,
        "first_rx_at": T0 + 101.9,
        "reported": 3,
        "declared": 5,
        "set_by_driver": 1,
        "drops": 1,
        "reconnects": 1,
        "later_drops": 2,
        "later_reconnects": 0,
        "poll_interval": 10,
        "liveness_every": 30,
        "silence_polls": 5,
        "traffic": {
            "count": 12, "sent": 6, "received": 6, "bytes": 240, "truncated_at": T0 + 130,
            "entries": [
                {"t": T0 + 101.6, "direction": "tx", "channel": "tcp", "text": "POWER?\r",
                 "hex": "504f5745523f0d", "meta": {}},
                {"t": T0 + 101.8, "direction": "rx", "channel": "tcp", "chunk": True,
                 "text": "PO", "hex": "504f", "meta": {}},
                {"t": T0 + 101.9, "direction": "rx", "channel": "tcp", "text": "POWER=ON\r",
                 "hex": "504f5745523d4f4e0d", "meta": {"peer": "10.0.0.50:23"}},
                {"t": T0 + 102.0, "direction": "rx", "channel": "udp", "text": "\x00\x01\xff",
                 "hex": "0001ff", "meta": {}},
                {"t": T0 + 102.1, "direction": "tx", "channel": "http", "text": "",
                 "meta": {"method": "GET", "target": "/status"}},
                {"t": T0 + 102.2, "direction": "rx", "channel": "http", "text": "{\"on\":true}",
                 "hex": "7b226f6e223a747275657d",
                 "meta": {"method": "GET", "target": "/status", "status": 200, "reason": "OK"}},
                {"t": T0 + 102.3, "direction": "rx", "channel": "http", "text": "",
                 "meta": {"method": "POST", "target": "/power", "error": "timed out"}},
                {"t": T0 + 102.4, "direction": "rx", "channel": "http_listener", "text": "x=1",
                 "hex": "783d31", "meta": {}},
                {"t": T0 + 102.5, "direction": "rx", "channel": "http", "text": "", "meta": {}},
                {"t": T0 + 102.6, "direction": "rx", "channel": "mqtt", "text": "on",
                 "hex": "6f6e", "meta": {"topic": "acme/widget/power"}},
                {"t": T0 + 102.7, "direction": "rx", "channel": "tcp", "text": "", "meta": {}},
                {"t": T0 + 102.8, "direction": "tx", "channel": "file", "text": "",
                 "meta": {"file": "firmware.bin", "size": 2048, "command": "Upload firmware"}},
                {"t": T0 + 102.9, "direction": "rx", "channel": "file", "text": "",
                 "meta": {"file": "log.txt"}},
                {"t": T0 + 103.0, "direction": "rx", "channel": "tcp", "text": "",
                 "meta": {"withheld": 4096}},
                {"t": T0 + 103.1, "direction": "tx", "channel": "tcp",
                 "text": "A" * 500, "hex": "41" * 500, "meta": {}},
            ],
        },
        "unprompted_replies": {"count": 2, "window_seconds": 1.5},
        "front_panel": {"answer": "showed", "note": "volume moved"},
        "status_table": {
            "variables": [
                {"name": "power", "label": "Power", "reported": True, "value": True},
                {"name": "model", "label": "Model", "reported": True, "value": "Widget 3000 Pro"},
                {"name": "firmware", "reported": True, "value": "2.1.0"},
                {"name": "device_name", "label": "Name", "reported": True, "value": "Lobby Widget"},
                {"name": "input", "label": "Input", "set_by_driver": True, "value": "hdmi1",
                 "problem": "not one of its declared values"},
                {"name": "volume", "label": "Volume", "sources": ["Volume poll", "Power"]},
                {"name": "last_error", "reported": False},
                {"name": "mute", "label": "Mute", "reported": True, "value": None},
            ],
            "children": {"zone": ["1", "2"], "input": ["a"], "output": ["x", "y", "z"]},
            "child_labels": {"zone": {"one": "zone", "many": "zones"}, "input": {"one": "input"}},
            "settings": [
                {"key": "name", "label": "Device name", "populated": True, "value": "Lobby"},
                {"key": "dhcp", "populated": True, "value": False},
                {"key": "gateway", "label": "Gateway", "populated": False},
            ],
        },
        "contract": {
            "counts": {"unmatched_response": 3, "undeclared_state": 1, "made_up_kind": 2,
                       "unknown_command": 1, "type_mismatch": 4, "coercion_failure": 1},
            "events": [
                {"kind": "unmatched_response", "detail": {"text": "ERR 12"}},
                {"kind": "undeclared_state", "detail": {"state": "temperature"}},
                {"kind": "made_up_kind", "detail": "a plain string"},
                {"kind": "unknown_command", "detail": {"command": "fly"}},
                {"kind": "type_mismatch", "detail": {"address": "zone 3"}},
                {"kind": "coercion_failure", "detail": {"other": "x"}},
            ],
        },
    }


def _attempts_more() -> list[dict[str, Any]]:
    return [
        {
            "status": "finished", "started_at": T0 + 200, "connected_at": T0 + 201,
            "ends_at": T0 + 202, "reported": 0, "declared": 4, "drops": 3,
            "later_reconnects": 1,
            "silence_polls": 3,
            "traffic": {"count": 1, "sent": 1, "received": 0, "bytes": 8, "entries": []},
            "front_panel": {"answer": "could_not_try"},
        },
        {
            "status": "finished", "started_at": T0 + 300, "connected_at": T0 + 301,
            "finished_at": T0 + 301.2, "reported": 1, "declared": 1,
            "traffic": {"count": 4, "sent": 4, "received": 0, "bytes": 30, "entries": []},
            "front_panel": {"answer": "missed"},
        },
        {
            "status": "finished", "started_at": T0 + 400, "connected_at": T0 + 401,
            "finished_at": T0 + 402, "reported": 2, "declared": 2,
            "traffic": {"not_captured": True},
        },
        {"status": "failed", "started_at": T0 + 500, "error": "The driver raised an error."},
        {"status": "failed", "started_at": T0 + 600,
         "offline": {"detail": "Connection refused.", "next_step": "Check the port."}},
        {"status": "failed", "started_at": T0 + 700, "offline": {"code": "timeout"}},
        {"status": "failed", "started_at": T0 + 800, "offline": {}},
    ]


def _commands() -> dict[str, Any]:
    return {
        "catalog": [
            {"name": "set_volume", "params": {"level": {"label": "Level"}}},
            "not a dict",
        ],
        "trials": [
            {"number": 1, "label": "Power on", "command": "power_on",
             "summary": "the device turned on", "answer": {"answer": "yes"}},
            {"number": 2, "label": "Set volume", "command": "set_volume",
             "params": {"level": 40, "input_id": "hdmi1"}, "batch": True,
             "since_previous": {"seconds": 1.0, "label": "Power on"},
             "summary": "volume went to 40", "answer": {"answer": "partly", "note": "slowly"}},
            {"number": 3, "label": "Set volume", "command": "set_volume", "attempt": 2,
             "params": {"level": 50},
             "since_previous": {"seconds": 4.25, "label": "Set volume"},
             "summary": "volume went to 50"},
            {"number": 4, "label": "Reboot", "command": "reboot", "attempt": 2,
             "error": "unknown command"},
        ],
        "changed": [
            {"key": "device.x.power", "label": "Power", "before": False, "now": True,
             "by": {"number": 1, "label": "Power on"}},
            {"key": "device.x.input", "before": "hdmi2", "now": "hdmi1"},
            {"key": "device.x.volume", "label": "Volume", "before": None, "now": 40,
             "by": {"number": 2, "label": "Set volume"}},
            {"key": "device.x.level", "label": "Level", "before": None, "now": 3,
             "by": {"number": 2, "label": "Set volume"}},
            {"key": "device.x.temp", "label": "Temperature", "before": None, "now": None},
            {"key": "device.x.clock", "label": "Clock", "on_its_own": True},
            {"key": "device.x.uptime", "on_its_own": True},
        ],
    }


def _driver_full() -> dict[str, Any]:
    return {
        "run": 0,
        "driver": {
            "id": "acme_widget", "name": "Acme Widget", "version": "1.2.0",
            "format": "avcdriver", "source": "catalog",
            "files": [
                {"name": "acme_widget.avcdriver", "sha256": "aa11", "matches_catalog": True,
                 "in_report": "driver/acme_widget.avcdriver"},
                {"name": "acme_widget_sim.py", "sha256": "bb22", "matches_catalog": False,
                 "catalog_sha256": "cc33"},
                {"name": "extra.txt", "sha256": "dd44"},
            ],
        },
        "entered": {"manufacturer": "Acme", "model": "Widget 3000", "firmware": "2.1.0"},
        "model_listing": {"listed": False},
        "verdict_agreement": "agrees",
        "connection": {"transport": "tcp", "config": {"host": "10.0.0.50", "port": 23},
                       "saved_from": "Lobby Widget"},
        "attempts": [_attempt_full(), *_attempts_more()],
        "commands": _commands(),
        "settings": {"trials": [
            {"key": "name", "label": "Device name", "summary": "written, read back, put back"},
            {"key": "dhcp", "summary": "written, not read back"},
        ]},
        "outages": [
            {"kind": "power_cycle", "number": 1, "summary": "noticed in 4 s, back in 30 s"},
            {"kind": "cable_pull", "number": 2, "status": "done"},
            {"kind": "mystery", "number": 3, "summary": "something happened"},
        ],
        "suggested_confidence": {
            "level": "partial",
            "reasons": [{"held": True, "text": "Connected."},
                        {"held": False, "text": "No command was tried."}],
        },
        "test_report": {"url": "https://example.invalid/form?title=a&b=c"},
    }


def _driver_python() -> dict[str, Any]:
    return {
        "run": 1,
        "driver": {"id": "acme_other", "name": "Acme Other", "version": None,
                   "format": "python", "source": "imported", "files": []},
        "entered": {"model": "Widget 3000 Pro"},
        "model_listing": {"listed": True},
        "verdict_agreement": "differs",
        "connection": {"transport": "serial", "config": {}},
        "attempts": [{
            "status": "finished", "started_at": T0 + 900, "connected_at": T0 + 900.4,
            "finished_at": T0 + 960, "reported": 1, "declared": 1,
            "traffic": {"count": 2, "sent": 1, "received": 1, "bytes": 9, "entries": [
                {"t": T0 + 901, "direction": "tx", "channel": "serial", "text": "Q\r",
                 "hex": "510d", "meta": {}},
            ]},
            "status_table": {"variables": [
                {"name": "model_name", "reported": True, "value": "Widget 3000 Pro"},
                {"name": "speaker_name", "reported": True, "value": ""},
                {"name": "system_name", "reported": True, "value": "lobby widget"},
            ]},
        }],
        "commands": {"trials": [{"number": 1, "label": "Ping", "command": "ping",
                                 "summary": "it answered"}]},
        "outages": [],
        "suggested_confidence": {"level": "full", "reasons": []},
        "test_report": {"url": ""},
    }


def _driver_unconnected() -> dict[str, Any]:
    return {
        "run": 2,
        "driver": {"id": "acme_third", "name": "Acme Third", "version": "0.1",
                   "format": "yaml?", "source": "elsewhere"},
        "entered": {},
        "verdict_agreement": "mystery",
        "attempts": [],
        "suggested_confidence": {"level": None, "reasons": [{"held": False, "text": "Did not connect."}]},
    }


def full_report() -> dict[str, Any]:
    return {
        "complete": True,
        "generator": {"openavc_version": "9.9.9", "os": "TestOS-1.0"},
        "session": {
            "started_at": T0,
            "tester": {"name": "Pat Tester", "company": "Acme AV", "email": "pat@example.invalid",
                       "notes": "Rack 2 <top>"},
        },
        "target": {"address": "widget.local", "ip": "10.0.0.50"},
        "device": {
            "entered": {"manufacturer": "Acme", "model": "Widget 3000"},
            "reported": {
                "manufacturer": "Acme Corp", "model": "Widget 3000", "model_number": "W3K",
                "firmware": "2.1.0", "serial_number": "AW3-0042", "device_name": "widget-lobby",
                "hostname": "widget.lan", "mac": "00:11:22:33:44:55",
            },
            "reported_sources": {"manufacturer": "the UPnP description", "device_name": "mDNS",
                                 "mac": "SNMP"},
        },
        "catalog": {"driver_count": 120, "used": "fresh", "sha256": "abc123"},
        "footprint": {
            "started_at": T0 + 1, "finished_at": T0 + 46.6,
            "ping": {"result": "alive"},
            "mac": {"address": "00:11:22:33:44:55", "source": "arp"},
            "names": {
                "reverse_dns": "widget.lan",
                "netbios": {"hostname": "WIDGET", "workgroup": "AV",
                            "names": [{"name": "WIDGET"}, {"name": "AV"}]},
            },
            "ports": {"checked": 40, "range": "the driver ports", "open": [23, 80, 8080],
                      "from_announcements": [8080], "refused": [22, 443], "filtered": [9999]},
            "greetings": {"23": {"text": "Welcome to Widget\r\n", "hex": "57"},
                          "80": {"text": "", "hex": ""},
                          "4000": {"text": "\x01\x02\x03", "hex": "010203"}},
            "web": {
                "80": {"url": "http://10.0.0.50/", "status_line": "HTTP/1.1 200 OK",
                       "title": "Widget", "server": "acme-httpd", "tries": 2},
                "8080": {"url": "http://10.0.0.50:8080/", "error": "timed out", "tries": 2},
                "8443": {"url": "https://10.0.0.50:8443/", "status_line": "HTTP/1.1 302 Found",
                         "www_authenticate": "Basic realm=\"Widget\"", "location": "/login",
                         "followed": {"url": "https://10.0.0.50:8443/login",
                                      "status_line": "HTTP/1.1 200 OK", "title": "Sign in"}},
                "9000": {"url": "http://10.0.0.50:9000/"},
            },
            "certificates": {
                "8443": {"subject": "CN=widget", "issuer": "CN=widget", "self_signed": True,
                         "san_dns": ["widget.local"], "san_ip": ["10.0.0.50"],
                         "not_before": "Jan 1 2026", "not_after": "Jan 1 2036", "sha256": "ee55"},
                "9443": {"subject": "CN=other", "issuer": "CN=Acme CA",
                         "not_before": "Feb 2 2026", "not_after": "Feb 2 2027", "sha256": "ff66"},
            },
            "mdns": {
                "services": [
                    {"service_type": "_acme._tcp", "instance_name": "Widget Lobby", "port": 23,
                     "txt": {"model": "W3K", "fw": "2.1"}},
                    {"service_type": "_http._tcp", "port": 80},
                ],
                "enumerated_types": ["_acme._tcp", "_http._tcp"],
            },
            "ssdp": {
                "device_types": ["urn:acme-com:device:Widget:1"],
                "friendly_name": "Widget Lobby", "manufacturer": "Acme Corp",
                "model_name": "Widget 3000", "model_number": "W3K", "server": "Acme/1.0 UPnP/1.0",
                "location": "http://10.0.0.50:49152/desc.xml",
                "raw_headers": [{"location": "http://10.0.0.50:49152/root.xml"}, "not a dict",
                                {"location": "http://10.0.0.50:49152/desc.xml"}, {}],
            },
            "amx_ddp": {"fields": {"Make": "Acme", "Model": "Widget 3000"}},
            "snmp": {"answered": True, "community": "community 2 of 2",
                     "values": {"sysDescr": "Acme Widget", "sysName": "widget-lobby"},
                     "walk": [{"oid": "1.3.6"}, {"oid": "1.3.7"}], "walk_complete": False},
            "probes": [
                {"probe_id": "custom_acme_widget_tcp", "kind": "tcp", "port": 23,
                 "started_at": T0 + 10, "matched": True,
                 "sent": {"text": "VER?\r", "hex": "5645523f0d"},
                 "reply": {"text": "VER=2.1\r", "hex": "5645523d322e310d"}},
                {"probe_id": "custom_acme_other_companion_udp", "kind": "udp", "port": 9131,
                 "started_at": T0 + 11, "miss": "no reply", "sent": {"text": "\x01\x02", "hex": "0102"}},
                {"probe_id": "custom_acme_third_tcp", "kind": "tcp", "port": 23,
                 "started_at": T0 + 12, "miss": "login_prompt"},
                {"probe_id": "some_probe", "kind": "tcp", "port": 23, "started_at": T0 + 13,
                 "from_greeting": True, "reply": {"text": "Welcome", "hex": "57656c636f6d65"}},
                {"probe_id": None, "kind": "tcp", "port": 4000, "started_at": None,
                 "error": "connection reset"},
            ],
        },
        "verdict": {
            "state": "identified",
            "identification": {"driver_id": "acme_widget"},
            "sentence": "This looks like an Acme Widget.",
            "explanation": {"signals": SIGNALS},
            "drivers": {"acme_widget": {"name": "Acme Widget", "manufacturer": "Acme"},
                        "acme_other": {"name": "Acme Other"}},
            "checks": {
                "acme_widget": [
                    {"declared": "mDNS _acme._tcp", "status": "matched", "detail": "heard"},
                    {"declared": "port 23", "status": "not_matched", "detail": "closed"},
                ],
                "acme_unknown": [
                    {"declared": "SSDP Widget", "status": "not_observed", "detail": "nothing"},
                ],
            },
        },
        "drivers": [_driver_full(), _driver_python(), _driver_unconnected()],
        "timeline": [
            {"t": T0, "kind": "session", "text": "Audit started."},
            {"t": T0 + 50, "kind": "check", "text": "Network check finished."},
            {"kind": "note", "text": "An entry with no time."},
        ],
        "limits": [{"id": "a", "text": "The device does not answer ping <ever>."},
                   {"id": "b", "text": "Second limit."}],
    }


def sparse_report() -> dict[str, Any]:
    """An unfinished check that heard almost nothing, and no driver."""
    return {
        "complete": False,
        "generator": {"openavc_version": "9.9.9", "os": "TestOS-1.0"},
        "session": {"started_at": T0, "tester": {"name": "", "notes": None}},
        "target": {"address": "10.0.0.60", "ip": "10.0.0.60"},
        "device": {"entered": {}, "reported": {}},
        "catalog": {"driver_count": 0, "used": "none"},
        "footprint": {
            "started_at": T0 + 1,
            "ping": {"result": "timeout"},
            "mac": {},
            "names": {},
            "ports": {"checked": 5, "range": "common ports", "open": [], "refused": [],
                      "filtered": [1, 2]},
            "greetings": {"23": {"text": ""}},
            "snmp": {"answered": False, "communities_tried": 1},
        },
        "verdict": {"sentence": "No driver recognized this device.",
                    "explanation": {"signals": [_signal("oui", value="00:11:24")]}},
        "drivers": [],
        "timeline": [],
        "limits": [],
    }


def middle_report() -> dict[str, Any]:
    """An SSDP-only device whose ping errored, no MAC, one community tried twice."""
    return {
        "complete": True,
        "generator": {"openavc_version": "9.9.9", "os": "TestOS-1.0"},
        "session": {"tester": {"email": "pat@example.invalid"}},
        "target": {"address": "acme-tv"},
        "device": {"entered": {}, "reported": {"manufacturer": "Acme"}},
        "catalog": {"driver_count": 7, "used": "cached"},
        "footprint": {
            "ping": {"result": "error"},
            "mac": {"address": "00:11:22:33:44:66", "source": "magic"},
            "names": {"netbios": {"hostname": "ACMETV", "names": []}},
            "ports": {"open": [8001]},
            "ssdp": {"device_types": [], "location": None},
            "snmp": {"communities_tried": 2},
        },
        "verdict": {"state": "candidates", "sentence": "",
                    "identification": {"driver_id": "acme_tv"},
                    "explanation": {"signals": [
                        _signal("vendor_string", value="Acme", from_driver=True),
                    ]}},
        "drivers": [{
            "driver": {"id": "acme_tv", "name": "Acme TV", "version": "1.0", "format": "python",
                       "source": "built_in", "files": []},
            "entered": {"manufacturer": "Acme"},
            "attempts": [{"status": "finished", "started_at": T0, "connected_at": T0 + 1,
                          "finished_at": T0 + 1, "reported": 0, "declared": 0,
                          "traffic": {}}],
        }],
        "timeline": [{"t": T0 + 5, "kind": "x", "text": "only one"}],
    }


def name_only_report() -> dict[str, Any]:
    """Nothing entered or reported: the device takes the recognizing driver's name."""
    return {
        "complete": True,
        "target": {"address": "10.0.0.70", "ip": "10.0.0.70"},
        "device": {"entered": {}, "reported": {}},
        "footprint": {"ping": {"result": "pending"}, "mdns": {"services": []},
                      "snmp": {"answered": True, "community": "public", "values": {},
                               "walk": [{"oid": "1.3.6"}], "walk_complete": True}},
        "verdict": {"state": "identified", "identification": {"driver_id": "acme_widget"},
                    "drivers": {"acme_widget": {"name": "Widget Controller",
                                                "manufacturer": "Acme Corp"}}},
    }


def amx_only_report() -> dict[str, Any]:
    """A device heard only through its AMX beacon, answering SNMP without a walk."""
    return {
        "complete": True,
        "target": {"address": "10.0.0.80", "ip": "10.0.0.80"},
        "footprint": {
            "amx_ddp": {"fields": {"Make": "Acme"}},
            "snmp": {"answered": True, "community": "public", "values": {"sysName": "amx"}},
        },
        "verdict": {"sentence": "Nothing matched."},
    }


REPORTS = {
    "amx_only": amx_only_report,
    "full": full_report,
    "sparse": sparse_report,
    "middle": middle_report,
    "name_only": name_only_report,
}


def _rendered(name: str) -> dict[str, str]:
    report = REPORTS[name]()
    return {
        f"{name}.summary.html": _placeholders(render_summary(report)),
        f"{name}.timeline.txt": _placeholders(render_timeline(report)),
    }


def _readable(text: str) -> list[str]:
    return text.replace("><", ">\n<").splitlines(keepends=True)


@pytest.mark.parametrize("name", sorted(REPORTS))
def test_the_rendered_files_have_not_changed(name):
    for filename, actual in _rendered(name).items():
        expected = (FIXTURES / filename).read_text(encoding="utf-8")
        if actual != expected:
            diff = "".join(difflib.unified_diff(
                _readable(expected), _readable(actual), "expected", "actual",
            ))
            pytest.fail(f"{filename} differs:\n{diff}")


def test_the_placeholders_hide_only_the_local_times():
    """A time zone changes the clock text, never anything the snapshot keeps."""
    text = _rendered("full")["full.timeline.txt"]
    assert "<clock>" in text and "UTC<offset>" in text and "<date>" in text
    assert "00:11:22:33:44:55" in _rendered("full")["full.summary.html"]


def _regen() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name in sorted(REPORTS):
        for filename, text in _rendered(name).items():
            (FIXTURES / filename).write_text(text, encoding="utf-8", newline="")
            print(f"wrote {FIXTURES / filename}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--regen"]:
        _regen()
    else:
        print(__doc__)
