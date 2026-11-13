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
from openavc.drivers.equipment import _RESULT_POLL_S, EquipmentDriver
from openavc.drivers.equipment_protocol import PROTOCOL

ANSWER_WINDOW = 0.2
# A wait, and a hardware delay, no test here lasts: a test that sets one can
# only be ended by what it is about, never by a timer running out.
LONGER_THAN_THE_TEST = 60
# How long a test waits for something that should happen at once. It catches a
# hang and nothing else: every command commits its ID to disk before it is
# sent, which a loaded CI runner has taken more than three seconds over.
HANG = 30


async def first_read(command, state):
    """Wait until the equipment is asked for ``command``'s outcome.

    A command that ends first fails the test with its own error, not as a
    timeout.
    """
    reading = asyncio.ensure_future(state["read_ids"].get())
    await asyncio.wait({reading, command}, timeout=HANG, return_when=asyncio.FIRST_COMPLETED)
    if reading.done():
        return reading.result()
    reading.cancel()
    if command.done():
        command.result()
        raise AssertionError("the command finished before its outcome was read")
    raise AssertionError("the command's outcome was never read")


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
             "posts": [], "reads": [], "results": {}, "read_ids": asyncio.Queue(),
             "completion_gate": None}
    finishing = []

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
        completion_gate = state["completion_gate"]

        def finish():
            result["outcome"] = final

        if completion_gate is not None:
            async def finish_when_released():
                await completion_gate.wait()
                finish()
            finishing.append(asyncio.create_task(finish_when_released()))
            await asyncio.sleep(ANSWER_WINDOW)
        elif state["delay"]:
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
        state["read_ids"].put_nowait(ident)
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
        for task in finishing:
            task.cancel()
        await asyncio.gather(*finishing, return_exceptions=True)
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
    assert time.monotonic() - started > 0.4
    # It stopped at its own half second, not the 5 s default. Counted, not
    # timed: the outcome is read about once per poll, so the default would read
    # some fifty times, and a slow runner only ever reads fewer.
    assert 1 <= len(state["reads"]) <= 2 * 0.5 / _RESULT_POLL_S
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
    driver = await connect(completion_wait=LONGER_THAN_THE_TEST)
    state["delay"] = LONGER_THAN_THE_TEST

    async def restart():
        # Change the session only after a command was accepted and the client
        # is reading its pending outcome, never during durable ID reservation.
        await asyncio.wait_for(state["read_ids"].get(), HANG)
        state["session"] = "second"

    restarting = asyncio.create_task(restart())
    with pytest.raises(CommandOutcomeError) as unconfirmed:
        await driver.send_command("increment")
    await restarting
    assert unconfirmed.value.outcome == "uncertain"
    # The new session ended it: only a wait that runs out says how long it waited.
    assert "within" not in str(unconfirmed.value)
    assert len(state["posts"]) == 1


@pytest.mark.asyncio
async def test_a_result_the_equipment_no_longer_holds_is_unconfirmed(peer):
    connect, state = peer
    driver = await connect(completion_wait=LONGER_THAN_THE_TEST)
    state.update(delay=LONGER_THAN_THE_TEST, forget=True)
    with pytest.raises(CommandOutcomeError) as unconfirmed:
        await driver.send_command("increment")
    assert unconfirmed.value.outcome == "uncertain"
    # The equipment's answer ended it: only a wait that runs out says how long
    # it waited.
    assert "within" not in str(unconfirmed.value)
    assert state["reads"]
    assert len(state["posts"]) == 1


@pytest.mark.asyncio
async def test_a_second_command_is_sent_while_the_first_is_waiting(peer):
    connect, state = peer
    # Only the release below may end the first command's wait.
    driver = await connect(completion_wait=LONGER_THAN_THE_TEST)
    completion = state["completion_gate"] = asyncio.Event()
    first = asyncio.create_task(driver.send_command("increment"))
    try:
        await first_read(first, state)
        state["completion_gate"] = None
        # The first result cannot complete before this explicit release. This
        # tests lock ownership rather than scheduler/filesystem speed.
        assert (await driver.send_command("increment"))["outcome"] == "completed"
        assert not first.done()
        completion.set()
        assert (await first)["outcome"] == "completed"
        (_, id_first), (_, id_second) = state["posts"]
        assert id_second > id_first
    finally:
        completion.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)


def test_the_setting_is_offered_for_network_equipment():
    field = EquipmentDriver.DRIVER_INFO["config_schema"]["completion_wait"]
    assert field["type"] == "number" and field["default"] == 5
    assert field["min"] == 0
