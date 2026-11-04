"""JSON-body response mode + on_connect query dispatch for ConfigurableDriver.

Platform features, exercised with an invented HTTP/JSON device (no real product):
  - JSON responses parse one body and populate many state vars at once (the
    engine no longer stops at the first matching rule for JSON bodies).
  - on_connect resolves command names and feeds HTTP responses to the matcher,
    the same way poll() does.
"""

import pytest

from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.inline_protocol import _normalize_one_response

# Invented JSON-over-HTTP device. One status endpoint returns several fields in
# a single JSON body — the shape that broke under first-match-wins regex rules.
JSON_DEFINITION = {
    "id": "acme_gizmo",
    "name": "Acme Gizmo",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "http",
    "default_config": {"host": "", "port": 80},
    "state_variables": {
        "in_use": {"type": "boolean"},
        "sessions": {"type": "integer"},
        "status_text": {"type": "string"},
        "mode": {"type": "string"},
        "depth": {"type": "integer"},
        "ratio": {"type": "number"},
    },
    "commands": {
        "query_status": {"label": "Query Status", "method": "GET", "path": "/status"},
    },
    "responses": [
        {
            "json": True,
            "set": {
                "in_use": {"key": "inUse", "type": "boolean"},
                "sessions": {"key": "sessions", "type": "integer"},
                "status_text": {"key": "status"},          # type from state var
                "ratio": {"key": "ratio", "type": "number"},
                "depth": {"key": "nested.depth", "type": "integer"},
                "mode": {"key": "modeCode", "map": {"1": "Extended", "2": "Clone"}},
            },
        },
    ],
    "on_connect": ["query_status"],
    "polling": {"queries": ["query_status"]},
}


@pytest.fixture
def driver(state, events):
    state.set_event_bus(events)
    cls = create_configurable_driver_class(JSON_DEFINITION)
    return cls("gizmo1", {"host": "127.0.0.1", "port": 80}, state, events)


# ── Gap A: multi-field JSON ──────────────────────────────────────────────────

async def test_json_body_populates_every_field(driver):
    """One JSON body sets all declared fields — not just the first rule."""
    body = (
        '{"inUse":true,"sessions":3,"status":"Ok","ratio":0.5,'
        '"modeCode":2,"nested":{"depth":7}}'
    )
    await driver.on_data_received(body.encode("utf-8"))
    assert driver.get_state("in_use") is True       # native bool preserved
    assert driver.get_state("sessions") == 3         # native int preserved
    assert driver.get_state("status_text") == "Ok"
    assert driver.get_state("ratio") == 0.5
    assert driver.get_state("depth") == 7            # nested dot path
    assert driver.get_state("mode") == "Clone"       # value map applied


async def test_json_native_bool_not_stringified(driver):
    """A JSON false stays a real bool, not the string 'False'."""
    await driver.on_data_received(b'{"inUse":false,"sessions":0}')
    assert driver.get_state("in_use") is False
    assert driver.get_state("sessions") == 0


async def test_json_absent_key_leaves_prior_value(driver):
    """A body missing a key doesn't clobber what an earlier body set."""
    await driver.on_data_received(b'{"sessions":5}')
    assert driver.get_state("sessions") == 5
    # status absent here — sessions stays put, status_text stays at its seed.
    await driver.on_data_received(b'{"status":"Warning"}')
    assert driver.get_state("sessions") == 5
    assert driver.get_state("status_text") == "Warning"


async def test_json_invalid_body_is_ignored(driver):
    """Non-JSON / non-object bodies don't crash and don't change state."""
    await driver.on_data_received(b"not json at all")
    await driver.on_data_received(b'["a","list"]')
    assert driver.get_state("sessions") is None
    assert driver.get_state("in_use") is None


async def test_json_single_element_array_body_unwrapped(driver):
    """A [{...}] body is unwrapped — several protocols wrap every reply in a
    single-element array (one unit per datagram) and previously read nothing."""
    body = '[{"inUse":true,"sessions":4,"status":"Ok","nested":{"depth":2}}]'
    await driver.on_data_received(body.encode("utf-8"))
    assert driver.get_state("in_use") is True
    assert driver.get_state("sessions") == 4
    assert driver.get_state("status_text") == "Ok"
    assert driver.get_state("depth") == 2


async def test_json_multi_element_array_still_ignored(driver):
    """A multi-element array is ambiguous — no unwrap, state untouched."""
    await driver.on_data_received(b'[{"sessions":9},{"sessions":8}]')
    assert driver.get_state("sessions") is None


async def test_json_falls_through_to_regex():
    """With both json and regex rules, a non-JSON line still matches regex."""
    definition = dict(JSON_DEFINITION, id="acme_mixed")
    definition["state_variables"] = {
        **JSON_DEFINITION["state_variables"],
        "banner": {"type": "string"},
    }
    definition["responses"] = JSON_DEFINITION["responses"] + [
        {"match": r"^MODEL (.+)$", "mappings": [{"group": 1, "state": "banner"}]},
    ]
    cls = create_configurable_driver_class(definition)

    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    events = EventBus()
    st = StateStore()
    st.set_event_bus(events)
    drv = cls("mixed1", {"host": "127.0.0.1"}, st, events)

    await drv.on_data_received(b"MODEL GZ-9")          # not JSON -> regex
    assert drv.get_state("banner") == "GZ-9"
    await drv.on_data_received(b'{"sessions":2}')      # JSON -> json rule
    assert drv.get_state("sessions") == 2


def test_normalize_json_response_row():
    """A friendly json editor row normalizes to a json: true set-rule."""
    out = _normalize_one_response(
        {"mode": "json", "field": "inUse", "state": "in_use", "type": "boolean"}
    )
    assert out == {"json": True, "set": {"in_use": {"key": "inUse", "type": "boolean"}}}


# ── require: scoping json rules to matching bodies ─────────────────────────


def _scoped_driver():
    """Invented device where two endpoints reuse the JSON key `status`: the
    power endpoint (has supportedStatuses) means On/Standby, the peripheral
    endpoint means Ok/Error — the classic cross-fire require: prevents."""
    definition = dict(JSON_DEFINITION, id="acme_scoped")
    definition["responses"] = [
        {
            "json": True,
            "require": "supportedStatuses",
            "set": {"status_text": {"key": "status"}},
        },
        {"json": True, "set": {"sessions": {"key": "sessions", "type": "integer"}}},
    ]
    cls = create_configurable_driver_class(definition)

    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    events = EventBus()
    st = StateStore()
    st.set_event_bus(events)
    return cls("scoped1", {"host": "127.0.0.1"}, st, events)


async def test_json_require_scopes_rule_to_matching_bodies():
    drv = _scoped_driver()
    # Peripheral body: carries `status` but not the required key — the scoped
    # rule must not apply; the unscoped rule still does.
    await drv.on_data_received(b'{"status":"Ok","sessions":2}')
    assert drv.get_state("status_text") is None
    assert drv.get_state("sessions") == 2
    # Power body: required key present — the scoped rule applies.
    await drv.on_data_received(
        b'{"status":"Standby","supportedStatuses":["On","Standby"]}'
    )
    assert drv.get_state("status_text") == "Standby"


async def test_json_require_list_needs_every_key():
    definition = dict(JSON_DEFINITION, id="acme_scoped2")
    definition["responses"] = [
        {
            "json": True,
            "require": ["alpha", "beta"],
            "set": {"status_text": {"key": "status"}},
        },
    ]
    cls = create_configurable_driver_class(definition)

    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    events = EventBus()
    st = StateStore()
    st.set_event_bus(events)
    drv = cls("scoped2", {"host": "127.0.0.1"}, st, events)

    await drv.on_data_received(b'{"status":"A","alpha":1}')
    assert drv.get_state("status_text") is None
    await drv.on_data_received(b'{"status":"B","alpha":1,"beta":2}')
    assert drv.get_state("status_text") == "B"


def test_loader_validates_require():
    from openavc.drivers.driver_loader import validate_driver_definition

    base = dict(JSON_DEFINITION, id="acme_v")
    base["name"] = "Acme V"
    base["author"] = "Test"
    base["description"] = "x"
    base["source_url"] = "https://example.com"

    ok = dict(base)
    ok["responses"] = [
        {"json": True, "require": "supportedStatuses", "set": {"mode": "mode"}},
        {"json": True, "require": ["a", "b"], "set": {"sessions": "sessions"}},
    ]
    # The invented definition trips unrelated metadata checks; only assert
    # that a well-formed require produces no require-specific error.
    assert not [e for e in validate_driver_definition(ok) if "require" in e]

    for bad, expect in [
        ({"match": "x", "require": "k", "set": {"mode": "$0"}}, "only applies to json"),
        ({"json": True, "require": "", "set": {"mode": "m"}}, "must name a JSON key"),
        ({"json": True, "require": [], "set": {"mode": "m"}}, "non-empty JSON key"),
        ({"json": True, "require": [""], "set": {"mode": "m"}}, "non-empty JSON key"),
        ({"json": True, "require": 5, "set": {"mode": "m"}}, "JSON key name or a"),
    ]:
        d = dict(base)
        d["responses"] = [bad]
        errors = validate_driver_definition(d)
        assert any(expect in e for e in errors), (bad, errors)


# ── Gap B: on_connect / dispatch query ───────────────────────────────────────

class _FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


class _FakeHttpTransport:
    """Minimal stand-in for the raw-path GET branch of _dispatch_query."""

    def __init__(self, text):
        self._text = text
        self.connected = True
        self.gets: list[str] = []

    async def get(self, path):
        self.gets.append(path)
        return _FakeResp(self._text)


async def test_dispatch_query_raw_path_feeds_matcher(driver):
    """A raw-path query GETs and runs the response through the matcher."""
    driver.transport = _FakeHttpTransport('{"sessions":9}')
    await driver._dispatch_query("/status")
    assert driver.transport.gets == ["/status"]
    assert driver.get_state("sessions") == 9          # response was matched


async def test_dispatch_query_command_name_runs_command(driver):
    """A query naming a command runs it (so on_connect-only queries work)."""
    called = []

    async def fake_send(command, params=None, *, read_back=False):
        called.append((command, read_back))

    driver._send_command = fake_send
    await driver._dispatch_query("query_status")
    # The driver's own line: run without the command's read_back.
    assert called == [("query_status", False)]


# ── after_json: one regex rule kept alive past the json rules ──────────────
# The shape: a reply that carries readings AND a field no json key reads. The
# json rules resolve, consume the frame, and the error field is never seen —
# so the driver's `last_error` stays at whatever it was while the device is
# telling it something went wrong.


def _error_reader_driver(driver_id="acme_err", after_json=True):
    """Invented device whose replies bundle values with an error envelope."""
    definition = dict(JSON_DEFINITION, id=driver_id)
    definition["state_variables"] = {
        **JSON_DEFINITION["state_variables"],
        "last_error": {"type": "string"},
    }
    error_rule = {
        "match": r'"error":\[.*?(\d{3}),\{"desc":"([^"]*)"',
        "mappings": [{"group": 2, "state": "last_error"}],
    }
    if after_json:
        error_rule["after_json"] = True
    definition["responses"] = JSON_DEFINITION["responses"] + [error_rule]
    cls = create_configurable_driver_class(definition)

    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    events = EventBus()
    st = StateStore()
    st.set_event_bus(events)
    return cls(driver_id, {"host": "127.0.0.1"}, st, events)


async def test_after_json_rule_reads_a_body_the_json_rules_consumed():
    drv = _error_reader_driver()
    body = '{"sessions":3,"error":[406,{"desc":"value out of range"}]}'
    await drv.on_data_received(body.encode("utf-8"))
    assert drv.get_state("sessions") == 3          # json rule still applied
    assert drv.get_state("last_error") == "value out of range"


async def test_without_the_flag_the_json_rules_still_consume_the_body():
    """The default is unchanged: no flag, no regex rule after a json rule."""
    drv = _error_reader_driver(driver_id="acme_err2", after_json=False)
    body = '{"sessions":3,"error":[406,{"desc":"value out of range"}]}'
    await drv.on_data_received(body.encode("utf-8"))
    assert drv.get_state("sessions") == 3
    assert drv.get_state("last_error") is None


async def test_after_json_rule_still_matches_when_no_json_rule_applied():
    """The flag adds eligibility, it never removes it: a body no json rule
    reads reaches the rule the ordinary way."""
    drv = _error_reader_driver(driver_id="acme_err3")
    await drv.on_data_received(b'{"error":[404,{"desc":"no such method"}]}')
    assert drv.get_state("last_error") == "no such method"
    assert drv.get_state("sessions") is None


async def test_plain_regex_rules_are_still_skipped_past_a_json_rule():
    """One flagged rule does not open the gate for the rest of the table."""
    definition = dict(JSON_DEFINITION, id="acme_err4")
    definition["state_variables"] = {
        **JSON_DEFINITION["state_variables"],
        "last_error": {"type": "string"},
        "banner": {"type": "string"},
    }
    definition["responses"] = JSON_DEFINITION["responses"] + [
        # Ordered first, matches the same body, and carries no flag: it must
        # stay skipped so the flagged rule below is what reads the frame.
        {"match": r'"(sessions)"', "mappings": [{"group": 1, "state": "banner"}]},
        {
            "match": r'"error":\[.*?(\d{3}),\{"desc":"([^"]*)"',
            "mappings": [{"group": 2, "state": "last_error"}],
            "after_json": True,
        },
    ]
    cls = create_configurable_driver_class(definition)

    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    events = EventBus()
    st = StateStore()
    st.set_event_bus(events)
    drv = cls("err4", {"host": "127.0.0.1"}, st, events)

    await drv.on_data_received(
        b'{"sessions":3,"error":[406,{"desc":"refused"}]}'
    )
    assert drv.get_state("banner") is None
    assert drv.get_state("last_error") == "refused"


def test_loader_validates_after_json():
    from openavc.drivers.driver_loader import validate_driver_definition

    base = dict(JSON_DEFINITION, id="acme_af")
    base["name"] = "Acme AF"
    base["author"] = "Test"
    base["description"] = "x"
    base["source_url"] = "https://example.com"

    ok = dict(base)
    ok["responses"] = JSON_DEFINITION["responses"] + [
        {"match": r"ERR (\d+)", "set": {"mode": "$1"}, "after_json": True},
    ]
    assert not [e for e in validate_driver_definition(ok) if "after_json" in e]

    for responses, expect in [
        (
            [{"json": True, "set": {"mode": "m"}, "after_json": True}],
            "a json: true rule already reads the body",
        ),
        (
            [
                {"json": True, "set": {"mode": "m"}},
                {"address": "/mode", "after_json": True},
            ],
            "an OSC address rule never runs after a json rule",
        ),
        (
            [{"match": "ERR", "set": {"mode": "x"}, "after_json": True}],
            "no response rule in this driver is json: true",
        ),
    ]:
        d = dict(base)
        d["responses"] = responses
        errors = validate_driver_definition(d)
        assert any(expect in e for e in errors), (responses, errors)


def test_after_json_sets_the_platform_floor():
    """A driver taking the flag cannot install on a release that ignores it."""
    from openavc.drivers.spec import platform_requirements

    definition = dict(JSON_DEFINITION, id="acme_floor")
    definition["responses"] = JSON_DEFINITION["responses"] + [
        {"match": r"ERR (\d+)", "set": {"mode": "$1"}, "after_json": True},
    ]
    assert ("responses[1].after_json", "0.34.0") in platform_requirements(
        definition
    )


# ---------------------------------------------------------------------------
# `contains`: a yes/no question of the value at a key
# ---------------------------------------------------------------------------

CONTAINS_DEFINITION = {
    "id": "acme_flagger",
    "name": "Acme Flagger",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "http",
    "default_config": {"host": "", "port": 80},
    "state_variables": {
        "no_link": {"type": "boolean", "label": "No link"},
        "low_battery": {"type": "boolean", "label": "Low battery"},
        "has_dante": {"type": "boolean", "label": "Has Dante"},
        "name_has_stage": {"type": "boolean", "label": "Stage in name"},
        "code_is_7": {"type": "boolean", "label": "Code is 7"},
        "in_bank_3": {"type": "boolean", "label": "Bank 3"},
        "warning_count": {"type": "integer", "label": "Warnings"},
    },
    "child_entity_types": {
        "channel": {
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {
                "no_link": {"type": "boolean"},
                "af_peak": {"type": "boolean"},
            },
            "instances": {"count": 2},
        }
    },
    "commands": {
        "query": {"label": "Query", "method": "GET", "path": "/status"},
    },
    "responses": [
        {
            "json": True,
            "set": {
                "no_link": {"key": "warnings", "contains": "NoLink"},
                "low_battery": {"key": "warnings", "contains": "LowBattery"},
                "has_dante": {"key": "interfaces", "contains": "dante"},
                "name_has_stage": {"key": "name", "contains": "Stage"},
                "code_is_7": {"key": "code", "contains": 7},
                "in_bank_3": {"key": "banks", "contains": 3},
                "warning_count": "warnings",
            },
        },
        {
            "json": True,
            "child_set": [
                {
                    "type": "channel",
                    "id": 1,
                    "state": {
                        "no_link": {"key": "/api/channel/0/warnings", "contains": "NoLink"},
                        "af_peak": {"key": "/api/channel/0/warnings", "contains": "AfPeak"},
                    },
                },
                {
                    "type": "channel",
                    "id": 2,
                    "state": {
                        "no_link": {"key": "/api/channel/1/warnings", "contains": "NoLink"},
                        "af_peak": {"key": "/api/channel/1/warnings", "contains": "AfPeak"},
                    },
                },
            ],
        },
    ],
}


@pytest.fixture
def flagger(state, events):
    state.set_event_bus(events)
    cls = create_configurable_driver_class(CONTAINS_DEFINITION)
    drv = cls("flag1", {"host": "127.0.0.1", "port": 80}, state, events)
    drv._register_declared_children()
    return drv


def test_loader_accepts_contains_specs():
    from openavc.drivers.driver_loader import validate_driver_definition

    assert validate_driver_definition(CONTAINS_DEFINITION) == []


@pytest.mark.asyncio
async def test_contains_answers_membership_key_substring_and_equality(flagger):
    body = (
        '{"warnings": ["NoLink", "RfPeak"], "interfaces": {"dante": {}, "analog": {}},'
        ' "name": "Stage left", "code": 7, "banks": [1, 3]}'
    )
    assert flagger._apply_json_responses(body) is True
    assert flagger.get_state("no_link") is True
    assert flagger.get_state("low_battery") is False
    assert flagger.get_state("has_dante") is True
    assert flagger.get_state("name_has_stage") is True
    assert flagger.get_state("code_is_7") is True
    assert flagger.get_state("in_bank_3") is True
    # The plain path on the same key still stores the array's length.
    assert flagger.get_state("warning_count") == 2


@pytest.mark.asyncio
async def test_contains_false_when_the_flag_clears(flagger):
    flagger._apply_json_responses('{"warnings": ["NoLink"]}')
    assert flagger.get_state("no_link") is True
    flagger._apply_json_responses('{"warnings": []}')
    assert flagger.get_state("no_link") is False
    assert flagger.get_state("warning_count") == 0


@pytest.mark.asyncio
async def test_contains_skips_a_body_without_the_key(flagger):
    flagger._apply_json_responses('{"warnings": ["LowBattery"]}')
    assert flagger.get_state("low_battery") is True
    # A body about something else leaves the flag alone.
    assert flagger._apply_json_responses('{"name": "Podium"}') is True
    assert flagger.get_state("low_battery") is True


@pytest.mark.asyncio
async def test_contains_routes_to_children_by_literal_id(flagger):
    body = (
        '{"/api/channel/0/warnings": ["NoLink"],'
        ' "/api/channel/1/warnings": ["AfPeak", "RfPeak"]}'
    )
    assert flagger._apply_json_responses(body) is True
    one = flagger.get_child_state("channel", 1)
    two = flagger.get_child_state("channel", 2)
    assert one["no_link"] is True
    assert one["af_peak"] is False
    assert two["no_link"] is False
    assert two["af_peak"] is True


# ── a reply that repeats a name keeps the values under every copy ──────────

# Invented device that bundles several addresses into one notification by
# repeating the name above them, each copy holding another part.
BUNDLE_DEFINITION = {
    "id": "acme_bundler",
    "name": "Acme Bundler",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "http",
    "default_config": {"host": "", "port": 80},
    "state_variables": {
        "mute_mode": {"type": "string", "label": "Mute mode"},
        "muted": {"type": "boolean", "label": "Muted"},
        "runtime": {"type": "integer", "label": "Runtime"},
        "gauge": {"type": "integer", "label": "Gauge"},
        "level": {"type": "integer", "label": "Level"},
        "modes": {"type": "integer", "label": "Modes"},
        "out1_names": {"type": "integer", "label": "Output 1 names"},
        "out1_has_gain": {"type": "boolean", "label": "Output 1 has gain"},
        "bays": {"type": "integer", "label": "Bays"},
        "unit_status": {"type": "string", "label": "Unit status"},
        "report_mode": {"type": "string", "label": "Report mode"},
    },
    "child_entity_types": {
        "output": {
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {"muted": {"type": "boolean"}, "gain": {"type": "integer"}},
            "instances": {"count": 2},
        }
    },
    "commands": {"query": {"label": "Query", "method": "GET", "path": "/status"}},
    "responses": [
        {
            "json": True,
            "set": {
                "mute_mode": "out1.mute_mode",
                "muted": "out1.muted",
                "runtime": "remotes.r1.runtime",
                "gauge": "remotes.r1.gauge",
                "level": "level",
                "modes": "modes",
                "out1_names": "out1",
                "out1_has_gain": {"key": "out1", "contains": "gain"},
                "bays": "bays",
            },
        },
        {
            "json": True,
            "require": "unit.mode",
            "set": {"unit_status": "unit.status"},
        },
        {
            "json": True,
            "child_set": [
                {"type": "output", "id": 1,
                 "state": {"muted": "out1.muted", "gain": "out1.gain"}},
                {"type": "output", "id": 2,
                 "state": {"muted": "out2.muted", "gain": "out2.gain"}},
            ],
        },
        # A JSON document carried inside a text line, read by json_path.
        {
            "match": r"^REPORT (\{.*\})$",
            "mappings": [{"group": 1, "json_path": "out1.mute_mode",
                          "state": "report_mode", "type": "string"}],
        },
    ],
}


@pytest.fixture
def bundler(state, events):
    state.set_event_bus(events)
    cls = create_configurable_driver_class(BUNDLE_DEFINITION)
    drv = cls("bundle1", {"host": "127.0.0.1", "port": 80}, state, events)
    drv._register_declared_children()
    return drv


def test_loader_accepts_the_bundle_definition():
    from openavc.drivers.driver_loader import validate_driver_definition

    assert validate_driver_definition(BUNDLE_DEFINITION) == []


async def test_a_bundled_reply_sets_every_value(bundler):
    """Four values under two repeated names: every one is set, where the last
    copy alone would have given two."""
    body = (
        '{"out1":{"mute_mode":"ON"}, "out1":{"muted":false},'
        '"remotes":{"r1":{"runtime":840}},"remotes":{"r1":{"gauge":2}}}'
    )
    await bundler.on_data_received(body.encode())
    assert bundler.get_state("mute_mode") == "ON"
    assert bundler.get_state("muted") is False
    assert bundler.get_state("runtime") == 840
    assert bundler.get_state("gauge") == 2


async def test_a_repeated_scalar_null_or_array_keeps_the_last_value(bundler):
    await bundler.on_data_received(b'{"level":1,"level":2}')
    assert bundler.get_state("level") == 2
    await bundler.on_data_received(b'{"level":3,"level":null}')
    assert bundler.get_state("level") is None
    await bundler.on_data_received(b'{"modes":["a","b","c"],"modes":["d"]}')
    assert bundler.get_state("modes") == 1


async def test_an_object_and_a_scalar_under_one_name_keep_the_last(bundler):
    await bundler.on_data_received(b'{"out1":{"muted":true}}')
    assert bundler.get_state("muted") is True
    # Object, then scalar: the scalar wins, out1.muted is not there, nothing
    # is written (a missing path never clears a value).
    await bundler.on_data_received(b'{"out1":{"muted":false},"out1":5}')
    assert bundler.get_state("muted") is True
    # Scalar, then object: the object wins.
    await bundler.on_data_received(b'{"out1":5,"out1":{"muted":false}}')
    assert bundler.get_state("muted") is False


async def test_equal_objects_in_an_array_stay_separate(bundler):
    await bundler.on_data_received(b'{"bays":[{"s":1},{"s":1}]}')
    assert bundler.get_state("bays") == 2


async def test_a_one_element_array_reply_with_a_repeat_inside(bundler):
    await bundler.on_data_received(
        b'[{"out1":{"mute_mode":"OFF"},"out1":{"muted":true}}]'
    )
    assert bundler.get_state("mute_mode") == "OFF"
    assert bundler.get_state("muted") is True


async def test_require_sees_a_name_from_the_earlier_copy(bundler):
    """The required name is in the first copy and the value in the second:
    the rule applies. With the last copy alone it was skipped."""
    await bundler.on_data_received(
        b'{"unit":{"mode":"A"},"unit":{"status":"ok"}}'
    )
    assert bundler.get_state("unit_status") == "ok"


async def test_contains_and_a_count_see_names_from_every_copy(bundler):
    """A read of the repeated object itself: contains finds a name from the
    earlier copy, and the object's size counts every copy's names."""
    await bundler.on_data_received(
        b'{"out1":{"gain":4},"out1":{"muted":false}}'
    )
    assert bundler.get_state("out1_has_gain") is True
    assert bundler.get_state("out1_names") == 2


async def test_children_read_from_every_copy(bundler):
    await bundler.on_data_received(
        b'{"out1":{"muted":true},"out2":{"gain":7},"out1":{"gain":3},'
        b'"out2":{"muted":false}}'
    )
    one = bundler.get_child_state("output", 1)
    two = bundler.get_child_state("output", 2)
    assert (one["muted"], one["gain"]) == (True, 3)
    assert (two["muted"], two["gain"]) == (False, 7)


async def test_json_path_in_a_text_line_reads_every_copy(bundler):
    await bundler.on_data_received(
        b'REPORT {"out1":{"mute_mode":"ON"},"out1":{"muted":true}}'
    )
    assert bundler.get_state("report_mode") == "ON"


async def test_a_reply_with_no_repeat_reads_as_before(bundler):
    await bundler.on_data_received(
        b'{"out1":{"mute_mode":"ON","muted":true,"gain":2},'
        b'"remotes":{"r1":{"runtime":10,"gauge":3}},"level":4,"bays":[{}]}'
    )
    assert bundler.get_state("mute_mode") == "ON"
    assert bundler.get_state("muted") is True
    assert bundler.get_state("runtime") == 10
    assert bundler.get_state("gauge") == 3
    assert bundler.get_state("level") == 4
    assert bundler.get_state("out1_names") == 3
    assert bundler.get_state("bays") == 1
