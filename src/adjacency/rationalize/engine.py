"""Core rationalization pipeline.

Takes raw NeighborRecords + Device data and produces a clean AdjacencyTable.

Pipeline stages:
  1. Build device identity index (MAC -> hostname, IP -> hostname)
  2. Resolve neighbor records against the identity index
  3. Detect and collapse LAG bundles
  4. Deduplicate remaining link entries
"""

from __future__ import annotations

from collections import defaultdict

from adjacency.models import (
    AdjacencyLink,
    AdjacencyTable,
    DataSource,
    Device,
    LinkType,
    NeighborRecord,
)
from adjacency.virtual_macs import is_multicast_mac, is_virtual_mac


def rationalize(
    devices: dict[str, Device],
    records: list[NeighborRecord],
) -> AdjacencyTable:
    """Run the full rationalization pipeline."""
    mac_index, ip_index, shared_macs, shared_ips = _build_identity_index(devices)
    resolved = _resolve_records(records, mac_index, ip_index, shared_macs, shared_ips)
    links = _build_links(resolved, devices)
    links = _collapse_lag_bundles(links, devices)
    links = _deduplicate_links(links)

    return AdjacencyTable(
        devices=devices,
        links=links,
        raw_records=records,
    )


# ---------------------------------------------------------------------------
# Stage 1: Identity index
# ---------------------------------------------------------------------------

def _build_identity_index(
    devices: dict[str, Device],
) -> tuple[dict[str, str], dict[str, str], set[str], set[str]]:
    """Map every known MAC and IP back to a device hostname.

    Returns (mac_to_host, ip_to_host, shared_macs, shared_ips) where
    shared_macs/shared_ips contain addresses claimed by multiple devices
    or recognized as virtual/protocol addresses.  These are excluded from
    the identity maps to prevent misattribution.
    """
    # First pass: collect all claimants per address
    mac_claimants: dict[str, list[str]] = defaultdict(list)
    ip_claimants: dict[str, list[str]] = defaultdict(list)

    for hostname, device in devices.items():
        for mac in device.known_macs:
            mac_claimants[mac].append(hostname)
        for ip in device.known_ips:
            ip_claimants[ip].append(hostname)
        if device.management_ip:
            ip_claimants[device.management_ip].append(hostname)

    # Second pass: build maps, excluding shared / virtual addresses
    mac_to_host: dict[str, str] = {}
    ip_to_host: dict[str, str] = {}
    shared_macs: set[str] = set()
    shared_ips: set[str] = set()

    # Collect explicitly-flagged shared addresses from devices
    for device in devices.values():
        shared_macs |= device.shared_macs
        shared_ips |= device.shared_ips

    for mac, hosts in mac_claimants.items():
        # Skip virtual/multicast MACs
        if is_virtual_mac(mac) or is_multicast_mac(mac):
            shared_macs.add(mac)
            continue
        # Skip MACs claimed by multiple devices (likely FHRP/MLAG virtual)
        if len(set(hosts)) > 1:
            shared_macs.add(mac)
            continue
        mac_to_host[mac] = hosts[0]

    for ip, hosts in ip_claimants.items():
        # Skip IPs claimed by multiple devices (likely VIP / anycast)
        if len(set(hosts)) > 1:
            shared_ips.add(ip)
            continue
        ip_to_host[ip] = hosts[0]

    return mac_to_host, ip_to_host, shared_macs, shared_ips


# ---------------------------------------------------------------------------
# Stage 2: Resolve raw records
# ---------------------------------------------------------------------------

def _resolve_records(
    records: list[NeighborRecord],
    mac_index: dict[str, str],
    ip_index: dict[str, str],
    shared_macs: set[str],
    shared_ips: set[str],
) -> list[NeighborRecord]:
    """Fill in ``remote_device`` on records where it is missing, using
    MAC / IP identity lookups.  Returns a new list (originals are not mutated).

    Records whose only identifier is a known-shared MAC or IP are kept but
    left unresolved (remote_device stays None) rather than being attributed
    to a single device incorrectly.
    """
    resolved: list[NeighborRecord] = []
    for rec in records:
        if rec.remote_device:
            resolved.append(rec)
            continue

        hostname: str | None = None

        # Try MAC lookup — but skip if the MAC is shared/virtual
        if rec.remote_mac and rec.remote_mac not in shared_macs:
            hostname = mac_index.get(rec.remote_mac)

        # Fall back to IP lookup — but skip shared IPs
        if not hostname and rec.remote_ip and rec.remote_ip not in shared_ips:
            hostname = ip_index.get(rec.remote_ip)

        resolved.append(rec.model_copy(update={"remote_device": hostname}))
    return resolved


# ---------------------------------------------------------------------------
# Stage 3: Build link objects
# ---------------------------------------------------------------------------

def _build_links(
    records: list[NeighborRecord],
    devices: dict[str, Device],
) -> list[AdjacencyLink]:
    """Convert resolved records into AdjacencyLink objects, one per
    unique (local_device, local_interface, remote_device) tuple.
    """
    # Group records by (local_device, local_interface, remote_device)
    grouped: dict[tuple[str, str, str], list[NeighborRecord]] = defaultdict(list)
    for rec in records:
        if not rec.remote_device:
            continue
        key = (rec.local_device, rec.local_interface, rec.remote_device)
        grouped[key].append(rec)

    links: list[AdjacencyLink] = []
    for (local_dev, local_intf, remote_dev), recs in grouped.items():
        # Pick the best remote_interface from available sources (prefer LLDP/CDP)
        remote_intf = None
        remote_mac = None
        remote_ip = None
        sources: list[DataSource] = []
        for r in recs:
            sources.append(r.source)
            if r.remote_interface and not remote_intf:
                remote_intf = r.remote_interface
            if r.remote_mac and not remote_mac:
                remote_mac = r.remote_mac
            if r.remote_ip and not remote_ip:
                remote_ip = r.remote_ip

        links.append(
            AdjacencyLink(
                local_device=local_dev,
                local_interface=local_intf,
                remote_device=remote_dev,
                remote_interface=remote_intf,
                sources=list(set(sources)),
                remote_mac=remote_mac,
                remote_ip=remote_ip,
            )
        )
    return links


# ---------------------------------------------------------------------------
# Stage 4: Collapse LAG bundles
# ---------------------------------------------------------------------------

def _collapse_lag_bundles(
    links: list[AdjacencyLink],
    devices: dict[str, Device],
) -> list[AdjacencyLink]:
    """Detect physical links whose local interface is a LAG member and
    nest them under a single LAG AdjacencyLink.
    """
    # Build a map of interface -> lag_parent for each device
    lag_map: dict[str, dict[str, str]] = {}  # device -> {member_intf: lag_name}
    for hostname, device in devices.items():
        mapping: dict[str, str] = {}
        for intf_name, intf in device.interfaces.items():
            if intf.lag_parent:
                mapping[intf_name] = intf.lag_parent
        if mapping:
            lag_map[hostname] = mapping

    # Group links by (local_device, lag_parent, remote_device)
    lag_groups: dict[tuple[str, str, str], list[AdjacencyLink]] = defaultdict(list)
    standalone: list[AdjacencyLink] = []

    for link in links:
        device_lags = lag_map.get(link.local_device, {})
        lag_parent = device_lags.get(link.local_interface)
        if lag_parent:
            key = (link.local_device, lag_parent, link.remote_device)
            lag_groups[key].append(link)
        else:
            standalone.append(link)

    # Create LAG bundle links
    for (local_dev, lag_intf, remote_dev), members in lag_groups.items():
        # Determine remote LAG interface if possible
        remote_lag = _infer_remote_lag(remote_dev, members, devices)

        all_sources = list({s for m in members for s in m.sources})
        bundle = AdjacencyLink(
            local_device=local_dev,
            local_interface=lag_intf,
            remote_device=remote_dev,
            remote_interface=remote_lag,
            link_type=LinkType.LAG,
            sources=all_sources,
            members=members,
        )
        standalone.append(bundle)

    return standalone


def _infer_remote_lag(
    remote_device: str,
    members: list[AdjacencyLink],
    devices: dict[str, Device],
) -> str | None:
    """Try to figure out the remote LAG interface name."""
    remote_dev = devices.get(remote_device)
    if not remote_dev:
        return None

    # Check if all member remote_interfaces belong to the same LAG on the remote
    remote_lags: set[str] = set()
    for m in members:
        if not m.remote_interface:
            continue
        remote_intf = remote_dev.interfaces.get(m.remote_interface)
        if remote_intf and remote_intf.lag_parent:
            remote_lags.add(remote_intf.lag_parent)

    if len(remote_lags) == 1:
        return remote_lags.pop()
    return None


# ---------------------------------------------------------------------------
# Stage 5: Deduplicate
# ---------------------------------------------------------------------------

def _deduplicate_links(links: list[AdjacencyLink]) -> list[AdjacencyLink]:
    """Remove duplicate links.

    Two links are duplicates if they connect the same pair of
    (local_device, local_interface) <-> (remote_device, remote_interface).
    When a duplicate is found, merge their sources and keep the richer record.
    """
    seen: dict[tuple, AdjacencyLink] = {}
    for link in links:
        # Canonical key — order devices alphabetically to catch A->B / B->A
        pair = tuple(sorted([
            (link.local_device, link.local_interface or ""),
            (link.remote_device, link.remote_interface or ""),
        ]))
        key = (*pair, link.link_type)

        if key in seen:
            existing = seen[key]
            existing.sources = list(set(existing.sources) | set(link.sources))
            if link.remote_mac and not existing.remote_mac:
                existing.remote_mac = link.remote_mac
            if link.remote_ip and not existing.remote_ip:
                existing.remote_ip = link.remote_ip
            if link.members and not existing.members:
                existing.members = link.members
        else:
            seen[key] = link

    return list(seen.values())
