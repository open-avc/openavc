"""What a device audit report leaves out, function by function.

The zip-level tests in test_audit_report.py prove a typed secret appears in
no file. These pin the pieces underneath on crafted inputs: which serial
numbers a record is found to hold, which redactions a session asks for and in
what order, which names the catalog publishes, and how the redactor treats a
short secret, a hex field, a published string and a name a secret would cut
into.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from openavc.audit import redaction
from openavc.audit.redaction import (
    REDACTED,
    SERIAL_REMOVED,
    Redaction,
    Redactor,
    serials_in as _serials_in,
    _strings_in,
    catalog_names,
    driver_public_strings,
    public_strings,
    redactions_for,
    serial_numbers,
    session_redactor,
)


def _footprint(device=None, record=None):
    return NS(device=device, to_dict=lambda: record if record is not None else {})


def _attempt(live=None, changes=None, secrets=()):
    return NS(
        to_dict=lambda: live or {},
        changes=changes,
        sandbox=NS(observer=NS(secrets=set(secrets))),
    )


def _run(index=0, identity=None, driver_id="acme_widget", listens=(), commands=None,
         settings=None, secrets=None):
    return NS(
        index=index,
        choice=NS(identity=identity, driver_id=driver_id),
        listens=list(listens),
        commands=commands,
        settings=settings,
        secrets=secrets,
    )


def _session(footprint=None, check=None, runs=(), communities=(), tester=None):
    return NS(
        footprint=footprint,
        check=check,
        runs=list(runs),
        options=NS(snmp_communities=list(communities)),
        tester=tester,
    )


# ---------------------------------------------------------------- serial numbers

def test_a_record_is_searched_for_serial_numbers_by_key_name_and_oid():
    tree = {
        "serial_number": "SN-1",
        "cert": {"issuer": "CN=Acme CA", "serial": "CERT-9", "serialNumber": "CERT-10"},
        "walk": [
            {"oid": "1.3.6.1.2.1.47.1.1.1.1.11.1", "value": "OID-SN"},
            {"oid": "1.3.6.1.2.1.47.1.1.1.1.11.2", "value": 5},
            {"oid": "1.3.6.1.2.1.1.1.0", "value": "not-a-serial"},
        ],
        "status": [
            {"name": "serialNumber", "value": 12345},
            {"name": "Serial", "value": True},
            {"key": "device.x.serial", "now": "NOW-SN", "before": None, "other": "OTHER"},
            {"state": "device.x.SERIAL", "old": "OLD-SN", "new": 7.5},
            {"state_key": "device.x.power", "value": "on"},
            {"name": 5, "value": "NOT-NAMED"},
        ],
        "Serial No": ["LIST-SN"],
        "nested": [[{"serial": "DEEP-SN"}], ({"SERIAL": 0},)],
        1: "numeric key",
        "flag_serial": False,
    }
    out: set[str] = set()
    _serials_in(tree, out)
    # A float is not read as a serial (7.5 under "new" above).
    assert out == {"SN-1", "OID-SN", "12345", "NOW-SN", "OLD-SN", "DEEP-SN", "0"}


def test_a_certificate_keeps_its_own_serial_and_a_plain_value_is_not_one():
    out: set[str] = set()
    _serials_in({"issuer": "x", "serial": "CERT"}, out)
    _serials_in("serial", out)
    _serials_in(["serial", 1, None], out)
    assert out == set()


def _heard_everywhere_session():
    footprint = _footprint(
        device=NS(serial_number="DEV-SN-1"),
        record={"snmp": {"values": {"entPhysicalSerialNum": "SNMP-SN-22"}}, "short": {"serial": "ab"}},
    )
    listen = _attempt(
        live={
            "status_table": {"variables": [{"name": "serial_number", "value": "LISTEN-SN"}]},
            "traffic": {"entries": [{"serial": "TRAFFIC-SN"}]},
        },
        changes=[{"key": "device.x.serial", "now": "CHANGE-SN"}],
    )
    commands = NS(to_dict=lambda: {"trials": [{"serial": "CMD-SN"}]})
    settings = NS(to_dict=lambda: {"trials": [{"serial": "   "}]})
    return _session(
        footprint=footprint,
        runs=[_run(listens=[listen], commands=commands), _run(index=1, settings=settings)],
    )


def test_every_serial_number_the_audit_heard_is_found_but_not_in_traffic():
    assert serial_numbers(_heard_everywhere_session()) == {
        "DEV-SN-1", "SNMP-SN-22", "LISTEN-SN", "CHANGE-SN", "CMD-SN",
    }


@pytest.mark.parametrize("session, expected", [
    (_session(), set()),
    (_session(check=NS(status="idle", footprint=_footprint(record={"serial": "IDLE-SN"}))), set()),
    (_session(check=NS(status="running", footprint=_footprint(record={"serial": "RUN-SN"}))),
     {"RUN-SN"}),
    (_session(footprint=_footprint(device=NS(serial_number=""), record={"x": 1})), set()),
    (_session(footprint=_footprint(device=NS(serial_number=4242))), {"4242"}),
    (_session(runs=[_run(listens=[_attempt(changes=None)])]), set()),
])
def test_serial_numbers_come_from_the_check_in_progress_or_finished(session, expected):
    assert serial_numbers(session) == expected


# ---------------------------------------------------------------- what a session asks to leave out

def test_a_session_asks_for_its_communities_serials_and_secrets_in_order():
    session = _heard_everywhere_session()
    session.options.snmp_communities = ["public", "", "comm-secret-1", "zz-comm"]
    session.tester = {"leave_out_serial": True}
    session.runs[0].secrets = ["pw-run-0", ""]
    session.runs[0].listens[0].sandbox.observer.secrets = {"tok-listen"}
    session.runs[1].secrets = None
    assert [(r.value, r.replacement) for r in redactions_for(session)] == [
        ("comm-secret-1", REDACTED),
        ("zz-comm", REDACTED),
        ("CHANGE-SN", SERIAL_REMOVED),
        ("CMD-SN", SERIAL_REMOVED),
        ("DEV-SN-1", SERIAL_REMOVED),
        ("LISTEN-SN", SERIAL_REMOVED),
        ("SNMP-SN-22", SERIAL_REMOVED),
        ("pw-run-0", REDACTED),
        ("tok-listen", REDACTED),
    ]


def test_serials_stay_unless_the_tester_asked_and_a_bare_session_asks_for_nothing():
    session = _heard_everywhere_session()
    assert redactions_for(session) == []
    session.tester = {"leave_out_serial": False}
    assert redactions_for(session) == []
    bare = NS(options=NS(snmp_communities=[]))
    assert redactions_for(bare) == []


# ---------------------------------------------------------------- published names and strings

def test_the_catalog_names_are_each_drivers_and_every_one_the_verdict_names():
    identity = {
        "id": "acme_widget", "name": "Acme Widget", "manufacturer": "Acme", "version": "1.0",
        "files": [{"name": "acme_widget.avcdriver"}, {"name": 5}, {"name": ""}, {}],
    }
    verdict = {
        "catalog": {"driver_id": "CATALOG-ONLY"},
        "identification": {"driver_id": "acme_widget", "driver_name": "Acme Widget Pro"},
        "drivers": {"acme_other": {"manufacturer": "Acme Corp", "name": "Not taken"}},
        "checks": {"acme_widget": [{"drivers": ["acme_third", 5], "manufacturer": 9}]},
        "candidates": [{"driver_id": "acme_fourth"}, "acme_plain"],
    }
    session = _session(
        footprint=_footprint(record={"verdict": verdict}),
        runs=[_run(index=1, identity=identity), _run(identity={"id": 5, "name": ""})],
    )
    assert catalog_names(session) == {
        "acme_widget", "Acme Widget", "Acme", "Acme Widget Pro",
        "acme_widget.avcdriver", "driver/acme_widget.avcdriver",
        "driver/run-2/acme_widget.avcdriver",
        "Acme Corp", "acme_third", "acme_fourth",
    }


def test_catalog_names_without_a_check_or_a_verdict():
    assert catalog_names(_session(runs=[_run(identity={})])) == set()
    assert catalog_names(_session(footprint=_footprint(record={}))) == set()


def test_every_string_in_a_value_is_collected_keys_included():
    out: set[str] = set()
    _strings_in({"a": ["b", ("c", {"d": {"e"}})], 1: frozenset({"f"}), "g": 2, "h": None}, out)
    assert out == {"a", "b", "c", "d", "e", "f", "g", "h"}


class _Published:
    DRIVER_INFO = {"id": "acme_widget", "name": "Acme Widget", "commands": {"power_on": {"label": ""}}}


@pytest.fixture
def published(monkeypatch):
    import openavc.drivers.registry as registry

    monkeypatch.setattr(registry, "get_driver_class", lambda driver_id: _Published)


def test_a_drivers_public_strings_only_when_every_file_is_the_catalogs(published):
    matching = {"files": [{"matches_catalog": True}, {"matches_catalog": True}]}
    assert driver_public_strings("acme_widget", matching) == {
        "id", "acme_widget", "name", "Acme Widget", "commands", "power_on", "label",
    }
    assert driver_public_strings("acme_widget", {"files": [{"matches_catalog": True}, {}]}) == set()
    assert driver_public_strings("acme_widget", {"files": []}) == set()
    assert driver_public_strings("acme_widget", None) == set()


def test_a_sessions_public_strings_are_every_run_drivers(published):
    runs = [_run(identity={"files": [{"matches_catalog": True}]}), _run(identity={"files": []})]
    assert public_strings(_session(runs=runs)) == {
        "id", "acme_widget", "name", "Acme Widget", "commands", "power_on", "label",
    }
    assert public_strings(NS()) == set()


# ---------------------------------------------------------------- the redactor

def test_a_secret_shorter_than_the_minimum_is_never_looked_for():
    redactor = Redactor([Redaction("xy"), Redaction("xyz")])
    assert redactor.text("xy xyz xyzw") == f"xy {REDACTED} {REDACTED}w"


def test_a_secret_of_hex_digits_is_not_cut_out_of_a_longer_hex_run():
    redactor = Redactor([Redaction("abc")])
    assert redactor.text("abc abcd 0abc .abc abc!") == f"{REDACTED} abcd 0abc .abc {REDACTED}!"


def test_a_numeric_secret_is_not_cut_out_of_a_longer_number():
    redactor = Redactor([Redaction("1234")])
    assert redactor.text("1234 12345 01234 1.1234 PIN1234") == (
        f"{REDACTED} 12345 01234 1.1234 PIN{REDACTED}"
    )


def test_a_hex_field_is_searched_only_for_the_secrets_hex_forms():
    secret = "1234"
    redactor = Redactor([Redaction(secret)])
    hexed = secret.encode().hex()
    tree = {
        "hex": f"00{secret}{hexed}", "sha256": hexed.upper(), "text": f"x{secret}y{hexed}",
        "catalog_sha256": f"ff{secret}ff",
    }
    assert redactor.tree(tree) == {
        "hex": f"00{secret}{REDACTED.encode().hex()}",
        # Digits only, so the upper-case hex form is the lower-case one, and
        # the first form seen gives the replacement.
        "sha256": REDACTED.encode().hex(),
        # A hex form is replaced by the replacement's own hex, wherever it is.
        "text": f"x{REDACTED}y{REDACTED.encode().hex()}",
        "catalog_sha256": f"ff{secret}ff",
    }


def test_a_published_string_and_a_name_a_secret_cuts_into_are_kept():
    redactor = Redactor(
        [Redaction("widget")], public={"widget"}, names={"acme_widget", "", "unrelated"},
    )
    tree = {
        "widget": ["widget", "acme_widget", "my widget acme_widget", ("widget",), 7, None],
        "the widget": "unrelated widget",
    }
    assert redactor.tree(tree) == {
        "widget": ["widget", "acme_widget", f"my {REDACTED} acme_widget", ["widget"], 7, None],
        f"the {REDACTED}": f"unrelated {REDACTED}",
    }


def test_only_a_name_a_secret_cuts_into_is_shielded():
    """A name the secret does not cut into is not split out, so a secret
    running across its edge is still found."""
    redactor = Redactor([Redaction("tedwid")], names={"unrelated"})
    assert redactor.text("unrelatedwidget") == f"unrela{REDACTED}get"


def test_a_session_redactor_takes_the_sessions_redactions_and_names(published):
    session = _session(
        communities=["comm-secret-1"],
        runs=[_run(identity={"id": "acme_widget", "files": [{"matches_catalog": True}]})],
    )
    redactor = session_redactor(session)
    assert redactor.text("comm-secret-1 acme_widget") == f"{REDACTED} acme_widget"
    assert redactor.tree({"name": "Acme Widget"}) == {"name": "Acme Widget"}
    assert isinstance(redactor, redaction.Redactor)
