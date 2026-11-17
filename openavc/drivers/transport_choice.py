"""THE rule for which transport a device connects over.

The device's own choice comes first: its config's ``transport``, picked from
its driver's ``transports`` in Edit Device, or the simulation's TCP stand-in
for a serial device. Else its driver's declared ``transport`` (``DRIVER_INFO``
for a Python driver, the definition for a YAML one, which carries the same
key). Else tcp.

A null, a blank or a missing value is no choice, at either level: a driver
file that writes ``transport: null`` reads as tcp everywhere, as it would with
the key left out. The driver checker refuses a YAML file written that way at
the doors where a driver is written; this is how one already installed is
read. A name is read lower-cased, as the transports are lower-case words and
every transport builder is keyed by one, so ``Serial`` in a hand-written
project connects as serial, the way Edit Device shows it.

Every reader asks this: the connect, the YAML runtime's choice of wire form,
a device's connection record (``core/device_config.connection_of``), the
connection test, the audit, the web page probe, discovery's hints and the
driver listing the Programmer reads. Standard library only, with no package
import, so any layer can ask it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The transport of a driver that declares none.
DEFAULT_TRANSPORT = "tcp"


def _named(value: Any) -> str:
    """A transport name as the builders key it; "" for no name."""
    if not value:
        return ""
    return str(value).strip().lower()


def configured_transport(config: Mapping[str, Any] | None) -> str:
    """The transport a device's config picks, or "" when it picks none."""
    if not isinstance(config, Mapping):
        return ""
    return _named(config.get("transport"))


def declared_transport(info: Mapping[str, Any] | None) -> str:
    """The transport a driver declares (its ``DRIVER_INFO`` or definition),
    tcp when it declares none."""
    if not isinstance(info, Mapping):
        return DEFAULT_TRANSPORT
    return _named(info.get("transport")) or DEFAULT_TRANSPORT


def effective_transport(
    config: Mapping[str, Any] | None, info: Mapping[str, Any] | None
) -> str:
    """The transport a device with this config connects over, on a driver
    that declares ``info``."""
    return configured_transport(config) or declared_transport(info)
