"""Setting who can connect for one test, without touching a real adapter.

``use_plan`` makes the process's listen plan what the test says; the
autouse ``_reset_global_state`` fixture in conftest forgets it afterwards.
"""

from __future__ import annotations

from openavc import listen
from openavc.listen import Adapter, ListenChoice, ListenPlan

ETHERNET = Adapter("Ethernet", "50-EB-F6-D2-FC-29", (("10.20.30.5", "10.20.30.0/24"),), True)
WIFI = Adapter("Wi-Fi", "F0-57-A6-A8-D4-0F", (("192.168.1.40", "192.168.1.0/24"),), True)


def plan_for(mode: str, adapter: Adapter | None = None, adapters=None) -> ListenPlan:
    identity = adapter.identity() if adapter is not None else {}
    found = adapters if adapters is not None else ([adapter] if adapter else [])
    return listen.resolve(ListenChoice(mode=mode, adapter=identity), found)


def use_plan(mode: str, adapter: Adapter | None = None, adapters=None) -> ListenPlan:
    plan = plan_for(mode, adapter, adapters)
    listen.set_current(plan)
    return plan


def use_bind(value: str) -> ListenPlan:
    """The plan ``OPENAVC_BIND=value`` gives, on a machine where an adapter
    holds any literal address it names."""
    from openavc.system_config import parse_bind_variable

    mode, identity = parse_bind_variable(value)
    address = identity.get("address")
    adapters = [Adapter("Ethernet", "", ((address, f"{address}/24"),), True)] if address else []
    plan = listen.resolve(ListenChoice(mode=mode, adapter=identity), adapters)
    listen.set_current(plan)
    return plan
