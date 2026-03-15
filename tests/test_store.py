"""Tests for snapshot persistence."""

import json

from adjacency.models import AdjacencyTable, DataSource, Device, AdjacencyLink, NeighborRecord, LinkType
from adjacency.store import (
    SnapshotEnvelope,
    delete_snapshot,
    list_snapshots,
    load_snapshot,
    save_snapshot,
)


def _sample_table() -> AdjacencyTable:
    return AdjacencyTable(
        devices={
            "sw1": Device(hostname="sw1", platform="eos", management_ip="10.0.0.1"),
            "sw2": Device(hostname="sw2", platform="nxos", management_ip="10.0.0.2"),
        },
        links=[
            AdjacencyLink(
                local_device="sw1", local_interface="Ethernet1",
                remote_device="sw2", remote_interface="Ethernet1",
                sources=[DataSource.LLDP],
            ),
        ],
        raw_records=[
            NeighborRecord(
                local_device="sw1", local_interface="Ethernet1",
                remote_device="sw2", remote_interface="Ethernet1",
                source=DataSource.LLDP,
            ),
        ],
    )


class TestSaveAndLoad:
    def test_save_creates_file(self, tmp_path):
        table = _sample_table()
        path = save_snapshot(table, label="test-run", snapshot_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".json"
        # Validate envelope structure
        raw = json.loads(path.read_text())
        envelope = SnapshotEnvelope.model_validate(raw)
        assert envelope.meta.label == "test-run"
        assert envelope.meta.device_count == 2
        assert envelope.meta.link_count == 1

    def test_load_by_id(self, tmp_path):
        table = _sample_table()
        save_snapshot(table, label="mytest", snapshot_dir=tmp_path)
        meta, loaded = load_snapshot("mytest", tmp_path)
        assert meta.label == "mytest"
        assert len(loaded.devices) == 2
        assert len(loaded.links) == 1

    def test_load_by_label_substring(self, tmp_path):
        table = _sample_table()
        save_snapshot(table, label="prod-datacenter-east", snapshot_dir=tmp_path)
        meta, loaded = load_snapshot("datacenter", tmp_path)
        assert meta.label == "prod-datacenter-east"

    def test_roundtrip_preserves_data(self, tmp_path):
        table = _sample_table()
        save_snapshot(table, label="rt", snapshot_dir=tmp_path)
        _, loaded = load_snapshot("rt", tmp_path)
        assert loaded.devices["sw1"].platform == "eos"
        assert loaded.links[0].remote_device == "sw2"
        assert loaded.raw_records[0].source == DataSource.LLDP


class TestList:
    def test_list_empty(self, tmp_path):
        assert list_snapshots(tmp_path) == []

    def test_list_returns_sorted(self, tmp_path):
        table = _sample_table()
        save_snapshot(table, label="first", snapshot_dir=tmp_path)
        save_snapshot(table, label="second", snapshot_dir=tmp_path)
        metas = list_snapshots(tmp_path)
        assert len(metas) == 2
        # Newest first
        assert metas[0].label == "second"
        assert metas[1].label == "first"


class TestDelete:
    def test_delete_by_label(self, tmp_path):
        table = _sample_table()
        save_snapshot(table, label="to-delete", snapshot_dir=tmp_path)
        assert delete_snapshot("to-delete", tmp_path) is True
        assert list_snapshots(tmp_path) == []

    def test_delete_nonexistent(self, tmp_path):
        assert delete_snapshot("nope", tmp_path) is False
