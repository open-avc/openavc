"""The Code view's Save & Reload and New Driver when the registry refuses the class.

A driver whose id is computed (so the file's own reading of it passes the
reserved-id check) can still turn out to declare a reserved id once it runs.
The registry refuses it at registration; the driver the devices were using
stays registered and its module stays resident, and a create leaves no file.
Invented device (Acme).
"""

import sys

import pytest

from openavc.api.models import PythonDriverCreateRequest
from openavc.api.routes import drivers, python_drivers
from openavc.core.installed_equipment import InstalledEquipmentError
from openavc.drivers import registry
from openavc.drivers.driver_loader import load_python_driver_file
from openavc.drivers.python_modules import forget_module, module_name

PROTECTED = "acme_installed"

SERVING = (
    "from openavc.drivers.base import BaseDriver\n"
    "class AcmeWidget(BaseDriver):\n"
    '    DRIVER_INFO = {"id": "acme_widget", "name": "Acme Widget"}\n'
)
# The id is worked out when the file runs, so reading the file finds none.
COMPUTED_RESERVED = (
    "from openavc.drivers.base import BaseDriver\n"
    'IDENTITY = "acme_" + "installed"\n'
    "class AcmeWidget(BaseDriver):\n"
    '    DRIVER_INFO = {"id": IDENTITY, "name": "Acme Widget"}\n'
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", {PROTECTED})
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", {})
    monkeypatch.setattr(drivers, "_get_driver_repo_dir", lambda: tmp_path)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", tmp_path)
    yield tmp_path
    for path in tmp_path.glob("*.py"):
        forget_module(path)
    forget_module(tmp_path / "acme_widget.py")
    forget_module(tmp_path / "acme_new.py")


@pytest.mark.asyncio
async def test_a_refused_reload_keeps_the_driver_the_devices_use(repo):
    path = repo / "acme_widget.py"
    path.write_text(SERVING, encoding="utf-8")
    serving = load_python_driver_file(path)
    registry.register_driver(serving)
    resident = sys.modules[module_name(path)]

    path.write_text(COMPUTED_RESERVED, encoding="utf-8")
    with pytest.raises(InstalledEquipmentError, match="manufacturer"):
        await python_drivers.reload_python_driver_endpoint("acme_widget")

    assert registry.get_driver_class("acme_widget") is serving
    assert sys.modules.get(module_name(path)) is resident


@pytest.mark.asyncio
async def test_a_refused_create_leaves_no_file(repo):
    body = PythonDriverCreateRequest(id="acme_new", source=COMPUTED_RESERVED)
    with pytest.raises(InstalledEquipmentError, match="manufacturer"):
        await python_drivers.create_python_driver(body)

    assert not (repo / "acme_new.py").exists()
    assert module_name(repo / "acme_new.py") not in sys.modules
    assert not registry.is_driver_registered("acme_new")
