"""Product hardware's driver ID is a hash; nothing an integrator reads shows it."""

import logging
from pathlib import Path

import pytest

from openavc.core.installed_equipment import (
    InstalledEquipment,
    driver_label,
    is_product_hardware_id,
    missing_driver_reason,
    model_driver_id,
)
from openavc.discovery.hints import load_discovery_hints
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver

DRIVER_LABEL_TS = (Path(__file__).resolve().parents[1]
                   / "openavc/web/programmer/src/components/shared/driverLabel.ts")

MANIFEST = {"device_id": "local", "identity": "unit-1", "integration": "acme_amplifier",
            "name": "Acme Amplifier", "connection": {"url": "https://127.0.0.1:9443", "token": "fixture"}}


@pytest.fixture
def drivers(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", {})
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set())


def test_the_programmer_reads_the_same_id_shape():
    driver_id = model_driver_id("acme_amplifier")
    assert is_product_hardware_id(driver_id)
    assert not is_product_hardware_id("openavc_builtin_widget")
    assert "/^openavc_builtin_[0-9a-f]{64}$/" in DRIVER_LABEL_TS.read_text()


def test_log_lines_and_a_missing_driver_name_product_hardware():
    assert missing_driver_reason(model_driver_id("acme_amplifier_mk1")) == (
        "Product hardware from another product model is not available on this server."
    )
    assert missing_driver_reason("acme_widget") == "Driver 'acme_widget' is not installed"
    assert driver_label(model_driver_id("acme_amplifier")) == "the product hardware"
    assert driver_label("acme_widget") == "driver 'acme_widget'"


def test_product_hardware_starts_without_a_discovery_warning(drivers, caplog):
    InstalledEquipment(MANIFEST).register()

    class Declared(BaseDriver):
        DRIVER_INFO = {"id": "acme_declared", "name": "Acme", "transport": "tcp", "discovery": {}}

    registry.register_driver(Declared)
    listed = {entry["id"]: entry for entry in registry.list_registered_drivers()}
    assert "discovery" not in listed[model_driver_id("acme_amplifier")]
    with caplog.at_level(logging.WARNING, logger="openavc.discovery.hints"):
        load_discovery_hints(list(listed.values()))
    warned = [r.getMessage() for r in caplog.records if "declares no fingerprints" in r.getMessage()]
    assert warned == ["acme_declared: discovery block declares no fingerprints or hints; "
                      "this driver will never participate in matching."]
