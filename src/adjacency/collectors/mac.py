"""Collect MAC address (L2 forwarding) tables via NAPALM."""

from __future__ import annotations

from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

from adjacency.models import DataSource, NeighborRecord


def _mac_task(task: Task) -> Result:
    """Nornir task: fetch MAC address table from a single device."""
    result = task.run(task=napalm_get, getters=["mac_address_table"])
    mac_data: list[dict] = result[0].result.get("mac_address_table", [])

    records: list[NeighborRecord] = []
    for entry in mac_data:
        # Skip entries with no interface (CPU/internal MACs)
        iface = entry.get("interface")
        if not iface:
            continue
        # Skip static entries that are typically local
        if entry.get("static", False):
            continue

        mac = _normalize_mac(entry.get("mac"))
        records.append(
            NeighborRecord(
                local_device=task.host.name,
                local_interface=iface,
                remote_mac=mac,
                source=DataSource.MAC_TABLE,
            )
        )
    return Result(host=task.host, result=records)


def collect_mac_table(nr: Nornir) -> list[NeighborRecord]:
    """Run MAC table collection across all inventory hosts."""
    agg = nr.run(task=_mac_task)
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
