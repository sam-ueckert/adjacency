"""Tests for the rationalization engine."""

from adjacency.models import (
    AdjacencyLink,
    DataSource,
    Device,
    InterfaceInfo,
    LinkType,
    NeighborRecord,
)
from adjacency.rationalize.engine import rationalize


def _make_device(hostname: str, macs: set[str] | None = None, ips: set[str] | None = None, interfaces: dict | None = None) -> Device:
    return Device(
        hostname=hostname,
        known_macs=macs or set(),
        known_ips=ips or set(),
        interfaces=interfaces or {},
    )


class TestIdentityResolution:
    """Verify that MAC/IP-only records get resolved to hostnames."""

    def test_resolve_via_mac(self):
        devices = {
            "switch-a": _make_device("switch-a"),
            "switch-b": _make_device("switch-b", macs={"aa:bb:cc:dd:ee:ff"}),
        }
        records = [
            NeighborRecord(
                local_device="switch-a",
                local_interface="Ethernet1",
                remote_mac="aa:bb:cc:dd:ee:ff",
                source=DataSource.MAC_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        assert len(table.links) == 1
        assert table.links[0].remote_device == "switch-b"

    def test_resolve_via_ip(self):
        devices = {
            "rtr-a": _make_device("rtr-a"),
            "rtr-b": _make_device("rtr-b", ips={"10.0.0.2"}),
        }
        records = [
            NeighborRecord(
                local_device="rtr-a",
                local_interface="GigabitEthernet0/1",
                remote_ip="10.0.0.2",
                source=DataSource.ARP_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        assert len(table.links) == 1
        assert table.links[0].remote_device == "rtr-b"

    def test_unresolvable_record_dropped(self):
        devices = {"switch-a": _make_device("switch-a")}
        records = [
            NeighborRecord(
                local_device="switch-a",
                local_interface="Ethernet1",
                remote_mac="00:00:00:00:00:01",
                source=DataSource.MAC_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        assert len(table.links) == 0


class TestLLDPRecords:
    """LLDP records already have remote_device filled in."""

    def test_lldp_link_created(self):
        devices = {
            "sw1": _make_device("sw1"),
            "sw2": _make_device("sw2"),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_device="sw2",
                remote_interface="Ethernet1",
                source=DataSource.LLDP,
            ),
        ]
        table = rationalize(devices, records)
        assert len(table.links) == 1
        link = table.links[0]
        assert link.local_device == "sw1"
        assert link.remote_device == "sw2"
        assert DataSource.LLDP in link.sources


class TestDeduplication:
    """Links seen from both sides should be merged."""

    def test_bidirectional_dedup(self):
        devices = {
            "sw1": _make_device("sw1"),
            "sw2": _make_device("sw2"),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_device="sw2",
                remote_interface="Ethernet1",
                source=DataSource.LLDP,
            ),
            NeighborRecord(
                local_device="sw2",
                local_interface="Ethernet1",
                remote_device="sw1",
                remote_interface="Ethernet1",
                source=DataSource.LLDP,
            ),
        ]
        table = rationalize(devices, records)
        assert len(table.links) == 1

    def test_multi_source_merge(self):
        devices = {
            "sw1": _make_device("sw1"),
            "sw2": _make_device("sw2", macs={"aa:bb:cc:dd:ee:ff"}),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_device="sw2",
                remote_interface="Ethernet1",
                source=DataSource.LLDP,
            ),
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_mac="aa:bb:cc:dd:ee:ff",
                source=DataSource.MAC_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        assert len(table.links) == 1
        assert DataSource.LLDP in table.links[0].sources
        assert DataSource.MAC_TABLE in table.links[0].sources


class TestLAGBundles:
    """Physical links belonging to a LAG should be collapsed."""

    def test_lag_collapse(self):
        devices = {
            "sw1": _make_device(
                "sw1",
                interfaces={
                    "Ethernet1": InterfaceInfo(name="Ethernet1", lag_parent="Port-Channel1"),
                    "Ethernet2": InterfaceInfo(name="Ethernet2", lag_parent="Port-Channel1"),
                    "Port-Channel1": InterfaceInfo(name="Port-Channel1", is_lag=True, lag_members=["Ethernet1", "Ethernet2"]),
                },
            ),
            "sw2": _make_device("sw2"),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_device="sw2",
                remote_interface="Ethernet1",
                source=DataSource.LLDP,
            ),
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet2",
                remote_device="sw2",
                remote_interface="Ethernet2",
                source=DataSource.LLDP,
            ),
        ]
        table = rationalize(devices, records)

        lag_links = [l for l in table.links if l.link_type == LinkType.LAG]
        assert len(lag_links) == 1
        assert lag_links[0].local_interface == "Port-Channel1"
        assert len(lag_links[0].members) == 2
