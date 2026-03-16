"""Data collectors — pull structured tables from network devices via NAPALM."""

from adjacency.collectors.arp import collect_arp_table
from adjacency.collectors.cdp import collect_cdp_neighbors
from adjacency.collectors.facts import collect_facts
from adjacency.collectors.interfaces import collect_interfaces
from adjacency.collectors.lldp import collect_lldp_neighbors
from adjacency.collectors.mac import collect_mac_table
from adjacency.collectors.routes import collect_routes

__all__ = [
    "collect_arp_table",
    "collect_cdp_neighbors",
    "collect_facts",
    "collect_interfaces",
    "collect_lldp_neighbors",
    "collect_mac_table",
    "collect_routes",
]
