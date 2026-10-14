"""Which adapter device traffic uses: Settings > Network > Reach devices through.

The choice (``network.reach_devices_through``) is ``{}`` for every connected
adapter (each device is reached through whichever adapter this computer routes
it to, and discovery scans every adapter with a link), or one adapter by
identity, ``{"name", "mac"}``, matched MAC first, then name, exactly as Accept
connections from names its adapter (``openavc/listen.py``). Device connections,
discovery probes, multicast joins and the callback address a device is told to
post to all bind to that adapter's address.

**Stored by identity, read as an address.** The readers need an address to
bind; :func:`address` resolves the identity against the adapters present and
keeps the answer, because a connection asks for it every time and enumerating
adapters is a system call. The server's ten-second tick (``main.py``) calls
:func:`refresh` off the event loop, so a DHCP renewal is followed within one
tick, with no restart.

**A chosen adapter that is missing keeps its last address.** Device traffic
then fails to leave this computer rather than going out through another
network, which is what choosing one adapter was for, and every surface that
already explains an address no adapter holds (discovery, the Discovery view)
explains it. Same rule as Accept connections from: never widen.

**A device at a 169.254 address** is reached from the chosen adapter's own
169.254 address (:func:`link_local_address`), and told to post its updates
to it: many link-local devices answer only a source on their own link, which
is why they work with every adapter (the OS picks a 169.254 source) and not
from the adapter's other address.
An adapter that holds none still binds its other address, never a wider one;
:func:`missing_link_local` is what the device card says then. A literal
address (``OPENAVC_CONTROL_INTERFACE``) is bound as it is, for every device.

**``OPENAVC_CONTROL_INTERFACE``** keeps its meaning: one literal address, or
empty for every adapter. It holds the choice when set, as
``{"address": ...}`` (``system_config.parse_control_interface_variable``).

**Rollback.** ``network.control_interface`` is kept as the resolved address,
the form earlier releases read, refreshed when the choice is settled at start
or changed. Nothing in this release reads it.

**Settling** (:func:`settle`): a system with no choice yet converts what
``control_interface`` held, at the server's first start: empty is every
adapter; an address becomes the adapter holding it; an address no adapter
holds stays that address, so a stale pin behaves as it did and the screens
that explain one still do.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

from openavc.utils.logger import get_logger

log = get_logger(__name__)

KEY = "reach_devices_through"
MIRROR_KEY = "control_interface"

LABEL_EVERY = "Every connected adapter"

# How long a resolved address is trusted when nothing refreshes it (a process
# without the server's tick: a test, a tool). The server refreshes every tick.
_STALE_AFTER = 60.0


@dataclass(frozen=True)
class Resolved:
    """The choice resolved against the adapters present.

    ``link_local_address`` is the chosen adapter's first 169.254 address, or
    "" (none held, every adapter, or a literal address). Not in
    :meth:`to_dict`: Settings lists and shows an adapter by its other
    addresses.
    """

    identity: dict[str, str]
    address: str = ""
    adapter_name: str = ""
    connected: bool = True
    link_local_address: str = ""

    def to_dict(self) -> dict[str, Any]:
        every = not self.identity
        return {
            "mode": "every" if every else "adapter",
            "adapter": dict(self.identity),
            "adapter_name": self.adapter_name,
            "address": self.address,
            "connected": self.connected,
            "label": LABEL_EVERY if every else (self.adapter_name or self.address),
        }


def clean_identity(value: Any) -> dict[str, str]:
    """The stored choice as an identity: ``{}`` for every adapter."""
    if not isinstance(value, dict):
        return {}
    return {k: str(v) for k, v in value.items() if k in ("name", "mac", "address") and v}


def choice(cfg: Any = None) -> dict[str, str]:
    """The choice this server runs with (the environment's when it holds it)."""
    if cfg is None:
        from openavc.system_config import get_system_config

        cfg = get_system_config()
    return clean_identity(cfg.get("network", KEY))


def resolve(identity: dict[str, str], adapters: Iterable[Any], last: str = "") -> Resolved:
    """``identity`` against ``adapters``. Pure: tests hand it a list.

    ``last`` is the address this choice resolved to before (the rollback
    mirror), kept when the adapter is missing so traffic never widens.
    """
    from openavc import listen

    identity = clean_identity(identity)
    if not identity:
        return Resolved(identity={})
    adapters = list(adapters)
    if listen.is_literal(identity):
        address = identity["address"]
        holder = next((a for a in adapters if address in a.ips), None)
        return Resolved(
            identity=identity,
            address=address,
            adapter_name=holder.name if holder else "",
            connected=bool(holder and holder.link),
        )
    adapter = listen.match_adapter(identity, adapters)
    name = adapter.name if adapter else identity.get("name", "")
    link_local = adapter.link_local[0][0] if adapter is not None and adapter.link_local else ""
    if adapter is None or not adapter.ips:
        return Resolved(
            identity=identity, address=last, adapter_name=name, connected=False,
            link_local_address=link_local,
        )
    return Resolved(
        identity=identity,
        address=adapter.ips[0],
        adapter_name=name,
        connected=adapter.link,
        link_local_address=link_local,
    )


# ---------------------------------------------------------------------------
# The address the readers bind
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cached: tuple[tuple, Resolved, float] | None = None


def _key(identity: dict[str, str]) -> tuple:
    return tuple(sorted(identity.items()))


def _last(cfg: Any) -> str:
    return str(cfg.stored("network", MIRROR_KEY) or "").strip()


def current(cfg: Any = None, adapters: Iterable[Any] | None = None) -> Resolved:
    """The choice resolved now: from the cache when it is for this choice and
    fresh, otherwise by enumerating the adapters (blocking)."""
    global _cached
    if cfg is None:
        from openavc.system_config import get_system_config

        cfg = get_system_config()
    identity = choice(cfg)
    if not identity:
        return Resolved(identity={})
    from openavc import listen

    if listen.is_literal(identity) and adapters is None:
        # No enumeration needed to know what to bind.
        return Resolved(identity=identity, address=identity["address"])
    key = _key(identity)
    with _lock:
        cached = _cached
    if adapters is None and cached and cached[0] == key and time.monotonic() - cached[2] < _STALE_AFTER:
        return cached[1]
    found = list(adapters) if adapters is not None else listen.list_adapters()
    resolved = resolve(identity, found, _last(cfg))
    with _lock:
        _cached = (key, resolved, time.monotonic())
    return resolved


def address() -> str:
    """The local address device traffic binds to, or "" for every adapter.

    THE one question every reader asks: device connections
    (``transport/endpoint.py``), discovery, the multicast listener, the
    device audit and the callback URL a device is told to post to.
    """
    try:
        return current().address
    except Exception:  # never fail a connect over the lookup
        log.debug("Reach devices through: lookup failed", exc_info=True)
        return ""


def link_local_address() -> str:
    """The chosen adapter's 169.254 address, or "": what a connection to a
    device at a 169.254 address binds (``transport/endpoint.py``)."""
    try:
        return current().link_local_address
    except Exception:  # never fail a connect over the lookup
        log.debug("Reach devices through: lookup failed", exc_info=True)
        return ""


def missing_link_local(host: Any) -> Resolved | None:
    """The choice, when ``host`` is a 169.254 address and the chosen adapter,
    connected, holds no 169.254 address to reach it from; otherwise None
    (every adapter, a literal address, another host, an adapter that is not
    connected, or one that holds a 169.254 address).

    The device card and the connection test ask it when a connection
    failed, to say so instead of "check the IP address"."""
    from openavc.transport.endpoint import is_link_local

    if not is_link_local(host):
        return None
    try:
        resolved = current()
    except Exception:
        log.debug("Reach devices through: lookup failed", exc_info=True)
        return None
    from openavc import listen

    if not resolved.identity or listen.is_literal(resolved.identity):
        return None
    if not resolved.connected or resolved.link_local_address:
        return None
    return resolved


def broadcast_sources(adapters: Iterable[Any] | None = None) -> list[str]:
    """Where a broadcast to 255.255.255.255 is sent from with every adapter:
    the first address of each connected adapter, one per adapter, and an
    adapter's 169.254 address when it holds no other (a network with no DHCP
    server). Blocking when ``adapters`` is not given (it enumerates them).

    An unbound limited broadcast leaves through the one adapter the OS picks:
    on Windows the lowest interface metric, which on a computer with WSL or
    Hyper-V is the virtual switch, so it reaches no network at all. A socket
    bound to an adapter's address sends it out that adapter (Windows and
    Linux alike), so ``BaseDriver.send_udp`` sends one from each of these,
    and so does the Inter-System beacon with Accept connections from on Any
    network (``core/isc.py``). A multicast group's adapter is picked the same
    way, so a multicast send with nothing chosen goes out once through each
    of these too, its adapter set per copy (``UDPTransport.send_to``).
    Virtual adapters are not in the inventory.
    """
    from openavc import listen

    if adapters is None:
        try:
            adapters = listen.list_adapters()
        except Exception:  # never fail a send over the lookup: it goes unbound
            log.debug("Reach devices through: adapter lookup failed", exc_info=True)
            return []
    return [
        adapter.ips[0] if adapter.ips else adapter.link_local[0][0]
        for adapter in adapters
        if adapter.link and (adapter.ips or adapter.link_local)
    ]


# How long a sender that asks often trusts the list before it is read again.
_SOURCES_FRESH_FOR = 10.0

_sources_lock = threading.Lock()
_kept_sources: tuple[list[str], float] | None = None
_sources_reading = False


def _read_sources() -> list[str]:
    """:func:`broadcast_sources` now, kept. Blocking."""
    global _kept_sources, _sources_reading
    try:
        sources = broadcast_sources()
        with _sources_lock:
            _kept_sources = (sources, time.monotonic())
        return sources
    finally:
        with _sources_lock:
            _sources_reading = False


async def kept_broadcast_sources() -> list[str]:
    """:func:`broadcast_sources` for a sender that asks often: the
    Inter-System beacon every five seconds, a multicast stream (sACN) many
    times a second, where reading the adapters per send costs more than the
    send.

    The answer is kept for ten seconds. An older one is still used while it
    is read again off the event loop, so no send waits for the adapters to
    be read, except the first, which has nothing to use yet. An adapter
    unplugged in between costs a copy that fails and is skipped.
    """
    global _sources_reading
    with _sources_lock:
        kept = _kept_sources
        stale = kept is None or time.monotonic() - kept[1] >= _SOURCES_FRESH_FOR
        start = stale and not _sources_reading
        if start:
            _sources_reading = True
    if kept is None:
        return await asyncio.to_thread(_read_sources)
    if start:
        asyncio.get_running_loop().run_in_executor(None, _read_sources)
    return list(kept[0])


def refresh(cfg: Any = None, adapters: Iterable[Any] | None = None) -> Resolved:
    """Resolve again now and keep the answer. The server's tick calls it off
    the event loop; a change of choice calls it too. Logs a moved address."""
    global _cached
    with _lock:
        before = _cached[1].address if _cached else None
        _cached = None
    resolved = current(cfg, adapters)
    if before is not None and resolved.address and before != resolved.address:
        log.info(
            "Reach devices through: %s moved from %s to %s",
            resolved.adapter_name or "the chosen adapter", before, resolved.address,
        )
    return resolved


def forget() -> None:
    """Drop the kept answers (a changed choice, tests)."""
    global _cached, _kept_sources
    with _lock:
        _cached = None
    with _sources_lock:
        _kept_sources = None


# ---------------------------------------------------------------------------
# Settling and the rollback mirror
# ---------------------------------------------------------------------------


def convert_control_interface(value: Any, adapters: Iterable[Any]) -> dict[str, str]:
    """What an earlier release's ``control_interface`` meant, as a choice."""
    text = str(value or "").strip()
    if not text:
        return {}
    holder = next((a for a in adapters if text in a.ips), None)
    if holder is not None:
        return holder.identity()
    return {"address": text}


def settle(cfg: Any, adapters: Any) -> bool:
    """Convert ``control_interface`` for a system with no choice yet, and keep
    the rollback mirror current. ``adapters`` is a list or a callable that
    enumerates them, only called when a conversion or the mirror needs it.
    Returns True when a choice was written. The caller saves."""
    from openavc import listen

    def present() -> list:
        return list(adapters() if callable(adapters) else adapters)

    stored = cfg.stored("network", KEY)
    settled = False
    if not isinstance(stored, dict):
        old = str(cfg.stored("network", MIRROR_KEY) or "").strip()
        identity = convert_control_interface(old, present() if old else [])
        cfg.persist_default("network", KEY, identity)
        if identity:
            log.info(
                "Reach devices through: %s (from the earlier control interface %s)",
                identity.get("name") or identity.get("address"), old,
            )
        settled = True
    identity = clean_identity(cfg.stored("network", KEY))
    if identity and not listen.is_literal(identity):
        refresh_mirror(cfg, present())
    elif listen.is_literal(identity) and cfg.stored("network", MIRROR_KEY) != identity["address"]:
        cfg.persist_default("network", MIRROR_KEY, identity["address"])
    elif not identity and cfg.stored("network", MIRROR_KEY):
        cfg.persist_default("network", MIRROR_KEY, "")
    return settled


def refresh_mirror(cfg: Any, adapters: Iterable[Any]) -> None:
    """``control_interface`` as an earlier release reads it, for the choice in
    the FILE (an environment value never reaches system.json)."""
    identity = clean_identity(cfg.stored("network", KEY))
    value = resolve(identity, adapters, _last(cfg)).address if identity else ""
    if cfg.stored("network", MIRROR_KEY) != value:
        cfg.persist_default("network", MIRROR_KEY, value)
