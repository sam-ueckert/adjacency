"""Collect interface details and LAG membership via NAPALM."""

from __future__ import annotations

import re

from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

from adjacency.models import Device, InterfaceInfo
from adjacency.virtual_macs import is_multicast_mac, is_virtual_mac

# Common LAG interface name patterns across vendors
_LAG_PATTERN = re.compile(
    r"^(port-channel|ae|bond|po|bundle-ether)", re.IGNORECASE
)


def _interface_task(task: Task) -> Result:
    """Nornir task: fetch interface info + IP addresses from a single device."""
    result = task.run(
        task=napalm_get,
        getters=["interfaces", "interfaces_ip"],
    )
    iface_data: dict = result[0].result.get("interfaces", {})
    ip_data: dict = result[0].result.get("interfaces_ip", {})

    interfaces: dict[str, InterfaceInfo] = {}
    known_macs: set[str] = set()
    shared_macs: set[str] = set()

    for name, info in iface_data.items():
        mac = _normalize_mac(info.get("mac_address"))
        if mac:
            # Classify: virtual/multicast MACs go to shared_macs,
            # normal unicast MACs go to known_macs (identity).
            if is_virtual_mac(mac) or is_multicast_mac(mac):
                shared_macs.add(mac)
            else:
                known_macs.add(mac)

        # Gather IP addresses from interfaces_ip
        ips: list[str] = []
        if name in ip_data:
            for family_data in ip_data[name].values():  # ipv4, ipv6
                ips.extend(family_data.keys())

        is_lag = bool(_LAG_PATTERN.match(name))

        interfaces[name] = InterfaceInfo(
            name=name,
            mac_address=mac,
            ip_addresses=ips,
            speed_mbps=info.get("speed", 0),
            is_up=info.get("is_up", False),
            mtu=info.get("mtu"),
            description=info.get("description", ""),
            is_lag=is_lag,
        )

    device = Device(
        hostname=task.host.name,
        platform=task.host.platform,
        management_ip=str(task.host.hostname),
        interfaces=interfaces,
        known_macs=known_macs,
        shared_macs=shared_macs,
        known_ips={ip for iface in interfaces.values() for ip in iface.ip_addresses},
    )
    return Result(host=task.host, result=device)


def collect_interfaces(nr: Nornir) -> dict[str, Device]:
    """Collect interface data for all hosts.  Returns {hostname: Device}."""
    agg = nr.run(task=_interface_task)
    devices: dict[str, Device] = {}
    for hostname, multi_result in agg.items():
        if multi_result.failed:
            continue
        devices[hostname] = multi_result[0].result
    return devices


def _normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    cleaned = mac.lower().replace("-", "").replace(".", "").replace(":", "")
    if len(cleaned) != 12:
        return mac.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
