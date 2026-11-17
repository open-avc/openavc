"""The generated wire contract matches its tables, and every builder and reader
matches the contract.

``python -m openavc.cloud.contract_gen`` renders ``openavc/cloud/wire_tables.py``
into ``openavc/cloud/wire_contract.json``, the file the cloud copies. These
tests re-render it and compare byte for byte, validate every example and every
upstream builder's output against the schemas, and read every downstream
example through the agent's reader.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import jsonschema
import pytest

from openavc.cloud import contract_gen, protocol
from openavc.cloud.crypto import verify_message_signature
from openavc.cloud.wire_tables import MESSAGES, Message

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = contract_gen.build_contract()


def test_committed_contract_matches_the_tables():
    path = contract_gen.artifact_path(REPO_ROOT)
    assert path.is_file(), "openavc/cloud/wire_contract.json is missing: run the generator"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == contract_gen.render(), (
        "openavc/cloud/wire_contract.json does not match openavc/cloud/wire_tables.py: "
        "regenerate with 'python -m openavc.cloud.contract_gen' (never edit it by hand)"
    )


def test_the_contract_names_every_message_once():
    assert set(CONTRACT["messages"]) == {m.type for m in MESSAGES}


def _validate(schema: dict, instance: object) -> None:
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(instance)


def _payload_of(m: Message, message: dict) -> dict:
    if m.phase == "tunnel_data":
        return {k: v for k, v in message.items() if k != "type"}
    return message["payload"]


@pytest.mark.parametrize("m", MESSAGES, ids=lambda m: m.type)
def test_every_example_is_a_valid_message(m):
    entry = CONTRACT["messages"][m.type]
    example = entry["example"]
    assert example["type"] == m.type
    _validate(CONTRACT["envelope"][m.phase], example)
    _validate(entry["payload"], _payload_of(m, example))
    if m.phase == "session":
        key = bytes.fromhex(CONTRACT["example_signing_key"])
        assert verify_message_signature(key, example, example["sig"])


# The handshake and tunnel builders name a few arguments differently from the
# fields they write.
_ARGUMENT_FIELDS = {"os_info": "os", "body_b64": "body", "data_b64": "data", "ws_id": "id"}


def _call_with_examples(builder, m: Message) -> dict:
    examples = {f.name: f.example for f in m.fields if f.example is not contract_gen.ABSENT}
    if m.phase == "tunnel_data":
        examples["request_id"] = examples.get("id")
    kwargs = {}
    for name in inspect.signature(builder).parameters:
        field = _ARGUMENT_FIELDS.get(name, name)
        if field in examples:
            kwargs[name] = examples[field]
    return builder(**kwargs)


_HANDSHAKE_BUILDERS = {
    "hello": protocol.build_hello,
    "authenticate": protocol.build_authenticate,
    "resume": protocol.build_resume,
}
_TUNNEL_BUILDERS = {
    "http_response": protocol.build_http_response,
    "http_response_start": protocol.build_http_response_start,
    "http_body": protocol.build_http_body,
    "http_body_end": protocol.build_http_body_end,
    "ws_frame": protocol.build_ws_frame,
    "ws_close": protocol.build_ws_close,
}


def test_every_upstream_message_has_a_builder():
    built = set(protocol.UPSTREAM_PAYLOAD_BUILDERS) | set(_HANDSHAKE_BUILDERS) | set(_TUNNEL_BUILDERS)
    assert built == {m.type for m in MESSAGES if m.direction in ("up", "both")}


@pytest.mark.parametrize("m", [m for m in MESSAGES if m.direction in ("up", "both")],
                         ids=lambda m: m.type)
def test_every_upstream_builder_writes_what_the_contract_says(m):
    entry = CONTRACT["messages"][m.type]
    if m.type in protocol.UPSTREAM_PAYLOAD_BUILDERS:
        payload = _call_with_examples(protocol.UPSTREAM_PAYLOAD_BUILDERS[m.type], m)
        _validate(entry["payload"], payload)
        return
    builder = _HANDSHAKE_BUILDERS.get(m.type) or _TUNNEL_BUILDERS[m.type]
    message = _call_with_examples(builder, m)
    assert message["type"] == m.type
    _validate(CONTRACT["envelope"][m.phase], message)
    _validate(entry["payload"], _payload_of(m, message))


def _declared(m: Message) -> set[str]:
    return {f.name for f in m.fields}


@pytest.mark.parametrize("m", [m for m in MESSAGES if m.direction in ("up", "both")],
                         ids=lambda m: m.type)
def test_this_agents_builders_send_only_declared_fields(m):
    """The schema lets an older agent's removed fields through; this agent's
    own builders may send nothing the contract does not name."""
    if m.type in protocol.UPSTREAM_PAYLOAD_BUILDERS:
        sent = _call_with_examples(protocol.UPSTREAM_PAYLOAD_BUILDERS[m.type], m)
    else:
        built = _call_with_examples(_HANDSHAKE_BUILDERS.get(m.type) or _TUNNEL_BUILDERS[m.type], m)
        sent = _payload_of(m, built)
    assert set(sent) <= _declared(m)


class _Capture:
    def __init__(self):
        self.sent = []

    async def send_message(self, msg_type, payload):
        self.sent.append((msg_type, payload))


def _check_sent(capture: _Capture, msg_type: str) -> None:
    payloads = [p for t, p in capture.sent if t == msg_type]
    assert payloads, f"nothing sent as {msg_type}"
    row = protocol.MESSAGE_ROWS[msg_type]
    for payload in payloads:
        _validate(CONTRACT["messages"][msg_type]["payload"], payload)
        assert set(payload) <= _declared(row)


@pytest.mark.asyncio
async def test_the_state_relays_batches_are_the_contracts():
    from openavc.cloud.state_relay import StateRelay
    from openavc.core.state_store import StateStore

    agent = _Capture()
    agent._config = {}
    relay = StateRelay(agent, StateStore())
    relay._on_state_change("var.level", 1, 2, "test")
    relay._on_state_change("var.gone", "old", None, "test")
    await relay._flush_bucket("top")
    _check_sent(agent, "state_batch")
    changes = [c for _t, p in agent.sent for c in p["changes"]]
    assert any(c.get("deleted") for c in changes)


@pytest.mark.asyncio
async def test_the_device_command_list_is_the_contracts():
    from types import SimpleNamespace

    from openavc.cloud.command_handler import CommandHandler

    devices = SimpleNamespace(
        list_devices=lambda: [{"id": "widget", "name": "Widget", "driver": "acme_widget",
                               "connected": True}],
        get_device_info=lambda _id: {"commands": {"power_on": {"params": {
            "level": {"type": "integer", "label": "Level"}}}}},
    )
    agent = _Capture()
    handler = CommandHandler(agent, devices, SimpleNamespace(emit=None))
    await handler.handle({"type": "get_device_commands", "payload": {"request_id": "r1"}})
    _check_sent(agent, "device_commands_data")


@pytest.mark.parametrize("m", [m for m in MESSAGES if m.direction in ("down", "both")],
                         ids=lambda m: m.type)
def test_every_downstream_example_reads_as_sent(m):
    example = CONTRACT["messages"][m.type]["example"]
    if m.phase == "tunnel_data":
        assert protocol.read_tunnel_data(example) == {
            **example,
            **{f.name: f.default for f in m.fields
               if f.name not in example and f.default is not contract_gen.ABSENT},
        }
    else:
        read = protocol.read_message(example)
        for name, value in example["payload"].items():
            assert read[name] == value


def test_a_since_newer_than_the_last_release_fails_a_different_cut():
    problems = contract_gen.unshipped_sinces("9.0.0", previous="8.0.0")
    assert problems == []
    problems = contract_gen.unshipped_sinces("0.41.0-rc1", previous="0.40.0")
    assert problems == []
    problems = contract_gen.unshipped_sinces("1.0.0", previous="0.40.0")
    assert any(p.startswith("first_contract_release") for p in problems)


def test_a_candidate_is_checked_as_its_final():
    assert contract_gen.final_of("0.41.0-rc1") == "0.41.0"
    assert contract_gen.final_of("v0.41.0rc2") == "0.41.0"
    assert contract_gen.final_of("0.41.0") == "0.41.0"


def test_the_check_mode_passes_on_the_committed_file(capsys):
    assert contract_gen.main(["--check"]) == 0


def test_the_contract_is_plain_json():
    path = contract_gen.artifact_path(REPO_ROOT)
    json.loads(path.read_text(encoding="utf-8"))
