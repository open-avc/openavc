"""Read complete equipment messages over a fragmented transport, with a byte bound."""

import json
import ssl
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from aiohttp import ClientSession, web
import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from openavc.drivers.equipment import EquipmentDriver
from openavc.drivers.equipment_protocol import MAX_MESSAGE_BYTES
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore


@pytest.mark.asyncio
@pytest.mark.parametrize("oversized", [False, True])
async def test_fragmented_response_and_message_bound(oversized):
    value = {"data": "x" * (MAX_MESSAGE_BYTES if oversized else 40000)}
    encoded = json.dumps(value).encode()

    async def respond(request):
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        try:
            for start in range(0, len(encoded), 997):
                await response.write(encoded[start:start + 997])
            await response.write_eof()
        except ConnectionResetError:
            pass  # The bounded reader closes an oversized response.
        return response

    app = web.Application()
    app.router.add_get("/v1/snapshot", respond)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        driver = object.__new__(EquipmentDriver)
        driver._sdk_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        async with ClientSession() as client:
            driver._sdk_http = client
            if oversized:
                with pytest.raises(ValueError, match="1 MiB"):
                    await driver._request("/v1/snapshot")
            else:
                assert await driver._request("/v1/snapshot") == value
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("local,verify", [(False, True), (False, False), (True, False)])
async def test_equipment_certificate_verification_is_default_and_local_is_always_verified(tmp_path, monkeypatch, local, verify):
    """An explicit remote override works; it cannot weaken the built-in link."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Acme equipment")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .sign(key, hashes.SHA256()))
    pem = tmp_path / "server.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem)
    authorization = []

    async def description(request):
        authorization.append(request.headers.get("Authorization"))
        info = dict(EquipmentDriver.DRIVER_INFO, id="acme_equipment")
        info["state_variables"] = {}
        info["config_schema"] = {}
        return web.json_response({"protocol": "openavc-device-1", "identity": "unit", "session": "session", "capabilities": info})

    app = web.Application()
    app.router.add_get("/v1/description", description)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config = {"host": "127.0.0.1", "port": port, "username": "engineer", "password": "fixture", "verify_ssl": verify}
    if local:
        config.update(token="local-fixture", url=f"https://127.0.0.1:{port}", identity="unit", integration="acme_equipment")
    monkeypatch.setattr("openavc.drivers.equipment.get_data_dir", lambda: tmp_path)
    driver = EquipmentDriver("equipment", config, StateStore(), EventBus())
    monkeypatch.setattr(driver, "_refresh", AsyncMock())
    try:
        if local or verify:
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                await driver.connect()
            assert authorization == []
        else:
            await driver.connect()
            assert authorization == [aiohttp.BasicAuth("engineer", "fixture").encode()]
    finally:
        await driver.disconnect()
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("status, kind, sentence", [
    (401, "ConnectionFaultError", "The product refused the username and password."),
    (500, "ValueError", "The product refused the request (HTTP 500)."),
    (200, "ValueError", "The reply from this address is not the product's. "
                        "Check that the host and port are the product's."),
])
async def test_a_reply_that_is_not_json_is_worded_not_parsed_aloud(status, kind, sentence):
    """A web server or proxy at the product's address answers with a page, not
    the product's JSON. The card says what happened, never the parser's text."""
    async def respond(request):
        return web.Response(status=status, text="<html><body>Bad Gateway</body></html>",
                            content_type="text/html")

    app = web.Application()
    app.router.add_get("/v1/snapshot", respond)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        driver = object.__new__(EquipmentDriver)
        driver._sdk_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        async with ClientSession() as client:
            driver._sdk_http = client
            with pytest.raises(Exception) as caught:
                await driver._request("/v1/snapshot")
    finally:
        await runner.cleanup()
    assert type(caught.value).__name__ == kind
    assert str(caught.value) == sentence
    if status == 401:
        assert caught.value.fault_code == "auth_failed"


@pytest.mark.asyncio
async def test_a_refusal_the_product_words_itself_is_kept():
    async def respond(request):
        return web.json_response({"message": "Acme: remote control is locked at the front panel."},
                                 status=423)

    app = web.Application()
    app.router.add_get("/v1/snapshot", respond)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        driver = object.__new__(EquipmentDriver)
        driver._sdk_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        async with ClientSession() as client:
            driver._sdk_http = client
            with pytest.raises(ValueError, match="remote control is locked at the front panel"):
                await driver._request("/v1/snapshot")
    finally:
        await runner.cleanup()
