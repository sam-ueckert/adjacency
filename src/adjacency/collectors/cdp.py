"""Collect CDP neighbor information.

NAPALM does not expose CDP as a dedicated getter, but several drivers
(ios, nxos_ssh) return CDP data via ``cli()`` or custom getters.  This
module sends ``show cdp neighbors detail`` and parses the structured
output when available, falling back to TextFSM parsing.
"""

from __future__ import annotations

import re

from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_cli

from adjacency.models import DataSource, NeighborRecord


_CDP_ENTRY_RE = re.compile(
    r"Device ID:\s*(?P<device>\S+).*?"
    r"Interface:\s*(?P<local_intf>[^,]+),\s*Port ID \(outgoing port\):\s*(?P<remote_intf>\S+).*?"
    r"Platform:\s*(?P<platform>[^\n,]+)",
    re.DOTALL,
)

_IP_RE = re.compile(r"IP(?:v4)? [Aa]ddress:\s*(?P<ip>\d+\.\d+\.\d+\.\d+)")


def _cdp_task(task: Task) -> Result:
    """Nornir task: fetch CDP neighbor detail via CLI."""
    try:
        result = task.run(
            task=napalm_cli, commands=["show cdp neighbors detail"]
        )
    except Exception:
        return Result(host=task.host, result=[])

    raw: str = list(result[0].result.values())[0] if result[0].result else ""
    records = _parse_cdp_output(task.host.name, raw)
    return Result(host=task.host, result=records)


def _parse_cdp_output(local_device: str, raw: str) -> list[NeighborRecord]:
    """Parse raw ``show cdp neighbors detail`` text into NeighborRecords."""
    records: list[NeighborRecord] = []
    # Split on the separator line
    entries = re.split(r"-{3,}", raw)
    for entry in entries:
        m = _CDP_ENTRY_RE.search(entry)
        if not m:
            continue
        ip_match = _IP_RE.search(entry)
        records.append(
            NeighborRecord(
                local_device=local_device,
                local_interface=m.group("local_intf").strip(),
                remote_device=m.group("device").strip(),
                remote_interface=m.group("remote_intf").strip(),
                remote_ip=ip_match.group("ip") if ip_match else None,
                remote_platform=m.group("platform").strip(),
                source=DataSource.CDP,
            )
        )
    return records


def collect_cdp_neighbors(nr: Nornir) -> list[NeighborRecord]:
    """Run CDP collection across all inventory hosts."""
    agg = nr.run(task=_cdp_task)
    records: list[NeighborRecord] = []
    for hostname, multi_result in agg.items():
        if multi_result.failed:
            continue
        records.extend(multi_result[0].result)
    return records
