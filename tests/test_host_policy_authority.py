"""Observe credential revocation through real writers, Settings and sockets."""
import asyncio
import base64
import copy
import json
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI, Request
import pytest

from openavc.api import auth, session_tokens, ws
from openavc.api._engine import set_engine
from openavc.api.host_policy import (
    HostPolicyMiddleware, ProgrammerRoute, configure_policy,
    request_admission, synchronous_response_commit,
)
from openavc.api.routes import auth as auth_routes, scripts, system
from openavc.core.engine import Engine
from openavc.core.host_policy import HostPolicy, HostPolicyDenied, PolicySnapshot
from openavc.core.project_loader import ProjectConfig


class Product(HostPolicy):
    current = PolicySnapshot(1, True, True)

    def snapshot(self):
        return self.current


@pytest.fixture
def rig(tmp_path, isolated_auth_config, monkeypatch):
    cfg = isolated_auth_config
    monkeypatch.setattr(cfg, '_data', copy.deepcopy(cfg._data))
    monkeypatch.setattr(cfg, '_file_data', copy.deepcopy(cfg._file_data))
    cfg.set('auth', 'programmer_username', 'engineer')
    cfg.set('auth', 'programmer_password', 'disposable-password')
    cfg.set('auth', 'api_key', '')
    clock = [1000.0]
    monkeypatch.setattr(session_tokens, 'time', SimpleNamespace(time=lambda: clock[0]))
    store = session_tokens.SessionTokenStore(ttl=10)
    monkeypatch.setattr(session_tokens, 'store', store)
    engine = Engine(str(tmp_path / 'project.avc'))
    engine.project = ProjectConfig.model_validate({
        'project': {'id': 'example', 'name': 'Example'},
        'scripts': [{'id': 'example', 'file': 'example.py'}],
    })
    set_engine(engine)
    script = tmp_path / 'scripts/example.py'
    script.parent.mkdir()
    script.write_text('original = True\n')
    app, host = FastAPI(), Product()
    configure_policy(app, host)
    app.add_middleware(HostPolicyMiddleware)
    for router in (scripts.router, system.router, auth_routes.open_router):
        app.include_router(router, prefix='/api')
    app.include_router(ws.router)
    token, _ = store.issue(auth.credential_fingerprint())
    return SimpleNamespace(app=app, host=host, cfg=cfg, engine=engine,
                           script=script, token=token, clock=clock, store=store)


def scope(path, token='', method='GET', socket=False, headers=()):
    path, _, query = path.partition('?')
    supplied = list(headers)
    if token:
        supplied.append((b'authorization', ('Bearer ' + token).encode()))
    return {'type': 'websocket' if socket else 'http',
            'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'method': method,
            'path': path, 'raw_path': path.encode(), 'root_path': '',
            'query_string': query.encode(), 'scheme': 'ws' if socket else 'http',
            'server': ('test', 80), 'client': ('192.0.2.1', 1234),
            'http_version': '1.1', 'subprotocols': [], 'headers': supplied}


async def http(rig, path, *, token=None, method='GET', body=None, before_body=None, headers=()):
    sent = []
    request = scope(path, rig.token if token is None else token, method, headers=headers)
    if body is not None:
        request['headers'].append((b'content-type', b'application/json'))
    async def receive():
        if before_body:
            await before_body()
        return {'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False}
    async def send(message):
        sent.append(message)
    await asyncio.wait_for(rig.app(request, receive, send), 2)
    return sent[0]['status'], json.loads(b''.join(m.get('body', b'') for m in sent))


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['logout', 'expiry', 'password', 'key'])
async def test_revoked_upload_does_not_write_or_inherit_another_grant(rig, change):
    headers, token = [], rig.token
    if change == 'key':
        rig.cfg.set('auth', 'api_key', 'first-key')
        token = ''
        headers = [(b'x-api-key', b'first-key'), (b'authorization', b'Basic ' +
                   base64.b64encode(b'engineer:disposable-password'))]
    async def invalidate():
        if change == 'expiry':
            rig.clock[0] += 11
        elif change == 'logout':
            assert (await http(rig, '/api/auth/session', method='DELETE'))[1]['revoked']
        else:
            field = {'programmer_password': 'replacement-password'} if change == 'password' else {'api_key': ''}
            assert (await http(rig, '/api/system/config', method='PATCH', body={'auth': field}))[0] == 200
    result = await http(rig, '/api/scripts/example/source', method='PUT', token=token,
                        body={'source': 'forbidden = True\n'}, before_body=invalidate, headers=headers)
    assert result[0] == 401
    assert result[1]['code'] == 'authentication_required'
    assert rig.script.read_text() == 'original = True\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['logout', 'expiry', 'password'])
async def test_existing_socket_stops_mutating_when_its_grant_ends(rig, change):
    queue, sent = asyncio.Queue(), []
    ready, ack, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    await queue.put({'type': 'websocket.connect'})
    async def send(message):
        sent.append(message)
        if message['type'] == 'websocket.close':
            closed.set()
        if message['type'] == 'websocket.send':
            kind = json.loads(message.get('text', '{}')).get('type')
            if kind == 'ui.definition':
                ready.set()
            if kind == 'state.set.ack':
                ack.set()
    task = asyncio.create_task(rig.app(scope('/ws?client=programmer', rig.token, socket=True), queue.get, send))
    try:
        await asyncio.wait_for(ready.wait(), 1)
        await queue.put({'type': 'websocket.receive', 'text': json.dumps({
            'type': 'state.set', 'key': 'var.authority', 'value': 1})})
        await asyncio.wait_for(ack.wait(), 1)
        assert rig.engine.state.get('var.authority') == 1
        if change == 'logout':
            assert (await http(rig, '/api/auth/session', method='DELETE'))[1]['revoked']
        elif change == 'expiry':
            rig.clock[0] += 9
            await asyncio.sleep(.15)  # A watcher must not renew an idle session.
            rig.clock[0] += 2
        else:
            result = await http(rig, '/api/system/config', method='PATCH', body={
                'auth': {'programmer_password': 'replacement-password'}})
            assert result == (200, {'status': 'updated', 'updated_sections': ['auth']})
            assert auth._check_credentials('engineer', 'replacement-password')
        await asyncio.wait_for(closed.wait(), 1)
        await queue.put({'type': 'websocket.receive', 'text': json.dumps({
            'type': 'state.set', 'key': 'var.authority', 'value': 99})})
        await asyncio.wait_for(task, 1)
        assert rig.engine.state.get('var.authority') == 1
        assert sent[-1]['code'] == 4001
        assert (await http(rig, '/api/scripts/example/source'))[0] == 401
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('disable', [False, True])
async def test_committed_settings_acknowledgement_preserves_host_revocation(rig, monkeypatch, disable):
    entered, release = asyncio.Event(), asyncio.Event()
    async def reconcile():
        entered.set()
        await release.wait()
    monkeypatch.setattr(rig.engine, 'reconcile_runtime_services', reconcile)
    pending = asyncio.create_task(http(rig, '/api/system/config', method='PATCH', body={
        'auth': {'programmer_password': 'replacement-password'}, 'discovery': {'advertise': rig.cfg.get('discovery', 'advertise')}}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert auth._check_credentials('engineer', 'replacement-password')
        assert (await http(rig, '/api/scripts/example/source'))[0] == 401
        if disable:
            rig.host.current = PolicySnapshot(2, False, True)
            rig.host.refresh()
        release.set()
        result = await asyncio.wait_for(pending, 1)
        assert result[0] == (403 if disable else 200)
        assert not rig.host._commits and not rig.host._admissions
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_completed_save_only_allows_its_response(rig, fail):
    router = APIRouter(route_class=ProgrammerRoute)
    @router.post('/finish')
    async def finish(request: Request):
        lease = request_admission(request)
        try:
            with synchronous_response_commit(request):
                if fail:
                    raise OSError('injected save failure')
        except OSError:
            assert not lease.response_only
            assert not rig.host._commits
            return {'failed': True}
        with pytest.raises(HostPolicyDenied):
            lease.begin_commit()
        with pytest.raises(HostPolicyDenied):
            await request.body()
        return {'finished': True}
    rig.app.include_router(router)
    result = await http(rig, '/finish', method='POST')
    assert result == (200, {'failed': True} if fail else {'finished': True})


@pytest.mark.asyncio
async def test_settings_replace_failure_does_not_authorize_a_success_response(rig, monkeypatch):
    from openavc import system_config
    rig.cfg.save()
    previous = rig.cfg.file_path.read_bytes()
    admissions = []
    replace = system_config.os.replace
    def fail_replace(source, destination):
        if str(destination) == str(rig.cfg.file_path):
            admissions.extend(rig.host._admissions)
            raise OSError('injected settings replacement failure')
        return replace(source, destination)
    monkeypatch.setattr(system_config.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='injected settings replacement'):
        await http(rig, '/api/system/config', method='PATCH', body={
            'auth': {'programmer_password': 'replacement-password'}})
    assert rig.cfg.file_path.read_bytes() == previous
    assert admissions and all(not lease.response_only for lease in admissions)
    assert not rig.host._commits and not rig.host._admissions
