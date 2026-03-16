"""Crawl-based discovery engine.

Instead of requiring a pre-built inventory, the crawler starts from a set
of seed devices, collects LLDP/CDP neighbors, and iteratively probes newly
discovered devices up to a configurable depth.  Credentials are selected
per-device from a :class:`CredentialStore` using network-range matching.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from dataclasses import dataclass, field

from napalm import get_network_driver

from adjacency.collectors.cdp import _parse_cdp_output
from adjacency.collectors.facts import enrich_devices_with_rdns
from adjacency.collectors.routes import connected_subnets, extract_route_neighbors
from adjacency.credentials import CredentialStore, Credential, detect_platform
from adjacency.models import (
    AdjacencyTable,
    DataSource,
    Device,
    HardwareFacts,
    InterfaceInfo,
    NeighborRecord,
)
from adjacency.rationalize import rationalize
from adjacency.virtual_macs import is_multicast_mac, is_virtual_mac

log = logging.getLogger(__name__)

# Common LAG interface name patterns across vendors
_LAG_PATTERN = re.compile(
    r"^(port-channel|ae|bond|po|bundle-ether)", re.IGNORECASE
)


@dataclass
class SeedDevice:
    """An initial device to start crawling from."""

    host: str  # IP address or resolvable hostname
    platform: str | None = None  # NAPALM driver name, or None for auto-detect


@dataclass
class CrawlTarget:
    """A device discovered during the crawl that should be probed next."""

    ip: str
    hostname: str | None = None
    platform_hint: str | None = None  # from LLDP/CDP system description
    discovered_at_depth: int = 0


@dataclass
class CrawlResult:
    """Accumulated data from a crawl run."""

    devices: dict[str, Device] = field(default_factory=dict)
    records: list[NeighborRecord] = field(default_factory=list)
    failed_hosts: dict[str, str] = field(default_factory=dict)  # ip -> error


def _normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    cleaned = mac.lower().replace("-", "").replace(".", "").replace(":", "")
    if len(cleaned) != 12:
        return mac.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


async def _resolve_hostname(name: str) -> str | None:
    """Forward DNS lookup (non-blocking).  Returns IP or None."""
    try:
        loop = asyncio.get_running_loop()
        results = await loop.getaddrinfo(name, None, family=socket.AF_INET)
        if results:
            return results[0][4][0]
        return None
    except (socket.gaierror, socket.herror, OSError):
        return None


# ---------------------------------------------------------------------------
# Single-device collection via raw NAPALM driver
# ---------------------------------------------------------------------------

def _collect_device(
    driver,
    host: str,
    hostname_override: str | None = None,
    *,
    collect_l2: bool = True,
    collect_l3: bool = True,
    collect_cdp: bool = True,
    collect_routes: bool = True,
) -> tuple[Device, list[NeighborRecord], list[CrawlTarget]]:
    """Collect all data from an open NAPALM driver.

    Returns (Device, neighbor_records, next_hop_targets).
    """
    # --- facts ---
    try:
        facts = driver.get_facts()
    except Exception:
        facts = {}

    effective_hostname = (
        hostname_override
        or facts.get("hostname")
        or facts.get("fqdn", "").split(".")[0]
        or host
    )

    hw = HardwareFacts(
        vendor=facts.get("vendor"),
        model=facts.get("model"),
        hardware_model=facts.get("model"),
        serial_number=facts.get("serial_number"),
        os_version=facts.get("os_version"),
        uptime_seconds=facts.get("uptime"),
        fqdn=facts.get("fqdn"),
    )

    # --- interfaces ---
    try:
        iface_data = driver.get_interfaces()
    except Exception:
        iface_data = {}

    try:
        ip_data = driver.get_interfaces_ip()
    except Exception:
        ip_data = {}

    interfaces: dict[str, InterfaceInfo] = {}
    known_macs: set[str] = set()
    shared_macs: set[str] = set()

    for name, info in iface_data.items():
        mac = _normalize_mac(info.get("mac_address"))
        if mac:
            if is_virtual_mac(mac) or is_multicast_mac(mac):
                shared_macs.add(mac)
            else:
                known_macs.add(mac)

        ips: list[str] = []
        if name in ip_data:
            for family_data in ip_data[name].values():
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
        hostname=effective_hostname,
        platform=driver.platform if hasattr(driver, "platform") else None,
        management_ip=host,
        vendor=facts.get("vendor"),
        model=facts.get("model"),
        os_version=facts.get("os_version"),
        serial=facts.get("serial_number"),
        hardware=hw,
        interfaces=interfaces,
        known_macs=known_macs,
        shared_macs=shared_macs,
        known_ips={ip for iface in interfaces.values() for ip in iface.ip_addresses},
    )

    records: list[NeighborRecord] = []
    next_hops: list[CrawlTarget] = []

    # --- LLDP ---
    try:
        lldp_data = driver.get_lldp_neighbors_detail()
    except Exception:
        lldp_data = {}

    for local_intf, neighbors in lldp_data.items():
        for nbr in neighbors:
            remote_name = nbr.get("remote_system_name")
            remote_desc = nbr.get("remote_system_description")
            records.append(NeighborRecord(
                local_device=effective_hostname,
                local_interface=local_intf,
                remote_device=remote_name,
                remote_interface=nbr.get("remote_port"),
                remote_mac=_normalize_mac(nbr.get("remote_chassis_id")),
                remote_platform=remote_desc,
                source=DataSource.LLDP,
            ))
            # Extract crawl target from LLDP
            _add_next_hop(next_hops, remote_name, None, remote_desc)

    # --- CDP ---
    if collect_cdp:
        try:
            cdp_raw_result = driver.cli(["show cdp neighbors detail"])
            cdp_text = list(cdp_raw_result.values())[0] if cdp_raw_result else ""
        except Exception:
            cdp_text = ""

        if cdp_text:
            cdp_records = _parse_cdp_output(effective_hostname, cdp_text)
            records.extend(cdp_records)
            for rec in cdp_records:
                _add_next_hop(next_hops, rec.remote_device, rec.remote_ip, rec.remote_platform)

    # --- MAC table ---
    if collect_l2:
        try:
            mac_entries = driver.get_mac_address_table()
        except Exception:
            mac_entries = []

        for entry in mac_entries:
            iface = entry.get("interface")
            if not iface or entry.get("static", False):
                continue
            records.append(NeighborRecord(
                local_device=effective_hostname,
                local_interface=iface,
                remote_mac=_normalize_mac(entry.get("mac")),
                source=DataSource.MAC_TABLE,
            ))

    # --- ARP ---
    if collect_l3:
        try:
            arp_entries = driver.get_arp_table()
        except Exception:
            arp_entries = []

        for entry in arp_entries:
            iface = entry.get("interface", "")
            ip = entry.get("ip")
            if not ip:
                continue
            records.append(NeighborRecord(
                local_device=effective_hostname,
                local_interface=iface,
                remote_mac=_normalize_mac(entry.get("mac")),
                remote_ip=ip,
                source=DataSource.ARP_TABLE,
            ))

    # --- Route table ---
    if collect_routes:
        try:
            route_data = driver.get_route_to(destination="")
        except Exception:
            route_data = {}

        # Only keep next-hops on locally connected subnets — those are
        # truly one L3 hop away.  Remote next-hops are behind intermediate
        # routers and do not represent direct adjacencies.
        local_nets = connected_subnets(ip_data)
        route_records = extract_route_neighbors(
            effective_hostname, route_data, local_nets,
        )
        records.extend(route_records)

        # Each unique adjacent next-hop IP is also a crawl target.
        seen_route_ips: set[str] = set()
        for rec in route_records:
            if rec.remote_ip and rec.remote_ip not in seen_route_ips:
                seen_route_ips.add(rec.remote_ip)
                next_hops.append(CrawlTarget(ip=rec.remote_ip))

    return device, records, next_hops


def _add_next_hop(
    targets: list[CrawlTarget],
    hostname: str | None,
    ip: str | None,
    system_desc: str | None,
) -> None:
    """Add a crawl target extracted from LLDP/CDP if we have enough info."""
    platform = detect_platform(system_desc)
    if ip:
        targets.append(CrawlTarget(ip=ip, hostname=hostname, platform_hint=platform))
    elif hostname:
        # We'll need to resolve this via DNS during crawl
        targets.append(CrawlTarget(ip="", hostname=hostname, platform_hint=platform))


# ---------------------------------------------------------------------------
# Connection logic
# ---------------------------------------------------------------------------

def _try_connect(
    host: str,
    cred_store: CredentialStore,
    platform_hint: str | None = None,
    timeout: int = 30,
) -> tuple[object, Credential] | None:
    """Try to open a NAPALM connection to *host* using credentials from the store.

    Returns (driver_instance, matched_credential) on success, or None.
    """
    candidates = cred_store.match_with_platform(host, platform_hint)
    if not candidates:
        log.warning("No credentials match host %s", host)
        return None

    # Build list of (platform, credential) combos to try
    attempts: list[tuple[str, Credential]] = []
    for cred in candidates:
        if cred.platform:
            attempts.append((cred.platform, cred))
        elif platform_hint:
            attempts.append((platform_hint, cred))
        else:
            # No platform info — try common drivers
            for p in ("eos", "ios", "nxos_ssh", "junos"):
                attempts.append((p, cred))

    for platform, cred in attempts:
        try:
            driver_cls = get_network_driver(platform)
        except Exception:
            continue

        optional_args: dict = {"transport": "ssh"}
        if timeout:
            optional_args["timeout"] = timeout
        if cred.secret:
            optional_args["secret"] = cred.secret

        try:
            drv = driver_cls(
                hostname=host,
                username=cred.username,
                password=cred.password,
                optional_args=optional_args,
            )
            drv.open()
            # Quick validation — call get_facts to confirm the connection works
            drv.get_facts()
            return drv, cred
        except Exception as exc:
            log.debug("Failed %s@%s with driver %s: %s", cred.username, host, platform, exc)
            try:
                drv.close()
            except Exception:
                pass

    return None


# ---------------------------------------------------------------------------
# Crawl engine
# ---------------------------------------------------------------------------

async def crawl(
    seeds: list[SeedDevice],
    cred_store: CredentialStore,
    *,
    max_depth: int = 1,
    max_workers: int = 10,
    collect_l2: bool = True,
    collect_l3: bool = True,
    collect_cdp: bool = True,
    collect_routes: bool = True,
    do_rdns: bool = True,
    timeout: int = 30,
) -> AdjacencyTable:
    """Run crawl-based discovery starting from seed devices.

    Parameters
    ----------
    seeds:
        Initial devices to connect to.
    cred_store:
        Credential store for authentication.
    max_depth:
        How many hops beyond the seeds to crawl.
        0 = seeds only, 1 = seeds + their neighbors, etc.
    max_workers:
        Parallelism for device probing at each depth level.
    collect_l2 / collect_l3 / collect_cdp / collect_routes:
        Which data sources to collect.  Route table next-hops are
        also used as crawl targets (within the configured depth).
    do_rdns:
        Perform reverse DNS on discovered device IPs.
    timeout:
        Per-device connection timeout in seconds.
    """
    result = CrawlResult()

    # Track visited IPs and hostnames to avoid re-probing
    visited_ips: set[str] = set()
    visited_hostnames: set[str] = set()

    # Build initial frontier from seeds
    frontier: list[CrawlTarget] = []
    for seed in seeds:
        ip = seed.host
        # If seed.host looks like a hostname, resolve it
        if not _is_ip(ip):
            resolved = await _resolve_hostname(ip)
            if resolved:
                frontier.append(CrawlTarget(
                    ip=resolved, hostname=seed.host,
                    platform_hint=seed.platform, discovered_at_depth=0,
                ))
            else:
                log.warning("Could not resolve seed host '%s'", seed.host)
                result.failed_hosts[seed.host] = "DNS resolution failed"
        else:
            frontier.append(CrawlTarget(
                ip=ip, hostname=None, platform_hint=seed.platform,
                discovered_at_depth=0,
            ))

    sem = asyncio.Semaphore(max_workers)

    for depth in range(max_depth + 1):
        if not frontier:
            break

        log.info("Crawl depth %d: %d targets", depth, len(frontier))

        # Deduplicate frontier
        to_probe: list[CrawlTarget] = []
        for target in frontier:
            if target.ip and target.ip in visited_ips:
                continue
            if target.hostname and target.hostname in visited_hostnames:
                continue
            # Resolve hostname to IP if needed
            if not target.ip and target.hostname:
                resolved = await _resolve_hostname(target.hostname)
                if not resolved:
                    continue
                target.ip = resolved
                if target.ip in visited_ips:
                    continue
            to_probe.append(target)
            if target.ip:
                visited_ips.add(target.ip)
            if target.hostname:
                visited_hostnames.add(target.hostname)

        if not to_probe:
            break

        next_frontier: list[CrawlTarget] = []

        # Probe devices concurrently using asyncio
        async def _probe_one(target: CrawlTarget):
            async with sem:
                try:
                    probe_result = await asyncio.to_thread(
                        _probe_single, target, cred_store, collect_l2,
                        collect_l3, collect_cdp, collect_routes, timeout,
                    )
                    return target, probe_result, None
                except Exception as exc:
                    return target, None, exc

        outcomes = await asyncio.gather(
            *[_probe_one(t) for t in to_probe]
        )

        for target, probe_result, error in outcomes:
            if error is not None:
                log.warning("Probe of %s failed: %s", target.ip, error)
                result.failed_hosts[target.ip] = str(error)
                continue

            if probe_result is None:
                result.failed_hosts[target.ip] = "authentication failed"
                continue

            device, records, next_hops = probe_result
            result.devices[device.hostname] = device
            result.records.extend(records)
            visited_hostnames.add(device.hostname)

            # Queue next hops if we haven't reached max depth
            if depth < max_depth:
                for hop in next_hops:
                    hop.discovered_at_depth = depth + 1
                    next_frontier.append(hop)

        frontier = next_frontier

    # Enrich with reverse DNS
    if do_rdns:
        await enrich_devices_with_rdns(result.devices)

    table = rationalize(result.devices, result.records)

    # Log summary
    n_failed = len(result.failed_hosts)
    if n_failed:
        log.warning(
            "Crawl complete: %d devices discovered, %d hosts unreachable",
            len(result.devices), n_failed,
        )

    return table


def _probe_single(
    target: CrawlTarget,
    cred_store: CredentialStore,
    collect_l2: bool,
    collect_l3: bool,
    collect_cdp: bool,
    collect_routes: bool,
    timeout: int,
) -> tuple[Device, list[NeighborRecord], list[CrawlTarget]] | None:
    """Connect to a single device, collect data, close connection."""
    conn = _try_connect(target.ip, cred_store, target.platform_hint, timeout)
    if conn is None:
        return None

    drv, cred = conn
    try:
        device, records, next_hops = _collect_device(
            drv, target.ip,
            hostname_override=target.hostname,
            collect_l2=collect_l2,
            collect_l3=collect_l3,
            collect_cdp=collect_cdp,
            collect_routes=collect_routes,
        )
        return device, records, next_hops
    finally:
        try:
            drv.close()
        except Exception:
            pass


def _is_ip(s: str) -> bool:
    """Quick check if a string looks like an IP address."""
    parts = s.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
