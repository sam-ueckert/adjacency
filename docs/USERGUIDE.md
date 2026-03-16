# Adjacency User Guide

Adjacency discovers, rationalizes, and visualizes network device
interconnections.  It collects L2/L3 forwarding data, LLDP/CDP neighbor
tables, ARP caches, MAC address tables, and hardware facts from live
devices, then produces a deduplicated adjacency graph that accounts for
LAG bundles, shared virtual addresses, and multi-homed connections.

---

## Table of Contents

1. [Installation](#1-installation)
2. [Concepts](#2-concepts)
3. [Quick Start](#3-quick-start)
4. [Discovery Modes](#4-discovery-modes)
   - [Inventory Mode](#41-inventory-mode)
   - [Crawl Mode](#42-crawl-mode)
5. [Credentials](#5-credentials)
6. [Snapshots](#6-snapshots)
7. [Visualization](#7-visualization)
8. [CLI Reference](#8-cli-reference)
9. [Data Model](#9-data-model)
10. [Rationalization Pipeline](#10-rationalization-pipeline)
11. [Virtual and Shared Address Handling](#11-virtual-and-shared-address-handling)
12. [Lab Simulation](#12-lab-simulation)
    - [Prerequisites](#121-prerequisites)
    - [Nokia SR Linux Lab (Free, No License)](#122-nokia-sr-linux-lab)
    - [Arista cEOS Lab](#123-arista-ceos-lab)
    - [Running Adjacency Against the Lab](#124-running-adjacency-against-the-lab)
    - [Running Integration Tests](#125-running-integration-tests)
13. [Testing](#13-testing)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Installation

```bash
git clone <repo-url> ~/repos/adjacency
cd ~/repos/adjacency
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Verify:

```bash
adjacency --version
adjacency --help
```

### System Requirements

- Python 3.11 or later
- SSH access to target network devices
- Devices must support NAPALM (Arista EOS, Cisco IOS/IOS-XE/NX-OS/IOS-XR,
  Juniper Junos, Nokia SR OS, Palo Alto PAN-OS, and others)

---

## 2. Concepts

**Discovery** connects to network devices and collects structured data from
multiple sources:

| Source | What it provides | NAPALM getter |
|--------|-----------------|---------------|
| LLDP | Neighbor hostname, interface, platform, chassis MAC | `get_lldp_neighbors_detail` |
| CDP | Neighbor hostname, IP, platform, interface | `cli("show cdp neighbors detail")` |
| MAC table | L2 forwarding: which MACs are reachable on which interface | `get_mac_address_table` |
| ARP table | L3 neighbor: IP-to-MAC mappings per interface | `get_arp_table` |
| Interfaces | Interface details, LAG membership, IP addresses, MACs | `get_interfaces`, `get_interfaces_ip` |
| Route table | Adjacent L3 next-hops on connected subnets | `get_route_to` |
| Facts | Vendor, model, serial, OS version, uptime, FQDN | `get_facts` |
| Reverse DNS | PTR records for management and interface IPs | `socket.gethostbyaddr` |

**Rationalization** merges these overlapping views into a single consistent
adjacency graph:

1. MACs and IPs are mapped back to known device hostnames
2. Records with only a MAC or IP (no hostname) are resolved via the identity index
3. Physical interfaces belonging to a LAG are collapsed under a single bundle link
4. Bidirectional observations (A sees B, B sees A) are deduplicated
5. Virtual/shared MACs (HSRP, VRRP, GLBP, MLAG) are excluded from identity resolution

**Snapshots** are the core output of the platform.  Each discovery run
produces a single snapshot — a self-contained JSON file that captures the
full topology: every device, every rationalized link, and every raw
observation.  Snapshots are what you load, visualize, query, and compare
over time.  See [Section 6](#6-snapshots) for the complete format reference
and an example from the included lab.

---

## 3. Quick Start

### Inventory mode (you know your devices)

```bash
# 1. Create an inventory
mkdir -p inventory
cp inventory/hosts.yaml.example inventory/hosts.yaml
# Edit inventory/hosts.yaml with your device IPs and platforms

# 2. Discover
adjacency discover

# 3. Visualize
adjacency visualize
open adjacency_20260315.html
```

### Crawl mode (let the tool find devices)

```bash
# 1. Create a credentials file
cp inventory/credentials.yaml.example credentials.yaml
# Edit with your credentials and network ranges

# 2. Discover from seed devices
adjacency discover \
  --seed 10.0.0.1 \
  --seed 10.0.0.2:eos \
  --depth 2 \
  --credentials credentials.yaml

# 3. Visualize
adjacency visualize
```

---

## 4. Discovery Modes

### 4.1 Inventory Mode

Uses a Nornir SimpleInventory directory containing `hosts.yaml` and
optionally `defaults.yaml`.  All listed hosts are contacted in parallel.

**`inventory/hosts.yaml`**

```yaml
spine-01:
  hostname: 10.0.0.1      # IP or resolvable hostname
  platform: eos            # NAPALM driver name
  groups: []
  data:
    role: spine

leaf-01:
  hostname: 10.0.1.1
  platform: nxos_ssh
  data:
    role: leaf
```

**`inventory/defaults.yaml`**

```yaml
username: admin
password: admin
```

**Run:**

```bash
adjacency -i inventory/ discover
```

Or, if there is an `inventory/` directory in the current working directory,
simply:

```bash
adjacency discover
```

**Available flags:**

| Flag | Effect |
|------|--------|
| `--no-l2` | Skip MAC address table collection |
| `--no-l3` | Skip ARP table collection |
| `--no-cdp` | Skip CDP collection |
| `--no-facts` | Skip hardware facts (`get_facts`) |
| `--no-rdns` | Skip reverse DNS lookups |
| `--no-save` | Do not auto-save a snapshot |
| `--label TEXT` | Tag the snapshot with a label |
| `--json` | Output raw JSON to stdout |
| `--raw` | Show unrationalized records in addition to results |

### 4.2 Crawl Mode

Activated by passing one or more `--seed` flags.  The crawler starts from
seed devices, collects LLDP/CDP neighbors, and iteratively probes newly
discovered devices outward to `--depth` hops.

**Seed format:**

```
--seed 10.0.0.1            # IP address, platform auto-detected
--seed 10.0.0.1:eos        # IP with explicit NAPALM platform
--seed spine-01.example.com # hostname (resolved via DNS)
--seed spine-01:junos       # hostname with platform
```

**Depth controls how far the crawler walks:**

| `--depth` | Behavior |
|-----------|----------|
| `0` | Collect data from seeds only |
| `1` | Seeds + their direct LLDP/CDP neighbors (default) |
| `2` | Seeds + neighbors + neighbors-of-neighbors |
| `N` | N hops from seeds |

**How the crawler finds next-hop targets:**

1. LLDP neighbors provide hostname and chassis MAC
2. CDP neighbors provide hostname and management IP directly
3. Hostnames are resolved to IPs via forward DNS
4. The credential store is consulted to find matching credentials for each IP
5. Platform is auto-detected from the LLDP/CDP `system_description` field

**Example:**

```bash
adjacency discover \
  --seed 10.0.0.1 \
  --seed 10.0.0.2:eos \
  --depth 3 \
  --credentials creds.yaml \
  --max-workers 20 \
  --timeout 60 \
  --label "dc1-full-crawl"
```

**Crawl-specific flags:**

| Flag | Default | Effect |
|------|---------|--------|
| `--seed` | | Seed device (repeatable) |
| `--credentials` / `-c` | | Credential YAML file |
| `--depth` / `-d` | `1` | Maximum crawl depth |
| `--max-workers` | `10` | Parallel device connections per depth level |
| `--timeout` | `30` | Per-device SSH connection timeout (seconds) |

---

## 5. Credentials

The credential file is a YAML list of entries.  Each entry has a username,
password, and optional network scope and platform hint.

**`credentials.yaml`**

```yaml
credentials:
  # Scoped: only tried for devices in these CIDR ranges
  - name: spine-arista
    username: admin
    password: s3cret
    platform: eos
    networks:
      - 10.0.0.0/24

  # Scoped with enable secret (for IOS devices)
  - name: access-ios
    username: netops
    password: acc3ss
    secret: enable_pass
    platform: ios
    networks:
      - 10.1.0.0/16

  # Wildcard: tried for any device not matched above
  - name: fallback
    username: admin
    password: admin
```

### Matching Logic

When connecting to a device at IP `10.0.0.5`:

1. **Scoped credentials** whose `networks` include `10.0.0.5` are tried first
   (in file order)
2. **Wildcard credentials** (no `networks` key) are tried second
3. Within each tier, credentials whose `platform` matches the detected
   platform are prioritized
4. If no `platform` is known (no LLDP hint, no credential platform), the
   crawler tries common NAPALM drivers in sequence: `eos`, `ios`, `nxos_ssh`,
   `junos`

### Platform Auto-Detection

When the crawler receives an LLDP or CDP neighbor advertisement, it parses
the `system_description` field to guess the NAPALM driver:

| Description contains | Detected driver |
|---------------------|----------------|
| "Arista" or "EOS" | `eos` |
| "Cisco NX-OS" or "Nexus" | `nxos_ssh` |
| "Cisco IOS-XR" | `iosxr` |
| "Cisco IOS-XE" or "Cisco IOS" | `ios` |
| "Juniper" or "JUNOS" | `junos` |
| "Palo Alto" or "PAN-OS" | `panos` |
| "Nokia SR OS" | `sros` |

### Security

- **Never commit `credentials.yaml` to version control.**  The `.gitignore`
  already excludes `credentials.yaml` and `inventory/credentials.yaml`.
- For production use, consider loading passwords from environment variables
  or a secrets manager and generating the credential file at runtime.

---

## 6. Snapshots

A snapshot is the primary output of every discovery run.  It is a single,
self-contained JSON file that captures the complete network topology at a
point in time: every discovered device, every rationalized adjacency link,
and every raw neighbor observation that contributed to those links.
Snapshots are what you load, visualize, compare, and share.

A complete example generated from the included lab topology is available at
[`docs/examples/lab-snapshot.json`](examples/lab-snapshot.json).

### 6.1 Lifecycle

Every `adjacency discover` run automatically saves a snapshot when it
finishes (disable with `--no-save`).

```
adjacency discover --label dc1-full     # saved as "dc1-full"
adjacency discover                       # saved with a random ID
adjacency discover --no-save             # not saved
```

Snapshots are stored in `~/.adjacency/snapshots/` by default.  Override
with `--snapshot-dir /path` or the `ADJACENCY_SNAPSHOT_DIR` environment
variable.

File names follow the pattern `{YYYYMMDDTHHmmss}_{id}.json`, so they sort
chronologically.  The ID is derived from `--label` (slugified) or a random
8-character hex string when no label is given.

### 6.2 File format

Each snapshot file is a JSON envelope with two top-level keys:

```json
{
  "meta": { ... },
  "data": { ... }
}
```

**`meta`** — summary metadata for fast listing without parsing the full file:

| Field | Description |
|-------|-------------|
| `snapshot_id` | Unique identifier (from `--label` or auto-generated) |
| `created_at` | ISO-8601 UTC timestamp |
| `adjacency_version` | Version of adjacency that created this snapshot |
| `label` | Human-readable label (empty if none provided) |
| `device_count` | Number of discovered devices |
| `link_count` | Number of rationalized adjacency links |
| `raw_record_count` | Number of raw neighbor observations |

**`data`** — the full `AdjacencyTable`, containing three sections:

#### `data.links` — the adjacency relationships

This is the primary content of the snapshot: the rationalized list of
device-to-device connections that the platform discovered.  Each link names
the two endpoints (device and interface on each side), the link type, and
which data sources provided evidence for it:

```json
{
  "local_device": "leaf-01",
  "local_interface": "ethernet-1/3",
  "remote_device": "spine-02",
  "remote_interface": "ethernet-1/1",
  "link_type": "physical",
  "sources": ["lldp", "route_table"],
  "remote_mac": "aa:c1:ab:00:02:01",
  "remote_ip": "10.1.3.1"
}
```

This entry says: leaf-01's ethernet-1/3 is directly connected to
spine-02's ethernet-1/1, and the connection was independently confirmed by
both LLDP and a routing table next-hop.  The `sources` array is the audit
trail — it tells you which collection methods contributed evidence for this
specific adjacency.

**LAG bundles** are represented as a single link with `link_type: "lag"`
and a `members` array containing the constituent physical links:

```json
{
  "local_device": "leaf-01",
  "local_interface": "lag1",
  "remote_device": "spine-01",
  "remote_interface": "lag1",
  "link_type": "lag",
  "sources": ["lldp"],
  "members": [
    {
      "local_device": "leaf-01",
      "local_interface": "ethernet-1/1",
      "remote_device": "spine-01",
      "remote_interface": "ethernet-1/1",
      "link_type": "physical",
      "sources": ["lldp"],
      "remote_mac": "aa:c1:ab:00:01:01",
      "remote_ip": "10.1.1.1"
    },
    {
      "local_device": "leaf-01",
      "local_interface": "ethernet-1/2",
      "remote_device": "spine-01",
      "remote_interface": "ethernet-1/2",
      "link_type": "physical",
      "sources": ["lldp"],
      "remote_mac": "aa:c1:ab:00:01:02",
      "remote_ip": "10.1.1.5"
    }
  ]
}
```

The rationalization engine detects that ethernet-1/1 and ethernet-1/2 are
both LAG members (via the `lag_parent` field on each interface) pointing at
the same remote device, and collapses them into a single LAG link.  The
physical member links are preserved inside `members` for reference.

#### `data.devices` — the device inventory

A dictionary keyed by hostname.  Each device includes management IP,
platform, vendor, hardware facts, serial number, and a full interface
inventory with MAC addresses, IP addresses, LAG membership, and link speed:

```json
"leaf-01": {
  "hostname": "leaf-01",
  "platform": "srlinux",
  "management_ip": "172.20.20.4",
  "vendor": "Nokia",
  "model": "7220 IXR-D3",
  "hardware": {
    "serial_number": "Sim-LF01",
    "os_version": "24.10.1",
    "fqdn": "leaf-01.lab"
  },
  "interfaces": {
    "ethernet-1/1": {
      "name": "ethernet-1/1",
      "mac_address": "aa:c1:ab:00:03:01",
      "ip_addresses": ["10.1.1.2"],
      "speed_mbps": 25000,
      "is_up": true,
      "lag_parent": "lag1"
    }
  },
  "known_macs": ["aa:c1:ab:00:03:01", "..."],
  "known_ips": ["10.1.1.2", "172.20.20.4", "..."]
}
```

The `known_macs` and `known_ips` sets are used during rationalization to
resolve raw records that only carry a MAC or IP back to the device that
owns them.  The `interfaces` dict provides the per-interface detail that
powers LAG detection and subnet-based route filtering.

#### `data.raw_records` — the pre-rationalization evidence

Every individual neighbor observation collected from the network, before
any merging or deduplication.  Each record is tagged with its `source` —
the collection method that produced it (`lldp`, `cdp`, `mac_table`,
`arp_table`, `route_table`).

Raw records are the input to the rationalization pipeline.  They are kept
in the snapshot for audit and debugging so you can trace exactly how each
link in `data.links` was derived.

An LLDP record identifies the remote end by hostname and interface:

```json
{
  "local_device": "spine-01",
  "local_interface": "ethernet-1/3",
  "remote_device": "leaf-02",
  "remote_interface": "ethernet-1/1",
  "remote_mac": "aa:c1:ab:00:04:01",
  "source": "lldp"
}
```

A route table record only carries a next-hop IP — no hostname or interface:

```json
{
  "local_device": "spine-01",
  "local_interface": "ethernet-1/3",
  "remote_device": null,
  "remote_ip": "10.1.2.2",
  "source": "route_table"
}
```

During rationalization, the IP `10.1.2.2` is looked up in the identity
index (built from `known_ips` across all devices), resolved to leaf-02, and
the record merges with the LLDP evidence for the same link.  The final
link in `data.links` then carries `"sources": ["lldp", "route_table"]`,
showing that two independent methods confirmed the adjacency.

### 6.3 Managing snapshots

**List:**

```bash
adjacency snapshot list
```

```
┌──────────┬─────────────────────┬───────────┬─────────┬───────┬─────────┐
│ ID       │ Created             │ Label     │ Devices │ Links │ Records │
├──────────┼─────────────────────┼───────────┼─────────┼───────┼─────────┤
│ dc1-full │ 2026-03-15 14:30:00 │ dc1-full  │      42 │    87 │     312 │
│ a1b2c3d4 │ 2026-03-15 10:00:00 │ -         │       8 │    14 │      56 │
└──────────┴─────────────────────┴───────────┴─────────┴───────┴─────────┘
```

**Load and display:**

```bash
adjacency snapshot load dc1              # by label (substring match)
adjacency snapshot load a1b2c3d4         # by ID
adjacency snapshot load dc1 --raw        # include raw records
adjacency snapshot load dc1 --json > export.json
```

**Delete:**

```bash
adjacency snapshot delete a1b2c3d4
adjacency snapshot delete dc1 --yes      # skip confirmation
```

### 6.4 Working with snapshot data

Load any snapshot JSON file directly — it does not need to live in the
snapshot directory:

```bash
adjacency show docs/examples/lab-snapshot.json
adjacency show docs/examples/lab-snapshot.json --json | jq '.devices | keys'
```

Visualize from a saved snapshot by label or ID:

```bash
adjacency visualize lab
adjacency visualize dc1-full -o dc1.html
```

Query snapshot files with standard JSON tools:

```bash
# List all device hostnames
jq '.data.devices | keys' snapshot.json

# Count links by type
jq '[.data.links[].link_type] | group_by(.) | map({(.[0]): length}) | add' snapshot.json

# Show all data sources that contributed evidence
jq '[.data.raw_records[].source] | unique' snapshot.json
```

---

## 7. Visualization

### Interactive HTML (Cytoscape.js)

```bash
# From the latest snapshot
adjacency visualize

# From a specific snapshot
adjacency visualize dc1-full

# From a JSON file
adjacency visualize export.json

# Custom output path and title
adjacency visualize -o topology.html -t "DC1 Fabric"
```

Opens a self-contained HTML page with:

- **Force-directed layout** (or switch to grid / circle / hierarchical via dropdown)
- **Nodes** color-coded by platform (Arista = blue, Cisco NX-OS = amber,
  Juniper = red, etc.)
- **Click a node** to see: hostname, platform, vendor, hardware model, OS
  version, serial number, management IP, DNS names, connected links
- **Click an edge** to see: local/remote interfaces, link type, evidence
  sources, and LAG member list
- **LAG edges** rendered thicker and blue with member count
- **Edge label toggle** and **fit-to-view** controls
- **Platform legend** in the corner

The HTML loads Cytoscape.js from a CDN and requires internet access to render.
All topology data is embedded in the file itself.

### GraphViz DOT

```bash
adjacency visualize --format dot
adjacency visualize --format dot -o fabric.dot
```

Render to image:

```bash
# Force-directed (best for mesh topologies)
neato -Tpng fabric.dot -o fabric.png

# Hierarchical (best for tree topologies)
dot -Tpng fabric.dot -o fabric.png

# SVG for web embedding
neato -Tsvg fabric.dot -o fabric.svg
```

DOT output features:
- Nodes grouped into colored `subgraph cluster_*` blocks by platform
- Node labels include hostname, management IP, hardware model, and DNS name
- LAG edges rendered bold with penwidth proportional to member count
- Logical links rendered dashed

---

## 8. CLI Reference

```
adjacency [OPTIONS] COMMAND [ARGS]

Global Options:
  --version                  Show version
  -i, --inventory DIRECTORY  Nornir inventory directory
  --snapshot-dir DIRECTORY   Override snapshot storage path
  -v, --verbose              Enable debug logging
  --help                     Show help

Commands:
  discover    Discover adjacencies from network devices
  show        Display a saved adjacency JSON file
  snapshot    Manage saved snapshots (list / load / delete)
  visualize   Generate topology visualization (HTML or DOT)
```

### discover

```
adjacency discover [OPTIONS]

Crawl Mode:
  -s, --seed TEXT          Seed device (IP or host[:platform]), repeatable
  -c, --credentials FILE   Credential YAML file
  -d, --depth INTEGER      Crawl depth (default: 1)
  --timeout INTEGER        Connection timeout in seconds (default: 30)
  --max-workers INTEGER    Parallel connections (default: 10)

Collection Control:
  --no-l2                  Skip MAC table collection
  --no-l3                  Skip ARP table collection
  --no-cdp                 Skip CDP collection
  --no-facts               Skip hardware facts collection
  --no-rdns                Skip reverse DNS lookups

Output:
  --no-save                Do not auto-save snapshot
  -l, --label TEXT         Snapshot label
  --json                   Output JSON to stdout
  --raw                    Include raw (unrationalized) records
```

### show

```
adjacency show FILE [--raw]
```

Displays a JSON file (either a raw AdjacencyTable or a snapshot envelope).

### snapshot

```
adjacency snapshot list
adjacency snapshot load IDENTIFIER [--raw] [--json]
adjacency snapshot delete IDENTIFIER [--yes]
```

### visualize

```
adjacency visualize [SOURCE] [OPTIONS]

  SOURCE: snapshot ID/label, JSON file, or omit for latest snapshot

  -f, --format [html|dot]  Output format (default: html)
  -o, --output PATH        Output file path
  -t, --title TEXT         HTML page title
```

---

## 9. Data Model

### Device

Each discovered device carries:

| Field | Type | Description |
|-------|------|-------------|
| `hostname` | `str` | Device hostname (from LLDP, facts, or inventory) |
| `platform` | `str?` | NAPALM driver name (eos, ios, nxos_ssh, ...) |
| `management_ip` | `str?` | Primary management IP |
| `vendor` | `str?` | Vendor name (Arista, Cisco, Juniper, ...) |
| `model` | `str?` | Device model |
| `os_version` | `str?` | Operating system version |
| `serial` | `str?` | Serial number |
| `dns_names` | `list[str]` | Reverse DNS PTR records |
| `hardware` | `HardwareFacts?` | Structured hardware/software facts |
| `interfaces` | `dict` | Interface name -> InterfaceInfo |
| `known_macs` | `set[str]` | Identity MACs (safe for device resolution) |
| `known_ips` | `set[str]` | Identity IPs |
| `shared_macs` | `set[str]` | Virtual/shared MACs (excluded from resolution) |
| `shared_ips` | `set[str]` | Virtual/shared IPs (excluded from resolution) |

### AdjacencyLink

| Field | Type | Description |
|-------|------|-------------|
| `local_device` | `str` | Local device hostname |
| `local_interface` | `str` | Local interface name |
| `remote_device` | `str` | Remote device hostname |
| `remote_interface` | `str?` | Remote interface name (if known) |
| `link_type` | `LinkType` | `physical`, `lag`, or `logical` |
| `sources` | `list[DataSource]` | Evidence sources (lldp, cdp, mac_table, arp_table) |
| `members` | `list[AdjacencyLink]` | LAG member links (only when link_type=lag) |

---

## 10. Rationalization Pipeline

Discovery produces raw `NeighborRecord` objects from multiple sources.  The
rationalization engine transforms these into a clean `AdjacencyTable` in five
stages:

### Stage 1: Identity Index

Build two lookup maps:

- **MAC -> hostname**: every unicast, non-virtual MAC from device interfaces
- **IP -> hostname**: every interface IP and management IP

**Exclusions:**
- MACs matching known virtual patterns (HSRP, VRRP, GLBP, multicast)
- MACs with the multicast bit set (LSB of first octet)
- MACs or IPs claimed by more than one device (MLAG system MAC, FHRP VIP)
- MACs/IPs explicitly flagged in `Device.shared_macs` / `shared_ips`

### Stage 2: Resolve Records

For records that have a MAC or IP but no `remote_device` (e.g., MAC table
entries), look up the MAC/IP in the identity index to determine which device
it belongs to.  Records whose only identifier is a shared address are left
unresolved rather than misattributed.

### Stage 3: Build Links

Group resolved records by `(local_device, local_interface, remote_device)`.
For each group, pick the best remote interface (prefer LLDP/CDP over MAC/ARP),
collect all evidence sources, and create an `AdjacencyLink`.

### Stage 4: Collapse LAG Bundles

For each device, check which physical interfaces are members of a LAG
(port-channel, ae, bond, etc.).  Group links whose local interface is a LAG
member into a single LAG-type `AdjacencyLink` with the member links nested
inside.  Attempt to infer the remote LAG name from the remote device's
interface data.

### Stage 5: Deduplicate

Canonicalize link keys so that (A:eth1 -> B:eth1) and (B:eth1 -> A:eth1) are
treated as the same link.  Merge evidence sources and supplementary data.

---

## 11. Virtual and Shared Address Handling

The following MAC address patterns are recognized as virtual or protocol
addresses and are **excluded from device identity resolution**:

| Pattern | Description |
|---------|-------------|
| `00:00:0c:07:ac:xx` | HSRP v1 virtual MAC (Cisco) |
| `00:00:0c:9f:fX:xx` | HSRP v2 virtual MAC (Cisco) |
| `00:00:5e:00:01:xx` | VRRP virtual MAC, IPv4 (RFC 5798) |
| `00:00:5e:00:02:xx` | VRRP virtual MAC, IPv6 |
| `00:07:b4:00:xx` | GLBP virtual MAC (Cisco) |
| `00:00:0c:9f:xx` | Cisco vPC / VSS virtual system MAC |
| `01:80:c2:00:00:0e` | LLDP multicast |
| `01:80:c2:00:00:02` | LACP multicast |
| `01:00:0c:cc:cc:cc` | CDP/VTP/DTP multicast |
| Any `01:xx:xx:xx:xx:xx` | IEEE multicast (bit check) |

Additionally, any MAC or IP that appears in the `known_macs` or `known_ips`
of **more than one device** is automatically treated as shared.  This catches
MLAG system MACs, anycast gateway IPs, and FHRP VIPs that are not covered by
the pattern list.

The pattern registry is in `src/adjacency/virtual_macs.py` and can be
extended by adding `VirtualMacPattern` entries to the `VIRTUAL_MAC_PATTERNS`
list.

---

## 12. Lab Simulation

Adjacency ships with Containerlab topology files for testing against
simulated networks with real control planes.  The lab devices run actual
network operating systems in Docker containers, respond to SSH, generate
LLDP traffic, and populate MAC/ARP tables -- exactly like production gear.

### 12.1 Prerequisites

**Docker** must be available.  If you are running Rancher Desktop on macOS,
Docker is already provided.

**Containerlab** runs the lab engine.  It requires a Linux environment.
On macOS with Rancher Desktop:

```bash
# Option A: Run containerlab as a Docker container (recommended on macOS)
# This uses the containerlab Docker image which bundles the clab binary.
# The /var/run/docker.sock mount lets it manage sibling containers.

alias clab='docker run --rm -it --privileged \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/run/docker/netns:/var/run/docker/netns \
  -v $(pwd):/work \
  --workdir /work \
  ghcr.io/srl-labs/clab clab'
```

```bash
# Option B: Install natively inside the Rancher VM
rdctl shell                                   # enter the VM
curl -sL https://get.containerlab.dev | sudo bash
exit
```

**Network OS Images** -- choose one or both:

| Image | License | Pull command |
|-------|---------|-------------|
| Nokia SR Linux | Free, no registration | `docker pull ghcr.io/nokia/srlinux:latest` |
| Arista cEOS | Free, requires [arista.com](https://www.arista.com/en/support/software-download) account | Download `cEOS64-lab-<version>.tar`, then `docker import cEOS64-lab-4.32.2F.tar ceos:4.32.2F` |

SR Linux is the zero-friction option.  cEOS provides the most realistic
Arista EOS experience and has mature NAPALM support.

### 12.2 Nokia SR Linux Lab

```bash
cd ~/repos/adjacency/lab

# Pull the image (one-time)
docker pull ghcr.io/nokia/srlinux:latest

# Deploy the 4-node topology
clab deploy -t small.clab.yml

# Verify all containers are running
clab inspect -t small.clab.yml
```

Expected output:

```
+---+-----------------------+---------+------+---------+----------+
| # | Name                  | Kind    | IPv4 | IPv6    | State    |
+---+-----------------------+---------+------+---------+----------+
| 1 | clab-adj-small-leaf-01  | srlinux | ...  | ...     | running  |
| 2 | clab-adj-small-leaf-02  | srlinux | ...  | ...     | running  |
| 3 | clab-adj-small-spine-01 | srlinux | ...  | ...     | running  |
| 4 | clab-adj-small-spine-02 | srlinux | ...  | ...     | running  |
+---+-----------------------+---------+------+---------+----------+
```

Note the IPv4 management addresses -- you will need them for the seed or
inventory configuration.

**Topology:**

```
         ┌──────────┐     ┌──────────┐
         │ spine-01  │     │ spine-02  │
         └──┬────┬───┘     └───┬────┬──┘
   LAG(2)   │    │             │    │
    ┌───────┘    └──────┬──────┘    └───────┐
    │                   │                   │
┌───┴──────┐      ┌────┴─────┐      (peer link)
│ leaf-01   ├──────┤ leaf-02   │
└──────────┘      └──────────┘
```

- 2 spines, 2 leaves
- leaf-01 to spine-01: 2-member LAG (e1-1, e1-2)
- All other inter-switch links: single physical
- leaf-01 to leaf-02: peer link (e1-4 / e1-3)
- LLDP enabled on all links

**Destroy when done:**

```bash
clab destroy -t small.clab.yml
```

### 12.3 Arista cEOS Lab

```bash
cd ~/repos/adjacency/lab

# Import the cEOS image (one-time, after downloading from arista.com)
docker import cEOS64-lab-4.32.2F.tar ceos:4.32.2F

# Deploy
clab deploy -t small-ceos.clab.yml

# Verify
clab inspect -t small-ceos.clab.yml
```

The cEOS variant adds:
- leaf-01 has a **Port-Channel1** (LACP) bundling Ethernet1 and Ethernet2
  toward spine-01, demonstrating LAG detection and collapse
- EOS eAPI enabled for potential future REST-based collection
- Realistic Arista CLI configuration

### 12.4 Running Adjacency Against the Lab

**Step 1: Get the management IPs**

```bash
clab inspect -t small-ceos.clab.yml
```

Note the IPv4 addresses in the output (typically `172.20.20.x`).

**Step 2a: Crawl mode (recommended for testing the crawler)**

```bash
# Use one spine as the seed; the crawler will find the rest
adjacency discover \
  --seed 172.20.20.2:eos \
  --depth 2 \
  --credentials lab/credentials.yaml \
  --label "lab-crawl"
```

**Step 2b: Inventory mode**

Edit `lab/lab-inventory/hosts.yaml` with the actual management IPs from
Step 1, then:

```bash
adjacency -i lab/lab-inventory discover --label "lab-inventory"
```

**Step 3: Visualize**

```bash
adjacency visualize -t "Lab Topology"
open adjacency_20260315.html
```

**Step 4: Inspect the snapshot**

```bash
adjacency snapshot list
adjacency snapshot load lab-crawl
adjacency snapshot load lab-crawl --json | python3 -m json.tool | head -50
```

### 12.5 Running Integration Tests

The integration test suite runs the full discovery pipeline against a live
lab topology.

```bash
# With a running lab
pytest tests/integration/ -v --lab

# Without a lab (integration tests are skipped automatically)
pytest tests/ -v
```

The integration tests verify:
- All 4 devices are discovered in inventory mode
- Crawl from a single seed finds at least 2 devices at depth 1
- LLDP records are present in raw data
- Hardware facts are populated
- HTML and DOT visualizations contain expected device names
- Snapshot save/reload round-trips correctly

---

## 13. Testing

### Run the full unit test suite

```bash
pytest tests/ -v
```

This runs 104 unit tests and 9 simulated network tests (all without network
access) and skips the 11 integration tests.

### Test organization

| File | Tests | What it covers |
|------|-------|---------------|
| `test_models.py` | 6 | Device merge, NeighborRecord properties |
| `test_rationalize.py` | 7 | Identity resolution, LLDP links, dedup, LAG collapse |
| `test_virtual_macs.py` | 16 | Virtual MAC detection, shared address exclusion |
| `test_credentials.py` | 15 | CIDR matching, credential ordering, platform detection |
| `test_crawl.py` | 15 | IP validation, next-hop extraction, mock device collection |
| `test_facts.py` | 7 | Hardware facts merge, reverse DNS enrichment |
| `test_store.py` | 8 | Snapshot save/load/list/delete round-trip |
| `test_visualize.py` | 12 | HTML generation, DOT generation, platform colors |
| `test_simulated_network.py` | 9 | Full end-to-end crawl pipeline against 4-node mock fabric |
| `integration/test_lab_discovery.py` | 11 | Live lab (requires `--lab` flag + running containerlab) |

### Simulated network tests

The simulated network test (`test_simulated_network.py`) defines a complete
4-node leaf-spine fabric as Python dictionaries that mock NAPALM driver
responses.  It exercises the full crawl pipeline -- credential matching,
connection handling, LLDP neighbor extraction, multi-hop crawling, and
rationalization -- without any network access or Docker.

```bash
# Run just the simulated network tests
pytest tests/test_simulated_network.py -v
```

### Code quality

```bash
ruff check src/ tests/
```

---

## 14. Troubleshooting

### "No --seed specified and no inventory directory found"

Either provide seeds for crawl mode (`--seed 10.0.0.1`) or ensure an
`inventory/` directory with `hosts.yaml` exists in the working directory, or
specify one with `-i /path/to/inventory`.

### Discovery finds 0 devices

- Verify SSH connectivity: `ssh admin@10.0.0.1`
- Check that the NAPALM platform is correct: `eos`, `ios`, `nxos_ssh`,
  `junos`, etc.
- Enable verbose logging: `adjacency -v discover ...`
- For crawl mode, ensure the credential file has matching entries

### LLDP neighbors missing

- Verify LLDP is enabled on the device and its interfaces
- Some platforms require `lldp run` globally and `lldp transmit` /
  `lldp receive` per interface
- LLDP data may take 30-60 seconds to populate after link up

### LAG members not detected

LAG detection relies on NAPALM's interface data reporting `lag_parent`
membership.  If your platform/driver does not populate this field, LAG
collapse will not trigger.  The interface name pattern
(`port-channel`, `ae`, `bond`, `po`, `bundle-ether`) is also checked.

### Virtual MACs not recognized

Add new patterns to `VIRTUAL_MAC_PATTERNS` in
`src/adjacency/virtual_macs.py`:

```python
VirtualMacPattern("aabb00", 6, "My custom virtual MAC", "vendor")
```

### Crawl stops at depth 0

The crawler needs management IPs or resolvable hostnames to reach the next
hop.  If LLDP neighbors only report a hostname and that hostname does not
resolve via DNS, the hop is skipped.  CDP is more reliable here because it
includes the management IP directly.  Ensure DNS is configured or use
`--depth 0` with multiple explicit seeds.

### HTML visualization blank

The HTML page loads Cytoscape.js from `https://unpkg.com`.  If you are
offline or behind a firewall that blocks this URL, the page will not render.
Use DOT format as an offline alternative.

### Containerlab fails on macOS

Containerlab requires a Linux kernel for network namespaces.  On macOS, run
it inside the Docker/Rancher VM:

```bash
# Using the containerlab Docker image
docker run --rm -it --privileged \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/run/docker/netns:/var/run/docker/netns \
  -v $(pwd):/work \
  --workdir /work \
  ghcr.io/srl-labs/clab clab deploy -t /work/lab/small.clab.yml
```

Or shell into the Rancher VM (`rdctl shell`) and run `containerlab` natively.
