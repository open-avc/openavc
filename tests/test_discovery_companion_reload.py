"""A driver installed after the server started takes part in the next scan.

Discovery loads every ``*_discovery.py`` companion once, at start, so a driver
installed from Browse Drivers ran no discovery check of its own until a
restart, and one uninstalled kept running its check. Installing, updating and
uninstalling all end in ``refresh_all_device_matches``, which now loads the
companions again. An invented driver.
"""

from __future__ import annotations

import pytest

from openavc import system_config
from openavc.api import discovery as discovery_api
from openavc.discovery.engine import DiscoveryEngine

COMPANION = "async def probe(ctx):\n    return None\n"


@pytest.fixture
def engine(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    builtin = tmp_path / "definitions"
    repo.mkdir()
    builtin.mkdir()
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", builtin)
    eng = DiscoveryEngine()

    async def no_catalog(force_catalog: bool = False) -> None:
        return None

    monkeypatch.setattr(eng, "refresh_signal_index_with_catalog", no_catalog)
    monkeypatch.setattr(discovery_api, "_engine", eng)
    eng.load_discovery_companions_from_dirs([builtin, repo])
    return eng, repo


async def test_an_installed_drivers_check_runs_without_a_restart(engine):
    eng, repo = engine
    assert "acme_widget" not in eng.discovery_companions

    (repo / "acme_widget_discovery.py").write_text(COMPANION, encoding="utf-8")
    await discovery_api.refresh_all_device_matches()
    assert "acme_widget" in eng.discovery_companions

    (repo / "acme_widget_discovery.py").unlink()
    await discovery_api.refresh_all_device_matches()
    assert "acme_widget" not in eng.discovery_companions
