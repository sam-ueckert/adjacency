"""Tests for hardware facts enrichment and reverse DNS."""

from unittest.mock import AsyncMock, patch

from adjacency.collectors.facts import enrich_devices_with_facts, enrich_devices_with_rdns
from adjacency.models import Device, HardwareFacts


def _make_device(hostname, management_ip=None, ips=None):
    return Device(
        hostname=hostname,
        management_ip=management_ip,
        known_ips=ips or set(),
    )


class TestFactsEnrichment:
    def test_merges_into_device(self):
        devices = {"sw1": _make_device("sw1")}
        facts = {
            "sw1": HardwareFacts(
                vendor="Arista",
                model="DCS-7050TX",
                hardware_model="DCS-7050TX-48-R",
                serial_number="ABC123",
                os_version="4.28.0F",
            ),
        }
        enrich_devices_with_facts(devices, facts)
        dev = devices["sw1"]
        assert dev.vendor == "Arista"
        assert dev.model == "DCS-7050TX"
        assert dev.serial == "ABC123"
        assert dev.os_version == "4.28.0F"
        assert dev.hardware is not None
        assert dev.hardware.hardware_model == "DCS-7050TX-48-R"

    def test_does_not_overwrite_existing(self):
        devices = {"sw1": _make_device("sw1")}
        devices["sw1"].vendor = "ExistingVendor"
        facts = {
            "sw1": HardwareFacts(vendor="NewVendor", model="X"),
        }
        enrich_devices_with_facts(devices, facts)
        assert devices["sw1"].vendor == "ExistingVendor"
        assert devices["sw1"].model == "X"

    def test_skips_unknown_host(self):
        devices = {"sw1": _make_device("sw1")}
        facts = {"sw999": HardwareFacts(vendor="Ghost")}
        enrich_devices_with_facts(devices, facts)
        assert devices["sw1"].hardware is None


class TestReverseDNS:
    @patch("adjacency.collectors.facts._reverse_lookup", new_callable=AsyncMock)
    async def test_enriches_dns_names(self, mock_lookup):
        mock_lookup.return_value = "switch-01.example.com"
        devices = {"sw1": _make_device("sw1", management_ip="10.0.0.1")}
        await enrich_devices_with_rdns(devices)
        assert "switch-01.example.com" in devices["sw1"].dns_names

    @patch("adjacency.collectors.facts._reverse_lookup", new_callable=AsyncMock)
    async def test_no_result_leaves_empty(self, mock_lookup):
        mock_lookup.return_value = None
        devices = {"sw1": _make_device("sw1", management_ip="10.0.0.1")}
        await enrich_devices_with_rdns(devices)
        assert devices["sw1"].dns_names == []

    @patch("adjacency.collectors.facts._reverse_lookup", new_callable=AsyncMock)
    async def test_multiple_ips_multiple_names(self, mock_lookup):
        def side_effect(ip):
            return {"10.0.0.1": "mgmt.example.com", "192.168.1.1": "data.example.com"}.get(ip)
        mock_lookup.side_effect = side_effect
        devices = {
            "sw1": _make_device("sw1", management_ip="10.0.0.1", ips={"192.168.1.1"}),
        }
        await enrich_devices_with_rdns(devices)
        assert "mgmt.example.com" in devices["sw1"].dns_names
        assert "data.example.com" in devices["sw1"].dns_names

    @patch("adjacency.collectors.facts._reverse_lookup", new_callable=AsyncMock)
    async def test_deduplicates_dns_names(self, mock_lookup):
        mock_lookup.return_value = "same.example.com"
        devices = {
            "sw1": _make_device("sw1", management_ip="10.0.0.1", ips={"10.0.0.2"}),
        }
        await enrich_devices_with_rdns(devices)
        assert devices["sw1"].dns_names.count("same.example.com") == 1
