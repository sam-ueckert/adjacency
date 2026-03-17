# Lab Environment

These [Containerlab](https://containerlab.dev/) topology files create
simulated networks to test adjacency discovery against real control planes.

## Tools Overview

### Containerlab

Containerlab is an orchestration tool that deploys network lab topologies
using containers. It manages container lifecycle, inter-node wiring, and
startup configs — all defined in a single YAML file.

- Site: https://containerlab.dev/
- Runs on Linux (Docker required)
- macOS requires a Linux VM (see below)

### Network OS Images

Containerlab supports many vendor network operating systems as container
images. The three most common for lab use:

| Image | Vendor | Platform | License | Containerlab `kind` |
|-------|--------|----------|---------|---------------------|
| **SR Linux** | Nokia | srlinux | Free, no account needed | `nokia_srlinux` |
| **cEOS** | Arista | eos | Free with Arista account | `ceos` |
| **cXRd** | Cisco | iosxr | Requires Cisco entitlement | `cisco_xrd` |

**SR Linux** is the easiest to get started with — no account or license
required. **cEOS** (containerized EOS) is Arista's switching OS in a
container. **cXRd** (containerized IOS XR) is Cisco's routing-focused
equivalent; it runs natively in Docker but requires an image download from
Cisco.

Cisco also offers VM-based options (**Nexus 9000v** for NX-OS, **CSR 1000v /
Catalyst 8000v** for IOS XE) that can integrate with Containerlab via
[vrnetlab](https://containerlab.dev/manual/vrnetlab/), but these are heavier
and slower to boot than native container images.

## Setup

### Install Containerlab

```bash
# Linux
bash -c "$(curl -sL https://get.containerlab.dev)"

# macOS: containerlab requires a Linux host or VM.  Options:
#   - Rancher Desktop (recommended, already provides Docker)
#   - Docker Desktop with a containerlab wrapper
#   - A Linux VM via colima, UTM, or Parallels
```

### Pull / Import Network OS Images

```bash
# Nokia SR Linux (free, no license needed)
docker pull ghcr.io/nokia/srlinux:latest

# Arista cEOS (free with Arista account)
#   1. Download from https://www.arista.com/en/support/software-download
#   2. Import:
docker import cEOS64-lab-4.32.2F.tar ceos:4.32.2F

# Cisco cXRd (requires Cisco entitlement)
#   1. Download from https://software.cisco.com
#   2. Load:
docker load -i xrd-control-plane-container-x64.dockerv1.tgz
```

## Topologies

### small.clab.yml — 4-node leaf-spine (SR Linux)

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
- Peer link between leaf-01 and leaf-02

### small-ceos.clab.yml — 4-node leaf-spine (Arista cEOS)

Same topology as above using Arista cEOS images. Requires the cEOS Docker
image to be imported first.

### medium.clab.yml — 10-node campus + DC fabric (SR Linux)

```
                  ┌──────────────┐
                  │ internet-rtr │  (linux — not crawlable)
                  └──────┬───────┘
                         │
                  ┌──────┴───────┐
                  │  border-01   │  DC border/edge
                  └──┬───────┬───┘
                     │       │
              ┌──────┴──┐ ┌──┴──────┐
              │spine-01 │ │ spine-02│  DC spine layer
              └──┬──┬───┘ └───┬──┬──┘
                 │  │         │  │
           ┌─────┘  └────┬────┘  └─────┐
           │             │             │
      ┌────┴────┐   peer link    ┌─────┴───┐
      │ leaf-01 │◄──────────────►│ leaf-02  │  DC leaf layer
      └─────────┘                └──┬──┬────┘
                                  LAG│  │LAG
                              ┌─────┴──┴────┐
                              │ campus-core  │  Campus core
                              └──┬───────┬──┘
                                 │       │
                          ┌──────┴──┐ ┌──┴──────┐
                          │ dist-01 │ │ dist-02  │  Campus distribution
                          └────┬────┘ └────┬─────┘
                               │           │
                        ┌──────┴───┐ ┌─────┴──────┐
                        │access-01 │ │ access-02  │  Campus access
                        └──────────┘ └────────────┘
```

- 10 network devices across DC fabric and campus tiers
- `internet-rtr` is a plain Linux container (no LLDP/SNMP) — simulates an
  uncrawlable upstream ISP with a default route from `border-01`
- LAG between leaf-02 and campus-core (2 member links)
- Peer link between leaf-01 and leaf-02
- Full LLDP on all managed links

### medium-ceos.clab.yml — 10-node campus + DC fabric (Arista cEOS)

Same topology as above using Arista cEOS images. Includes Port-Channel
configs for the LAG between leaf-02 and campus-core. Requires the cEOS
Docker image to be imported first.

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

## Integration Tests

```bash
# Full integration test (requires running lab)
pytest tests/integration/ -v --lab

# Skip integration tests (default in CI)
pytest tests/ -v  # integration tests auto-skip without --lab
```
