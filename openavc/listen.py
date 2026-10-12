"""Who can connect: Settings > Network > Accept connections from.

The choice (``network.accept_connections_from``) and the addresses the web
listeners bind for it:

==================  =====================================================
``any``             ``0.0.0.0``: every network this computer is on
``adapter``         that adapter's IPv4 address(es), plus ``127.0.0.1``
``this_computer``   ``127.0.0.1``
==================  =====================================================

**Loopback is in every choice.** The computer's own screen, the tray and menu
bar, the Pi kiosk, the appliance's watchdog, the cloud tunnel and every
loopback trust rule (``utils/request_origin.py``) reach the server on
``127.0.0.1``. Listening on one adapter alone would cut all of them off, and
the appliance's watchdog would then restart the server in a loop.

**The adapter is stored by identity, never by address**
(``network.accept_connections_adapter`` = ``{"name", "mac"}``), matched MAC
first, then name, and resolved to its addresses at every start. A choice
stored as an address breaks at the next DHCP renewal. ``OPENAVC_BIND`` is the
one exception: it holds the choice when set, and a literal address in it is
an identity of ``{"address": ...}`` (``system_config.parse_bind_variable``).

**A chosen adapter that is missing listens on this computer only**: unplugged,
renamed, waiting for DHCP. Never every network, because whoever chose one
adapter chose to keep the others out, and never a restart loop. The plan then
carries a :class:`ListenProblem` that every surface says in the same words,
and :class:`ListenWatcher` restarts the server when the adapter's addresses
change, which is also how a DHCP renewal is followed.

**The default is the install type's.** The key is new, so its absence is a
reliable signal on every existing system (the old ``bind_address`` is written
with a default by nearly every system that has run once, and cannot be told
apart from a choice). :func:`settle_choice` writes the default on the first
start: ``any`` on every installed type, and on a run from source whatever
``bind_address`` held, so a checkout keeps listening where it did. It runs on
the server only, never in ``SystemConfig.load()``: the simulator and the
driver validator load the same file, and that import closure is held to the
standard library (``tests/test_compiled_protocol_purity.py``).

**Rollback.** ``network.bind_address`` is kept as a mirror of the choice in
the terms an earlier release understands (``0.0.0.0``, ``127.0.0.1``, or the
adapter's address), refreshed whenever the choice is settled or changed, so a
rollback starts on a sane listen address. An adapter that cannot be resolved
mirrors to ``127.0.0.1``: the earlier release would refuse to start on an
address this computer does not have, and widening to every network is the
other thing this setting exists to prevent.

The resolved plan is process state, set by ``main()`` once the sockets are
bound (:func:`set_current`): ``python -m openavc.main`` imports the served app
as a second module, so a global in ``main.py`` is invisible to it, and this
module is shared by both.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Iterable

from openavc.utils.logger import get_logger

log = get_logger(__name__)

MODE_KEY = "accept_connections_from"
ADAPTER_KEY = "accept_connections_adapter"
MIRROR_KEY = "bind_address"

MODE_ANY = "any"
MODE_ADAPTER = "adapter"
MODE_THIS_COMPUTER = "this_computer"
MODES = (MODE_ANY, MODE_ADAPTER, MODE_THIS_COMPUTER)

LOOPBACK = "127.0.0.1"
WILDCARD = "0.0.0.0"

# Every type that a person installs and then reaches from a touch panel. The
# from-source run (git_dev) and an unrecognised layout keep listening on this
# computer only unless their own bind_address said otherwise.
INSTALLED_TYPES = frozenset({
    "windows_installer",
    "linux_package",
    "macos_app",
    "docker",
    "android_appliance",
})

_ANY_SPELLINGS = ("", "0.0.0.0", "::", "[::]", "*")
_LOOPBACK_SPELLINGS = ("127.0.0.1", "::1", "[::1]", "localhost")

# Labels as Settings shows them. One term per thing: the docs and the
# Programmer use these words.
LABEL_ANY = "Any network"
LABEL_THIS_COMPUTER = "This computer only"

_NOT_ACCEPTING = "Not accepting connections from the network"


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Adapter:
    """One physical adapter: its OS name, MAC, IPv4 addresses and link.

    ``link_local`` is its 169.254 addresses, which nothing here listens on or
    shows; Reach devices through binds one for a device at a 169.254 address
    (``openavc/reach_devices.py``).
    """

    name: str
    mac: str = ""
    addresses: tuple[tuple[str, str], ...] = ()  # (ip, subnet)
    link: bool = True
    link_local: tuple[tuple[str, str], ...] = ()  # (ip, subnet)

    @property
    def ips(self) -> tuple[str, ...]:
        return tuple(ip for ip, _ in self.addresses)

    @classmethod
    def from_inventory(cls, entry: dict[str, Any]) -> Adapter:
        def pairs(key: str) -> tuple[tuple[str, str], ...]:
            return tuple(
                (str(a.get("ip") or ""), str(a.get("subnet") or ""))
                for a in entry.get(key) or ()
                if a.get("ip")
            )

        return cls(
            name=str(entry.get("name") or ""),
            mac=str(entry.get("mac") or ""),
            addresses=pairs("addresses"),
            link=bool(entry.get("link", True)),
            link_local=pairs("link_local"),
        )

    def identity(self) -> dict[str, str]:
        return {"name": self.name, "mac": self.mac}


def list_adapters() -> list[Adapter]:
    """Every physical adapter on this computer, connected or not. Blocking."""
    from openavc.discovery.network_scanner import get_adapter_inventory

    return [Adapter.from_inventory(entry) for entry in get_adapter_inventory()]


def normalize_mac(mac: Any) -> str:
    """A MAC with case and separators removed, so Windows' ``50-EB-F6-...``
    and Linux's ``50:eb:f6:...`` compare equal."""
    return "".join(ch for ch in str(mac or "").lower() if ch in "0123456789abcdef")


def match_adapter(identity: dict[str, Any], adapters: Iterable[Adapter]) -> Adapter | None:
    """The adapter ``identity`` names: by MAC first, then by name.

    MAC first because a renamed adapter keeps its MAC; name second because a
    replaced network card keeps its connection name on Windows. An identity
    that is only an address (``OPENAVC_BIND``) matches the adapter holding it.
    """
    adapters = list(adapters)
    mac = normalize_mac(identity.get("mac"))
    if mac:
        for adapter in adapters:
            if normalize_mac(adapter.mac) == mac:
                return adapter
    name = str(identity.get("name") or "").strip().casefold()
    if name:
        for adapter in adapters:
            if adapter.name.casefold() == name:
                return adapter
    address = str(identity.get("address") or "").strip()
    if address and not mac and not name:
        for adapter in adapters:
            if address in adapter.ips:
                return adapter
    return None


def is_literal(identity: dict[str, Any]) -> bool:
    """An identity that is an address only, which only ``OPENAVC_BIND`` and a
    from-source ``bind_address`` no adapter holds can produce."""
    return bool(identity.get("address")) and not identity.get("name") and not identity.get("mac")


# ---------------------------------------------------------------------------
# The choice and the plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListenChoice:
    """What was chosen, before it is resolved against the adapters present."""

    mode: str
    adapter: dict[str, str] = field(default_factory=dict)
    held_by: str | None = None  # the environment variable holding it, if any

    @classmethod
    def from_config(cls, cfg: Any) -> ListenChoice:
        mode = str(cfg.get("network", MODE_KEY) or "")
        if mode not in MODES:
            mode = MODE_THIS_COMPUTER
        identity = cfg.get("network", ADAPTER_KEY) or {}
        if not isinstance(identity, dict) or mode != MODE_ADAPTER:
            identity = {}
        clean = {k: str(v) for k, v in identity.items() if k in ("name", "mac", "address") and v}
        return cls(mode=mode, adapter=clean, held_by=cfg.env_override("network", MODE_KEY))

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "adapter": dict(self.adapter)}


@dataclass(frozen=True)
class ListenProblem:
    """Why the network is not being listened on, said once for every surface."""

    code: str  # adapter_not_connected | adapter_no_address | address_not_found
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _adapter_phrase(name: str) -> str:
    return f"the {name} adapter" if name else "the chosen adapter"


def problem_not_connected(name: str) -> ListenProblem:
    return ListenProblem(
        "adapter_not_connected",
        f"{_NOT_ACCEPTING}: {_adapter_phrase(name)} isn't connected.",
    )


def problem_no_address(name: str) -> ListenProblem:
    return ListenProblem(
        "adapter_no_address",
        f"{_NOT_ACCEPTING}: {_adapter_phrase(name)} has no address yet.",
    )


def problem_address_not_found(address: str) -> ListenProblem:
    return ListenProblem(
        "address_not_found",
        f"{_NOT_ACCEPTING}: this computer has no address {address}.",
    )


@dataclass(frozen=True)
class ListenPlan:
    """A choice resolved to addresses: what the web listeners bind.

    ``interfaces`` is what the adapter enumeration found for the choice (an
    adapter's ``(ip, subnet)`` pairs); ``dropped`` is any of those the bind
    then refused (an address the OS listed but would not let a socket use, as
    Windows does for an unplugged adapter's static address). The watcher
    compares enumerations, never binds, so a refused address cannot restart
    the server in a loop.
    """

    choice: ListenChoice
    adapter_name: str = ""
    interfaces: tuple[tuple[str, str], ...] = ()
    dropped: tuple[str, ...] = ()
    problem: ListenProblem | None = None

    @property
    def mode(self) -> str:
        return self.choice.mode

    @property
    def network_addresses(self) -> tuple[str, ...]:
        """The adapter addresses listened on (adapter choice only)."""
        return tuple(ip for ip, _ in self.interfaces if ip not in self.dropped)

    @property
    def network_subnets(self) -> tuple[str, ...]:
        return tuple(subnet for ip, subnet in self.interfaces if ip not in self.dropped)

    @property
    def hosts(self) -> tuple[str, ...]:
        """Every address a web listener binds, loopback always among them."""
        if self.mode == MODE_ANY:
            return (WILDCARD,)
        return self.network_addresses + (LOOPBACK,)

    @property
    def local_only(self) -> bool:
        """Nothing on the network can connect: this computer only, or an
        adapter choice whose adapter is missing."""
        if self.mode == MODE_ANY:
            return False
        return not self.network_addresses

    @property
    def reaches_network(self) -> bool:
        return not self.local_only

    @property
    def legacy_bind(self) -> str:
        """The one address an earlier release would have bound for this."""
        if self.mode == MODE_ANY:
            return WILDCARD
        if self.local_only:
            return LOOPBACK
        return self.network_addresses[0]

    @property
    def label(self) -> str:
        return choice_label(self.mode, self.adapter_name or self.choice.adapter.get("address", ""))

    def serves(self, host: str) -> bool:
        """Whether a connection to this computer's ``host`` is accepted.

        Exact: ``0.0.0.0`` accepts every address, but a listener bound to
        ``127.0.0.1`` refuses a browser that opened ``127.0.0.2``, although
        both are loopback.
        """
        host = (host or "").strip().strip("[]")
        if self.mode == MODE_ANY or host.lower() == "localhost":
            return True
        return host in self.hosts

    def without(self, addresses: Iterable[str]) -> ListenPlan:
        """This plan with ``addresses`` refused by the bind. An adapter choice
        left with none listens on this computer only and says why."""
        dropped = tuple(sorted(set(self.dropped) | set(addresses)))
        plan = replace(self, dropped=dropped)
        if plan.mode == MODE_ADAPTER and not plan.network_addresses and plan.problem is None:
            plan = replace(plan, problem=problem_not_connected(self.adapter_name))
        return plan

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "adapter": dict(self.choice.adapter),
            "adapter_name": self.adapter_name,
            "label": self.label,
            "addresses": list(self.hosts),
            "network_addresses": list(self.network_addresses),
            "local_only": self.local_only,
            "held_by": self.choice.held_by,
            "problem": self.problem.to_dict() if self.problem else None,
        }


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def choice_label(mode: str, adapter_name: str = "") -> str:
    """The choice as Settings lists it: "Any network", "Ethernet only"."""
    if mode == MODE_ANY:
        return LABEL_ANY
    if mode == MODE_THIS_COMPUTER:
        return LABEL_THIS_COMPUTER
    return f"{adapter_name} only" if adapter_name else "One network only"


def choice_phrase(mode: str, adapter_name: str = "") -> str:
    """The same, inside a sentence: "any network", "Ethernet only"."""
    label = choice_label(mode, adapter_name)
    return label if mode == MODE_ADAPTER else label[0].lower() + label[1:]


def resolve(choice: ListenChoice, adapters: Iterable[Adapter]) -> ListenPlan:
    """``choice`` against the adapters present now. Pure: tests hand it a list."""
    if choice.mode != MODE_ADAPTER:
        return ListenPlan(choice=choice)
    adapters = list(adapters)
    identity = choice.adapter
    if is_literal(identity):
        address = identity["address"]
        holder = next((a for a in adapters if address in a.ips), None)
        if holder is None:
            return ListenPlan(choice=choice, problem=problem_address_not_found(address))
        if not holder.link:
            return ListenPlan(
                choice=choice,
                adapter_name=holder.name,
                problem=problem_not_connected(holder.name),
            )
        subnet = dict(holder.addresses).get(address, "")
        return ListenPlan(choice=choice, adapter_name=holder.name, interfaces=((address, subnet),))

    adapter = match_adapter(identity, adapters)
    name = adapter.name if adapter else str(identity.get("name") or "")
    if adapter is None or not adapter.link:
        return ListenPlan(choice=choice, adapter_name=name, problem=problem_not_connected(name))
    if not adapter.addresses:
        return ListenPlan(choice=choice, adapter_name=name, problem=problem_no_address(name))
    return ListenPlan(choice=choice, adapter_name=name, interfaces=adapter.addresses)


# ---------------------------------------------------------------------------
# Settling the choice on a system that has never had one
# ---------------------------------------------------------------------------


def default_mode(deployment_type: str) -> str:
    return MODE_ANY if deployment_type in INSTALLED_TYPES else MODE_THIS_COMPUTER


AdapterSource = Iterable[Adapter] | Callable[[], Iterable[Adapter]]


def _adapters_of(source: AdapterSource) -> list[Adapter]:
    """The adapter list, enumerated only when a caller actually needs it: most
    choices never name an adapter, and enumeration is a system call per start."""
    return list(source() if callable(source) else source)


def convert_bind_address(value: Any, adapters: AdapterSource) -> tuple[str, dict[str, str]]:
    """What a from-source system's ``bind_address`` meant, as a choice."""
    if value is None:
        return MODE_THIS_COMPUTER, {}
    text = str(value).strip()
    if text in _ANY_SPELLINGS:
        return MODE_ANY, {}
    if text.lower() in _LOOPBACK_SPELLINGS:
        return MODE_THIS_COMPUTER, {}
    text = text.strip("[]")
    holder = next((a for a in _adapters_of(adapters) if text in a.ips), None)
    if holder is not None:
        return MODE_ADAPTER, holder.identity()
    return MODE_ADAPTER, {"address": text}


def _deployment_type() -> str:
    from openavc.updater.platform import detect_deployment_type

    return detect_deployment_type().value


def settle_choice(
    cfg: Any,
    adapters: AdapterSource,
    deployment_type: str | None = None,
) -> bool:
    """Write the install type's default for a system with no choice yet, and
    keep the rollback mirror current. Returns True when a default was written.

    Writes the persisted layer only (``persist_default``): an ``OPENAVC_BIND``
    keeps winning at runtime and never reaches the file. The caller saves, or
    the next save writes it.
    """
    stored = cfg.stored("network", MODE_KEY)
    settled = False
    if stored not in MODES:
        if stored not in (None, ""):
            log.warning(
                "network.%s is %r, which is not a choice; using this install's default",
                MODE_KEY, stored,
            )
        kind = deployment_type or _deployment_type()
        if kind in INSTALLED_TYPES:
            mode, identity = MODE_ANY, {}
        else:
            mode, identity = convert_bind_address(
                cfg.stored("network", MIRROR_KEY), adapters
            )
        cfg.persist_default("network", MODE_KEY, mode)
        cfg.persist_default("network", ADAPTER_KEY, identity)
        log.info(
            "Accept connections from: %s (this install's default)",
            choice_label(mode, identity.get("name") or identity.get("address", "")),
        )
        settled = True
    refresh_mirror(cfg, adapters)
    return settled


def mirror_for(mode: str, identity: dict[str, Any], adapters: AdapterSource) -> str:
    """``bind_address`` as an earlier release reads it, for this choice."""
    if mode == MODE_ANY:
        return WILDCARD
    if mode != MODE_ADAPTER:
        return LOOPBACK
    plan = resolve(ListenChoice(mode=mode, adapter=dict(identity)), _adapters_of(adapters))
    return plan.legacy_bind


def refresh_mirror(cfg: Any, adapters: AdapterSource) -> None:
    """Recompute the rollback mirror from the choice in the FILE (never the
    environment's: an environment value never reaches system.json)."""
    mode = cfg.stored("network", MODE_KEY)
    identity = cfg.stored("network", ADAPTER_KEY) or {}
    if mode not in MODES:
        return
    value = mirror_for(mode, identity if isinstance(identity, dict) else {}, adapters)
    if cfg.stored("network", MIRROR_KEY) != value:
        cfg.persist_default("network", MIRROR_KEY, value)


# ---------------------------------------------------------------------------
# The plan this process runs
# ---------------------------------------------------------------------------

_current: ListenPlan | None = None


def start(
    cfg: Any = None,
    adapters: Iterable[Adapter] | None = None,
    deployment_type: str | None = None,
) -> ListenPlan:
    """Settle the choice, resolve it against the adapters present, and make
    that the current plan. ``main()`` calls it before binding; anything that
    asks :func:`current` first (a test client, a tool) gets the same."""
    from openavc.system_config import get_system_config

    cfg = cfg if cfg is not None else get_system_config()
    found: list[Adapter] | None = list(adapters) if adapters is not None else None

    def present() -> list[Adapter]:
        nonlocal found
        if found is None:
            found = list_adapters()
        return found

    settle_choice(cfg, present, deployment_type)
    # Reach devices through is settled here too: the same first start, the
    # same adapter list (openavc/reach_devices.py).
    from openavc import reach_devices

    try:
        reach_devices.settle(cfg, present)
    except Exception:  # never block startup on the device-side choice
        log.exception("Could not settle Reach devices through")
    choice = ListenChoice.from_config(cfg)
    plan = resolve(choice, present() if choice.mode == MODE_ADAPTER else [])
    set_current(plan)
    return plan


def current() -> ListenPlan:
    """The plan this server is listening with."""
    if _current is None:
        return start()
    return _current


def set_current(plan: ListenPlan) -> None:
    global _current
    _current = plan


def reset() -> None:
    """Forget the current plan (tests)."""
    global _current
    _current = None


def panel_addresses(plan: ListenPlan) -> list[str]:
    """The addresses a panel on the network can open under ``plan``, best
    first. Every reachable address for any network; the adapter's own for one
    adapter; none for this computer only."""
    if plan.local_only:
        return []
    if plan.mode == MODE_ANY:
        from openavc.discovery.network_scanner import get_ranked_interface_ips

        return get_ranked_interface_ips()
    return list(plan.network_addresses)


def certificate_addresses(plan: ListenPlan) -> list[str] | None:
    """The addresses the self-signed certificate must name, beyond loopback:
    None means every adapter (any network)."""
    if plan.mode == MODE_ANY:
        return None
    return list(plan.network_addresses)


# ---------------------------------------------------------------------------
# What a change would do (the Settings screen asks before saving)
# ---------------------------------------------------------------------------


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _in_subnets(address: str, subnets: Iterable[str]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    for subnet in subnets:
        try:
            if ip in ipaddress.ip_network(subnet, strict=False):
                return True
        except ValueError:
            continue
    return False


def keeps_connection(plan: ListenPlan, through: str, from_this_computer: bool) -> bool:
    """Whether the person on a connection that reached this computer at
    ``through`` can keep working under ``plan`` without walking anywhere: the
    address still answers, or they are on this computer (its own screen, any
    loopback address, the cloud tunnel), which always reaches ``127.0.0.1``.
    The safety net is armed exactly when this is False."""
    through = (through or "").strip().strip("[]")
    return from_this_computer or _is_loopback(through) or plan.serves(through)


def preview(
    candidate: ListenChoice,
    adapters: Iterable[Adapter],
    *,
    connected_through: str,
    from_this_computer: bool,
    approved_panel_addresses: Iterable[str] = (),
    isc_enabled: bool = False,
) -> dict[str, Any]:
    """What saving ``candidate`` would do, for the person about to save it.

    ``connected_through`` is this computer's address the asking browser
    reached (the request's server-side address); ``from_this_computer`` is
    True for the computer's own screen and the cloud tunnel, which arrive on
    loopback and keep working under every choice. One rule, here, for the
    Settings screen and anything else that asks.
    """
    plan = resolve(candidate, adapters)
    through = (connected_through or "").strip().strip("[]")
    keeps = keeps_connection(plan, through, from_this_computer)

    consequences: list[dict[str, Any]] = []
    approved = [a for a in approved_panel_addresses if a]
    if plan.local_only:
        stranded = len(approved)
    elif plan.mode == MODE_ADAPTER:
        stranded = sum(
            1 for a in approved
            if not _is_loopback(a) and not _in_subnets(a, plan.network_subnets)
        )
    else:
        stranded = 0
    if stranded:
        consequences.append({
            "code": "approved_panels",
            "count": stranded,
            "message": (
                f"{stranded} approved touch {_plural(stranded, 'panel', 'panels')} "
                f"will stop connecting."
            ),
        })
    if plan.local_only and isc_enabled:
        consequences.append({
            "code": "inter_system",
            "count": 1,
            "message": "Other OpenAVC systems can't reach this one (Inter-System is on).",
        })

    return {
        "mode": plan.mode,
        "adapter_name": plan.adapter_name,
        "label": plan.label,
        "panel_addresses": panel_addresses(plan),
        "connected_through": through,
        # Whether the address this browser opened still answers. Can be false
        # while the connection is kept: a browser on this computer at a
        # loopback address the choice leaves out moves to 127.0.0.1.
        "serves_this_address": plan.serves(through),
        "keeps_this_connection": keeps,
        "safety_net": not keeps,
        "problem": plan.problem.to_dict() if plan.problem else None,
        "consequences": consequences,
    }


# ---------------------------------------------------------------------------
# Following the adapter
# ---------------------------------------------------------------------------


def address_available(address: str) -> bool:
    """Whether a socket can be bound to ``address`` now (any free port)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((address, 0))
        return True
    except OSError:
        return False


class ListenWatcher:
    """Restarts the server when the chosen adapter's addresses change.

    Every ``interval`` seconds, while the choice is one adapter: enumerate the
    adapters, resolve the choice again and compare with what this process
    resolved at start. A difference seen on ``confirm_polls`` checks in a row
    (an adapter coming back, unplugged, or renewed onto a new address by DHCP)
    restarts the server through the same path as Settings' restart, and the
    service manager brings it back on the new addresses. An address that
    enumerated but was refused at the bind is retried with a trial bind, so
    it is picked up the moment the OS allows it. A restart rather than a
    re-bind in place: uvicorn cannot swap the sockets of a running server, and
    a restart is the path every install type already handles.

    ``on_tick`` runs after each check whatever the choice (the safety net's
    deadline uses it).
    """

    def __init__(
        self,
        restart: Callable[[str], Awaitable[None]],
        *,
        adapters: Callable[[], list[Adapter]] = list_adapters,
        plan: Callable[[], ListenPlan] = current,
        available: Callable[[str], bool] = address_available,
        on_tick: Callable[[], Awaitable[None]] | None = None,
        interval: float = 10.0,
        confirm_polls: int = 2,
    ) -> None:
        self._restart = restart
        self._adapters = adapters
        self._plan = plan
        self._available = available
        self._on_tick = on_tick
        self._interval = interval
        self._confirm_polls = max(1, confirm_polls)
        self._seen: tuple[str, ...] | None = None
        self._count = 0
        self._restarting = False

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let a bad poll stop the watching
                log.exception("Accept connections from: adapter check failed")

    async def tick(self) -> None:
        if not self._restarting:
            reason = await self._check_adapter()
            if reason:
                self._restarting = True
                log.warning("Accept connections from: %s; restarting to follow it", reason)
                await self._restart(reason)
                return
        if self._on_tick is not None:
            await self._on_tick()

    async def _check_adapter(self) -> str | None:
        plan = self._plan()
        if plan.mode != MODE_ADAPTER:
            return None
        found = await asyncio.to_thread(self._adapters)
        fresh = resolve(plan.choice, found).network_addresses
        before = tuple(ip for ip, _ in plan.interfaces)
        adapter = f"the {plan.adapter_name} adapter" if plan.adapter_name else "the chosen adapter"
        if fresh == before:
            refused = [ip for ip in plan.dropped if ip in fresh]
            usable = refused and all(
                await asyncio.gather(*(asyncio.to_thread(self._available, ip) for ip in refused))
            )
            if not usable:
                self._seen, self._count = None, 0
                return None
            key = ("usable",) + fresh
            reason = f"{adapter} can be listened on now ({', '.join(refused)})"
        elif not fresh:
            key, reason = fresh, f"{adapter} is no longer connected"
        elif not before:
            key, reason = fresh, f"{adapter} is connected ({', '.join(fresh)})"
        else:
            key = fresh
            reason = f"{adapter}'s address changed from {', '.join(before)} to {', '.join(fresh)}"
        if key != self._seen:
            self._seen, self._count = key, 0
        self._count += 1
        return reason if self._count >= self._confirm_polls else None
