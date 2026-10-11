"""Move imported programming onto permanent equipment through Reassign Driver."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import secrets
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from playwright.sync_api import expect
import pytest

from openavc.core.installed_equipment import model_driver_id


@pytest.fixture
def hardware(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Acme test equipment")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert = tmp_path / "equipment.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key = tmp_path / "equipment-key.pem"
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    token = secrets.token_hex(32)
    model = {"id": "acme_amplifier_v2", "name": "Acme Amplifier", "manufacturer": "Acme",
        "category": "audio", "version": "1.0.0", "author": "OpenAVC", "transport": "http",
        "description": "Test equipment", "config_schema": {},
        "commands": {"increment": {"label": "Increment Once", "params": {}}},
        "state_variables": {"counter": {"type": "integer", "label": "Completed Commands"}}}
    state = {"writes": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value):
            content = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def authorized(self):
            if self.headers.get("Authorization") == "Bearer " + token:
                return True
            self.send_error(401)
            return False

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == "/v1/description":
                self.reply({"protocol": "openavc-device-1", "identity": "receiving-unit", "session": "current", "capabilities": model})
            else:
                self.reply({"identity": "receiving-unit", "session": "current", "quality": "current",
                            "state": {"counter": state["writes"]}, "children": []})

        def do_POST(self):
            if not self.authorized():
                return
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/v1/command" or value.get("command") != "increment":
                self.send_error(422)
                return
            state["writes"] += 1
            self.reply({"id": value["id"], "outcome": "completed"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"device_id": "local", "identity": "receiving-unit",
        "integration": model["id"], "name": "Built-in amplifier", "connection": {
            "url": f"https://127.0.0.1:{server.server_port}", "token": token, "ca_file": str(cert)}}))
    try:
        yield manifest, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("claimed", [False, True])
def test_import_and_reassign_uses_existing_programming(server_factory, page, tmp_path, hardware, claimed):
    manifest, state = hardware
    password = secrets.token_urlsafe(24)
    environment = {"OPENAVC_EQUIPMENT_MANIFEST": str(manifest)}
    if claimed:
        environment.update(OPENAVC_PROGRAMMER_USERNAME="admin", OPENAVC_PROGRAMMER_PASSWORD=password,
                           OPENAVC_ALLOW_ANONYMOUS="false")
    handle = server_factory(project_overrides={"devices": []}, env=environment)
    foreign = {"openavc_version": "0.14.0", "project": {"id": "hall", "name": "Hall"},
        "devices": [{"id": "amp", "name": "House amplifier", "driver": model_driver_id("acme_amplifier"),
                     "config": {"integration": "acme_amplifier"}}],
        "macros": [{"id": "operate", "name": "Operate amplifier", "steps": [
            {"action": "device.command", "device": "amp", "command": "increment", "params": {}}]}]}
    source = tmp_path / "hall.avc"
    source.write_text(json.dumps(foreign))
    page.goto(handle.base_url + "/programmer/", wait_until="domcontentloaded")
    if claimed:
        page.get_by_label("Username").fill("admin")
        page.get_by_label("Password").fill(password)
        page.get_by_role("button", name="Sign In", exact=True).click()
    page.get_by_role("button", name="Program", exact=True).click()
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="Import", exact=True).first.click()
    chooser.value.set_files(str(source))
    missing = page.get_by_role("dialog", name="Missing drivers")
    expect(missing).to_be_visible(timeout=30000)
    missing.get_by_role("button", name="Skip", exact=True).click()
    before = json.loads(handle.project_path.read_text())
    page.get_by_role("button", name="Devices", exact=True).click()
    page.get_by_role("button").filter(has_text="House amplifier").click()
    page.get_by_role("button", name="Reassign Driver", exact=True).click()
    dialog = page.get_by_role("dialog", name="Edit Device")
    dialog.get_by_placeholder("Search drivers...").fill("Product hardware")
    dialog.get_by_text("Product hardware", exact=True).click()
    expect(dialog.get_by_text("Use this product's hardware", exact=False)).to_be_visible()
    expect(dialog.get_by_text("Connection Settings", exact=True)).to_have_count(0)
    page.screenshot(path=str(tmp_path / "select-built-in.png"), full_page=True)
    with page.expect_response(lambda response: response.request.method == "PATCH" and response.url.endswith("/api/devices/amp")) as saved:
        dialog.get_by_role("button", name="Save Changes", exact=True).click()
    assert saved.value.status == 200, saved.value.text()
    expect(dialog).not_to_be_visible()
    project = json.loads(handle.project_path.read_text())
    assert [device["id"] for device in project["devices"]] == ["amp"]
    assert project["macros"] == before["macros"]
    assert project["devices"][0]["driver"] == model_driver_id("acme_amplifier_v2")
    page.wait_for_function("async () => (await (await fetch('/api/devices/amp')).json()).connected === true")
    expect(page.get_by_role("region", name="Product hardware")).to_be_visible(timeout=15000)
    expect(page.get_by_text("Driver Not Installed", exact=True)).to_have_count(0)
    page.screenshot(path=str(tmp_path / "reassigned-equipment.png"), full_page=True)
    page.get_by_role("button", name="Macros", exact=True).click()
    page.get_by_text("Operate amplifier", exact=True).first.click()
    with page.expect_response(lambda response: "/api/macros/operate" in response.url and response.request.method == "POST") as operated:
        page.get_by_role("button", name="Test", exact=True).click()
    assert operated.value.status == 200
    page.wait_for_function("async () => (await (await fetch('/api/devices/amp')).json()).state.counter === 1")
    assert state["writes"] == 1
    page.screenshot(path=str(tmp_path / "imported-macro-completed.png"), full_page=True)
