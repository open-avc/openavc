"""A command the equipment finishes after its first answer is followed to its outcome.

The equipment answers a command within a fraction of a second whether or not
the hardware has finished, and keeps the final outcome under the same request
ID. The fake below does the same: it answers 202 ``uncertain`` when the
hardware takes longer than its answer window, and serves the outcome at
``GET /v1/commands/{id}``.
"""

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import ipaddress
import ssl
import time

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.command_outcome import CommandOutcomeError
from openavc.drivers.equipment import EquipmentDriver
from openavc.drivers.equipment_protocol import PROTOCOL

ANSWER_WINDOW = 0.2


@pytest.fixture
async def peer(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example equipment")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .sign(key, hashes.SHA256()))
    ca = tmp_path / "ca.pem"
    ca.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    pem = tmp_path / "server.pem"
    pem.write_bytes(ca.read_bytes() + key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(pem)
    model = copy.deepcopy(EquipmentDriver.DRIVER_INFO)
    model.update(id="acme_equipment", manufacturer="Acme", name="Example equipment",
        config_schema={}, state_variables={"counter": {"type": "integer"}},
        commands={"increment": {"params": {}}})
    state = {"session": "first", "delay": 0.0, "final": "completed", "forget": False,
             "posts": [], "reads": [], "results": {}}

    async def description(request):
        return web.json_response({"protocol": PROTOCOL, "identity": "example-unit",
            "session": state["session"], "capabilities": model})

    async def snapshot(request):
        done = sum(r["outcome"] == "completed" for r in state["results"].values())
        return web.json_response({"identity": "example-unit", "session": state["session"],
            "state": {"counter": done}, "children": []})

    async def command(request):
        body = await request.json()
        state["posts"].append((time.monotonic(), body["id"]))
        result = state["results"][body["id"]] = {"id": body["id"], "outcome": "pending"}
        final = state["final"]

        def finish():
            result["outcome"] = final

        if state["delay"]:
            asyncio.get_running_loop().call_later(state["delay"], finish)
            await asyncio.sleep(min(state["delay"], ANSWER_WINDOW))
        else:
            finish()
        if result["outcome"] == "pending":
            return web.json_response({"id": body["id"], "outcome": "uncertain",
                "message": "Hardware has not confirmed completion."}, status=202)
        return web.json_response(result, status=200 if result["outcome"] == "completed" else 409)

    async def result(request):
        ident = int(request.match_info["ident"])
        state["reads"].append(ident)
        if state["forget"] or ident not in state["results"]:
            return web.json_response({"error": "unknown_request", "message": "No retained result."}, status=404)
        return web.json_response(state["results"][ident])

    app = web.Application()
    app.router.add_get("/v1/description", description)
    app.router.add_get("/v1/snapshot", snapshot)
    app.router.add_post("/v1/command", command)
    app.router.add_get("/v1/commands/{ident}", result)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
    await site.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    monkeypatch.setattr("openavc.drivers.equipment.get_data_dir", lambda: tmp_path)
    port = site._server.sockets[0].getsockname()[1]
    drivers = []

    async def connect(**config):
        driver = EquipmentDriver("equipment", {"host": "127.0.0.1", "port": port,
            "username": "example", "password": "fixture", "verify_ssl": True, **config},
            StateStore(), EventBus())
        drivers.append(driver)
        await driver.connect()
        return driver

    try:
        yield connect, state
    finally:
        for driver in drivers:
            await driver.disconnect()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_a_command_that_finishes_late_is_reported_completed(peer):
    connect, state = peer
    driver = await connect()
    state["delay"] = 0.6
    result = await driver.send_command("increment")
    assert result["outcome"] == "completed"
    assert len(state["posts"]) == 1 and state["reads"]
    assert driver.get_state("counter") == 1


@pytest.mark.asyncio
async def test_a_command_that_finishes_at_once_is_not_asked_again(peer):
    connect, state = peer
    driver = await connect()
    assert (await driver.send_command("increment"))["outcome"] == "completed"
    assert state["reads"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("final", ["failed", "rejected"])
async def test_a_late_failure_is_reported_as_that_failure(peer, final):
    connect, state = peer
    driver = await connect()
    state.update(delay=0.6, final=final)
    with pytest.raises(CommandOutcomeError) as refused:
        await driver.send_command("increment")
    assert refused.value.outcome == final
    assert len(state["posts"]) == 1


@pytest.mark.asyncio
async def test_the_wait_is_the_devices_own_setting(peer):
    connect, state = peer
    driver = await connect(completion_wait=0.5)
    state["delay"] = 5
    started = time.monotonic()
    with pytest.raises(CommandOutcomeError) as unconfirmed:
        await driver.send_command("increment")
    assert 0.4 < time.monotonic() - started < 1.5
    assert unconfirmed.value.outcome == "uncertain"
    assert str(unconfirmed.value) == (
        "The product did not confirm the command finished within 0.5 seconds. "
        "It may still finish, so check the product's status before sending it again."
    )
    assert len(state["posts"]) == 1


@pytest.mark.asyncio
async def test_a_wait_of_zero_answers_with_the_products_first_reply(peer):
    connect, state = peer
    driver = await connect(completion_wait=0)
    state["delay"] = 5
    with pytest.raises(CommandOutcomeError) as unconfirmed:
        await driver.send_command("increment")
    assert unconfirmed.value.outcome == "uncertain"
    assert "within" not in str(unconfirmed.value)
    assert state["reads"] == []


@pytest.mark.asyncio
async def test_a_new_equipment_session_ends_the_wait_without_sending_again(peer):
    connect, state = peer
    driver = await connect()
    state["delay"] = 5

    async def restart():
        await asyncio.sleep(0.5)
        state["session"] = "second"

    started = time.monotonic()
    restarting = asyncio.create_task(restart())
    with pytest.raises(CommandOutcomeError) as unconfirmed:
        await driver.send_command("increment")
    await restarting
    assert unconfirmed.value.outcome == "uncertain"
    assert time.monotonic() - started < 2
    assert len(state["posts"]) == 1


@pytest.mark.asyncio
async def test_a_result_the_equipment_no_longer_holds_is_unconfirmed(peer):
    connect, state = peer
    driver = await connect()
    state.update(delay=5, forget=True)
    started = time.monotonic()
    with pytest.raises(CommandOutcomeError) as unconfirmed:
        await driver.send_command("increment")
    assert unconfirmed.value.outcome == "uncertain"
    assert time.monotonic() - started < 1
    assert len(state["posts"]) == 1


@pytest.mark.asyncio
async def test_a_second_command_is_sent_while_the_first_is_waiting(peer):
    connect, state = peer
    driver = await connect()
    state["delay"] = 1.0
    first = asyncio.create_task(driver.send_command("increment"))
    await asyncio.sleep(0.4)
    state["delay"] = 0.0
    assert (await driver.send_command("increment"))["outcome"] == "completed"
    assert not first.done()
    assert (await first)["outcome"] == "completed"
    (sent_first, id_first), (sent_second, id_second) = state["posts"]
    assert id_second > id_first
    assert sent_second - sent_first < 0.6


def test_the_setting_is_offered_for_network_equipment():
    field = EquipmentDriver.DRIVER_INFO["config_schema"]["completion_wait"]
    assert field["type"] == "number" and field["default"] == 5
    assert field["min"] == 0
