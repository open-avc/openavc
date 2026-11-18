"""The report contract: each kind's version, the fields its readers rely on, the caps.

``openavc/support/report_contract.py`` declares them once. These hold the two
builders to it (a problem report and a whole device audit, built by the
production code), pin each version to its fields' shape so a field changed
without a ``report_version`` bump fails here, and check that the builders, the
upload door and the generated contract all read the one table.
"""

from __future__ import annotations

import asyncio
import copy

import pytest

from openavc.audit import report as audit_report
from openavc.audit.commands import DONE, commands_for
from openavc.audit.driver_choice import driver_identity
from openavc.audit.report import build_report
from openavc.cloud import contract_gen, wire_tables
from openavc.core.device_traffic import get_traffic_recorder
from openavc.discovery.result import Evidence, SignalTier
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.support import problem_report as pr
from openavc.support import report_contract as rc
from openavc.support import report_upload
from openavc.utils.log_redaction import get_secret_registry
from tests.test_audit_commands import DRIVER, WINDOW, _connected, _fake_device, _until
from tests.test_audit_report import _footprint
from tests.test_problem_report import _context, engine  # noqa: F401  (the fixture)


def _problem(engine) -> dict:  # noqa: F811
    snapshot = pr.Snapshot(engine, "hall_widget", catalog=None)
    report, _ = pr.build_report(
        snapshot, message="It stopped answering", name="Pat", email="pat@example.com",
        context=_context(),
    )
    return report


def _footprint_seen_everywhere():
    """The network check with every section a reader reads holding something."""
    fp = _footprint("127.0.0.1")
    fp.mdns = {
        "hostname": "widget-3000.local", "address_name": "widget-3000.local",
        "services": [{"service_type": "_acme-widget._tcp.local.", "txt": {"model": "W3000"}}],
    }
    fp.certificates = {443: {"subject": {"commonName": "widget-3000"}}}
    fp.ssdp.update({"manufacturer": "Acme", "model_name": "Widget 3000"})
    fp.evidence = [Evidence(SignalTier.PASSIVE_LISTENER, "mdns:_acme-widget._tcp.local.",
                            {"txt": {"model": "W3000"}})]
    fp.verdict["checks"]["acme_widget"].append({
        "kind": "probe", "declared": "TCP port 23, sends WHO?", "status": "matched",
        "detail": "The device answered.",
        "probes": [{"matched": True, "reply": {"hex": "573330", "text": "W30"}}],
    })
    return fp


async def _audit() -> dict:
    """A whole device audit: the network check, one driver connected to a
    loopback device, its status queries and one command."""
    _DRIVER_REGISTRY["acme_commands"] = create_configurable_driver_class(DRIVER)
    server, port = await _fake_device()
    try:
        session, run, _ = await _connected(port)
        run.choice.identity = driver_identity("acme_commands", catalog=None)
        run.choice.identity["files"] = [{
            "name": "acme_commands.avcdriver", "sha256": "ab" * 32, "catalog_sha256": None,
            "matches_catalog": None,
        }]
        session.footprint = _footprint_seen_everywhere()
        session.tester = {"name": "Pat", "company": "Acme AV", "email": "pat@example.com",
                          "notes": "Bench unit"}
        try:
            commands = commands_for(session, run, **WINDOW)
            await commands.run_queries()
            await _until(lambda: commands.batch["status"] == "done")
            trial = await commands.send("set_volume", {"level": 30})
            await _until(lambda: trial.status == DONE)
        finally:
            await run.stop()
        return build_report(session)
    finally:
        server.close()
        _DRIVER_REGISTRY.pop("acme_commands", None)
        get_traffic_recorder().clear()
        get_secret_registry().clear()


@pytest.fixture(scope="module")
def audit() -> dict:
    return asyncio.run(_audit())


# --- The builders hold their kind's fields ---


def test_a_problem_report_holds_every_field_its_readers_rely_on(engine):  # noqa: F811
    assert rc.field_problems("problem_report", _problem(engine)) == []


def test_a_device_audit_holds_every_field_its_readers_rely_on(audit):
    assert rc.field_problems("device_audit", audit) == []


def test_the_audit_fixture_reaches_every_field_it_can(audit):
    """An empty list or a null section holds anything, so the audit built
    above has to put something in every place a reader reads. What it leaves
    out is what the passes it does not run write: a front-panel answer, the
    power and cable checks, the settings check."""
    sometimes = {
        "drivers[].commands.trials[].answer.answer",
        "drivers[].outages[].kind", "drivers[].outages[].summary",
        "drivers[].settings.trials[].key", "drivers[].settings.trials[].summary",
    }
    unreached = []
    for f in rc.KINDS["device_audit"].fields:
        toks = rc.tokens(f.path)
        holders = [h for _, h in rc._holders(audit, toks[:-1], "") if isinstance(h, dict)]
        if not any(toks[-1] in h for h in holders):
            unreached.append(f.path)
    assert sorted(unreached) == sorted(sometimes)


def test_the_builders_stamp_the_contracts_version():
    assert audit_report.REPORT_VERSION == rc.report_version("device_audit")
    assert pr.REPORT_VERSION == rc.report_version("problem_report")
    assert audit_report.REPORT_KIND == "device_audit" and pr.REPORT_KIND == "problem_report"


def test_the_first_recorded_shape_ships_with_the_wire_contract():
    assert rc.FIRST_RECORDED_RELEASE == wire_tables.FIRST_CONTRACT_RELEASE


def test_the_upload_door_holds_the_contracts_caps_and_kinds():
    assert report_upload.MAX_REPORT_BYTES == rc.MAX_REPORT_BYTES
    assert report_upload.MAX_REPORT_JSON_BYTES == rc.MAX_REPORT_JSON_BYTES
    assert report_upload.KINDS == tuple(rc.KINDS)


# --- A field changed without a bump fails ---


@pytest.mark.parametrize("kind", sorted(rc.KINDS))
def test_each_version_is_pinned_to_its_fields(kind):
    entry = rc.KINDS[kind]
    current = entry.versions[-1]
    assert current.shape == rc.shape_digest(kind), (
        f"The fields of {kind} changed but its report_version is still {current.version}: "
        f"bump report_version in openavc/support/report_contract.py (add version "
        f"{current.version + 1}, since the release it ships in, with shape "
        f"{rc.shape_digest(kind)!r}) so a reader can tell the new shape from the old. "
        f"When version {current.version} has not shipped yet (its since, "
        f"{current.since}, is the coming release), set its shape to that instead."
    )


@pytest.mark.parametrize("kind", sorted(rc.KINDS))
def test_versions_count_up_and_only_the_first_recorded_no_shape(kind):
    versions = rc.KINDS[kind].versions
    assert [v.version for v in versions] == list(range(1, len(versions) + 1))
    assert versions[0].shape is None and all(v.shape for v in versions[1:])
    assert versions[0].since == rc.KINDS[kind].since


def test_the_checker_catches_what_a_builder_could_get_wrong(audit):
    dropped = copy.deepcopy(audit)
    del dropped["drivers"][0]["driver"]["id"]
    retyped = copy.deepcopy(audit)
    retyped["session"]["status"] = 3
    added = {**copy.deepcopy(audit), "report_saved_at": 1.0}
    assert rc.field_problems("device_audit", dropped) == ["drivers[0].driver.id: missing"]
    assert rc.field_problems("device_audit", retyped) == [
        "session.status: int, declared string",
    ]
    assert rc.field_problems("device_audit", added) == [
        "report_saved_at: a top-level field the report contract does not declare",
    ]


# --- The table itself ---


@pytest.mark.parametrize("kind", sorted(rc.KINDS))
def test_every_field_is_well_formed(kind):
    entry = rc.KINDS[kind]
    paths = [f.path for f in entry.fields]
    assert len(paths) == len(set(paths)), "a field declared twice"
    top = set(rc.top_level(kind))
    for f in entry.fields:
        toks = rc.tokens(f.path)
        assert toks[-1] not in ("[]", "*"), f"{f.path}: a path ends with a field's name"
        assert toks[0] in top, f"{f.path}: its top-level field is not declared"
        assert set(f.readers) <= set(rc.READERS), f.path
        assert all(t == "any" or t in rc._JSON_TYPES for t in f.type.split("|")), f.path
        contract_gen._version(rc.field_since(entry, f))


@pytest.mark.parametrize("kind", sorted(rc.KINDS))
def test_the_generated_example_holds_its_kinds_fields(kind):
    example = contract_gen.report_example(rc.KINDS[kind])
    assert rc.field_problems(kind, example) == []
    assert example["report_kind"] == kind
    assert example["report_version"] == rc.report_version(kind)
