"""Tests for virtual/shared MAC detection and identity exclusion."""

from adjacency.models import DataSource, Device, InterfaceInfo, NeighborRecord
from adjacency.rationalize.engine import rationalize
from adjacency.virtual_macs import is_multicast_mac, is_virtual_mac


class TestVirtualMacDetection:
    def test_hsrp_v1_detected(self):
        assert is_virtual_mac("00:00:0c:07:ac:01") is not None

    def test_hsrp_v2_detected(self):
        assert is_virtual_mac("00:00:0c:9f:f0:01") is not None

    def test_vrrp_detected(self):
        assert is_virtual_mac("00:00:5e:00:01:01") is not None

    def test_glbp_detected(self):
        assert is_virtual_mac("00:07:b4:00:01:01") is not None

    def test_lldp_multicast_detected(self):
        assert is_virtual_mac("01:80:c2:00:00:0e") is not None

    def test_cdp_multicast_detected(self):
        assert is_virtual_mac("01:00:0c:cc:cc:cc") is not None

    def test_normal_mac_not_flagged(self):
        assert is_virtual_mac("aa:bb:cc:dd:ee:ff") is None

    def test_cisco_vpc_detected(self):
        assert is_virtual_mac("00:00:0c:9f:00:01") is not None

    def test_multicast_bit(self):
        assert is_multicast_mac("01:00:00:00:00:00") is True
        assert is_multicast_mac("00:11:22:33:44:55") is False

    def test_various_mac_formats(self):
        # Dotted (Cisco style)
        assert is_virtual_mac("0000.0c07.ac01") is not None
        # Dashed (Windows style)
        assert is_virtual_mac("00-00-0C-07-AC-01") is not None
        # No separators
        assert is_virtual_mac("00000c07ac01") is not None


class TestSharedMacRationalization:
    """Verify that shared/virtual MACs don't cause misattribution."""

    def _make_device(self, hostname, macs=None, ips=None, shared_macs=None, shared_ips=None):
        return Device(
            hostname=hostname,
            known_macs=macs or set(),
            known_ips=ips or set(),
            shared_macs=shared_macs or set(),
            shared_ips=shared_ips or set(),
        )

    def test_hsrp_mac_not_used_for_identity(self):
        """A MAC table entry for an HSRP virtual MAC should not resolve."""
        hsrp_mac = "00:00:0c:07:ac:01"
        devices = {
            "sw1": self._make_device("sw1"),
            "rtr1": self._make_device("rtr1", macs={hsrp_mac}),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_mac=hsrp_mac,
                source=DataSource.MAC_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        # Should NOT create a link — the HSRP MAC is virtual
        assert len(table.links) == 0

    def test_shared_ip_not_used_for_identity(self):
        """An IP claimed by two devices (VIP) should not resolve to either."""
        vip = "10.0.0.100"
        devices = {
            "sw1": self._make_device("sw1"),
            "rtr1": self._make_device("rtr1", ips={vip, "10.0.0.1"}),
            "rtr2": self._make_device("rtr2", ips={vip, "10.0.0.2"}),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_ip=vip,
                source=DataSource.ARP_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        # VIP is shared — should not resolve to rtr1 or rtr2
        assert len(table.links) == 0

    def test_unique_ip_still_resolves_when_vip_excluded(self):
        """Non-shared IPs should still work even when a VIP exists."""
        vip = "10.0.0.100"
        devices = {
            "sw1": self._make_device("sw1"),
            "rtr1": self._make_device("rtr1", ips={vip, "10.0.0.1"}),
            "rtr2": self._make_device("rtr2", ips={vip, "10.0.0.2"}),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_ip="10.0.0.1",
                source=DataSource.ARP_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        assert len(table.links) == 1
        assert table.links[0].remote_device == "rtr1"

    def test_mac_shared_by_two_devices_excluded(self):
        """A MAC appearing in known_macs of two devices (MLAG system MAC)
        should be treated as shared and not used for resolution."""
        mlag_mac = "aa:bb:cc:00:00:01"
        devices = {
            "sw1": self._make_device("sw1"),
            "peer1": self._make_device("peer1", macs={mlag_mac, "11:11:11:11:11:11"}),
            "peer2": self._make_device("peer2", macs={mlag_mac, "22:22:22:22:22:22"}),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_mac=mlag_mac,
                source=DataSource.MAC_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        # Shared MAC — should not resolve
        assert len(table.links) == 0

    def test_explicit_shared_mac_on_device(self):
        """Device.shared_macs should be excluded even if only one device has it."""
        vmac = "aa:bb:cc:dd:ee:ff"
        devices = {
            "sw1": self._make_device("sw1"),
            "rtr1": self._make_device("rtr1", shared_macs={vmac}),
        }
        records = [
            NeighborRecord(
                local_device="sw1",
                local_interface="Ethernet1",
                remote_mac=vmac,
                source=DataSource.MAC_TABLE,
            ),
        ]
        table = rationalize(devices, records)
        assert len(table.links) == 0
