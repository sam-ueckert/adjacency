"""Data models for devices, interfaces, and adjacency links."""

from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import BaseModel, Field


class DataSource(str, Enum):
    """Origin of a piece of adjacency data."""

    LLDP = "lldp"
    CDP = "cdp"
    MAC_TABLE = "mac_table"
    ARP_TABLE = "arp_table"
    ROUTE_TABLE = "route_table"
    INTERFACE = "interface"
    MANUAL = "manual"


class InterfaceInfo(BaseModel):
    """A single device interface with optional LAG membership."""

    name: str
    mac_address: str | None = None
    ip_addresses: list[str] = Field(default_factory=list)
    speed_mbps: int | None = None
    is_up: bool = True
    mtu: int | None = None
    description: str = ""

    # LAG relationships
    is_lag: bool = False
    lag_parent: str | None = None  # port-channel this interface belongs to
    lag_members: list[str] = Field(default_factory=list)  # if this IS a LAG


class HardwareFacts(BaseModel):
    """Hardware and software identity facts for a device."""

    vendor: str | None = None
    model: str | None = None
    hardware_model: str | None = None  # full hardware SKU, e.g. "WS-C3850-48T-S"
    serial_number: str | None = None
    os_version: str | None = None
    uptime_seconds: int | None = None
    fqdn: str | None = None  # device-reported FQDN


class Device(BaseModel):
    """A network device discovered or configured in inventory."""

    hostname: str
    platform: str | None = None
    management_ip: str | None = None
    serial: str | None = None
    vendor: str | None = None
    model: str | None = None
    os_version: str | None = None

    # Reverse DNS name(s) resolved from management_ip and interface IPs
    dns_names: list[str] = Field(default_factory=list)

    # Structured hardware/software facts from NAPALM get_facts
    hardware: HardwareFacts | None = None

    interfaces: dict[str, InterfaceInfo] = Field(default_factory=dict)

    # All known MAC addresses for this device (across all interfaces)
    known_macs: set[str] = Field(default_factory=set)
    # All known IP addresses
    known_ips: set[str] = Field(default_factory=set)

    # Virtual / shared addresses — present on this device but NOT safe for
    # unique identity resolution (HSRP/VRRP VIPs, virtual MACs, etc.)
    shared_macs: set[str] = Field(default_factory=set)
    shared_ips: set[str] = Field(default_factory=set)

    def merge_identity(self, other: Self) -> None:
        """Absorb identity info from another Device record for the same box."""
        if other.platform and not self.platform:
            self.platform = other.platform
        if other.management_ip and not self.management_ip:
            self.management_ip = other.management_ip
        if other.vendor and not self.vendor:
            self.vendor = other.vendor
        if other.model and not self.model:
            self.model = other.model
        if other.os_version and not self.os_version:
            self.os_version = other.os_version
        if other.hardware and not self.hardware:
            self.hardware = other.hardware
        if other.dns_names:
            existing = set(self.dns_names)
            for name in other.dns_names:
                if name not in existing:
                    self.dns_names.append(name)
        self.known_macs |= other.known_macs
        self.known_ips |= other.known_ips
        self.shared_macs |= other.shared_macs
        self.shared_ips |= other.shared_ips


class NeighborRecord(BaseModel):
    """A raw neighbor observation from a single source before rationalization."""

    local_device: str
    local_interface: str
    remote_device: str | None = None
    remote_interface: str | None = None
    remote_mac: str | None = None
    remote_ip: str | None = None
    remote_platform: str | None = None
    source: DataSource

    @property
    def remote_id(self) -> str:
        """Best available identifier for the remote end."""
        return self.remote_device or self.remote_mac or self.remote_ip or "unknown"


class LinkType(str, Enum):
    PHYSICAL = "physical"
    LAG = "lag"
    LOGICAL = "logical"


class AdjacencyLink(BaseModel):
    """A rationalized link between two devices.

    For LAG bundles, ``members`` contains the constituent physical links
    and ``link_type`` is ``LinkType.LAG``.
    """

    local_device: str
    local_interface: str
    remote_device: str
    remote_interface: str | None = None
    link_type: LinkType = LinkType.PHYSICAL

    # Evidence that contributed to this link
    sources: list[DataSource] = Field(default_factory=list)

    # LAG bundle members (only populated when link_type == LAG)
    members: list[AdjacencyLink] = Field(default_factory=list)

    # Supplementary data attached during rationalization
    remote_mac: str | None = None
    remote_ip: str | None = None
    speed_mbps: int | None = None


class AdjacencyTable(BaseModel):
    """The final rationalized view: devices and the links between them."""

    devices: dict[str, Device] = Field(default_factory=dict)
    links: list[AdjacencyLink] = Field(default_factory=list)

    # Raw records kept for audit / debugging
    raw_records: list[NeighborRecord] = Field(default_factory=list)
