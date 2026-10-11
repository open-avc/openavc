"""Built-in Test Connection follows ownership on both existing entry points."""

import json
from unittest.mock import AsyncMock

import pytest

from openavc.api.routes import devices as routes
from openavc.core.engine import Engine
from openavc.core.project_loader import DeviceConfig, ProjectConfig
from openavc.drivers import registry
from tests.test_ai_device_tools_hardening import _pure_handler


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['acme_amplifier', 'acme_switcher'])
@pytest.mark.parametrize('door', ['rest', 'ai'])
async def test_model_scoped_local_reference_uses_host_diagnostic(tmp_path, monkeypatch, model, door):
    manifest = tmp_path / 'host.json'
    manifest.write_text(json.dumps({'device_id': 'local', 'identity': 'unit-a', 'integration': model,
                                    'name': 'Built-in equipment', 'connection': {}}))
    monkeypatch.setenv('OPENAVC_EQUIPMENT_MANIFEST', str(manifest))
    monkeypatch.setattr(registry, '_DRIVER_REGISTRY', dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, '_RESERVED_DRIVERS', set(registry._RESERVED_DRIVERS))
    engine = Engine(str(tmp_path / 'project.avc'))
    policy = engine.installed_equipment
    engine.project = policy.compose(ProjectConfig(project={'id': 'p', 'name': 'P'}, devices=[
        DeviceConfig(id='retained', name='Imported label', driver=policy.driver_id, config={'integration': model})]), for_load=True)
    expected = {'success': True, 'error': None, 'latency_ms': 1}
    diagnostic = AsyncMock(return_value=expected)
    monkeypatch.setattr(policy, 'test_connection', diagnostic)
    try:
        if door == 'rest':
            monkeypatch.setattr(routes, '_get_engine', lambda: engine)
            result = await routes.test_device_connection('retained')
        else:
            handler = _pure_handler()
            monkeypatch.setattr(handler, '_get_engine', lambda: engine)
            result = await handler._test_device_connection({'device_id': 'retained'})
        assert result == expected
        diagnostic.assert_awaited_once_with()
    finally:
        await engine.stop()
