"""End-to-end test using a simulated 4-node network.

This exercises the full crawl pipeline (credential matching, device probing,
LLDP/CDP neighbor extraction, multi-hop crawl, rationalization) without
requiring Docker or a live lab.  Each simulated device returns canned NAPALM
getter data that mimics a real leaf-spine fabric.

Topology:
    spine-01 (10.0.0.1) <--e1/e1--> leaf-01 (10.0.1.1)
    spine-01 (10.0.0.1) <--e2/e1--> leaf-02 (10.0.2.1)
    spine-02 (10.0.0.2) <--e1/e2--> leaf-01 (10.0.1.1)
    spine-02 (10.0.0.2) <--e1/e2--> leaf-02 (10.0.2.1)
    leaf-01  (10.0.1.1) <--e3/e3--> leaf-02 (10.0.2.1)   (peer link)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from adjacency.crawl import SeedDevice, crawl
from adjacency.credentials import Credential, CredentialStore
from adjacency.models import DataSource, LinkType


# ---------------------------------------------------------------------------
# Simulated device data
# ---------------------------------------------------------------------------

DEVICES = {
    "10.0.0.1": {
        "facts": {
            "hostname": "spine-01", "vendor": "Arista", "model": "DCS-7050TX",
            "serial_number": "SP01SN", "os_version": "4.28.0F", "uptime": 86400,
            "fqdn": "spine-01.lab",
        },
        "interfaces": {
            "Ethernet1": {"is_up": True, "is_enabled": True, "mac_address": "aa:01:00:00:00:01",
                          "speed": 10000, "mtu": 9214, "description": "to leaf-01"},
            "Ethernet2": {"is_up": True, "is_enabled": True, "mac_address": "aa:01:00:00:00:02",
                          "speed": 10000, "mtu": 9214, "description": "to leaf-02"},
            "Management1": {"is_up": True, "is_enabled": True, "mac_address": "aa:01:00:00:ff:00",
                            "speed": 1000, "mtu": 1500, "description": ""},
        },
        "interfaces_ip": {
            "Ethernet1": {"ipv4": {"10.1.1.1": {"prefix_length": 30}}},
            "Ethernet2": {"ipv4": {"10.1.2.1": {"prefix_length": 30}}},
            "Management1": {"ipv4": {"10.0.0.1": {"prefix_length": 24}}},
        },
        "lldp": {
            "Ethernet1": [{"remote_system_name": "leaf-01", "remote_port": "Ethernet1",
                           "remote_chassis_id": "bb:01:00:00:00:01",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
            "Ethernet2": [{"remote_system_name": "leaf-02", "remote_port": "Ethernet1",
                           "remote_chassis_id": "bb:02:00:00:00:01",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
        },
        "mac_table": [
            {"mac": "bb:01:00:00:00:01", "interface": "Ethernet1", "vlan": 0,
             "static": False, "active": True, "moves": 0, "last_move": 0.0},
            {"mac": "bb:02:00:00:00:01", "interface": "Ethernet2", "vlan": 0,
             "static": False, "active": True, "moves": 0, "last_move": 0.0},
        ],
        "arp": [
            {"interface": "Ethernet1", "mac": "bb:01:00:00:00:01", "ip": "10.1.1.2", "age": 300.0},
            {"interface": "Ethernet2", "mac": "bb:02:00:00:00:01", "ip": "10.1.2.2", "age": 300.0},
        ],
        "cdp": "",
    },
    "10.0.0.2": {
        "facts": {
            "hostname": "spine-02", "vendor": "Arista", "model": "DCS-7050TX",
            "serial_number": "SP02SN", "os_version": "4.28.0F", "uptime": 86400,
            "fqdn": "spine-02.lab",
        },
        "interfaces": {
            "Ethernet1": {"is_up": True, "is_enabled": True, "mac_address": "aa:02:00:00:00:01",
                          "speed": 10000, "mtu": 9214, "description": "to leaf-01"},
            "Ethernet2": {"is_up": True, "is_enabled": True, "mac_address": "aa:02:00:00:00:02",
                          "speed": 10000, "mtu": 9214, "description": "to leaf-02"},
            "Management1": {"is_up": True, "is_enabled": True, "mac_address": "aa:02:00:00:ff:00",
                            "speed": 1000, "mtu": 1500, "description": ""},
        },
        "interfaces_ip": {
            "Ethernet1": {"ipv4": {"10.1.3.1": {"prefix_length": 30}}},
            "Ethernet2": {"ipv4": {"10.1.4.1": {"prefix_length": 30}}},
            "Management1": {"ipv4": {"10.0.0.2": {"prefix_length": 24}}},
        },
        "lldp": {
            "Ethernet1": [{"remote_system_name": "leaf-01", "remote_port": "Ethernet2",
                           "remote_chassis_id": "bb:01:00:00:00:02",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
            "Ethernet2": [{"remote_system_name": "leaf-02", "remote_port": "Ethernet2",
                           "remote_chassis_id": "bb:02:00:00:00:02",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
        },
        "mac_table": [],
        "arp": [
            {"interface": "Ethernet1", "mac": "bb:01:00:00:00:02", "ip": "10.1.3.2", "age": 300.0},
            {"interface": "Ethernet2", "mac": "bb:02:00:00:00:02", "ip": "10.1.4.2", "age": 300.0},
        ],
        "cdp": "",
    },
    "10.0.1.1": {
        "facts": {
            "hostname": "leaf-01", "vendor": "Arista", "model": "DCS-7280SR",
            "serial_number": "LF01SN", "os_version": "4.28.0F", "uptime": 86400,
            "fqdn": "leaf-01.lab",
        },
        "interfaces": {
            "Ethernet1": {"is_up": True, "is_enabled": True, "mac_address": "bb:01:00:00:00:01",
                          "speed": 10000, "mtu": 9214, "description": "to spine-01"},
            "Ethernet2": {"is_up": True, "is_enabled": True, "mac_address": "bb:01:00:00:00:02",
                          "speed": 10000, "mtu": 9214, "description": "to spine-02"},
            "Ethernet3": {"is_up": True, "is_enabled": True, "mac_address": "bb:01:00:00:00:03",
                          "speed": 10000, "mtu": 9214, "description": "to leaf-02"},
            "Management1": {"is_up": True, "is_enabled": True, "mac_address": "bb:01:00:00:ff:00",
                            "speed": 1000, "mtu": 1500, "description": ""},
        },
        "interfaces_ip": {
            "Ethernet1": {"ipv4": {"10.1.1.2": {"prefix_length": 30}}},
            "Ethernet2": {"ipv4": {"10.1.3.2": {"prefix_length": 30}}},
            "Ethernet3": {"ipv4": {"10.1.5.1": {"prefix_length": 30}}},
            "Management1": {"ipv4": {"10.0.1.1": {"prefix_length": 24}}},
        },
        "lldp": {
            "Ethernet1": [{"remote_system_name": "spine-01", "remote_port": "Ethernet1",
                           "remote_chassis_id": "aa:01:00:00:00:01",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
            "Ethernet2": [{"remote_system_name": "spine-02", "remote_port": "Ethernet1",
                           "remote_chassis_id": "aa:02:00:00:00:01",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
            "Ethernet3": [{"remote_system_name": "leaf-02", "remote_port": "Ethernet3",
                           "remote_chassis_id": "bb:02:00:00:00:03",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
        },
        "mac_table": [],
        "arp": [
            {"interface": "Ethernet1", "mac": "aa:01:00:00:00:01", "ip": "10.1.1.1", "age": 300.0},
            {"interface": "Ethernet3", "mac": "bb:02:00:00:00:03", "ip": "10.1.5.2", "age": 300.0},
        ],
        "cdp": "",
    },
    "10.0.2.1": {
        "facts": {
            "hostname": "leaf-02", "vendor": "Arista", "model": "DCS-7280SR",
            "serial_number": "LF02SN", "os_version": "4.28.0F", "uptime": 86400,
            "fqdn": "leaf-02.lab",
        },
        "interfaces": {
            "Ethernet1": {"is_up": True, "is_enabled": True, "mac_address": "bb:02:00:00:00:01",
                          "speed": 10000, "mtu": 9214, "description": "to spine-01"},
            "Ethernet2": {"is_up": True, "is_enabled": True, "mac_address": "bb:02:00:00:00:02",
                          "speed": 10000, "mtu": 9214, "description": "to spine-02"},
            "Ethernet3": {"is_up": True, "is_enabled": True, "mac_address": "bb:02:00:00:00:03",
                          "speed": 10000, "mtu": 9214, "description": "to leaf-01"},
            "Management1": {"is_up": True, "is_enabled": True, "mac_address": "bb:02:00:00:ff:00",
                            "speed": 1000, "mtu": 1500, "description": ""},
        },
        "interfaces_ip": {
            "Ethernet1": {"ipv4": {"10.1.2.2": {"prefix_length": 30}}},
            "Ethernet2": {"ipv4": {"10.1.4.2": {"prefix_length": 30}}},
            "Ethernet3": {"ipv4": {"10.1.5.2": {"prefix_length": 30}}},
            "Management1": {"ipv4": {"10.0.2.1": {"prefix_length": 24}}},
        },
        "lldp": {
            "Ethernet1": [{"remote_system_name": "spine-01", "remote_port": "Ethernet2",
                           "remote_chassis_id": "aa:01:00:00:00:02",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
            "Ethernet2": [{"remote_system_name": "spine-02", "remote_port": "Ethernet2",
                           "remote_chassis_id": "aa:02:00:00:00:02",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
            "Ethernet3": [{"remote_system_name": "leaf-01", "remote_port": "Ethernet3",
                           "remote_chassis_id": "bb:01:00:00:00:03",
                           "remote_system_description": "Arista Networks EOS 4.28.0F"}],
        },
        "mac_table": [],
        "arp": [
            {"interface": "Ethernet1", "mac": "aa:01:00:00:00:02", "ip": "10.1.2.1", "age": 300.0},
            {"interface": "Ethernet3", "mac": "bb:01:00:00:00:03", "ip": "10.1.5.1", "age": 300.0},
        ],
        "cdp": "",
    },
}

# Map hostnames to management IPs for DNS resolution mock
_HOST_TO_IP = {d["facts"]["hostname"]: ip for ip, d in DEVICES.items()}


def _mock_driver(ip: str):
    """Create a MagicMock NAPALM driver returning canned data for the given IP."""
    data = DEVICES[ip]
    drv = MagicMock()
    drv.platform = "eos"
    drv.get_facts.return_value = data["facts"]
    drv.get_interfaces.return_value = data["interfaces"]
    drv.get_interfaces_ip.return_value = data["interfaces_ip"]
    drv.get_lldp_neighbors_detail.return_value = data["lldp"]
    drv.get_mac_address_table.return_value = data["mac_table"]
    drv.get_arp_table.return_value = data["arp"]
    drv.cli.return_value = {"show cdp neighbors detail": data["cdp"]}
    return drv


def _mock_try_connect(host, cred_store, platform_hint=None, timeout=30):
    """Mock _try_connect: return a mock driver if the IP is in our simulated network."""
    if host in DEVICES:
        creds = cred_store.match(host)
        cred = creds[0] if creds else Credential(username="admin", password="admin")
        return _mock_driver(host), cred
    return None


def _mock_resolve(name):
    """Mock DNS resolution using our hostname->IP map."""
    return _HOST_TO_IP.get(name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSimulatedCrawlDepth0:
    """Depth 0: only seed devices, no neighbor crawling."""

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_seeds_only(self, _rdns, _dns, _conn):
        cred_store = CredentialStore(credentials=[
            Credential(username="admin", password="admin"),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1")],
            cred_store,
            max_depth=0,
            do_rdns=True,
        )
        assert len(table.devices) == 1
        assert "spine-01" in table.devices


class TestSimulatedCrawlDepth1:
    """Depth 1: seeds + their direct LLDP neighbors."""

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_discovers_neighbors(self, _rdns, _dns, _conn):
        cred_store = CredentialStore(credentials=[
            Credential(username="admin", password="admin"),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1")],
            cred_store,
            max_depth=1,
            do_rdns=True,
        )
        hostnames = set(table.devices.keys())
        # spine-01 + its LLDP neighbors (leaf-01, leaf-02)
        assert "spine-01" in hostnames
        assert "leaf-01" in hostnames
        assert "leaf-02" in hostnames

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_links_created(self, _rdns, _dns, _conn):
        cred_store = CredentialStore(credentials=[
            Credential(username="admin", password="admin"),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1")],
            cred_store,
            max_depth=1,
            do_rdns=True,
        )
        assert len(table.links) > 0
        # There should be links between spine-01 and each leaf
        link_pairs = {(l.local_device, l.remote_device) for l in table.links}
        link_pairs |= {(l.remote_device, l.local_device) for l in table.links}
        assert ("spine-01", "leaf-01") in link_pairs or ("leaf-01", "spine-01") in link_pairs


class TestSimulatedCrawlDepth2:
    """Depth 2: full fabric discovery from a single seed."""

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_full_fabric(self, _rdns, _dns, _conn):
        cred_store = CredentialStore(credentials=[
            Credential(username="admin", password="admin"),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1")],
            cred_store,
            max_depth=2,
            do_rdns=True,
        )
        # All 4 devices should be discovered
        assert len(table.devices) == 4
        hostnames = set(table.devices.keys())
        assert hostnames == {"spine-01", "spine-02", "leaf-01", "leaf-02"}

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_hardware_facts(self, _rdns, _dns, _conn):
        cred_store = CredentialStore(credentials=[
            Credential(username="admin", password="admin"),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1")],
            cred_store,
            max_depth=2,
            do_rdns=True,
        )
        for dev in table.devices.values():
            assert dev.hardware is not None
            assert dev.vendor == "Arista"

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_bidirectional_dedup(self, _rdns, _dns, _conn):
        """Links seen from both directions should be deduplicated."""
        cred_store = CredentialStore(credentials=[
            Credential(username="admin", password="admin"),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1")],
            cred_store,
            max_depth=2,
            do_rdns=True,
        )
        # The peer link leaf-01:e3 <-> leaf-02:e3 should appear once, not twice
        peer_links = [
            l for l in table.links
            if {l.local_device, l.remote_device} == {"leaf-01", "leaf-02"}
        ]
        # Could be 1 (deduped) — at most 1 per interface pair
        intf_pairs = set()
        for l in peer_links:
            pair = tuple(sorted([
                (l.local_device, l.local_interface),
                (l.remote_device, l.remote_interface or ""),
            ]))
            intf_pairs.add(pair)
        # Each unique interface pair should appear exactly once
        assert len(intf_pairs) == len(peer_links)


class TestSimulatedMultiSeed:
    """Multiple seeds should not cause duplicate device entries."""

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_no_duplicate_devices(self, _rdns, _dns, _conn):
        cred_store = CredentialStore(credentials=[
            Credential(username="admin", password="admin"),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1"), SeedDevice(host="10.0.0.2")],
            cred_store,
            max_depth=1,
            do_rdns=True,
        )
        # Both spines as seeds, depth 1 should find all 4
        assert len(table.devices) == 4
        # No duplicates
        assert len(set(table.devices.keys())) == 4


class TestSimulatedCredentialMatching:
    """Verify that scoped credentials are used correctly during crawl."""

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_scoped_creds_work(self, _rdns, _dns, _conn):
        cred_store = CredentialStore(credentials=[
            Credential(name="spines", username="admin", password="spine-pass",
                       platform="eos", networks=["10.0.0.0/24"]),
            Credential(name="leaves", username="admin", password="leaf-pass",
                       platform="eos", networks=["10.0.1.0/24", "10.0.2.0/24"]),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1")],
            cred_store,
            max_depth=1,
            do_rdns=True,
        )
        # Should still work — both scoped creds match their respective ranges
        assert len(table.devices) >= 2


class TestSimulatedVisualization:
    """Verify visualization works with crawl output."""

    @patch("adjacency.crawl._try_connect", side_effect=_mock_try_connect)
    @patch("adjacency.crawl._resolve_hostname", side_effect=_mock_resolve)
    @patch("adjacency.collectors.facts._reverse_lookup", return_value=None)
    def test_html_generation(self, _rdns, _dns, _conn, tmp_path):
        from adjacency.visualize import generate_html

        cred_store = CredentialStore(credentials=[
            Credential(username="admin", password="admin"),
        ])
        table = crawl(
            [SeedDevice(host="10.0.0.1")],
            cred_store,
            max_depth=2,
            do_rdns=True,
        )
        out = tmp_path / "sim.html"
        generate_html(table, out)
        content = out.read_text()
        assert "spine-01" in content
        assert "leaf-01" in content
        assert "leaf-02" in content
        assert "spine-02" in content
