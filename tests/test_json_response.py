"""JSON-body response mode + on_connect query dispatch for ConfigurableDriver.

Platform features, exercised with an invented HTTP/JSON device (no real product):
  - JSON responses parse one body and populate many state vars at once (the
    engine no longer stops at the first matching rule for JSON bodies).
  - on_connect resolves command names and feeds HTTP responses to the matcher,
    the same way poll() does.
"""

import pytest

from server.drivers.configurable import create_configurable_driver_class
from server.drivers.inline_protocol import _normalize_one_response

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
    assert driver.get_state("sessions") == 0
    assert driver.get_state("in_use") is False


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
    assert driver.get_state("sessions") == 0


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

    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

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

    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

    events = EventBus()
    st = StateStore()
    st.set_event_bus(events)
    return cls("scoped1", {"host": "127.0.0.1"}, st, events)


async def test_json_require_scopes_rule_to_matching_bodies():
    drv = _scoped_driver()
    # Peripheral body: carries `status` but not the required key — the scoped
    # rule must not apply; the unscoped rule still does.
    await drv.on_data_received(b'{"status":"Ok","sessions":2}')
    assert drv.get_state("status_text") == ""
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

    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

    events = EventBus()
    st = StateStore()
    st.set_event_bus(events)
    drv = cls("scoped2", {"host": "127.0.0.1"}, st, events)

    await drv.on_data_received(b'{"status":"A","alpha":1}')
    assert drv.get_state("status_text") == ""
    await drv.on_data_received(b'{"status":"B","alpha":1,"beta":2}')
    assert drv.get_state("status_text") == "B"


def test_loader_validates_require():
    from server.drivers.driver_loader import validate_driver_definition

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

    async def fake_send(command, params=None):
        called.append(command)

    driver.send_command = fake_send
    await driver._dispatch_query("query_status")
    assert called == ["query_status"]
