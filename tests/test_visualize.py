"""Tests for visualization generators."""

from pathlib import Path

from adjacency.models import (
    AdjacencyLink,
    AdjacencyTable,
    DataSource,
    Device,
    HardwareFacts,
    InterfaceInfo,
    LinkType,
)
from adjacency.visualize import generate_dot, generate_html, _platform_color


def _sample_table() -> AdjacencyTable:
    return AdjacencyTable(
        devices={
            "spine-01": Device(
                hostname="spine-01", platform="eos", management_ip="10.0.0.1",
                vendor="Arista", model="DCS-7050TX",
                dns_names=["spine-01.dc1.example.com"],
                hardware=HardwareFacts(
                    vendor="Arista", model="DCS-7050TX",
                    hardware_model="DCS-7050TX-48-R", serial_number="ABC123",
                    os_version="4.28.0F",
                ),
            ),
            "spine-02": Device(
                hostname="spine-02", platform="eos", management_ip="10.0.0.2",
                vendor="Arista",
            ),
            "leaf-01": Device(
                hostname="leaf-01", platform="nxos_ssh", management_ip="10.0.1.1",
                vendor="Cisco", model="N9K-C93180YC-EX",
                hardware=HardwareFacts(
                    vendor="Cisco", model="N9K-C93180YC-EX",
                    hardware_model="N9K-C93180YC-EX", serial_number="XYZ789",
                    os_version="9.3(8)",
                ),
            ),
        },
        links=[
            AdjacencyLink(
                local_device="leaf-01", local_interface="Ethernet1/49",
                remote_device="spine-01", remote_interface="Ethernet1",
                sources=[DataSource.LLDP],
            ),
            AdjacencyLink(
                local_device="leaf-01", local_interface="Ethernet1/50",
                remote_device="spine-02", remote_interface="Ethernet1",
                sources=[DataSource.LLDP, DataSource.MAC_TABLE],
            ),
            AdjacencyLink(
                local_device="leaf-01", local_interface="Port-Channel1",
                remote_device="spine-01", remote_interface="Port-Channel1",
                link_type=LinkType.LAG,
                sources=[DataSource.LLDP],
                members=[
                    AdjacencyLink(
                        local_device="leaf-01", local_interface="Ethernet1/51",
                        remote_device="spine-01", remote_interface="Ethernet3",
                        sources=[DataSource.LLDP],
                    ),
                    AdjacencyLink(
                        local_device="leaf-01", local_interface="Ethernet1/52",
                        remote_device="spine-01", remote_interface="Ethernet4",
                        sources=[DataSource.LLDP],
                    ),
                ],
            ),
        ],
    )


class TestPlatformColors:
    def test_known_platforms(self):
        assert _platform_color("eos") == "#4285F4"
        assert _platform_color("nxos") == "#F4B400"
        assert _platform_color("junos") == "#DB4437"

    def test_nxos_ssh_normalised(self):
        # nxos_ssh should strip _ssh and match nxos
        assert _platform_color("nxos_ssh") == "#F4B400"

    def test_unknown_is_deterministic(self):
        c1 = _platform_color("someos")
        c2 = _platform_color("someos")
        assert c1 == c2
        assert c1.startswith("#")

    def test_none_gives_gray(self):
        assert _platform_color(None) == "#78909C"


class TestHTMLGeneration:
    def test_generates_valid_html(self, tmp_path):
        table = _sample_table()
        out = tmp_path / "test.html"
        result = generate_html(table, out)
        assert result.exists()
        content = result.read_text()
        assert "<!DOCTYPE html>" in content
        assert "cytoscape" in content

    def test_contains_all_nodes(self, tmp_path):
        table = _sample_table()
        out = tmp_path / "test.html"
        generate_html(table, out)
        content = out.read_text()
        assert "spine-01" in content
        assert "spine-02" in content
        assert "leaf-01" in content

    def test_contains_edges(self, tmp_path):
        table = _sample_table()
        out = tmp_path / "test.html"
        generate_html(table, out)
        content = out.read_text()
        assert "Ethernet1/49" in content
        assert "Port-Channel1" in content

    def test_contains_hardware_data(self, tmp_path):
        table = _sample_table()
        out = tmp_path / "test.html"
        generate_html(table, out)
        content = out.read_text()
        assert "DCS-7050TX-48-R" in content
        assert "ABC123" in content

    def test_contains_dns_data(self, tmp_path):
        table = _sample_table()
        out = tmp_path / "test.html"
        generate_html(table, out)
        content = out.read_text()
        assert "spine-01.dc1.example.com" in content

    def test_legend_shows_platforms(self, tmp_path):
        table = _sample_table()
        out = tmp_path / "test.html"
        generate_html(table, out)
        content = out.read_text()
        assert "eos" in content
        assert "nxos_ssh" in content


class TestDOTGeneration:
    def test_generates_valid_dot(self, tmp_path):
        table = _sample_table()
        out = tmp_path / "test.dot"
        dot = generate_dot(table, out)
        assert out.exists()
        assert dot.startswith("graph adjacency {")
        assert dot.strip().endswith("}")

    def test_contains_all_nodes(self, tmp_path):
        table = _sample_table()
        dot = generate_dot(table, tmp_path / "test.dot")
        assert '"spine-01"' in dot
        assert '"spine-02"' in dot
        assert '"leaf-01"' in dot

    def test_lag_edge_is_bold(self, tmp_path):
        table = _sample_table()
        dot = generate_dot(table, tmp_path / "test.dot")
        assert "LAG x2" in dot
        assert "style=bold" in dot

    def test_platform_clusters(self, tmp_path):
        table = _sample_table()
        dot = generate_dot(table, tmp_path / "test.dot")
        assert "cluster_eos" in dot
        assert "cluster_nxos_ssh" in dot

    def test_contains_hardware_in_label(self, tmp_path):
        table = _sample_table()
        dot = generate_dot(table, tmp_path / "test.dot")
        assert "DCS-7050TX-48-R" in dot

    def test_contains_dns_in_label(self, tmp_path):
        table = _sample_table()
        dot = generate_dot(table, tmp_path / "test.dot")
        assert "spine-01.dc1.example.com" in dot
