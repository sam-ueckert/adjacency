"""Known virtual, reserved, and multicast MAC address patterns.

These MACs must be excluded from device identity resolution because they
are shared across multiple devices (FHRP, MLAG, etc.) or are protocol
artifacts (multicast, LACP).

Each entry is a (prefix_hex, mask_length, description) tuple.  The prefix
is compared against the first ``mask_length`` hex characters of the
normalized (no-separator, lowercase) MAC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VirtualMacPattern:
    prefix: str          # lowercase hex prefix to match (no separators)
    mask_len: int        # how many hex chars of the MAC to compare
    description: str
    vendor: str | None = None


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------
VIRTUAL_MAC_PATTERNS: list[VirtualMacPattern] = [
    # -- FHRP virtual MACs --------------------------------------------------
    VirtualMacPattern("00000c07ac", 10, "HSRP v1 virtual MAC", "cisco"),
    VirtualMacPattern("00000c9ff", 9,  "HSRP v2 virtual MAC", "cisco"),
    VirtualMacPattern("00005e0001", 10, "VRRP virtual MAC (IPv4)", None),
    VirtualMacPattern("00005e0002", 10, "VRRP virtual MAC (IPv6)", None),
    VirtualMacPattern("0007b400", 8,   "GLBP virtual MAC", "cisco"),

    # -- Multicast / protocol MACs ------------------------------------------
    VirtualMacPattern("0180c2000000", 12, "STP bridge group", None),
    VirtualMacPattern("0180c200000e", 12, "LLDP multicast", None),
    VirtualMacPattern("01000ccccccc", 12, "CDP/VTP/DTP multicast", "cisco"),
    VirtualMacPattern("01000ccccccd", 12, "PVST+ multicast", "cisco"),
    VirtualMacPattern("0100", 4,         "IEEE multicast (01:xx)", None),

    # -- MLAG / vPC system MACs (vary by deployment, but common OUIs) -------
    VirtualMacPattern("00000c9f", 8, "Cisco vPC / VSS virtual system MAC", "cisco"),

    # -- LACP -----------------------------------------------------------------
    VirtualMacPattern("0180c2000002", 12, "LACP multicast", None),
]

# Broadcast
_BROADCAST_MAC = "ffffffffffff"


def normalize_mac_raw(mac: str) -> str:
    """Strip a MAC to 12 lowercase hex characters."""
    return mac.lower().replace("-", "").replace(".", "").replace(":", "")


def is_virtual_mac(mac: str) -> VirtualMacPattern | None:
    """Return the matching VirtualMacPattern if this MAC is virtual/reserved,
    or None if it looks like a normal unicast MAC.
    """
    raw = normalize_mac_raw(mac)
    if raw == _BROADCAST_MAC:
        return VirtualMacPattern("ffffffffffff", 12, "broadcast", None)
    for pat in VIRTUAL_MAC_PATTERNS:
        if raw[: pat.mask_len] == pat.prefix:
            return pat
    return None


def is_multicast_mac(mac: str) -> bool:
    """True if the MAC has the multicast bit set (LSB of first octet)."""
    raw = normalize_mac_raw(mac)
    if len(raw) < 2:
        return False
    return int(raw[0:2], 16) & 0x01 == 1
