# Lab topologies for adjacency testing

These [Containerlab](https://containerlab.dev/) topology files create
simulated networks to test adjacency discovery against real control planes.

## Prerequisites

```bash
# Install containerlab (Linux — runs the lab engine)
bash -c "$(curl -sL https://get.containerlab.dev)"

# macOS: containerlab requires a Linux host or VM.  Options:
#   - Rancher Desktop (recommended, already provides Docker)
#   - Docker Desktop with a containerlab wrapper
#   - A Linux VM via colima, UTM, or Parallels

# Pull a free network OS image (no license needed):
docker pull ghcr.io/nokia/srlinux:latest

# Or, with an Arista account, download cEOS:
#   https://www.arista.com/en/support/software-download
# Then: docker import cEOS64-lab-4.32.2F.tar ceos:4.32.2F
```

## Topologies

### small.clab.yml — 4-node leaf-spine

```
         ┌──────────┐     ┌──────────┐
         │ spine-01  │     │ spine-02  │
         └──┬────┬───┘     └───┬────┬──┘
            │    │             │    │
    ┌───────┘    └──────┬──────┘    └───────┐
    │                   │                   │
┌───┴──────┐      ┌────┴─────┐      ┌──────┴───┐
│ leaf-01   │      │ leaf-02   │      │ (future) │
└──────────┘      └──────────┘      └──────────┘
```

- 2 spines, 2 leaves
- Each leaf dual-homed to both spines
- LLDP enabled on all links
- LAG between leaf-01 and spine-01 (2 member links)

### medium.clab.yml — 3-tier with MLAG pair (planned)

Adds an access layer and an MLAG/vPC pair.

## Usage

```bash
# Deploy
cd lab/
sudo containerlab deploy -t small.clab.yml

# Verify
sudo containerlab inspect -t small.clab.yml

# Run adjacency in crawl mode against the lab
adjacency discover \
  --seed 172.20.20.2 --seed 172.20.20.3 \
  --depth 1 -c credentials.yaml

# Or inventory mode
adjacency -i lab-inventory/ discover

# Destroy
sudo containerlab destroy -t small.clab.yml
```

## Integration tests

```bash
# Full integration test (requires running lab)
pytest tests/integration/ -v --lab

# Skip integration tests (default in CI)
pytest tests/ -v  # integration tests auto-skip without --lab
```
