"""The project's own secrets reach the log redaction wherever the project is replaced.

The mesh key (``isc.auth_key``) belongs to the project rather than to a device,
so no driver registers it. The engine does, every time ``self.project`` is
replaced: at start, on every save or push, and when a failed reconcile rolls
back. It is registered whether the mesh runs or not, because the key is in the
project either way.

An invented project throughout.
"""

from __future__ import annotations

import json

import pytest

from openavc.core.project_loader import ISCConfig, ProjectConfig, ProjectMeta
from openavc.utils.log_redaction import get_secret_registry


@pytest.fixture(autouse=True)
def clean_registry():
    registry = get_secret_registry()
    registry.clear()
    yield registry
    registry.clear()


@pytest.fixture
def engine(tmp_path, monkeypatch):
    from openavc.core.engine import Engine

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(data))
    eng = Engine(str(tmp_path / "project" / "project.avc"))
    eng.project_path.parent.mkdir(parents=True)
    return eng


def _project(key: str, *, enabled: bool = False, name: str = "Hall") -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id="p1", name=name),
        isc=ISCConfig(enabled=enabled, auth_key=key),
    )


class _Loaded(Exception):
    """Stops a startup once the project has been put in place."""


async def test_start_registers_the_mesh_key_with_the_mesh_off(engine, monkeypatch, clean_registry):
    engine.project_path.write_text(
        json.dumps(_project("mesh-key-4417").model_dump(mode="json")), encoding="utf-8",
    )
    real = engine._apply_project_settings

    def applied_then_stop():
        real()
        raise _Loaded()

    monkeypatch.setattr(engine, "_apply_project_settings", applied_then_stop)
    with pytest.raises(_Loaded):
        await engine.start()

    assert engine.project.isc.enabled is False
    assert clean_registry.redact_any("key mesh-key-4417") == "key ***"


async def test_a_save_replaces_the_registered_key(engine, clean_registry):
    engine.project = _project("mesh-key-4417")
    engine._apply_project_settings()
    try:
        await engine.apply_project(_project("mesh-key-9902", name="Hall B"))
        assert clean_registry.redact_any("mesh-key-4417 mesh-key-9902") == "mesh-key-4417 ***"

        await engine.apply_project(_project("", name="Hall C"))
        assert clean_registry.has_secrets() is False
    finally:
        await engine.stop()


async def test_a_failed_reconcile_puts_the_previous_key_back(engine, monkeypatch, clean_registry):
    engine.project = _project("mesh-key-4417")
    engine._apply_project_settings()

    async def broken(diff, origin):
        raise RuntimeError("reconcile failed")

    monkeypatch.setattr(engine, "_reconcile", broken)
    with pytest.raises(RuntimeError):
        await engine.apply_project(_project("mesh-key-9902", name="Hall B"))

    assert engine.project.isc.auth_key == "mesh-key-4417"
    assert clean_registry.redact_any("mesh-key-4417 mesh-key-9902") == "*** mesh-key-9902"
