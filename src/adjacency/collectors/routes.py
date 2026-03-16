"""Collect routing table entries and extract next-hop neighbors.

NAPALM's ``get_route_to(destination='')`` returns the full routing table
on supported platforms.  We extract next-hops that are *immediately adjacent*
— meaning the next-hop IP falls within one of the device's own connected
subnets — and emit one :class:`NeighborRecord` per unique
(outgoing_interface, next_hop) pair.
"""

from __future__ import annotations

import ipaddress

from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

from adjacency.models import DataSource, NeighborRecord

# Protocols whose "next-hop" is the device itself — never a neighbor.
_LOCAL_PROTOCOLS = frozenset({"connected", "local", "direct"})


def connected_subnets(
    ip_data: dict,
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Extract connected subnets from NAPALM ``get_interfaces_ip`` data.

    *ip_data* is the dict returned by ``driver.get_interfaces_ip()``, e.g.::

        {"Ethernet1": {"ipv4": {"10.1.1.1": {"prefix_length": 30}}}}

    Returns a list of ``ip_network`` objects representing subnets directly
    attached to the device.
    """
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for iface_data in ip_data.values():
        for family_data in iface_data.values():  # ipv4, ipv6
            for addr, info in family_data.items():
                prefix_len = info.get("prefix_length", 32)
                try:
                    nets.append(
                        ipaddress.ip_network(f"{addr}/{prefix_len}", strict=False)
                    )
                except ValueError:
                    pass
    return nets


def is_adjacent_nexthop(
    ip: str,
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Return True if *ip* falls within any of the connected *nets*.

    A next-hop that sits on a locally connected subnet is one L3 hop
    away — i.e. immediately adjacent.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in nets)


def extract_route_neighbors(
    local_device: str,
    route_data: dict,
    local_subnets: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> list[NeighborRecord]:
    """Turn raw NAPALM ``get_route_to`` data into unique next-hop records.

    Only active routes whose next-hop is *immediately adjacent* — i.e. the
    next-hop IP falls within one of the device's own connected subnets
    (``local_subnets``, derived from ``get_interfaces_ip``) — produce a
    record.  Next-hops on remote subnets are behind intermediate routers
    and do not represent direct adjacencies.

    Connected, local, and direct routes are also skipped because their
    "next-hop" is the device itself.

    Returns one :class:`NeighborRecord` per unique
    ``(outgoing_interface, next_hop)`` combination.
    """
    seen: set[tuple[str, str]] = set()
    records: list[NeighborRecord] = []

    for _prefix, entries in route_data.items():
        for entry in entries:
            if not entry.get("current_active", False):
                continue
            protocol = (entry.get("protocol") or "").lower()
            if protocol in _LOCAL_PROTOCOLS:
                continue
            next_hop = entry.get("next_hop", "")
            if not next_hop or next_hop == "0.0.0.0":
                continue

            # Adjacency filter: skip next-hops that aren't on a connected
            # subnet — they are reached *through* another router, not direct.
            if not is_adjacent_nexthop(next_hop, local_subnets):
                continue

            interface = entry.get("outgoing_interface", "")

            key = (interface, next_hop)
            if key in seen:
                continue
            seen.add(key)

            records.append(
                NeighborRecord(
                    local_device=local_device,
                    local_interface=interface,
                    remote_ip=next_hop,
                    source=DataSource.ROUTE_TABLE,
                )
            )
    return records


# ---------------------------------------------------------------------------
# Nornir task / orchestrator (inventory mode)
# ---------------------------------------------------------------------------

def _routes_task(task: Task) -> Result:
    """Nornir task: fetch routing table and interface IPs from a single device.

    Interface IPs are needed to compute connected subnets so we can filter
    route next-hops to only those that are immediately adjacent.
    """
    try:
        result = task.run(
            task=napalm_get,
            getters=["interfaces_ip", "route_to"],
            getter_options={"route_to": {"destination": ""}},
        )
    except Exception:
        return Result(host=task.host, result=[])

    data: dict = result[0].result
    ip_data: dict = data.get("interfaces_ip", {})
    route_data: dict = data.get("route_to", {})

    local_nets = connected_subnets(ip_data)
    records = extract_route_neighbors(task.host.name, route_data, local_nets)
    return Result(host=task.host, result=records)


def collect_routes(nr: Nornir) -> list[NeighborRecord]:
    """Run route table collection across all inventory hosts."""
    agg = nr.run(task=_routes_task)
    records: list[NeighborRecord] = []
    for hostname, multi_result in agg.items():
        if multi_result.failed:
            continue
        records.extend(multi_result[0].result)
    return records
