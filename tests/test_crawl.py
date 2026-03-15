"""Tests for the crawl engine internals.

These tests exercise the helper functions and data flow without requiring
actual network connections.  Connection/NAPALM tests would need mocking
of the NAPALM driver, which is deferred to integration tests.
"""

from unittest.mock import MagicMock, patch

from adjacency.crawl import (
    CrawlTarget,
    SeedDevice,
    _add_next_hop,
    _collect_device,
    _is_ip,
    _normalize_mac,
)
from adjacency.models import DataSource


class TestIsIP:
    def test_valid_ipv4(self):
        assert _is_ip("10.0.0.1") is True
        assert _is_ip("192.168.1.1") is True
        assert _is_ip("255.255.255.255") is True

    def test_hostname(self):
        assert _is_ip("switch-01") is False
        assert _is_ip("switch-01.example.com") is False

    def test_partial(self):
        assert _is_ip("10.0.0") is False

    def test_out_of_range(self):
        assert _is_ip("10.0.0.999") is False


class TestAddNextHop:
    def test_with_ip(self):
        targets: list[CrawlTarget] = []
        _add_next_hop(targets, "sw2", "10.0.0.2", "Arista EOS")
        assert len(targets) == 1
        assert targets[0].ip == "10.0.0.2"
        assert targets[0].hostname == "sw2"
        assert targets[0].platform_hint == "eos"

    def test_with_hostname_only(self):
        targets: list[CrawlTarget] = []
        _add_next_hop(targets, "sw2", None, None)
        assert len(targets) == 1
        assert targets[0].ip == ""
        assert targets[0].hostname == "sw2"

    def test_nothing_if_no_ip_or_hostname(self):
        targets: list[CrawlTarget] = []
        _add_next_hop(targets, None, None, None)
        assert len(targets) == 0


class TestCollectDevice:
    """Test _collect_device with a mocked NAPALM driver."""

    def _mock_driver(self):
        drv = MagicMock()
        drv.platform = "eos"
        drv.get_facts.return_value = {
            "hostname": "spine-01",
            "vendor": "Arista",
            "model": "DCS-7050TX",
            "serial_number": "ABC123",
            "os_version": "4.28.0F",
            "uptime": 86400,
            "fqdn": "spine-01.dc1.example.com",
        }
        drv.get_interfaces.return_value = {
            "Ethernet1": {
                "is_up": True, "is_enabled": True, "mac_address": "aa:bb:cc:dd:ee:01",
                "speed": 10000, "mtu": 9214, "description": "to leaf-01",
            },
            "Management1": {
                "is_up": True, "is_enabled": True, "mac_address": "aa:bb:cc:dd:ee:00",
                "speed": 1000, "mtu": 1500, "description": "",
            },
        }
        drv.get_interfaces_ip.return_value = {
            "Management1": {"ipv4": {"10.0.0.1": {"prefix_length": 24}}},
        }
        drv.get_lldp_neighbors_detail.return_value = {
            "Ethernet1": [{
                "remote_system_name": "leaf-01",
                "remote_port": "Ethernet49",
                "remote_chassis_id": "11:22:33:44:55:66",
                "remote_system_description": "Cisco NX-OS(tm) n9000",
            }],
        }
        drv.cli.return_value = {}  # no CDP
        drv.get_mac_address_table.return_value = [
            {"mac": "11:22:33:44:55:66", "interface": "Ethernet1",
             "vlan": 1, "static": False, "active": True, "moves": 0, "last_move": 0.0},
        ]
        drv.get_arp_table.return_value = [
            {"interface": "Ethernet1", "mac": "11:22:33:44:55:66",
             "ip": "10.0.1.1", "age": 300.0},
        ]
        return drv

    def test_builds_device(self):
        drv = self._mock_driver()
        device, records, next_hops = _collect_device(drv, "10.0.0.1")
        assert device.hostname == "spine-01"
        assert device.vendor == "Arista"
        assert device.hardware is not None
        assert device.hardware.serial_number == "ABC123"
        assert "Ethernet1" in device.interfaces
        assert "Management1" in device.interfaces

    def test_collects_lldp_records(self):
        drv = self._mock_driver()
        _, records, _ = _collect_device(drv, "10.0.0.1")
        lldp = [r for r in records if r.source == DataSource.LLDP]
        assert len(lldp) == 1
        assert lldp[0].remote_device == "leaf-01"
        assert lldp[0].remote_interface == "Ethernet49"

    def test_collects_mac_records(self):
        drv = self._mock_driver()
        _, records, _ = _collect_device(drv, "10.0.0.1")
        mac = [r for r in records if r.source == DataSource.MAC_TABLE]
        assert len(mac) == 1

    def test_collects_arp_records(self):
        drv = self._mock_driver()
        _, records, _ = _collect_device(drv, "10.0.0.1")
        arp = [r for r in records if r.source == DataSource.ARP_TABLE]
        assert len(arp) == 1
        assert arp[0].remote_ip == "10.0.1.1"

    def test_extracts_next_hops(self):
        drv = self._mock_driver()
        _, _, next_hops = _collect_device(drv, "10.0.0.1")
        assert len(next_hops) >= 1
        # LLDP neighbor leaf-01 should be in next hops
        hostnames = {h.hostname for h in next_hops}
        assert "leaf-01" in hostnames

    def test_detects_platform_from_lldp(self):
        drv = self._mock_driver()
        _, _, next_hops = _collect_device(drv, "10.0.0.1")
        leaf_hops = [h for h in next_hops if h.hostname == "leaf-01"]
        assert leaf_hops[0].platform_hint == "nxos_ssh"

    def test_hostname_override(self):
        drv = self._mock_driver()
        device, _, _ = _collect_device(drv, "10.0.0.1", hostname_override="custom-name")
        assert device.hostname == "custom-name"

    def test_skip_l2_l3(self):
        drv = self._mock_driver()
        _, records, _ = _collect_device(
            drv, "10.0.0.1", collect_l2=False, collect_l3=False, collect_cdp=False,
        )
        # Only LLDP records should remain
        sources = {r.source for r in records}
        assert sources == {DataSource.LLDP}
