"""Collect LLDP neighbor information via NAPALM."""

from __future__ import annotations

from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

from adjacency.models import DataSource, NeighborRecord


def _lldp_task(task: Task) -> Result:
    """Nornir task: fetch LLDP neighbor detail from a single device."""
    result = task.run(task=napalm_get, getters=["lldp_neighbors_detail"])
    lldp_data: dict = result[0].result.get("lldp_neighbors_detail", {})

    records: list[NeighborRecord] = []
    for local_intf, neighbors in lldp_data.items():
        for nbr in neighbors:
            records.append(
                NeighborRecord(
                    local_device=task.host.name,
                    local_interface=local_intf,
                    remote_device=nbr.get("remote_system_name"),
                    remote_interface=nbr.get("remote_port"),
                    remote_mac=_normalize_mac(nbr.get("remote_chassis_id")),
                    remote_platform=nbr.get("remote_system_description"),
                    source=DataSource.LLDP,
                )
            )
    return Result(host=task.host, result=records)


def collect_lldp_neighbors(nr: Nornir) -> list[NeighborRecord]:
    """Run LLDP collection across all inventory hosts."""
    agg = nr.run(task=_lldp_task)
    records: list[NeighborRecord] = []
    for hostname, multi_result in agg.items():
        if multi_result.failed:
            continue
        records.extend(multi_result[0].result)
    return records


def _normalize_mac(mac: str | None) -> str | None:
    """Normalize MAC to lower-case colon-separated format."""
    if not mac:
        return None
    cleaned = mac.lower().replace("-", "").replace(".", "").replace(":", "")
    if len(cleaned) != 12:
        return mac.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
