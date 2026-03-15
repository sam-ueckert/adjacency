"""Integration tests that run against a live containerlab topology.

These require:
  1. A running containerlab topology (see lab/ directory)
  2. The --lab pytest flag

Run:
  sudo containerlab deploy -t lab/small-ceos.clab.yml
  pytest tests/integration/ -v --lab
"""

from pathlib import Path

import pytest

LAB_DIR = Path(__file__).parent.parent.parent / "lab"
LAB_INVENTORY = LAB_DIR / "lab-inventory"
LAB_CREDENTIALS = LAB_DIR / "credentials.yaml"


pytestmark = pytest.mark.lab


@pytest.fixture(scope="module")
def inventory_table():
    """Run inventory-mode discovery against the lab."""
    from adjacency.collector import discover

    return discover(
        LAB_INVENTORY,
        collect_l2=True,
        collect_l3=True,
        collect_cdp=True,
        collect_hw_facts=True,
        do_rdns=False,  # lab hosts won't have PTR records
    )


@pytest.fixture(scope="module")
def crawl_table():
    """Run crawl-mode discovery against the lab."""
    from adjacency.crawl import SeedDevice, crawl
    from adjacency.credentials import load_credentials

    cred_store = load_credentials(LAB_CREDENTIALS)
    seeds = [SeedDevice(host="172.20.20.2", platform="eos")]
    return crawl(
        seeds,
        cred_store,
        max_depth=1,
        collect_l2=True,
        collect_l3=True,
        collect_cdp=True,
        do_rdns=False,
    )


class TestInventoryMode:
    def test_discovers_all_devices(self, inventory_table):
        assert len(inventory_table.devices) == 4

    def test_discovers_links(self, inventory_table):
        assert len(inventory_table.links) > 0

    def test_lldp_records_present(self, inventory_table):
        from adjacency.models import DataSource
        sources = {r.source for r in inventory_table.raw_records}
        assert DataSource.LLDP in sources

    def test_devices_have_hostnames(self, inventory_table):
        for dev in inventory_table.devices.values():
            assert dev.hostname

    def test_devices_have_interfaces(self, inventory_table):
        for dev in inventory_table.devices.values():
            assert len(dev.interfaces) > 0


class TestCrawlMode:
    def test_discovers_devices(self, crawl_table):
        # Should find at least the seed + its neighbors
        assert len(crawl_table.devices) >= 2

    def test_discovers_links(self, crawl_table):
        assert len(crawl_table.links) > 0

    def test_devices_have_hardware_facts(self, crawl_table):
        for dev in crawl_table.devices.values():
            assert dev.hardware is not None
            assert dev.vendor


class TestVisualization:
    def test_html_from_lab(self, inventory_table, tmp_path):
        from adjacency.visualize import generate_html

        out = tmp_path / "lab.html"
        generate_html(inventory_table, out, title="Lab Test")
        content = out.read_text()
        assert "spine-01" in content
        assert "leaf-01" in content

    def test_dot_from_lab(self, inventory_table, tmp_path):
        from adjacency.visualize import generate_dot

        out = tmp_path / "lab.dot"
        dot = generate_dot(inventory_table, out)
        assert "spine-01" in dot


class TestSnapshot:
    def test_save_and_reload(self, inventory_table, tmp_path):
        from adjacency.store import load_snapshot, save_snapshot

        path = save_snapshot(inventory_table, label="lab-test", snapshot_dir=tmp_path)
        meta, reloaded = load_snapshot("lab-test", tmp_path)
        assert len(reloaded.devices) == len(inventory_table.devices)
        assert len(reloaded.links) == len(inventory_table.links)
