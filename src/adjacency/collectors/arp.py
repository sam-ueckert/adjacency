"""Collect ARP / L3 neighbor tables via NAPALM."""

from __future__ import annotations

from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

from adjacency.models import DataSource, NeighborRecord


def _arp_task(task: Task) -> Result:
    """Nornir task: fetch ARP table from a single device."""
    result = task.run(task=napalm_get, getters=["arp_table"])
    arp_data: list[dict] = result[0].result.get("arp_table", [])

    records: list[NeighborRecord] = []
    for entry in arp_data:
        iface = entry.get("interface", "")
        mac = _normalize_mac(entry.get("mac"))
        ip = entry.get("ip")
        if not ip:
            continue

        records.append(
            NeighborRecord(
                local_device=task.host.name,
                local_interface=iface,
                remote_mac=mac,
                remote_ip=ip,
                source=DataSource.ARP_TABLE,
            )
        )
    return Result(host=task.host, result=records)


def collect_arp_table(nr: Nornir) -> list[NeighborRecord]:
    """Run ARP table collection across all inventory hosts."""
    agg = nr.run(task=_arp_task)
    records: list[NeighborRecord] = []
    for hostname, multi_result in agg.items():
        if multi_result.failed:
            continue
        records.extend(multi_result[0].result)
    return records


def _normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    cleaned = mac.lower().replace("-", "").replace(".", "").replace(":", "")
    if len(cleaned) != 12:
        return mac.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
