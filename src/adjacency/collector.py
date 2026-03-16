"""Orchestrates all collectors and feeds results into rationalization."""

from __future__ import annotations

import asyncio
from pathlib import Path

from nornir import InitNornir
from nornir.core import Nornir

from adjacency.collectors import (
    collect_arp_table,
    collect_cdp_neighbors,
    collect_interfaces,
    collect_lldp_neighbors,
    collect_mac_table,
    collect_routes,
)
from adjacency.collectors.facts import (
    collect_facts,
    enrich_devices_with_facts,
    enrich_devices_with_rdns,
)
from adjacency.models import AdjacencyTable, Device, NeighborRecord
from adjacency.rationalize import rationalize


def init_nornir(inventory_dir: Path) -> Nornir:
    """Initialize Nornir with SimpleInventory pointing at the given directory."""
    hosts_file = inventory_dir / "hosts.yaml"
    defaults_file = inventory_dir / "defaults.yaml"

    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 20}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file": str(hosts_file),
                "defaults_file": str(defaults_file) if defaults_file.exists() else None,
            },
        },
    )
    return nr


async def discover(
    inventory_dir: Path,
    *,
    collect_l2: bool = True,
    collect_l3: bool = True,
    collect_cdp: bool = True,
    collect_route_table: bool = True,
    collect_hw_facts: bool = True,
    do_rdns: bool = True,
) -> AdjacencyTable:
    """Run full discovery and return a rationalized AdjacencyTable.

    Parameters
    ----------
    inventory_dir:
        Path to directory containing ``hosts.yaml`` (and optional ``defaults.yaml``).
    collect_l2:
        Collect MAC address tables.
    collect_l3:
        Collect ARP tables.
    collect_cdp:
        Attempt CDP collection (in addition to LLDP which is always collected).
    collect_route_table:
        Collect routing table and use next-hops for L3 adjacency.
    collect_hw_facts:
        Collect hardware/software facts via NAPALM get_facts.
    do_rdns:
        Perform reverse DNS lookups on device IPs.
    """
    nr = init_nornir(inventory_dir)

    # Always collect interfaces and LLDP (blocking Nornir calls offloaded to threads)
    devices: dict[str, Device] = await asyncio.to_thread(collect_interfaces, nr)
    records: list[NeighborRecord] = await asyncio.to_thread(collect_lldp_neighbors, nr)

    if collect_cdp:
        records.extend(await asyncio.to_thread(collect_cdp_neighbors, nr))
    if collect_l2:
        records.extend(await asyncio.to_thread(collect_mac_table, nr))
    if collect_l3:
        records.extend(await asyncio.to_thread(collect_arp_table, nr))
    if collect_route_table:
        records.extend(await asyncio.to_thread(collect_routes, nr))

    # Enrich with hardware facts
    if collect_hw_facts:
        facts = await asyncio.to_thread(collect_facts, nr)
        enrich_devices_with_facts(devices, facts)

    # Enrich with reverse DNS (natively async)
    if do_rdns:
        await enrich_devices_with_rdns(devices)

    return rationalize(devices, records)
