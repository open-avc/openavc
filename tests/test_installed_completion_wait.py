"""Product hardware keeps one project setting: how long a command waits to finish."""

import pytest

from openavc.core.installed_equipment import InstalledEquipment, InstalledEquipmentError
from openavc.core.project_loader import ProjectConfig

MANIFEST = {"device_id": "local", "identity": "unit-1", "integration": "acme_amplifier",
            "name": "Acme Amplifier", "connection": {"url": "https://127.0.0.1:9443", "token": "fixture"}}
POLICY = InstalledEquipment(MANIFEST)


def _project(*devices):
    return ProjectConfig.model_validate({"project": {"id": "hall", "name": "Hall"}, "devices": list(devices)})


def _product(**config):
    return {"id": "local", "name": "Amplifier", "driver": POLICY.driver_id,
            "config": {"integration": "acme_amplifier", **config}}


def test_the_wait_is_kept_and_reaches_the_driver():
    device = POLICY.compose(_project(_product(completion_wait=12.5))).devices[0]
    assert device.config == {"integration": "acme_amplifier", "completion_wait": 12.5}
    assert POLICY.resolve(device)["config"]["completion_wait"] == 12.5
    unset = POLICY.compose(_project(_product())).devices[0]
    assert "completion_wait" not in POLICY.resolve(unset)["config"]


@pytest.mark.parametrize("wait", ["12", -1, 601, True])
def test_a_wait_that_is_not_seconds_in_range_is_refused(wait):
    with pytest.raises(InstalledEquipmentError, match="Wait for completion must be a number of seconds"):
        POLICY.compose(_project(_product(completion_wait=wait)))


def test_the_connection_is_still_not_the_projects():
    with pytest.raises(InstalledEquipmentError, match="cannot be changed"):
        POLICY.compose(_project(_product(url="https://192.0.2.9:9443")))


def test_network_equipment_loaded_onto_this_product_keeps_its_wait():
    remote = {"id": "amp", "name": "Amplifier", "driver": "openavc_equipment",
              "config": {"host": "192.0.2.9", "username": "admin", "reported_identity": "unit-1",
                         "reported_model": "acme_amplifier", "completion_wait": 20}}
    device = POLICY.compose(_project(remote), for_load=True).devices[0]
    assert (device.id, device.driver) == ("amp", POLICY.driver_id)
    assert device.config == {"integration": "acme_amplifier", "completion_wait": 20}
