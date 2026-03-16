"""Tests for the route table collector and extract_route_neighbors helper."""

import ipaddress

from adjacency.collectors.routes import (
    connected_subnets,
    extract_route_neighbors,
    is_adjacent_nexthop,
)
from adjacency.models import DataSource


# Convenience: subnets that would come from get_interfaces_ip on a device
# with Ethernet1 = 10.1.1.1/30 and Management1 = 10.0.0.1/24.
_E1_SUBNET = ipaddress.ip_network("10.1.1.0/30")
_MGMT_SUBNET = ipaddress.ip_network("10.0.0.0/24")
_LOCAL_NETS = [_E1_SUBNET, _MGMT_SUBNET]


class TestExtractRouteNeighbors:
    """Unit tests for extract_route_neighbors."""

    def test_extracts_ospf_nexthop(self):
        route_data = {
            "10.0.1.0/24": [
                {"protocol": "ospf", "current_active": True, "age": 600,
                 "next_hop": "10.1.1.2", "outgoing_interface": "Ethernet1",
                 "preference": 110},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 1
        assert records[0].remote_ip == "10.1.1.2"
        assert records[0].local_interface == "Ethernet1"
        assert records[0].source == DataSource.ROUTE_TABLE

    def test_skips_connected_routes(self):
        route_data = {
            "10.1.1.0/30": [
                {"protocol": "connected", "current_active": True, "age": 0,
                 "next_hop": "", "outgoing_interface": "Ethernet1",
                 "preference": 0},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 0

    def test_skips_local_routes(self):
        route_data = {
            "10.1.1.1/32": [
                {"protocol": "local", "current_active": True, "age": 0,
                 "next_hop": "", "outgoing_interface": "Ethernet1",
                 "preference": 0},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 0

    def test_includes_static_routes(self):
        """Static routes with adjacent next-hops should NOT be skipped."""
        route_data = {
            "0.0.0.0/0": [
                {"protocol": "static", "current_active": True, "age": 0,
                 "next_hop": "10.0.0.254", "outgoing_interface": "Management1",
                 "preference": 1},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 1
        assert records[0].remote_ip == "10.0.0.254"

    def test_skips_inactive_routes(self):
        route_data = {
            "10.0.1.0/24": [
                {"protocol": "ospf", "current_active": False, "age": 600,
                 "next_hop": "10.1.1.2", "outgoing_interface": "Ethernet1",
                 "preference": 110},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 0

    def test_deduplicates_by_interface_and_nexthop(self):
        route_data = {
            "10.0.1.0/24": [
                {"protocol": "ospf", "current_active": True, "age": 600,
                 "next_hop": "10.1.1.2", "outgoing_interface": "Ethernet1",
                 "preference": 110},
            ],
            "10.0.2.0/24": [
                {"protocol": "ospf", "current_active": True, "age": 600,
                 "next_hop": "10.1.1.2", "outgoing_interface": "Ethernet1",
                 "preference": 110},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 1

    def test_multiple_nexthops_different_interfaces(self):
        # Add a second subnet for Ethernet2
        nets = _LOCAL_NETS + [ipaddress.ip_network("10.1.2.0/30")]
        route_data = {
            "10.0.1.0/24": [
                {"protocol": "ospf", "current_active": True, "age": 600,
                 "next_hop": "10.1.1.2", "outgoing_interface": "Ethernet1",
                 "preference": 110},
            ],
            "10.0.2.0/24": [
                {"protocol": "ospf", "current_active": True, "age": 600,
                 "next_hop": "10.1.2.2", "outgoing_interface": "Ethernet2",
                 "preference": 110},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, nets)
        assert len(records) == 2
        ips = {r.remote_ip for r in records}
        assert ips == {"10.1.1.2", "10.1.2.2"}

    def test_skips_zero_nexthop(self):
        route_data = {
            "10.0.1.0/24": [
                {"protocol": "ospf", "current_active": True, "age": 600,
                 "next_hop": "0.0.0.0", "outgoing_interface": "Ethernet1",
                 "preference": 110},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 0

    def test_handles_bgp_routes(self):
        """BGP next-hop on a connected subnet IS adjacent."""
        route_data = {
            "192.168.0.0/16": [
                {"protocol": "bgp", "current_active": True, "age": 3600,
                 "next_hop": "10.1.1.2", "outgoing_interface": "Ethernet1",
                 "preference": 200},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 1
        assert records[0].remote_ip == "10.1.1.2"


class TestAdjacencyFilter:
    """Tests for the connected-subnet adjacency filter."""

    def test_adjacent_nexthop_included(self):
        """Next-hop on a connected /30 is adjacent — should produce a record."""
        route_data = {
            "10.99.0.0/16": [
                {"protocol": "ospf", "current_active": True, "age": 600,
                 "next_hop": "10.1.1.2", "outgoing_interface": "Ethernet1",
                 "preference": 110},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 1
        assert records[0].remote_ip == "10.1.1.2"

    def test_remote_nexthop_excluded(self):
        """Next-hop NOT on any connected subnet — not adjacent, no record."""
        route_data = {
            "192.168.0.0/16": [
                {"protocol": "bgp", "current_active": True, "age": 3600,
                 "next_hop": "172.16.0.1", "outgoing_interface": "Ethernet1",
                 "preference": 200},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 0

    def test_static_adjacent_included(self):
        """A static default route whose next-hop is on the management
        subnet IS an adjacent gateway."""
        route_data = {
            "0.0.0.0/0": [
                {"protocol": "static", "current_active": True, "age": 0,
                 "next_hop": "10.0.0.254", "outgoing_interface": "Management1",
                 "preference": 1},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 1
        assert records[0].remote_ip == "10.0.0.254"

    def test_static_remote_excluded(self):
        """A static route whose next-hop is NOT on a connected subnet
        should be filtered out."""
        route_data = {
            "0.0.0.0/0": [
                {"protocol": "static", "current_active": True, "age": 0,
                 "next_hop": "192.168.1.1", "outgoing_interface": "Management1",
                 "preference": 1},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, _LOCAL_NETS)
        assert len(records) == 0

    def test_empty_subnets_excludes_everything(self):
        """With no connected subnets, no next-hop can be adjacent."""
        route_data = {
            "10.0.1.0/24": [
                {"protocol": "ospf", "current_active": True, "age": 600,
                 "next_hop": "10.1.1.2", "outgoing_interface": "Ethernet1",
                 "preference": 110},
            ],
        }
        records = extract_route_neighbors("sw1", route_data, [])
        assert len(records) == 0


class TestConnectedSubnets:
    """Tests for connected_subnets helper."""

    def test_extracts_subnets(self):
        ip_data = {
            "Ethernet1": {"ipv4": {"10.1.1.1": {"prefix_length": 30}}},
            "Management1": {"ipv4": {"10.0.0.1": {"prefix_length": 24}}},
        }
        nets = connected_subnets(ip_data)
        assert len(nets) == 2
        assert ipaddress.ip_network("10.1.1.0/30") in nets
        assert ipaddress.ip_network("10.0.0.0/24") in nets

    def test_empty_data(self):
        assert connected_subnets({}) == []


class TestIsAdjacentNexthop:
    def test_in_subnet(self):
        nets = [ipaddress.ip_network("10.1.1.0/30")]
        assert is_adjacent_nexthop("10.1.1.2", nets) is True

    def test_not_in_subnet(self):
        nets = [ipaddress.ip_network("10.1.1.0/30")]
        assert is_adjacent_nexthop("172.16.0.1", nets) is False

    def test_invalid_ip(self):
        nets = [ipaddress.ip_network("10.1.1.0/30")]
        assert is_adjacent_nexthop("not-an-ip", nets) is False
