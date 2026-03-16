"""Collect hardware/software facts and perform reverse DNS enrichment."""

from __future__ import annotations

import asyncio
import socket

from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir_napalm.plugins.tasks import napalm_get

from adjacency.models import Device, HardwareFacts


def _facts_task(task: Task) -> Result:
    """Nornir task: fetch device facts via NAPALM get_facts."""
    result = task.run(task=napalm_get, getters=["facts"])
    facts: dict = result[0].result.get("facts", {})

    hw = HardwareFacts(
        vendor=facts.get("vendor"),
        model=facts.get("model"),
        hardware_model=facts.get("model"),
        serial_number=facts.get("serial_number"),
        os_version=facts.get("os_version"),
        uptime_seconds=facts.get("uptime"),
        fqdn=facts.get("fqdn"),
    )
    return Result(host=task.host, result=hw)


def collect_facts(nr: Nornir) -> dict[str, HardwareFacts]:
    """Collect hardware/software facts for all hosts."""
    agg = nr.run(task=_facts_task)
    facts: dict[str, HardwareFacts] = {}
    for hostname, multi_result in agg.items():
        if multi_result.failed:
            continue
        facts[hostname] = multi_result[0].result
    return facts


def enrich_devices_with_facts(
    devices: dict[str, Device],
    facts: dict[str, HardwareFacts],
) -> None:
    """Merge hardware facts into existing Device records in-place."""
    for hostname, hw in facts.items():
        dev = devices.get(hostname)
        if not dev:
            continue
        dev.hardware = hw
        if hw.vendor and not dev.vendor:
            dev.vendor = hw.vendor
        if hw.model and not dev.model:
            dev.model = hw.model
        if hw.serial_number and not dev.serial:
            dev.serial = hw.serial_number
        if hw.os_version and not dev.os_version:
            dev.os_version = hw.os_version


# ---------------------------------------------------------------------------
# Reverse DNS
# ---------------------------------------------------------------------------

async def _reverse_lookup(ip: str) -> str | None:
    """Attempt a PTR lookup for a single IP (non-blocking).  Returns FQDN or None."""
    try:
        loop = asyncio.get_running_loop()
        hostname, _ = await loop.getnameinfo((ip, 0), socket.NI_NAMEREQD)
        return hostname
    except (socket.herror, socket.gaierror, OSError):
        return None


async def enrich_devices_with_rdns(
    devices: dict[str, Device],
    *,
    max_workers: int = 20,
) -> None:
    """Perform reverse DNS lookups for management IPs and interface IPs.

    Results are stored in ``Device.dns_names``.  Uses asyncio for
    non-blocking, concurrent DNS resolution.
    """
    # Collect all (hostname, ip) pairs to look up
    work: list[tuple[str, str]] = []
    for hostname, dev in devices.items():
        if dev.management_ip:
            work.append((hostname, dev.management_ip))
        for ip in dev.known_ips:
            if ip != dev.management_ip:
                work.append((hostname, ip))

    # Deduplicate IPs per device
    seen: set[tuple[str, str]] = set()
    unique_work: list[tuple[str, str]] = []
    for item in work:
        if item not in seen:
            seen.add(item)
            unique_work.append(item)

    # Concurrent async DNS lookups with semaphore for concurrency control
    sem = asyncio.Semaphore(max_workers)

    async def _lookup_one(dev_hostname: str, ip: str):
        async with sem:
            dns_name = await _reverse_lookup(ip)
            return dev_hostname, dns_name

    outcomes = await asyncio.gather(
        *[_lookup_one(dh, ip) for dh, ip in unique_work]
    )

    # Apply results
    results: dict[str, set[str]] = {}
    for dev_hostname, dns_name in outcomes:
        if dns_name:
            results.setdefault(dev_hostname, set()).add(dns_name)

    for hostname, names in results.items():
        dev = devices.get(hostname)
        if dev:
            existing = set(dev.dns_names)
            for name in sorted(names):
                if name not in existing:
                    dev.dns_names.append(name)
