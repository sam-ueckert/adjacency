# adjacency

Intelligent network device adjacency mapping. Collects L2/L3 forwarding data,
LLDP/CDP neighbor information, and interface details from network devices, then
rationalizes the results into a deduplicated adjacency graph that accounts for
link bundles, multi-homed connections, and overlapping MAC/IP identities.

## Architecture

**Collection** - Uses [Nornir](https://nornir.readthedocs.io/) with the
[NAPALM](https://napalm.readthedocs.io/) plugin to pull structured data from
devices:

- LLDP / CDP neighbor tables
- MAC address (L2 forwarding) tables
- ARP / neighbor (L3) tables
- Interface and LAG membership details

**Modeling** - Pydantic models represent devices, interfaces, and adjacency
links with full provenance tracking (which source reported each fact).

**Rationalization** - A multi-pass engine that:

1. Merges LLDP/CDP, MAC, and ARP views of the same neighbor
2. Detects LAG / port-channel bundles and nests physical members
3. Deduplicates devices that appear under multiple IPs or MACs

**Output** - Rich terminal tables and optional JSON/YAML export.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# configure inventory
cp inventory/hosts.yaml.example inventory/hosts.yaml
# edit hosts.yaml with your devices

adjacency discover
adjacency show
```

## Inventory

Nornir SimpleInventory format. See `inventory/hosts.yaml.example`.

## Roadmap

- [ ] vPC / MLAG logical-device merging
- [ ] Stacking (e.g. VSS, StackWise) chassis consolidation
- [ ] NHRP / DMVPN spoke-to-hub mapping
- [ ] GraphViz / D2 diagram export
- [ ] Diff mode (compare two snapshots)
