"""What a driver run's results support, pinned whole: the suggested catalog
confidence (every reason, in order, and the level), the catalog's driver test
report filled in from them (every field, the link, how a long one is cut),
what the report says the driver test could not see, and each discovery signal
as the report's evidence lines say it.

Each case states the full result, never one sentence of it: a reordered
reason, a changed count or a field that moved is a failure here.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from openavc.audit.report import (
    TEST_REPORT_URL_MAX,
    _driver_limits,
    _signal_text,
    driver_test_report,
    suggested_confidence,
)


def _section(**over):
    """A connected run: every value reported, nothing mishandled, two commands
    (one answered Yes, one confirmed by its sets), one setting round-tripped."""
    section = {
        "driver": {"id": "acme_widget", "name": "Acme Widget", "version": "1.2.0",
                   "source": "catalog", "files": [{"name": "acme_widget.avcdriver"}]},
        "entered": {"manufacturer": "Acme", "model": "W-100", "firmware": "2.1"},
        "connection": {"transport": "tcp", "config": {"host": "10.0.0.50", "port": 4999}},
        "attempts": [_attempt()],
        "commands": {"trials": [
            {"label": "Power On", "command": "power_on", "answer": {"answer": "yes"},
             "summary": "Power went from off to on."},
            {"label": "Set Volume", "command": "set_volume", "answer": None,
             "effects": [{"outcome": "confirmed"}], "summary": "Volume is now 40."},
        ]},
        "settings": {"trials": [{"label": "Name", "write": {"confirmed": True},
                                 "restore": {"confirmed": True}, "summary": "Read back."}]},
        "outages": [],
    }
    section.update(over)
    return section


def _attempt(**over):
    attempt = {
        "connected_at": 1.0, "contract": {"counts": {}},
        "traffic": {"sent": 10, "received": 12, "not_captured": False},
        "status_table": {"variables": [
            {"name": "power", "label": "Power", "reported": True},
            {"name": "last_error", "label": "Last Error", "reported": False},
        ]},
    }
    attempt.update(over)
    return attempt


def _variables(missing: int, last_error_reported: bool | None = None):
    rows = [{"name": "power", "label": "Power", "reported": True}]
    rows += [{"name": f"v{i}", "label": f"Value {i}", "reported": False} for i in range(missing)]
    if last_error_reported is not None:
        rows.append({"name": "last_error", "label": None, "reported": last_error_reported})
    return {"variables": rows}


def _trials(*trials):
    return {"trials": list(trials)}


CONNECTED = (True, "Connected.")
ANSWERED = (True, "The device answered (12 received).")
EVERY_VALUE = (True, "Every status value was reported (1 of 2, Last Error aside: the driver "
                     "writes it only when something goes wrong).")
HANDLED = (True, "Nothing came up that the driver could not handle.")
SETTING_BACK = (True, "Every setting written was read back and put back (1).")
SILENT_VALUES = (False, "None of the status values came from the device; any it shows are the "
                        "driver's own.")
COUNTS_AS = (
    "A command counts as confirmed when the person answered Yes, the values it should set "
    "read back, or, for a status query, its value came back."
)
GOOD_START = [CONNECTED, ANSWERED, EVERY_VALUE, HANDLED]


def _not_captured_many_missing():
    section = _section()
    section["attempts"][0]["traffic"] = {"not_captured": True, "sent": 0, "received": 0}
    section["attempts"][0]["status_table"] = _variables(7, last_error_reported=False)
    return section


def _attempts(*attempts):
    return _section(attempts=list(attempts))


CONFIDENCE_CASES = {
    "not connected": (
        _attempts({"connected_at": None}, {}),
        None, [(False, "Did not connect.")],
    ),
    "full": (_section(), "full", [*GOOD_START, (True, "Every command tried was confirmed (2)."),
                                  SETTING_BACK]),
    "traffic not captured, more than five values missing, last_error unlabelled": (
        _not_captured_many_missing(),
        "partial",
        [CONNECTED, (False, "1 of 9 status values were reported. Never reported: Value 0, "
                            "Value 1, Value 2, Value 3, Value 4 and more (last_error aside: the "
                            "driver writes it only when something goes wrong)."),
         HANDLED, (True, "Every command tried was confirmed (2)."), SETTING_BACK],
    ),
    "two values missing, no last_error": (
        _attempts(_attempt(status_table=_variables(2))),
        "partial",
        [CONNECTED, ANSWERED,
         (False, "1 of 3 status values were reported. Never reported: Value 0, Value 1."),
         HANDLED, (True, "Every command tried was confirmed (2)."), SETTING_BACK],
    ),
    "last_error reported": (
        _attempts(_attempt(status_table=_variables(0, last_error_reported=True))),
        "full",
        [CONNECTED, ANSWERED, (True, "Every status value was reported (2 of 2)."), HANDLED,
         (True, "Every command tried was confirmed (2)."), SETTING_BACK],
    ),
    "the device sent nothing back": (
        _attempts(_attempt(traffic={"sent": 7, "received": 0, "not_captured": False})),
        "partial",
        [CONNECTED, (False, "The device sent nothing back to 7 messages."), SILENT_VALUES,
         HANDLED, (True, "Every command tried was confirmed (2)."), SETTING_BACK],
    ),
    "silent with no status table and no sent count": (
        _attempts(_attempt(traffic={"received": 0}, status_table=None)),
        "partial",
        [CONNECTED, (False, "The device sent nothing back to 0 messages."), SILENT_VALUES,
         HANDLED, (True, "Every command tried was confirmed (2)."), SETTING_BACK],
    ),
    "problems counted across every attempt, the last connected one read": (
        _attempts(
            {"connected_at": None, "contract": {"counts": {"unmatched_response": 2}}},
            {"connected_at": 1.0, "contract": {"counts": {"undeclared_state": 1, "x": 0}},
             "traffic": {"sent": 1, "received": 0}},
            _attempt(connected_at=5.0),
        ),
        "partial",
        [CONNECTED, ANSWERED, EVERY_VALUE,
         (False, "The driver could not handle something 3 times (a reply that matched none "
                 "of its rules, a status value it does not list)."),
         (True, "Every command tried was confirmed (2)."), SETTING_BACK],
    ),
    "an error then a Yes for one command; the name falls back to the command": (
        _section(commands=_trials(
            {"label": "Mute", "command": "mute", "error": "refused"},
            {"label": "Mute", "command": "mute", "answer": {"answer": "yes"}},
            {"label": None, "command": "unmute", "answer": {"answer": "yes"}},
            {"command": "reboot", "answer": {"answer": "yes"}},
            {"label": "Reboot", "command": "reboot", "error": "x"},
        )),
        "partial",
        [*GOOD_START,
         (False, f"3 of 4 commands tried were confirmed. Not confirmed: Reboot. {COUNTS_AS}"),
         SETTING_BACK],
    ),
    "a Yes then an error keeps the Yes": (
        _section(commands=_trials(
            {"label": "Power On", "command": "power_on", "answer": {"answer": "yes"}},
            {"label": "Power On", "command": "power_on", "error": "busy"},
        )),
        "full",
        [*GOOD_START, (True, "Every command tried was confirmed (1)."), SETTING_BACK],
    ),
    "seven commands with no sets, none confirmed": (
        _section(commands=_trials(*(
            {"label": f"Cmd {i}", "command": f"c{i}", "answer": {"answer": "no"}}
            for i in range(7)
        ))),
        None,
        [*GOOD_START,
         (False, "0 of 7 commands tried were confirmed. Not confirmed: Cmd 0, Cmd 1, Cmd 2, "
                 f"Cmd 3, Cmd 4 and more. {COUNTS_AS} This driver does not say which value "
                 "Cmd 0, Cmd 1, Cmd 2, Cmd 3, Cmd 4 and more set, so only a Yes can confirm "
                 "them."),
         SETTING_BACK],
    ),
    "six commands with no sets: the sixth is the one more": (
        _section(commands=_trials(*({"label": f"Cmd {i}", "answer": None} for i in range(6)))),
        None,
        [*GOOD_START,
         (False, "0 of 6 commands tried were confirmed. Not confirmed: Cmd 0, Cmd 1, Cmd 2, "
                 f"Cmd 3, Cmd 4 and more. {COUNTS_AS} This driver does not say which value "
                 "Cmd 0, Cmd 1, Cmd 2, Cmd 3, Cmd 4 and more set, so only a Yes can confirm "
                 "them."),
         SETTING_BACK],
    ),
    "five commands with no sets are all named": (
        _section(commands=_trials(*({"label": f"Cmd {i}", "answer": None} for i in range(5)))),
        None,
        [*GOOD_START,
         (False, "0 of 5 commands tried were confirmed. Not confirmed: Cmd 0, Cmd 1, Cmd 2, "
                 f"Cmd 3, Cmd 4. {COUNTS_AS} This driver does not say which value "
                 "Cmd 0, Cmd 1, Cmd 2, Cmd 3 and Cmd 4 set, so only a Yes can confirm them."),
         SETTING_BACK],
    ),
    "two with no sets among others not confirmed": (
        _section(commands=_trials(
            {"label": "Mute", "answer": None},
            {"label": "Unmute", "answer": {"answer": "partly"}},
            {"label": "Input", "answer": None, "effects": [{"outcome": "different"}]},
            {"label": "Query", "answer": None, "query": {"outcome": "no_reply"}},
            {"label": "Bad", "error": "nope"},
        )),
        None,
        [*GOOD_START,
         (False, "0 of 5 commands tried were confirmed. Not confirmed: Mute, Unmute, Input, "
                 f"Query, Bad. {COUNTS_AS} This driver does not say which value Mute and "
                 "Unmute set, so only a Yes can confirm them."),
         SETTING_BACK],
    ),
    "every declared effect, or the query, must read back": (
        _section(commands=_trials(
            {"label": "Input", "answer": None,
             "effects": [{"outcome": "confirmed"}, {"outcome": "already"}]},
            {"label": "Level", "answer": None,
             "effects": [{"outcome": "confirmed"}, {"outcome": "unchanged"}]},
            {"label": "Q", "answer": None, "query": {"outcome": "reported"}, "effects": []},
        )),
        "partial",
        [*GOOD_START,
         (False, f"2 of 3 commands tried were confirmed. Not confirmed: Level. {COUNTS_AS}"),
         SETTING_BACK],
    ),
    "no commands and no settings": (
        _section(commands=None, settings=None),
        None, [*GOOD_START, (False, "No command was tried.")],
    ),
    "settings written that did not round-trip": (
        _section(commands=_trials(), settings=_trials(
            {"write": {"confirmed": True}, "restore": {"confirmed": True}},
            {"write": {"confirmed": True}, "restore": {"confirmed": False}},
            {"write": None, "restore": {"confirmed": True}},
        )),
        None,
        [*GOOD_START, (False, "No command was tried."),
         (False, "2 of 3 settings written were not both read back and put back.")],
    ),
    "one command with no sets, a can't-tell answer": (
        _section(commands=_trials({"label": "Mute", "answer": {"answer": "cant_tell"}})),
        None,
        [*GOOD_START,
         (False, f"0 of 1 commands tried were confirmed. Not confirmed: Mute. {COUNTS_AS} "
                 "This driver does not say which value Mute sets, so only a Yes can confirm "
                 "it."),
         SETTING_BACK],
    ),
}


@pytest.mark.parametrize("name", list(CONFIDENCE_CASES))
def test_the_suggested_confidence_says_every_reason_in_order(name):
    section, level, reasons = CONFIDENCE_CASES[name]
    before = copy.deepcopy(section)
    result = suggested_confidence(section)
    assert result == {
        "level": level,
        "reasons": [{"held": held, "text": text} for held, text in reasons],
    }
    assert section == before  # read, never written


def _report(**over):
    report = {
        "generator": {"openavc_version": "9.9.9"},
        "device": {"reported": {"model": "W-100R", "firmware": "4.0.1.12",
                                "mac": "00:11:22:33:44:55"}},
        "target": {"address": "amp.local", "ip": "192.168.1.75", "hostname": "amp.local",
                   "local_ip": "192.168.1.2"},
    }
    report.update(over)
    return report


def _mixed_section():
    """Reported model and firmware, no connection, every kind of command
    outcome, a setting each way and three outages."""
    section = _section(
        driver={"id": "acme_widget", "source": "catalog"},
        entered={},
        connection=None,
        commands=_trials(
            {"label": "Power On", "answer": {"answer": "yes"},
             "summary": "it turned on at 192.168.1.75"},
            {"label": "Mute", "error": "refused by amp.local", "summary": ""},
            {"label": "Input", "answer": {"answer": "no"}, "summary": "nothing changed"},
            {"label": "Level", "answer": {"answer": "partly"}, "summary": "level moved"},
            {"label": "Clock", "answer": {"answer": "cant_tell"}, "summary": "unclear"},
            {"label": "Query", "answer": None, "query": {"outcome": "reported"},
             "summary": "it reported 10.1.1.1"},
        ),
        settings=_trials(
            {"key": "name", "write": {"confirmed": True}, "restore": {"confirmed": True},
             "summary": "ok"},
            {"label": "Room", "write": {"confirmed": False}, "summary": "not read back"},
        ),
        outages=[
            {"kind": "power_cycle", "summary": "came back after 30 s from 192.168.1.2"},
            {"kind": "cable_pull", "summary": ""},
            {"summary": "no kind"},
        ],
    )
    section["suggested_confidence"] = suggested_confidence(section)
    return section


def test_the_test_report_sorts_every_result_and_takes_every_address_out():
    filled = driver_test_report(_report(), _mixed_section())
    assert filled["fields"] == {
        "title": "[Test report] acme_widget on W-100R",
        "driver": "acme_widget",
        "models": "W-100R",
        "firmware": "4.0.1.12",  # looks like an address, and is left alone
        "transport": "",
        "worked": "\n".join([
            "Connected.",
            "The device answered (12 received).",
            "Every status value was reported (1 of 2, Last Error aside: the driver writes it "
            "only when something goes wrong).",
            "Nothing came up that the driver could not handle.",
            "Power On: It turned on at [address]",
            "Query: It reported [address]",
            "Setting name: ok",
            "Power cycle: came back after 30 s from [address]",
            ": no kind",
        ]),
        "didnt": "\n".join([
            "2 of 6 commands tried were confirmed. Not confirmed: Mute, Input, Level, Clock. "
            f"{COUNTS_AS} This driver does not say which value Input, Level and Clock set, so "
            "only a Yes can confirm them.",
            "1 of 2 settings written were not both read back and put back.",
            "Mute: Not accepted: refused by [address]",
            "Input: Nothing changed (the person said it did not happen)",
            "Level: Level moved (the person said it partly happened)",
            "Setting Room: not read back",
        ]),
        "notes": "From an OpenAVC device audit (OpenAVC 9.9.9). The audit suggests "
                 "confidence: partial.",
    }
    # The link carries every non-empty field, in the form's order, nothing cut.
    query = parse_qs(urlsplit(filled["url"]).query)
    assert list(query) == ["template", "title", "driver", "models", "firmware", "worked",
                           "didnt", "notes"]
    assert {k: v[0] for k, v in query.items() if k != "template"} == {
        k: v for k, v in filled["fields"].items() if v
    }


def test_the_test_report_with_nothing_to_name_the_model_or_level():
    section = _section(
        driver={"id": "acme_widget", "version": None, "source": "catalog",
                "files": [{"name": ""}, {}]},
        connection={"transport": "http", "config": {}},
        entered={"model": "", "firmware": ""},
        commands=None, settings=None,
    )
    section["suggested_confidence"] = {
        "level": None, "reasons": [{"held": False, "text": "Did not connect."}],
    }
    filled = driver_test_report(_report(device={"reported": {}}, generator={}, target={}),
                                section)
    assert filled == {
        "url": "https://github.com/open-avc/openavc-drivers/issues/new?template="
               "driver-test-report.yml&title=%5BTest%20report%5D%20acme_widget%20on%20unknown"
               "%20model&driver=acme_widget&transport=http&didnt=Did%20not%20connect.&notes="
               "From%20an%20OpenAVC%20device%20audit%20%28OpenAVC%20None%29.",
        "fields": {
            "title": "[Test report] acme_widget on unknown model",
            "driver": "acme_widget",
            "models": "",
            "firmware": "",
            "transport": "http",
            "worked": "",
            "didnt": "Did not connect.",
            "notes": "From an OpenAVC device audit (OpenAVC None).",
        },
    }


@pytest.mark.parametrize("source", ["imported", "built_in", None])
def test_no_test_report_for_a_driver_the_catalog_does_not_carry(source):
    section = _section()
    section["driver"]["source"] = source
    assert driver_test_report(_report(), section) is None


def test_a_link_too_long_cuts_what_worked_before_what_did_not():
    section = _section(commands=_trials(
        *({"label": f"Command {n}", "answer": {"answer": "yes"}, "summary": "w" * 150}
          for n in range(30)),
        *({"label": f"Broken {n}", "error": "e" * 150} for n in range(30)),
    ))
    section["suggested_confidence"] = suggested_confidence(section)
    filled = driver_test_report(_report(), section)
    assert len(filled["url"]) == 5960
    query = parse_qs(urlsplit(filled["url"]).query)
    assert query["worked"] == ["(more in the attached report)"]
    didnt = query["didnt"][0].split("\n")
    assert didnt == filled["fields"]["didnt"].split("\n")[:28] + ["(more in the attached report)"]
    # The record keeps everything the link could not.
    assert filled["fields"]["worked"].count("\n") == 35
    assert filled["fields"]["didnt"].count("\n") == 30
    assert len(filled["url"]) <= TEST_REPORT_URL_MAX


# ---------------------------------------------------------------------------
# What the driver test could not see
# ---------------------------------------------------------------------------


def _limit_attempt(not_captured=False, truncated=None, callbacks=(), counts=None):
    observer = SimpleNamespace(truncated_at=truncated, traffic=[], event_counts=dict(counts or {}))
    return SimpleNamespace(
        to_dict=lambda: {"traffic": {"not_captured": not_captured}},
        sandbox=SimpleNamespace(observer=observer, push_callbacks=lambda: list(callbacks)),
    )


def _outage(used):
    return SimpleNamespace(ping={"used": used}, to_dict=lambda: {"kind": "cable_pull"})


def _run(index, name, listens, outages=()):
    return SimpleNamespace(
        index=index,
        choice=SimpleNamespace(identity={"name": name} if name else {}, driver_id="acme_x"),
        listens=listens, outages=list(outages),
    )


LIMIT_CASES = {
    "no runs at all": (SimpleNamespace(runs=None), [
        {"id": "no_driver_tested",
         "text": "No driver was tested; this report covers the network check only."},
    ]),
    "the person said there is no driver": (SimpleNamespace(runs=[], no_driver=True), [
        {"id": "no_driver_tested",
         "text": "The person said there is no driver for this device yet, so none was tested; "
                 "this report covers the network check only."},
    ]),
    "one chosen and not connected, one with nothing to say": (
        SimpleNamespace(runs=[_run(0, "Acme One", []), _run(1, None, [_limit_attempt()])]),
        [{"id": "driver_not_connected", "run": 0,
          "text": "Acme One was chosen, but the audit did not connect it."}],
    ),
    "every limit a connected run can have, once each": (
        SimpleNamespace(
            runs=[_run(0, "Acme Two", [
                _limit_attempt(True, 2.0, counts={"a": 50, "b": 51}),
                _limit_attempt(True, None, counts={"a": 1}),
            ], outages=[_outage(True), _outage(False), _outage(False)])],
            log=SimpleNamespace(dropped=1, keep=5000),
        ),
        [
            {"id": "traffic_not_captured", "run": 0,
             "text": "Acme Two manages its own connection, so its traffic was not captured."},
            {"id": "outage_by_marks", "run": 0,
             "text": "The device does not answer ping, so the times in Acme Two's cable pull "
                     "test come from the buttons pressed during it."},
            {"id": "traffic_truncated", "run": 0,
             "text": "Acme Two's traffic passed 20 MB, so the report keeps the first 20 MB "
                     "of it."},
            {"id": "events_capped", "run": 0,
             "text": "Where Acme Two did the same thing wrong more than 50 times, the report "
                     "keeps the first 50 in full and counts the rest."},
            {"id": "log_capped",
             "text": "OpenAVC wrote more than 5000 log lines about this audit; log.txt keeps "
                     "the first 5000."},
        ],
    ),
    "exactly as many events as are kept is not capped": (
        SimpleNamespace(runs=[_run(0, "Acme", [_limit_attempt(counts={"a": 50})])]),
        [],
    ),
    "a log that dropped nothing": (
        SimpleNamespace(runs=[_run(0, "Acme", [_limit_attempt()])],
                        log=SimpleNamespace(dropped=0, keep=9)),
        [],
    ),
}


@pytest.mark.parametrize("name", list(LIMIT_CASES))
def test_the_driver_limits_are_every_one_in_order(name):
    session, expected = LIMIT_CASES[name]
    assert _driver_limits(session) == expected


def test_a_push_limit_is_said_once_per_run(monkeypatch):
    url = "http://192.168.1.20:8080/api/push/audit-x"
    run = _run(0, "Acme Speaker", [_limit_attempt(callbacks=[url]),
                                   _limit_attempt(callbacks=["http://other/"])])
    session = SimpleNamespace(runs=[run, _run(1, "Acme Quiet", [_limit_attempt()])])
    limits = _driver_limits(session)
    assert [(x["id"], x["run"], x["callbacks"]) for x in limits] == [
        ("push_never_arrived", 0, [url]),
    ]


# ---------------------------------------------------------------------------
# A discovery signal as the evidence lines say it
# ---------------------------------------------------------------------------


def _sig(data=None, source=None):
    out = {}
    if data is not None:
        out["evidence"] = {"data": data}
    if source is not None:
        out["source"] = source
    return out


def _vendor(**data):
    return _sig({"kind": "vendor_string", "value": "Acme", **data})


NAMES = {"acme_x": {"name": "Acme X"}, "acme_y": "not a dict"}

SIGNAL_CASES = [
    (_sig({"kind": "mdns", "source_id": "_acme._tcp"}), "mDNS announcement on _acme._tcp"),
    (_sig({"kind": "mdns"}), "mDNS announcement on an unknown service"),
    (_sig({"kind": "ssdp", "source_id": "urn:acme:device:Widget:1"}),
     "SSDP announcement for urn:acme:device:Widget:1"),
    (_sig({"kind": "ssdp"}), "SSDP announcement for an unknown device type"),
    (_sig({"kind": "amx_ddp", "make": "Acme", "model": "W1"}), "AMX DDP beacon: Acme W1"),
    (_sig({"kind": "amx_ddp", "make": "Acme", "model": None}), "AMX DDP beacon: Acme"),
    (_sig({"kind": "amx_ddp"}), "AMX DDP beacon"),
    (_sig({"kind": "broadcast", "port": 9131, "matched_pattern": "ACME.*"}),
     "UDP probe on port 9131 matched ACME.*"),
    (_sig({"kind": "broadcast", "port": "9131"}), "UDP probe matched"),
    (_sig({"kind": "probe", "port": 23, "response": {"text": "\x00\x01Welcome  to  Acme\r\n"}}),
     'TCP probe on port 23 returned "Welcome to Acme"'),
    (_sig({"kind": "probe", "port": 23, "response": {"text": 'say "hi"'}}),
     "TCP probe on port 23 returned 'say \"hi\"'"),
    (_sig({"kind": "probe", "port": 23, "response": {"text": "A" * 100}}),
     f'TCP probe on port 23 returned "{"A" * 80}"'),
    (_sig({"kind": "probe", "port": 23, "matched_pattern": "^\\x02",
           "response": {"text": "\x02a\x03\x04b\x05", "hex": "02610304"}}),
     "TCP probe on port 23 matched ^\\x02"),
    (_sig({"kind": "probe", "response": {"text": "\x02\x03", "hex": "0203" * 50}}),
     "TCP probe returned hex " + " ".join(["02 03"] * 20)),
    (_sig({"kind": "probe", "response": {"text": "a\x01b"}}), 'TCP probe returned "a b"'),
    (_sig({"kind": "probe", "response": {"text": "a\x01\x01b"}}), "TCP probe answered"),
    (_sig({"kind": "probe", "port": 23}), "TCP probe on port 23 answered"),
    (_sig({"kind": "probe", "port": 1, "response": {"text": "A"}}), "TCP probe on port 1 answered"),
    (_sig({"kind": "oui", "value": "00:11:22", "vendor": "Acme"}),
     "MAC address prefix 00:11:22 belongs to Acme"),
    (_sig({"kind": "oui", "value": "00:11:22"}), "MAC address prefix 00:11:22 seen"),
    (_sig({"kind": "hostname", "value": "acme-1", "matched_pattern": "^acme"}),
     "Hostname pattern ^acme matched acme-1"),
    (_sig({"kind": "hostname", "value": "acme-1"}), "Hostname acme-1 observed"),
    (_sig({"kind": "snmp_pen", "value": 9999}), "SNMP enterprise number 9999"),
    (_vendor(from_driver=True, supplied_by="acme_x"),
     'Manufacturer "Acme" named by the Acme X driver when its probe matched'),
    (_vendor(from_driver=True, supplied_by="acme_y"),
     'Manufacturer "Acme" named by the driver when its probe matched'),
    (_vendor(from_driver=True), 'Manufacturer "Acme" named by the driver when its probe matched'),
    (_vendor(source_probe_id="greeting:23"), 'Manufacturer "Acme" named in the greeting on port 23'),
    (_vendor(source_probe_id="http_server:80"),
     'Manufacturer "Acme" named by the web server on port 80'),
    (_vendor(source_probe_id="ssdp_server"), 'Manufacturer "Acme" named in the SSDP server name'),
    (_vendor(from_kind="ssdp"), 'Manufacturer "Acme" named in the SSDP description'),
    (_vendor(from_kind="mdns"), 'Manufacturer "Acme" named in an mDNS announcement'),
    (_vendor(from_kind="amx_ddp"), 'Manufacturer "Acme" named in the AMX DDP beacon'),
    (_vendor(source_probe_id="greeting:"), 'Manufacturer "Acme" named in a probe reply'),
    (_sig({"kind": "open_port", "value": 4999}), "Port 4999 is open"),
    (_sig({}, source="mdns:_acme._tcp"), "mDNS announcement on _acme._tcp"),
    (_sig(None, source="ssdp:urn:x"), "SSDP announcement for urn:x"),
    (_sig(None, source="oui:00:11:22"), "MAC address prefix 00:11:22 seen"),
    (_sig(None, source="snmp_pen:9999"), "SNMP enterprise number 9999"),
    (_sig(None, source="hostname:acme-1"), "Hostname acme-1 observed"),
    (_sig(None, source="port_open:23"), "Port 23 is open"),
    (_sig(None, source="mdns:"), "mdns:"),
    (_sig(None, source="probe:custom_acme_tcp"), "A TCP identification check answered"),
    (_sig(None, source="broadcast:x"), "A UDP identification check answered"),
    (_sig(None, source="weird"), "weird"),
    (_sig(None), "A signal"),
    (_sig({"kind": "new_thing"}, source="new_thing:x"), "new_thing:x"),
]


@pytest.mark.parametrize("sig,text", SIGNAL_CASES)
def test_each_signal_reads_as_the_evidence_lines_say_it(sig, text):
    assert _signal_text(sig, NAMES) == text


def test_a_signal_without_the_verdict_s_names_names_no_driver():
    sig = _vendor(from_driver=True, supplied_by="acme_x")
    assert _signal_text(sig) == 'Manufacturer "Acme" named by the driver when its probe matched'
