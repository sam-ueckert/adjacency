"""Snapshot persistence — save, list, load, and delete adjacency snapshots."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from adjacency import __version__
from adjacency.models import AdjacencyTable


class SnapshotMeta(BaseModel):
    """Metadata envelope for a saved adjacency snapshot."""

    snapshot_id: str
    created_at: str  # ISO-8601 UTC
    adjacency_version: str = __version__
    inventory_path: str = ""
    label: str = ""
    device_count: int = 0
    link_count: int = 0
    raw_record_count: int = 0


class SnapshotEnvelope(BaseModel):
    """On-disk JSON format: metadata + serialised AdjacencyTable."""

    meta: SnapshotMeta
    data: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:40]


def get_snapshot_dir(base: Path | None = None) -> Path:
    """Return the snapshot storage directory, creating it if needed."""
    if base:
        d = base
    else:
        d = Path(os.environ.get("ADJACENCY_SNAPSHOT_DIR", "~/.adjacency/snapshots"))
    d = d.expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_snapshot(
    table: AdjacencyTable,
    inventory_path: Path | None = None,
    label: str = "",
    snapshot_dir: Path | None = None,
) -> Path:
    """Serialise an AdjacencyTable to a snapshot JSON file.  Returns the path."""
    now = datetime.now(timezone.utc)
    sid = _slugify(label) if label else uuid4().hex[:8]
    ts = now.strftime("%Y%m%dT%H%M%S")
    filename = f"{ts}_{sid}.json"

    meta = SnapshotMeta(
        snapshot_id=sid,
        created_at=now.isoformat(),
        inventory_path=str(inventory_path or ""),
        label=label,
        device_count=len(table.devices),
        link_count=len(table.links),
        raw_record_count=len(table.raw_records),
    )
    envelope = SnapshotEnvelope(
        meta=meta,
        data=table.model_dump(mode="json"),
    )

    dest = get_snapshot_dir(snapshot_dir) / filename
    dest.write_text(json.dumps(envelope.model_dump(mode="json"), indent=2) + "\n")
    return dest


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def list_snapshots(snapshot_dir: Path | None = None) -> list[SnapshotMeta]:
    """Return metadata for all snapshots, sorted newest first.

    Only parses the ``meta`` key from each file for speed.
    """
    d = get_snapshot_dir(snapshot_dir)
    metas: list[SnapshotMeta] = []
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            raw = json.loads(f.read_text())
            metas.append(SnapshotMeta.model_validate(raw["meta"]))
        except (json.JSONDecodeError, KeyError):
            continue
    return metas


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_snapshot(
    identifier: str,
    snapshot_dir: Path | None = None,
) -> tuple[SnapshotMeta, AdjacencyTable]:
    """Load a snapshot by ID, label, or filename.

    Search order:
      1. Exact filename match
      2. snapshot_id match
      3. label (case-insensitive substring) match
    """
    d = get_snapshot_dir(snapshot_dir)

    # 1. Exact filename
    exact = d / identifier
    if not exact.suffix:
        exact = d / (identifier + ".json")
    if exact.exists():
        return _load_file(exact)

    # 2/3. Search all files
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            raw = json.loads(f.read_text())
            meta = SnapshotMeta.model_validate(raw["meta"])
        except (json.JSONDecodeError, KeyError):
            continue

        if meta.snapshot_id == identifier:
            return meta, AdjacencyTable.model_validate(raw["data"])
        if identifier.lower() in meta.label.lower():
            return meta, AdjacencyTable.model_validate(raw["data"])

    raise FileNotFoundError(f"No snapshot matching '{identifier}' found in {d}")


def _load_file(path: Path) -> tuple[SnapshotMeta, AdjacencyTable]:
    raw = json.loads(path.read_text())
    meta = SnapshotMeta.model_validate(raw["meta"])
    table = AdjacencyTable.model_validate(raw["data"])
    return meta, table


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_snapshot(
    identifier: str,
    snapshot_dir: Path | None = None,
) -> bool:
    """Delete a snapshot by ID, label, or filename.  Returns True if deleted."""
    d = get_snapshot_dir(snapshot_dir)

    # Find the file first
    exact = d / identifier
    if not exact.suffix:
        exact = d / (identifier + ".json")
    if exact.exists():
        exact.unlink()
        return True

    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            raw = json.loads(f.read_text())
            meta = SnapshotMeta.model_validate(raw["meta"])
        except (json.JSONDecodeError, KeyError):
            continue

        if meta.snapshot_id == identifier or identifier.lower() in meta.label.lower():
            f.unlink()
            return True

    return False
