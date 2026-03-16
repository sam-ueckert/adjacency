"""Visualization generators — interactive HTML (cytoscape.js), GraphViz DOT, and Lucidchart."""

from __future__ import annotations

import hashlib
import json
import math
import urllib.request
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from adjacency.models import AdjacencyTable, LinkType


# ---------------------------------------------------------------------------
# Platform colour palette
# ---------------------------------------------------------------------------

PLATFORM_COLORS: dict[str, str] = {
    "eos": "#4285F4",
    "ios": "#0F9D58",
    "iosxe": "#34A853",
    "iosxr": "#8BC34A",
    "nxos": "#F4B400",
    "nxos_ssh": "#F4B400",
    "junos": "#DB4437",
    "panos": "#FF6D00",
    "sros": "#9C27B0",
}

_FALLBACK_PALETTE = [
    "#607D8B", "#795548", "#FF5722", "#009688",
    "#3F51B5", "#E91E63", "#00BCD4", "#CDDC39",
    "#FFC107", "#9E9E9E", "#2196F3", "#4CAF50",
]


def _platform_color(platform: str | None) -> str:
    if not platform:
        return "#78909C"
    key = platform.lower().replace("_ssh", "").replace("-", "")
    if key in PLATFORM_COLORS:
        return PLATFORM_COLORS[key]
    idx = int(hashlib.md5(key.encode()).hexdigest(), 16) % len(_FALLBACK_PALETTE)
    return _FALLBACK_PALETTE[idx]


# ---------------------------------------------------------------------------
# Graph abstraction
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    id: str
    label: str
    platform: str
    vendor: str
    model: str
    management_ip: str
    interface_count: int
    dns_names: list[str] = field(default_factory=list)
    hardware_model: str = ""
    os_version: str = ""
    serial: str = ""
    color: str = ""


@dataclass
class GraphEdge:
    id: str
    source: str
    target: str
    source_intf: str
    target_intf: str
    link_type: str
    sources: list[str]
    member_count: int
    members: list[dict] = field(default_factory=list)


def _build_graph_data(table: AdjacencyTable) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes: list[GraphNode] = []
    for hostname in sorted(table.devices):
        dev = table.devices[hostname]
        hw_model = ""
        os_ver = ""
        if dev.hardware:
            hw_model = dev.hardware.hardware_model or dev.hardware.model or ""
            os_ver = dev.hardware.os_version or ""
        nodes.append(GraphNode(
            id=hostname,
            label=hostname,
            platform=dev.platform or "",
            vendor=dev.vendor or "",
            model=dev.model or "",
            management_ip=dev.management_ip or "",
            interface_count=len(dev.interfaces),
            dns_names=dev.dns_names,
            hardware_model=hw_model,
            os_version=os_ver or dev.os_version or "",
            serial=dev.serial or (dev.hardware.serial_number if dev.hardware else None) or "",
            color=_platform_color(dev.platform),
        ))

    edges: list[GraphEdge] = []
    for i, link in enumerate(table.links):
        members = []
        if link.members:
            for m in link.members:
                members.append({
                    "local_intf": m.local_interface,
                    "remote_intf": m.remote_interface or "",
                    "sources": [s.value for s in m.sources],
                })
        edges.append(GraphEdge(
            id=f"e{i}",
            source=link.local_device,
            target=link.remote_device,
            source_intf=link.local_interface,
            target_intf=link.remote_interface or "",
            link_type=link.link_type.value,
            sources=[s.value for s in link.sources],
            member_count=len(link.members),
            members=members,
        ))

    return nodes, edges


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://unpkg.com/cytoscape@3/dist/cytoscape.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, sans-serif;
       display:flex; height:100vh; background:#1a1a2e; color:#e0e0e0; }}
#cy {{ flex:1; }}
#sidebar {{ width:340px; background:#16213e; padding:16px; overflow-y:auto;
            border-left:1px solid #0f3460; font-size:13px; display:none; }}
#sidebar h3 {{ color:#e94560; margin:0 0 10px; }}
#sidebar .field {{ margin:4px 0; }}
#sidebar .label {{ color:#999; }}
#sidebar .val {{ color:#eee; }}
#sidebar .close {{ cursor:pointer; float:right; color:#e94560; font-size:18px; }}
#toolbar {{ position:absolute; top:10px; left:10px; z-index:10; display:flex; gap:8px; }}
#toolbar select, #toolbar button {{ background:#16213e; color:#e0e0e0; border:1px solid #0f3460;
  padding:4px 10px; border-radius:4px; cursor:pointer; font-size:12px; }}
#legend {{ position:absolute; bottom:10px; left:10px; z-index:10; background:#16213ecc;
           padding:10px; border-radius:6px; font-size:11px; }}
#legend .item {{ display:flex; align-items:center; gap:6px; margin:2px 0; }}
#legend .swatch {{ width:12px; height:12px; border-radius:2px; }}
</style>
</head>
<body>
<div id="toolbar">
  <select id="layoutSelect">
    <option value="cose">Force-Directed</option>
    <option value="grid">Grid</option>
    <option value="circle">Circle</option>
    <option value="breadthfirst">Hierarchical</option>
  </select>
  <button onclick="cy.fit(undefined,40)">Fit</button>
  <button id="lblBtn" onclick="toggleLabels()">Edge Labels: Off</button>
</div>
<div id="cy"></div>
<div id="sidebar">
  <span class="close" onclick="closeSidebar()">&times;</span>
  <div id="detail"></div>
</div>
<div id="legend">{legend_html}</div>
<script>
const ELEMENTS = {elements_json};
let edgeLabelsOn = false;
const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements: ELEMENTS,
  style: [
    {{ selector: 'node', style: {{
        'label': 'data(label)', 'background-color': 'data(color)',
        'color': '#eee', 'text-valign': 'bottom', 'text-margin-y': 6,
        'font-size': 11, 'shape': 'round-rectangle', 'width': 30, 'height': 30,
        'border-width': 2, 'border-color': '#0f3460',
    }} }},
    {{ selector: 'edge', style: {{
        'width': 'data(width)', 'line-color': 'data(color)',
        'curve-style': 'bezier', 'target-arrow-shape': 'none',
        'opacity': 0.7, 'label': '',
    }} }},
    {{ selector: 'edge[link_type="lag"]', style: {{
        'line-style': 'solid', 'line-color': '#4fc3f7',
    }} }},
    {{ selector: 'edge[link_type="logical"]', style: {{
        'line-style': 'dashed', 'line-color': '#81c784',
    }} }},
    {{ selector: ':selected', style: {{
        'border-color': '#e94560', 'border-width': 3,
        'line-color': '#e94560', 'opacity': 1,
    }} }},
  ],
  layout: {{ name: 'cose', animate: false, nodeDimensionsIncludeLabels: true }},
}});
document.getElementById('layoutSelect').addEventListener('change', function() {{
  cy.layout({{ name: this.value, animate: true, animationDuration: 400,
               nodeDimensionsIncludeLabels: true }}).run();
}});
function toggleLabels() {{
  edgeLabelsOn = !edgeLabelsOn;
  cy.edges().style('label', edgeLabelsOn ? function(e){{ return e.data('edgeLabel'); }} : '');
  document.getElementById('lblBtn').textContent = 'Edge Labels: ' + (edgeLabelsOn ? 'On' : 'Off');
}}
function closeSidebar() {{ document.getElementById('sidebar').style.display='none'; }}
function showDetail(html) {{
  document.getElementById('detail').innerHTML = html;
  document.getElementById('sidebar').style.display='block';
}}
function f(label, val) {{ return val ? '<div class="field"><span class="label">'+label+':</span> <span class="val">'+val+'</span></div>' : ''; }}
cy.on('tap', 'node', function(evt) {{
  const d = evt.target.data();
  let html = '<h3>'+d.label+'</h3>';
  html += f('Platform', d.platform);
  html += f('Vendor', d.vendor);
  html += f('Model', d.model);
  html += f('Hardware', d.hardware_model);
  html += f('OS Version', d.os_version);
  html += f('Serial', d.serial);
  html += f('Mgmt IP', d.management_ip);
  if (d.dns_names && d.dns_names.length) html += f('DNS Names', d.dns_names.join(', '));
  html += f('Interfaces', d.interface_count);
  // Connected links
  const edges = evt.target.connectedEdges();
  if (edges.length) {{
    html += '<h3 style="margin-top:14px">Links ('+edges.length+')</h3>';
    edges.forEach(function(e) {{
      const ed = e.data();
      const dir = ed.source === d.id ? ed.source_intf+' &rarr; '+ed.target+' '+ed.target_intf
                                      : ed.target_intf+' &larr; '+ed.source+' '+ed.source_intf;
      html += '<div class="field" style="border-bottom:1px solid #0f3460;padding:4px 0">';
      html += '<span class="val">'+dir+'</span><br>';
      html += '<span class="label">'+ed.link_type+'</span>';
      if (ed.member_count > 0) html += ' <span class="label">('+ed.member_count+' members)</span>';
      html += '</div>';
    }});
  }}
  showDetail(html);
}});
cy.on('tap', 'edge', function(evt) {{
  const d = evt.target.data();
  let html = '<h3>Link</h3>';
  html += f('Local', d.source + ' ' + d.source_intf);
  html += f('Remote', d.target + ' ' + d.target_intf);
  html += f('Type', d.link_type);
  html += f('Sources', d.sources.join(', '));
  if (d.members && d.members.length) {{
    html += '<h3 style="margin-top:14px">Members ('+d.members.length+')</h3>';
    d.members.forEach(function(m) {{
      html += '<div class="field">';
      html += '<span class="val">'+m.local_intf+' &harr; '+(m.remote_intf||'?')+'</span>';
      html += ' <span class="label">['+m.sources.join(', ')+']</span></div>';
    }});
  }}
  showDetail(html);
}});
</script>
</body>
</html>"""


def generate_html(
    table: AdjacencyTable,
    output_path: Path,
    title: str = "Adjacency Map",
) -> Path:
    """Generate an interactive HTML topology map."""
    nodes, edges = _build_graph_data(table)

    elements = {
        "nodes": [
            {"data": {
                "id": n.id, "label": n.label, "platform": n.platform,
                "vendor": n.vendor, "model": n.model,
                "hardware_model": n.hardware_model, "os_version": n.os_version,
                "serial": n.serial, "management_ip": n.management_ip,
                "interface_count": n.interface_count, "dns_names": n.dns_names,
                "color": n.color,
            }}
            for n in nodes
        ],
        "edges": [
            {"data": {
                "id": e.id, "source": e.source, "target": e.target,
                "source_intf": e.source_intf, "target_intf": e.target_intf,
                "link_type": e.link_type, "sources": e.sources,
                "member_count": e.member_count, "members": e.members,
                "edgeLabel": f"{e.source_intf} - {e.target_intf}",
                "width": max(2, 1 + e.member_count) if e.link_type == "lag" else 2,
                "color": "#4fc3f7" if e.link_type == "lag"
                         else "#81c784" if e.link_type == "logical"
                         else "#78909C",
            }}
            for e in edges
        ],
    }

    # Build legend from platforms present
    platforms_seen: dict[str, str] = {}
    for n in nodes:
        if n.platform and n.platform not in platforms_seen:
            platforms_seen[n.platform] = n.color
    legend_items = "".join(
        f'<div class="item"><div class="swatch" style="background:{color}"></div>{plat}</div>'
        for plat, color in sorted(platforms_seen.items())
    )

    html = _HTML_TEMPLATE.format(
        title=title,
        elements_json=json.dumps(elements),
        legend_html=legend_items,
    )
    output_path = Path(output_path)
    output_path.write_text(html)
    return output_path


# ---------------------------------------------------------------------------
# GraphViz DOT generation
# ---------------------------------------------------------------------------

def generate_dot(
    table: AdjacencyTable,
    output_path: Path | None = None,
) -> str:
    """Generate a GraphViz DOT representation of the topology."""
    nodes, edges = _build_graph_data(table)

    lines: list[str] = [
        "graph adjacency {",
        '    graph [rankdir=LR, overlap=false, splines=true, bgcolor="#1a1a2e"];',
        '    node [shape=box, style="filled,rounded", fontname="Helvetica", fontsize=10, fontcolor="#eeeeee"];',
        '    edge [fontname="Helvetica", fontsize=8, fontcolor="#cccccc"];',
        "",
    ]

    # Group nodes by platform into subgraph clusters
    by_platform: dict[str, list[GraphNode]] = {}
    for n in nodes:
        by_platform.setdefault(n.platform or "unknown", []).append(n)

    for plat, plat_nodes in sorted(by_platform.items()):
        color = _platform_color(plat if plat != "unknown" else None)
        # Make a lighter fill for the cluster background
        lines.append(f'    subgraph "cluster_{plat}" {{')
        lines.append(f'        label="{plat}"; fontcolor="#cccccc";')
        lines.append(f'        style=filled; fillcolor="{color}22"; color="{color}";')
        for n in plat_nodes:
            hw = f"\\n{n.hardware_model}" if n.hardware_model else ""
            dns = f"\\n{n.dns_names[0]}" if n.dns_names else ""
            node_label = f"{n.label}\\n{n.management_ip}{hw}{dns}"
            lines.append(
                f'        "{n.id}" [label="{node_label}", fillcolor="{n.color}"];'
            )
        lines.append("    }")
        lines.append("")

    # Edges
    for e in edges:
        attrs: list[str] = []
        intf_label = f"{e.source_intf} -- {e.target_intf}" if e.target_intf else e.source_intf
        if e.link_type == "lag":
            attrs.append(f'label="{intf_label}\\nLAG x{e.member_count}"')
            attrs.append("style=bold")
            attrs.append(f"penwidth={2 + e.member_count}")
            attrs.append('color="#4fc3f7"')
        elif e.link_type == "logical":
            attrs.append(f'label="{intf_label}"')
            attrs.append("style=dashed")
            attrs.append('color="#81c784"')
        else:
            attrs.append(f'label="{intf_label}"')
            attrs.append('color="#78909C"')

        attr_str = ", ".join(attrs)
        lines.append(f'    "{e.source}" -- "{e.target}" [{attr_str}];')

    lines.append("}")
    dot_text = "\n".join(lines) + "\n"

    if output_path:
        Path(output_path).write_text(dot_text)

    return dot_text


# ---------------------------------------------------------------------------
# Lucidchart Standard Import (.lucid) generation
# ---------------------------------------------------------------------------

_LUCID_SHAPE_W = 200
_LUCID_SHAPE_H = 120
_LUCID_PAD_X = 80
_LUCID_PAD_Y = 60
_LUCID_CLUSTER_GAP = 200


def _grid_layout(nodes: list[GraphNode]) -> dict[str, tuple[float, float]]:
    """Compute (x, y) positions for nodes, grouped by platform in a grid."""
    by_platform: dict[str, list[GraphNode]] = {}
    for n in nodes:
        by_platform.setdefault(n.platform or "unknown", []).append(n)

    positions: dict[str, tuple[float, float]] = {}
    # Max nodes per row within a platform cluster
    cols = max(4, int(math.ceil(math.sqrt(len(nodes)))))
    y_offset = 0.0

    for _plat, plat_nodes in sorted(by_platform.items()):
        for i, n in enumerate(plat_nodes):
            col = i % cols
            row = i // cols
            x = col * (_LUCID_SHAPE_W + _LUCID_PAD_X)
            y = y_offset + row * (_LUCID_SHAPE_H + _LUCID_PAD_Y)
            positions[n.id] = (x, y)
        rows_used = math.ceil(len(plat_nodes) / cols) if plat_nodes else 1
        y_offset += rows_used * (_LUCID_SHAPE_H + _LUCID_PAD_Y) + _LUCID_CLUSTER_GAP

    return positions


def generate_lucid(
    table: AdjacencyTable,
    output_path: Path,
    title: str = "Adjacency Map",
) -> Path:
    """Generate a Lucidchart Standard Import (.lucid) file."""
    nodes, edges = _build_graph_data(table)
    positions = _grid_layout(nodes)

    shapes = []
    for n in nodes:
        x, y = positions[n.id]
        label_parts = [n.label]
        if n.management_ip:
            label_parts.append(n.management_ip)
        if n.hardware_model:
            label_parts.append(n.hardware_model)
        if n.platform:
            label_parts.append(n.platform)

        color = n.color or _platform_color(n.platform)
        shapes.append({
            "id": n.id,
            "type": "rectangle",
            "boundingBox": {"x": x, "y": y, "w": _LUCID_SHAPE_W, "h": _LUCID_SHAPE_H},
            "text": "\n".join(label_parts),
            "style": {
                "fill": {"type": "color", "color": color},
                "stroke": {"color": "#333333", "width": 2, "style": "solid"},
                "rounding": 8,
            },
        })

    lines_list = []
    for e in edges:
        label_parts = []
        if e.target_intf:
            label_parts.append(f"{e.source_intf} -- {e.target_intf}")
        else:
            label_parts.append(e.source_intf)
        if e.link_type == "lag" and e.member_count:
            label_parts.append(f"LAG x{e.member_count}")
        elif e.link_type == "logical":
            label_parts.append("logical")

        line_style = "solid"
        line_color = "#78909C"
        if e.link_type == "lag":
            line_color = "#4fc3f7"
        elif e.link_type == "logical":
            line_style = "dashed"
            line_color = "#81c784"

        lines_list.append({
            "id": e.id,
            "type": "line",
            "endpoint1": {"type": "shapeEndpoint", "shapeId": e.source},
            "endpoint2": {"type": "shapeEndpoint", "shapeId": e.target},
            "text": "\n".join(label_parts),
            "style": {
                "stroke": {"color": line_color, "width": 2, "style": line_style},
            },
        })

    document = {
        "version": 1,
        "pages": [{
            "id": "page1",
            "title": title,
            "shapes": shapes,
            "lines": lines_list,
        }],
    }

    output_path = Path(output_path)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("document.json", json.dumps(document, indent=2))
    output_path.write_bytes(buf.getvalue())
    return output_path


def push_lucid(
    lucid_path: Path,
    api_key: str,
    title: str = "Adjacency Map",
) -> str:
    """Upload a .lucid file to Lucidchart via the Standard Import API.

    Returns the edit URL of the created document.
    """
    lucid_path = Path(lucid_path)
    file_data = lucid_path.read_bytes()

    # Build multipart/form-data request
    boundary = "----AdjacencyLucidBoundary"
    body = bytearray()

    # File part
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{lucid_path.name}"\r\n'.encode()
    )
    body.extend(b"Content-Type: x-application/vnd.lucid.standardImport\r\n\r\n")
    body.extend(file_data)
    body.extend(b"\r\n")

    # Title part
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="title"\r\n\r\n')
    body.extend(title.encode())
    body.extend(b"\r\n")

    # Product part
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="product"\r\n\r\n')
    body.extend(b"lucidchart\r\n")

    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        "https://api.lucid.co/v1/documents",
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result.get("editUrl", result.get("documentId", ""))
