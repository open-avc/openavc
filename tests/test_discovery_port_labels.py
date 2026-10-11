"""The names Discovery shows beside a device's open ports.

A port several drivers declare belongs to none of them in particular, so it
is never labelled with whichever driver happened to be read first: that put
one model's name on a port of its sibling model. The results also say which
drivers claim each port, so a device's card can name its own driver.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openavc.api.discovery import _build_port_labels


def _hint(driver_id, name, manufacturer, probe_port=None, port_open=()):
    return SimpleNamespace(
        driver_id=driver_id,
        driver_name=name,
        manufacturer=manufacturer,
        tcp_probe=SimpleNamespace(port=probe_port) if probe_port else None,
        port_open=list(port_open),
    )


class _Index:
    def __init__(self, drivers):
        self._drivers = drivers

    async def get_drivers(self):
        return self._drivers


def _engine(hints, catalog=()):
    return SimpleNamespace(discovery_hints=hints, community_index=_Index(list(catalog)))


def _labels(hints, catalog=()):
    return asyncio.run(_build_port_labels(_engine(hints, catalog)))


def test_a_port_one_driver_declares_carries_its_name():
    labels, drivers = _labels([_hint("acme_widget", "Acme Widget", "Acme", probe_port=17300)])
    assert labels["17300"] == "Acme Widget"
    assert drivers["17300"] == ["acme_widget"]


def test_a_port_two_models_share_is_labelled_by_their_maker():
    labels, drivers = _labels([
        _hint("acme_widget", "Acme Widget", "Acme", probe_port=17300),
        _hint("acme_widget_pro", "Acme Widget Pro", "Acme", probe_port=17300,
              port_open=[17200]),
    ])
    assert labels["17300"] == "Acme"
    assert drivers["17300"] == ["acme_widget", "acme_widget_pro"]
    assert labels["17200"] == "Acme Widget Pro"


def test_a_port_two_makers_share_names_both():
    labels, _ = _labels([
        _hint("acme_widget", "Acme Widget", "Acme", probe_port=4100),
        _hint("zenith_box", "Zenith Box", "Zenith", port_open=[4100]),
    ])
    assert labels["4100"] == "Acme Widget or Zenith Box"


def test_a_port_many_makers_share_says_how_many():
    hints = [
        _hint(f"maker{n}_box", f"Maker{n} Box", f"Maker{n}", probe_port=4100)
        for n in range(4)
    ]
    labels, drivers = _labels(hints)
    assert labels["4100"] == "4 drivers"
    assert len(drivers["4100"]) == 4


def test_a_catalog_driver_counts_once_beside_its_installed_copy():
    labels, drivers = _labels(
        [_hint("acme_widget", "Acme Widget", "Acme", probe_port=17300)],
        catalog=[
            {"id": "acme_widget", "name": "Acme Widget", "manufacturer": "Acme", "ports": [17300]},
            {"id": "acme_dsp", "name": "Acme DSP", "manufacturer": "Acme", "ports": [1702]},
        ],
    )
    assert labels["17300"] == "Acme Widget"
    assert drivers["17300"] == ["acme_widget"]
    assert labels["1702"] == "Acme DSP"


def test_the_generic_ports_keep_their_generic_names():
    labels, drivers = _labels([_hint("acme_widget", "Acme Widget", "Acme", probe_port=80)])
    assert labels["80"] == "HTTP"
    assert labels["23"] == "Telnet"
    assert "80" not in drivers
