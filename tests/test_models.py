"""Tests for data models."""

from adjacency.models import DataSource, Device, HardwareFacts, NeighborRecord


class TestDeviceMerge:
    def test_merge_fills_gaps(self):
        a = Device(hostname="sw1", platform="eos", known_macs={"aa:bb:cc:dd:ee:ff"})
        b = Device(hostname="sw1", vendor="Arista", known_macs={"11:22:33:44:55:66"}, known_ips={"10.0.0.1"})
        a.merge_identity(b)
        assert a.vendor == "Arista"
        assert a.platform == "eos"  # not overwritten
        assert "11:22:33:44:55:66" in a.known_macs
        assert "aa:bb:cc:dd:ee:ff" in a.known_macs
        assert "10.0.0.1" in a.known_ips

    def test_merge_does_not_overwrite(self):
        a = Device(hostname="sw1", platform="eos")
        b = Device(hostname="sw1", platform="ios")
        a.merge_identity(b)
        assert a.platform == "eos"

    def test_merge_hardware(self):
        a = Device(hostname="sw1")
        hw = HardwareFacts(vendor="Arista", model="7050TX")
        b = Device(hostname="sw1", hardware=hw)
        a.merge_identity(b)
        assert a.hardware is not None
        assert a.hardware.vendor == "Arista"

    def test_merge_dns_names(self):
        a = Device(hostname="sw1", dns_names=["a.example.com"])
        b = Device(hostname="sw1", dns_names=["b.example.com", "a.example.com"])
        a.merge_identity(b)
        assert "a.example.com" in a.dns_names
        assert "b.example.com" in a.dns_names
        assert a.dns_names.count("a.example.com") == 1


class TestNeighborRecord:
    def test_remote_id_prefers_device(self):
        rec = NeighborRecord(
            local_device="sw1",
            local_interface="Eth1",
            remote_device="sw2",
            remote_mac="aa:bb:cc:dd:ee:ff",
            source=DataSource.LLDP,
        )
        assert rec.remote_id == "sw2"

    def test_remote_id_falls_back_to_mac(self):
        rec = NeighborRecord(
            local_device="sw1",
            local_interface="Eth1",
            remote_mac="aa:bb:cc:dd:ee:ff",
            source=DataSource.MAC_TABLE,
        )
        assert rec.remote_id == "aa:bb:cc:dd:ee:ff"
