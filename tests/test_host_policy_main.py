"""Exercise canonical application registration in a disposable process.

No Engine startup or external I/O runs: this qualifies routing, middleware,
body admission and static responses, not the runtime/provider delivery bundle.
"""
import os
from pathlib import Path
import subprocess
import sys


def test_main_routes_and_startup_responses_obey_host_policy(tmp_path):
    code = r'''
import asyncio
import sys

def audit(event, args):
    if event in {"socket.connect", "socket.bind", "socket.sendto", "subprocess.Popen", "os.system"}:
        raise RuntimeError("External I/O is forbidden in this route fixture: " + event)
sys.addaudithook(audit)

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from openavc import main
from openavc.api.host_policy import validate_policies
from openavc.core.host_policy import HostPolicy, PolicySnapshot

class ProductPolicy(HostPolicy):
    current = PolicySnapshot(1, True, True)
    def snapshot(self):
        return self.current

validate_policies(main.app)
host = ProductPolicy()
main.configure_host_policy(host)
assert main.engine.host_policy is host
# Use TestClient without entering lifespan. Static files and the real router/
# middleware execute; no device, discovery, cloud or update startup runs.
client = TestClient(main.app)
try:
    assert client.get('/panel/').status_code == 200
    assert client.get('/docs').status_code == 200
    assert client.get('/absent').status_code == 404
    host.current = PolicySnapshot(2, False, True)
    assert client.get('/panel/').status_code == 200
    assert client.get('/panel/index.html?edit=1').status_code == 403
    assert client.get('/docs').status_code == 403
    assert client.get('/api/startup-status').status_code == 200
    host.current = PolicySnapshot(3, True, False)
    assert client.get('/panel/').status_code == 403
    assert client.get('/panel/panel.js').status_code == 200
    assert client.get('/docs').status_code == 200
    with client.websocket_connect('/ws?client=panel') as ws:
        try:
            ws.receive_json()
        except WebSocketDisconnect as error:
            assert error.code == 4013
        else:
            raise AssertionError('Disabled panel stayed open')

    main.app.state.engine_ready = False
    main.app.state.engine_error = None
    host.current = PolicySnapshot(4, False, False)
    assert client.get('/panel/').status_code == 403  # No startup-splash bypass.
    assert client.get('/api/startup-status').json() == {'ready': False, 'error': None}

    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'method': 'POST', 'path': '/api/auth/setup', 'raw_path': b'/api/auth/setup',
             'query_string': b'', 'root_path': '', 'scheme': 'http',
             'headers': [(b'transfer-encoding', b'chunked')],
             'server': ('test', 80), 'client': ('127.0.0.1', 23456), 'http_version': '1.1'}
    sent = []
    async def receive():
        raise AssertionError('Disabled upload was consumed')
    async def send(message):
        sent.append(message)
    asyncio.run(main.app(scope, receive, send))
    assert sent[0]['status'] == 403
finally:
    client.close()
'''
    env = {**os.environ, "OPENAVC_DATA_DIR": str(tmp_path / "data"),
           "OPENAVC_PROJECT": str(tmp_path / "data/projects/example/project.avc")}
    result = subprocess.run([sys.executable, "-c", code], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
