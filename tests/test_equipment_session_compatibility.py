"""A restarted TLS peer must be described again before further control."""

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import ipaddress
import ssl

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.equipment import EquipmentDriver
from openavc.drivers.equipment_protocol import PROTOCOL
from openavc.drivers.registry import _DRIVER_REGISTRY


@pytest.fixture
async def equipment_peer(tmp_path, monkeypatch):
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
    peer = {"protocol": PROTOCOL, "session": "first", "model": model,
            "commands": [], "descriptions": [], "snapshot_session": None, "children": []}

    async def description(request):
        peer["descriptions"].append((peer["session"], peer["protocol"]))
        return web.json_response({"protocol": peer["protocol"], "identity": "example-unit",
            "session": peer["session"], "capabilities": peer["model"]})

    async def snapshot(request):
        return web.json_response({"identity": "example-unit",
            "session": peer["snapshot_session"] or peer["session"],
            "state": {"counter": len(peer["commands"])}, "children": peer["children"]})

    async def command(request):
        body = await request.json()
        peer["commands"].append(body)
        if peer.get("restart_after_command"):
            # Finishes the command, then restarts before anything else is read.
            peer["session"] = "second"
        return web.json_response({"id": body["id"], "outcome": "completed"})

    app = web.Application()
    app.router.add_get("/v1/description", description)
    app.router.add_get("/v1/snapshot", snapshot)
    app.router.add_post("/v1/command", command)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
    await site.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    monkeypatch.setattr("openavc.drivers.equipment.get_data_dir", lambda: tmp_path)
    driver = EquipmentDriver("equipment", {"host": "127.0.0.1",
        "port": site._server.sockets[0].getsockname()[1],
        "username": "example", "password": "fixture", "verify_ssl": True}, StateStore(), EventBus())
    try:
        yield driver, peer
    finally:
        await driver.disconnect()
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["protocol", "additive", "removed"])
async def test_new_session_rechecks_protocol_and_capabilities_before_control(equipment_peer, change):
    driver, peer = equipment_peer
    await driver.connect()
    await driver.send_command("increment")
    assert len(peer["commands"]) == 1
    peer["session"] = "second"
    if change == "protocol":
        peer["protocol"] = "unsupported-example"
    else:
        peer["model"]["commands"]["advance"] = {"params": {}}
        if change == "removed":
            del peer["model"]["commands"]["increment"]
    async with asyncio.timeout(2):
        while driver._connected:
            await asyncio.sleep(.01)
    assert driver.state.get("device.equipment.counter") is None
    with pytest.raises(ConnectionError, match="connection must be restored"):
        await driver.send_command("increment")
    assert len(peer["commands"]) == 1
    if change == "protocol":
        with pytest.raises(ValueError, match="protocol does not match"):
            await driver.connect()
        assert not driver._connected
    else:
        await driver.connect()
        assert driver._connected
        assert ("increment" in driver.DRIVER_INFO["commands"]) == (change == "additive")
        await driver.send_command("advance")
        assert peer["commands"][-1]["session"] == "second"
        assert len(peer["commands"]) == 2
    assert peer["descriptions"] == [("first", PROTOCOL), ("second", peer["protocol"])]


@pytest.mark.asyncio
async def test_a_product_whose_child_type_declares_no_readings_reconnects_after_a_restart(
        equipment_peer, monkeypatch):
    driver, peer = equipment_peer
    peer["model"]["child_entity_types"] = {"zone": {"label": "Zone"}}
    peer["children"] = [{"type": "zone", "id": 1, "state": {}}]
    monkeypatch.setitem(_DRIVER_REGISTRY, "openavc_equipment", EquipmentDriver)
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    manager = DeviceManager(state, events)
    try:
        await manager.add_device({"id": "amp", "driver": "openavc_equipment", "name": "Amp",
                                  "config": dict(driver.config)})
        assert state.get("device.amp.connected") is True
        peer["session"] = "second"
        async with asyncio.timeout(10):
            while len(peer["descriptions"]) < 2 or not state.get("device.amp.connected"):
                await asyncio.sleep(.05)
    finally:
        await manager.disconnect_all()
    assert peer["descriptions"] == [("first", PROTOCOL), ("second", PROTOCOL)]


@pytest.mark.asyncio
async def test_session_change_during_initial_description_refuses_connection(equipment_peer):
    driver, peer = equipment_peer
    peer["snapshot_session"] = "different-from-description"
    with pytest.raises(ConnectionError, match="equipment restarted"):
        await driver.connect()
    assert not driver._connected
    assert peer["commands"] == []


@pytest.mark.asyncio
async def test_a_command_that_completed_stays_completed_when_the_product_restarts_after_it(
        equipment_peer):
    """The product finished the command, then restarted before the refresh that
    follows it. The command still answers completed; the restart reconnects."""
    driver, peer = equipment_peer
    await driver.connect()
    peer["restart_after_command"] = True
    result = await driver.send_command("increment")
    assert result["outcome"] == "completed"
    assert len(peer["commands"]) == 1
    peer["restart_after_command"] = False
    # The poll meets the new session and stops trusting the old capabilities.
    async with asyncio.timeout(2):
        while driver._connected:
            await asyncio.sleep(.01)
    await driver.connect()
    assert peer["descriptions"] == [("first", PROTOCOL), ("second", PROTOCOL)]
